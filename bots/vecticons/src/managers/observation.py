"""Observation manager (Stage 2; renamed from knowledge manager 2026-10-21).

Per-turn assembly of facts the bot needs. Pure read-only; no decisions,
no SDK writes. Cached map-derived data (camp layout, altar positions,
per-zone camp lists) lives on the Bot instance and is filled on the
first play turn (2x fuel).

Update frequency:
  - Self state (HP, gold, gems, position): every turn.
  - Vision-derived facts: every turn (depends on vision()).
  - Map cache (camp groups, altars, spawn): once at first play turn.

Read path: bot.play_turn() calls observation.snapshot(self) once per turn,
then passes the snapshot through to execution. Knowledge never mutates
engine state.

S2.3 fuel notes (measured 2026-10-23, CLI 3.0.4 / engine 1.0.2):
  - Every SDK call costs ~3.4k-30k fuel; the per-turn budget is 2,493,228.
  - ``CampGroup.state()`` costs ~49k: it re-reads the whole vision bitboard
    and then probes EVERY camp's spawn tile (one SDK call each). The S2.3
    path therefore asks the engine for the raw group state and does the
    vision guard once per turn, on the group tiles it already has cached.
  - ``Camp.kind()`` and ``Camp.spawn_position()`` are plain attributes
    (free); ``Camp.state()`` is one SDK call.
  - The visible-group set is derived from the visible camps (one vision
    read) instead of ANDing every group's spawn mask against the visible
    tiles (one SDK call per group).
  - Reading a ``Vec2`` coordinate out of the engine costs ~1.8k fuel, so
    every tile coordinate the bot uses in a per-turn loop is cached as a
    PLAIN INT here (the S2.3 coordinate cache) and never re-read.
  - ``me.state()`` returns every champion flag in ONE call (~15k); the
    per-flag accessors each cost a call of their own, so the S2.3 path
    reads the flags once.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import cambc
from cambc import game_map

import config
from managers import fielding, topology


@dataclass
class Snapshot:
    """Per-turn read of facts the bot needs. Cheap; safe to call every turn."""

    me: Any
    is_alive: bool
    is_recalling: bool
    is_submitting_gems: bool
    is_teleporting: bool
    can_move: bool
    can_attack: bool

    # Self resources.
    hp: float
    max_hp: float
    gold: int
    gems: int
    position: Any

    # Map cache (filled on first call).
    altars: tuple
    spawn_tile: Any | None
    camp_groups: tuple
    camp_zone_for_champion: int

    # Vision-derived.
    visible_camp_groups: tuple
    visible_enemies: tuple

    # Per-group cached metadata (is_monolith, static_hp). Filled from the bot's map cache.
    group_meta: dict

    # Per-group cached spawn tiles (engine handle -> Bitboard) and their
    # positions (handle -> tuple of Vec2). Cached on the first turn: the
    # per-turn ``group.spawn_tiles()`` calls were the dominant fuel cost
    # (one SDK call ~ 10k fuel; ~40 groups x 2 calls per turn).
    group_tiles: dict
    group_positions: dict

    # Static union of excluded groups' spawn tiles (Monolith / over-cap
    # static HP): permanent doors for the reachability gate. Cached once.
    excluded_tiles: Any

    # Enemy fog memory (intelligence manager): engine handle -> last-known
    # record. Reference to bot.enemy_memory; updated in place each turn.
    enemy_memory: dict

    # S2.2 folded ally intel (intelligence.fold_ally_intel, wired in bot.py
    # after read_signals): parsed beacon dicts, per-region fresh-sighting
    # density (region id -> count), contested altar tiles as (x, y).
    # Defaults keep scoring S2.0-identical when the fold fails or is skipped.
    ally_signals: tuple = ()
    region_sightings: dict = field(default_factory=dict)
    contested_altars: frozenset = frozenset()

    # S2.3 shared per-turn distance infrastructure (managers.fielding).
    # The scoring DistanceField and its reachable set are cached here so the
    # whole turn scores against ONE field.
    distance_field: Any = None
    field_visited: Any = None
    scoring_walls: Any = None
    # Static terrain blockers, cached once with the map data.
    terrain_walls: Any = None
    # The terrain-only boss-engagement field (built lazily, once per turn).
    boss_field: Any = None
    # Visible live camps (one vision read per turn, shared by dispatch).
    visible_camps: tuple = ()
    # Per-group state cache (engine handle -> CampGroupState). A group's
    # state is read at most once per turn even when several candidate
    # generators need it.
    group_states: dict = field(default_factory=dict)
    # Our spawn tiles (recall distance).
    spawn_tiles: tuple = ()
    # (handle, group) pairs in map order. The handle lookup is a Python
    # method call (~2.5k fuel); the per-turn group scans use this index
    # instead of re-deriving the handle every turn.
    group_index: tuple = ()

    # ---- S2.3 per-turn camp facts -------------------------------------
    # True when the snapshot was built in the S2.3 mode (the shared field,
    # the cached camp facts). The frozen Stage 2 baseline keeps its own
    # engine-call profile through the default arguments.
    s23_cache: bool = False
    # Camp handle -> (group handle, camp kind, base HP). First-turn cache:
    # the visible camps are resolved without any per-turn SDK lookup.
    #
    # S2.3.2: the entry also carries the camp's SPAWN TILE
    # (Vec2, x, y) - the farm candidate's goal must be a LIVE camp of the
    # group (a dead camp is not reported by ``vision.camps()``, so the
    # visible camps ARE the live ones). ``camp.spawn_position()`` is a free
    # attribute (no SDK call), so the per-turn goal costs no engine read.
    camp_info: dict = field(default_factory=dict)
    # Group handle -> the LIVE visible camps of that group, as
    # (Vec2, x, y) tuples. Filled every turn by _read_visible_camps: a dead
    # camp is never reported by ``vision.camps()``, so every visible camp is
    # alive. The farm candidate walks to the nearest live camp instead of
    # the group's static representative tile - the static anchor is the
    # spawn tile of the group's FIRST camp, and when that camp is killed
    # the anchor is a dead tile: the distance stays 0, the score stays
    # maximal and the walk refuses to move (we already stand on the goal
    # tile), which pinned the champion on the dead camp for the whole match
    # (measured on 01yellow_brick_road: (5,17) from round 19 to 2000).
    group_live_tiles: dict = field(default_factory=dict)
    # Group handle -> number of camps in the group (first-turn cache).
    group_camp_count: dict = field(default_factory=dict)
    # Group handle -> live visible HP (S2.3 path only).
    group_live_hp: dict = field(default_factory=dict)
    # Handles of the groups with at least one visible camp.
    visible_group_keys: frozenset = frozenset()
    # Awake (already hit) monsters as (tile, strength) pairs.
    awake_monsters: tuple = ()
    # Camp handle -> the camp's engine state (CampState). One read per
    # visible camp per turn; it also derives the group state.
    camp_states: dict = field(default_factory=dict)
    # Group handle -> number of visible camps of that group.
    group_visible_camps: dict = field(default_factory=dict)
    # Group handle -> number of visible DEAD camps of that group.
    group_dead_camps: dict = field(default_factory=dict)
    # Group handle -> derived group state: "reward" (all camps visible and
    # dead - the reward can be captured) or "alive". A group that is only
    # partly in vision is absent (unknown).
    group_derived_states: dict = field(default_factory=dict)
    # The turn's visible tiles (one vision read; used for the "is this
    # group fully in vision" test of the capture candidate).
    visible_tiles: Any = None
    # Enemy-owned altar locks (one read per turn). `has_altar_lock` is
    # team-agnostic, so our own locks would lock us out of our own altars.
    enemy_altar_locks: Any = None
    # Remembered enemy champions (fog memory) as threat records.
    threat_memory: tuple = ()
    # Visible enemy champions as (champion, x, y) with PLAIN INT coordinates.
    # Filled once per turn by the intelligence manager (which reads the
    # positions anyway) so the altar-contest test does not re-read every
    # enemy's coordinates out of the engine (~3.7k fuel each).
    enemy_positions: tuple = ()
    # The Bot instance the snapshot was built from. The S2.3 path caches the
    # gradient path on it (the path survives turns; the snapshot does not).
    bot: Any = None
    # Our position as PLAIN INTS (px, py). Reading Vec2.x / Vec2.y out of
    # the engine costs ~1.8k fuel each, so the per-turn code reads them
    # exactly once and works with ints from then on.
    origin_xy: tuple = (0, 0)
    # Group handle -> the group's REPRESENTATIVE tile as (Vec2, x, y). One
    # tile per group is enough for the pool pre-filter and for the honest
    # distance read; the group's other tiles are cached in group_positions.
    group_reps: dict = field(default_factory=dict)
    # Group handle -> straight-line distance anchor (x, y) of the
    # representative tile, for the cheap pool pre-filter (pure Python).
    group_anchors: dict = field(default_factory=dict)
    # (x, y) -> Vec2 lookup of the walkable tiles around each altar (the
    # tribute approach ring). Cached once: the ring is static.
    altar_rings: dict = field(default_factory=dict)
    # (ax, ay) -> tuple of (x, y, Vec2) WALKABLE tiles in the tribute
    # approach ring (the altar tile itself is a wall, so it is excluded).
    # The bank candidate reads the field distance of the nearest few of
    # them: the honest "distance to the altar" is the distance to the
    # tile we can stand on to pay tribute.
    altar_approach: dict = field(default_factory=dict)
    # The altars' own coordinates as plain ints (static).
    altar_coords: tuple = ()
    # Our spawn tiles as (x, y, Vec2) - the recall walk home.
    spawn_coords: tuple = ()
    # Map size (width, height) - cached; game_map.width() is an engine call.
    map_size: tuple = (0, 0)
    # The group handles that pass the killable filter (not the Monolith,
    # static HP within the cap). STATIC for the match, so it is computed
    # once with the map cache: the per-turn zone-pool scan is a set lookup
    # per group instead of a metadata read.
    killable_keys: frozenset = frozenset()
    # The groups whose reward is available for capture (every monster of
    # the group is dead), as (key, group) pairs.
    #
    # A DEAD camp is NOT reported by ``vision.camps()`` (probe-verified
    # 2026-10-24), so the cleared state CANNOT be derived from the visible
    # camps: the S2.3 first cut derived it from the camp states, the
    # capture candidate never fired, and the bot farmed cleared groups
    # forever (0 gems on 01starvation). The engine's own answer is
    # ``group.state()`` -> CampGroupRewardAvailable (the S2.2 baseline used
    # it), so that is what the S2.3 path reads - but only for the few
    # groups that can be in our vision (see observation._detect_cleared_groups).
    cleared_groups: tuple = ()
    # The groups whose reward was ALREADY claimed (the engine's
    # ``CampGroupRespawning``), as (key, group) pairs. A respawning group
    # has no live monster, so it is NOT a farm candidate - the S2.3 second
    # cut scored it as one (its value is the group's full gem total and the
    # champion stands on its tile, so the "farm" candidate outscored every
    # other action and the bot farmed a dead group for 1,900 rounds).
    claimed_groups: tuple = ()
    # The round number of this turn (one engine read per turn; the S2.3
    # claim memory needs it to expire a claim when the group respawns).
    round_number: int = 0
    # S2.3 FUEL: the flat per-group FARM ROWS (static for the match).
    #
    # The farm pool is scored once per candidate per turn, and the S2.3
    # second cut paid ~50k fuel PER CANDIDATE for that (a dict-of-dicts
    # metadata read, four float() conversions, a math.ceil call and a
    # region-risk helper call - 36 candidates = 2.05M fuel, the whole
    # per-turn budget). The engine charges roughly 130 fuel per Python
    # BYTECODE, so the fix is to move every per-group CONSTANT out of the
    # per-turn loop and leave a handful of bytecodes behind.
    #
    # A row is a TUPLE (tuple indexing beats dict lookups):
    #   0 key          - the group's engine handle (int, unique)
    #   1 group        - the group handle (for the candidate payload)
    #   2 vec          - the anchor tile (Vec2) for the one field read
    #   3 ax, 4 ay     - the anchor tile's coordinates as PLAIN INTS
    #   5 value        - gem_value + gold_value * GOLD_TO_GEM
    #   6 gems         - the group's real gem total
    #   7 gold         - the group's real gold total
    #   8 cls          - the clear class id (see farm_classes)
    #   9 region       - the 4x4 region id of the anchor (static)
    #   10 tiles       - the group's other spawn tiles (Vec2), for the
    #                    sealed-anchor fallback read
    #   11 span       - the group's Chebyshev diameter (the walk across
    #                    the group), charged to the clear time
    farm_rows: tuple = ()
    # The groups that fail the killable filter (Monolith / over-cap static
    # HP) as (key, group) pairs, with their STATIC boss value.
    #
    # S2.3 FUEL: a boss row is
    #   0 key          - the group's engine handle
    #   1 group        - the group handle (candidate payload)
    #   2 value        - drops + unlock_value (the boss's total prize)
    #   3 cls          - the clear class id (see farm_classes)
    #   4 ax, 5 ay     - the anchor tile's coordinates as PLAIN INTS
    #   6 unlock       - the unlock value (diagnostic note)
    # The per-turn boss loop used to re-read the group's metadata (a dict
    # get, four float() conversions and a math.ceil call per boss): ~37k
    # fuel per boss per turn, ~150k a turn on 01yellow_brick_road.
    boss_rows: tuple = ()
    # The distinct (static HP, total strength) classes of the map. The
    # clear time and the P(survive the clear) depend only on the class,
    # the team's damage output and our health - so they are computed ONCE
    # PER TURN for the handful of classes instead of once per candidate.
    farm_classes: tuple = ()
    # The groups that fail the killable filter (Monolith / over-cap static
    # HP) as (key, group) pairs. STATIC for the match: the boss generator
    # and the boss field used to re-derive this by scanning every group on
    # the map, every turn.
    excluded_groups: tuple = ()
    # The altars' own Vec2 tiles (static) - the enemy-lock test used to
    # build a fresh Vec2 per altar per turn (~2.4k fuel each).
    altar_vecs: dict = field(default_factory=dict)
    # The altars' 4x4 region ids (static) - the bank score used to call
    # intelligence.region_of() per altar per turn.
    altar_regions: dict = field(default_factory=dict)
    # Group handle -> the group's spawn tiles as PLAIN INT (x, y) pairs.
    # Reading a Vec2 coordinate out of the engine costs ~1.8k fuel, so the
    # "do we stand on this group's tile" test used to cost six engine
    # reads per capture candidate.
    group_tile_xy: dict = field(default_factory=dict)
    # Our attack tiles (one engine read per turn, shared by the fight
    # candidate, the dispatch and the opportunistic attack path).
    attack_tiles: Any = None
    # Group handle -> group handle (a static lookup table). The visible
    # group set used to be rebuilt by a generator over EVERY group on the
    # map, every turn.
    group_by_key: dict = field(default_factory=dict)


# The engine's champion-state flag bits, cached once per process. Every
# attribute lookup on the wasm-side enum class crosses the SDK boundary,
# and the per-turn fact read needs five of them.
_STATE_FLAGS: tuple = ()


def _state_flags() -> tuple:
    """(CAN_MOVE, CAN_ATTACK, RECALLING, SUBMITTING_GEMS, TELEPORTING)."""
    global _STATE_FLAGS
    if not _STATE_FLAGS:
        cs = cambc.ChampionState
        _STATE_FLAGS = (
            cs.CAN_MOVE,
            cs.CAN_ATTACK,
            cs.RECALLING,
            cs.SUBMITTING_GEMS,
            cs.TELEPORTING,
        )
    return _STATE_FLAGS


def _me_facts(me, s23_cache: bool = False) -> Snapshot:
    """Our own facts.

    S2.3 fuel: the six per-flag accessors (``can_move``, ``can_attack``,
    ``is_recalling``, ``is_submitting_gems``, ``is_teleporting``) each cost
    an engine call of their own, while ``me.state()`` returns EVERY flag in
    one call (~15k fuel, measured). The S2.3 path reads the state once; the
    frozen baseline keeps its historical per-flag profile (the ``s23_cache``
    opt-in keeps the default behaviour byte-compatible).
    """
    if s23_cache:
        try:
            state = me.state()
            flags = _state_flags()
            can_move = bool(state & flags[0])
            can_attack = bool(state & flags[1])
            is_recalling = bool(state & flags[2])
            is_submitting = bool(state & flags[3])
            is_teleporting = bool(state & flags[4])
        except Exception:
            can_move = bool(me.can_move())
            can_attack = bool(me.can_attack())
            is_recalling = bool(me.is_recalling())
            is_submitting = bool(me.is_submitting_gems())
            is_teleporting = bool(me.is_teleporting())
    else:
        can_move = bool(me.can_move())
        can_attack = bool(me.can_attack())
        is_recalling = bool(me.is_recalling())
        is_submitting = bool(me.is_submitting_gems())
        is_teleporting = bool(me.is_teleporting())
    position = me.position()
    origin_xy = (0, 0)
    if s23_cache:
        try:
            origin_xy = (int(position.x), int(position.y))
        except Exception:
            origin_xy = (0, 0)
    return Snapshot(
        me=me,
        is_alive=bool(me.is_alive()),
        is_recalling=is_recalling,
        is_submitting_gems=is_submitting,
        is_teleporting=is_teleporting,
        can_move=can_move,
        can_attack=can_attack,
        hp=float(me.health()),
        max_hp=float(me.max_health()),
        gold=int(me.gold_count()),
        gems=int(me.gem_count()),
        position=position,
        origin_xy=origin_xy,
        altars=(),
        spawn_tile=None,
        camp_groups=(),
        camp_zone_for_champion=0,
        visible_camp_groups=(),
        visible_enemies=(),
        group_meta={},
        group_tiles={},
        group_positions={},
        group_index=(),
        enemy_memory={},
        excluded_tiles=None,
    )


def _zone_id_for_me() -> int:
    """Parse the Stage 0 draft-memory buffer for this champion's zone id.

    Header layout (architecture.md Stage 0):
      byte 0       format version (must be 1)
      byte 1       pick count
      byte 2       ownership bit map
      byte 3       reserved
    Then 7-byte per-pick records starting at offset 4. record[0]=champion id,
    record[2]=camp zone id. The record for this champion is at
    offset 4 + 7*pick_index, where pick_index is our index in current_draft(ALLY).
    """
    try:
        raw = bytes(cambc.DraftMemoryBuffer())
    except Exception:
        return 0
    if len(raw) < 4 or int(raw[0]) not in (1, topology.DRAFT_MEMORY_VERSION):
        return 0
    try:
        picks_ally = cambc.draft.current_draft(cambc.Team.ALLY)
    except Exception:
        return 0
    if not picks_ally:
        return 0
    try:
        me = cambc.me()
        my_id = draft_id_for(int(me.champion_name()))
    except Exception:
        return 0
    try:
        idx = list(picks_ally).index(my_id)
    except ValueError:
        return 0
    zone_offset = 4 + 7 * idx + 2
    if zone_offset >= len(raw):
        return 0
    return int(raw[zone_offset])


def champion_short_name(champion_name_int: int) -> str:
    """Return the short champion name from an int ChampionType.

    `cambc.ChampionType.to_string(value)` yields 'ChampionType.RYTH'. We want
    'RYTH' for config.CHAMPION_STAT_PRIORITY lookups.
    """
    raw = cambc.ChampionType.to_string(int(champion_name_int))
    if "." in raw:
        return raw.split(".", 1)[1]
    return raw


def draft_id_for(champion_type_int: int) -> int:
    """ChampionType value -> matching ChampionDraft value.

    The two enums diverge from Cassi & Issac onward: CASSI_AND_ISSAC is
    one draft pick (3) but two playable types (CASSI=3, ISSAC=4), shifting
    every later value by one. Comparing a ChampionType against draft picks
    directly therefore only works for RYTH/VESPER_VALE — the bug that made
    three of five champions silent on the S2.1 channel (and, latently,
    misread their draft-memory zone records since Stage 2).
    """
    short = champion_short_name(int(champion_type_int))
    if short == "ISSAC":
        short = "CASSI_AND_ISSAC"
    attr = getattr(cambc.ChampionDraft, short, None)
    return int(attr) if attr is not None else -1


# The engine handle of every cached camp group, keyed by the ID of the
# wrapper object. `group.__index__()` is an engine call (~2.5k fuel), and
# the per-turn group scans used it once per group per turn. The map cache
# holds a STRONG reference to every group object (in `_GROUP_OBJECTS`), so
# the ids can never be recycled by another object - the lookup is safe.
_GROUP_KEY_BY_ID: dict = {}
_GROUP_OBJECTS: list = []

# The engine's own camp design table: camp kind -> (base_health,
# base_strength, base_attack_range), read from `camp.base_stats()`.
#
# S2.3.4: this table is the single source of truth for the camp kinds. The
# hand-written table in config.py was missing a kind (the 5000-HP /
# 120-strength monster), and a missing kind read back as strength 0 - which
# priced its groups as harmless (P(survive) = 1.0) and made the boss
# candidate win the argmax on every turn of 00road_of_riches.
_CAMP_DESIGN: dict = {}


def camp_design(kind: int):
    """The engine's design record of a camp kind, or None when unknown."""
    try:
        return _CAMP_DESIGN.get(int(kind))
    except Exception:
        return None


def _register_group_key(group, key: int) -> None:
    """Remember a group's engine handle for the cheap id-based lookup."""
    try:
        _GROUP_KEY_BY_ID[id(group)] = key
        _GROUP_OBJECTS.append(group)
    except Exception:
        pass


def group_key(group) -> int:
    """Stable key for a camp group: the engine handle.

    int(group) is stable across wrapper objects (fresh camp.camp_group()
    views resolve to the same key); id(group) would only match the
    cached objects held by the map-cache tuple.

    S2.3 fuel: the S2.3 map cache registers every group, so the hot path
    is a dict lookup on the wrapper's id instead of an engine call.
    """
    try:
        key = _GROUP_KEY_BY_ID.get(id(group))
        if key is not None:
            return key
    except Exception:
        pass
    try:
        return int(group.__index__())
    except Exception:
        return id(group)


def group_positions(snap, group):
    """The group's cached spawn tiles as Vec2 positions (None if unknown).

    S2.3 fuel: the positions are cached on the first turn, so the per-turn
    code never calls group.spawn_tiles() (one SDK call ~ 10k fuel, and the
    group scan was the largest fuel item in the S2.2 layer).
    """
    cache = getattr(snap, "group_positions", None)
    if not cache:
        return None
    try:
        return cache.get(group_key(group))
    except Exception:
        return None


def group_tiles(snap, group):
    """The group's cached spawn-tile bitboard (None if unknown)."""
    cache = getattr(snap, "group_tiles", None)
    if not cache:
        return None
    try:
        return cache.get(group_key(group))
    except Exception:
        return None


def _populate_map_cache(bot, snap, s23_cache: bool = False) -> None:
    """First-turn (2x fuel) cache: altars, spawns, camp groups, draft zone.

    Also pre-computes per-group metadata (is_monolith, static_hp, the REAL
    gem/gold value of the group, its monster strength) so per-turn filters
    don't have to call ``group.camps()`` / ``state()`` again. Without this
    cache, the Stage 3 utility layer was exceeding its per-turn fuel budget
    on large maps (group-by-group camp iteration every turn).

    S2.3: the boss "unlock value" (the reachability delta of an excluded
    group - what opens if its tiles open) is precomputed here when the draft
    memory does not carry it (the draft normally computes it in the 10x fuel
    window and ships it through the team draft memory).

    ``s23_cache`` is opt-in so the frozen Stage 2 baseline keeps its
    historical engine-call profile; the S2.3 bot enables the static walls,
    spawn tiles and positions needed by its shared field/dispatch path.
    """
    if bot.map_cache is not None:
        cache = bot.map_cache
        # A Bot instance normally uses one cache mode for its whole life.
        # If a caller changes the mode, rebuild rather than mixing the
        # legacy per-turn reads with the S2.3 cached terrain/group data.
        if isinstance(cache, dict) and bool(cache.get("s23_cache", False)) == bool(s23_cache):
            snap.altars = cache["altars"]
            snap.spawn_tile = cache["spawn"]
            snap.camp_groups = cache["groups"]
            snap.camp_zone_for_champion = cache["zone"]
            snap.group_meta = cache["group_meta"]
            snap.excluded_tiles = cache["excluded"]
            snap.spawn_tiles = cache["spawn_tiles"]
            snap.group_tiles = cache["group_tiles"]
            snap.group_positions = cache["group_positions"]
            snap.group_index = cache["group_index"]
            snap.terrain_walls = cache["terrain_walls"]
            snap.camp_info = cache["camp_info"]
            snap.group_camp_count = cache["group_camp_count"]
            snap.group_reps = cache["group_reps"]
            snap.group_anchors = cache["group_anchors"]
            snap.altar_rings = cache["altar_rings"]
            snap.altar_coords = cache["altar_coords"]
            snap.altar_approach = cache.get("altar_approach", {})
            snap.spawn_coords = cache["spawn_coords"]
            snap.map_size = cache["map_size"]
            snap.killable_keys = cache.get("killable_keys", frozenset())
            snap.farm_rows = cache.get("farm_rows", ())
            snap.farm_classes = cache.get("farm_classes", ())
            snap.excluded_groups = cache.get("excluded_groups", ())
            snap.boss_rows = cache.get("boss_rows", ())
            snap.altar_vecs = cache.get("altar_vecs", {})
            snap.altar_regions = cache.get("altar_regions", {})
            snap.group_tile_xy = cache.get("group_tile_xy", {})
            snap.group_by_key = cache.get("group_by_key", {})
            snap.s23_cache = bool(s23_cache)
            return
        # Old callers may still have the pre-S2.3 tuple-shaped cache.  Do
        # not consume it as if it contained the new fields; rebuild the
        # cache in the requested mode instead.
        bot.map_cache = None

    altars = tuple(game_map.altars().positions())
    spawn_positions = game_map.spawn_region(cambc.Team.ALLY).positions()
    spawn = spawn_positions[0] if spawn_positions else None
    groups = tuple(game_map.camp_groups())
    zone = _zone_id_for_me()
    # Terrain blockers never change during a match. Cache them with the
    # first-turn map data so every scoring/dispatch turn reuses one set.
    terrain_walls = fielding.terrain_walls() if s23_cache else None

    # Pre-compute per-group metadata. This is the single expensive call that
    # we never want to repeat on every play turn.
    group_meta = build_group_meta(groups, s23_cache=s23_cache)

    # Static door overlay for the reachability gate: spawn tiles of every
    # group failing the killable filter (Monolith or over-cap static HP).
    # Position-independent by construction - a vision-derived overlay
    # re-created the target-flip oscillator on 00ambush (reach flickered
    # with which excluded camps were visible). Staleness limitation: if
    # the enemy clears an excluded group, its tiles free up but the
    # overlay stays conservative until respawn (never oscillates, may
    # over-block a route briefly).
    excluded = cambc.Bitboard()
    # S2.3 fuel: the group's spawn tiles are cached here (first turn, 2x
    # fuel) so the per-turn code never calls group.spawn_tiles() again -
    # one SDK call costs ~10k fuel and the per-turn group scan was the
    # single largest fuel item in the S2.2 layer.
    group_tiles: dict = {}
    group_positions: dict = {}
    group_index: list = []
    # S2.3 fuel: group handle -> the group's REPRESENTATIVE tile as
    # (Vec2, x, y). One tile per group is enough for the pool pre-filter
    # and for the honest distance read: a group is a cluster of cardinally
    # connected monsters, so its tiles sit within a few tiles of each other
    # and the distance error is a couple of turns out of ~100. Reading the
    # tile's coordinates out of the engine costs ~3.7k fuel, so it is paid
    # ONCE per group here (first turn) and never again.
    group_reps: dict = {}
    group_anchors: dict = {}
    # S2.3: camp handle -> (group handle, camp kind, base HP). Resolving a
    # visible camp to its group used to cost an SDK call per camp
    # (camp.camp_group() = ~57k fuel!); the map is static, so the table is
    # built once and the per-turn path is a dict lookup.
    camp_info: dict = {}
    group_camp_count: dict = {}
    # Group handle -> the group's spawn tiles as PLAIN INT (x, y) pairs.
    group_tile_xy: dict = {}
    for group in groups:
        key = group_key(group)
        group_index.append((key, group))
        if s23_cache:
            _register_group_key(group, key)
        m = group_meta.get(key, {})
        is_excluded = (
            m.get("is_monolith", False)
            or int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP
        )
        if s23_cache:
            # S2.3 fuel: the group's spawn positions are built from the
            # camps' cached `spawn_position` attribute (pure Python, no SDK
            # call). `group.spawn_tiles().positions()` was 46 SDK calls
            # (460k fuel) on a 46-group map, for the same list.
            try:
                camps = list(group.camps())
            except Exception:
                camps = []
            try:
                positions = tuple(camp.spawn_position() for camp in camps)
            except Exception:
                positions = ()
            group_positions[key] = positions
            if positions:
                rep = positions[0]
                try:
                    group_reps[key] = (rep, int(rep.x), int(rep.y))
                    group_anchors[key] = (int(rep.x), int(rep.y))
                except Exception:
                    pass
                # S2.3 FUEL: the group's spawn tiles as PLAIN INTS. The
                # "do we stand on this group's tile" test (the capture
                # commitment factor) read six Vec2 coordinates out of the
                # engine per candidate (~1.8k fuel each).
                xy = []
                for _p in positions:
                    try:
                        xy.append((int(_p.x), int(_p.y)))
                    except Exception:
                        xy.append((-1, -1))
                group_tile_xy[key] = tuple(xy)
            try:
                tiles = group.spawn_tiles()
            except Exception:
                continue
            group_tiles[key] = tiles
            group_camp_count[key] = len(positions)
            for _i, camp in enumerate(camps):
                try:
                    _kind = int(camp.kind())
                except Exception:
                    continue
                _design = _CAMP_DESIGN.get(_kind)
                if _design is None:
                    _hp = float(config.CAMP_BASE_HP.get(_kind, 0.0))
                    _reach = int(config.MONSTER_RANGE_BY_KIND.get(_kind, 3))
                else:
                    _hp = float(_design[0])
                    _reach = int(_design[2])
                # S2.3.2: the camp's SPAWN TILE travels with the entry, so
                # the per-turn farm goal can point at a LIVE camp without
                # reading a coordinate out of the engine.
                _spawn = None
                _sx = -1
                _sy = -1
                try:
                    _spawn = camp.spawn_position()
                    _sx = int(_spawn.x)
                    _sy = int(_spawn.y)
                except Exception:
                    _spawn = None
                try:
                    camp_info[int(camp.__index__())] = (
                        key,
                        _kind,
                        _hp,
                        _reach,
                        _spawn,
                        _sx,
                        _sy,
                    )
                except Exception:
                    continue
        elif is_excluded:
            # Preserve the frozen baseline's historical call profile: only
            # excluded groups need their spawn tiles for the static overlay.
            try:
                tiles = group.spawn_tiles()
            except Exception:
                continue
        if is_excluded:
            try:
                excluded |= tiles
            except Exception:
                pass

    # S2.3: boss unlock values (reachability delta). Prefer the values the
    # draft computed in its 10x fuel window (they travel through the team
    # draft memory); fall back to computing them here (first play turn, 2x
    # fuel) when the draft memory carries none.  This is deliberately
    # opt-in: the frozen baseline must not pay the static topology cost or
    # change its draft/play behavior.
    if s23_cache:
        unlock_values = _draft_unlock_values()
        if not unlock_values:
            unlock_values = _compute_unlock_values_cheap(groups, group_meta)
        for key, value in (unlock_values or {}).items():
            meta = group_meta.get(key)
            if meta is not None:
                meta["unlock_value"] = int(value)

    # S2.3 fuel: the tribute approach ring of every altar is static, so it
    # is built once here as a (x, y) -> Vec2 lookup. Per turn the bank
    # candidate asks for the ring tile nearest to us (O(1) arithmetic) and
    # reads THAT tile's distance from the shared field - no per-turn ring
    # rebuild, no per-turn sort of the ring's tiles.
    #
    # S2.3.2 FIX (measured 2026-10-24): the ring radius is an ENGINE
    # constant (cambc.constants.SUBMIT_GEMS_RANGE, resolved into
    # config.TRIBUTE_RANGE by config.resolve()). The map cache used to be
    # built BEFORE the first resolve() call, so the ring was built with
    # radius 0 - the altar tile alone, which is a WALL. Every altar then
    # read back as UNREACHABLE, the bank candidate was never generated,
    # and the bot captured gems it never paid tribute (0 submitted gems
    # on 01starvation: 14 gems captured per champion, submittedGems 0).
    # The engine constant is resolved HERE, before the ring is built.
    altar_rings: dict = {}
    altar_coords: tuple = ()
    altar_approach: dict = {}
    altar_vecs: dict = {}
    altar_regions: dict = {}
    spawn_coords: tuple = ()
    map_size: tuple = (0, 0)
    if s23_cache:
        try:
            config.resolve()
        except Exception:
            pass
        try:
            radius = int(config.TRIBUTE_RANGE)
        except Exception:
            radius = 2
        if radius < 1:
            radius = 1
        try:
            from managers import intelligence as _intel
        except Exception:
            _intel = None
        coords_list = []
        for altar in altars:
            try:
                ax = int(altar.x)
                ay = int(altar.y)
            except Exception:
                continue
            coords_list.append((ax, ay))
            # S2.3 FUEL: the altar's own Vec2 and its region id are STATIC.
            # The enemy-lock test built a fresh Vec2 per altar per turn
            # (~2.4k fuel) and the bank score called region_of() per altar
            # per turn (~800 fuel).
            try:
                altar_vecs[(ax, ay)] = altar
            except Exception:
                pass
            if _intel is not None:
                try:
                    altar_regions[(ax, ay)] = int(_intel.region_of(ax, ay))
                except Exception:
                    pass
            ring: dict = {}
            approach: list = []
            for dy in range(-radius, radius + 1):
                for dx in range(-radius, radius + 1):
                    if max(abs(dx), abs(dy)) > radius:
                        continue
                    try:
                        vec = cambc.Vec2(ax + dx, ay + dy)
                    except Exception:
                        continue
                    ring[(ax + dx, ay + dy)] = vec
                    if dx == 0 and dy == 0:
                        # The altar tile itself is a WALL: it is a legal
                        # tribute position but not a walkable one.
                        continue
                    if terrain_walls is not None:
                        try:
                            if terrain_walls.is_set(vec):
                                continue
                        except Exception:
                            pass
                    approach.append((ax + dx, ay + dy, vec))
            altar_rings[(ax, ay)] = ring
            altar_approach[(ax, ay)] = tuple(approach)
        altar_coords = tuple(coords_list)
        coords = []
        for tile in spawn_positions:
            try:
                coords.append((int(tile.x), int(tile.y), tile))
            except Exception:
                continue
        spawn_coords = tuple(coords)
        try:
            map_size = (int(game_map.width()), int(game_map.height()))
        except Exception:
            map_size = (0, 0)

    # S2.3 fuel: the killable filter reads STATIC facts (the Monolith flag
    # and the cached static HP), which never change during a match, so the
    # set of killable group handles is computed ONCE here. The per-turn
    # zone-pool scan is then a set lookup per group instead of a metadata
    # read (a dict get + an int conversion per group per turn).
    killable_keys: frozenset = frozenset()
    if s23_cache:
        _killable = set()
        for _key, _entry in group_meta.items():
            try:
                if _entry.get("is_monolith", False):
                    continue
                if int(_entry.get("static_hp", 0)) > config.KILLABLE_HP_CAP:
                    continue
            except Exception:
                pass
            _killable.add(_key)
        killable_keys = frozenset(_killable)

    # S2.3 FUEL: the flat per-group FARM ROWS and the CLEAR CLASSES.
    #
    # Everything the farm pool needs per group is a CONSTANT of the match:
    # the group's anchor tile, its real gem/gold totals, its static HP, its
    # monster strength and its region. The per-turn loop then only has to
    # read ONE field distance per candidate and do the arithmetic - the
    # S2.3 second cut paid ~50k fuel per candidate for metadata reads.
    farm_rows: tuple = ()
    farm_classes: tuple = ()
    excluded_groups: tuple = ()
    boss_rows: tuple = ()
    group_by_key: dict = {}
    if s23_cache:
        try:
            from managers import intelligence as _intel2
        except Exception:
            _intel2 = None
        class_ids: dict = {}
        class_list: list = []
        rows: list = []
        excluded_list: list = []
        boss_list: list = []
        by_key: dict = {}
        for key, group in group_index:
            by_key[key] = group
            m = group_meta.get(key) or {}
            rep = group_reps.get(key)
            if rep is None:
                continue
            try:
                gems = float(m.get("gem_value", 0) or 0)
                gold = float(m.get("gold_value", 0) or 0)
                hp = float(m.get("static_hp", 0) or 0)
                strength = float(m.get("total_strength", 0.0) or 0.0)
            except Exception:
                continue
            value = gems + gold * float(config.GOLD_TO_GEM)
            cls_key = (hp, strength)
            cid = class_ids.get(cls_key)
            if cid is None:
                cid = len(class_list)
                class_ids[cls_key] = cid
                class_list.append((hp, strength, cid))
            region = -1
            if _intel2 is not None:
                try:
                    region = int(_intel2.region_of(rep[1], rep[2]))
                except Exception:
                    region = -1
            positions = group_positions.get(key) or ()
            extra_tiles = []
            for p in positions[1:3]:
                extra_tiles.append(p)
            # S2.3.2: the group's SPAN - the Chebyshev diameter of its spawn
            # tiles. It is the walk the champion has to make ACROSS the
            # group to clear it: a group whose camps are spread over 30
            # tiles (the gold rows of 01yellow_brick_road) costs 30 turns
            # of walking on top of the kill turns, and the clear-time
            # estimate has to pay for it (the HP term alone priced a
            # 30-camp row like a 4-camp cluster).
            span = 0
            tiles_xy = group_tile_xy.get(key) or ()
            if tiles_xy:
                xs = [t[0] for t in tiles_xy if t[0] >= 0]
                ys = [t[1] for t in tiles_xy if t[1] >= 0]
                if xs and ys:
                    span = max(max(xs) - min(xs), max(ys) - min(ys))
            rows.append(
                (
                    key,
                    group,
                    rep[0],
                    rep[1],
                    rep[2],
                    value,
                    gems,
                    gold,
                    cid,
                    region,
                    tuple(extra_tiles),
                    span,
                )
            )
            if (
                m.get("is_monolith", False)
                or int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP
            ):
                excluded_list.append((key, group))
                # S2.3 FUEL: the boss's prize (its drops plus the reachability
                # delta it unlocks) is a CONSTANT of the match, so it is
                # computed here instead of per turn.
                try:
                    unlock = float(m.get("unlock_value", 0) or 0)
                except Exception:
                    unlock = 0.0
                boss_list.append(
                    (key, group, value + unlock, cid, rep[1], rep[2], int(unlock))
                )
        farm_rows = tuple(rows)
        farm_classes = tuple(class_list)
        excluded_groups = tuple(excluded_list)
        boss_rows = tuple(boss_list)
        group_by_key = by_key

    bot.map_cache = {
        "altars": altars,
        "spawn": spawn,
        "spawn_tiles": tuple(spawn_positions),
        "groups": groups,
        "group_index": tuple((key, group) for key, group in group_index),
        "zone": zone,
        "group_meta": group_meta,
        "excluded": excluded,
        "group_tiles": group_tiles,
        "group_positions": group_positions,
        "group_camp_count": group_camp_count,
        "camp_info": camp_info,
        "terrain_walls": terrain_walls,
        "group_reps": group_reps,
        "group_anchors": group_anchors,
        "altar_rings": altar_rings,
        "altar_coords": altar_coords,
        "altar_approach": altar_approach,
        "spawn_coords": spawn_coords,
        "map_size": map_size,
        "killable_keys": killable_keys,
        "farm_rows": farm_rows,
        "farm_classes": farm_classes,
        "excluded_groups": excluded_groups,
        "boss_rows": boss_rows,
        "group_by_key": group_by_key,
        "altar_vecs": altar_vecs,
        "altar_regions": altar_regions,
        "group_tile_xy": group_tile_xy,
        "s23_cache": bool(s23_cache),
    }
    snap.altars = altars
    snap.spawn_tile = spawn
    snap.spawn_tiles = tuple(spawn_positions)
    snap.camp_groups = groups
    snap.camp_zone_for_champion = zone
    snap.group_meta = group_meta
    snap.excluded_tiles = excluded
    snap.group_tiles = group_tiles
    snap.group_positions = group_positions
    snap.group_camp_count = group_camp_count
    snap.camp_info = camp_info
    snap.group_index = tuple(group_index)
    snap.terrain_walls = terrain_walls
    snap.group_reps = group_reps
    snap.group_anchors = group_anchors
    snap.altar_rings = altar_rings
    snap.altar_coords = altar_coords
    snap.altar_approach = altar_approach
    snap.spawn_coords = spawn_coords
    snap.map_size = map_size
    snap.killable_keys = killable_keys
    snap.farm_rows = farm_rows
    snap.farm_classes = farm_classes
    snap.excluded_groups = excluded_groups
    snap.boss_rows = boss_rows
    snap.group_by_key = group_by_key
    snap.altar_vecs = altar_vecs
    snap.altar_regions = altar_regions
    snap.group_tile_xy = group_tile_xy
    snap.s23_cache = bool(s23_cache)


def _compute_unlock_values_cheap(groups, group_meta) -> dict:
    """Boss unlock values computed on the first play turn.

    The draft normally ships these through the team draft memory (the
    draft's 10x-fuel window is the right place for a static topology
    cost). This fallback is the safety net for a draft that ran out of
    fuel - it is deliberately SMALLER (fewer doors, smaller flood radius)
    so the first play turn can afford it: the first turn's budget is
    2x and the map cache has already spent ~1.5M of it.
    """
    try:
        fuel = int(cambc.fuel_remaining())
    except Exception:
        fuel = 0
    if fuel < int(config.UNLOCK_MIN_FUEL):
        return {}
    try:
        from managers import topology
        return topology.compute_unlock_values(
            groups,
            group_meta,
            max_groups=3,
            radius=64,
        )
    except Exception:
        return {}


def _draft_unlock_values() -> dict:
    """Read the boss unlock values the draft phase shipped (format v2)."""
    try:
        from managers import topology
        raw = bytes(cambc.DraftMemoryBuffer())
        if len(raw) < 5 or int(raw[0]) != topology.DRAFT_MEMORY_VERSION:
            return {}
        pick_count = int(raw[1])
        offset = topology.unlock_section_offset(pick_count)
        return topology.unpack_unlock_section(raw, offset)
    except Exception:
        return {}


def build_group_meta(groups, s23_cache: bool = False) -> dict:
    """Per-group static metadata, keyed by engine handle (group_key).

    is_monolith: any camp of the group is a Monolith. static_hp: sum of
    the camps' base health (GameRules camp table is static HP; the
    precompute is probe-verified). Powers the candidate gate's
    position-independent HP check (architecture.md Stage 2 killable
    definition, amended 2026-10-21) and the opportunistic-attack filter.

    S2.3 adds the REAL per-group economy (gem_value / gold_value, read from
    the engine's static map data) plus the group's monster strength and camp
    count. The old flat estimates (FARM_VISIBLE_GEM_ESTIMATE /
    FARM_ZONE_GEM_ESTIMATE) made every farm candidate look alike, so farm
    picks were nearest-first by construction.

    S2.3 fuel: `camp.base_stats()` is a per-CAMP-TYPE design lookup
    (`_bc.camp_design(type)`), so it is cached per type - five calls for the
    whole map instead of one per camp. On a 118-camp map that was 1.5M fuel
    of the first play turn's budget (the map cache build measured 3.8M
    against the 5M first-turn budget).

    S2.3.4: the design table is the ENGINE's (`CampStats.base_health` /
    `base_strength` / `base_attack_range`), not the table in
    ref/GameRules.md and not a hand-written constant. The hand-written
    table was missing a camp kind (the 5000-HP / 120-strength monster), and
    the missing kind's strength read back as 0 - which priced every
    big-monster group as a free prize (the boss candidate won the argmax
    on every turn of 00road_of_riches and the bot farmed nothing).

    The S2.3-only fields (economy, strength) are skipped unless
    `s23_cache` is set, so the frozen Stage 2 baseline keeps its
    historical first-turn engine-call profile.
    """
    from cambc import CampType
    meta: dict = {}
    designs: dict = {}
    for group in groups:
        is_monolith = False
        static_hp = 0
        max_strength = 0.0
        total_strength = 0.0
        camp_count = 0
        try:
            for camp in group.camps():
                camp_count += 1
                try:
                    kind = int(camp.kind())
                except Exception:
                    kind = -1
                try:
                    if kind == int(CampType.MONOLITH):
                        is_monolith = True
                except Exception:
                    pass
                try:
                    design = designs.get(kind)
                    if design is None:
                        design = camp.base_stats()
                        designs[kind] = design
                    _CAMP_DESIGN[kind] = (
                        float(design.base_health),
                        float(design.base_strength),
                        int(design.base_attack_range),
                    )
                    # S2.3.4: the strength comes from the engine's own
                    # design table (a camp kind that is missing from the
                    # hand-written table would otherwise read back as 0 and
                    # price its group as harmless).
                    strength = float(design.base_strength)
                    total_strength += strength
                    if strength > max_strength:
                        max_strength = strength
                except Exception:
                    continue
                try:
                    static_hp += float(design.base_health)
                except Exception:
                    continue
        except Exception:
            is_monolith = False
        entry = {
            "is_monolith": is_monolith,
            "static_hp": int(static_hp),
            "camp_count": camp_count,
            # S2.3: reachability delta of this group (boss unlock value).
            # Filled from the draft memory or the first-play-turn precompute.
            "unlock_value": 0,
        }
        if s23_cache:
            try:
                gems = int(group.gem_value())
            except Exception:
                gems = 0
            try:
                gold = int(group.gold_value())
            except Exception:
                gold = 0
            entry["gem_value"] = gems
            entry["gold_value"] = gold
            entry["max_strength"] = max_strength
            entry["total_strength"] = total_strength
        meta[group_key(group)] = entry
    return meta


# ---------------------------------------------------------------------------
# Per-turn camp facts (S2.3).
# ---------------------------------------------------------------------------


def _read_visible_camps(snap) -> None:
    """Read the visible camps once per turn and derive the camp facts.

    The S2.3 path resolves a visible camp to its group through the
    first-turn camp table (a dict lookup) instead of the SDK's
    ``camp.camp_group()`` call (~57k fuel per camp!). The camp's STATE
    (dead / awake / asleep) is one SDK call each and is only read for
    camps whose kind can actually threaten us.

    The camp states also DERIVE the group state (the engine's
    ``group.state()`` costs ~49k per group per turn because it re-reads the
    vision bitboard and probes every camp of the group):

      - a group whose camps are ALL visible and ALL dead has its reward
        available (the engine's CampGroupRewardAvailable);
      - a group with a live camp is alive;
      - anything else is unknown (the group is only partly in vision).

    The derivation is exact for a fully visible group, and a camp group is
    a cluster of cardinally connected monsters, so its tiles are normally
    all inside one vision radius.
    """
    try:
        camps = cambc.vision.camps()
    except Exception:
        camps = ()
    snap.visible_camps = tuple(camps)
    camp_info = getattr(snap, "camp_info", None) or {}
    live_hp: dict = {}
    counts: dict = {}
    dead: dict = {}
    awake: list = []
    keys = set()
    camp_states: dict = {}
    # S2.3.2: the LIVE camps of every visible group. A dead camp is never
    # reported by ``vision.camps()`` (probe-verified 2026-10-24), so every
    # camp in this list is alive - the list is the group's live spawn tiles
    # without a single engine read. The farm candidate walks to the nearest
    # live camp instead of the group's static representative tile: the
    # static anchor is the FIRST camp's spawn tile, and a champion that
    # killed that camp used to be PINNED on it forever (the distance stayed
    # 0, so the score stayed maximal, and the walk refused to move because
    # we already stood on the goal tile).
    live_tiles: dict = {}
    for camp in camps:
        try:
            handle = int(camp.__index__())
        except Exception:
            continue
        info = camp_info.get(handle)
        if info is None:
            continue
        key = info[0]
        kind = info[1]
        base_hp = info[2]
        reach = info[3]
        keys.add(key)
        counts[key] = counts.get(key, 0) + 1
        spawn = info[4] if len(info) > 4 else None
        if spawn is not None:
            entry = live_tiles.get(key)
            if entry is None:
                live_tiles[key] = [(spawn, info[5], info[6])]
            else:
                entry.append((spawn, info[5], info[6]))
        design = _CAMP_DESIGN.get(kind)
        if design is not None:
            strength = float(design[1])
        else:
            strength = float(config.MONSTER_STRENGTH.get(kind, 0.0))
        if strength <= 0.0:
            # S2.3 FUEL: a strength-0 camp (a gem cluster or a gold cluster)
            # cannot threaten anybody: its monsters deal no damage, so it
            # never enters the threat model, and its health / state are read
            # by nobody (snap.group_live_hp and snap.camp_states are write-
            # only facts). The two SDK calls per camp are therefore pure
            # waste for these camps - and they are the MAJORITY of the
            # visible camps on the gold-heavy maps (01yellow_brick_road: ~16
            # of ~22 visible camps are strength-0 clusters).
            continue
        # S2.3.4 fuel: the camp's HEALTH is one SDK call, the camp's STATE
        # is another. A camp at FULL health has never been struck, so its
        # monster is still asleep (GameRules: "the monsters are friendly
        # until they are hit") and the state read buys nothing: the threat
        # model only cares about AWAKE monsters. The state read is therefore
        # spent only on a camp that actually took damage.
        try:
            hp = float(camp.health())
        except Exception:
            hp = 0.0
        if hp <= 0.0:
            continue
        is_awake = False
        if hp < base_hp:
            # One SDK call: the camp's state tells us whether its monster is
            # dead, awake or asleep. Only an AWAKE monster threatens the path.
            try:
                state = camp.state()
                is_awake = bool(state.is_awake())
                is_dead = bool(state.is_dead())
            except Exception:
                continue
            camp_states[handle] = state
            if is_dead:
                dead[key] = dead.get(key, 0) + 1
        if not is_awake:
            continue
        live_hp[key] = live_hp.get(key, 0.0) + hp
        try:
            # S2.3.4: the threat carries the monster's OWN attack range (the
            # engine's CampStats.base_attack_range), not a flat 3.
            awake.append((camp.spawn_position(), strength, reach))
        except Exception:
            continue
    snap.visible_group_keys = frozenset(keys)
    snap.group_visible_camps = counts
    snap.group_live_hp = live_hp
    snap.awake_monsters = tuple(awake)
    snap.camp_states = camp_states
    snap.group_dead_camps = dead
    snap.group_live_tiles = live_tiles
    _derive_group_states(snap, counts, dead)


def _derive_group_states(snap, counts: dict, dead: dict) -> None:
    """Derive the per-group state from the visible camp states.

    "reward" - every camp of the group is visible and dead: the reward is
               available for capture.
    "alive"  - the group has a live camp (asleep or awake).
    (absent) - the group is only partly in vision: the state is unknown.

    NOTE (S2.3, measured 2026-10-24): a DEAD camp is never reported by
    ``vision.camps()``, so the "reward" branch is UNREACHABLE - the camp
    count of a fully cleared group is 0, never 3. The capture candidate
    therefore has to come from the engine's own group state
    (``_detect_cleared_groups``), not from this derivation. The derived
    states are still used for the "is this group alive" test (a group with
    a visible camp is alive).
    """
    totals = getattr(snap, "group_camp_count", {}) or {}
    states: dict = {}
    for key, visible in counts.items():
        total = int(totals.get(key, visible))
        dead_count = int(dead.get(key, 0))
        if total > 0 and visible >= total and dead_count >= total:
            states[key] = "reward"
        else:
            states[key] = "alive"
    snap.group_derived_states = states


def _detect_cleared_groups(snap) -> None:
    """Find the groups whose reward is available for capture.

    The engine's answer is ``group.state()``:
      - ``CampGroupRewardAvailable`` - every monster of the group is dead
        and the reward (gems + gold) waits on its spawn tiles;
      - ``CampGroupRespawning``      - the reward was already claimed, the
        group is on its respawn timer;
      - ``CampGroupAlive``           - the group still has a live monster.

    The read costs ~28k fuel, so it is spent only where it can answer the
    question:

      - a group with a VISIBLE camp has a live monster, so it is alive.
        The visible camp set is free (one vision read per turn), so this
        test costs nothing.
      - a group whose cached anchor tile is farther than
        CLEAR_SCAN_RADIUS tiles is out of our vision: ``group.state()``
        would raise the SDK's visibility error, so it is skipped without
        an engine call.

    The radius is generous (the map's vision radius is 8 tiles and a camp
    group is a cluster of cardinally connected monsters), so the group the
    bot just cleared is always inside the scan.

    S2.3.2: the RESPAWNING state is recorded too. A respawning group has
    no live monster, so it is not a farm target - but the S2.3 second cut
    scored it as one (the group's full gem value, distance 0 because the
    champion stands on its tile) and that candidate outscored everything
    else for 1,900 rounds. The claim is also remembered on the Bot so it
    survives the walk out of the group's vision (see claimed_group_keys).
    """
    snap.cleared_groups = ()
    snap.claimed_groups = ()
    if not bool(getattr(snap, "s23_cache", False)):
        return
    visible_keys = getattr(snap, "visible_group_keys", frozenset()) or frozenset()
    anchors = getattr(snap, "group_anchors", None) or {}
    try:
        radius = int(config.CLEAR_SCAN_RADIUS)
    except Exception:
        radius = 12
    px, py = snap.origin_xy
    out = []
    claimed = []
    for key, group in getattr(snap, "group_index", ()) or ():
        if key in visible_keys:
            continue
        entry = anchors.get(key)
        if entry is None:
            continue
        dx = entry[0] - px
        if dx < 0:
            dx = -dx
        dy = entry[1] - py
        if dy < 0:
            dy = -dy
        if (dx if dx > dy else dy) > radius:
            continue
        try:
            state = group.state()
        except Exception:
            # Not in vision (or the engine refused the read).
            continue
        try:
            if isinstance(state, game_map.CampGroupRewardAvailable):
                out.append((key, group))
            elif isinstance(state, game_map.CampGroupRespawning):
                claimed.append((key, group))
        except Exception:
            continue
    snap.cleared_groups = tuple(out)
    snap.claimed_groups = tuple(claimed)
    _remember_claims(snap, claimed)


def _remember_claims(snap, claimed) -> None:
    """Remember on the Bot which groups had their reward claimed.

    The memory is per champion (each champion runs in its own process with
    its own Bot instance), so it is the champion's OWN fact: "I saw this
    group in the respawning state, so its reward is gone". It expires when
    the group respawns (RESPAWN_ROUNDS), so a group is farmable again
    after its cooldown.
    """
    if not claimed:
        return
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    try:
        memory = getattr(bot, "claimed_groups", None)
        if not isinstance(memory, dict):
            memory = {}
            try:
                bot.claimed_groups = memory
            except Exception:
                return
        now = int(getattr(snap, "round_number", 0) or 0)
        for entry in claimed:
            try:
                memory[entry[0]] = now
            except Exception:
                continue
    except Exception:
        pass


def claimed_group_keys(snap) -> frozenset:
    """The groups that must NOT be farmed this turn.

    Three sources:
      - the groups whose reward is waiting (a CAPTURE, not a farm);
      - the groups seen in the respawning state (their reward is gone);
      - the champion's own claim memory (a claim seen earlier, still
        inside the respawn window).

    The memory is what stops the "farm a dead group forever" loop: the
    group is out of vision by the time the champion walks away, so the
    per-turn state read cannot answer any more.
    """
    keys = set()
    for source in (
        getattr(snap, "cleared_groups", ()) or (),
        getattr(snap, "claimed_groups", ()) or (),
    ):
        for entry in source:
            try:
                keys.add(entry[0])
            except Exception:
                continue
    bot = getattr(snap, "bot", None)
    if bot is not None:
        try:
            memory = getattr(bot, "claimed_groups", None)
            if isinstance(memory, dict) and memory:
                now = int(getattr(snap, "round_number", 0) or 0)
                window = int(config.RESPAWN_ROUNDS) or 60
                for key, when in memory.items():
                    if now - int(when) < window:
                        keys.add(key)
        except Exception:
            pass
    return frozenset(keys)



def snapshot(bot, s23_cache: bool = False) -> Snapshot:
    """Build the per-turn observation snapshot; caches map data on bot.

    ``s23_cache`` is an explicit opt-in for the S2.3 shared-field path.
    The default is the historical Stage 2 path so callers such as the
    frozen ``bot_baseline.py`` retain their engine-call profile.
    """
    me = cambc.me()
    snap = _me_facts(me, s23_cache=s23_cache)
    # The bot is needed by the field/path caches (managers.fielding) and by
    # the cleared-group scan, so it is attached before anything reads them.
    snap.bot = bot
    if s23_cache:
        # One engine read per turn: the claim memory expires on it.
        try:
            snap.round_number = int(cambc.round_number())
        except Exception:
            snap.round_number = 0
    _populate_map_cache(bot, snap, s23_cache=s23_cache)
    if s23_cache:
        # S2.3 path: the visible camps carry the group facts, so the
        # per-group "is this group visible" AND (one SDK call per group)
        # is replaced by one vision read.
        _read_visible_camps(snap)
        # The capture candidate needs the groups whose reward is waiting.
        _detect_cleared_groups(snap)
        # S2.3 fuel: the enemy altar locks are only READ when we could
        # bank: the lock set only decides whether an altar is a candidate,
        # and with no gems there is no bank candidate at all (the read is
        # ~20k fuel a turn). With gems the read happens as before.
        if snap.gems >= config.BANK_THRESHOLD:
            try:
                snap.enemy_altar_locks = cambc.vision.altar_lock_tiles(
                    cambc.Team.ENEMY
                )
            except Exception:
                snap.enemy_altar_locks = None
        snap.visible_camp_groups = tuple(
            snap.group_by_key[k]
            for k in snap.visible_group_keys
            if k in snap.group_by_key
        )
        snap.visible_enemies = tuple(cambc.vision.champions(cambc.Team.ENEMY))
    else:
        visible = cambc.vision.visible_tiles()
        visible_groups = []
        for group in snap.camp_groups:
            tiles = group.spawn_tiles()
            if not (tiles & visible).is_empty():
                visible_groups.append(group)
        snap.visible_camp_groups = tuple(visible_groups)
        snap.visible_enemies = tuple(cambc.vision.champions(cambc.Team.ENEMY))
    snap.enemy_memory = getattr(bot, "enemy_memory", None) or {}
    refresh_threat_memory(bot, snap)
    return snap


def refresh_threat_memory(bot, snap) -> None:
    """Fold the fog memory into the snapshot's threat records.

    The threat model (managers.fielding.threats) needs the monsters we can
    see, the enemies we can see and the enemies we REMEMBER. The last group
    is the only threat input outside our own vision, so it is what makes
    the carried-gem risk term honest when the enemy is out of sight.

    Only records inside the TTL (ENEMY_TRACK_TTL) are used - an old sighting
    is a guess about a place, not a threat that is there.
    """
    try:
        from managers import intelligence
        records = intelligence.recent_sightings(bot)
    except Exception:
        records = {}
    out = []
    for record in (records or {}).values():
        try:
            out.append(
                {
                    "x": int(record["x"]),
                    "y": int(record["y"]),
                    "strength": float(record.get("strength", 0.0)),
                    "range": int(record.get("range", config.MONSTER_RANGE)),
                    "round": int(record.get("round", 0)),
                }
            )
        except Exception:
            continue
    snap.threat_memory = tuple(out)

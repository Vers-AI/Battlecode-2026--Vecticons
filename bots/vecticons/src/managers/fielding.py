"""Shared per-turn distance infrastructure (S2.3 - honest distances).

Replaces the Stage 3 straight-line proxies with ONE native engine
DistanceField per turn. The engine's field is a uniform-cost 8-directional
BFS (Chebyshev), so `discount ** true_distance` prices detours naturally
and a sealed target is simply never reached (distance sentinel) - no
special case, no scenario detection.

Two wall sets (owner-ratified 2026-10-23, two-wall-set design):

  SCORING field - feeds `d` for farm / bank / capture / boss scoring.
      walls = terrain (walls | holes | altars | enemy spawn)
            + the STATIC overlay of excluded groups' spawn tiles
      This is exactly the wall set execution.reachable_tiles_from ships:
      the overlay is deliberately NOT vision-derived (a vision-derived overlay
      flickered with vision and re-created the 00ambush target-flip
      oscillator). It is position-independent by construction, so the field
      never oscillates. Consequence: the altar behind boss_battle's Monolith
      corridor is unreachable and its bank candidate scores 0 BY ARITHMETIC.

  BOSS-ENGAGEMENT field - feeds the boss fight candidates.
      walls = terrain only (the boss group's own tiles are DESTINATIONS,
      never walls): champions fight the boss from the corridor edge.
      Monster proximity is priced as path exposure (the threat term), not
      as walls.

Read paths via the shared field / next_move_from - the engine's gradient is
walked, never re-implemented in Python (fuel).

Perf notes (measured 2026-10-23, CLI 3.0.4 / engine 1.0.2, 80x80 map):
  - DistanceField() construction            ~24k fuel
  - field.compute(max=4096)  (full sweep)   ~518k fuel
  - field.get(tile)                         ~2.1k fuel
  - field.next_move_from(tile)              ~2.2k fuel
  - Vec2.x / Vec2.y (a coordinate read)     ~1.8k fuel
  - game_map.camp_groups()                  ~465k fuel (FIRST TURN ONLY)
  - CampGroup.state()                       ~49k fuel (it re-reads the whole
    vision bitboard and then probes every camp's spawn tile - one SDK call
    each). The S2.3 layer avoids it on the hot path (see observation).
  So the design rules are: ONE field per turn, NO second full-map field for
  movement (the gradient is walked instead), NO coordinate read in a
  per-turn loop (every coordinate the hot path uses is cached as a plain
  int by the observation layer), and as few `get` reads as the candidate
  pool allows.

FIELD CACHE: the scoring field is a pure function of (our position, the
static wall set), so it is cached on the Bot by position. A turn on which
we do not move (fighting, channelling, fuel-suspended) reuses the field
instead of paying another ~518k.
"""
from __future__ import annotations

from typing import Any

import cambc
from cambc import game_map

import config

# The engine's "unreached" sentinel (verified by probe 2026-10-23: a sealed
# target reads back as 65535).
UNREACHABLE: int = 65535

# Terrain blockers are static for the life of a match/process. Keep one
# copy instead of rebuilding four engine-backed bitboards every turn.
_TERRAIN_WALLS: Any = None


# ---------------------------------------------------------------------------
# Wall sets.
# ---------------------------------------------------------------------------


def terrain_walls() -> Any:
    """Static terrain blockers: walls, holes, altars, enemy spawn.

    Altars are walls for movement (you stand next to them to tribute) and
    the enemy spawn region is impassable (GameRules). The result is cached
    per process; callers that mutate it must copy it first.
    """
    global _TERRAIN_WALLS
    if _TERRAIN_WALLS is not None:
        return _TERRAIN_WALLS
    blocked = (
        game_map.walls() | game_map.holes() | game_map.altars()
        | game_map.spawn_region(cambc.Team.ENEMY)
    )
    _TERRAIN_WALLS = blocked
    return blocked


def scoring_walls(snap) -> Any:
    """The scoring wall set: terrain + the STATIC excluded-group overlay."""
    cached = getattr(snap, "scoring_walls", None)
    if cached is not None:
        return cached
    walls = terrain_walls().copy()
    overlay = getattr(snap, "excluded_tiles", None)
    if overlay is not None:
        walls |= overlay
    snap.scoring_walls = walls
    return walls


# ---------------------------------------------------------------------------
# The shared scoring field.
# ---------------------------------------------------------------------------


def scoring_field(snap) -> Any:
    """The shared per-turn scoring DistanceField (cached by position).

    roots = our position; distances are read per candidate target from the
    field. The engine's plain `compute` is used deliberately: it is a full
    sweep, but it is the ONLY variant that distinguishes "sealed behind a
    door" from "far away" for every tile of the map. The destination-
    limited `compute_to` stops only once EVERY destination is reached, so
    a single unreachable destination turns it into the same full sweep
    while a many-destination set makes it strictly more expensive (probe
    evidence: 132 destinations exhausted the turn).

    `visited` is the reachable set (it replaces the old per-turn
    reachable_tiles BFS in execution.reachable_tiles_from).

    The field depends only on our position and the static wall set, so it
    is cached on the Bot by position: a turn on which we do not move costs
    no field at all.
    """
    field = getattr(snap, "distance_field", None)
    if field is not None:
        return field
    bot = getattr(snap, "bot", None)
    origin = getattr(snap, "origin_xy", (0, 0))
    key = (int(origin[0]), int(origin[1]))
    if bot is not None:
        cached = getattr(bot, "field_cache", None)
        if cached is not None and cached[0] == key:
            snap.distance_field = cached[1]
            snap.field_visited = cached[2]
            return cached[1]
    walls = scoring_walls(snap).copy()
    walls.set(snap.position, False)
    roots = cambc.Bitboard()
    roots.set(snap.position)
    visited = cambc.Bitboard()
    field = cambc.DistanceField()
    try:
        field.compute(walls, roots, visited, int(config.FIELD_MAX_DISTANCE))
    except Exception:
        return None
    snap.distance_field = field
    snap.field_visited = visited
    if bot is not None:
        try:
            bot.field_cache = (key, field, visited)
        except Exception:
            pass
    return field


def reachable_set(snap) -> Any:
    """The reachable set under the scoring wall set (None when unknown)."""
    if getattr(snap, "distance_field", None) is None:
        return None
    return getattr(snap, "field_visited", None)


def chebyshev(a, b) -> int:
    """Chebyshev distance between two tiles (the engine's movement metric).

    S2.3 fuel: this reads four Vec2 coordinates out of the engine (~7k
    fuel). The hot paths use the cached integer coordinates instead.
    """
    try:
        return max(abs(int(a.x) - int(b.x)), abs(int(a.y) - int(b.y)))
    except Exception:
        return 0


def nearest_tile(field, tiles, origin=None, limit: int | None = None):
    """The reachable tile of `tiles` nearest to us: (distance, tile).

    Kept for the callers that have no cached integer coordinates; the
    S2.3 hot paths use group_distance / altar_distance / spawn_distance,
    which never read a Vec2 coordinate.
    """
    if field is None or not tiles:
        return None, None
    ordered = list(tiles)
    if origin is not None:
        ordered.sort(key=lambda t: chebyshev(t, origin))
    if limit:
        ordered = ordered[: int(limit)]
    best = None
    best_tile = None
    for tile in ordered:
        try:
            d = int(field.get(tile))
        except Exception:
            continue
        if d >= UNREACHABLE:
            continue
        if best is None or d < best:
            best = d
            best_tile = tile
    return best, best_tile


def distance_to(field, tiles) -> int | None:
    """Distance from our position to the nearest of `tiles`.

    None when the target is unreachable (or beyond the field's radius) -
    callers score those 0 (unreachable scores 0 by arithmetic).
    """
    d, _tile = nearest_tile(field, tiles)
    return d


def group_tiles_by_distance(snap, field, key, limit: int | None = None):
    """The group's spawn tiles ordered by their field distance: [(d, tile)].

    A capture needs a tile of the group that is actually FREE: the group
    is a cluster of a few tiles and five champions want the same one, so
    the engine refuses the move ("Known occupied destination") and the
    champion used to stay wedged. Offering every tile of the group lets
    the movement fall back to a free one.

    The reads are bounded (FARM_TILE_READS): a group is a cluster of
    cardinally connected monsters, so its tiles sit within a tile or two
    of each other.
    """
    if field is None:
        return []
    positions = (getattr(snap, "group_positions", None) or {}).get(key) or ()
    if not positions:
        return []
    try:
        reads = int(config.FARM_TILE_READS if limit is None else limit)
    except Exception:
        reads = 3
    if reads < 1:
        reads = 1
    out = []
    for tile in positions[:reads]:
        try:
            d = int(field.get(tile))
        except Exception:
            continue
        if d >= UNREACHABLE:
            continue
        out.append((d, tile))
    out.sort(key=lambda item: item[0])
    return out


def group_distance(snap, field, key):
    """Honest distance to a camp group: (distance, tile).

    The group's REPRESENTATIVE tile (the first camp's spawn position,
    cached by the observation layer) is the distance anchor: one field
    read. When that tile is unreachable the group's other cached tiles are
    tried, so a group whose anchor tile happens to sit behind a door is
    still reachable through its cluster.

    A group is a cluster of cardinally connected monsters, so its tiles
    are within a few tiles of each other: the anchor tile's distance is
    the group's distance to within a couple of turns.
    """
    if field is None:
        return None, None
    reps = getattr(snap, "group_reps", None) or {}
    entry = reps.get(key)
    if entry is None:
        return None, None
    anchor = entry[0]
    try:
        d = int(field.get(anchor))
    except Exception:
        return None, None
    if d < UNREACHABLE:
        return d, anchor
    best = None
    best_d = None
    positions = (getattr(snap, "group_positions", None) or {}).get(key) or ()
    for tile in positions[: int(config.FARM_TILE_READS) + 1]:
        try:
            td = int(field.get(tile))
        except Exception:
            continue
        if td >= UNREACHABLE:
            continue
        if best_d is None or td < best_d:
            best_d = td
            best = tile
    return best_d, best


def altar_distance(snap, field, ax: int, ay: int):
    """Honest distance to the walkable tile nearest us around an altar.

    The tribute approach ring is a Chebyshev disk of radius TRIBUTE_RANGE
    around the altar (the altar tile itself is a wall, so it is not a
    walkable approach). The observation layer caches the ring's WALKABLE
    tiles as (x, y, Vec2) tuples.

    S2.3.2 FIX: the ring used to be a plain (x, y) -> Vec2 map that was
    built with the UNRESOLVED config.TRIBUTE_RANGE (0), so the ring held
    the altar tile alone - a wall - and every altar read back as
    UNREACHABLE. The bank candidate was therefore never generated and the
    bot captured gems it never paid tribute (0 submitted gems on
    01starvation).

    The honest distance to an altar is the distance to the cheapest
    approach tile we can actually stand on. The approach tiles are ranked
    by their straight-line distance (plain ints, no engine call) and only
    the nearest few pay for a field read: the ring is tiny, so the
    straight-line rank and the true path rank agree to within a tile or
    two, and a sealed altar reads as unreachable (score 0 by arithmetic).
    """
    if field is None:
        return None, None
    approach = getattr(snap, "altar_approach", None) or {}
    tiles = approach.get((int(ax), int(ay)))
    if not tiles:
        return None, None
    px, py = snap.origin_xy
    ranked = []
    for entry in tiles:
        try:
            tx = int(entry[0])
            ty = int(entry[1])
        except Exception:
            continue
        dx = tx - px
        if dx < 0:
            dx = -dx
        dy = ty - py
        if dy < 0:
            dy = -dy
        ranked.append((dx if dx > dy else dy, tx, ty, entry[2]))
    if not ranked:
        return None, None
    ranked.sort()
    try:
        reads = int(config.ALTAR_DISTANCE_READS)
    except Exception:
        reads = 4
    if reads < 1:
        reads = 1
    best = None
    best_d = None
    for _cheap, _tx, _ty, tile in ranked[:reads]:
        try:
            d = int(field.get(tile))
        except Exception:
            continue
        if d >= UNREACHABLE:
            continue
        if best_d is None or d < best_d:
            best_d = d
            best = tile
    return best_d, best


def spawn_distance(snap, field):
    """Honest distance to the nearest tile of our spawn region.

    The spawn tiles are cached with their integer coordinates, so the
    nearest one is found with pure Python arithmetic and one field read.
    """
    if field is None:
        return None, None
    spawns = getattr(snap, "spawn_coords", None) or ()
    if not spawns:
        return None, None
    px, py = snap.origin_xy
    best = None
    best_d = None
    for entry in spawns:
        try:
            sx = int(entry[0])
            sy = int(entry[1])
        except Exception:
            continue
        dx = sx - px
        if dx < 0:
            dx = -dx
        dy = sy - py
        if dy < 0:
            dy = -dy
        if best_d is None or (dx if dx > dy else dy) < best_d:
            best_d = dx if dx > dy else dy
            best = entry
    if best is None:
        return None, None
    try:
        d = int(field.get(best[2]))
    except Exception:
        return None, None
    if d >= UNREACHABLE:
        return None, None
    return d, best[2]


# ---------------------------------------------------------------------------
# The gradient walk: movement + path exposure from ONE field.
# ---------------------------------------------------------------------------


def _same(a, b) -> bool:
    try:
        return int(a.x) == int(b.x) and int(a.y) == int(b.y)
    except Exception:
        return a == b


def goal_walk(snap, field, goal, limit: int | None = None):
    """Walk the field's gradient from `goal` back to our position.

    The field's roots are OUR position, so the gradient of every tile points
    back at us. Walking it from the goal yields the tiles of the path we
    would walk, in order: tiles[0] is the goal, tiles[-1] is our own tile.

    FUEL (probe 2026-10-23, records/s23-probe/movement-fuel.md): the walk
    costs ~3.6k fuel per step (next_move_from ~2.2k + the Vec2 addition
    ~1k). The number of steps is known in advance: the gradient lowers the
    field distance by exactly one per step, so the walk from the goal takes
    `field.get(goal)` steps to arrive at our own tile. Knowing the length
    avoids a per-step position comparison (a Vec2 coordinate read is
    ~1.8k fuel).

    Only the first PATH_EXPOSURE_STEPS tiles are added to the exposure
    bitboard (the tiles next to the destination, where the destination's
    monsters wait); the rest of the path is only needed for the movement
    step, which the cached cursor indexes.

    Returns (exposure_bitboard, tiles, reached):
      exposure - bitboard of the tiles next to the goal
      tiles    - the walked tiles in order (goal first, our tile last)
      reached  - True when the walk arrived at our position
    """
    exposure = cambc.Bitboard()
    tiles: list = []
    if field is None or goal is None:
        return exposure, tiles, False
    try:
        steps = int(field.get(goal))
    except Exception:
        steps = 0
    if steps >= UNREACHABLE:
        return exposure, tiles, False
    limit = int(config.PATH_MAX_STEPS if limit is None else limit)
    if steps > limit:
        steps = limit
    try:
        exposure.set(goal)
    except Exception:
        pass
    tiles.append(goal)
    current = goal
    exposure_steps = int(config.PATH_EXPOSURE_STEPS)
    for index in range(steps):
        try:
            step = field.next_move_from(current)
        except Exception:
            step = None
        if step is None:
            return exposure, tiles, False
        current = current + step
        if index < exposure_steps:
            try:
                exposure.set(current)
            except Exception:
                pass
        tiles.append(current)
    return exposure, tiles, True


# ---------------------------------------------------------------------------
# The path cache: one gradient walk per goal, reused across turns.
# ---------------------------------------------------------------------------
#
# The scoring wall set (terrain + the static excluded-group overlay) and the
# boss wall set (terrain only) are STATIC for the life of the match, so the
# gradient path between two fixed tiles never changes. The cache therefore
# holds the walked tile list and the exposure bitboard, and the movement
# step costs NO engine call once the path is cached.
#
# The cache lives on the Bot instance (it survives turns, unlike the
# snapshot) and is keyed by (wall set kind, goal tile). A small LRU bound
# keeps a target that flip-flops between two camps from rebuilding the walk
# every turn.

PATH_CACHE_ENTRIES: int = 4


def _path_cache(bot) -> dict:
    cache = getattr(bot, "path_cache", None)
    if not isinstance(cache, dict):
        cache = {}
        try:
            bot.path_cache = cache
        except Exception:
            pass
    return cache


def path_entry(bot, snap, kind: str, goal):
    """The cached walk toward `goal`: {tiles, cursor, exposure, reached}.

    `kind` is "scoring" (the shared field's wall set) or "boss" (the
    terrain-only boss wall set).

    S2.3.2 FIX: a STALE entry used to be served forever. The walk is
    rooted at our position, so when the champion left the path (a refused
    move, a flee, a push) the entry was marked "not reached" - and the
    cache handed that dead entry back on every later turn, so the
    champion could never walk toward that goal again. A dead entry is now
    dropped and rebuilt.
    """
    if goal is None or bot is None:
        return None
    try:
        key = (str(kind), int(goal.x), int(goal.y))
    except Exception:
        return None
    cache = _path_cache(bot)
    entry = cache.get(key)
    if entry is not None:
        if entry.get("reached"):
            entry["used"] = _path_tick(bot)
            return entry
        # Stale: the champion left the path. Rebuild it against the
        # current position instead of serving a dead walk.
        try:
            del cache[key]
        except Exception:
            pass
    if kind == "boss":
        field = boss_field(snap)
    else:
        field = scoring_field(snap)
    exposure, tiles, reached = goal_walk(snap, field, goal)
    entry = {
        "tiles": tiles,
        "cursor": len(tiles) - 1,
        "exposure": exposure,
        "reached": reached,
        "pending": None,
        "used": _path_tick(bot),
    }
    if len(cache) >= PATH_CACHE_ENTRIES:
        _evict_oldest(cache)
    cache[key] = entry
    return entry


def _path_tick(bot) -> int:
    """A monotonically increasing counter for the path cache's LRU order."""
    try:
        tick = int(getattr(bot, "path_tick", 0)) + 1
        bot.path_tick = tick
        return tick
    except Exception:
        return 0


def _evict_oldest(cache: dict) -> None:
    """Drop the least recently used path entry.

    The S2.3 second cut CLEARED the whole cache when it overflowed, so a
    target that flip-flopped between two camps rebuilt its walk (up to
    163 gradient reads) on every turn. The LRU keeps the neighbours of the
    current target alive.
    """
    oldest_key = None
    oldest_tick = None
    for key, entry in cache.items():
        try:
            tick = int(entry.get("used", 0))
        except Exception:
            tick = 0
        if oldest_tick is None or tick < oldest_tick:
            oldest_tick = tick
            oldest_key = key
    if oldest_key is not None:
        try:
            del cache[oldest_key]
        except Exception:
            pass


def step_toward(bot, snap, kind: str, goal):
    """The tile to step to in order to move toward `goal`.

    Returns (tile, entry) - the entry carries the cursor that `advance`
    moves once the step was actually taken.

    The cached path is ordered goal-first, so the next tile toward the
    goal is the one BEFORE our own tile in the list. The cursor is the
    index of our own tile: it is verified against our position with ONE
    coordinate read (the path is a shortest path, so walking it keeps us
    on it; a deviation is detected and the path is rebuilt).

    S2.3.3 FIX (the 01starvation wedge): the position check used to run
    ONLY when the cursor was >= 1, so an entry that had already reached
    its goal (cursor == 0) was served FOREVER without ever being checked
    against the champion's real position. A champion that left the goal
    (a flee, a push, a re-target) kept the dead entry and could not walk
    anywhere again: WILLOW sat at (2,48) for 240+ rounds with a goal 3
    tiles away, and the whole team stalled behind it. The check now runs
    for EVERY cursor value, and a champion that is off the path either
    re-anchors onto it (a one-tile detour) or drops the entry so the
    walk is rebuilt from where it actually stands.
    """
    entry = path_entry(bot, snap, kind, goal)
    if entry is None:
        return None, None
    tiles = entry.get("tiles") or ()
    cursor = int(entry.get("cursor") or 0)
    if not entry.get("reached") or len(tiles) < 2:
        return None, entry
    px, py = snap.origin_xy
    if not _tile_is(tiles, cursor, px, py):
        try:
            reanchor = int(config.PATH_REANCHOR_READS)
        except Exception:
            reanchor = 12
        cursor = _locate(tiles, px, py, limit=reanchor)
        if cursor is None:
            # Off the path: drop the entry and rebuild the walk from here.
            _drop_entry(bot, kind, goal)
            entry = path_entry(bot, snap, kind, goal)
            if entry is None:
                return None, None
            tiles = entry.get("tiles") or ()
            cursor = int(entry.get("cursor") or 0)
            if not entry.get("reached") or len(tiles) < 2 or cursor < 1:
                return None, entry
        else:
            entry["cursor"] = cursor
    if cursor < 1:
        return None, entry
    entry["pending"] = cursor - 1
    return tiles[cursor - 1], entry


def _tile_is(tiles, index, px, py) -> bool:
    """True when tiles[index] is the tile (px, py)."""
    try:
        tile = tiles[index]
        return int(tile.x) == int(px) and int(tile.y) == int(py)
    except Exception:
        return False


def _drop_entry(bot, kind: str, goal) -> None:
    """Forget the cached walk toward `goal`."""
    if bot is None or goal is None:
        return
    try:
        key = (str(kind), int(goal.x), int(goal.y))
    except Exception:
        return
    cache = _path_cache(bot)
    try:
        cache.pop(key, None)
    except Exception:
        pass


def _locate(tiles, px, py, limit: int | None = None):
    """Index of the tile (px, py) in the walked path (None if absent).

    Used after the champion left the cached path by ONE tile (a refused
    step that took the downhill alternative): the path tiles are scanned
    from the end (our own tile is the last one), bounded by `limit` so
    the scan cannot cost a full path of engine reads.
    """
    if limit is None:
        limit = len(tiles)
    if limit < 1:
        limit = 1
    if limit > len(tiles):
        limit = len(tiles)
    for index in range(len(tiles) - 1, len(tiles) - 1 - limit, -1):
        try:
            tile = tiles[index]
            if int(tile.x) == int(px) and int(tile.y) == int(py):
                return index
        except Exception:
            continue
    return None


def advance(entry) -> None:
    """Record that the step toward the goal was taken (cursor moves on)."""
    if not entry:
        return
    pending = entry.get("pending")
    if pending is None:
        return
    entry["cursor"] = int(pending)
    entry["pending"] = None


def exposure_tiles(bot, snap, kind: str, goal, limit: int | None = None):
    """The tiles near the goal that the walk exposes us to.

    Only the first PATH_EXPOSURE_STEPS tiles of the walk (the ones next to
    the destination, where the destination's monsters wait) are counted -
    the exposure is a risk multiplier, not a candidate source.

    `goal` may be a single tile or a tuple of goal tiles (the capture
    candidate offers every spawn tile of the group); the first tile is the
    one the walk ends at.
    """
    if isinstance(goal, (tuple, list)):
        goal = goal[0] if goal else None
    entry = path_entry(bot, snap, kind, goal)
    if entry is None or not entry.get("reached"):
        return None
    return entry.get("exposure")


# ---------------------------------------------------------------------------
# Boss-engagement field (terrain-only walls).
# ---------------------------------------------------------------------------


def _excluded_groups(snap) -> list:
    """Every group that fails the killable filter (the boss candidates).

    Unseen groups are included: boss graduation must make a high-value
    over-cap group a real exploration target, not merely a label on a group
    that happens to be in fog.

    S2.3 FUEL: the list is STATIC for the match
    (observation.excluded_groups), so this no longer re-derives it by
    scanning every group on the map (and every group's metadata) on
    every turn.
    """
    static = getattr(snap, "excluded_groups", None)
    if static is not None:
        return [group for _key, group in static]
    out = []
    meta = getattr(snap, "group_meta", {}) or {}
    for group in getattr(snap, "camp_groups", ()) or ():
        try:
            from managers import observation
            m = meta.get(observation.group_key(group), {}) or {}
            excluded = bool(m.get("is_monolith", False)) or (
                int(m.get("static_hp", 0)) > int(config.KILLABLE_HP_CAP)
            )
        except Exception:
            continue
        if excluded:
            out.append(group)
    return out


def boss_field(snap, destinations: Any = None) -> Any:
    """Terrain-only field for the boss engagement (one per turn).

    The boss groups' own tiles are DESTINATIONS, never walls here: a
    champion fights the boss from the corridor edge. The field's roots are
    our position, so its gradient is the walk we would take to the boss.

    One field serves every boss candidate (and the boss path-exposure
    walk); it is built lazily, only when a boss candidate survived the
    arithmetic pruning in utility._boss_candidates, and skipped when the
    turn is out of fuel. `destinations` bounds the destination set (the
    engine's compute_to stops only when EVERY destination is reached, so
    each extra destination can turn a path's worth of expansion into a
    full-map sweep).
    """
    cached = getattr(snap, "boss_field", None)
    if cached is not None:
        # False is the "skipped" sentinel (fuel guard / no boss in sight).
        return None if cached is False else cached
    groups = _excluded_groups(snap)
    if not groups:
        snap.boss_field = False
        return None
    if destinations is None:
        # Bound the destination set: the engine's compute_to stops only when
        # EVERY destination is reached, so each extra group can push the
        # search from a path's worth of expansion to a full-map sweep.
        groups = groups[: int(config.BOSS_FIELD_MAX_GROUPS)]
    try:
        if int(cambc.fuel_remaining()) < int(config.BOSS_FIELD_MIN_FUEL):
            snap.boss_field = False
            return None
    except Exception:
        pass
    walls = terrain_walls().copy()
    walls.set(snap.position, False)
    roots = cambc.Bitboard()
    roots.set(snap.position)
    dests = cambc.Bitboard()
    for group in groups:
        try:
            from managers import observation
            tiles = observation.group_tiles(snap, group)
        except Exception:
            tiles = None
        if tiles is None:
            try:
                tiles = group.spawn_tiles()
            except Exception:
                continue
        try:
            dests |= tiles
        except Exception:
            continue
    if destinations is not None and not destinations.is_empty():
        dests &= destinations
    if dests.is_empty():
        snap.boss_field = False
        return None
    field = cambc.DistanceField()
    try:
        field.compute_to(
            walls,
            roots,
            dests,
            cambc.Bitboard(),
            int(config.BOSS_FIELD_MAX_DISTANCE),
        )
    except Exception:
        field = None
    snap.boss_field = field if field is not None else False
    return field


def boss_distance(snap, group) -> int | None:
    """Honest distance to engage a boss group (terrain-only walls)."""
    field = boss_field(snap)
    if field is None:
        return None
    positions = None
    try:
        from managers import observation
        positions = observation.group_positions(snap, group)
    except Exception:
        positions = None
    return distance_to_group(snap, field, group, positions)[0]


def distance_to_group(snap, field, group, positions=None,
                      limit: int | None = None):
    """Honest distance to a camp group: (distance, tile).

    `positions` is the cached spawn-tile list (observation.group_positions);
    when it is not supplied the engine is asked for the tiles (one SDK
    call - the first-turn cache exists to avoid it per turn).
    """
    if positions is None:
        try:
            tiles = group.spawn_tiles().positions()
        except Exception:
            return None, None
    else:
        tiles = positions
    if not tiles:
        return None, None
    reads = int(config.FARM_TILE_READS if limit is None else limit)
    return nearest_tile(field, tiles, origin=snap.position, limit=reads)


def distance_to_altar(field, altar) -> int | None:
    """Honest distance to an altar tile."""
    return distance_to(field, (altar,))


# ---------------------------------------------------------------------------
# Threat model + path exposure.
# ---------------------------------------------------------------------------


def awake_monsters(snap) -> list:
    """Awake (already hit) monsters as (tile, strength, range) triples.

    GameRules: monsters are friendly until struck; a damaged monster attacks
    the nearest champion every turn. The observation layer already read the
    visible-camp list once this turn and resolved each camp's state, so no
    extra engine read is needed here.

    S2.3.4: the range is the monster's OWN attack range (the engine's
    CampStats.base_attack_range: 3 for a wolf, 4 for an elk, 5 for the
    monolith), not a flat 3.
    """
    return list(getattr(snap, "awake_monsters", ()) or ())


def threats(snap) -> list:
    """Every threat on the map as (tile, strength, range) triples.

    - awake monsters (strength from the camp design table, range 3);
    - visible enemy champions (their own strength and attack range);
    - remembered enemy champions from the fog memory (a last-known position
      is a stale guess, so they are priced at a conservative strength).

    This is the input of the carried-gem risk term (bank score x
    P(survive the trip)) and of the farm/boss survival terms.
    """
    out: list = []
    for record in awake_monsters(snap):
        try:
            pos, strength, reach = record
        except Exception:
            continue
        out.append((pos, float(strength), int(reach)))
    for enemy in getattr(snap, "visible_enemies", ()) or ():
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            strength = float(enemy.strength())
            reach = int(enemy.attack_range())
        except Exception:
            continue
        out.append((pos, strength, reach))
    for record in (getattr(snap, "threat_memory", ()) or ()):
        try:
            pos = cambc.Vec2(int(record["x"]), int(record["y"]))
            strength = float(record.get("strength", 0.0))
            reach = int(record.get("range", config.MONSTER_RANGE))
        except Exception:
            continue
        out.append((pos, strength, reach))
    return out


def path_exposure(path, threat_list) -> float:
    """Expected damage along an engine-computed path.

    `path` is the bitboard of tiles the gradient walk visited. One path tile
    is one turn of exposure; a threat covers every tile within its range
    (GameRules: all monsters have range 3; champions use their own range).
    """
    if not threat_list or path is None:
        return 0.0
    try:
        if path.is_empty():
            return 0.0
    except Exception:
        return 0.0
    total = 0.0
    for threat in threat_list:
        try:
            pos, strength, reach = threat
        except Exception:
            continue
        if strength <= 0.0:
            continue
        try:
            disk = cambc.Bitboard()
            disk.draw_chebyshev(pos, int(reach))
            turns = int((path & disk).count())
            if turns:
                total += float(strength) * turns
        except Exception:
            continue
    return total

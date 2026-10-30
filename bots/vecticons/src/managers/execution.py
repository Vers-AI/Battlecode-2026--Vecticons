"""Execution manager (Stage 2).

Translates an action choice into SDK calls. SDK helpers only; never
hand-roll pathfinding.
"""
from __future__ import annotations

from typing import Any

import cambc
from cambc import game_map

import config
from managers import fielding
from managers import observation


def _ensure_resolved() -> None:
    config.resolve()


def _distance(a, b) -> int:
    return a.chebyshev_distance(b)


def _tiles_around(position, radius) -> Any:
    tiles = cambc.Bitboard()
    tiles.draw_chebyshev(position, radius)
    return tiles


def our_attack_tiles(snap=None):
    """The tiles we can attack this turn, read ONCE and cached.

    `me.attack_tiles()` is an engine call, and the S2.3 path asked for it
    in three places per turn (the fight candidate, the dispatch and the
    opportunistic attack before and after a move). The snapshot caches
    it, so the turn pays for it once.
    """
    if snap is not None:
        tiles = getattr(snap, "attack_tiles", None)
        if tiles is not None:
            return None if tiles is False else tiles
    try:
        tiles = cambc.me().attack_tiles()
    except Exception:
        tiles = False
    if snap is not None:
        try:
            snap.attack_tiles = tiles
        except Exception:
            pass
    return None if tiles is False else tiles


def attack_first_in_range(targets, snap=None, allow_excluded=False,
                          group=None, on_target=None) -> bool:
    """Attack the first visible target inside our attack tiles.

    With snap, the killable filter applies (architecture.md Stage 3 gate:
    a camp whose group fails the filter — Monolith or over-cap static HP
    — is never attacked; the lost_world bait clusters woke the 200-strength
    Monolith via this path, 56 deaths). Without snap, legacy behavior is
    preserved for the frozen Stage 2 baseline.

    S2.3 boss graduation: allow_excluded=True drops the killable filter so
    the boss candidate can strike its own group; group=<group> narrows the
    targets to that one camp group, so an allowed boss fight never spills
    onto an unrelated Monolith that happens to be adjacent.

    S2.3 FUEL: the "can we attack" test is the snapshot's cached flag
    (one `me.state()` read per turn) and the attack tiles are cached on
    the snapshot too - the old code paid for both on every call.

    F3a: `on_target` is an optional hook called with the target we
    attacked, right AFTER the attack. It is how the ability layer joins
    the basic attack in the same turn (a cast does not consume the
    attack allowance) - the camp burst shortens a clear.
    """
    me = cambc.me()
    if not targets:
        return False
    if snap is not None:
        if not getattr(snap, "can_attack", True):
            return False
    else:
        state = me.state()
        if not (state & cambc.ChampionState.CAN_ATTACK):
            return False
    meta = getattr(snap, "group_meta", None) if snap is not None else None
    wanted = None
    if group is not None:
        wanted = observation.group_key(group)
    attack_tiles = our_attack_tiles(snap)
    if attack_tiles is None:
        return False
    for target in targets:
        if target.is_visible():
            try:
                hp = target.health()
            except Exception:
                continue
            if hp <= 0:
                continue
            if not attack_tiles.is_set(target.position()):
                continue
            if wanted is not None:
                if not _camp_in_group(snap, target, wanted):
                    continue
            if (not allow_excluded) and meta is not None and _camp_excluded(
                snap, target, meta
            ):
                continue
            me.attack(target)
            if on_target is not None:
                try:
                    on_target(target)
                except Exception:
                    pass
            return True
    return False


def _camp_excluded(snap, camp, meta) -> bool:
    """True if the camp's group fails the killable filter.

    The group's handle comes from the first-turn camp table when the
    snapshot provides one (S2.3 fuel: `camp.camp_group()` costs ~57k fuel
    PER CAMP); otherwise the engine call is used (the frozen baseline).
    """
    key = None
    if snap is not None:
        key = _camp_group_key(snap, camp)
    if key is None:
        try:
            key = observation.group_key(camp.camp_group())
        except Exception:
            return False
    m = meta.get(key, {})
    if m.get("is_monolith", False):
        return True
    return int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP


def _camp_group_key(snap, camp):
    """The group handle of a camp, without the SDK's camp_group() call.

    `camp.camp_group()` costs ~57k fuel PER CAMP (the engine walks the camp's
    spawn tiles and builds a group view). The S2.3 map cache already maps
    every camp handle to its group handle (observation.camp_info), so the
    per-turn lookup is a dict read.

    Returns None when the camp is not in the cache (the caller then falls
    back to the engine call).
    """
    info = getattr(snap, "camp_info", None) or {}
    if not info:
        return None
    try:
        handle = int(camp.__index__())
    except Exception:
        return None
    entry = info.get(handle)
    if entry is None:
        return None
    try:
        return int(entry[0])
    except Exception:
        return None


def _camp_in_group(snap, camp, wanted_key) -> bool:
    """True when the camp belongs to the group with handle `wanted_key`.

    S2.3 fuel: the group membership is a dict lookup on the first-turn camp
    table. The frozen baseline (no snapshot) keeps the engine call.
    """
    if snap is None:
        try:
            return observation.group_key(camp.camp_group()) == wanted_key
        except Exception:
            return False
    key = _camp_group_key(snap, camp)
    if key is None:
        # Not in the cache: fall back to the engine call (rare).
        try:
            return observation.group_key(camp.camp_group()) == wanted_key
        except Exception:
            return False
    return key == wanted_key


def _blocked_tiles(snap=None) -> Any:
    """Terrain plus currently visible unit/teleport blockers.

    When a snapshot is supplied, its cached static terrain bitboard is
    reused. The optional argument keeps the frozen baseline call shape
    byte-compatible while the S2.3 bot avoids four static engine reads on
    every dispatch.
    """
    if snap is not None:
        terrain = getattr(snap, "terrain_walls", None)
        if terrain is not None:
            blocked = terrain.copy()
        else:
            blocked = (
                game_map.walls() | game_map.holes() | game_map.altars()
                | game_map.spawn_region(cambc.Team.ENEMY)
            )
    else:
        blocked = (
            game_map.walls() | game_map.holes() | game_map.altars()
            | game_map.spawn_region(cambc.Team.ENEMY)
        )
    blocked |= (
        cambc.vision.champion_tiles() | cambc.vision.camp_tiles()
        | cambc.vision.teleport_tiles()
    )
    blocked.set(cambc.me().position(), False)
    return blocked


def reachable_tiles_from(snap) -> Any | None:
    """Bitboard of tiles walkable from our position this turn, or None.

    Terrain-only blockers (walls, holes, altars, enemy spawn) plus the
    STATIC door overlay of excluded groups' spawn tiles
    (snap.excluded_tiles, precomputed once — Monolith / over-cap groups
    are doors we never open). The overlay is deliberately NOT
    vision-derived: an earlier version overlaid visible excluded camps,
    which made reach itself flicker with vision and re-created the
    target-flip oscillator on 00ambush. Killable groups' camps are not
    blockers — they are the targets, and standing on their spawn tiles
    is how we farm (blocking on them gutted gem_fields 1005 → 99).
    Staleness: if the enemy clears an excluded group, the overlay stays
    conservative until respawn — over-blocking, never oscillating.

    Perf note: one map-sized BFS per farm-candidate turn, the same cost
    class as the move-toward field; computed once, reused for all
    candidates.
    """
    try:
        overlay = getattr(snap, "excluded_tiles", None)
        blocked = (
            game_map.walls() | game_map.holes() | game_map.altars()
            | game_map.spawn_region(cambc.Team.ENEMY)
        )
        if overlay is not None:
            blocked = blocked | overlay
        blocked.set(snap.position, False)
        radius = int(game_map.width()) + int(game_map.height())
        return blocked.reachable_tiles(snap.position, radius)
    except Exception:
        return None


def move_towards_closest(goals, snap=None) -> bool:
    """Move one step toward the closest reachable goal (starter-style).

    WITHOUT a snapshot this is the frozen Stage 2 path, kept byte-compatible
    for bot_baseline.py: a reachable-tiles BFS plus a destination-limited
    field, then a step to the closest reachable tile.

    WITH a snapshot (the S2.3 path) the movement no longer builds a field of
    its own at all: the utility layer already resolved the goal tile from
    the shared scoring field, and the gradient path to that tile is cached
    on the bot (see managers.fielding.cached_walk). The step is then the
    next tile of the cached path - no engine call at all.

    Returns True if a move step was emitted.
    """
    me = cambc.me()
    if not me.can_move():
        return False
    origin = me.position()
    if goals.is_empty() or goals.is_set(origin):
        return False

    if snap is not None:
        return move_towards_goal(snap, goals, kind="scoring")

    blocked = _blocked_tiles(snap)
    # Allow camp tiles as goals: they are passable as roots in the field, so
    # the bot walks TO the spawn tile even though `blocked` blocks the
    # single-target helper.
    safe_goals = goals & (~blocked | cambc.vision.camp_tiles())
    if safe_goals.is_empty():
        return False

    try:
        reachable = blocked.reachable_tiles(origin, me.base_stats().base_move_speed).positions()
    except Exception:
        return False
    try:
        field = cambc.DistanceField()
        # Include origin and reachable tiles as destinations so the field
        # computes distances to tiles we will actually query (compute_to
        # short-circuits early once the destinations are reached - empty
        # destinations leaves the field at the unreachable sentinel).
        dests = cambc.Bitboard()
        dests.set(origin)
        for r in reachable:
            dests.set(r)
        # Starter-style: walls = blockers minus roots (so the search can
        # reach blocked camp tiles via them as roots).
        walls = blocked & ~safe_goals
        field.compute_to(walls, safe_goals, dests, cambc.Bitboard())
    except Exception:
        return False
    candidates = [origin, *reachable]
    try:
        step = min(candidates, key=field.get)
    except Exception:
        return False
    if step == origin:
        return False
    try:
        return me.move_absolute(step)
    except Exception:
        return False


def move_towards_goal(snap, goal, kind: str = "scoring") -> bool:
    """Step one tile toward `goal` using the cached gradient path.

    `goal` is either a single tile (the utility layer's goal tile) or a
    tuple of goal tiles (the capture candidate offers every spawn tile of
    the group). The step comes from the cached walk
    (managers.fielding.step_toward), which costs no engine call once the
    path is cached: the walk is the engine's own gradient, read once and
    kept on the Bot, and the per-turn movement is a cursor step along it.

    S2.3.2: the goal list matters because a group is a cluster of a few
    tiles and every champion of the team wants the same one. The engine
    refuses an occupied destination ("Known occupied destination"), so the
    champion has to fall back to another tile of the group instead of
    standing still (01starvation: three champions pinned next to a
    captured camp for 1,900 rounds).

    A blocked step (a unit standing on the next tile) does not consume the
    movement allowance - the engine allows a retry - so the other
    neighbours that are not farther from the goal are tried in turn.

    S2.3.5: the walk STOPS when we already stand on one of the goal tiles
    (see the comment in the loop below): the goal tiles of a camp group are
    its spawn tiles, and moving between them resets the engine's capture
    timer, so the champion oscillated on the group it was capturing and
    the reward was never claimed (0 gems on 00road_of_riches against the
    S2.2 reference's 486).
    """
    me = cambc.me()
    if not me.can_move():
        return False
    origin = me.position()
    bot = getattr(snap, "bot", None)
    goals = _goal_tile_list(snap, goal)
    if not goals:
        return False
    for tile in goals:
        if _same_tile(tile, origin):
            # We already stand on one of the candidate's goal tiles, so the
            # walk is DONE. The walk must not continue to the next goal
            # tile: a group's spawn tiles are the CAPTURE REGION, and
            # stepping from one of them to another RESETS the engine's
            # five-round capture timer (GameRules "After defeating a camp,
            # make sure to capture!"). The S2.3 capture candidate offers
            # every spawn tile of the group (so a champion that cannot take
            # a taken tile can fall back to a free one), and that fallback
            # made the champion oscillate between the two tiles of the
            # group it was capturing - measured on 00road_of_riches: the
            # champion stood on (25,6) at r=80, stepped to (26,6) at r=82,
            # back to (25,6) at r=84, and the group's reward was never
            # claimed (0 gems for the whole match, against the S2.2
            # reference's 486). Standing still is what completes the
            # capture.
            return False
        destination = None
        entry = None
        if bot is not None:
            destination, entry = fielding.step_toward(bot, snap, kind, tile)
        if destination is None:
            continue
        if _same_tile(destination, origin):
            continue
        try:
            if me.move_absolute(destination):
                # The step was taken: advance the cached path cursor so the
                # next turn continues along the same gradient.
                fielding.advance(entry)
                return True
        except Exception:
            pass
        # The step was refused (the destination tile is taken by another
        # unit): try the neighbours that are not farther from the goal.
        # The engine does not consume the allowance.
        for alternative in _downhill_alternatives(snap, origin, destination, tile):
            try:
                if me.move_absolute(alternative):
                    # The alternative is OFF the cached path: the cursor is
                    # left alone, so the next turn re-locates us on the
                    # path (or rebuilds it).
                    return True
            except Exception:
                continue
    return False


def _same_tile(a, b) -> bool:
    try:
        return int(a.x) == int(b.x) and int(a.y) == int(b.y)
    except Exception:
        return a == b


def _tile_order_key(tile):
    """Deterministic tie-break key for a tile: its coordinates, not its id.

    id() is a memory address, so an id()-based tie-break makes the bot's
    choice depend on the compiled module's memory layout - a source edit
    elsewhere in the bot can flip the tie and change the whole match.
    Coordinates are stable across builds, so the ranking is reproducible.
    """
    try:
        return (int(tile.x), int(tile.y))
    except Exception:
        return (0, 0)


def _goal_tile_list(snap, goal) -> list:
    """The goal tiles of a candidate, best first.

    A single tile stays a single tile; a tuple of tiles is ranked by the
    shared field's distance (the engine's honest path distance) so the
    nearest free tile is tried first. Ties are broken by COORDINATES, not
    by object identity (see _tile_order_key).
    """
    if goal is None:
        return []
    if isinstance(goal, (tuple, list)):
        tiles = [t for t in goal if t is not None]
        if len(tiles) <= 1:
            return tiles
        field = None
        try:
            field = fielding.scoring_field(snap)
        except Exception:
            field = None
        if field is None:
            return tiles
        ranked = []
        for tile in tiles:
            try:
                d = int(field.get(tile))
            except Exception:
                d = fielding.UNREACHABLE
            if d >= fielding.UNREACHABLE:
                continue
            ranked.append((d, _tile_order_key(tile), tile))
        if not ranked:
            return tiles
        ranked.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in ranked]
    return [goal]


def _downhill_alternatives(snap, origin, primary, goal=None):
    """Neighbouring tiles that are not farther from the goal than we are.

    Used only when the engine refused the step (the destination tile is
    taken by another unit).

    S2.3.2 FIX: the old version compared the neighbours against the
    SHARED FIELD's distance measured FROM OUR OWN POSITION. The field is
    rooted at us, so every neighbour is FARTHER than us and the fallback
    list was ALWAYS EMPTY - a champion that could not step onto an
    occupied tile had no second option and stayed wedged for the rest of
    the match (three champions pinned next to a captured camp for 1,900
    rounds on 01starvation).

    The direction now comes from the GOAL (the Chebyshev distance to the
    goal tile, computed on plain ints - no engine call), and the terrain
    filter comes from the shared field (a wall reads back as the
    unreachable sentinel).
    """
    if goal is None:
        return []
    field = None
    try:
        field = fielding.scoring_field(snap)
    except Exception:
        field = None
    try:
        gx = int(goal.x)
        gy = int(goal.y)
        ox = int(origin.x)
        oy = int(origin.y)
        px = int(primary.x)
        py = int(primary.y)
    except Exception:
        return []
    here = abs(gx - ox)
    other = abs(gy - oy)
    if other > here:
        here = other
    out = []
    for ddx in (-1, 0, 1):
        for ddy in (-1, 0, 1):
            if ddx == 0 and ddy == 0:
                continue
            tx = ox + ddx
            ty = oy + ddy
            if tx == px and ty == py:
                continue
            d = abs(gx - tx)
            other = abs(gy - ty)
            if other > d:
                d = other
            if d > here:
                continue
            try:
                tile = cambc.Vec2(tx, ty)
            except Exception:
                continue
            if field is not None:
                try:
                    fd = int(field.get(tile))
                except Exception:
                    continue
                if fd >= fielding.UNREACHABLE:
                    # A wall (or a tile the field never reached).
                    continue
            out.append((d, tx * 1000 + ty, tile))
    out.sort(key=lambda item: (item[0], item[1]))
    return [item[2] for item in out]


def recall_to_spawn() -> bool:
    """Begin recalling to our spawn tile."""
    me = cambc.me()
    if me.is_recalling():
        return True
    try:
        me.recall()
        return True
    except Exception:
        return False


def _altar_vec(altar):
    """Coerce an altar reference into a Vec2 tile.

    The S2.3 utility layer keeps the altars' coordinates as PLAIN INTS
    (the bank candidate's target is the (x, y) tuple, so the per-turn
    loop never reads a Vec2 coordinate out of the engine), but the SDK's
    tribute call needs the tile itself.
    """
    if altar is None:
        return None
    if isinstance(altar, (tuple, list)):
        try:
            return cambc.Vec2(int(altar[0]), int(altar[1]))
        except Exception:
            return None
    return altar


def submit_gems_to_altar(altar, snap=None, break_lock: bool = False) -> bool:
    """Start a tribute channel at altar (if unlocked, in range, carrying gems).

    S2.3: `me.submit_gems` RAISES (it has no status return): an altar the
    enemy locked raises INVALID_ACTION_SUBMIT_GEMS_ALTAR_LOCKED, a second
    start in the same turn raises ..._ALREADY_USED. An escaping exception
    would abort the champion's whole turn (main.py's catch-all ends the
    turn), so the call is guarded and the two benign codes are handled
    explicitly.

    The altar-lock test is ENEMY-only in the S2.3 path: `has_altar_lock` is
    team-agnostic ("either team"), so our own defensive lock would lock us
    out of our own altar while the engine lets the owner team pay tribute
    (GameRules: "An altar lock blocks opposing tributes but allows its
    owner's team to tribute"). The frozen baseline keeps its historical
    team-agnostic test.

    F6 (`break_lock=True`): the lock-break path. The engine converts a
    tribute attempt against a locked altar into attacks on the lock
    (GameRules:242 "Attempts to tribute against a locked altar are
    implicitly converted to auto attacks against the altar lock"), so the
    submit call IS the lock hit - no separate attack action is needed.
    Probed on engine 4.0.0 (2026-10-30, /tmp/opencode/f6probe): against an
    ENEMY lock the submit returned OK, the lock's hits_remaining went
    5 -> 4 -> 3 -> 2 -> 1 -> gone over five attempts, and
    is_submitting_gems() stayed False (no tribute channel was started).
    The lock guard below is therefore SKIPPED when the caller wants the
    break: the guard exists to avoid a pointless submit, not to forbid
    the engine's own conversion.

    `altar` may be the Vec2 tile or the (x, y) integer tuple the utility
    layer caches (S2.3 fuel: no coordinate read out of the engine).
    """
    _ensure_resolved()
    me = cambc.me()
    if me.gem_count() <= 0:
        return False
    if me.is_submitting_gems():
        return True
    altar = _altar_vec(altar)
    if altar is None:
        return False
    if _distance(me.position(), altar) > config.TRIBUTE_RANGE:
        return False
    if not break_lock and snap is not None and bool(getattr(snap, "s23_cache", False)):
        if _altar_locked_by_enemy(snap, altar):
            return False
    try:
        me.submit_gems(altar)
    except cambc.Error as error:
        code = int(getattr(error, "code", -1))
        if code in _tribute_benign_codes():
            return False
        try:
            print(f"execution: submit_gems failed: {error!r}", flush=True)
        except Exception:
            pass
        return False
    except Exception:
        return False
    return True


_TRIBUTE_BENIGN_CODES: frozenset = frozenset()


def _tribute_benign_codes() -> frozenset:
    """The engine's benign tribute error codes (looked up defensively).

    The altar is locked by the enemy, or we already started a tribute this
    turn: both are normal outcomes, not bugs.
    """
    global _TRIBUTE_BENIGN_CODES
    if _TRIBUTE_BENIGN_CODES:
        return _TRIBUTE_BENIGN_CODES
    codes = set()
    for name in (
        "INVALID_ACTION_SUBMIT_GEMS_ALTAR_LOCKED",
        "INVALID_ACTION_SUBMIT_GEMS_ALREADY_USED",
    ):
        value = getattr(getattr(cambc, "ErrorCode", None), name, None)
        if value is not None:
            try:
                codes.add(int(value))
            except Exception:
                continue
    _TRIBUTE_BENIGN_CODES = frozenset(codes)
    return _TRIBUTE_BENIGN_CODES


def _altar_locked_by_enemy(snap, altar) -> bool:
    """True when an ENEMY altar lock stands on this altar."""
    locks = getattr(snap, "enemy_altar_locks", None)
    if locks is None:
        try:
            locks = cambc.vision.altar_lock_tiles(cambc.Team.ENEMY)
        except Exception:
            return False
    try:
        return bool(locks.is_set(altar))
    except Exception:
        return False


def buy_item(item, count: int = 1) -> bool:
    """Buy `count` copies of `item` (the shop action).

    S2.4: the shop is legal on a spawn tile or while dead (GameRules:361);
    the legality is decided by the utility layer's shopping decision, this
    function only performs the purchase. `can_buy` is asked for the exact
    count first and falls back to a single copy (the engine's shop rules
    can refuse a bulk purchase for reasons the caller cannot see).

    Returns True when a purchase was emitted.
    """
    me = cambc.me()
    if item is None:
        return False
    try:
        count = int(count)
    except Exception:
        count = 1
    if count <= 0:
        return False
    try:
        if not me.can_buy(item, count):
            count = 1
            if not me.can_buy(item, 1):
                return False
        me.buy(item, count)
        return True
    except Exception:
        return False


def consumable_count(kind) -> int:
    """How many of a consumable we carry (0 when the slot is empty).

    One engine read (`champion_consumables`); only the shop / lock paths
    ask for it.
    """
    try:
        me = cambc.me()
        slots = me.consumables()
        return int(slots[int(kind)].count())
    except Exception:
        return 0


def buy_consumable(kind) -> bool:
    """Buy ONE consumable (the engine buys consumables one at a time).

    `can_buy` takes no count for a consumable and checks both the gold
    and the carry limit (BotAPIPython:3616). The carry limit of the altar
    lock is ONE (GameRules "carry one at a time"), so the assert below is
    the belt to the engine's braces.
    """
    me = cambc.me()
    try:
        if not me.can_buy(kind):
            return False
        me.buy(kind, 1)
        return True
    except Exception:
        return False


def altar_lock_occupied(snap, altar) -> bool:
    """True when ANY team already has a lock on this altar.

    ONE engine read (both teams' lock bitboards). Only the placement
    path asks for it - a lock on an already-locked altar is refused by
    the engine (INVALID_ACTION_ALTAR_LOCK_ALREADY_LOCKED).
    """
    vec = None
    vecs = getattr(snap, "altar_vecs", None) or {}
    try:
        vec = vecs.get((int(altar[0]), int(altar[1])))
    except Exception:
        vec = None
    if vec is None:
        try:
            vec = cambc.Vec2(int(altar[0]), int(altar[1]))
        except Exception:
            return False
    try:
        return bool(cambc.vision.altar_lock_tiles().is_set(vec))
    except Exception:
        return False


def use_altar_lock(snap, altar) -> bool:
    """Place our carried altar lock on an altar.

    The lock is used ON the altar (GameRules: "Place on an altar within
    two tiles"); the engine reports a bad altar / an out-of-range altar /
    an already-locked altar as error codes. The destination is the altar's
    STATIC Vec2 (observation.altar_vecs), so no coordinate is read out of
    the engine here.
    """
    try:
        me = cambc.me()
        slots = me.consumables()
        slot = slots[int(cambc.ConsumableType.ALTAR_LOCK)]
        if int(slot.count()) <= 0:
            return False
    except Exception:
        return False
    vec = None
    vecs = getattr(snap, "altar_vecs", None) or {}
    try:
        vec = vecs.get((int(altar[0]), int(altar[1])))
    except Exception:
        vec = None
    if vec is None:
        try:
            vec = cambc.Vec2(int(altar[0]), int(altar[1]))
        except Exception:
            return False
    try:
        return bool(slot.use(vec))
    except Exception:
        return False


def attacked_by_enemy(snap) -> bool:
    """Return True if a visible enemy champion is within this champion's attack range."""
    me = cambc.me()
    if not (me.state() & cambc.ChampionState.CAN_ATTACK):
        return False
    attack_tiles = me.attack_tiles()
    for enemy in cambc.vision.champions(cambc.Team.ENEMY):
        if enemy.is_visible() and attack_tiles.is_set(enemy.position()):
            return True
    return False


def _camp_handle(camp):
    """Return the integer camp handle (Camp has __index__ but not .index())."""
    try:
        return int(camp.__index__())
    except Exception:
        try:
            return int(camp._handle)
        except Exception:
            return id(camp)


def _group_visible_total_hp(group) -> int:
    """Sum HP across the camp group's currently visible camps (skip hidden ones)."""
    total = 0
    for camp in group.camps():
        # Only count HP for camps currently visible from our position.
        try:
            if not camp.is_visible():
                continue
            hp = camp.health()
        except Exception:
            continue
        if hp > 0:
            total += int(hp)
    return total


def group_state(snap, group):
    """Return and cache this group's engine state for the current turn."""
    cache = getattr(snap, "group_states", None)
    if cache is None:
        cache = {}
        try:
            snap.group_states = cache
        except Exception:
            pass
    key = observation.group_key(group)
    if key in cache:
        return cache[key]
    try:
        state = group.state()
    except Exception:
        state = None
    cache[key] = state
    return state


def visible_killable_camps(snap) -> list:
    """Visible camp groups whose monsters' total HP ≤ KILLABLE_HP_CAP.

    Excludes Monolith (20,000 HP trap — architecture.md "Monolith trap").
    Excludes respawning groups (no monsters). Matches Stage 2 spec exactly.

    Performance: the HP test uses the cached static base HP (per-group
    cache in snap.group_meta, precomputed on the first turn) instead of
    iterating the group's camps every turn - the per-turn ``group.camps()``
    iteration was the documented fuel wall (S2.0). The static gate is also
    position-independent, which is what keeps the candidate set from
    flickering with vision (the 00ambush oscillator).

    S2.3 fuel: the respawning test is the engine's ``group.state()`` call
    (~49k fuel PER GROUP - it re-reads the whole vision bitboard and probes
    every camp of the group). The S2.3 snapshot derives the group state from
    the visible camp states it already read (observation._derive_group_states):
    a group whose camps are all visible and all dead has its reward
    available, a group with a live camp is alive. A respawning group has NO
    camps on the map, so it is not in the visible set at all - the derived
    state answers the same question without an engine call.
    """
    _ensure_resolved()
    result = []
    meta = getattr(snap, "group_meta", {}) or {}
    derived = getattr(snap, "group_derived_states", None)
    s23 = bool(getattr(snap, "s23_cache", False)) and derived is not None
    for group in snap.visible_camp_groups:
        try:
            key = observation.group_key(group)
        except Exception:
            key = None
        try:
            m = meta.get(key, {})
        except Exception:
            m = {}
        # Exclude Monolith (cheap cache lookup, engine-handle keyed).
        try:
            if m.get("is_monolith", False):
                continue
        except Exception:
            pass
        # Skip respawning groups (no monsters on the map). The state read is
        # cached on the snapshot and shared with the reward-candidate path.
        if s23:
            # The derived state replaces the engine call: "reward" means
            # every camp is dead (nothing left to kill).
            if derived.get(key) == "reward":
                continue
        else:
            try:
                if isinstance(group_state(snap, group), game_map.CampGroupRespawning):
                    continue
            except Exception:
                pass
        # Per-spec HP cap on visible monsters (cached static HP).
        try:
            static_hp = int(m.get("static_hp", 0))
        except Exception:
            static_hp = 0
        if static_hp <= 0:
            # No cached HP (cache miss): fall back to the live total.
            try:
                if _group_visible_total_hp(group) > config.KILLABLE_HP_CAP:
                    continue
            except Exception:
                continue
        elif static_hp > config.KILLABLE_HP_CAP:
            continue
        result.append(group)
    return result


def reward_available_camp(snap):
    """Return a visible camp group whose reward is available for capture, or None.

    Standing on one of its spawn tiles starts the 5-round capture timer
    (architecture.md Stage 2 baseline). Monolith excluded even when reward
    available - it is too costly in HP to fight.

    Performance: the Monolith check uses the per-group cache in snap.group_meta.
    """
    for group in reward_available_camps(snap):
        return group
    return None


def reward_available_camps(snap) -> list:
    """Every visible camp group whose reward is available for capture.

    S2.3: the utility layer picks the NEAREST reward-available group by the
    shared distance field instead of the first one a scan finds. The frozen
    baseline keeps the first-found behaviour through reward_available_camp.

    S2.3 fuel: the engine's ``group.state()`` (~28k per group) is NOT read
    for every visible group. The snapshot's cleared-group scan
    (observation._detect_cleared_groups) already asked the engine the
    question, but only for the groups that can be in our vision, so this
    function just returns its answer.

    Why the derived state cannot answer it: a DEAD camp is never reported
    by ``vision.camps()`` (probe-verified 2026-10-24), so the visible camp
    set of a cleared group is EMPTY - the "every camp visible and dead"
    derivation can never fire, and the capture candidate never appeared
    (0 gems on 01starvation in the S2.3 first cut).
    """
    _ensure_resolved()
    if bool(getattr(snap, "s23_cache", False)):
        meta = getattr(snap, "group_meta", {}) or {}
        out = []
        for key, group in getattr(snap, "cleared_groups", ()) or ():
            try:
                if meta.get(key, {}).get("is_monolith", False):
                    continue
            except Exception:
                pass
            out.append(group)
        return out
    meta = getattr(snap, "group_meta", {}) or {}
    out = []
    for group in snap.visible_camp_groups:
        try:
            key = observation.group_key(group)
        except Exception:
            key = None
        try:
            if meta.get(key, {}).get("is_monolith", False):
                continue
        except Exception:
            pass
        # RewardAvailable means the group's monsters are all dead. We try
        # to read group.state() which raises if any spawn tile is not in vision.
        try:
            state = group_state(snap, group)
        except Exception:
            continue
        if isinstance(state, game_map.CampGroupRewardAvailable):
            out.append(group)
    return out


def capture_tiles_for_group(group) -> Any:
    """Spawn tiles of a camp group (the tiles we stand on to start capture)."""
    return group.spawn_tiles()


def zone_killable_camps(snap, check_state=True, use_chebyshev=False,
                        use_cached_anchors=False) -> list:
    """All unseen, non-Monolith camp groups, ranked nearest-to-current-position.

    Used when no visible killable camp exists. Distance is the cheap
    straight-line estimate from the bot's current tile (the honest
    distance is computed by the utility layer from the shared field), so
    the bot actually walks toward the closest reachable camp rather than
    chasing a static draft-time target.

    S2.3 fuel: the positions come from the first-turn cache (no
    ``group.spawn_tiles()`` per group per turn) and the respawning test
    (one SDK call per group) is optional - the utility layer only needs
    it for the handful of candidates it actually scores.

    S2.3 fuel: ``use_cached_anchors`` ranks the pool by the group's
    CACHED representative tile (plain ints, no engine call at all). The
    previous ranking read a Vec2 coordinate per group tile, and a Vec2
    coordinate read costs ~1.8k fuel: on a 46-group map with 10 tiles per
    pack that was 1.5M fuel per turn - the whole per-turn budget, for a
    ranking the shared field then re-measures honestly anyway.

    Performance: the Monolith check uses the per-group cache in snap.group_meta.
    ``use_chebyshev`` is opt-in so the frozen baseline keeps its historical
    ordering; the S2.3 utility layer requests the movement metric.
    """
    meta = getattr(snap, "group_meta", {}) or {}
    positions_cache = getattr(snap, "group_positions", None) or {}
    anchors = getattr(snap, "group_anchors", None) or {}
    visible_handles = {
        observation.group_key(g) for g in getattr(snap, "visible_camp_groups", ()) or ()
    }
    result = []
    # S2.3 fuel: the cached (handle, group) index avoids re-deriving the
    # engine handle for every group every turn.
    index = getattr(snap, "group_index", None) or tuple(
        (observation.group_key(g), g) for g in snap.camp_groups
    )
    for key, group in index:
        if key in visible_handles:
            continue
        try:
            m = meta.get(key, {})
            if m.get("is_monolith", False):
                continue
            # Position-independent HP gate (architecture.md Stage 2 killable
            # definition, amended 2026-10-21): unseen groups are gated by
            # cached static base HP against the same KILLABLE_HP_CAP. The
            # pre-fix code left unseen groups ungated, so a 5,040-HP pack
            # flipped target/not-target across vision boundaries (the
            # 00ambush bush-line oscillator).
            if int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP:
                continue
        except Exception:
            pass
        # Skip respawning groups - no monsters until timer counts down.
        if check_state:
            try:
                if isinstance(group.state(), game_map.CampGroupRespawning):
                    continue
            except Exception:
                pass
        result.append((key, group))
    if not result:
        return []
    px, py = snap.origin_xy

    if use_cached_anchors:
        # Pure-Python ranking on the cached integer anchors.
        def _d_anchor(item) -> int:
            key = item[0]
            entry = anchors.get(key)
            if entry is None:
                return 10 ** 9
            dx = entry[0] - px
            dy = entry[1] - py
            if dx < 0:
                dx = -dx
            if dy < 0:
                dy = -dy
            if use_chebyshev:
                return dx if dx > dy else dy
            return dx + dy

        result.sort(key=_d_anchor)
        return [group for _key, group in result]

    me_pos = snap.position
    px = int(me_pos.x)
    py = int(me_pos.y)

    def _d(item) -> int:
        key, group = item
        positions = positions_cache.get(key)
        if not positions:
            try:
                positions = group.spawn_tiles().positions()
            except Exception:
                return 10 ** 9
        best = 10 ** 9
        for p in positions:
            if use_chebyshev:
                d = max(abs(int(p.x) - px), abs(int(p.y) - py))
            else:
                d = abs(int(p.x) - px) + abs(int(p.y) - py)
            if d < best:
                best = d
        return best

    result.sort(key=_d)
    return [group for _key, group in result]


def zone_killable_index(snap) -> list:
    """The unseen killable camp groups as (distance, key, group) tuples.

    The S2.3 farm pool's ranking input. It is the same pool as
    `zone_killable_camps(..., use_cached_anchors=True)`, but it carries the
    CACHED anchor distance and the group KEY with each entry:

      - the key saves a group_key() call per candidate in the farm loop
        (a Python call is ~500 fuel in the engine's wasm fuel model, and
        the pool is 30+ wide);
      - the distance is the ranking the pool is cut by, so the farm loop
        does not have to recompute it;
      - the sort is a native tuple sort (no Python key function per
        element).

    The killable filter reads the STATIC per-group facts (the Monolith
    flag and the cached static HP), which never change during a match, so
    the filter is precomputed once into `snap.killable_keys` and the
    per-group work here is a set lookup.
    """
    meta = getattr(snap, "group_meta", {}) or {}
    anchors = getattr(snap, "group_anchors", None) or {}
    killable = getattr(snap, "killable_keys", None)
    if not killable:
        # The map cache normally supplies the static set; this is the
        # fallback for a snapshot built without it.
        killable = set()
        for key, entry in meta.items():
            try:
                if entry.get("is_monolith", False):
                    continue
                if int(entry.get("static_hp", 0)) > config.KILLABLE_HP_CAP:
                    continue
            except Exception:
                pass
            killable.add(key)
        try:
            snap.killable_keys = frozenset(killable)
        except Exception:
            pass
    visible_handles = getattr(snap, "visible_group_keys", None)
    if not visible_handles:
        visible_handles = {
            observation.group_key(g)
            for g in getattr(snap, "visible_camp_groups", ()) or ()
        }
    index = getattr(snap, "group_index", None) or tuple(
        (observation.group_key(g), g) for g in snap.camp_groups
    )
    px, py = snap.origin_xy
    out = []
    for key, group in index:
        if key in visible_handles:
            continue
        if key not in killable:
            continue
        entry = anchors.get(key)
        if entry is None:
            out.append((10 ** 9, key, group))
            continue
        dx = entry[0] - px
        if dx < 0:
            dx = -dx
        dy = entry[1] - py
        if dy < 0:
            dy = -dy
        out.append((dx if dx > dy else dy, key, group))
    out.sort()
    return out


def group_respawning(group, snap=None) -> bool:
    """True if the camp group is waiting to respawn (no monsters on the map).

    One SDK call per group - the utility layer calls it only for the
    candidates it actually scores (S2.3 fuel rule). When a snapshot is
    supplied, the state is shared with the visible/reward candidate paths.

    A group we cannot SEE raises API_NOT_IN_VISION from ``group.state()``.
    That is NOT the same fact as "respawning": an unseen group's state is
    simply unknown, and the S2.2 layer treated it as alive (the zone pool
    kept it). Returning True here dropped every unseen group from the farm
    pool, which left the bot with an empty candidate set and no actions at
    all (00eight, 00ambush, 00great_wall, 00contest: 0 camps, 0 gems).
    Unknown -> assume alive; only an explicit respawning state skips it.
    """
    try:
        if snap is not None:
            state = group_state(snap, group)
        else:
            state = group.state()
        return isinstance(state, game_map.CampGroupRespawning)
    except Exception:
        return False


def altar_approach_goals(snap, altar=None) -> Any:
    """Bitboard of altar approach tiles, skipping locked altars.

    With altar: goals around that altar only (S2.2 — the bank argmax may
    score down a contested near altar and pick a farther safe one; the
    walk must honor the chosen target, or the bot heads to the nearest
    altar's ring instead, is never in tribute range of its choice, and
    stalls carrying gems).
    Without: legacy behavior — every unlocked altar (frozen baseline path).

    S2.3: the lock test is ENEMY-only. `vision.has_altar_lock` is
    TEAM-AGNOSTIC, so our own defensive lock would lock us out of our own
    altar - the engine lets the owner team pay tribute (GameRules:237). The
    frozen baseline keeps the historical team-agnostic test.
    """
    _ensure_resolved()
    goals = cambc.Bitboard()
    altars = (altar,) if altar is not None else snap.altars
    s23 = bool(getattr(snap, "s23_cache", False))
    for a in altars:
        if s23:
            if _altar_locked_by_enemy(snap, a):
                continue
        else:
            try:
                if cambc.vision.has_altar_lock(a):
                    continue
            except Exception:
                pass
        goals |= _tiles_around(a, config.TRIBUTE_RANGE)
    return goals


def nearest_unlocked_altar(snap) -> Any | None:
    """Pick the nearest altar that is not visibly locked.

    S2.3: the lock test is ENEMY-only (see altar_approach_goals).
    """
    me_pos = snap.position
    best = None
    best_d = None
    s23 = bool(getattr(snap, "s23_cache", False))
    for altar in snap.altars:
        if s23:
            if _altar_locked_by_enemy(snap, altar):
                continue
        else:
            try:
                if cambc.vision.has_altar_lock(altar):
                    continue
            except Exception:
                pass
        d = abs(altar.x - me_pos.x) + abs(altar.y - me_pos.y)
        if best_d is None or d < best_d:
            best_d = d
            best = altar
    return best


def enemy_in_range(snap) -> Any | None:
    """The visible enemy champion that can hit us on its next attack.

    S2.3: the recall candidate must never start a channel inside the
    ENEMY's attack range (a recall channel is interrupted by damage), and
    the flee candidate needs the enemy's tile to walk away from. This uses
    the enemy's own range, not ours.
    """
    me = cambc.me()
    try:
        if not (me.state() & cambc.ChampionState.CAN_ATTACK):
            return None
        origin = me.position()
    except Exception:
        return None
    for enemy in getattr(snap, "visible_enemies", ()) or ():
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            distance = max(
                abs(int(pos.x) - int(origin.x)),
                abs(int(pos.y) - int(origin.y)),
            )
            if distance <= int(enemy.attack_range()):
                return enemy
        except Exception:
            continue
    return None


def move_away_from(enemy_pos) -> bool:
    """Step away from a threat (S2.3 flee candidate).

    The engine does the walking: we pick a tile FLEE_STEP tiles away from
    the threat, in the direction that increases the distance, and let
    move_towards route around walls/holes/altars/units. Returns True when a
    step was emitted.
    """
    me = cambc.me()
    if enemy_pos is None:
        return False
    try:
        if not me.can_move():
            return False
        origin = me.position()
    except Exception:
        return False
    step = int(config.FLEE_STEP)
    dx = int(origin.x) - int(enemy_pos.x)
    dy = int(origin.y) - int(enemy_pos.y)
    sx = (dx > 0) - (dx < 0)
    sy = (dy > 0) - (dy < 0)
    if sx == 0 and sy == 0:
        sx, sy = 1, 0
    try:
        width = int(game_map.width())
        height = int(game_map.height())
    except Exception:
        width = height = 0
    # Candidate tiles: straight away first, then the two diagonals.
    candidates = []
    for cx, cy in ((sx, sy), (sx, 0), (0, sy), (-sx, sy), (-sx, 0), (0, -sy)):
        tx = int(origin.x) + cx * step
        ty = int(origin.y) + cy * step
        if width and not (0 <= tx < width):
            continue
        if height and not (0 <= ty < height):
            continue
        candidates.append(cambc.Vec2(tx, ty))
    for target in candidates:
        try:
            if me.move_towards(target):
                return True
        except Exception:
            continue
    return False

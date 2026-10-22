"""Execution manager (Stage 2).

Translates an action choice into SDK calls. SDK helpers only; never
hand-roll pathfinding.
"""
from __future__ import annotations

from typing import Any

import cambc
from cambc import game_map

import config
from managers import observation


def _ensure_resolved() -> None:
    config.resolve()


def _distance(a, b) -> int:
    return a.chebyshev_distance(b)


def _tiles_around(position, radius) -> Any:
    tiles = cambc.Bitboard()
    tiles.draw_chebyshev(position, radius)
    return tiles


def attack_first_in_range(targets, snap=None) -> bool:
    """Attack the first visible target inside our attack tiles.

    With snap, the killable filter applies (architecture.md Stage 3 gate:
    a camp whose group fails the filter — Monolith or over-cap static HP
    — is never attacked; the lost_world bait clusters woke the 200-strength
    Monolith via this path, 56 deaths). Without snap, legacy behavior is
    preserved for the frozen Stage 2 baseline.
    """
    me = cambc.me()
    if not targets:
        return False
    state = me.state()
    if not (state & cambc.ChampionState.CAN_ATTACK):
        return False
    meta = getattr(snap, "group_meta", None) if snap is not None else None
    attack_tiles = me.attack_tiles()
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
            if meta is not None and _camp_excluded(target, meta):
                continue
            me.attack(target)
            return True
    return False


def _camp_excluded(camp, meta) -> bool:
    """True if the camp's group fails the killable filter.

    Uses the engine-handle-keyed group_meta so fresh camp.camp_group()
    wrappers resolve (observation.group_key).
    """
    try:
        group = camp.camp_group()
    except Exception:
        return False
    m = meta.get(observation.group_key(group), {})
    if m.get("is_monolith", False):
        return True
    return int(m.get("static_hp", 0)) > config.KILLABLE_HP_CAP


def _blocked_tiles() -> Any:
    blocked = (
        game_map.walls() | game_map.holes() | game_map.altars()
        | game_map.spawn_region(cambc.Team.ENEMY) | cambc.vision.champion_tiles()
        | cambc.vision.camp_tiles() | cambc.vision.teleport_tiles()
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


def move_towards_closest(goals) -> bool:
    """Move one step toward the closest reachable goal (starter-style).

    Builds a distance field from the goals using `walls = blocked & ~goals`,
    then picks the reachable tile (or origin) with the smallest field value.
    Robust against wall layouts and partial blockage by enemy champions:
    the field expands from all goals through every non-wall tile and the
    reachable set keeps the candidate steps to bot-movement speed.

    Returns True if a move step was emitted.
    """
    me = cambc.me()
    if not me.can_move():
        return False
    origin = me.position()
    if goals.is_empty() or goals.is_set(origin):
        return False
    blocked = _blocked_tiles()
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


def submit_gems_to_altar(altar) -> bool:
    """Start a tribute channel at altar (if unlocked, in range, carrying gems)."""
    _ensure_resolved()
    me = cambc.me()
    if me.gem_count() <= 0:
        return False
    if me.is_submitting_gems():
        return True
    if _distance(me.position(), altar) > config.TRIBUTE_RANGE:
        return False
    me.submit_gems(altar)
    return True


def shop_on_spawn(snap, champion_name: str) -> bool:
    """Buy shards of the priority stat on spawn tiles."""
    _ensure_resolved()
    me = cambc.me()
    if not game_map.spawn_region(cambc.Team.ALLY).is_set(me.position()):
        return False
    item = config.power_shard_for(champion_name)
    gold = me.gold_count()
    cost_each = int(item.cost())
    if cost_each <= 0:
        return False
    max_count = gold // cost_each
    if max_count <= 0:
        return False
    if not me.can_buy(item, max_count):
        max_count = 1
    if max_count <= 0:
        return False
    try:
        me.buy(item, max_count)
        return True
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


def visible_killable_camps(snap) -> list:
    """Visible camp groups whose monsters' total HP ≤ KILLABLE_HP_CAP.

    Excludes Monolith (20,000 HP trap — architecture.md "Monolith trap").
    Excludes respawning groups (no monsters). Matches Stage 2 spec exactly.

    Performance: the Monolith check uses the per-group cache in snap.group_meta
    so we don't have to call ``group.camps()`` per visible group per turn.
    """
    _ensure_resolved()
    result = []
    meta = getattr(snap, "group_meta", {}) or {}
    for group in snap.visible_camp_groups:
        # Exclude Monolith (cheap cache lookup, engine-handle keyed).
        try:
            if meta.get(observation.group_key(group), {}).get("is_monolith", False):
                continue
        except Exception:
            pass
        # Skip respawning groups (no monsters on the map).
        try:
            if isinstance(group.state(), game_map.CampGroupRespawning):
                continue
        except Exception:
            pass
        # Per-spec HP cap on visible monsters.
        try:
            if _group_visible_total_hp(group) > config.KILLABLE_HP_CAP:
                continue
        except Exception:
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
    _ensure_resolved()
    me_pos = snap.position
    meta = getattr(snap, "group_meta", {}) or {}
    for group in snap.visible_camp_groups:
        try:
            if meta.get(observation.group_key(group), {}).get("is_monolith", False):
                continue
        except Exception:
            pass
        # RewardAvailable means the group's monsters are all dead. We try
        # to read group.state() which raises if any spawn tile is not in vision.
        try:
            state = group.state()
        except Exception:
            continue
        if isinstance(state, game_map.CampGroupRewardAvailable):
            return group
    return None


def capture_tiles_for_group(group) -> Any:
    """Spawn tiles of a camp group (the tiles we stand on to start capture)."""
    return group.spawn_tiles()


def zone_killable_camps(snap) -> list:
    """All unseen, non-Monolith camp groups, ranked nearest-to-current-position.

    Used when no visible killable camp exists. Distance is Manhattan from
    the bot's current tile (not spawn) so the bot actually walks toward the
    closest reachable camp rather than chasing a static draft-time target.

    Performance: the Monolith check uses the per-group cache in snap.group_meta.
    """
    me_pos = snap.position
    meta = getattr(snap, "group_meta", {}) or {}
    result = []
    for group in snap.camp_groups:
        if group in snap.visible_camp_groups:
            continue
        try:
            m = meta.get(observation.group_key(group), {})
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
        try:
            if isinstance(group.state(), game_map.CampGroupRespawning):
                continue
        except Exception:
            pass
        result.append(group)
    if not result:
        return []
    def _d(group) -> int:
        positions = group.spawn_tiles().positions()
        if not positions:
            return 10**9
        return min(
            abs(p.x - me_pos.x) + abs(p.y - me_pos.y) for p in positions
        )
    return sorted(result, key=_d)


def altar_approach_goals(snap) -> Any:
    """Bitboard of altar approach tiles, skipping locked altars."""
    _ensure_resolved()
    goals = cambc.Bitboard()
    for altar in snap.altars:
        try:
            if cambc.vision.has_altar_lock(altar):
                continue
        except Exception:
            pass
        goals |= _tiles_around(altar, config.TRIBUTE_RANGE)
    return goals


def nearest_unlocked_altar(snap) -> Any | None:
    """Pick the nearest altar that is not visibly locked."""
    me_pos = snap.position
    best = None
    best_d = None
    for altar in snap.altars:
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

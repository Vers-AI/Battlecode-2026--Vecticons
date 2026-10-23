"""Utility manager 

Generates candidate actions, scores them, returns the argmax. The killable-camp
filter (KILLABLE_HP_CAP, Monolith exclusion) is enforced *before* scoring so
the "Monolith trap" (gems-only argmax picks the 20k HP Monolith) cannot happen.

Scoring (config.py units, see plan/architecture.md §Stage 3):
  farm_camp(c)  = gems(c) * TIME_DISCOUNT ** (reach_dist + clear_turns)
  bank(a)       = gem_count * BANK_UTILITY_PER_GEM     when gem_count >= BANK_THRESHOLD
  recall        = RECALL_UTILITY                        when hp_frac < RECALL_HP_FRAC
  fight(e)      = KILL_REWARD_GOLD * TIME_DISCOUNT ** turns_to_kill
  capture(c)    = RECALL_UTILITY + 1000                 when reward-available

S2.2 intel consumers (facts folded by intelligence.fold_ally_intel):
  bank: contested altars (beacon altar flag / own sighting) score down by
        BANK_CONTEST_PENALTY — score-down, not skip, so maps where every
        altar contests still bank; region risk multiplies bank AND farm
        scores by REGION_RISK_FACTOR ** fresh-sighting count in the target
        region. P(survive)/fight-flee/carried-gem risk stay S2.3 scope.

Tie-breaking: input order (capture > bank > recall > fight > farm).
"""
from __future__ import annotations

from typing import Any

import cambc

import config
from managers import execution, intelligence


# ---------------------------------------------------------------------------
# Candidate tuple.
# ---------------------------------------------------------------------------


Candidate = Any  # tuple[kind, target, score, note]


# ---------------------------------------------------------------------------
# Tunables (mirrored from config.py at import time).
# ---------------------------------------------------------------------------


VISIBLE_CAP: int = 3
ZONE_CAP: int = 3
VISIBLE_GEM_ESTIMATE: int = 60
ZONE_GEM_ESTIMATE: int = 30


def _load_caps() -> None:
    """Refresh the mirrored cap constants from config (in case config is mutated)."""
    global VISIBLE_CAP, ZONE_CAP, VISIBLE_GEM_ESTIMATE, ZONE_GEM_ESTIMATE
    VISIBLE_CAP = int(getattr(config, "FARM_VISIBLE_CAP", 3))
    ZONE_CAP = int(getattr(config, "FARM_ZONE_CAP", 3))
    VISIBLE_GEM_ESTIMATE = int(getattr(config, "FARM_VISIBLE_GEM_ESTIMATE", 60))
    ZONE_GEM_ESTIMATE = int(getattr(config, "FARM_ZONE_GEM_ESTIMATE", 30))


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ensure_resolved() -> None:
    config.resolve()


def _manhattan(a, b) -> int:
    return abs(int(a.x) - int(b.x)) + abs(int(a.y) - int(b.y))


# ---------------------------------------------------------------------------
# Candidate generators - all return plain tuples to keep per-turn cost low.
# ---------------------------------------------------------------------------


def _capture_candidates(snap) -> list:
    """Reward-available capture: stand on a cleared spawn tile."""
    _ensure_resolved()
    try:
        reward = execution.reward_available_camp(snap)
    except Exception:
        reward = None
    if reward is None:
        return []
    return [("capture", reward, float(config.RECALL_UTILITY) + 1000.0, "reward")]


def _bank_candidates(snap) -> list:
    """Bank candidates, one per unlocked altar.

    S2.2: contested altars score down by BANK_CONTEST_PENALTY (score-down,
    not skip: maps where every altar is contested must still bank); region
    risk multiplies by REGION_RISK_FACTOR ** fresh-sighting count. The
    winner's note gains 'flipped' when the contest penalty alone changed
    which altar wins the argmax (done-when evidence; bot.py prints it
    rate-limited — a regular print, so it survives --release).
    """
    _ensure_resolved()
    if snap.gems < config.BANK_THRESHOLD:
        return []
    base = float(snap.gems) * float(config.BANK_UTILITY_PER_GEM)
    discount = float(config.TIME_DISCOUNT)
    contested = getattr(snap, "contested_altars", frozenset()) or frozenset()
    density = getattr(snap, "region_sightings", {}) or {}
    # [kind, altar, score, note, raw score, contest-only score]
    cands = []
    for altar in snap.altars:
        try:
            if cambc.vision.has_altar_lock(altar):
                continue
        except Exception:
            pass
        d = _manhattan(snap.position, altar)
        raw = base * (discount ** max(0, d))
        score = raw
        with_contest = raw
        note = f"d={d}"
        try:
            if (int(altar.x), int(altar.y)) in contested:
                with_contest = raw * float(config.BANK_CONTEST_PENALTY)
                score = with_contest
                note += " contest"
            risk = int(density.get(
                intelligence.region_of(int(altar.x), int(altar.y)), 0))
            if risk:
                score *= float(config.REGION_RISK_FACTOR) ** risk
                note += f" risk={risk}"
        except Exception:
            pass
        cands.append(["bank", altar, score, note, raw, with_contest])
    if not cands:
        return []
    winner = max(cands, key=lambda c: c[2])
    if "contest" in winner[3]:
        raw_pick = max(cands, key=lambda c: c[4])[1]
        contest_pick = max(cands, key=lambda c: c[5])[1]
        if raw_pick != contest_pick and winner[1] == contest_pick:
            winner[3] += " flipped"
    return [tuple(c[:4]) for c in cands]


def _recall_candidates(snap) -> list:
    """Recall candidate - HP below the floor."""
    _ensure_resolved()
    if snap.max_hp <= 0:
        return []
    if snap.hp >= snap.max_hp * config.RECALL_HP_FRAC:
        return []
    return [("recall", None, float(config.RECALL_UTILITY), "")]


def _fight_candidates(snap) -> list:
    """Fight candidate - enemy in attack range."""
    if not snap.can_attack:
        return []
    out = []
    me = cambc.me()
    try:
        attack_tiles = me.attack_tiles()
    except Exception:
        return []
    enemies = cambc.vision.champions(cambc.Team.ENEMY)
    for enemy in enemies:
        try:
            if not enemy.is_visible():
                continue
        except Exception:
            continue
        try:
            enemy_pos = enemy.position()
        except Exception:
            continue
        if not attack_tiles.is_set(enemy_pos):
            continue
        try:
            enemy_max_hp = float(enemy.max_health())
        except Exception:
            enemy_max_hp = 1000.0
        try:
            my_dmg = max(1.0, float(me.strength()))
        except Exception:
            my_dmg = 50.0
        turns_to_kill = max(1, int((enemy_max_hp + my_dmg - 1) // my_dmg))
        score = float(config.KILL_REWARD_GOLD) * (
            float(config.TIME_DISCOUNT) ** max(0, turns_to_kill)
        )
        out.append(("fight", enemy, score, ""))
    return out


def _group_reachable(group, reach) -> bool:
    """True if any of the group's spawn tiles is in the reachable set.

    On failure, permissive (never over-filter on a broken computation).
    """
    try:
        return not (group.spawn_tiles() & reach).is_empty()
    except Exception:
        return True


def _farm_candidates(snap) -> list:
    """Farm candidates - one per killable camp group (visible + zone), capped.

    Performance contract: per-turn cost is bounded by VISIBLE_CAP + ZONE_CAP,
    not by the full map's group count. We deliberately skip ``group.camps()``
    iteration inside the score path because it pushed per-turn fuel over
    budget in v15 (every group in the pool was iterated for the kind lookup
    even though the killable filter already excluded the costly ones).

    The killable filter (KILLABLE_HP_CAP + Monolith exclusion) is enforced in
    ``execution.visible_killable_camps`` and ``execution.zone_killable_camps``
    BEFORE scoring - mandatory per architecture.md (the "Monolith trap").
    Since 2026-10-21 the zone path is gated by cached static base HP against
    the same cap (position-independent), and every candidate must be
    walk-reachable (one shared reachable-tiles computation per turn; a
    hole-ringed vault or a monolith door must never win the argmax - the bot
    would press against it forever).

    Both pools are sorted by distance (closest first) and capped, so the
    argmax always has the top-N closest options to choose between.
    """
    _ensure_resolved()
    _load_caps()
    out = []
    pos = snap.position
    discount = float(config.TIME_DISCOUNT)
    reach = execution.reachable_tiles_from(snap)

    # Visible killable groups (already HP-filtered; monsters ≤ KILLABLE_HP_CAP).
    # Sort by distance from current position; take the nearest VISIBLE_CAP.
    try:
        visible_all = execution.visible_killable_camps(snap)
    except Exception:
        visible_all = []
    visible = _sorted_by_distance(visible_all, pos)[:VISIBLE_CAP]
    for group in visible:
        if reach is not None and not _group_reachable(group, reach):
            continue
        d = _distance_to_group(group, pos)
        clear_turns = 2
        turns = max(1, d + clear_turns)
        score = float(VISIBLE_GEM_ESTIMATE) * (discount ** max(0, turns))
        score *= _region_risk_multiplier(snap, group, pos)
        out.append(("farm", group, score, f"v d={d}"))

    # Zone (unseen) groups: zone_killable_camps already sorts by distance.
    # Cap to the nearest 3. Score conservatively (we don't know what's there).
    try:
        zone_groups = execution.zone_killable_camps(snap)[:ZONE_CAP]
    except Exception:
        zone_groups = []
    for group in zone_groups:
        if reach is not None and not _group_reachable(group, reach):
            continue
        d = _distance_to_group(group, pos)
        turns = max(1, d + 3)
        score = float(ZONE_GEM_ESTIMATE) * (discount ** max(0, turns))
        score *= _region_risk_multiplier(snap, group, pos)
        out.append(("farm", group, score, f"z d={d}"))
    return out


def _nearest_spawn_tile(group, pos):
    """Group's spawn tile nearest to pos (where a farm walk would head)."""
    try:
        tiles = group.spawn_tiles().positions()
    except Exception:
        return None
    if not tiles:
        return None
    return min(
        tiles,
        key=lambda t: abs(int(t.x) - int(pos.x)) + abs(int(t.y) - int(pos.y)),
    )


def _distance_to_group(group, pos) -> int:
    """Cheap Manhattan distance from bot to a camp group's spawn tiles."""
    tile = _nearest_spawn_tile(group, pos)
    if tile is None:
        return 10**6
    return abs(int(tile.x) - int(pos.x)) + abs(int(tile.y) - int(pos.y))


def _region_risk_multiplier(snap, group, pos) -> float:
    """REGION_RISK_FACTOR ** fresh-sighting count in the group's region.

    S2.2 consumer (b): the region of the nearest spawn tile (where the
    walk heads) carries the broadcast sighting density. Failure -> 1.0
    (S2.0-identical scoring).
    """
    density = getattr(snap, "region_sightings", None) or {}
    if not density:
        return 1.0
    tile = _nearest_spawn_tile(group, pos)
    if tile is None:
        return 1.0
    try:
        region = intelligence.region_of(int(tile.x), int(tile.y))
        return float(config.REGION_RISK_FACTOR) ** int(density.get(region, 0))
    except Exception:
        return 1.0


def _sorted_by_distance(groups, pos) -> list:
    """Sort groups by Manhattan distance from pos (closest first)."""
    try:
        return sorted(groups, key=lambda g: _distance_to_group(g, pos))
    except Exception:
        return list(groups)


# ---------------------------------------------------------------------------
# Internal helpers.
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def generate_candidates(snap) -> list[Candidate]:
    """Generate every candidate action for this turn. Defensive: each
    subgenerator is wrapped so a failure in one kind does not break the
    others. The expensive farm generator is short-circuited whenever a
    higher-value category exists (capture / bank / fight), since argmax
    cannot pick farm in that case anyway - we save the per-turn work.
    """
    out = []
    for fn in (
        lambda: _capture_candidates(snap),
        lambda: _bank_candidates(snap),
        lambda: _recall_candidates(snap),
        lambda: _fight_candidates(snap) if snap.can_attack else [],
    ):
        try:
            out.extend(fn())
        except Exception as e:
            # Log to debug stream; never let one generator kill the whole.
            try:
                cambc.debug.log(f"utility: generator failed: {e!r}")
            except Exception:
                pass

    # Short-circuit the farm generator when a higher-value category exists.
    has_higher = any(c[0] in ("capture", "bank", "fight") for c in out)
    if not has_higher:
        try:
            out.extend(_farm_candidates(snap))
        except Exception as e:
            try:
                cambc.debug.log(f"utility: farm generator failed: {e!r}")
            except Exception:
                pass
    return out


def safety_override(candidates: list[Candidate], snap) -> Candidate | None:
    """HP-floor override: force recall when HP is critically low."""
    if not candidates:
        return None
    if snap.max_hp <= 0:
        return None
    floor = float(getattr(config, "SAFETY_HP_FRAC", 0.25))
    if snap.hp >= snap.max_hp * floor:
        return None
    for c in candidates:
        if c[0] == "recall":
            return c
    return None


def best(candidates: list[Candidate]) -> Candidate | None:
    """Argmax. Ties broken by input order."""
    if not candidates:
        return None
    best = candidates[0]
    for c in candidates[1:]:
        if c[2] > best[2]:
            best = c
    return best


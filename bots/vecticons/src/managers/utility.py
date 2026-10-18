"""Utility manager (Stage 3).

Generates candidate actions, scores them, returns the argmax. The killable-camp
filter (KILLABLE_HP_CAP, Monolith exclusion) is enforced *before* scoring so
the "Monolith trap" (gems-only argmax picks the 20k HP Monolith) cannot happen.

Scoring (config.py units, see plan/architecture.md §Stage 3):
  farm_camp(c)  = gems(c) * TIME_DISCOUNT ** (reach_dist + clear_turns)
  bank(a)       = gem_count * BANK_UTILITY_PER_GEM     when gem_count >= BANK_THRESHOLD
  recall        = RECALL_UTILITY                        when hp_frac < RECALL_HP_FRAC
  fight(e)      = KILL_REWARD_GOLD * TIME_DISCOUNT ** turns_to_kill
  capture(c)    = RECALL_UTILITY + 1000                 when reward-available

Tie-breaking: input order (capture > bank > recall > fight > farm).
"""
from __future__ import annotations

from typing import Any

import cambc

import config
from managers import execution


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
    """Bank candidates, one per unlocked altar."""
    _ensure_resolved()
    if snap.gems < config.BANK_THRESHOLD:
        return []
    out = []
    base = float(snap.gems) * float(config.BANK_UTILITY_PER_GEM)
    discount = float(config.TIME_DISCOUNT)
    for altar in snap.altars:
        try:
            if cambc.vision.has_altar_lock(altar):
                continue
        except Exception:
            pass
        d = _manhattan(snap.position, altar)
        score = base * (discount ** max(0, d))
        out.append(("bank", altar, score, f"d={d}"))
    return out


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

    Both pools are sorted by distance (closest first) and capped, so the
    argmax always has the top-N closest options to choose between.
    """
    _ensure_resolved()
    _load_caps()
    out = []
    pos = snap.position
    discount = float(config.TIME_DISCOUNT)

    # Visible killable groups (already HP-filtered; monsters ≤ KILLABLE_HP_CAP).
    # Sort by distance from current position; take the nearest VISIBLE_CAP.
    try:
        visible_all = execution.visible_killable_camps(snap)
    except Exception:
        visible_all = []
    visible = _sorted_by_distance(visible_all, pos)[:VISIBLE_CAP]
    for group in visible:
        d = _distance_to_group(group, pos)
        clear_turns = 2
        turns = max(1, d + clear_turns)
        score = float(VISIBLE_GEM_ESTIMATE) * (discount ** max(0, turns))
        out.append(("farm", group, score, f"v d={d}"))

    # Zone (unseen) groups: zone_killable_camps already sorts by distance.
    # Cap to the nearest 3. Score conservatively (we don't know what's there).
    try:
        zone_groups = execution.zone_killable_camps(snap)[:ZONE_CAP]
    except Exception:
        zone_groups = []
    for group in zone_groups:
        d = _distance_to_group(group, pos)
        turns = max(1, d + 3)
        score = float(ZONE_GEM_ESTIMATE) * (discount ** max(0, turns))
        out.append(("farm", group, score, f"z d={d}"))
    return out


def _distance_to_group(group, pos) -> int:
    """Cheap Manhattan distance from bot to a camp group's spawn tiles."""
    try:
        tiles = group.spawn_tiles().positions()
        if tiles:
            return min(
                abs(int(t.x) - int(pos.x)) + abs(int(t.y) - int(pos.y))
                for t in tiles
            )
    except Exception:
        pass
    return 10**6


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


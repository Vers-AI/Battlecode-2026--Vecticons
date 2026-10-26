"""Utility manager (S2.3 - honest distances, real gem counts, boss graduation).

Generates candidate actions, scores them, returns the argmax.

Scoring (all in GEM-EQUIVALENTS, see plan/architecture.md §Core design):

  farm(g)    = value(g) x TIME_DISCOUNT ** (d(g) + clear_turns + capture)
                x P(survive the clear) x region_risk
  capture(g) = value(g) x TIME_DISCOUNT ** (d(g) + CAPTURE_TURNS)
  bank(a)    = gem_count x BANK_UTILITY_PER_GEM
                x TIME_DISCOUNT ** (d(a) + channel_turns)
                x contest/risk modifiers x P(survive the trip)
  recall     = (heal_value + gold_value)
                x TIME_DISCOUNT ** (d_spawn + RECALL_CHANNEL_TURNS)
                x P(channel survives)
  fight(e)   = (KILL_REWARD_GOLD x GOLD_TO_GEM + enemy.gem_count())
                x TIME_DISCOUNT ** turns x P(win)
  flee(e)    = (FLEE_BASE_VALUE + carried x FLEE_CARRIED_WEIGHT)
                x hp_deficit x threat_falloff
  boss(g)    = (drops + unlock_value) x TIME_DISCOUNT ** (d + turns)
                x P(survive)

`d` is the HONEST distance: one shared per-turn engine DistanceField
(managers.fielding), roots = our position. A target the field never reaches
(sealed behind a door, or beyond the field radius) scores 0 - unreachable
falls out by arithmetic, with no special case (boss_battle's altar behind
the Monolith corridor is the canonical example).

`value(g)` is the group's REAL economy: gem_value() plus gold_value()
converted at GOLD_TO_GEM. The flat 60/30 estimates of the Stage 3 layer
made every farm candidate look alike, so farm picks were nearest-first by
construction.

`clear_turns(g)` is the group's REAL clear time: its static HP divided by
the team's damage output. A Monolith therefore needs hundreds of turns and
prices itself out by arithmetic (TIME_DISCOUNT ** turns ~ 0) instead of
being excluded by a gate - the same treatment the over-cap packs get.

S2.3 changes against the Stage 3 / S2.2 layer:
  - the old straight-line proxies are gone: every candidate's distance comes
    from the shared field (Chebyshev, 8-directional, detours priced honestly);
  - farm candidates carry the group's REAL gem value instead of the flat
    60/30 estimates;
  - boss graduation: the excluded groups (Monolith, mega-packs) are scored
    FIGHT candidates - (drops + unlock_value) x discount ^ turns x P(survive)
    - where the unlock value is the precomputed reachability delta of the
      group (what opens if its tiles open);
  - the threat term: expected damage along the engine-computed path (awake
    monsters + visible enemies + remembered enemies, GameRules strength
    table) is folded in as P(survive); the carried-gem risk term multiplies
    the bank score by P(survive the trip);
  - the fight score counts the bag (the killer takes the carried gems) and
    is multiplied by a win probability; a flee candidate replaces the old
    "attack anything in range" reflex;
  - recall is a scored candidate (heal + gold to spend), so the hardcoded
    low-HP gate and the safety_override path are gone;
  - the capture candidate exists again (S2.3's first cut deleted it by
    accident): capturing is what turns a cleared camp into gems, and a
    capture in progress is a commitment (leaving the tile resets the
    engine's five-round timer).

S2.2 intel consumers (facts folded by intelligence.fold_ally_intel) are kept:
contested altars score down by BANK_CONTEST_PENALTY and region risk
multiplies bank/farm scores by REGION_RISK_FACTOR ** sighting count.

Tie-breaking: input order (capture > bank > recall > fight > flee > boss >
farm). Candidates whose score is not positive are dropped - a target we
cannot reach, or a fight we cannot win, must never win the argmax by
default.

S2.3 FUEL: every per-turn loop works on PLAIN INTS (the observation layer
caches every coordinate it needs) and reads the shared field once per
candidate anchor tile. Reading a Vec2 coordinate out of the engine costs
~1.8k fuel, so a per-turn sort over Vec2 tiles was the single largest
fuel item in the S2.3 first cut (the farm pool cost 1.5M of the 2.49M
budget on a 46-group map).
"""
from __future__ import annotations

import math
from typing import Any

import cambc

import config
from managers import execution, fielding, intelligence
from managers import observation


# ---------------------------------------------------------------------------
# Candidate tuple.
# ---------------------------------------------------------------------------
# [kind, target, score, note, goal_tile]
#
#   kind      - "capture" | "bank" | "recall" | "fight" | "flee" | "boss" |
#               "farm"
#   target    - the camp group / altar / enemy champion (None for recall)
#   score     - the utility, in gem-equivalents
#   note      - a short diagnostic string (d=, value=, exposure, ...)
#   goal_tile - the tile the walk ends at (None when there is no walk)

Candidate = Any  # list[kind, target, score, note, goal_tile]


# ---------------------------------------------------------------------------
# Tunables (mirrored from config.py at import time).
# ---------------------------------------------------------------------------


VISIBLE_CAP: int = 3
ZONE_CAP: int = 3
CAPTURE_CAP: int = 3

# Rate-limited generator-failure reporting (a plain print so it survives
# --release; the S2.2 evidence lines came from the same channel).
_ERROR_REPORTS: int = 0
_ERROR_REPORT_LIMIT: int = 5


def _load_caps() -> None:
    """Refresh the mirrored cap constants from config (in case config is mutated)."""
    global VISIBLE_CAP, ZONE_CAP, CAPTURE_CAP
    VISIBLE_CAP = int(getattr(config, "FARM_VISIBLE_CAP", 3))
    ZONE_CAP = int(getattr(config, "FARM_ZONE_CAP", 3))
    CAPTURE_CAP = int(getattr(config, "CAPTURE_MAX_CANDIDATES", 3))


def _report(where: str, error: Exception) -> None:
    """Print a generator failure, rate limited.

    `cambc.debug` has no log() in this SDK, and the failures are exactly
    what the S2.3 first cut hid (a missing config constant silently
    deleted a whole candidate class), so they must reach stdout.
    """
    global _ERROR_REPORTS
    if _ERROR_REPORTS >= _ERROR_REPORT_LIMIT:
        return
    _ERROR_REPORTS += 1
    try:
        print(f"utility: {where} failed: {error!r}", flush=True)
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ensure_resolved() -> None:
    config.resolve()


def _discount() -> float:
    return float(config.TIME_DISCOUNT)


def _capture_turns() -> int:
    """The engine's capture timer (five rounds)."""
    try:
        return max(1, int(config.CAPTURE_ROUNDS))
    except Exception:
        return 5


def _tribute_turns(gems: int) -> int:
    """The tribute channel length: ceil(gems / 10) turns."""
    per_gem = float(config.TRIBUTE_CHANNEL_TURNS_PER_GEM)
    if per_gem <= 0.0:
        return 1
    return max(1, int(math.ceil(int(gems) * per_gem)))


def _survival(damage: float, max_hp: float) -> float:
    """P(survive) from an expected damage figure.

    The model is DEATH_RISK_BASE ** (damage / max_hp): DEATH_RISK_BASE is the
    survival probability when the expected damage equals our whole health
    pool, and the probability falls away geometrically from there. A fight
    we cannot win (the Monolith hits for 200 a turn while our pool is 1,000)
    therefore prices itself out by arithmetic instead of being rescued by a
    floor - which is what the S2.3 first cut did, and it fed the bot to the
    Monolith (71 deaths on 01boss_battle in the S2.2 reference).
    """
    floor = float(config.DEATH_RISK_BASE)
    if max_hp <= 0:
        return floor
    if damage <= 0:
        return 1.0
    ratio = float(damage) / float(max_hp)
    if ratio <= 0:
        return 1.0
    p = floor ** ratio
    if p > 1.0:
        p = 1.0
    return p


def _our_damage(snap) -> float:
    """Our basic-attack damage per turn (strength)."""
    try:
        dmg = float(snap.me.strength())
    except Exception:
        dmg = 0.0
    if dmg <= 0.0:
        try:
            dmg = float(snap.me.focus())
        except Exception:
            dmg = 0.0
    if dmg <= 0.0:
        dmg = 10.0
    return dmg


def _team_dps(snap) -> float:
    """The team's damage output against a camp.

    ASSISTED_DPS_FACTOR is the expected number of champions that pile onto
    the same camp (the group helpers in the team do the same thing); it
    sets both the clear time and how the incoming damage is shared.
    """
    return _our_damage(snap) * float(config.ASSISTED_DPS_FACTOR)


def _group_meta(snap, group) -> dict:
    meta = getattr(snap, "group_meta", {}) or {}
    try:
        return meta.get(observation.group_key(group), {}) or {}
    except Exception:
        return {}


def _group_key(group) -> int:
    try:
        return observation.group_key(group)
    except Exception:
        return -1


def _group_value(meta: dict) -> float:
    """The group's real economy in gem-equivalents.

    The engine reports the group's own gem and gold totals (the sums over
    its camps); gold buys shards, so it is worth GOLD_TO_GEM of a gem.
    """
    try:
        gems = float(meta.get("gem_value", 0))
    except Exception:
        gems = 0.0
    try:
        gold = float(meta.get("gold_value", 0))
    except Exception:
        gold = 0.0
    return gems + gold * float(config.GOLD_TO_GEM)


def _clear_survival(meta: dict, clear_turns: int, max_hp: float) -> float:
    """P(survive the clear): the camp's damage output over the clear time.

    The group's monsters hit the champions in range; the damage is shared
    across the champions that pile onto the camp (ASSISTED_DPS_FACTOR).
    """
    try:
        strength = float(meta.get("total_strength", 0.0))
    except Exception:
        strength = 0.0
    if strength <= 0.0:
        return 1.0
    spread = max(1.0, float(config.ASSISTED_DPS_FACTOR))
    incoming = strength * float(clear_turns) / spread
    return _survival(incoming, max_hp)


def _standing_on_group(snap, group) -> bool:
    """True when we stand on one of the group's spawn tiles.

    That is the capture tile: standing there starts (and keeps) the
    engine's five-round capture timer.

    S2.3 FUEL: the group's spawn tiles are cached as PLAIN INTS
    (observation.group_tile_xy), so the test is pure Python - reading a
    Vec2 coordinate out of the engine costs ~1.8k fuel and the test read
    six of them per capture candidate.
    """
    key = _group_key(group)
    tiles = getattr(snap, "group_tile_xy", None) or {}
    entry = tiles.get(key)
    if entry:
        px, py = snap.origin_xy
        for tile in entry:
            if tile[0] == px and tile[1] == py:
                return True
        return False
    positions = _group_positions(snap, group)
    if not positions:
        return False
    px, py = snap.origin_xy
    for tile in positions:
        try:
            if int(tile.x) == px and int(tile.y) == py:
                return True
        except Exception:
            continue
    return False


def _group_positions(snap, group):
    """The group's cached spawn tiles (S2.3 fuel: no SDK call per turn)."""
    try:
        return observation.group_positions(snap, group)
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Per-turn tunables.
# ---------------------------------------------------------------------------
# The engine charges fuel for every wasm instruction (measured 2026-10-24:
# 20,000 trivial Python loop iterations cost 4.33M fuel, i.e. ~36 fuel per
# bytecode), so a module attribute lookup inside a per-candidate loop is a
# REAL cost: 36 candidates x 8 lookups x ~200 fuel is ~58k fuel a turn for
# nothing. The constants are therefore copied into module globals once per
# turn and read as globals (LOAD_GLOBAL) in the hot loops.
_T_DISCOUNT: float = 0.99
_T_GOLD_TO_GEM: float = 0.05
_T_CLEAR_FLOOR: int = 2
_T_ASSISTED: float = 3.0
_T_DEATH_BASE: float = 0.05
_T_RISK_FACTOR: float = 0.85
_T_CAPTURE_TURNS: int = 5
_T_MAX_HP: float = 0.0


def _refresh_tuning(snap) -> None:
    """Copy the config constants into module globals (once per turn)."""
    global _T_DISCOUNT, _T_GOLD_TO_GEM, _T_CLEAR_FLOOR, _T_ASSISTED
    global _T_DEATH_BASE, _T_RISK_FACTOR, _T_CAPTURE_TURNS, _T_MAX_HP
    _T_DISCOUNT = float(config.TIME_DISCOUNT)
    _T_GOLD_TO_GEM = float(config.GOLD_TO_GEM)
    _T_CLEAR_FLOOR = int(config.FARM_CLEAR_TURNS)
    _T_ASSISTED = float(config.ASSISTED_DPS_FACTOR)
    _T_DEATH_BASE = float(config.DEATH_RISK_BASE)
    _T_RISK_FACTOR = float(config.REGION_RISK_FACTOR)
    _T_CAPTURE_TURNS = _capture_turns()
    try:
        _T_MAX_HP = float(snap.max_hp)
    except Exception:
        _T_MAX_HP = 0.0


def _straight_distance(snap, positions) -> int:
    """Chebyshev straight-line distance to the nearest tile.

    This is only a pool pre-filter or a degraded fallback. It never
    replaces the engine field in a candidate's final score.
    """
    if not positions:
        return 10 ** 9
    px, py = snap.origin_xy
    best = 10 ** 9
    for p in positions:
        try:
            dx = int(p.x) - px
            dy = int(p.y) - py
            if dx < 0:
                dx = -dx
            if dy < 0:
                dy = -dy
            d = dx if dx > dy else dy
        except Exception:
            continue
        if d < best:
            best = d
            if best == 0:
                break
    return best


def _anchor_distance(snap, key) -> int:
    """Chebyshev straight-line distance to a group's cached anchor tile.

    S2.3 fuel: the anchor's coordinates are cached as plain ints, so this
    pre-filter costs no engine call at all (the S2.3 first cut read a Vec2
    coordinate per comparison: ~5.8k fuel per comparison).
    """
    anchors = getattr(snap, "group_anchors", None) or {}
    entry = anchors.get(key)
    if entry is None:
        return 10 ** 9
    px, py = snap.origin_xy
    dx = entry[0] - px
    dy = entry[1] - py
    if dx < 0:
        dx = -dx
    if dy < 0:
        dy = -dy
    return dx if dx > dy else dy


# ---------------------------------------------------------------------------
# Candidate generators - all return plain tuples to keep per-turn cost low.
# ---------------------------------------------------------------------------


def _capture_candidates(snap, field) -> list:
    """Reward-available capture: stand on a cleared spawn tile.

    The capture is what turns a dead camp into gems (GameRules "After
    defeating a camp, make sure to capture!"), so it is scored in the
    same gem-equivalents as everything else: the group's real value,
    discounted by the walk and by the five-round capture timer. A capture
    we have already started (we stand on the tile) is a COMMITMENT: the
    engine resets the timer when the tile is left, so it outranks the
    alternatives.

    S2.3.2: the goal is EVERY spawn tile of the group, not just its
    representative tile. A group is a cluster of a few tiles and the whole
    team wants the same one; the engine refuses an occupied destination,
    so a champion that only ever aimed at the representative tile was
    wedged next to the camp (three champions pinned for 1,900 rounds on
    01starvation). Any tile of the group counts - the capture region is
    the group's spawn tiles (GameRules: "occupy at least one of its spawn
    tiles uncontested").
    """
    _ensure_resolved()
    try:
        groups = execution.reward_available_camps(snap)
    except Exception:
        groups = []
    discount = _discount()
    turns = _capture_turns()
    out = []
    for group in list(groups)[:CAPTURE_CAP]:
        meta = _group_meta(snap, group)
        value = _group_value(meta)
        if value <= 0.0:
            continue
        key = _group_key(group)
        d, tile = fielding.group_distance(snap, field, key)
        if d is None:
            continue  # unreachable: scores 0 by arithmetic
        score = value * (discount ** max(0, d + turns))
        note = f"d={d} v={int(value)}"
        if _standing_on_group(snap, group):
            score *= float(config.CAPTURE_COMMIT_FACTOR)
            note += " commit"
        goals = _group_goal_tiles(snap, field, key, tile)
        out.append(["capture", group, score, note, goals])
    return out


def _group_goal_tiles(snap, field, key, fallback):
    """The group's spawn tiles as the candidate's goal list.

    Ordered by the shared field's distance, so the nearest tile is tried
    first and a tile taken by another champion falls through to the next
    one. The representative tile is always included (it is the anchor the
    distance read used).
    """
    tiles = []
    try:
        ordered = fielding.group_tiles_by_distance(snap, field, key)
    except Exception:
        ordered = []
    for _d, tile in ordered:
        tiles.append(tile)
    if fallback is not None and fallback not in tiles:
        tiles.insert(0, fallback)
    if not tiles:
        tiles = [fallback] if fallback is not None else []
    return tuple(tiles)


def _bank_candidates(snap, field) -> list:
    """Bank candidates, one per unlocked altar.

    S2.2: contested altars score down by BANK_CONTEST_PENALTY (score-down,
    not skip: maps where every altar is contested must still bank); region
    risk multiplies by REGION_RISK_FACTOR ** fresh-sighting count. The
    winner's note gains 'flipped' when the contest penalty alone changed
    which altar wins the argmax (done-when evidence; bot.py prints it
    rate-limited - a regular print, so it survives --release).

    S2.3: `d` is the honest distance to the altar's approach ring (the altar
    tile itself is a wall, so it is never a valid destination), the tribute
    channel length is priced in, and the carried-gem risk term multiplies
    the score by P(survive the trip) (the path-exposure term below).

    S2.3 FUEL: an empty bag means NO bank candidate at all, so the altar
    distances are not read. The distances used to be read BEFORE that test
    - 10 altars x 4 field reads plus the approach-ring ranking, measured
    ~616k fuel a turn on 01yellow_brick_road (a quarter of the per-turn
    budget) - for a candidate that was then thrown away. The dead
    `snap.altar_distance` fact (written here, read nowhere) went with it.

    S2.3 FUEL: the altars are read in the order of their SCORE BOUND. The
    straight-line distance is a lower bound of the path distance, so
    base x discount ** (straight + channel) x modifiers is an UPPER bound of
    the altar's score; the scan stops as soon as the bound cannot beat the
    best score found so far. The altars that are scored are the ones that
    can still win the argmax, and the honest distance decides between them.
    """
    _ensure_resolved()
    if snap.gems < config.BANK_THRESHOLD:
        # No gems: no bank candidate (and no altar distance reads).
        return []
    coords = getattr(snap, "altar_coords", None) or ()
    if not coords:
        return []
    base = float(snap.gems) * float(config.BANK_UTILITY_PER_GEM)
    discount = _discount()
    channel = _tribute_turns(snap.gems)
    contested = getattr(snap, "contested_altars", frozenset()) or frozenset()
    density = getattr(snap, "region_sightings", {}) or {}
    regions = getattr(snap, "altar_regions", None) or {}
    px, py = snap.origin_xy
    # One pass over the altars: the modifiers and the score bound are
    # plain-int / float arithmetic (no engine call).
    entries = []
    for ax, ay in coords:
        if _altar_locked(snap, (ax, ay)):
            continue
        dx = int(ax) - px
        if dx < 0:
            dx = -dx
        dy = int(ay) - py
        if dy < 0:
            dy = -dy
        cheap = dx if dx > dy else dy
        mod = 1.0
        note = ""
        try:
            if (int(ax), int(ay)) in contested:
                mod *= float(config.BANK_CONTEST_PENALTY)
                note = " contest"
            # S2.3 FUEL: the altar's region id is STATIC
            # (observation.altar_regions) - the score used to call
            # intelligence.region_of() per altar per turn.
            region = -1
            try:
                region = regions.get((int(ax), int(ay)), -1)
            except Exception:
                region = -1
            if region < 0:
                region = int(intelligence.region_of(ax, ay))
            risk = int(density.get(region, 0))
            if risk:
                mod *= float(config.REGION_RISK_FACTOR) ** risk
                note += f" risk={risk}"
        except Exception:
            pass
        upper = base * (discount ** max(0, cheap + channel)) * mod
        entries.append((upper, int(ax), int(ay), mod, note))
    if not entries:
        return []
    entries.sort(key=lambda item: -item[0])
    cands = []
    best = 0.0
    for upper, ax, ay, mod, note in entries:
        if upper <= best:
            # Every remaining altar is bounded below this one, so none of
            # them can win the argmax: no field read, no candidate.
            break
        d, tile = fielding.altar_distance(snap, field, ax, ay)
        if d is None:
            continue  # unreachable: scores 0 by arithmetic
        raw = base * (discount ** max(0, d + channel))
        score = raw * mod
        if score <= 0.0:
            continue
        cands.append(
            ["bank", (ax, ay), score, f"d={d} g={snap.gems}{note}", (tile,),
             raw, raw * float(config.BANK_CONTEST_PENALTY) if "contest" in note
             else raw, d]
        )
        if score > best:
            best = score
    if not cands:
        return []
    # The candidates keep the altars' coordinate order (the argmax breaks
    # ties by input order).
    cands.sort(key=lambda c: (c[1][0], c[1][1]))
    winner = max(cands, key=lambda c: c[2])
    if "contest" in winner[3]:
        raw_pick = max(cands, key=lambda c: c[5])[1]
        contest_pick = max(cands, key=lambda c: c[6])[1]
        if raw_pick != contest_pick and winner[1] == contest_pick:
            winner[3] += " flipped"
    return [c[:5] for c in cands]


def _altar_locked(snap, coord) -> bool:
    """True when an ENEMY altar lock stands on this altar.

    `has_altar_lock` is TEAM-AGNOSTIC (BotAPIPython:7243 "either team"), so
    our own defensive locks would lock us out of our own altars while the
    engine lets the owner team pay tribute (GameRules:237). The S2.3 path
    reads the enemy lock occupancy (one read per turn) and checks it here.

    S2.3 FUEL: the altar's Vec2 is STATIC (observation.altar_vecs), so the
    lock test no longer builds a fresh Vec2 per altar per turn (~2.4k
    fuel each).
    """
    locks = getattr(snap, "enemy_altar_locks", None)
    if locks is not None:
        vec = None
        vecs = getattr(snap, "altar_vecs", None) or {}
        try:
            vec = vecs.get((int(coord[0]), int(coord[1])))
        except Exception:
            vec = None
        if vec is None:
            try:
                vec = cambc.Vec2(int(coord[0]), int(coord[1]))
            except Exception:
                vec = None
        if vec is not None:
            try:
                return bool(locks.is_set(vec))
            except Exception:
                pass
    return False


def _recall_candidates(snap, field) -> list:
    """Recall candidate: heal plus gold to spend, discounted by the walk home.

    S2.3: the hardcoded low-HP gate (bot.py) and the safety_override path
    are gone - recall is scored like every other candidate:
      (heal_value + gold_to_spend_value)
        x discount ^ (d_spawn + RECALL_CHANNEL_TURNS) x P(survives)
    The heal term is active only below RECALL_HP_FRAC. The gold term is
    active only at/above RECALL_GOLD_TRIGGER, so ordinary pocket change
    never makes a healthy champion walk home. P(survives) drops when an
    enemy can interrupt the channel (a recall is damage-interrupted).
    """
    _ensure_resolved()
    if snap.max_hp <= 0:
        return []
    hp_frac = float(snap.hp) / float(snap.max_hp)
    heal_value = 0.0
    if hp_frac < float(config.RECALL_HP_FRAC):
        heal_value = float(config.RECALL_HEAL_VALUE) * max(0.0, 1.0 - hp_frac)
    gold_value = 0.0
    if int(snap.gold) >= int(config.RECALL_GOLD_TRIGGER):
        gold_value = min(int(snap.gold), int(config.RECALL_GOLD_CAP)) * float(
            config.RECALL_GOLD_VALUE
        )
    if heal_value <= 0.0 and gold_value <= 0.0:
        return []

    d, tile = fielding.spawn_distance(snap, field)
    if d is None:
        # A failed/unavailable field must not turn a long walk home into the
        # highest-scoring action. Fall back to the straight-line lower bound.
        d = _straight_distance(snap, getattr(snap, "spawn_tiles", ()) or ())
    survive = 1.0
    if _enemy_in_interrupt_range(snap):
        survive = 1.0 - float(config.RECALL_INTERRUPT_RISK)
    channel = int(config.RECALL_CHANNEL_TURNS)
    score = (heal_value + gold_value) * (
        _discount() ** max(0, d + channel)
    ) * survive
    return [["recall", None, score, f"d={d} hp={hp_frac:.2f}", (tile,)]]


def _enemy_in_interrupt_range(snap) -> bool:
    """True if a visible enemy can interrupt a channel with its next attack."""
    px, py = snap.origin_xy
    for enemy in getattr(snap, "visible_enemies", ()) or ():
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            reach = int(enemy.attack_range())
            dx = int(pos.x) - px
            dy = int(pos.y) - py
            if dx < 0:
                dx = -dx
            if dy < 0:
                dy = -dy
            distance = dx if dx > dy else dy
            if distance <= reach:
                return True
        except Exception:
            continue
    return False


def _fight_candidates(snap) -> list:
    """Fight candidates: enemy champions in attack range.

    The score counts the bag in gem-equivalent units: the killer receives
    ALL carried gems plus 50 base gold (GameRules "When a champion dies"),
    so a gem carrier is worth far more than a naked champion. Multiplied by
    a win probability built from their HP versus our damage and their
    damage versus our HP.
    """
    if not snap.can_attack:
        return []
    out = []
    try:
        attack_tiles = execution.our_attack_tiles(snap)
    except Exception:
        return []
    if attack_tiles is None:
        return []
    our_damage = _our_damage(snap)
    px, py = snap.origin_xy
    for enemy in getattr(snap, "visible_enemies", ()) or ():
        try:
            if not enemy.is_visible():
                continue
            enemy_pos = enemy.position()
            ex = int(enemy_pos.x)
            ey = int(enemy_pos.y)
        except Exception:
            continue
        if not attack_tiles.is_set(enemy_pos):
            continue
        try:
            enemy_max_hp = float(enemy.max_health())
        except Exception:
            enemy_max_hp = 1000.0
        try:
            enemy_strength = float(enemy.strength())
        except Exception:
            enemy_strength = 20.0
        try:
            bag = int(enemy.gem_count())
        except Exception:
            bag = 0
        turns_to_kill = max(1, int((enemy_max_hp + our_damage - 1) // our_damage))
        incoming = enemy_strength * turns_to_kill
        win = _survival(incoming, float(snap.max_hp))
        if win < float(config.FIGHT_WIN_FLOOR):
            win = float(config.FIGHT_WIN_FLOOR)
        reward_value = float(config.KILL_REWARD_GOLD) * float(config.GOLD_TO_GEM)
        score = (reward_value + float(bag)) * (
            _discount() ** max(0, turns_to_kill)
        ) * win
        out.append(["fight", enemy, score, f"bag={bag} t={turns_to_kill}", None])
    return out


def _flee_candidates(snap) -> list:
    """Flee candidate: weak and an enemy can hit us -> move away first.

    Replaces the old "attack anything in range" reflex: a fight we cannot
    win is worth less than the ground we give up. The score scales with
    what we would lose (the carried gems), how hurt we are and how close
    the threat is. The candidate only exists below FLEE_HP_FRAC, so a
    healthy champion still takes its fights.
    """
    if snap.max_hp <= 0:
        return []
    if float(snap.hp) >= float(snap.max_hp) * float(config.FLEE_HP_FRAC):
        return []
    deficit = max(0.0, 1.0 - float(snap.hp) / float(snap.max_hp))
    carried = float(snap.gems)
    px, py = snap.origin_xy
    for enemy in getattr(snap, "visible_enemies", ()) or ():
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            reach = int(enemy.attack_range())
            dx = int(pos.x) - px
            dy = int(pos.y) - py
            if dx < 0:
                dx = -dx
            if dy < 0:
                dy = -dy
            distance = dx if dx > dy else dy
            if distance > reach:
                continue
        except Exception:
            continue
        falloff = float(config.FLEE_THREAT_FALLOFF)
        threat = 0.5 ** (float(distance) / max(0.1, falloff))
        score = (
            float(config.FLEE_BASE_VALUE)
            + carried * float(config.FLEE_CARRIED_WEIGHT)
        ) * (deficit ** float(config.FLEE_HP_WEIGHT)) * threat
        return [["flee", enemy, score, f"weak d={distance}", None]]
    return []


def _boss_candidates(snap, field, best_other: float, clear_map=None) -> list:
    """Boss candidates: the excluded groups (Monolith, mega-packs).

    S2.3 boss graduation (owner ruling 2026-10-22): the groups the
    killable filter used to exclude are now scored fight candidates:
        (drops + unlock_value) x discount ^ (d + turns) x P(survive)
    - drops = the group's real gold/gem value (draft-time map data);
    - unlock_value = the precomputed reachability delta of the group (what
      opens if its tiles open: altars and gem groups behind the door);
    - the estimated turns are honest (group HP / our team DPS), so the early
      game prices the Monolith out BY ARITHMETIC (discount ^ hundreds of
      turns ~ 0 and a hopeless P(survive)) instead of by a gate.

    FUEL: the boss engagement field is a SECOND full-map field, so it is
    built only when a boss can still win. The pruning is arithmetic: the
    straight-line distance is a lower bound on the path distance, so
    value x discount ^ (straight_line + turns) x P(survive) is an UPPER
    bound on the honest score. A boss whose upper bound cannot beat the
    best non-boss candidate is dropped without any engine read.

    S2.3 FUEL: the excluded groups are a STATIC list of the map
    (observation.excluded_groups), so the generator no longer scans every
    group on the map every turn to find them (46 groups x a metadata read
    each = the whole boss generator's budget).

    S2.3 FUEL: the per-group constants (the prize, the clear class and the
    anchor tile) are cached as flat rows (observation.boss_rows) and the
    clear time and P(survive) come from the SHARED clear-class table, so
    the per-turn loop is pure arithmetic over tuples. The old loop re-read
    the group's metadata per boss (a dict get, four float() conversions
    and a math.ceil call): ~37k fuel per boss per turn, ~150k a turn on
    01yellow_brick_road.
    """
    out = []
    boss_rows = getattr(snap, "boss_rows", None) or ()
    if not boss_rows:
        return out
    if clear_map is None:
        clear_map = _clear_map(snap)
    if not clear_map:
        return out
    discount = _discount()
    px, py = snap.origin_xy
    bosses = []
    for key, group, value, cid, ax, ay, unlock in boss_rows:
        ci = clear_map.get(cid)
        if ci is None:
            continue
        turns = ci[0]
        survive = ci[1]
        if value <= 0.0:
            continue
        dx = ax - px
        if dx < 0:
            dx = -dx
        dy = ay - py
        if dy < 0:
            dy = -dy
        cheap = dx if dx > dy else dy
        upper = value * (discount ** max(0, cheap + turns)) * survive
        bosses.append((upper, value, turns, survive, key, group, unlock, cheap))
    if not bosses:
        return out
    # Keep only the bosses whose UPPER bound can still beat the field.
    bosses = [b for b in bosses if b[0] > best_other]
    if not bosses:
        return out
    bosses.sort(key=lambda item: -item[0])
    bosses = bosses[: int(config.BOSS_MAX_CANDIDATES)]

    # One boss-engagement field for the survivors (terrain-only walls, the
    # boss tiles are destinations).
    dests = cambc.Bitboard()
    for _upper, _value, _turns, _survive, key, _group, _unlock, _d in bosses:
        try:
            tiles = observation.group_tiles(snap, _group)
        except Exception:
            tiles = None
        if tiles is None:
            continue
        try:
            dests |= tiles
        except Exception:
            continue
    boss_field = None
    if not dests.is_empty():
        boss_field = fielding.boss_field(snap, dests)
    if boss_field is None:
        return out
    for _upper, value, turns, survive, key, group, unlock, _cheap in bosses:
        d, tile = fielding.group_distance(snap, boss_field, key)
        if d is None:
            continue  # cannot reach it even with the terrain-only wall set
        total_turns = max(1, int(d) + turns)
        score = value * (discount ** total_turns) * survive
        out.append(
            [
                "boss",
                group,
                score,
                f"d={d} turns={turns} unlock={int(unlock)}",
                (tile,),
            ]
        )
    return out


def _farm_note(d, value, turns) -> str:
    """The diagnostic note of a farm candidate (built only for the kept few)."""
    return f"d={d} v={int(value)} t={turns}"


def _clear_map(snap) -> dict:
    """The per-turn clear-time / survival table, keyed by clear class.

    The clear time (the group's static HP over the team's damage output)
    and the P(survive the clear) (its monster strength over the clear
    time) depend on the group only through its (static HP, total
    strength) pair - and there are only a handful of such pairs on a map.
    So they are computed ONCE PER TURN per class instead of once per
    candidate: the S2.3 second cut paid ~50k fuel per candidate for
    exactly this arithmetic.

    Returns {class_id: (clear_turns, survive)}.
    """
    classes = getattr(snap, "farm_classes", None) or ()
    if not classes:
        return {}
    team_dps = _team_dps(snap)
    try:
        max_hp = float(snap.max_hp)
    except Exception:
        max_hp = 0.0
    out = {}
    for cls in classes:
        try:
            hp = float(cls[0])
            strength = float(cls[1])
            cid = cls[2]
        except Exception:
            continue
        clear_turns = _T_CLEAR_FLOOR
        if hp > 0.0 and team_dps > 0.0:
            hp_turns = int(math.ceil(hp / team_dps))
            if hp_turns > clear_turns:
                clear_turns = hp_turns
        if clear_turns < 1:
            clear_turns = 1
        survive = 1.0
        if strength > 0.0:
            spread = _T_ASSISTED
            if spread < 1.0:
                spread = 1.0
            incoming = strength * clear_turns / spread
            if incoming > 0.0:
                if max_hp <= 0.0:
                    survive = _T_DEATH_BASE
                else:
                    survive = _T_DEATH_BASE ** (incoming / max_hp)
                    if survive > 1.0:
                        survive = 1.0
        out[cid] = (clear_turns, survive)
    return out


def _risk_map(snap) -> dict:
    """The per-turn region-risk multipliers, keyed by region id.

    REGION_RISK_FACTOR ** fresh-sighting count, per region. The regions
    are static (a group's anchor never moves), so only the sighting
    counts change per turn - and only the handful of regions that were
    sighted need a multiplier.
    """
    density = getattr(snap, "region_sightings", None) or {}
    if not density:
        return {}
    factor = _T_RISK_FACTOR
    out = {}
    for region, count in density.items():
        try:
            seen = int(count)
        except Exception:
            continue
        if seen > 0:
            out[region] = factor ** seen
    return out


def _farm_candidates(snap, field, clear_map=None) -> list:
    """Farm candidates - one per killable camp group (visible + zone), capped.

    Performance contract: the per-turn cost is bounded by the visible camp
    groups plus FARM_VISIBLE_POOL + FARM_ZONE_POOL scored candidates. The
    killable filter (KILLABLE_HP_CAP) gates the FARM candidacy; the
    excluded groups are not thrown away but scored as boss fight candidates
    (S2.3 boss graduation). Every candidate must be walk-reachable: the
    shared field's distance is the reachability test (unreachable scores 0).

    S2.3 FUEL (the decisive change): the pool is built from the STATIC
    farm rows (observation.farm_rows) - one tuple per group holding the
    anchor tile, the group's real economy, its clear class and its
    region. The per-turn loop therefore does no metadata reads, no
    float() conversions, no per-group scans of the map and no
    group_key() calls: it splits the rows into the visible and the zone
    pool (one pass, pure integer arithmetic), sorts the two pools with
    NATIVE tuple sorts, and then pays exactly ONE field read per
    surviving candidate.

    The S2.3 second cut did the same job with a dict-of-dicts metadata
    read, four float() conversions, a math.ceil call and a region-risk
    helper call PER CANDIDATE: ~50k fuel x 36 candidates = 2.05M, the
    whole per-turn budget.

    The score uses the group's REAL economy and its REAL clear time, so
    farm picks are neither nearest-first nor flat: a gem cluster beats a
    wolf pack of the same range, and a mega-pack prices itself out.

    S2.3: a CLEARED group (its reward is waiting for capture) is NOT a farm
    candidate - it has no live monster left to kill, so its "clear time"
    is fiction. It is a CAPTURE candidate instead.
    """
    _ensure_resolved()
    _load_caps()
    out = []
    if field is None:
        return out
    rows = getattr(snap, "farm_rows", None) or ()
    if not rows:
        return out
    killable = getattr(snap, "killable_keys", None) or frozenset()
    visible_keys = getattr(snap, "visible_group_keys", None) or frozenset()
    # S2.3.2: the groups whose reward was already claimed are NOT farm
    # targets. A respawning group has no live monster, but its cached
    # value is the group's FULL gem total and the champion stands on its
    # tile, so the "farm" candidate scored 25.6 at distance 0 and
    # outranked every other action - the bot farmed a dead group for
    # 1,900 rounds (01starvation). The claim memory on the Bot keeps the
    # group out of the pool even after the champion walked out of its
    # vision.
    try:
        cleared_keys = set(observation.claimed_group_keys(snap))
    except Exception:
        cleared_keys = set()

    px, py = snap.origin_xy
    # ONE pass over the static rows: split the pool into the visible and
    # the zone set and rank both by the cached straight-line distance
    # (plain ints, no engine call). The straight-line distance is a lower
    # bound of the path distance, so it is only used to CUT the pool -
    # the honest distance comes from the shared field below.
    visible = []
    zone = []
    for row in rows:
        key = row[0]
        if key not in killable:
            continue
        dx = row[3] - px
        if dx < 0:
            dx = -dx
        dy = row[4] - py
        if dy < 0:
            dy = -dy
        cheap = dx if dx > dy else dy
        if key in visible_keys:
            visible.append((cheap, row))
        else:
            zone.append((cheap, row))
    visible.sort()
    zone.sort()

    discount = _T_DISCOUNT
    if clear_map is None:
        clear_map = _clear_map(snap)
    risk = _risk_map(snap)
    if not clear_map:
        return out
    visible_pool = int(getattr(config, "FARM_VISIBLE_POOL", 6))
    zone_pool = int(getattr(config, "FARM_ZONE_POOL", 6))
    zone_extra = int(config.FARM_ZONE_UNCERTAINTY_TURNS)
    unreachable = fielding.UNREACHABLE
    # S2.3.2: the LIVE camps of every visible group (observation fills the
    # map every turn: a dead camp is never reported by vision.camps(), so
    # the visible camps ARE the live ones).
    live_map = getattr(snap, "group_live_tiles", None) or {}
    goal_reads = int(config.FARM_TILE_READS)
    if goal_reads < 1:
        goal_reads = 1

    for pool, limit, extra, cap in (
        (visible, visible_pool, 0, VISIBLE_CAP),
        (zone, zone_pool, zone_extra, ZONE_CAP),
    ):
        if not pool:
            continue
        scored = []
        index = 0
        for _cheap, row in pool[:limit]:
            key = row[0]
            if key in cleared_keys:
                continue  # the reward is waiting or already claimed
            tile = row[2]
            goal = None
            alts = row[10]
            live = live_map.get(key)
            if live:
                # S2.3.2: the goal is a LIVE camp of the group. The static
                # anchor (row[2]) is the spawn tile of the group's FIRST
                # camp: when that camp is killed the anchor is a DEAD tile,
                # the distance to it stays 0, the score stays maximal and
                # the walk refuses to move (we already stand on the goal
                # tile) - the champion was PINNED on the dead camp for the
                # whole match (01yellow_brick_road: (5,17) from round 19 to
                # round 2000, one camp cleared in 2,000 rounds).
                #
                # The live camps are ranked by the straight-line distance
                # (plain ints, no engine call) and the nearest few become
                # the goal list, so a champion that cannot take the first
                # tile (it is taken) falls back to the next one.
                ranked = []
                for _spawn, _sx, _sy in live:
                    dx = _sx - px
                    if dx < 0:
                        dx = -dx
                    dy = _sy - py
                    if dy < 0:
                        dy = -dy
                    ranked.append((dx if dx > dy else dy, _spawn))
                ranked.sort(key=lambda item: item[0])
                goal = tuple(t for _c, t in ranked[:goal_reads])
                if not goal:
                    goal = None
                else:
                    tile = goal[0]
                    alts = goal[1:]
            try:
                d = field.get(tile)
            except Exception:
                continue
            if d >= unreachable:
                # The anchor tile is sealed: the group's other spawn tiles
                # are tried (a group is a cardinally connected cluster, so
                # this only happens when the door cuts the cluster).
                d = unreachable
                for alt in alts:
                    try:
                        td = field.get(alt)
                    except Exception:
                        continue
                    if td < d:
                        d = td
                        tile = alt
                if d >= unreachable:
                    continue  # unreachable: scores 0 by arithmetic
            ci = clear_map.get(row[8])
            if ci is None:
                continue
            # S2.3.2: the clear time pays for the WALK ACROSS the group
            # (the group's span). The HP term alone priced a 30-camp row
            # spread over 30 tiles like a 4-camp cluster, and the gold rows
            # of 01yellow_brick_road then outranked every real gem group.
            span = row[11]
            turns = d + ci[0] + span + extra + _T_CAPTURE_TURNS
            if turns < 1:
                turns = 1
            score = row[5] * (discount ** turns) * ci[1] * risk.get(row[9], 1.0)
            if score <= 0.0:
                continue
            scored.append((-score, index, row, d, turns, goal))
            index += 1
        scored.sort()
        for _ns, _i, row, d, turns, goal in scored[:cap]:
            if goal is None:
                goal = (row[2],)
            out.append(
                ["farm", row[1], -_ns, _farm_note(d, row[5], turns), goal]
            )
    return out


# ---------------------------------------------------------------------------
# Threat term: expected damage along the engine-computed path.
# ---------------------------------------------------------------------------


def _apply_path_exposure(cands, snap, field) -> None:
    """Fold the path exposure into the top candidates' P(survive).

    The path comes from the CACHED gradient walk (managers.fielding): the
    engine does the walking, Python only reads the tiles. The walk is
    cached on the bot, so the exposure term costs no extra engine reads -
    the same walk also produces the movement step.

    Only the tiles next to the destination (the first PATH_EXPOSURE_STEPS
    tiles of the walk - where the destination's monsters wait) are priced,
    and only the top PATH_EXPOSURE_CANDIDATES are probed, so the per-turn
    fuel cost stays bounded. This is the carried-gem risk term: the bank
    score is multiplied by P(survive the trip).

    The bank candidate is always part of the read set (the carried-gem risk
    term is a S2.3 deliverable), plus the best candidate overall.
    """
    if not cands or field is None:
        return
    threat_list = fielding.threats(snap)
    if not threat_list:
        return
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    # S2.3 FUEL: the exposure probe costs a gradient walk per probed goal,
    # and the walk is CACHED on the bot (managers.fielding), so a repeated
    # goal is nearly free. A fuel guard here was measured and REJECTED:
    # skipping the probe when the turn is nearly spent changed the
    # argmax on 01boss_battle (48 camps cleared at HEAD -> 34 with the
    # guard, 45 without it) and did not even save fuel (1,054 exhaustion
    # events at HEAD -> 190 with the guard, 143 without it) - the other
    # fuel fixes in this stage removed the exhaustion by themselves.
    budget = int(config.PATH_EXPOSURE_CANDIDATES)
    order = sorted(range(len(cands)), key=lambda i: -float(cands[i][2]))
    selected = []
    for idx in order:
        if len(selected) >= budget:
            break
        if cands[idx][0] == "bank":
            selected.append(idx)
            break
    for idx in order:
        if len(selected) >= budget:
            break
        if idx not in selected:
            selected.append(idx)
    for idx in selected:
        cand = cands[idx]
        kind = cand[0]
        goal = cand[4] if len(cand) > 4 else None
        if goal is None:
            continue
        # The boss's own tiles are walls in the scoring field: the
        # engagement path is read from the terrain-only boss field.
        path = fielding.exposure_tiles(
            bot, snap, "boss" if kind == "boss" else "scoring", goal
        )
        damage = fielding.path_exposure(path, threat_list)
        if damage <= 0.0:
            continue
        survive = _survival(damage, float(snap.max_hp))
        cand[2] = float(cand[2]) * survive
        cand[3] = f"{cand[3]} exp={int(damage)}"


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def generate_candidates(snap) -> list[Candidate]:
    """Generate every candidate action for this turn.

    One shared DistanceField per turn (managers.fielding) feeds every
    candidate's distance. Defensive: each subgenerator is wrapped so a
    failure in one kind does not break the others.
    """
    out = []
    field = None
    try:
        _ensure_resolved()
        _refresh_tuning(snap)
        field = fielding.scoring_field(snap)
    except Exception as error:
        _report("scoring field", error)
        field = None
    # The clear-class table (clear time + P(survive the clear) per class) is
    # shared by the farm and the boss generators: the classes are static, so
    # the table is built ONCE per turn.
    try:
        clear_map = _clear_map(snap)
    except Exception as error:
        _report("clear map", error)
        clear_map = {}
    for name, fn in (
        ("capture", lambda: _capture_candidates(snap, field)),
        ("bank", lambda: _bank_candidates(snap, field)),
        ("recall", lambda: _recall_candidates(snap, field)),
        ("fight", lambda: _fight_candidates(snap)),
        ("flee", lambda: _flee_candidates(snap)),
    ):
        try:
            out.extend(fn())
        except Exception as error:
            _report("generator", error)

    # The farm pool is always scored: the S2.2 short-circuit (skip farm
    # when a higher category exists) assumed the categories were ordered by
    # value, which the S2.3 scoring no longer guarantees - a weak fight
    # candidate must not hide a much better farm target.
    try:
        out.extend(_farm_candidates(snap, field, clear_map))
    except Exception as error:
        _report("farm generator", error)

    # Boss graduation: scored against the best candidate the other kinds
    # produced, so a hopeless boss never builds its own field.
    try:
        best_other = max([float(c[2]) for c in out], default=0.0)
        out.extend(_boss_candidates(snap, field, best_other, clear_map))
    except Exception as error:
        _report("boss generator", error)

    # Threat term: fold the path exposure into the top candidates.
    try:
        _apply_path_exposure(out, snap, field)
    except Exception as error:
        _report("path exposure", error)

    # A candidate worth nothing never wins the argmax (unreachable targets,
    # hopeless fights): drop the non-positive scores.
    return [c for c in out if float(c[2]) > 0.0]


def best(candidates: list[Candidate]) -> Candidate | None:
    """Argmax. Ties broken by input order."""
    if not candidates:
        return None
    best_c = candidates[0]
    for c in candidates[1:]:
        if c[2] > best_c[2]:
            best_c = c
    return best_c

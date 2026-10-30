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
  fight(e)   = (bag + kill_gold + denial + capture_reset + tiebreak)
                x TIME_DISCOUNT ** turns x P(win | our CURRENT health)
  chase(e)   = the same value x TIME_DISCOUNT ** (path + turns)
                x P(win) x CHASE_SUCCESS_FACTOR
  flee(e)    = (FLEE_BASE_VALUE + carried x FLEE_CARRIED_WEIGHT)
                x hp_factor x escape_probability
  boss(g)    = (drops + unlock_value) x TIME_DISCOUNT ** (d + turns)
                x P(survive)

  Every non-combat candidate is multiplied by the THREAT factor
  (P(survive the action while a hostile champion is on us): farming or
  banking under attack is worth what we actually collect, which is
  nothing when we are killed on the way.

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
from managers import abilities, execution, fielding, intelligence
from managers import observation
from managers import transit


# F1 CLOSEOUT (2026-10-28): Threat-view tuple cache
# The fight/chase/flee generators used to re-read the engine per candidate.
# Now we cache the full enemy tuple as plain ints at the start of the turn:
# (ex, ey, reach, dmg, hp, max_hp, bag) — one build, many reads.
# The cache key is the engine handle (_enemy_handle(enemy)), so the same
# enemy across generators hits the cached tuple.
_ENEMY_TUPLE_CACHE: dict[int, tuple[int, int, int, float, float, float, int]] = {}
# Filled by _hostile_threat's per-turn pass (see _get_enemy_tuple): one
# engine pass per turn builds the threat picture AND the tuple cache.


# F1 CLOSEOUT (2026-10-28): Chase stickiness state
# Once we commit to a chase target, a commitment factor holds until
# target dies, exits vision, or score collapses.
# Pattern: CAPTURE_COMMIT_FACTOR from config.
_CHASE_COMMITMENT: dict[str, tuple[int, float]] = {}  # target_handle -> (target_handle, boosted_score)


# F1 CLOSEOUT (2026-10-28): Track the best non-fight score for EV pruning
_BEST_NON_FIGHT_SCORE: float = 0.0


# F1 CLOSEOUT (2026-10-28): the stdout evidence counters.
#
#   _CACHE_HITS   - a consumer was served the enemy tuple from the
#                   per-turn cache (no engine read)
#   _CACHE_MISSES - a consumer asked for an enemy the cache did not hold
#                   (the fallback engine read path)
#   _EV_PRUNES    - a fight/chase candidate was dropped by the score bound
#                   BEFORE the full EV math (and, for a chase, before the
#                   field read)
#   _CHASE_COMMITS- a chase commitment was RECORDED (a new target)
#   _CHASE_BOOSTS - a recorded commitment was re-applied to its target
#
# These are the numbers the close-out campaign must show on stdout; they
# are printed by bot.py (rate limited, a regular print so it survives
# --release) and harvested from the replay bundle by scripts/run_pool.py.
_CACHE_HITS: int = 0
_CACHE_MISSES: int = 0
_EV_PRUNES: int = 0
_CHASE_COMMITS: int = 0
_CHASE_BOOSTS: int = 0


# F3b: the regroup state lives on the bot instance, keyed by the
# champion id (bot.regroup_streak) - the bot instance and the module
# globals are shared by the whole team, so a module-level counter would
# count the turns of ALL our champions at once.


def counters() -> dict:
    """The close-out counters (the stdout evidence of the three mechanisms)."""
    return {
        "cache_hits": int(_CACHE_HITS),
        "cache_misses": int(_CACHE_MISSES),
        "ev_prunes": int(_EV_PRUNES),
        "chase_commits": int(_CHASE_COMMITS),
        "chase_boosts": int(_CHASE_BOOSTS),
    }


def _enemy_handle(enemy) -> int:
    """The stable engine handle of a champion - THE CACHE KEY.

    `int(enemy)` is NOT the handle: the champion object exposes
    `__index__`, and `int(champion)` raises TypeError. The fill and the
    read must use the SAME key, or the cache never hits - which is
    exactly what the close-out smoke test measured (hits=0, misses=126:
    the tuple cache and the chase commitment were dead).

    The pattern mirrors managers.intelligence._handle: __index__ first,
    the champion type as a degraded fallback (two mirror-picked enemies
    of the same type can share a key - acceptable last resort), and -1
    when even that fails (never cached under -1).
    """
    try:
        return int(enemy.__index__())
    except Exception:
        try:
            return int(enemy.champion_name())
        except Exception:
            return -1


def _get_enemy_tuple(handle: int) -> tuple | None:
    """Get the cached enemy tuple by engine handle.

    The cache is filled by _hostile_threat's per-turn pass (one engine
    pass, shared with the threat picture) - there is no separate build
    pass, and the previous turn's entries are cleared at the top of the
    pass.

    Every read is counted: a hit is a consumer served from plain ints, a
    miss is a consumer that had to fall back to the engine.
    """
    global _CACHE_HITS, _CACHE_MISSES
    facts = _ENEMY_TUPLE_CACHE.get(handle)
    if facts is None:
        _CACHE_MISSES += 1
        return None
    _CACHE_HITS += 1
    return facts


def record_chase_win(cand) -> None:
    """Record that this turn's argmax was won by a chase candidate.

    Called by bot.py after `utility.best` picks a chase: the commitment
    (handle + boosted score) is what the stickiness boost in
    generate_candidates re-applies on later turns. Without this write,
    the commitment dict stays empty and the boost never fires.
    """
    global _CHASE_COMMITMENT
    global _CHASE_COMMITS
    try:
        if not cand or cand[0] != "chase":
            return
        handle = _enemy_handle(cand[1])
        if handle < 0:
            return
        factor = float(getattr(config, "CHASE_COMMIT_FACTOR", 2.0))
        boosted = float(cand[2]) * factor
        if handle not in _CHASE_COMMITMENT:
            # A NEW commitment (the target changed): that is the event the
            # close-out counter counts.
            _CHASE_COMMITS += 1
        _CHASE_COMMITMENT[handle] = (handle, boosted)
    except Exception:
        return


def clear_chase_commitments() -> None:
    """Drop every commitment (called on respawn/death from bot.py)."""
    global _CHASE_COMMITMENT
    _CHASE_COMMITMENT.clear()


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
    floor = _T_DEATH_BASE
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
# B1 fuel: the two constants the EV model reads PER CANDIDATE. They are
# copied here once per turn so the hot path never walks into the config
# module (a module attribute lookup is a real cost in this wasm build:
# ~430 fuel per statement, and the farm generator scores ~24 candidates a
# turn).
_T_DEATH_COST: float = 10.0
_T_PURSUIT_TURNS: int = 3
# B1: the capture HOLD - how long a champion may hold a capture that does
# not complete, and how close it must be for the hold to run.
_T_CAPTURE_HOLD: int = 12
_T_CAPTURE_HOLD_TILES: int = 2


def _refresh_tuning(snap) -> None:
    """Copy the config constants into module globals (once per turn)."""
    global _T_DISCOUNT, _T_GOLD_TO_GEM, _T_CLEAR_FLOOR, _T_ASSISTED
    global _T_DEATH_BASE, _T_RISK_FACTOR, _T_CAPTURE_TURNS, _T_MAX_HP
    global _T_DEATH_COST, _T_PURSUIT_TURNS
    global _T_CAPTURE_HOLD, _T_CAPTURE_HOLD_TILES
    _T_DISCOUNT = float(config.TIME_DISCOUNT)
    _T_GOLD_TO_GEM = float(config.GOLD_TO_GEM)
    _T_CLEAR_FLOOR = int(config.FARM_CLEAR_TURNS)
    _T_ASSISTED = float(config.ASSISTED_DPS_FACTOR)
    _T_DEATH_BASE = float(config.DEATH_RISK_BASE)
    _T_RISK_FACTOR = float(config.REGION_RISK_FACTOR)
    _T_CAPTURE_TURNS = _capture_turns()
    _T_DEATH_COST = float(config.FIGHT_DEATH_COST)
    try:
        _T_PURSUIT_TURNS = int(config.THREAT_PURSUIT_TURNS)
    except Exception:
        _T_PURSUIT_TURNS = 3
    try:
        _T_CAPTURE_HOLD = int(config.CAPTURE_HOLD_MAX_ROUNDS)
    except Exception:
        _T_CAPTURE_HOLD = 12
    try:
        _T_CAPTURE_HOLD_TILES = int(config.CAPTURE_HOLD_TILES)
    except Exception:
        _T_CAPTURE_HOLD_TILES = 2
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


def _capture_hold(snap, key, standing) -> int:
    """How long this champion has been HOLDING this group's capture.

    A capture that does not complete is a capture that is BLOCKED: the
    GameRules delay the whole group while any of its spawn tiles is
    occupied by an enemy ("Any occupied spawn tile delays the entire
    group"), and an enemy entering the region resets the progress. The
    measured symptom (01yellow_brick_road, the release build): our
    champion stood ON the capture tile of a 30-camp row for 900 rounds
    while the capture never completed - the capture candidate outscored
    every farm (the row's 600 gold is worth 12 gem-equivalents, the
    nearby farms 7) and the champion never left, so the whole match was
    spent holding one tile.

    The hold therefore TIMES OUT: after CAPTURE_HOLD_MAX_ROUNDS rounds
    of STANDING on the tile without the reward arriving, the capture is
    not going to happen and the champion goes back to farming.

    Only a champion that actually stands on one of the group's spawn
    tiles holds the capture - a champion that is merely NEAR the group
    (waiting for an ally to step off the tile, or walking in) holds
    nothing, and the hold must not expire under it (01elk_arena: 130
    camp clears and 72 banked gems at S2.4 - the first cut of this
    timeout expired the capture of every group the champion could not
    step onto, and the map banked 0 gems).

    The memory lives on the Bot instance (per champion) and is pruned
    when the group stops being a capture candidate (the capture landed,
    or the group was claimed).
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return 0
    mem = getattr(bot, "capture_hold", None)
    if not isinstance(mem, dict):
        mem = {}
        try:
            bot.capture_hold = mem
        except Exception:
            return 0
    now = int(getattr(snap, "round_number", 0) or 0)
    if not standing:
        # Not on the tile: we hold nothing, so we hold no timer either.
        mem.pop(key, None)
        return 0
    start = mem.get(key)
    if start is None:
        mem[key] = now
        return 0
    return now - int(start)


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
    seen = set()
    for group in list(groups)[:CAPTURE_CAP]:
        meta = _group_meta(snap, group)
        value = _group_value(meta)
        if value <= 0.0:
            continue
        key = _group_key(group)
        seen.add(key)
        d, tile = fielding.group_distance(snap, field, key)
        if d is None:
            continue  # unreachable: scores 0 by arithmetic
        score = value * (discount ** max(0, d + turns))
        # B1: the capture HOLD - a capture that does not complete is a
        # capture that is blocked (an enemy on one of the group's spawn
        # tiles delays the whole group). The champion must not spend the
        # match holding a tile that never pays out.
        standing = _standing_on_group(snap, group)
        hold = _capture_hold(snap, key, standing)
        if hold > _T_CAPTURE_HOLD:
            continue
        # B1: a capture we perform while a hostile champion is on us is
        # worth what we actually collect - nothing if we die first.
        score = _threat_adjust(snap, d + turns, score)
        note = f"d={d} v={int(value)}"
        if standing:
            score *= float(config.CAPTURE_COMMIT_FACTOR)
            note += " commit"
        goals = _group_goal_tiles(snap, field, key, tile)
        out.append(["capture", group, score, note, goals])
    _prune_capture_hold(snap, seen)
    return out


def _prune_capture_hold(snap, seen) -> None:
    """Forget the groups that are not capture candidates any more.

    A group that stops being a capture candidate either paid out (the
    capture landed) or was claimed by the enemy - both end the hold, so
    the next capture of that group (after the respawn) starts fresh.
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    mem = getattr(bot, "capture_hold", None)
    if not isinstance(mem, dict) or not mem:
        return
    for key in list(mem):
        if key not in seen:
            mem.pop(key, None)


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

    F6: an ENEMY-LOCKED altar is no longer filtered out - it stays a
    candidate whose cost carries the BREAK COST (the engine constant
    ALTAR_LOCK_HITS, five basic attacks) on top of the walk and the
    tribute channel: base x discount ** (d + hits + channel) x mod. The
    lock-break is PRICED, not gated: it competes in the SAME argmax, so a
    multi-altar map still routes around the lock (an open altar is
    cheaper in discounted turns) while a single-altar map breaks the
    lock instead of idling. The score bound carries the same +hits, so
    the early-break stays correct.
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
    try:
        lock_hits = max(1, int(config.ALTAR_LOCK_HITS))
    except Exception:
        lock_hits = 5
    # One pass over the altars: the modifiers and the score bound are
    # plain-int / float arithmetic (no engine call).
    entries = []
    for ax, ay in coords:
        # F6: the lock test is the one bitboard read per altar that was
        # already here; the lock no longer removes the altar from the
        # argmax, it only makes the altar more expensive.
        locked = _altar_locked(snap, (ax, ay))
        break_turns = lock_hits if locked else 0
        dx = int(ax) - px
        if dx < 0:
            dx = -dx
        dy = int(ay) - py
        if dy < 0:
            dy = -dy
        cheap = dx if dx > dy else dy
        mod = 1.0
        note = ""
        if locked:
            note = f" lockbreak+{break_turns}"
        try:
            if (int(ax), int(ay)) in contested:
                mod *= float(config.BANK_CONTEST_PENALTY)
                note += " contest"
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
        upper = base * (discount ** max(0, cheap + channel + break_turns)) * mod
        entries.append((upper, int(ax), int(ay), mod, note, break_turns))
    if not entries:
        return []
    entries.sort(key=lambda item: -item[0])
    cands = []
    best = 0.0
    for upper, ax, ay, mod, note, break_turns in entries:
        if upper <= best:
            # Every remaining altar is bounded below this one, so none of
            # them can win the argmax: no field read, no candidate.
            break
        d, tile = fielding.altar_distance(snap, field, ax, ay)
        if d is None:
            continue  # unreachable: scores 0 by arithmetic
        raw = base * (discount ** max(0, d + channel + break_turns))
        score = raw * mod
        # B1: banking under attack is worth what we bank, not what we
        # carry - the trip ends when we are dead on it. The gross already
        # IS the carried bag (discounted by the trip), so the bag is not
        # credited again in the success branch (keep_bag=False).
        # F6: the lock-break adds its five attack-turns to the exposure
        # window too - the banker stands next to the altar while it
        # hammers the lock, so the threat term prices the same exposure.
        score = _threat_adjust(snap, d + channel + break_turns, score,
                                keep_bag=False)
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


# ---------------------------------------------------------------------------
# B1 - the threat picture: who can hurt us, and how much.
# ---------------------------------------------------------------------------


def _hostile_threat(snap) -> dict:
    """The per-turn threat picture: the enemies that can hurt us.

    ONE pass over the visible enemy champions per turn; the result is
    cached on the snapshot, because the fight, chase, flee, farm, bank and
    capture generators all read it.

    F1 CLOSEOUT: the pass also fills the enemy tuple cache
    (_ENEMY_TUPLE_CACHE) with the full read set (pos/reach/dmg/hp/max_hp/
    bag as plain ints) - ONE engine pass per turn instead of the separate
    cache-build pass that measured +14% fuel on the closeout campaign.

    Two radii:
      CONTACT - the enemy is inside its OWN attack range: it can hit us
                on its next attack.
      NEAR    - the enemy is within its attack range plus
                THREAT_PURSUE_TILES: it is closing, and a champion that
                keeps farming will be in contact within a turn or two.

    The "recent damage from a champion" signal of the owner's ruling is
    the HP drop: bot.py records our health from the previous turn, and a
    drop with an enemy next to us is proof that the enemy is hostile (it
    may have hit us with an ability from outside its basic range). It is
    free (one float compare) and it only promotes the NEAR enemies when
    nothing is already in contact.
    """
    cached = getattr(snap, "threat_view", None)
    if cached is not None:
        return cached
    # F1 CLOSEOUT: the tuple cache is filled inside this pass; clear the
    # previous turn's entries first.
    _ENEMY_TUPLE_CACHE.clear()
    view = {
        "all": [],       # (enemy, dist, damage)
        "contact": [],   # (enemy, dist, damage) - can hit us NOW
        "near": [],      # (enemy, dist, damage) - closing
        "contact_dps": 0.0,
        "near_dps": 0.0,
        # F3a: the enemy ability burst. An enemy whose cooldowns are down
        # is much more dangerous than its basic attack says, and every
        # P(survive) has to know that. The burst is a ONE-SHOT number (an
        # 8-round cooldown, not a per-turn damage source), so it enters
        # the threat model as a single chunk.
        "burst": 0.0,
        "burst_by_handle": {},
    }
    px, py = snap.origin_xy
    pursue = int(config.THREAT_PURSUE_TILES)
    try:
        enemies = getattr(snap, "visible_enemies", ()) or ()
    except Exception:
        enemies = ()
    for enemy in enemies:
        try:
            if not enemy.is_visible():
                continue
            pos = enemy.position()
            ex = int(pos.x)
            ey = int(pos.y)
            reach = int(enemy.attack_range())
            try:
                dmg = float(enemy.strength())
            except Exception:
                dmg = 20.0
            if dmg <= 0.0:
                dmg = 20.0
        except Exception:
            continue
        # F1 CLOSEOUT: fill the tuple cache in THIS pass - hp/max_hp/bag
        # are the only extra reads, and the position/strength/range reads
        # are already paid for above.
        try:
            hp = float(enemy.health())
            if hp <= 0.0:
                hp = float(enemy.max_health())
            max_hp = float(enemy.max_health())
            bag = int(enemy.gem_count())
            handle = _enemy_handle(enemy)
            if handle >= 0:
                _ENEMY_TUPLE_CACHE[handle] = (ex, ey, reach, dmg, hp, max_hp, bag)
        except Exception:
            pass
        dx = ex - px
        if dx < 0:
            dx = -dx
        dy = ey - py
        if dy < 0:
            dy = -dy
        dist = dx if dx > dy else dy
        entry = (enemy, dist, dmg, pos)
        view["all"].append(entry)
        if dist <= reach:
            view["contact"].append(entry)
            view["contact_dps"] += dmg
        elif dist <= reach + pursue:
            view["near"].append(entry)
            view["near_dps"] += dmg
    if (not view["contact"]) and view["near"] and getattr(snap, "hit_recently", False):
        # We took damage this turn and no enemy is in basic range: the
        # ones next to us hit us with an ability. Treat them as contact.
        for entry in view["near"]:
            view["contact"].append(entry)
            view["contact_dps"] += entry[2]
        view["near_dps"] = 0.0
    # F3a: the enemy ability burst, folded into the threat picture.
    #
    # FUEL: the read is ~4 engine calls per enemy (the cached ability
    # slots, their cooldowns and the damage against us), so it is
    # restricted to the enemies that can ACTUALLY REACH US - the threat
    # view's contact and near lists. A far-away enemy's ability is not
    # incoming damage, so pricing it would cost fuel and mean nothing.
    # The cap (config.ABILITY_THREAT_ENEMY_CAP) takes the closest ones.
    cap = int(config.ABILITY_THREAT_ENEMY_CAP)
    if cap > 0:
        pool = []
        seen_handles = set()
        for entry in list(view["contact"]) + list(view["near"]):
            h = _enemy_handle(entry[0])
            if h in seen_handles:
                continue
            seen_handles.add(h)
            pool.append(entry)
        pool.sort(key=lambda entry: entry[1])
        for entry in pool[:cap]:
            enemy = entry[0]
            pos = entry[3]
            try:
                ex = int(pos.x)
                ey = int(pos.y)
            except Exception:
                continue
            try:
                burst = abilities.enemy_burst_against(snap, enemy, ex, ey)
            except Exception:
                burst = 0.0
            if burst > 0.0:
                view["burst_by_handle"][_enemy_handle(enemy)] = burst
    # The chunk that lands on US: the bursts of the enemies that are in
    # contact (or closing), summed.
    contact_handles = set()
    for entry in view["contact"]:
        contact_handles.add(_enemy_handle(entry[0]))
    burst_total = 0.0
    for handle, burst in view["burst_by_handle"].items():
        if handle in contact_handles:
            burst_total += burst
    if burst_total <= 0.0 and not contact_handles:
        # Nobody is in contact: the closest enemies' bursts are the
        # incoming threat if they close.
        burst_total = sum(view["burst_by_handle"].values())
    view["burst"] = burst_total
    try:
        snap.threat_view = view
    except Exception:
        pass
    return view


def _threat_factor(snap, turns, view=None) -> float:
    """P(survive the action) while a hostile champion is on us.

    This is the "never mid-farm-inert" term (the owner's ruling): the
    score of an action we are PERFORMING while someone is beating on us
    must be discounted by the chance we survive it, because a champion
    that farms to its death collects nothing and loses the bag it was
    carrying. Before this term, the measured behaviour was a bot that
    kept picking "farm" for round after round while three enemies killed
    it (00elkring, our replay: 1 kill in 5 maps, 143 gems stolen).

    The survival model is the same geometric one every other candidate
    uses, but on our CURRENT health: a champion at 20% health must not
    believe it can farm through a fight.

    FUEL: the caller passes the threat VIEW in (it is cached on the
    snapshot, but the lookup is still a statement per candidate).
    """
    if view is None:
        view = _hostile_threat(snap)
    contact_dps = float(view["contact_dps"])
    near_dps = float(view["near_dps"])
    if contact_dps <= 0.0 and near_dps <= 0.0:
        return 1.0
    t = max(1, int(turns))
    incoming = contact_dps * t + near_dps * min(t, _T_PURSUIT_TURNS)
    # F3a: the enemy's ready ability burst is a ONE-SHOT chunk of
    # incoming damage (an 8-round cooldown, not a per-turn source), so
    # it enters once, not multiplied by the action's length.
    burst = float(view.get("burst", 0.0) or 0.0)
    if burst > 0.0:
        incoming += burst
    if incoming <= 0.0:
        return 1.0
    hp = float(snap.hp)
    if hp <= 0.0:
        hp = float(snap.max_hp)
    if hp <= 0.0:
        return _T_DEATH_BASE
    return _survival(incoming, hp)


def _our_reach(snap) -> int:
    """Our own basic-attack range (1 for melee, 3 for ranged)."""
    try:
        return int(snap.me.attack_range())
    except Exception:
        return 1


def _our_bag(snap) -> float:
    """The gems we carry right now (a champion that dies loses them all).

    GameRules: "When a champion dies ... the killer receives all carried
    gems"; "If a camp kills you, the carried gems are lost."
    """
    try:
        return float(snap.gems)
    except Exception:
        return 0.0


def _ev(snap, p, gain, bag_in_success=False, stake_share=1.0) -> float:
    """The expected value of an action, in gem-equivalents.

    The ONE form every candidate uses (B1):

        EV = P(survive) x (what the action collects)
             - (1 - P(survive)) x (the bag we lose + what our death costs)

    The stake is the bag we carry (a champion that dies loses every
    carried gem - the killer takes them) plus the respawn window we
    produce nothing through.

    `bag_in_success` credits the bag in the SUCCESS branch as well: the
    fight, the chase and the flee are actions whose POINT is what we hold
    afterwards, so a loaded champion that escapes (or wins) keeps its
    gems and must be paid for that. Without it the flee scored
    negative whenever we carried a bag, every candidate went negative with
    it, the argmax froze and the champion stood still and died with the
    bag (the probe on 00gemfight: "BEST=NONE" on 3 of 28 contact rounds,
    22 gems lost to deaths in one match).

    The BANK candidate never credits the bag: its gross already IS the
    delivered bag.
    """
    bag = _our_bag(snap)
    stake = (bag + _T_DEATH_COST) * float(stake_share)
    if bag_in_success:
        gain = gain + bag
    return p * gain - (1.0 - p) * stake


def _fight_win(ratio: float) -> float:
    """P(we win the fight) from the damage we take to the damage we give.

    The ratio is (expected incoming damage over the fight) / (our CURRENT
    health). A ratio of 1.0 means we die about when they do. The logistic
    is centred at FIGHT_WIN_RATIO_CENTER (we must survive with a little
    margin) and scaled by FIGHT_WIN_LOGISTIC_K.

    The S2.3 model (DEATH_RISK_BASE ** ratio) was far too harsh: it
    priced a fight we win with half our health left at 22%, and the probe
    showed "win=0.15" (the floor) on almost every contact round - the
    fight candidate never beat the escape, and the bot never fought back.
    """
    k = float(config.FIGHT_WIN_LOGISTIC_K)
    center = float(config.FIGHT_WIN_RATIO_CENTER)
    try:
        z = k * (float(ratio) - center)
        if z >= 40.0:
            p = 0.0
        elif z <= -40.0:
            p = 1.0
        else:
            # NOTE the sign: the ratio is the damage we TAKE over our own
            # health, so a BIGGER ratio must mean a SMALLER chance to win.
            p = 1.0 / (1.0 + math.exp(z))
    except Exception:
        p = 0.5
    floor = float(config.FIGHT_WIN_FLOOR)
    if p < floor:
        p = floor
    if p > 0.95:
        p = 0.95
    return p


def _fight_window(snap, enemy, dist, dmg, our_damage, ex=0, ey=0,
                  in_range=False):
    """How long the fight lasts and how much damage we take in it.

    The window is not symmetric: a MELEE champion walks and attacks in
    the same turn (GameRules), so it can hit us while it closes, while a
    RANGED champion must choose between moving and attacking - it cannot
    hit us on the turns it walks. A melee champion of ours has to spend
    turns closing before it can hit back at all.

    FUEL: reach/hp come from the per-turn enemy tuple cache (one engine
    build per turn in generate_candidates, plain-int reads here).

    F3a (the ability layer folded in):
      * OUR burst removes hits from the kill time - a damage spell is a
        one-shot that shortens the fight, so the fight is priced as the
        shorter fight it actually is.
      * THEIR burst (the enemy's ready ability) is a one-shot chunk of
        incoming damage, so the window carries it once.
    """
    our_reach = _our_reach(snap)
    handle = _enemy_handle(enemy)
    tup = _get_enemy_tuple(handle)
    if tup is not None:
        their_reach = int(tup[2])
        their_hp = float(tup[4])
        if their_hp <= 0.0:
            their_hp = float(tup[5])
    else:
        try:
            their_reach = int(enemy.attack_range())
        except Exception:
            their_reach = 1
        try:
            their_hp = float(enemy.health())
            if their_hp <= 0.0:
                their_hp = float(enemy.max_health())
        except Exception:
            their_hp = 0.0
    if their_hp <= 0.0:
        return None
    our_close = dist - our_reach
    if our_close < 0:
        our_close = 0
    their_close = dist - their_reach
    if their_close < 0:
        their_close = 0
    # F3a: our ability burst against this target.
    #
    # FUEL: the EXACT number (`ability_damage_against`) costs one engine
    # read per ready damage slot, so it is read only for the enemy we
    # are actually fighting. The other candidates (the chase targets)
    # use the RAW focus-scaled number, which is one read for the whole
    # turn and is close enough for an estimate that only discounts a
    # walk.
    our_burst = 0.0
    try:
        if in_range:
            our_burst = abilities.our_burst_against(snap, enemy, ex, ey)
        else:
            our_burst = abilities.raw_burst_within(snap, ex, ey)
    except Exception:
        our_burst = 0.0
    our_burst *= float(config.ABILITY_BURST_WEIGHT)
    # F3a: the enemy's ready burst against US (from the threat view).
    their_burst = 0.0
    try:
        view = _hostile_threat(snap)
        their_burst = float(
            view.get("burst_by_handle", {}).get(handle, 0.0) or 0.0
        )
    except Exception:
        their_burst = 0.0
    their_burst *= float(config.ABILITY_ENEMY_BURST_WEIGHT)
    effective_hp = their_hp - our_burst
    if effective_hp <= 0.0:
        # The burst alone finishes them: one turn of the fight.
        hits = 1
    else:
        hits = int(math.ceil(effective_hp / max(1.0, our_damage)))
    kill_turns = our_close + hits
    damage_turns = hits
    if their_close > 0 and their_reach <= 1:
        # a melee champion attacks on its way in (every other turn)
        damage_turns += their_close // 2
    return {
        "hits": hits,
        "kill_turns": kill_turns,
        "incoming": dmg * damage_turns + their_burst,
    }


def _threat_adjust(snap, turns, gross: float, keep_bag: bool = False) -> float:
    """The EV of an action performed while a hostile champion is on us.

    The "never mid-farm-inert" term (the owner's ruling): the score of an
    action we are PERFORMING while someone is beating on us must be
    discounted by the chance we survive it, because a champion that farms
    to its death collects nothing and loses the bag it was carrying.
    Before this term, the measured behaviour was a bot that kept picking
    "farm" for round after round while three enemies killed it
    (00elkring, our replay: 1 kill in 5 maps, 143 gems stolen).

    The form is the same expected-value swing every candidate uses:

        EV = gross x P(survive) - (1 - P(survive)) x our stake

    so a farm under a beating is worth what we actually collect (nothing
    if we die on the way) MINUS what we lose when we die (the bag and the
    respawn window). The bag we still carry is credited in the success
    branch (see _ev); the bank candidate opts out (keep_bag=False)
    because its gross already IS the discounted bag.

    B1 FUEL (the single biggest per-turn item of the whole bot): when NO
    hostile champion is on us, P(survive) is exactly 1.0, so the EV is
    exactly the gross - and the whole EV machinery (the survival call,
    the power, the stake) is dead weight. The measured cost of that dead
    weight was ~9k fuel PER CANDIDATE on 01yellow_brick_road (the farm
    generator scores ~24 candidates a turn), i.e. ~220k of the turn's
    budget spent proving that no enemy was visible. The fast path below
    is arithmetically identical (1.0 x gross - 0.0 x stake == gross).
    """
    view = _hostile_threat(snap)
    if float(view["contact_dps"]) <= 0.0 and float(view["near_dps"]) <= 0.0:
        return gross
    p = _threat_factor(snap, turns, view)
    return _ev(snap, p, gross, bag_in_success=False)


def _fight_candidates(snap, field) -> list:
    """Fight candidates (enemy in our reach) and chase candidates.

    B1 (owner ruling 2026-10-26): the fight value is the WHOLE economy
    of the kill, not the flat kill-gold score the old candidate used:

      (a) IMMEDIATE  - the bag: ALL the victim's carried gems (GameRules
          "When a champion dies": the killer takes them), 1:1 in gem
          equivalents, plus the kill gold;
      (b) DENIAL     - a dead champion cannot farm, capture or tribute
          for the whole respawn window (RESPAWN_ROUNDS). That is their
          expected production for the window, and it is the compounding
          half of the game economy;
      (c) CONTESTED ACCESS - an enemy standing on our cleared camp tiles
          resets our capture progress;
      (d) SNOWBALL   - their gold compounds into shards while they are
          dead and idle (the kill gold of (a) is the immediate part);
      (e) the kill tiebreak (gems -> gold -> kills).

    The score is an EXPECTED-VALUE SWING, not a gross:

      score = P(win) x (the kill value, discounted by the fight length)
              - (1 - P(win)) x (what our death costs us)

    The old model scored only the gain and multiplied it by a win
    probability built from our MAX health, so a champion being beaten to
    death still believed it could win, and a champion that was winning
    still scored its fight at the 0.15 floor. The probe on 00gemfight
    measured the consequence: 281 contact rounds, 278 of them answered
    with "flee", 2 with "fight", and 0 damage ever landed on an enemy.

    The win probability uses our CURRENT health and the enemy's CURRENT
    health, and the incoming damage is priced over the real fight window
    (see _fight_window).

    CHASE (the courier hunt): an enemy that is NOT in our reach but
    carries a bag is worth walking to, scored with the shared field's
    honest path distance (S2.3 honest distances: the straight-line proxy
    lies on the maze maps). An empty enemy far away is not worth the walk
    - the economy-first architecture stays. The candidate pool is capped
    (CHASE_MAX_CANDIDATES) and the targets must be VISIBLE (the fuel
    guard of the S2.3 history: fog candidates are guesses, and a guess
    that costs a field read per enemy is how the budget dies).

    F1 CLOSEOUT (2026-10-28, wired 2026-10-28 owner-side):
      - Enemy tuple cache (_ENEMY_TUPLE_CACHE, built once per turn in
        generate_candidates): the fight/chase loop and _fight_window read
        positions/bag/hp from the cache as plain ints instead of
        re-reading the engine per candidate.
      - Score-bounded EV: _BEST_NON_FIGHT_SCORE (tracked as capture/bank/
        recall/farm/boss candidates are generated) prunes fight/chase
        candidates whose gross upper bound cannot win the argmax BEFORE
        the field read and the full EV (S2.3 altar-scan pattern).
      - Chase stickiness: when a chase candidate WINS the argmax, the
        handle and score are recorded in _CHASE_COMMITMENT; on later
        turns the same target's score is re-boosted by
        CHASE_COMMIT_FACTOR until the target exits vision, dies, or the
        base score collapses below CHASE_COMMIT_MIN.
    """
    view = _hostile_threat(snap)
    enemies = view["all"]
    if not enemies:
        return []
    out = []
    global _EV_PRUNES
    our_damage = _our_damage(snap)
    if our_damage <= 0.0:
        return []
    hp = float(snap.hp)
    if hp <= 0.0:
        hp = float(snap.max_hp)
    discount = _discount()
    attack_tiles = None
    if snap.can_attack:
        try:
            attack_tiles = execution.our_attack_tiles(snap)
        except Exception:
            attack_tiles = None
    chases = []
    max_chase_turns = int(config.CHASE_MAX_TURNS)
    # F1 CLOSEOUT: the score bound for EV pruning - the best score the
    # non-combat generators have already produced this turn (capture/
    # bank/recall/farm/boss). A fight/chase whose GROSS upper bound
    # cannot beat it is dropped before the field read and the EV.
    best_other_score = _BEST_NON_FIGHT_SCORE

    for enemy, dist, dmg, pos in enemies:
        handle = _enemy_handle(enemy)
        tup = _get_enemy_tuple(handle)
        if tup is not None:
            ex = int(tup[0])
            ey = int(tup[1])
            # pos travels WITH the threat entry (the same Vec2 read the
            # threat pass already paid for); the fight candidate needs it
            # for the attack-tiles membership test, the chase candidate
            # for the field read. Never None here.
        else:
            try:
                pos = enemy.position()
                ex = int(pos.x)
                ey = int(pos.y)
            except Exception:
                continue
        in_range = False
        if attack_tiles is not None and pos is not None:
            try:
                in_range = bool(attack_tiles.is_set(pos))
            except Exception:
                in_range = False
        if not in_range:
            if dist > max_chase_turns:
                continue
        window = _fight_window(snap, enemy, dist, dmg, our_damage, ex, ey,
                               in_range)
        if window is None:
            continue
        value = _kill_value(snap, enemy, ex, ey)
        if value <= 0.0:
            continue
        if in_range:
            # Score-bound: at P(win)=1 the EV is gain + our bag (the
            # success branch credits the carried bag), so that is the
            # fight's gross upper bound; if even that cannot beat the
            # best non-fight candidate, skip the EV arithmetic.
            if value + _our_bag(snap) <= best_other_score:
                _EV_PRUNES += 1
                continue
            ratio = window["incoming"] / hp if hp > 0.0 else 9.9
            win = _fight_win(ratio)
            gain = value * (discount ** max(0, window["kill_turns"]))
            score = _ev(snap, win, gain, bag_in_success=True)
            note = (f"bag={int(value)} t={window['kill_turns']} "
                    f"r={ratio:.2f} win={win:.2f} ev={score:.1f}")
            out.append(["fight", enemy, score, note, None])
            continue
        # Not in our reach: a CHASE candidate (the courier hunt).
        if tup is not None:
            bag = int(tup[6])
        else:
            try:
                bag = int(enemy.gem_count())
            except Exception:
                bag = 0
        if bag < int(config.CHASE_MIN_BAG):
            continue
        if field is None:
            continue
        # Score-bound BEFORE the field read (the expensive part of a
        # chase candidate): at p=CHASE_SUCCESS_FACTOR the best possible
        # EV is p*(gain + our bag), so that is the chase's gross upper
        # bound; if it cannot beat the best non-fight candidate, the
        # field read is never paid.
        upper = (value + _our_bag(snap)) * (discount ** max(0, dist)) * float(
            config.CHASE_SUCCESS_FACTOR
        )
        if upper <= best_other_score:
            _EV_PRUNES += 1
            continue
        try:
            if pos is None:
                pos = enemy.position()
            d = field.get(pos)
        except Exception:
            continue
        if d is None:
            continue
        d = int(d)
        if d >= fielding.UNREACHABLE or d > int(config.CHASE_MAX_TURNS):
            continue
        total = d + window["kill_turns"]
        ratio = window["incoming"] / hp if hp > 0.0 else 9.9
        win = _fight_win(ratio)
        gain = value * (discount ** max(0, total))
        p = win * float(config.CHASE_SUCCESS_FACTOR)
        score = _ev(snap, p, gain, bag_in_success=True,
                    stake_share=float(config.CHASE_STAKE_SHARE))
        chases.append((score, enemy, pos,
                       f"bag={bag} d={d} t={total} win={win:.2f} ev={score:.1f}"))
    if chases:
        chases.sort(key=lambda item: -item[0])
        cap = int(config.CHASE_MAX_CANDIDATES)
        for score, enemy, pos, note in chases[:cap]:
            out.append(["chase", enemy, score, note, (pos,)])
    return out


def _kill_value(snap, enemy, ex, ey) -> float:
    """The full value of a kill, in gem-equivalents.

    The bag counts 1:1 (a carried gem IS a gem-equivalent), the kill
    gold is priced at the standard gold rate, and the denial term is the
    victim's own production for the respawn window (see config for the
    measurement behind every constant).
    FUEL: the bag comes from the per-turn enemy tuple cache when present.
    """
    value = 0.0
    bag = None
    try:
        tup = _get_enemy_tuple(_enemy_handle(enemy))
        if tup is not None:
            bag = int(tup[6])
    except Exception:
        bag = None
    if bag is None:
        try:
            bag = int(enemy.gem_count())
        except Exception:
            bag = 0
    value += float(bag)
    # The kill gold is the ENGINE's value (cambc.constants.KILL_GOLD = 100
    # on engine 2.0.0), resolved by config.resolve(). The old hardcoded 50
    # was the pre-update rule value and under-priced every kill by half.
    value += float(config.KILL_GOLD_ON_DEATH) * float(config.FIGHT_GOLD_VALUE)
    value += float(config.FIGHT_DENIAL_VALUE) * _denial_scale(snap)
    value += _capture_reset_value(snap, ex, ey)
    value += float(config.FIGHT_TIEBREAK_VALUE)
    return value


def _denial_scale(snap) -> float:
    """How much of the respawn window is still worth denying.

    A kill in the last rounds of the match denies nothing: the enemy
    would be back before the match ends. This keeps the term honest at
    the end of the match instead of paying a full window for a corpse
    that respawns in time.
    """
    try:
        now = int(getattr(snap, "round_number", 0) or 0)
    except Exception:
        now = 0
    end = int(config.FIGHT_DENIAL_END_ROUNDS)
    if end <= 0 or now <= 0:
        return 1.0
    remaining = end - now
    if remaining <= 0:
        return 0.0
    window = int(config.RESPAWN_ROUNDS) or 60
    scale = float(remaining) / float(window)
    if scale > 1.0:
        scale = 1.0
    return scale


def _capture_reset_value(snap, ex, ey) -> float:
    """The value of removing an enemy that stands on our capture tiles.

    GameRules capture: admitting an enemy into the capture region resets
    the five-round capture timer, so an enemy on the tiles of a group we
    have already cleared destroys the farm cycle we paid for.
    """
    groups = getattr(snap, "cleared_groups", None)
    if not groups:
        return 0.0
    tiles = getattr(snap, "group_tile_xy", None) or {}
    radius = int(config.FIGHT_CAPTURE_RADIUS)
    for entry in groups:
        try:
            key = entry[0]
        except Exception:
            continue
        for tile in tiles.get(key, ()) or ():
            try:
                dx = int(tile[0]) - ex
                dy = int(tile[1]) - ey
            except Exception:
                continue
            if dx < 0:
                dx = -dx
            if dy < 0:
                dy = -dy
            if (dx if dx > dy else dy) <= radius:
                return float(config.FIGHT_CAPTURE_RESET_VALUE)
    return 0.0


def _flee_candidates(snap) -> list:
    """Flee candidate: break contact with a champion that is hitting us.

    B1: the score is an EXPECTED-VALUE SWING, like every other candidate:

      score = P(escape) x (the ground we save + the bag we keep)
              - (1 - P(escape)) x (what our death costs us)

    The first B1 cut scored the escape as (base + carried bag) x hp factor,
    which made it win EVERY contact round (the probe: 278 escapes against
    2 fights on 00gemfight) - the bot ran from fights it was winning and
    stood still while a ranged enemy chipped it to death.

    The bag is priced in the SUCCESS branch (an escape is a way of KEEPING
    the bag, so a loaded champion escapes for a real reason) and the death
    branch carries only the respawn cost.

    The escape probability depends on the enemy's reach: a RANGED champion
    must choose between moving and attacking (GameRules), so a champion
    that keeps walking breaks contact; a MELEE champion walks and attacks
    in the same turn and cannot be shaken off.
    """
    view = _hostile_threat(snap)
    contact = view["contact"]
    if not contact:
        return []
    # F3a: a ready DASH breaks contact in one turn where a walk only
    # buys one tile, so the escape is materially more likely.
    dash = False
    try:
        dash = abilities.dash_ready(snap)
    except Exception:
        dash = False
    best = None
    for enemy, dist, dmg, pos in contact:
        try:
            reach = int(enemy.attack_range())
        except Exception:
            reach = 1
        if reach > 1:
            escape = float(config.FLEE_ESCAPE_RANGED)
        else:
            escape = float(config.FLEE_ESCAPE_MELEE)
        # A melee champion that is already adjacent cannot be shaken: it
        # walks and attacks in the same turn.
        if reach <= 1 and dist <= 1:
            escape *= 0.5
        if dash:
            escape += float(config.ABILITY_DASH_ESCAPE_BONUS)
            if escape > 0.95:
                escape = 0.95
        score = _ev(snap, escape, float(config.FLEE_BASE_VALUE),
                    bag_in_success=True)
        if best is None or score > best[0]:
            best = (score, enemy)
    if best is None or best[0] <= 0.0:
        return []
    return [["flee", best[1], best[0], f"ev={best[0]:.1f}", None]]


# ---------------------------------------------------------------------------
# S2.4 - shopping utility (the shard / lock / hold mini-argmax).
# ---------------------------------------------------------------------------
# The shop is legal in exactly two states (GameRules "Shards and emblems"):
# on a spawn tile, and WHILE DEAD (a dead champion keeps its shopping
# turns for the whole 60-round respawn window - the death window is a
# FREE gold-to-stats conversion, and the old Gate 1 skipped it).
#
# The decision is scored in the same gem-equivalents as every other
# candidate:
#   shard = EMBLEM_COMPLETION_VALUE (when the purchase completes the
#           emblem) + count * SHARD_STAT_VALUE (the permanent stats)
#   lock  = the denial value of an altar lock (S2.4.3, added there)
#   hold  = CONSUMABLE_RESERVE_VALUE - the gold kept for the consumable
#           reserve (a lock / a teleport crystal). Gold held earns nothing.
# The gold gate does the real work: a shard is bought when the gold covers
# the shard AND the reserve (30 + 20), so the reserve survives the shop.
# ---------------------------------------------------------------------------


def can_shop(snap) -> bool:
    """True when this champion may shop this turn (on spawn OR dead).

    GameRules:361 - dead champions retain their shopping turns. The alive
    case is a pure-Python test against the cached spawn coordinates (no
    engine read): the snapshot caches them as plain ints in a set, so the
    test is one lookup (S2.4 fuel contract).
    """
    if not snap.is_alive:
        return True
    px, py = snap.origin_xy
    spawn_set = getattr(snap, "spawn_xy_set", None)
    if spawn_set:
        return (px, py) in spawn_set
    for entry in getattr(snap, "spawn_coords", None) or ():
        try:
            if int(entry[0]) == px and int(entry[1]) == py:
                return True
        except Exception:
            continue
    return False


def _shard_progress(snap, item) -> int:
    """How many shards of this item we already carry (0..4).

    ``me.item_count()`` is one engine read (the carried counts indexed by
    ItemType). The engine combines five matching shards into the emblem
    (Greed: 25 - see config.EMBLEM_SHARD_COUNT), so the shard count is the
    progress toward the next emblem. It is only read on a shop turn.
    """
    try:
        counts = snap.me.item_count()
    except Exception:
        return 0
    try:
        return int(counts[int(item)])
    except Exception:
        return 0


def shop_candidates(snap) -> list[Candidate]:
    """The shopping mini-argmax: family shard vs altar lock vs hold.

    Returns the candidates; the argmax decides. The HOLD candidate is
    always present (gold held earns nothing, but the consumable reserve is
    worth keeping), so the shop decision is a real comparison and not a
    hardcoded gate.

    S2.4.3 - the ALTAR LOCK is the third option. It is a DENIAL
    consumable (10 gold): it removes an altar from the enemy's banking
    options, but only when the intel says the enemy is actually using
    that altar (lock_traffic). The shard keeps priority: the lock is only
    offered when the shard is NOT affordable, so a permanent stat is never
    traded for a situational denial.
    """
    out: list[Candidate] = []
    if not can_shop(snap):
        return [["hold", None, float(config.CONSUMABLE_RESERVE_VALUE), "noshop", None]]
    short = observation.champion_short_for(snap)
    item = config.power_shard_for(short)
    try:
        cost = int(item.cost())
    except Exception:
        cost = 30
    gold = int(snap.gold)
    reserve = int(config.CONSUMABLE_RESERVE_GOLD)
    affordable = 0
    if cost > 0:
        affordable = (gold - reserve) // cost
    if affordable < 1:
        # The shard is not affordable: the LOCK is the alternative to
        # holding the gold (it beats HOLD whenever the intel shows enemy
        # traffic at an altar).
        lock = lock_candidate(snap)
        if lock is not None:
            out.append(lock)
        out.append(["hold", None, float(config.CONSUMABLE_RESERVE_VALUE), "hold", None])
        return out
    owned = _shard_progress(snap, item)
    needed = max(1, int(config.EMBLEM_SHARD_COUNT) - owned)
    count = int(affordable)
    score = float(count) * float(config.SHARD_STAT_VALUE)
    note = f"g={gold} n={count} own={owned}"
    if count >= needed:
        # The purchase completes an emblem: the family passive switches on
        # for the rest of the match.
        score += float(config.EMBLEM_COMPLETION_VALUE)
        note += " emblem"
    out.append(["shop", count, score, note, None])
    # HOLD is scored second: on a tie the shard wins (it is the permanent
    # stat; the reserve is already priced into the gold gate).
    out.append(["hold", None, float(config.CONSUMABLE_RESERVE_VALUE), "hold", None])
    return out


# ---------------------------------------------------------------------------
# S2.4.3 - the altar lock (the denial consumable).
# ---------------------------------------------------------------------------
# An altar lock costs 10 gold, is carried ONE at a time and is placed on
# an altar within two tiles of the champion (GameRules "Altar locks"). It
# blocks the OPPOSING team's tributes and cancels an existing enemy
# channel, while the OWNER team can still pay tribute through it
# (GameRules :237) - which is why the lock must never be scored as "our
# altar is blocked" (that was the team-agnostic `has_altar_lock` bug).
# ---------------------------------------------------------------------------


def _traffic_present(snap) -> bool:
    """Cheap pre-check: is there ANY enemy-traffic intel?

    Two cached facts, no engine read and no per-altar loop: a contested
    altar (own vision / an ally's fresh beacon flag) or a fresh sighting
    in any region (broadcast intel).
    """
    if getattr(snap, "contested_altars", None):
        return True
    density = getattr(snap, "region_sightings", None) or {}
    for _region, count in density.items():
        if count:
            return True
    return False


def lock_traffic(snap) -> dict:
    """Per-altar ENEMY TRAFFIC score: {(x, y): score}.

    The prior that makes a lock worth its 10 gold:
      DIRECT - the altar is contested (own vision or an ally's fresh
               beacon flag): the enemy is AT the altar.
      REGION - a fresh sighting in the altar's region: the enemy is in
               the neighbourhood.
      FOG    - our own last-known enemy position is near the altar.

    All inputs are cached facts (plain ints); no engine read. FUEL: this
    is the full map (every altar x every remembered enemy), so it is only
    used on shop turns - the per-turn path is lock_targets (the altars in
    reach only).
    """
    coords = getattr(snap, "altar_coords", None) or ()
    if not coords:
        return {}
    contested = getattr(snap, "contested_altars", frozenset()) or frozenset()
    density = getattr(snap, "region_sightings", {}) or {}
    regions = getattr(snap, "altar_regions", None) or {}
    fog = getattr(snap, "fog_enemies", None) or ()
    fog_radius = int(config.LOCK_FOG_RADIUS)
    fog_w = float(config.LOCK_TRAFFIC_FOG)
    direct_w = float(config.LOCK_TRAFFIC_DIRECT)
    region_w = float(config.LOCK_TRAFFIC_REGION)
    out: dict = {}
    for entry in coords:
        ax = int(entry[0])
        ay = int(entry[1])
        key = (ax, ay)
        score = 0.0
        if key in contested:
            score += direct_w
        region = -1
        try:
            region = int(regions.get(key, -1))
        except Exception:
            region = -1
        if region >= 0:
            try:
                if int(density.get(region, 0)) > 0:
                    score += region_w
            except Exception:
                pass
        if fog and score < direct_w:
            for record in fog:
                dx = record[0] - ax
                if dx < 0:
                    dx = -dx
                dy = record[1] - ay
                if dy < 0:
                    dy = -dy
                if (dx if dx > dy else dy) <= fog_radius:
                    score += fog_w
                    break
        if score > 0.0:
            out[key] = score
    return out


def lock_targets(snap) -> list:
    """The traffic altars within the placement radius, nearest first.

    Only altars within the placement radius (two tiles) are candidates -
    the lock must be placed from next to the altar. The nearest ones come
    first (the tightest reach = the least walking).

    FUEL: this is the PER-TURN path. It exits on the cheap pre-check (no
    traffic intel at all -> nothing to lock) and only walks the altars
    that are in reach.
    """
    if not _traffic_present(snap):
        return []
    coords = getattr(snap, "altar_coords", None) or ()
    if not coords:
        return []
    px, py = snap.origin_xy
    radius = int(config.ALTAR_LOCK_RADIUS)
    contested = getattr(snap, "contested_altars", None) or frozenset()
    density = getattr(snap, "region_sightings", None) or {}
    regions = getattr(snap, "altar_regions", None) or {}
    fog = getattr(snap, "fog_enemies", None) or ()
    fog_radius = int(config.LOCK_FOG_RADIUS)
    fog_w = float(config.LOCK_TRAFFIC_FOG)
    direct_w = float(config.LOCK_TRAFFIC_DIRECT)
    region_w = float(config.LOCK_TRAFFIC_REGION)
    out = []
    for entry in coords:
        ax = int(entry[0])
        ay = int(entry[1])
        dx = ax - px
        if dx < 0:
            dx = -dx
        dy = ay - py
        if dy < 0:
            dy = -dy
        d = dx if dx > dy else dy
        if d > radius:
            continue
        key = (ax, ay)
        score = 0.0
        if key in contested:
            score += direct_w
        region = -1
        try:
            region = int(regions.get(key, -1))
        except Exception:
            region = -1
        if region >= 0:
            try:
                if int(density.get(region, 0)) > 0:
                    score += region_w
            except Exception:
                pass
        if fog and score < direct_w:
            for record in fog:
                ex = record[0] - ax
                if ex < 0:
                    ex = -ex
                ey = record[1] - ay
                if ey < 0:
                    ey = -ey
                if (ex if ex > ey else ey) <= fog_radius:
                    score += fog_w
                    break
        if score > 0.0:
            out.append((d, ax, ay))
    out.sort()
    return [(ax, ay) for _d, ax, ay in out]


def lock_target(snap) -> tuple | None:
    """The altar to lock: the traffic altar NEAREST to us (or None)."""
    targets = lock_targets(snap)
    if not targets:
        return None
    return targets[0]


def lock_candidate(snap) -> Candidate | None:
    """The scored LOCK purchase candidate, or None.

    Bought only when:
      - the shop is legal (the caller checks),
      - the gold covers the lock AND the consumable reserve,
      - we carry NO lock (the engine's carry limit is one),
      - the intel shows enemy traffic at some altar.
    The score is the denial value (LOCK_DENIAL_VALUE) - above the hold
    value, so the lock beats holding the gold.
    """
    gold = int(snap.gold)
    cost = int(config.ALTAR_LOCK_COST)
    reserve = int(config.CONSUMABLE_RESERVE_GOLD)
    if gold < cost + reserve:
        return None
    if not _traffic_present(snap):
        return None
    try:
        slots = snap.me.consumables()
        carried = int(slots[int(cambc.ConsumableType.ALTAR_LOCK)].count())
    except Exception:
        return None
    if carried > 0:
        return None
    target = None
    best = 0.0
    for coord, score in lock_traffic(snap).items():
        if score > best:
            best = score
            target = coord
    if target is None:
        return None
    return ["lock", target, float(config.LOCK_DENIAL_VALUE),
            f"g={gold} traffic={best:.1f}", None]


def shop_candidate(snap) -> Candidate | None:
    """The winning shopping candidate for this turn (None when no shop)."""
    try:
        cands = shop_candidates(snap)
    except Exception as error:
        _report("shop candidates", error)
        return None
    if not cands:
        return None
    return best(cands)


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
        # B1: the threat term (a boss clear under attack ends when we do).
        score = _threat_adjust(snap, total_turns, score)
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
    # B1: the clear is priced against our CURRENT health, not our maximum.
    # The old model used max_hp, so a champion that was already beaten to
    # 30% health still believed it could walk into a camp that would kill
    # it - the measured symptom was 68 of our 73 deaths on 00elkring and
    # 13 of 22 on 00gemfight being CAMP deaths, each one losing the bag
    # the champion was carrying (22 gems lost to deaths in one match).
    try:
        cur_hp = float(snap.hp)
    except Exception:
        cur_hp = max_hp
    if cur_hp <= 0.0:
        cur_hp = max_hp
    # F3a: the ability burst shortens the clear. A damage spell lands
    # ONCE per clear (an 8-round cooldown), so it removes one burst's
    # worth of HP from the arithmetic - and that changes the "too strong
    # to engage" verdicts the basic-attack-only estimate got wrong.
    try:
        camp_burst = abilities.camp_burst(snap) * float(
            config.ABILITY_CAMP_BURST_WEIGHT
        )
    except Exception:
        camp_burst = 0.0
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
            effective = hp - camp_burst
            if effective <= 0.0:
                hp_turns = 1
            else:
                hp_turns = int(math.ceil(effective / team_dps))
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
                if cur_hp <= 0.0:
                    survive = _T_DEATH_BASE
                else:
                    survive = _T_DEATH_BASE ** (incoming / cur_hp)
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
    # B1 FUEL: the pool rows are the KILLABLE groups only - the filter is
    # a static property of the map (observation.farm_kill_rows), so the
    # per-turn loop does not test it per row any more (one set lookup per
    # row was ~430 fuel x every row of the map, every turn).
    rows = getattr(snap, "farm_kill_rows", None)
    if rows is None:
        rows = getattr(snap, "farm_rows", None) or ()
        killable = getattr(snap, "killable_keys", None) or frozenset()
        if killable:
            rows = tuple(r for r in rows if r[0] in killable)
    if not rows:
        return out
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
        dx = abs(row[3] - px)
        dy = abs(row[4] - py)
        cheap = dx if dx > dy else dy
        if row[0] in visible_keys:
            visible.append((cheap, row))
        else:
            zone.append((cheap, row))
    visible.sort()
    zone.sort()

    # S2.4: the SCOUTING INTEREST - the nearest UNSEEN camp groups (the
    # zones we have not seen yet). Plain ints, no engine read: the ward
    # mini-argmax reuses this scan instead of walking the group table a
    # second time (the fuel contract - in this wasm build one statement
    # in a loop costs ~700 fuel). The stash happens only on the turns
    # where the ward can actually be placed (snap.want_ward).
    if getattr(snap, "want_ward", False):
        try:
            snap.ward_interest = tuple(
                (row[3], row[4], float(config.WARD_CAMP_WEIGHT))
                for _cheap, row in zone[:3]
            )
        except Exception:
            snap.ward_interest = ()

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
            # B1: the threat term - farming while a hostile champion is on
            # us is worth what we collect, and we collect nothing if we
            # are killed on the way to the camp.
            score = _threat_adjust(snap, turns, score)
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


def _apply_path_exposure(cands, snap, field) -> dict:
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

    F3b: the same walk also prices the TRANSIT - the corridor between
    here and the goal - against the DYNAMIC danger zones (the awake camps
    and the death corridors). The destination exposure covers the tiles
    next to the goal; the transit term covers the WHOLE walk, so the
    route AROUND an aggro'd zone wins the argmax instead of the route
    through it. The transit term prices the ROUTING kinds only (farm,
    capture): the walk home has no alternative route, so pricing it
    could only stop the bot from banking (measured - see the note
    below). The return value maps the candidate index to its
    (total, corridor) transit damage - the corridor part is what the
    regroup decision reads (a danger we walk INTO is a corridor; the
    danger at the destination is the destination's own business).
    """
    transit_damage: dict = {}
    if not cands or field is None:
        return transit_damage
    threat_list = fielding.threats(snap)
    bot = getattr(snap, "bot", None)
    if bot is None:
        return transit_damage
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
        if damage > 0.0:
            survive = _survival(damage, float(snap.max_hp))
            cand[2] = float(cand[2]) * survive
            cand[3] = f"{cand[3]} exp={int(damage)}"
    # F3b: the TRANSIT term (the walk, priced against the MEMORY danger
    # zones - the camps we woke and lost sight of, and the corridor
    # where we died). It runs on the same probed set, and it prices the
    # WHOLE walk: the danger that matters for a corridor is the danger
    # at OUR end of it, which the near-goal exposure prefix does not
    # see at all.
    #
    # The two sets are DISJOINT: the awake camps the vision can still
    # see are priced by the exposure term above, and the transit term
    # prices only the ones the vision CANNOT see. So no damage is
    # counted twice.
    #
    # ONLY THE ROUTING KINDS. The transit term exists to price WHICH
    # CAMP we walk to - it is the price of a ROUTE, and only a route has
    # an alternative. The walk HOME (the bank) has no alternative: there
    # is exactly one way to the altar, so pricing it cannot reroute
    # anything - it can only make the bot carry its gems forever.
    #
    # That was measured, not assumed (the 00gemfight replay of the F3b
    # ten-map A/B): the altar of 00gemfight sits next to a camp, the
    # walk home crossed that camp's zone, the bank candidate lost half
    # its score every turn (2.6 against 4.8), the farming candidates
    # (4.5-5.5) won the argmax instead, and the team collected 300
    # gems it never banked: 124 submitted against the F3a baseline's
    # 1,026. The bank keeps its F3a price (the carried-gem risk term).
    try:
        zone_list = transit.zones(snap)
    except Exception:
        zone_list = []
    if zone_list:
        version = transit.zone_version(snap)
        # The FUEL PROBE: one `fuel_remaining()` read before the pricing
        # and one after, so the campaign can report what the transit
        # term actually costs (the engine charges the bot WASM fuel per
        # turn, and a turn that runs out is a "fuelExhausted" event -
        # the f1-retest gate counts those).
        try:
            _f0 = int(cambc.fuel_remaining())
        except Exception:
            _f0 = None
        for idx in selected:
            cand = cands[idx]
            kind = cand[0]
            if kind not in ("farm", "capture"):
                continue
            goal = cand[4] if len(cand) > 4 else None
            if goal is None:
                continue
            total, corridor = fielding.transit_damage(
                bot, snap, "scoring", goal, zone_list, version,
            )
            if total <= 0.0:
                continue
            weight = float(config.TRANSIT_DAMAGE_WEIGHT)
            survive = _survival(total * weight, float(snap.max_hp))
            floor = float(config.TRANSIT_SURVIVAL_FLOOR)
            if survive < floor:
                survive = floor
            cand[2] = float(cand[2]) * survive
            cand[3] = f"{cand[3]} transit={int(total)}"
            transit_damage[idx] = (float(total), float(corridor))
            transit.count_priced()
        if _f0 is not None:
            try:
                transit._FUEL["price"] += _f0 - int(cambc.fuel_remaining())
            except Exception:
                pass
    return transit_damage


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def _regroup_candidate(cands, snap, transit_damage) -> None:
    """The REGROUP hold (F3b): wait for the company.

    The respawn -> walk-back-alone -> die loop is a GROUP problem: a
    champion that died in a corridor walks back through the SAME corridor
    alone and dies again (the evidence match: 18 corridor deaths, the same
    champion, the same corridor, every ~70 rounds). The transit term
    already reroutes the champion around a priced corridor; this is the
    case where EVERY route we would take crosses one.

    The hold is a scored candidate, so the argmax picks it: it outscores
    the action it defers (REGROUP_HOLD_BONUS) and it expires
    (REGROUP_MAX_ROUNDS), so a permanent danger cannot freeze the bot.

    Conditions (all of them):
      * the action we would take is a TRANSIT action (farm / capture) -
        we never defer a fight, a flee, a bank or a recall;
      * its route crosses a CORRIDOR worth more than
        REGROUP_DAMAGE_FRACTION of our health pool (the danger at the
        destination and the danger we stand in are not corridors);
      * no hostile champion is in contact (we do not stand still with an
        enemy on us);
      * no ally is within REGROUP_ALLY_TILES - with company we cross as
        a group instead of waiting;
      * we carry NO gems - a champion with a bag has a strictly better
        action than waiting (walk home and bank it), so the hold is
        reserved for the empty-handed champion of the loop.

    The hold streak is PER CHAMPION. The bot instance (and the module
    globals) are shared by the whole team, so a module-level counter
    counted the turns of ALL our champions: one champion's hold
    shortened the expiry for the others. The streak lives on the bot
    instance, keyed by the champion id.
    """
    bot = getattr(snap, "bot", None)
    if bot is None:
        return
    try:
        champ = int(snap.me.champion_name())
    except Exception:
        champ = -1
    state = getattr(bot, "regroup_streak", None)
    if not isinstance(state, dict):
        state = {}
        try:
            bot.regroup_streak = state
        except Exception:
            pass
    streak = int(state.get(champ, 0))

    def _clear():
        state[champ] = 0

    if not cands or not transit_damage:
        _clear()
        return
    try:
        if int(getattr(snap, "gems", 0)) > 0:
            # A bag in hand beats a wait: bank it.
            _clear()
            return
    except Exception:
        pass
    # The action the bot would otherwise take: the hold may only
    # preempt THAT action (a hold that beat a better action would waste
    # the turn on a fear instead of on gems). The shop candidates are
    # skipped: bot.py removes them from the argmax set before it picks
    # (a purchase is a free parallel action, not a move).
    best_idx = -1
    for index, cand in enumerate(cands):
        if cand[0] in ("shop", "lock"):
            continue
        try:
            score = float(cand[2])
        except Exception:
            continue
        if best_idx < 0 or score > float(cands[best_idx][2]):
            best_idx = index
    if best_idx < 0:
        _clear()
        return
    if cands[best_idx][0] not in ("farm", "capture"):
        # A bank, a recall or a fight is never deferred.
        _clear()
        return
    facts = transit_damage.get(best_idx) or (0.0, 0.0)
    try:
        damage = float(facts[1])
    except Exception:
        damage = 0.0
    threshold = float(config.REGROUP_DAMAGE_FRACTION) * float(snap.max_hp)
    if damage < threshold:
        _clear()
        return
    if streak >= int(config.REGROUP_MAX_ROUNDS):
        # The hold expires: the bot goes anyway. No deadlock. The
        # negative streak is the cooldown after an expiry - the hold is
        # not a metronome.
        state[champ] = -int(config.REGROUP_MAX_ROUNDS)
        return
    if streak < 0:
        # The cooldown after an expiry: the bot may not hold again yet.
        state[champ] = streak + 1
        return
    try:
        view = _hostile_threat(snap)
        if view.get("contact"):
            # We do not stand still with an enemy on us.
            _clear()
            return
    except Exception:
        pass
    try:
        if transit.ally_close(snap, int(config.REGROUP_ALLY_TILES)):
            # We have company: crossing as a group beats waiting.
            _clear()
            return
    except Exception:
        pass
    score = float(cands[best_idx][2]) * float(config.REGROUP_HOLD_BONUS)
    cands.append([
        "regroup", None, score,
        f"regroup wait d={int(damage)}", None,
    ])
    state[champ] = streak + 1
    transit.count_hold()


def generate_candidates(snap) -> list[Candidate]:
    """Generate every candidate action for this turn.

    One shared DistanceField per turn (managers.fielding) feeds every
    candidate's distance. Defensive: each subgenerator is wrapped so a
    failure in one kind does not break the others.
    """
    # F1 CLOSEOUT: the enemy tuple cache is filled inside
    # _hostile_threat's per-turn pass (no separate build pass); the
    # fight generator's first _hostile_threat call primes it.
    global _BEST_NON_FIGHT_SCORE
    _BEST_NON_FIGHT_SCORE = 0.0
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
        # B1: the FIGHT candidate comes BEFORE the flee candidate, so a
        # tie is broken towards trading blows. The first B1 cut ordered
        # them the other way and the escape won every contact round
        # (the probe: 278 escapes vs 2 fights on 00gemfight) - a fight
        # we are winning is worth more than the ground we would give up.
        ("fight", lambda: _fight_candidates(snap, field)),
        ("flee", lambda: _flee_candidates(snap)),
    ):
        try:
            candidates = fn()
            out.extend(candidates)
            # F1 CLOSEOUT: track best non-fight score for EV pruning
            for c in candidates:
                if c[0] not in ("fight", "chase", "flee"):
                    score = float(c[2])
                    if score > _BEST_NON_FIGHT_SCORE:
                        _BEST_NON_FIGHT_SCORE = score
        except Exception as error:
            _report("generator", error)

    # The farm pool is always scored: the S2.2 short-circuit (skip farm
    # when a higher category exists) assumed the categories were ordered by
    # value, which the S2.3 scoring no longer guarantees - a weak fight
    # candidate must not hide a much better farm target.
    try:
        out.extend(_farm_candidates(snap, field, clear_map))
        for c in out:
            if c[0] not in ("fight", "chase", "flee"):
                score = float(c[2])
                if score > _BEST_NON_FIGHT_SCORE:
                    _BEST_NON_FIGHT_SCORE = score
    except Exception as error:
        _report("farm generator", error)

    # Boss graduation: scored against the best candidate the other kinds
    # produced, so a hopeless boss never builds its own field.
    try:
        best_other = max([float(c[2]) for c in out], default=0.0)
        out.extend(_boss_candidates(snap, field, best_other, clear_map))
    except Exception as error:
        _report("boss generator", error)

    # F1 CLOSEOUT: Chase stickiness - if already chasing a target, boost its score
    # using CHASE_COMMIT_FACTOR until target dies, exits vision, or score collapses.
    # Record: when a chase candidate wins this turn's argmax, the handle and its
    # boosted score are written to _CHASE_COMMITMENT (bot.py calls
    # utility.record_chase_win after the argmax); on later turns the same
    # target's score is re-boosted here so a one-tile score dip cannot
    # drop the pursuit (the documented oscillator class).
    try:
        global _CHASE_COMMITMENT
        if _CHASE_COMMITMENT:
            # Check if the committed target is still valid (visible, alive)
            visible_handles = set()
            try:
                for e in getattr(snap, "visible_enemies", ()) or ():
                    visible_handles.add(_enemy_handle(e))
            except Exception:
                pass
            for handle in list(_CHASE_COMMITMENT.keys()):
                if handle not in visible_handles:
                    del _CHASE_COMMITMENT[handle]
                    continue
                committed_handle, boosted_score = _CHASE_COMMITMENT[handle]
                # Find the chase candidate for this target and boost it.
                # Candidates carry the enemy object; _enemy_handle(enemy) is
                # the engine handle, the same key the commitment is stored
                # under.
                for c in out:
                    if c[0] != "chase":
                        continue
                    try:
                        if _enemy_handle(c[1]) != committed_handle:
                            continue
                    except Exception:
                        continue
                    # The commitment holds only while the base chase is still
                    # worth something: a collapsed base score ends it.
                    factor = float(getattr(config, "CHASE_COMMIT_FACTOR", 2.0))
                    c[2] = max(float(c[2]), float(c[2]) * factor)
                    if float(c[2]) < float(getattr(config, "CHASE_COMMIT_MIN", 0.5)):
                        del _CHASE_COMMITMENT[handle]
                        continue
                    global _CHASE_BOOSTS
                    _CHASE_BOOSTS += 1
                    _CHASE_COMMITMENT[handle] = (committed_handle, float(c[2]))
                    c[3] = c[3] + " COMMIT"
                    break
    except Exception:
        pass

    # S2.4: the shopping decision (family shard vs lock vs hold) joins the
    # scored candidate set. Buying is a FREE parallel action - it costs no
    # move/attack allowance - so bot.py executes the purchase and then
    # removes the shop candidate from the argmax set: a purchase must not
    # shadow the turn's movement / fight decision.
    try:
        shop = shop_candidate(snap)
        if shop is not None and shop[0] in ("shop", "lock"):
            out.append(shop)
    except Exception as error:
        _report("shop generator", error)

    # Threat term: fold the path exposure into the top candidates.
    try:
        transit_damage = _apply_path_exposure(out, snap, field)
    except Exception as error:
        _report("path exposure", error)
        transit_damage = {}

    # F3b: the regroup hold - an action that crosses a lethal corridor
    # while we are alone is deferred (the bot waits for the company).
    try:
        _regroup_candidate(out, snap, transit_damage)
    except Exception as error:
        _report("regroup", error)

    # A candidate worth nothing never wins the argmax (unreachable targets,
    # hopeless fights): drop the non-positive scores.
    pos = [c for c in out if float(c[2]) > 0.0]
    if pos:
        return pos
    # B1: EVERY candidate is a net loss (the EV swing priced our death
    # higher than the prize). The candidate set must not become EMPTY:
    # a champion that stands still collects nothing and dies to whatever
    # comes, so the least-bad action still beats doing nothing. The
    # measured symptom (01yellow_brick_road): a champion with two gems
    # in the bag had NO candidate at all for 1,700 rounds - the farm EV
    # is negative whenever we carry a bag (the stake is the bag plus the
    # death cost), so every farm was dropped and the champion froze.
    if not out:
        return []
    return [max(out, key=lambda c: float(c[2]))]


def best(candidates: list[Candidate]) -> Candidate | None:
    """Argmax. Ties broken by input order."""
    if not candidates:
        return None
    best_c = candidates[0]
    for c in candidates[1:]:
        if c[2] > best_c[2]:
            best_c = c
    return best_c

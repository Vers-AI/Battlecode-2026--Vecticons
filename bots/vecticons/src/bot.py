"""Per-champion decisions. main.py dispatches here after the draft.

Stage 3 utility layer (architecture.md §Stage 3).
==================================================

Per the spec, the utility layer replaces hardcoded gates with candidate
generation + argmax scoring. The critical "Monolith trap" constraint
(KILLABLE_HP_CAP + Monolith exclusion runs BEFORE scoring) is preserved
via the shared execution.filter in utility.generate_candidates.

What this implementation does:
- Battle plan candidates: capture (stand on a cleared spawn tile),
  bank (submit gems at the chosen altar), recall (low HP), fight (enemy
  in attack range), farm (killable camp groups).
- Scoring per architecture.md:
    farm_camp(c) = gems(c) * TIME_DISCOUNT ** (reach + clear)
    bank(a)      = gem_count * BANK_UTILITY_PER_GEM * TIME_DISCOUNT ** d
    recall       = RECALL_UTILITY  (only when hp_frac < RECALL_HP_FRAC)
    fight(e)     = KILL_REWARD_GOLD * TIME_DISCOUNT ** turns_to_kill
    capture(c)   = RECALL_UTILITY + 1000  (always dominant when available)
- Argmax wins. Ties fall back to baseline gate order via the dispatch
  sequence (capture > bank > recall > fight > farm).
- A match-time safety override forces recall when HP < SAFETY_HP_FRAC.

The Stage 2 reference is frozen at bots/vecticons/src/bot_baseline.py for
the A/B comparison required by Stage 3 done-when.

Shared-memory channel layout (S2.1; encoded/decoded in
managers/intelligence.py — all instances agree by shared source):

    byte i (owned by champion i, i = index in the ally draft order)
      bits 0-3  region id  - 4x4 grid over the map (position quantized)
      bit  4    altar-contested flag (enemy within contest radius of an
                 altar)
      bits 5-7  freshness  - 0 = no fresh sighting (zero byte = nothing);
                 1-7 = age bucket of ~8 rounds (<= ~55-round horizon)

    Discipline: write ONLY your own byte, every play turn; read all.
    Champion kind is NOT carried (region quantization); a dead writer's
    byte freezes until it respawns.

    S2.2 consumers (facts folded by managers/intelligence.fold_ally_intel;
    scoring in managers/utility): the altar-contested flag scores contested
    bank candidates down (BANK_CONTEST_PENALTY); broadcast sighting density
    per region multiplies bank/farm scores (REGION_RISK_FACTOR ** count).
"""
from __future__ import annotations

import cambc
from cambc import game_map

import config
from managers import execution
from managers import intelligence
from managers import observation
from managers import utility

Team, Item = cambc.Team, cambc.ItemType


class Bot:
    def __init__(self):
        self.chosen_stat = None
        self.map_cache = None
        # Enemy fog memory (intelligence manager): handle -> last-known
        # record. Plain instance RAM; persists across turns.
        self.enemy_memory = {}
        # S2.1 signal channel: our owned byte index (ally draft order)
        # and the last byte value we wrote (change detection for logs).
        self.channel_index = None
        self.last_beacon = None
        # S2.2 evidence: rate limit for the contest-flag bank prints
        # (regular stdout so it survives --release; bounded fuel cost).
        self.contest_logs = 0

    def buy_items(self, champion_short_name: str):
        me = cambc.me()
        stat = config.CHAMPION_STAT_PRIORITY.get(champion_short_name, "STRENGTH")
        if stat == "STRENGTH":
            item = Item.POWER_SHARD_OF_STRENGTH
        elif stat == "FOCUS":
            item = Item.POWER_SHARD_OF_FOCUS
        else:
            item = Item.POWER_SHARD_OF_HEALTH
        count = me.gold_count() // item.cost()
        if count and me.can_buy(item, count):
            me.buy(item, count)

    def play_turn(self):
        # First-turn bookkeeping.
        if self.chosen_stat is None:
            try:
                short = observation.champion_short_name(int(cambc.me().champion_name()))
            except Exception:
                short = ""
            stat_priority = config.CHAMPION_STAT_PRIORITY.get(short, "STRENGTH")
            self.chosen_stat = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}[stat_priority]
            print(
                f"bot: champion={short} stat={stat_priority}",
                flush=True,
            )

        # Round stamp removed (S2.1): the all-champion bytes 0-3 stamp
        # violated the byte-ownership map (byte i belongs to champion i)
        # and had no consumer; the S2.1 beacon supersedes it. The frozen
        # Stage 2 baseline (bot_baseline.py) keeps its own stamp.

        snap = observation.snapshot(self)
        me = snap.me
        position = snap.position

        # Intel: persist last-known enemy locations (facts only; runs even
        # while channeling — spotting an enemy mid-tribute is exactly when
        # we want it). First sights are logged via debug_block (dev only).
        try:
            first_sights = intelligence.update_enemy_memory(self, snap)
        except Exception:
            first_sights = []
        if first_sights:
            cambc.debug_block(
                lambda: print(
                    "intel: first sight "
                    + ", ".join(
                        f"{observation.champion_short_name(r['kind'])}"
                        f"@({r['x']},{r['y']}) r{r['round']}"
                        for r in first_sights
                    ),
                    flush=True,
                )
            )

        # S2.1: write our owned signal byte (write-only discipline) and
        # read the whole channel (read-all). Runs before the channeling
        # gate so a tribute-channeling champion still broadcasts — the
        # altar-contested use case. Beacon changes are logged dev-only.
        wrote = None
        try:
            wrote = intelligence.write_beacon(self, snap)
        except Exception:
            wrote = None
        ally_signals = []
        try:
            ally_signals = intelligence.read_signals()
        except Exception:
            ally_signals = []

        # S2.2: fold ally beacons into the snapshot as facts (consumers in
        # utility: contested altars / region risk). Failure leaves the
        # Snapshot defaults -> S2.0-identical scoring.
        try:
            intelligence.fold_ally_intel(snap, ally_signals)
        except Exception:
            pass
        if wrote is not None and wrote != self.last_beacon:
            prev_txt = "init" if self.last_beacon is None else hex(self.last_beacon)
            self.last_beacon = wrote
            fresh_allies = sum(1 for s in ally_signals if s["fresh"] > 0)
            cambc.debug_block(
                lambda: print(
                    f"signal: idx={self.channel_index} {prev_txt}->{hex(wrote)} "
                    f"r={cambc.round_number()} fresh_allies={fresh_allies}",
                    flush=True,
                )
            )

        # Gate 1: channeling / dead.
        if (
            not snap.is_alive
            or snap.is_recalling
            or snap.is_submitting_gems
            or snap.is_teleporting
        ):
            return

        at_spawn = game_map.spawn_region(Team.ALLY).is_set(position)

        # Gate 2: on spawn -> buy shards.
        if at_spawn:
            try:
                short = observation.champion_short_name(int(me.champion_name()))
            except Exception:
                short = ""
            try:
                self.buy_items(short)
            except Exception:
                pass

        # Gate 3: low HP away from spawn -> recall (baseline rule preserved
        # outside the utility argmax so an early death is always avoided).
        if (
            not at_spawn
            and snap.max_hp > 0
            and snap.hp < snap.max_hp * config.RECALL_HP_FRAC
        ):
            if execution.recall_to_spawn():
                return

        # Build candidate set; pick argmax; safety override may force recall.
        candidates = utility.generate_candidates(snap)
        cand = utility.safety_override(candidates, snap) or utility.best(candidates)

        # Diagnostic - log every cand.kind for first few rounds.
        try:
            if cambc.round_number() <= 20:
                kinds = [c[0] for c in candidates]
                me_id = me.champion_name()
                short = observation.champion_short_name(int(me_id)) if me_id is not None else "?"
                cs = cand[0] if cand else "NONE"
                print(
                    f"v16: r={cambc.round_number()} {short} kinds={kinds[:8]} best={cs}",
                    flush=True,
                )
        except Exception:
            pass

        if cand is not None:
            kind, target = cand[0], cand[1]

            # Capture: stand on the cleared spawn tile for 5 rounds.
            if kind == "capture":
                goals = target.spawn_tiles()
                execution.move_towards_closest(goals)
                return

            # Bank: try to submit at this altar; if not in range, walk toward
            # this altar's approach tiles (S2.2: the chosen altar, not the
            # nearest ring — see execution.altar_approach_goals).
            if kind == "bank":
                # S2.2 evidence (rate-limited, regular print so it survives
                # --release): the beacon-contested flag shaped this decision.
                if "contest" in (cand[3] or "") and self.contest_logs < 3:
                    self.contest_logs += 1
                    try:
                        print(
                            f"s22: bank contest flag: {cand[3]} "
                            f"altar=({target.x},{target.y})",
                            flush=True,
                        )
                    except Exception:
                        pass
                if execution.submit_gems_to_altar(target):
                    return
                goals = execution.altar_approach_goals(snap, target)
                if not goals.is_empty() and execution.move_towards_closest(goals):
                    return
                # Without reachable altar tiles, fall through to farm.

            # Recall: explicit fallback for the safety override.
            if kind == "recall":
                if execution.recall_to_spawn():
                    return

            # Fight: enemy in attack range.
            if kind == "fight" and snap.can_attack and target is not None:
                try:
                    me_obj = cambc.me()
                    atk = me_obj.attack_tiles()
                    if atk.is_set(target.position()):
                        me_obj.attack(target)
                        return
                except Exception:
                    pass

            # Farm: walk toward the chosen group's spawn tiles; opportunistically
            # attack any visible camp in our attack tiles before and after the
            # move (killable-filtered via snap: never wake a Monolith's group).
            if kind == "farm":
                try:
                    if execution.attack_first_in_range(cambc.vision.camps(), snap):
                        return
                except Exception:
                    pass
                goals = target.spawn_tiles()
                execution.move_towards_closest(goals)
                try:
                    execution.attack_first_in_range(cambc.vision.camps(), snap)
                except Exception:
                    pass
                return

        # Baseline-style opportunistic camp attack when we have no utility
        # decision (rare; mainly during fuel-suspended turns). Mirrors gate
        # 6 of the Stage 2 baseline, plus the killable filter.
        try:
            if execution.attack_first_in_range(cambc.vision.camps(), snap):
                return
        except Exception:
            pass
        # Else sit still.

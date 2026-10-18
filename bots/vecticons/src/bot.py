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
"""
from __future__ import annotations

import struct

import cambc
from cambc import game_map

import config
from managers import execution
from managers import knowledge
from managers import utility

Team, Item = cambc.Team, cambc.ItemType


class Bot:
    def __init__(self):
        self.chosen_stat = None
        self.map_cache = None

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
                short = knowledge.champion_short_name(int(cambc.me().champion_name()))
            except Exception:
                short = ""
            stat_priority = config.CHAMPION_STAT_PRIORITY.get(short, "STRENGTH")
            self.chosen_stat = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}[stat_priority]
            print(
                f"bot: champion={short} stat={stat_priority}",
                flush=True,
            )

        # Round stamp (shared-memory layout documented in bot_baseline.py).
        try:
            data = bytearray(4)
            cambc.shared_memory.read(0, data)
            struct.unpack("<I", data)[0]
            cambc.shared_memory.write(0, struct.pack("<I", cambc.round_number()))
        except Exception:
            pass

        snap = knowledge.snapshot(self)
        me = snap.me
        position = snap.position

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
                short = knowledge.champion_short_name(int(me.champion_name()))
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
                short = knowledge.champion_short_name(int(me_id)) if me_id is not None else "?"
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
            # the altar approach tiles.
            if kind == "bank":
                if execution.submit_gems_to_altar(target):
                    return
                goals = execution.altar_approach_goals(snap)
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
            # attack any visible camp in our attack tiles before and after the move.
            if kind == "farm":
                try:
                    if execution.attack_first_in_range(cambc.vision.camps()):
                        return
                except Exception:
                    pass
                goals = target.spawn_tiles()
                execution.move_towards_closest(goals)
                try:
                    execution.attack_first_in_range(cambc.vision.camps())
                except Exception:
                    pass
                return

        # Baseline-style opportunistic camp attack when we have no utility
        # decision (rare; mainly during fuel-suspended turns). Mirrors gate
        # 6 of the Stage 2 baseline.
        try:
            if execution.attack_first_in_range(cambc.vision.camps()):
                return
        except Exception:
            pass
        # Else sit still.

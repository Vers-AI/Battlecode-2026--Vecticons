"""Per-champion decisions. main.py dispatches here after the draft.

Stage 2 baseline loop (hardcoded):
  1. If channeling (recall/tribute/teleport), dead, or cannot act: return.
  2. If on spawn: buy shards per CHAMPION_STAT_PRIORITY.
  3. If HP < RECALL_HP_FRAC and not on spawn: recall.
  4. If enemy champion in attack range: attack it.
  5. If gem_count >= BANK_THRESHOLD: path to nearest unlocked altar, submit in range.
  6. Else: farm nearest killable camp.

Killable-camp filter (KILLABLE_HP_CAP, Monolith exclusion) runs before any
scoring - mandatory per architecture.md ("Monolith trap").

Stage 3 (next) replaces the hardcoded gates with candidate-action scoring.
The A/B reference for that is bots/vecticons/src/bot_baseline.py (copied
from this file at the start of Stage 3).
"""
from __future__ import annotations

import struct

import cambc
from cambc import game_map

import config
from managers import execution
from managers import knowledge

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

        # Read before writing round stamp at byte 0.
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

        # Gate 1: channeling / dead / cannot act.
        if (
            not snap.is_alive
            or snap.is_recalling
            or snap.is_submitting_gems
            or snap.is_teleporting
            or (not snap.can_move and not snap.can_attack)
        ):
            return

        at_spawn = game_map.spawn_region(Team.ALLY).is_set(position)

        # Gate 2: on spawn -> buy shards.
        if at_spawn:
            try:
                short = knowledge.champion_short_name(int(me.champion_name()))
            except Exception:
                short = ""
            self.buy_items(short)

        # Gate 3: low HP away from spawn -> recall.
        if (
            not at_spawn
            and snap.max_hp > 0
            and snap.hp < snap.max_hp * config.RECALL_HP_FRAC
        ):
            if execution.recall_to_spawn():
                return

        # Gate 4: enemy champion in attack range -> attack.
        if snap.can_attack and execution.attacked_by_enemy(snap):
            return

        # Gate 5: carrying enough gems -> bank at nearest unlocked altar.
        if snap.gems >= config.BANK_THRESHOLD:
            altar = execution.nearest_unlocked_altar(snap)
            if altar is not None:
                if execution.submit_gems_to_altar(altar):
                    return
                goals = execution.altar_approach_goals(snap)
                if not goals.is_empty() and execution.move_towards_closest(goals):
                    return

        # Gate 6: farm the nearest killable camp.
        # First: if any visible group has a reward available, stand on its
        # spawn tile to capture (5-round timer, GameRules capture phase).
        reward = execution.reward_available_camp(snap)
        if reward is not None:
            goals = reward.spawn_tiles()
            execution.move_towards_closest(goals)
            return

        # Attack any visible camp in attack range first; then walk toward the
        # nearest not-yet-visible killable camp.
        visible = cambc.vision.camps()
        if execution.attack_first_in_range(visible):
            return

        target_camp = None
        killable_visible = execution.visible_killable_camps(snap)
        if killable_visible:
            target_camp = killable_visible[0]
        else:
            zone_groups = execution.zone_killable_camps(snap)
            target_camp = zone_groups[0] if zone_groups else None

        if target_camp is not None:
            goals = target_camp.spawn_tiles()
            execution.move_towards_closest(goals)
            execution.attack_first_in_range(cambc.vision.camps())
            return

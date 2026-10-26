"""Per-champion decisions. main.py dispatches here after the draft.

Stage 3 utility layer (architecture.md §Stage 3), upgraded by S2.3.
==================================================================

Per the spec, the utility layer replaces hardcoded gates with candidate
generation + argmax scoring. The critical "Monolith trap" constraint
(KILLABLE_HP_CAP + Monolith exclusion runs BEFORE scoring) is preserved
via the shared execution.filter in utility.generate_candidates.

What this implementation does:
- Battle plan candidates: capture (stand on a cleared spawn tile),
  bank (submit gems at the chosen altar), recall (heal + spend gold),
  fight (enemy in attack range), flee (weak, enemy in range), boss (the
  excluded groups, scored as fights), farm (killable camp groups).
- Scoring per architecture.md:
    farm_camp(c) = gems(c) * TIME_DISCOUNT ** (reach + clear)
    bank(a)      = gem_count * BANK_UTILITY_PER_GEM * TIME_DISCOUNT ** d
    recall       = (heal_value + gold_value) * TIME_DISCOUNT ** d_spawn
    fight(e)     = (KILL_REWARD_GOLD + bag) * TIME_DISCOUNT ** turns
    capture(c)   = CAPTURE_UTILITY  (always dominant when available)
- Argmax wins. Ties fall back to baseline gate order via the dispatch
  sequence (capture > bank > recall > fight > flee > boss > farm).
- S2.3 removed the hardcoded low-HP recall gate (Gate 3) and the
  safety_override path: recall is a scored candidate now, and a recall
  channel is never started inside an enemy's attack range (the flee
  candidate breaks the line of contact first).

The Stage 2 reference is frozen at bots/vecticons/src/bot_baseline.py for
the A/B comparison required by Stage 3 done-when.

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
from managers import scouting
from managers import utility

Team, Item = cambc.Team, cambc.ItemType


def _goal_xy(goal) -> str:
    """The goal tile of a candidate as "x,y" (the goal is a tuple of tiles)."""
    try:
        tile = goal[0] if isinstance(goal, (tuple, list)) else goal
        return "%d,%d" % (int(tile.x), int(tile.y))
    except Exception:
        return "?"


class Bot:
    # main.py uses this marker to opt the shared draft manager into the
    # S2.3 format.  A frozen baseline module has no marker and therefore
    # keeps the legacy draft payload.
    S23_ENABLED = True

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
        # S2.3: the champion's OWN claim memory (group handle -> the round
        # the group's reward was claimed). A group whose reward was taken
        # has no live monster until it respawns, so it must not be farmed
        # again in the meantime. Per champion: each champion runs in its
        # own process with its own Bot instance, so this is a per-champion
        # fact, not shared state.
        self.claimed_groups = {}
        # S2.4: rate limit for the shopping evidence prints (a regular
        # print, so it survives --release).
        self.shop_logs = 0
        # S2.4: the ward's trinket charge, mirrored locally. The engine
        # is the authority (bot.ward_turn reads it when the mirror
        # expires), but the mirror keeps the common turn at one
        # comparison instead of an engine read (the fuel contract).
        self.ward_cd = 0
        # S2.4.3: do we CARRY an altar lock? The engine's carry limit is
        # one, so the mirror is exact: we buy it (flag on) and place it
        # (flag off). A respawn re-syncs it from the engine.
        self.lock_carried = False

    def buy_items(self, champion_short_name: str, count: int = 1) -> bool:
        """Buy our emblem-family shard (ONE buyer, ONE mapping).

        S2.4: the item comes from config.power_shard_for - the emblem
        FAMILY (config.CHAMPION_EMBLEM_FAMILY, e.g. TREASURE for Vesper
        Vale) and the priority STAT variant (config.CHAMPION_STAT_PRIORITY).
        The old local if-chain always bought a POWER shard and ignored the
        family map, so no emblem passive was ever chosen on purpose.
        """
        item = config.power_shard_for(champion_short_name)
        return execution.buy_item(item, count)

    def shop_turn(self, snap, where: str, cand=None) -> bool:
        """The shopping action of a shop turn (on spawn or while dead).

        The utility layer scores the purchase (family shard vs altar lock
        vs hold vs the consumable reserve); this method only performs it.

        S2.4.3: the "lock" candidate buys ONE altar lock. The engine's
        carry limit is one lock at a time (GameRules "carry one at a
        time"), and the assert below states that contract in the code.
        """
        if cand is None:
            try:
                cand = utility.shop_candidate(snap)
            except Exception:
                cand = None
        if cand is None:
            return False
        kind = cand[0]
        if kind == "lock":
            carried = execution.consumable_count(cambc.ConsumableType.ALTAR_LOCK)
            assert carried <= 1, f"altar lock carry limit exceeded: {carried}"
            if carried > 0:
                return False
            bought = execution.buy_consumable(cambc.ConsumableType.ALTAR_LOCK)
            if bought:
                self.lock_carried = True
            if bought and self.shop_logs < 3:
                self.shop_logs += 1
                print(
                    f"s24: shop {where} ALTAR_LOCK gold={snap.gold}",
                    flush=True,
                )
            return bought
        if kind != "shop":
            return False
        count = int(cand[1])
        if count <= 0:
            return False
        try:
            short = observation.champion_short_for(snap)
        except Exception:
            short = ""
        bought = self.buy_items(short, count)
        if bought and self.shop_logs < 3:
            self.shop_logs += 1
            try:
                item = config.power_shard_for(short)
                name = getattr(item, "name", str(item))
                print(
                    f"s24: shop {where} {name} x{count} gold={snap.gold}",
                    flush=True,
                )
            except Exception:
                pass
        return bought

    def place_lock(self, snap) -> bool:
        """Place a carried altar lock on the altar the intel points at.

        S2.4.3: the lock is a FREE action (GameRules "you can use
        consumable items without limit"), so it runs next to the ward
        placement and does not consume the turn's movement. It is placed
        on the nearest TRAFFIC altar (an altar the enemy is using) that
        is within two tiles of us - the lock must be placed from next to
        the altar.

        FUEL: the inventory mirror (self.lock_carried) keeps the common
        turn free - the engine's consumable count is only read when we
        believe we carry a lock (or right after a respawn).
        """
        if getattr(self, "lock_resync", False):
            self.lock_resync = False
            try:
                self.lock_carried = (
                    execution.consumable_count(cambc.ConsumableType.ALTAR_LOCK) > 0
                )
            except Exception:
                self.lock_carried = False
        if not self.lock_carried:
            return False
        targets = utility.lock_targets(snap)
        if not targets:
            return False
        placed = False
        target = None
        for coord in targets:
            if execution.altar_lock_occupied(snap, coord):
                continue
            placed = execution.use_altar_lock(snap, coord)
            target = coord
            break
        if placed:
            self.lock_carried = False
        if placed and self.shop_logs < 6:
            self.shop_logs += 1
            print(
                f"s24: lock altar=({target[0]},{target[1]})",
                flush=True,
            )
        return placed

    def ward_turn(self, snap, candidates) -> bool:
        """The ward mini-argmax (S2.4) - a FREE parallel action.

        The ward spends the trinket charge, NOT the move/attack
        allowance (GameRules :116), so it is placed independently of the
        turn's action. The engine folds the ward's vision into our own
        vision from the next round on, so the observation layer (visible
        camps, enemy sightings) and the S2.1/S2.2 signal path see
        through it.

        The interest tiles are the nearest UNSEEN camp groups (stashed by
        the utility layer's farm scan on this turn), the chosen bank
        altar and the objective path's midpoint - all plain ints, no
        engine read.
        """
        if self.ward_cd > 0:
            self.ward_cd -= 1
            return False
        # The engine is the authority on the trinket charge: one read.
        try:
            cd = int(cambc.me().ward_cooldown())
        except Exception:
            cd = 0
        if cd > 0:
            self.ward_cd = cd
            return False
        interest = []
        for entry in getattr(snap, "ward_interest", ()) or ():
            interest.append((entry[0], entry[1], entry[2]))
        # The objective path: the altar we are going to bank at and the
        # midpoint of the walk to it.
        for cand in candidates or ():
            if cand[0] == "bank":
                try:
                    ax = int(cand[1][0])
                    ay = int(cand[1][1])
                except Exception:
                    break
                interest.append((ax, ay, float(config.WARD_ALTAR_WEIGHT)))
                px, py = snap.origin_xy
                interest.append(
                    ((px + ax) // 2, (py + ay) // 2, float(config.WARD_PATH_WEIGHT))
                )
                break
        if not interest:
            # Nothing worth seeing yet: back off a few rounds instead of
            # asking the engine every turn.
            self.ward_cd = 5
            return False
        placed = scouting.place_ward(snap, interest)
        if placed:
            self.ward_cd = int(config.WARD_COOLDOWN_ROUNDS)
        return placed

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

        # S2.3 opts into the shared distance-field/cache path.  The default
        # snapshot() mode remains the frozen Stage 2 behavior for
        # bot_baseline.py and other legacy callers.
        snap = observation.snapshot(self, s23_cache=True)
        me = snap.me
        position = snap.position

        # Intel: persist last-known enemy locations (facts only; runs even
        # while channeling — spotting an enemy mid-tribute is exactly when
        # we want it). First sights are logged via debug_block (dev only).
        try:
            first_sights = intelligence.update_enemy_memory(self, snap)
        except Exception:
            first_sights = []
        # The threat model reads the fog memory, so it is refreshed after
        # the memory update (otherwise the threat records would lag a turn).
        try:
            observation.refresh_threat_memory(self, snap)
        except Exception:
            pass
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

        # S2.4.3: the fog traffic prior for the altar lock - our own
        # last-known enemy positions as plain ints (no engine read: the
        # memory was just updated above).
        try:
            snap.fog_enemies = tuple(
                (int(rec["x"]), int(rec["y"]))
                for rec in intelligence.recent_sightings(self).values()
            )
        except Exception:
            snap.fog_enemies = ()

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

        # Gate 1: channeling.
        #
        # S2.4: a DEAD champion is NOT a dead end - dead champions retain
        # their shopping turns (GameRules:361), so the 60-round death
        # window is a FREE gold-to-stats conversion. The old gate skipped
        # it entirely (the death window bought nothing).
        if snap.is_recalling or snap.is_submitting_gems or snap.is_teleporting:
            return
        if not snap.is_alive:
            # S2.4.3: a respawn re-syncs the lock mirror (the engine
            # decides whether a dead champion keeps its consumables).
            self.lock_carried = False
            self.lock_resync = True
            try:
                self.shop_turn(snap, "dead")
            except Exception:
                pass
            return

        # Gate 2 is GONE (S2.4): the old hardcoded "on spawn -> buy every
        # POWER shard" gate is replaced by the SCORED shopping decision in
        # the utility layer (family shard vs hold vs the consumable
        # reserve), which runs as part of the argmax below. The old
        # gate's engine read (spawn_region(...).is_set(position)) went
        # with it: the shop legality is a pure-Python test against the
        # cached spawn tiles (utility.can_shop), so the turn no longer
        # spends ~15k fuel on a bitboard draw it does not use.

        # Gate 3 is GONE (S2.3): the low-HP recall is a scored candidate now
        # (heal value + gold to spend, discounted by the walk home, scaled
        # by the channel-interrupt risk), so the hardcoded gate would fight
        # the argmax instead of feeding it.

        # Build candidate set; pick argmax.
        #
        # S2.4: the ward's interest tiles come from the utility layer's
        # farm scan (the nearest unseen camp groups) - the flag tells it
        # to stash them on this turn (the fuel contract: no second scan
        # over the group table).
        snap.want_ward = self.ward_cd <= 0
        candidates = utility.generate_candidates(snap)

        # S2.4: the shopping decision is scored in the argmax (utility).
        # Buying is a FREE parallel action (it costs no move/attack
        # allowance), so the purchase is executed here and the shop
        # candidate is then removed from the argmax set - a purchase must
        # never shadow the turn's movement / fight decision.
        shop_cand = None
        for c in candidates:
            if c[0] in ("shop", "lock"):
                shop_cand = c
                break
        if shop_cand is not None:
            try:
                self.shop_turn(snap, "spawn", cand=shop_cand)
            except Exception:
                pass
            candidates = [c for c in candidates if c[0] not in ("shop", "lock")]

        # S2.4: the WARD mini-argmax - a FREE parallel action (the trinket
        # charge, not the move/attack allowance). It runs after the
        # candidate set is built so it can reuse the farm scan's interest
        # tiles.
        try:
            self.ward_turn(snap, candidates)
        except Exception:
            pass

        # S2.4.3: the altar lock - another FREE parallel action (GameRules
        # "you can use consumable items without limit"). Placed on the
        # altar the traffic intel points at.
        try:
            self.place_lock(snap)
        except Exception:
            pass

        cand = utility.best(candidates)

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
            if config.S23_TRACE and cambc.round_number() <= int(config.S23_TRACE_ROUNDS):
                short = observation.champion_short_name(int(me.champion_name()))
                detail = " | ".join(
                    f"{c[0]}:{c[2]:.1f}:{c[3]}:g=({_goal_xy(c[4])})"
                    if len(c) > 4 and c[4] is not None
                    else f"{c[0]}:{c[2]:.1f}:{c[3]}"
                    for c in candidates
                )
                print(
                    f"trace: r={cambc.round_number()} {short} "
                    f"pos=({position.x},{position.y}) hp={snap.hp:.0f} "
                    f"gems={snap.gems} "
                    f"vis_camps={len(snap.visible_camps)} "
                    f"vg={len(snap.visible_camp_groups)} "
                    f"zc={len(snap.camp_groups)} "
                    f"gp={len(snap.group_positions)} "
                    f"gpn={sum(1 for v in snap.group_positions.values() if v)} "
                    f"gi={len(snap.group_index)} "
                    f"| {detail}",
                    flush=True,
                )
        except Exception:
            pass

        if cand is not None:
            kind, target = cand[0], cand[1]
            # The goal tile is the tile the utility layer resolved from the
            # shared field: the nearest reachable tile of the target (the
            # camp group's spawn tiles, the altar's approach ring, our own
            # spawn). It is the tile the cached gradient path ends at.
            goal = cand[4] if len(cand) > 4 else None

            # Capture: stand on the cleared spawn tile for 5 rounds.
            if kind == "capture":
                if goal is not None:
                    execution.move_towards_goal(snap, goal, kind="scoring")
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
                            f"altar=({target[0]},{target[1]})",
                            flush=True,
                        )
                    except Exception:
                        pass
                if execution.submit_gems_to_altar(target, snap):
                    return
                if goal is not None and execution.move_towards_goal(
                    snap, goal, kind="scoring"
                ):
                    return
                # Without reachable altar tiles, fall through to farm.

            # Recall: heal + spend gold at spawn. Never start the channel
            # inside an enemy's attack range (a recall channel is
            # interrupted by damage) - step away first and recall next turn.
            if kind == "recall":
                threat = execution.enemy_in_range(snap)
                if threat is not None:
                    try:
                        if execution.move_away_from(threat.position()):
                            return
                    except Exception:
                        pass
                    # Never begin a damage-interruptible channel while the
                    # enemy can still hit us. If the step away failed, wait
                    # for a safer turn instead of falling through to recall.
                    return
                if execution.recall_to_spawn():
                    return

            # Flee (S2.3): weak and an enemy in range -> move away first.
            if kind == "flee":
                try:
                    pos = target.position()
                except Exception:
                    pos = None
                if execution.move_away_from(pos):
                    return

            # Fight: enemy in attack range.
            if kind == "fight" and snap.can_attack and target is not None:
                try:
                    atk = execution.our_attack_tiles(snap)
                    if atk is not None and atk.is_set(target.position()):
                        me.attack(target)
                        return
                except Exception:
                    pass

            # Boss (S2.3 boss graduation): the excluded groups are scored
            # fight candidates. Walk to the group's tiles and strike its
            # camps when they come into range (the killable filter is
            # lifted for THIS group only).
            if kind == "boss" and target is not None:
                try:
                    if execution.attack_first_in_range(
                        snap.visible_camps, snap, allow_excluded=True, group=target
                    ):
                        return
                except Exception:
                    pass
                # The boss's own tiles are WALLS in the scoring field (the
                # boss is a door), so the engagement path comes from the
                # terrain-only boss field.
                if goal is not None and execution.move_towards_goal(
                    snap, goal, kind="boss"
                ):
                    return
                try:
                    execution.attack_first_in_range(
                        snap.visible_camps, snap, allow_excluded=True, group=target
                    )
                except Exception:
                    pass
                return

            # Farm: walk toward the chosen group's spawn tiles; opportunistically
            # attack any visible camp in our attack tiles before and after the
            # move (killable-filtered via snap: never wake a Monolith's group).
            if kind == "farm":
                try:
                    if execution.attack_first_in_range(snap.visible_camps, snap):
                        return
                except Exception:
                    pass
                if goal is not None and execution.move_towards_goal(
                    snap, goal, kind="scoring"
                ):
                    return
                try:
                    execution.attack_first_in_range(snap.visible_camps, snap)
                except Exception:
                    pass
                return

        # Baseline-style opportunistic camp attack when we have no utility
        # decision (rare; mainly during fuel-suspended turns). Mirrors gate
        # 6 of the Stage 2 baseline, plus the killable filter.
        try:
            if execution.attack_first_in_range(snap.visible_camps, snap):
                return
        except Exception:
            pass
        # Else sit still.

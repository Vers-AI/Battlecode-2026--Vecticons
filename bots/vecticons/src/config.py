"""All Sprint 1 tunables (architecture.md config.py contract).

No magic numbers elsewhere. Every value carries either a source citation
(GameRules / champion stat tables / mechanics) or a PLACEHOLDER tag for first-pass
tuning values pending A/B data.

NOTE on cambc.constants resolution: do NOT read them at module import
time - the wasm preload phase has no engine context. Use `resolve()`
the first time a value is needed in a turn function.
"""
from __future__ import annotations


# Sources: GameRules camp table, champion stats, mechanics sections.
# PLACEHOLDER = first-pass value; not final.

BANK_THRESHOLD: int = 1           # PLACEHOLDER. gems; tribute channel = ceil(gems/10) turns.
# S2.3.1 RE-TUNE (measured 2026-10-24): the old 0.9 was far too steep for a
# 2,000-round match. 0.9 ** 108 (the walk to the first camp on the maze
# maps) is 1.3e-5, so every distant farm candidate scored ~0 and the argmax
# was decided by noise; worse, 0.9 ** (d + ceil(gems/10)) made the BANK
# candidate unreachable (40 gems banked 20 tiles away scored 3.9 against a
# farm candidate's 23.9, so the bot would never tribute and the score would
# stay 0).
#
# The discount prices the DELAY of a payoff, not the passage of time: a
# target that pays off in T turns is worth discount ** T of its value, and
# 0.99 means "a target 100 turns away is still worth 37% of its value" -
# which matches a 2,000-round match where a camp cycle is 20-40 turns.
# Measured on 01starvation: at 0.99 the bank candidate wins the argmax at
# ~30-40 carried gems (a camp cycle's worth), which is the intended
# "farm a bit more, then bank" trade-off.
TIME_DISCOUNT: float = 0.99       # PLACEHOLDER. per-turn multiplier on utility.
RECALL_HP_FRAC: float = 0.35      # from starter bot heuristic.
KILLABLE_HP_CAP: int = 5_000      # PLACEHOLDER. Floor on hopeless FARM fights: a group whose
                                   # total HP exceeds it is not a farm candidate. S2.3: boss
                                   # groups (Monolith, mega-packs) are no longer *excluded* from
                                   # candidacy - they become scored FIGHT candidates whose
                                   # early-game math prices them out by arithmetic
                                   # (discount ^ turns_to_kill ~ 0), not by this gate.
DEATH_RISK_BASE: float = 0.05     # PLACEHOLDER. Floor of every survival probability (S2.3:
                                   # activated - the carried-gem risk term and P(survive)).
BANK_UTILITY_PER_GEM: float = 1.0 # S2.3: every candidate score is in GEM-EQUIVALENTS, so one
                               # carried gem is worth one unit of bank utility (multiplied by
                               # the travel discount and the channel length). The Stage 3
                               # flat 100/gem made "bank" a flat 100+ that no farm candidate
                               # could ever beat - the score compared unlike units.
                               # S2.3 measured note: the gem-equivalent farm score is small
                               # (a group's whole value is ~5-40 gem-equivalents and the
                               # clear time is priced in), so a bank trip of 12+ turns needs
                               # ~4-6 carried gems to win the argmax. That is the intended
                               # "farm a bit more, then bank" trade-off.
RECALL_UTILITY: float = 200.0     # PLACEHOLDER. Legacy constant (Stage 3 recall candidate).
                              # S2.3: the recall candidate is scored
                              # (RECALL_HEAL_VALUE + RECALL_GOLD_VALUE) x discount^d_spawn x
                              # P(channel survives) instead of this flat number.
KILL_REWARD_GOLD: int = 50        # GameRules "When a champion dies".
# S2.3: channel lengths (GameRules). The tribute channel runs ceil(gems/10)
# rounds; the recall channel is 5 ticks. Both are priced into the discount so
# a long channel is worth less than a short one.
TRIBUTE_CHANNEL_TURNS_PER_GEM: float = 0.1
RECALL_CHANNEL_TURNS: int = 5
# S2.3: the flee candidate scales with what we would lose (carried value),
# how hurt we are and how close the threat is - a flat score cannot express
# "drop everything and run".
FLEE_CARRIED_WEIGHT: float = 1.0  # gem-equivalents carried multiply the flee score.
FLEE_HP_WEIGHT: float = 1.0       # the HP deficit (0..1) multiplies the flee score.
FLEE_THREAT_FALLOFF: float = 0.5  # tiles of threat distance that halve the score.
# S2.3: the base of the flee score, in gem-equivalents (it is a "give up the
# ground we stand on" score, so it lives on the same scale as the candidates
# it competes with).
FLEE_BASE_VALUE: float = 4.0

# Stage 3 utility-layer tuning (architecture.md §Stage 3).
# Safety floor under which low-HP capping overrides the utility argmax
# (bot retreats and recalls before the pack wakes up on us).
SAFETY_HP_FRAC: float = 0.25       # PLACEHOLDER. Below this HP fraction the recall candidate always wins.
# Per-turn candidate-pool caps. The utility layer's per-turn cost is bounded by
# (VISIBLE_CAP + ZONE_CAP) farm candidates plus the constant-cost capture/bank/
# recall/fight candidates. Without these caps, v15 burned fuel in proportion to
# the full map's group count (one ``group.camps()`` call per group per turn).
FARM_VISIBLE_CAP: int = 3          # PLACEHOLDER. Top-N nearest killable visible camps considered.
FARM_ZONE_CAP: int = 3             # PLACEHOLDER. Top-N nearest unseen zone camps considered.
# S2.3: the flat gem estimates (FARM_VISIBLE_GEM_ESTIMATE / FARM_ZONE_GEM_ESTIMATE)
# are DELETED - every farm candidate now carries its group's REAL gem value
# (group.gem_value(), precomputed into group_meta), so farm picks are no longer
# nearest-first by construction. The group score is still in gem units; the
# clear-time estimate below is the flat per-cycle cost.
FARM_CLEAR_TURNS: int = 2          # PLACEHOLDER. Floor on the clear-time estimate of a
                                   # farm cycle (kill + capture). S2.3 replaces the flat
                                   # estimate with the group's REAL clear time
                                   # (group HP / team DPS); this stays as the floor for
                                   # groups that die in one hit.
# Extra turn of discount for a camp we have never seen (the group's static
# value is known, but the walk is a guess).
FARM_ZONE_UNCERTAINTY_TURNS: int = 1
# S2.3 fuel: how many candidates get the HONEST field distance (one engine
# read per spawn tile, ~3.4k fuel each). The pools are pre-filtered by the
# cheap straight-line distance, so the engine reads hit the nearest
# candidates first. Scoring every group on the map cost 1.5M fuel per turn
# on a 46-group map (the whole per-turn budget) and starved the turn.
#
# S2.3.1: the pre-filter used to be a hard cap because the honest distance
# needed a Vec2 coordinate read per group (the S2.3 first cut paid ~1.8k
# per coordinate). The representative tile is now CACHED as plain ints, so
# an honest distance read is ONE field.get (~2.1k). The pools were widened
# so the ranking sees the real path distances: on the maze maps the
# straight-line-nearest group was 98 path-steps away while a group further
# away in a straight line was 20 steps away on the path - the narrow pool
# sent the bot on a 130-round walk to the wrong camp.
FARM_VISIBLE_POOL: int = 12
FARM_ZONE_POOL: int = 24
# S2.3 fuel: how many of a group's spawn tiles are distance-read. A group's
# tiles form one cluster, so the nearest few carry the honest distance;
# the rest are within a tile or two of it. Bounded so a big pack cannot
# spend 20 field reads on one candidate.
FARM_TILE_READS: int = 3

# ---------------------------------------------------------------------------
# S2.3 - honest distances (shared per-turn DistanceField).
# ---------------------------------------------------------------------------
# PATH-DISTANCE CAP of the shared scoring field's BFS. The engine's
# `DistanceField.compute(walls, roots, visited, max_distance)` treats
# max_distance as a limit on the PATH distance from the root: tiles farther
# than that are left unreached and read back as the 65535 sentinel
# (probe-verified 2026-10-23). A cap therefore cannot distinguish "sealed
# behind a door" from "far away on the other side of a maze" - and the
# sentinel is what the scoring reads as "unreachable, score 0".
#
# The S2.3 first cut used 32, which on the maze maps (01starvation: the
# nearest camp is 111 path-steps away, the farthest 163) made EVERY group
# unreachable: the bot had an empty candidate set and idled for 2,000
# rounds (19 of the 40 maps). The cap must cover the whole reachable map:
# the honest answer for a genuinely sealed target is "unreachable", and that
# has to stay distinguishable from "far".
FIELD_MAX_DISTANCE: int = 4096
# Fuel floor below which the shared field is NOT rebuilt this turn. The
# degraded path (a truncated field) was the S2.3 first cut and it was
# WRONG: a truncated field cannot tell "sealed behind a door" from "far
# away on the other side of a maze", so the bot either idled (no candidate
# at all) or believed a sealed altar was reachable. The S2.3 design is
# ONE full-map field per turn; the fuel floor only decides whether we can
# afford it at all (below it the turn degrades to "act on what is in
# sight", never "walk toward an unknown target").
FIELD_MIN_FUEL: int = 900_000
# How many of an altar's approach tiles pay for a field read per turn. The
# approach ring is a 3x3 Chebyshev disk (SUBMIT_GEMS_RANGE == 1), so its
# tiles are within a tile or two of each other; reading the nearest four
# is enough to find a reachable one even when the closest one is a wall.
ALTAR_DISTANCE_READS: int = 4
# Radius of the boss-engagement field (terrain-only walls; the boss group's
# own tiles are DESTINATIONS, never walls). Same path-distance semantics as
# the scoring field, so it needs the same whole-map cap.
BOSS_FIELD_MAX_DISTANCE: int = 4096
# How many boss groups may share ONE boss-engagement field. The engine's
# compute_to stops only when EVERY destination is reached, so each extra
# destination can push the search from a path's worth of expansion to a
# full-map sweep. Two is the measured sweet spot.
BOSS_FIELD_MAX_GROUPS: int = 2
# S2.3 fuel: the movement no longer builds a second full-map field. The
# engine's gradient is walked backwards from the goal tile to our position;
# the last gradient step IS the step to take (negated). One gradient read
# costs ~3.8k fuel, so the walk costs PATH_MAX_STEPS reads. The cap must
# cover the LONGEST honest path on the patched pool (measured 163 path
# steps on the maze maps) - the S2.3 first cut used 64 and every long-path
# map failed to move at all.
PATH_MAX_STEPS: int = 200
# S2.3.3: how many of the walked path's tiles are re-read to re-anchor a
# champion that left the path by one tile (a refused step that took the
# downhill alternative). The path is ordered goal-first, so the scan runs
# backwards from our own tile; 12 tiles is enough for a one-tile detour
# and bounds the cost of the scan (~2 coordinate reads per tile).
PATH_REANCHOR_READS: int = 12
# The exposure probe walks at most PATH_EXPOSURE_STEPS gradient steps back
# from the goal (the tiles where the destination's monsters wait).
PATH_EXPOSURE_STEPS: int = 24
# How many top-scoring candidates get a path-exposure read per turn.
PATH_EXPOSURE_CANDIDATES: int = 2
# Monster attack range (GameRules: "All monsters have attack range 3").
MONSTER_RANGE: int = 3

# ---------------------------------------------------------------------------
# S2.3 - boss graduation (excluded groups become scored fight candidates).
# ---------------------------------------------------------------------------
# Reachability-delta precompute (unlock value): the shared flood radius and
# the cap on how many excluded groups get a precomputed value (fuel bound).
# Closed and open floods MUST use the same root and radius; otherwise the
# comparison measures two different regions instead of one door's delta.
UNLOCK_RADIUS: int = 256
UNLOCK_MAX_GROUPS: int = 6
# Gem-equivalent of a banking point that opens up behind a door (an altar).
ALTAR_UNLOCK_VALUE: float = 400.0
# Gold -> gem-equivalent conversion (a shard costs 30 gold).
#
# S2.3.2 (2026-10-26): lowered from 0.05 to 0.02, i.e. 50 gold per gem
# instead of 20. Gold is the SECOND tiebreak (GameRules "compare tributed
# gems, then lifetime gold earned") and its only sink is the shard shop,
# which can only run while a champion stands on a spawn tile - so gold is
# a DELAYED, RATE-LIMITED conversion into combat power, while a gem is
# the win condition itself (1,000 tribute) and the FIRST tiebreak.
#
# The old rate priced a 600-gold row of 30 gold clusters at 30
# gem-equivalents on 01yellow_brick_road - more than every real gem
# cluster on that map (26-45 gems, and each of those is discounted by the
# survival term). The argmax therefore walked 29 tiles across a
# 30-camp row of ZERO gems instead of the gem clusters: 142 gems
# collected, 87 of them destroyed by 35 deaths, 46 submitted, and the map
# lost. At 0.02 the same row is worth 12 gem-equivalents, the gem
# clusters win the argmax, and the bot collects 310 and submits 263
# (measured, --release, one clean build each).
#
# The rate is NOT a map-specific knob: 01gold_rush improves (639 -> 710
# gems) and the gold-tiebreak margin on 00stampede is preserved
# (+978 -> +938 gold earned over the opponent), so the correction does
# not trade a gold economy for a gem economy elsewhere.
GOLD_TO_GEM: float = 0.02
# Expected allies piling into a boss fight (team-stat estimate; the 8-byte
# channel carries no strength data).
ASSISTED_DPS_FACTOR: float = 3.0

# ---------------------------------------------------------------------------
# S2.3 - survival, fight and flee model.
# ---------------------------------------------------------------------------
FIGHT_WIN_FLOOR: float = 0.15      # PLACEHOLDER. Floor of the fight win probability.
# Flee candidate (weak + enemy in range -> move away, then recall).
FLEE_UTILITY: float = 120.0        # PLACEHOLDER.
FLEE_HP_FRAC: float = 0.35         # PLACEHOLDER. At/below this HP fraction, flee instead of fighting.
# Recall candidate (S2.3): heal value and gold-to-spend value, both in
# gem-equivalent units. The heal term is active only below RECALL_HP_FRAC;
# the gold term is active only at/above RECALL_GOLD_TRIGGER.
RECALL_HEAL_VALUE: float = 10.0   # PLACEHOLDER. Gem-equivalent of a full heal.
                              # S2.3.1 RE-TUNE (measured 2026-10-24): the old
                              # 2000 was on the pre-S2.3 score scale (the flat
                              # Stage 3 numbers), not on the gem scale every
                              # candidate now uses - a recall scored 700 at 100
                              # tiles and outranked every farm candidate, so
                              # the bot walked home from the far side of the
                              # map at the first scratch. 10 gem-equivalents
                              # is about one good camp: the recall wins the
                              # argmax when the survival term of the farm
                              # candidate has already priced the low HP in
                              # (at 20% HP the farm score falls by the
                              # P(survive) term), not before.
RECALL_GOLD_VALUE: float = 0.05    # Gem-equivalent value of one held gold (a shard costs 30 gold).
RECALL_GOLD_TRIGGER: int = 150     # PLACEHOLDER. Gold held before the spend term becomes active.
RECALL_GOLD_CAP: int = 300         # PLACEHOLDER. Gold above this is not worth a trip.
RECALL_INTERRUPT_RISK: float = 0.5 # PLACEHOLDER. P(channel survives) with an enemy in range.
# Engine-probed camp design table (CLI 3.0.4 / engine 1.0.2).  The table is
# read from the engine's own `camp.base_stats()` (CampStats), NOT from
# ref/GameRules.md: the engine's design table and the document disagree.
#
# Re-probed 2026-10-24 on 00road_of_riches (51 groups, SIX camp kinds):
#   kind  base HP  strength  attack range
#     0      1350        20             3    (wolf)
#     1      2400        60             4    (elk)
#     2       120         0             3    (gem cluster)
#     3       120         0             3    (gold cluster)
#     4     20000       200             5    (monolith)
#     5      5000       120             4    (the "big" monster - NOT in
#                                               ref/GameRules.md's table)
#
# S2.3.4 FIX: kind 5 was MISSING from the table. Its strength read back as
# 0, so every group of big monsters had total_strength == 0, its
# P(survive the clear) came out as 1.0, and the boss candidate was scored
# as a free 5000-HP prize. On 00road_of_riches the bot walked 43 tiles to
# a kind-5 pack on every turn, killed nothing, banked nothing (0 gems,
# 0 kills) and burned 4,471 fuel-exhaustion events. With the real
# strength (120) the boss prices itself out BY ARITHMETIC, which is the
# mechanism the S2.3 done-when requires (no exclusion gate).
#
# The group values are the sum over the group's camps. The HP table feeds
# the killable filter and the clear-time estimate; the strength table
# feeds the threat model and the survival terms.
CAMP_BASE_HP: dict[int, float] = {
    0: 1350.0, 1: 2400.0, 2: 120.0, 3: 120.0, 4: 20000.0, 5: 5000.0,
}
MONSTER_STRENGTH: dict[int, float] = {
    0: 20.0, 1: 60.0, 2: 0.0, 3: 0.0, 4: 200.0, 5: 120.0,
}
# The monster's own attack range (the engine's CampStats.base_attack_range).
# The S2.3 threat model prices a threat by ITS range, not by a flat 3.
MONSTER_RANGE_BY_KIND: dict[int, int] = {
    0: 3, 1: 4, 2: 3, 3: 3, 4: 5, 5: 4,
}

# ---------------------------------------------------------------------------
# S2.3 - candidate weights and cheap-turn guards.
# ---------------------------------------------------------------------------
# S2.3 diagnostics: when S23_TRACE is true the bot prints a per-round trace
# line (the chosen candidates, their scores and notes, the goal tiles and
# the remaining fuel). It is OFF by default - the trace is a development
# aid, and every printed line costs fuel.
S23_TRACE: bool = False
# How many rounds the trace covers.
S23_TRACE_ROUNDS: int = 40
# S2.3: the capture candidate is scored in gem-equivalents like every other
# candidate: the group's real value x discount^(d + CAPTURE_TURNS). The old
# flat CAPTURE_UTILITY=1200 dwarfed every farm/bank score, which made the
# argmax a constant and hid the real trade-off (capture now, or farm more).
# CAPTURE_TURNS is the engine's capture timer (constants.CAPTURE_TIME,
# resolved into CAPTURE_ROUNDS at runtime).
# S2.3: a capture in progress is COMMITMENT - leaving the tile resets the
# engine's five-round capture timer (GameRules "After defeating a camp"), so
# a capture we have already started outranks the alternatives.
CAPTURE_COMMIT_FACTOR: float = 2.0
# S2.3 fuel: the radius (in Chebyshev tiles) around us in which a camp
# group's state is read. The map's vision radius is 8 tiles, and the
# engine's state read needs one of the group's spawn tiles in vision, so a
# radius of 8 is exactly the set of groups the read can answer. A wider
# radius only buys failed reads (each ~28k of fuel).
CLEAR_SCAN_RADIUS: int = 8
# How many reward-available groups get a scored capture candidate per turn.
CAPTURE_MAX_CANDIDATES: int = 3
# How many excluded (boss) groups get a scored fight candidate per turn.
BOSS_MAX_CANDIDATES: int = 3
# Tiles the flee step tries to put between us and the enemy.
FLEE_STEP: int = 3
# Fuel guards: the optional per-turn reads (boss engagement field) are
# skipped when the turn's fuel is nearly spent. The scoring field itself is
# never skipped - every candidate's distance depends on it.
BOSS_FIELD_MIN_FUEL: int = 400_000
# S2.3 fuel: the unlock precompute is a first-turn topology cost (several
# radius-256 floods). It only runs when the turn can afford it.
UNLOCK_MIN_FUEL: int = 2_500_000

# Enemy fog memory (intelligence manager). Rounds a last-known sighting stays
# "fresh" in recent_sightings(); 60 = one respawn cycle (GameRules death rules).
ENEMY_TRACK_TTL: int = 60             # PLACEHOLDER. Rounds before a sighting goes stale.

# S2.1 shared-memory signals. An enemy within this Chebyshev radius of any
# altar sets the altar-contested flag in the owner's byte. Source: GameRules
# altar locks are placeable within 2 tiles of an altar — that is the contest
# reach worth signaling.
SIGNAL_ALTAR_CONTEST_RADIUS: int = 2  # PLACEHOLDER.

# S2.2 intel consumers (facts folded by intelligence.fold_ally_intel).
# Bank-score multiplier for a contested altar (beacon altar flag / own
# sighting within SIGNAL_ALTAR_CONTEST_RADIUS). Score-down, not skip: when
# every altar is contested, banking must still happen.
BANK_CONTEST_PENALTY: float = 0.5   # PLACEHOLDER. ~7 tiles of TIME_DISCOUNT distance.
# Per-sighting multiplier on bank/farm scores in a region (architecture.md
# "crude constant per region ... estimated later" — S2.2 is the estimate).
# Tuning history (S2.2 A/B, stage ratchet vs S2.0's 16-24/13,392):
#   0.7 -> 14-26/12,977: avoidance too strong — farm redirect steered the
#   bot off enemy-trafficked GEM economies into safe gold clusters
#   (01gold_rush: +110 camps, 33 gems, ~11.7k gold held); bank delivery
#   intact everywhere (no hoard: collected==submitted).
REGION_RISK_FACTOR: float = 0.85   # PLACEHOLDER. ^count; ~2 discount tiles per sighting.

# ---------------------------------------------------------------------------
# B1 - enemy-dominance economics (the fight-back / courier-hunt model).
# ---------------------------------------------------------------------------
# Owner ruling 2026-10-26 (plan/tasks.md B1): a kill is worth far more
# than the flat 50 gold the old fight candidate scored, because the killer
# takes ALL the victim's carried gems AND the dead champion cannot farm,
# capture or tribute for the whole respawn window.
#
# The values below are GEM-EQUIVALENTS, the same unit as every other
# candidate score (the utility layer's contract).
#
# (a) IMMEDIATE - the bag (the victim's carried gems, counted 1:1) plus
#     the kill gold. The gold is priced at the same rate as every other
#     gold (GOLD_TO_GEM: 50 gold buys one gem-equivalent of shard stats).
FIGHT_GOLD_VALUE: float = 0.02
# (b) DENIAL - a dead champion cannot farm, capture or tribute for
#     RESPAWN_ROUNDS (60). Measured on our own record: the bot banks
#     ~1,000 gems over a 2,000-round match with five champions, i.e.
#     ~200 gems per champion, so one champion's 60-round respawn window
#     is worth ~6 gems of foregone production. 8.0 is that figure with a
#     small margin (the dead champion also stops compounding gold into
#     shards, and their claim memory expires).
FIGHT_DENIAL_VALUE: float = 8.0
# (c) CONTESTED ACCESS - an enemy standing on our cleared camp tiles
#     resets the engine's five-round capture timer (GameRules capture
#     rules), so removing it protects the farm cycle we already paid for.
FIGHT_CAPTURE_RESET_VALUE: float = 3.0
# Chebyshev radius around our capture tiles in which an enemy threatens
# the capture.
FIGHT_CAPTURE_RADIUS: int = 2
# (d)+(e) SNOWBALL and the kill tiebreak (gems -> gold -> kills). Small,
# but a kill is also a tiebreak point once the gem and gold scores are
# level.
FIGHT_TIEBREAK_VALUE: float = 1.0
# The denial value is scaled down when the match is nearly over: a kill in
# the last few rounds denies nothing (the window is truncated by the end
# of the match).
FIGHT_DENIAL_END_ROUNDS: int = 2000
# Chase (courier hunt): a loaded enemy is worth walking to. The enemy's
# bag must clear the bar, the walk must be short, and the candidate pool
# is capped (the S2.3 fuel history: candidate floods burn the budget).
CHASE_MIN_BAG: int = 8           # gems the target must carry
CHASE_MAX_TURNS: int = 12        # path turns we are willing to spend
CHASE_MAX_CANDIDATES: int = 3
# The target may keep moving, so the chase is not certain to land.
CHASE_SUCCESS_FACTOR: float = 0.6
# Threat term (the "never mid-farm-inert" rule). A hostile champion within
# THREAT_PURSUE_TILES of its own attack range is priced as incoming
# damage on the action we are scoring: farming or banking while someone
# is hitting us is worth what we actually collect, which is nothing if we
# die on the way.
THREAT_PURSUE_TILES: int = 2     # tiles beyond their attack range
THREAT_PURSUIT_TURNS: int = 3    # rounds a closing enemy is priced for
# Flee (S2.3, re-scaled by B1): the old score was ~1 gem-equivalent, so
# the escape could never win the argmax and the bot farmed to its death.
# The score is the value of what we save, scaled by how hurt we are.
FLEE_BASE_VALUE: float = 6.0     # the ground we give up (about one camp)
FLEE_HP_FLOOR: float = 0.4       # the score multiplier at full health
# Escape probability by the enemy's reach: a RANGED champion must choose
# between moving and attacking (GameRules: ranged champions cannot move
# and basic-attack in the same turn), so a champion that keeps walking
# breaks contact; a MELEE champion walks and attacks in the same turn and
# cannot be shaken off.
FLEE_ESCAPE_RANGED: float = 1.0
FLEE_ESCAPE_MELEE: float = 0.4

MONOLITH_HP: int = 20_000         # GameRules camp table (Monolith total HP).

# Per-champion stat priority used when shopping on spawn tiles.
CHAMPION_STAT_PRIORITY: dict[str, str] = {
    # Keys are short names as returned by ChampionType.to_string() (uppercase, underscored).
    "RYTH": "HEALTH",
    "STELLAR": "FOCUS",
    "VESPER_VALE": "STRENGTH",
    "VARG": "STRENGTH",
    "WILLOW": "FOCUS",
    "VENDETTA": "STRENGTH",
    "PRISM": "FOCUS",
    "MARCELLA": "STRENGTH",
    "REN": "FOCUS",
    "TAVI": "FOCUS",
    "CASSI": "STRENGTH",
    "ISSAC": "STRENGTH",
}

_POWER_SHARD_BY_STAT: dict[str, str] = {
    "STRENGTH": "POWER_SHARD_OF_STRENGTH",
    "FOCUS": "POWER_SHARD_OF_FOCUS",
    "HEALTH": "POWER_SHARD_OF_HEALTH",
}

_STAT_INDEX: dict[str, int] = {"STRENGTH": 0, "FOCUS": 1, "HEALTH": 2}

# ---------------------------------------------------------------------------
# S2.4 - shopping utility: per-champion emblem family.
# ---------------------------------------------------------------------------
# GameRules "Emblem families": five matching shards combine into an emblem
# and ONLY the completed emblem grants the passive:
#   MONSTER    +10% damage to camps
#   POWER      six shards' worth of the chosen stat (no separate passive)
#   DURABILITY -10% incoming damage
#   TREASURE   +20% gold gained (ADDS to Vesper Vale's 33% -> 53%)
#   JUNGLE     -30% damage taken from camps
#   HOME       50% shorter recall (5 turn ends -> 3)
#   BRUISER    a spread of stats
#
# The FAMILY picks the passive; the STAT variant still comes from the
# existing CHAMPION_STAT_PRIORITY map, so there is exactly ONE mapping
# (config.power_shard_for) and ONE buyer (bot.buy_items).
CHAMPION_EMBLEM_FAMILY: dict[str, str] = {
    # Melee tanks / farmers: MONSTER (+10% camp damage = faster clears) or
    # DURABILITY (-10% incoming damage = survive the pack).
    "RYTH": "MONSTER",        # melee tank, area stun; camps are its economy
    "VARG": "MONSTER",        # splash melee, farms packs
    "MARCELLA": "DURABILITY",  # stun-immune tank (tribute gold passive)
    "REN": "DURABILITY",      # retaliation tank
    "WILLOW": "DURABILITY",   # healer: keep her alive
    # The economy engine: Vesper's 33% gold + Treasure's 20% = 53%.
    "VESPER_VALE": "TREASURE",
    # Kill-reset farmer: camp damage is her leverage.
    "VENDETTA": "MONSTER",
    # Ranged casters: POWER (six shards' worth of the chosen stat).
    "STELLAR": "POWER",
    "PRISM": "POWER",
    "TAVI": "POWER",
    "CASSI": "POWER",
    "ISSAC": "POWER",
}

# ---------------------------------------------------------------------------
# S2.4 - shopping scoring (gem-equivalents, like every other candidate).
# ---------------------------------------------------------------------------
# A shard is a PERMANENT stat gain for the rest of the match, while gold
# held earns NOTHING (the shop is the only sink and the shop only runs on
# a spawn tile or while dead), so the shard side of the trade is strictly
# positive - the only question is how much gold to keep back.
SHARD_STAT_VALUE: float = 1.0
# Completing an emblem (five shards of a family + stat) grants the family
# passive for the whole match - the permanent-stat snowball.
EMBLEM_COMPLETION_VALUE: float = 3.0
# The consumable reserve: gold kept back for an altar lock (10g) or a
# teleport crystal (10g). A shard is bought only when the gold covers the
# shard AND this reserve.
CONSUMABLE_RESERVE_GOLD: int = 20
# The reserve's option value in gem-equivalents (the denial value of a
# lock / the reach of a teleport). Below SHARD_STAT_VALUE, so a shard
# beats the reserve as soon as the gold covers both.
CONSUMABLE_RESERVE_VALUE: float = 0.5
# Shards needed for one emblem (GameRules: five matching shards combine).
EMBLEM_SHARD_COUNT: int = 5

# ---------------------------------------------------------------------------
# S2.4 - altar locks (the denial consumable).
# ---------------------------------------------------------------------------
# An altar lock costs 10 gold, is placed within two tiles of an altar and
# blocks the OPPOSING team's tributes until three basic attacks clear it
# (GameRules "Consumables"). The owner team can still pay tribute through
# its own lock (GameRules :237), so our locks never block our bankers.
ALTAR_LOCK_COST: int = 10
# The placement radius of the lock around the altar (GameRules: "within
# two tiles").
ALTAR_LOCK_RADIUS: int = 2
# The denial value of a lock (gem-equivalents): it removes an altar from
# the enemy's banking options for as long as they do not clear it. It is
# only worth anything when the intel shows ENEMY TRAFFIC at an altar -
# otherwise it is 10 gold of tuition (the task's "accept some wasted
# placements as tuition until the prior sharpens").
LOCK_DENIAL_VALUE: float = 2.0
# The traffic prior (the intel that makes a lock worth its 10 gold):
#   DIRECT  - an enemy is at/near the altar (own vision or an ally's
#             fresh beacon flag): the enemy is USING this altar.
#   REGION  - a fresh sighting in the altar's region (broadcast intel):
#             the enemy is in the neighbourhood, not necessarily at it.
#   FOG     - our own last-known enemy position is near the altar.
# A lock is only bought when the traffic prior is non-zero.
LOCK_TRAFFIC_DIRECT: float = 3.0
LOCK_TRAFFIC_REGION: float = 1.0
LOCK_TRAFFIC_FOG: float = 1.0
# How near a remembered enemy must be to count as fog traffic at an altar.
LOCK_FOG_RADIUS: int = 4


def _short_name(champion_name) -> str:
    """Normalise a champion name to its short form ('RYTH')."""
    if isinstance(champion_name, str):
        if "." in champion_name:
            return champion_name.split(".", 1)[1]
        return champion_name
    return str(champion_name)


def emblem_family_for(champion_name) -> str:
    """The emblem family this champion builds (GameRules emblem families).

    Unknown champions (the engine's random substitutes, e.g. the new
    HEART_OF_RUIN) fall back to POWER: six shards' worth of the chosen
    stat, the family with no situational requirement.
    """
    return CHAMPION_EMBLEM_FAMILY.get(_short_name(champion_name), "POWER")


# ---------------------------------------------------------------------------
# S2.4 - ward mini-argmax (the free parallel action).
# ---------------------------------------------------------------------------
# The ward's own vision radius and its placement radius are ENGINE
# constants (cambc.constants.WARD_VISION_RADIUS / WARD_PLACEMENT_RADIUS);
# the defaults below are the documented values (6 and 2) and are replaced
# by the engine's own numbers in resolve().
WARD_VISION_RADIUS: int = 6
WARD_PLACEMENT_RADIUS: int = 2
# Information-gain weights (the ward's score is the sum of the covered
# interesting tiles). Camps first: the map's gem economy is the thing
# worth seeing; enemy-side altars second (they are where the enemy
# tributes); the objective path last (we walk it anyway).
WARD_CAMP_WEIGHT: float = 3.0
WARD_PATH_WEIGHT: float = 1.0
WARD_ALTAR_WEIGHT: float = 2.0
# How many placeable tiles are checked for legality per round (the top N
# by the coarse score). The candidate set is the 5x5 box around us, so
# this bounds the whole per-turn cost of the argmax.
WARD_CANDIDATE_CAP: int = 5
# The trinket charge: the rounds between two ward placements (the engine
# constant WARD_COOLDOWN, resolved in resolve()). bot.py mirrors it
# locally so the common turn costs one comparison, not an engine read.
WARD_COOLDOWN_ROUNDS: int = 1

# Cached cambc.constants lookups, lazy to avoid the wasm preload.
_resolved = False
TRIBUTE_RANGE: int = 0
CAPTURE_ROUNDS: int = 0
RESPAWN_ROUNDS: int = 0
KILL_GOLD_ON_DEATH: int = 0


def resolve() -> None:
    """Resolve cached engine constants exactly once. Safe to call repeatedly."""
    global _resolved, TRIBUTE_RANGE, CAPTURE_ROUNDS, RESPAWN_ROUNDS
    global KILL_GOLD_ON_DEATH, WARD_VISION_RADIUS, WARD_PLACEMENT_RADIUS
    global WARD_COOLDOWN_ROUNDS
    if _resolved:
        return
    import cambc.constants as _constants
    TRIBUTE_RANGE = int(_constants.SUBMIT_GEMS_RANGE)
    CAPTURE_ROUNDS = int(_constants.CAPTURE_TIME)
    RESPAWN_ROUNDS = int(_constants.RESPAWN_TIME)
    KILL_GOLD_ON_DEATH = int(_constants.KILL_GOLD)
    try:
        WARD_VISION_RADIUS = int(_constants.WARD_VISION_RADIUS)
    except Exception:
        pass
    try:
        WARD_PLACEMENT_RADIUS = int(_constants.WARD_PLACEMENT_RADIUS)
    except Exception:
        pass
    try:
        WARD_COOLDOWN_ROUNDS = int(_constants.WARD_COOLDOWN)
    except Exception:
        pass
    _resolved = True


def stat_index(champion_name: str) -> int:
    """Index of this champion's priority stat (0=strength, 1=focus, 2=health)."""
    return _STAT_INDEX.get(CHAMPION_STAT_PRIORITY.get(champion_name, "STRENGTH"), 0)


def power_shard_for(champion_name: str):
    """Return the shard this champion buys: its family's shard of its stat.

    S2.4: extended from the POWER-only mapping to the per-champion family
    map (CHAMPION_EMBLEM_FAMILY) - the FAMILY picks the emblem passive and
    the existing CHAMPION_STAT_PRIORITY picks the stat variant. One buyer
    (bot.buy_items), one mapping (here).

    Accepts the short name ('RYTH') or the SDK's prefixed form
    ('ChampionType.RYTH'); strips the prefix.
    """
    import cambc
    name = _short_name(champion_name)
    family = CHAMPION_EMBLEM_FAMILY.get(name, "POWER")
    stat = CHAMPION_STAT_PRIORITY.get(name, "STRENGTH")
    attr = f"{family}_SHARD_OF_{stat}"
    try:
        return getattr(cambc.ItemType, attr)
    except AttributeError:
        return getattr(cambc.ItemType, _POWER_SHARD_BY_STAT.get(stat, "POWER_SHARD_OF_STRENGTH"))

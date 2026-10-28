# Vecticons — Build Spec

**Event:** Cambridge Battlecode 2026a (`cambc2026a`) · the project targets the ladder and the contest.
**Repo:** Vers-AI/Battlecode-2026--Vecticons
**Toolchain:** `cambc` CLI **4.0.0** / engine **2.0.0** (`pip install cambc==4.0.0`). `cambc.toml` pins `sdk_version = "4.0.0"`, which is what the bot is compiled against. Engine build hash on this line: `85aa7167b44db7cea97509f55e997750520e33d644e9f4f90d6128c50eba1d21`.
**Map pool:** **70** maps in `maps/` (Patch-4: the kept-40 sha-unchanged, 30 new sha-verified). `.cambc/map-pool.json` lists them.
**Game rules:** `ref/GameRules.md`, `ref/BotAPIPython.md` (repo). Every number below cites those files.
**Vault mirror:** the owner's Obsidian note "Vecticons Bot Architecture (Battlecode 2026a)" mirrors this file; this copy is canonical for coding work. **Re-sync the mirror after any change here.**
**Doc status:** synced 2026-10-28 to the shipped S2.4 + F1-closeout state, and re-synced the same day to the updated `ref/GameRules.md` (kill gold 100, ward charge 10 turn ends, lock = 5 attacks, draft turn 100× fuel, the Golem, Heart of Ruin, the revised emblem passives). Sections that describe Sprint 1 are kept as a historical record and labelled as such; everything else describes what the bot does today.

---

## Core design: utility theory as the decision engine

The whole game is denominated in one currency (gems), so the bot's core loop is: **score every candidate action in gem-equivalents-per-turn, take the argmax.**

- Score = value ÷ (turns to realize) — time-discounted utility
- Carried-gem risk: value of farming longer vs. risk of dying and losing the carry → principled banking thresholds, not constants
- Region risk: gems × P(survive) — per-region constant, estimated later

**Key architectural claim:** if each agent maximizes well-formed local utility, synergy emerges *without* coordination. The draft plan pre-commits assignments; geometry deconflicts greedy agents. The 8-byte channel stays nearly silent — a feature, not a failure.

**What the currency buys us.** Every score in the system is in *gem-equivalents*, so candidates from different subsystems are directly comparable: 100 gold of kill reward is `100 × GOLD_TO_GEM`; a shard's permanent stat gain is `SHARD_STAT_VALUE`; an altar lock's denial value is `LOCK_DENIAL_VALUE`. Nothing is compared across units.

---

## Manager architecture

| Manager | Module | Runs | Job |
|---|---|---|---|
| **Draft** | `draft.py` | once, pre-match | Pick champions; write the 1,024-byte plan; precompute the map cache (camps, altars, spawns, distances) with full visibility + 100× fuel |
| **Observation/State** | `managers/observation.py` | per-turn, cheap | Assemble facts: HP, gold, carried gems, position, vision, draft cache. No decisions. |
| **Utility/Decision** | `managers/utility.py` | per-turn | Generate candidate actions, score each, argmax |
| **Execution** | `managers/execution.py` | per-turn | Chosen action → SDK calls (`reachable_tiles()`, `movement_mask()` — never hand-rolled A*) |
| **Fielding** | `managers/fielding.py` | per-turn | The shared `DistanceField` infrastructure — honest distances, two wall sets |
| **Intelligence** | `managers/intelligence.py` | per-turn + persistent | Enemy fog memory; the shared-memory beacon protocol |
| **Scouting** | `managers/scouting.py` | per-turn | The ward mini-argmax (a free parallel action) |
| **Topology** | `managers/topology.py` | draft-time | Static reachability analysis: boss unlock values, the draft-memory unlock section |

Shopping, combat, wards and locks live as **rules inside the utility function** and were promoted to their own modules only once they earned opinions of their own (shopping and wards became scored candidates in S2.4; the beacon protocol became a module in S2.1).

**Build order (historical):** draft manager + map cache → state manager → hardcoded baseline loop (the measurable floor) → the utility layer, A/B against the baseline.

---

## File structure

The wasm sandbox resolves imports **only within `src/`**. Verified empirically: sibling folders at bot root raise `ModuleNotFoundError` even with `sys.path` insertion; subfolders inside `src/` work.

```
bots/vecticons/
  src/
    main.py            # entry point. Dispatch: is_draft() -> draft.py, else bot.play_turn(). Calls cambc.end_turn().
    draft.py           # draft-phase picks + draft memory write (v2 payload, incl. the boss unlock section)
    bot.py             # per-champion turn logic — ALWAYS the shipped implementation
    bot_baseline.py    # frozen copy of the Stage 2 baseline, kept for A/B (created in Stage 3)
    config.py          # ALL tunable constants. No magic numbers in any other file.
    randomness.py      # seeded PRNG (WASI randomness is zero)
    managers/
      __init__.py
      observation.py   # per-turn state assembly (facts only, no decisions)
      utility.py       # candidate action scoring, argmax
      execution.py     # action -> SDK calls (SDK pathing helpers only)
      fielding.py      # shared per-turn DistanceField infrastructure (S2.3)
      intelligence.py  # enemy fog memory + the S2.1 beacon protocol
      scouting.py      # the ward mini-argmax (S2.4)
      topology.py      # static reachability / boss unlock values (S2.3)
  pyrightconfig.json   # keep: extraPaths ../../sdk/python
```

Import rule: `from managers.utility import ...` (src-relative) only. No imports from bot root.

---

## Stage record — Sprint 1 (complete, locked 18 Oct 2026)

Kept for reference: the frozen definitions each stage shipped. **This is not the current behaviour** — see the sections below.

### Stage 0 — Draft memory format v1 (frozen)

Little-endian, written by `draft.py` during the draft phase, max 1,024 bytes.

```
offset  size  field
0       1     format version = 1
1       1     pick count (from cambc.draft.team_size())
2       1     shared-memory bit map: bit i = champion i owns shared-memory byte i
3       1     reserved 0
4       7*n   per pick (n = pick count):
              +0   champion id (ChampionDraft enum value)
              +1   assigned altar index (0-based, game_map.altars() order)
              +2   camp zone id (nearest-camp-to-spawn split computed at draft)
              +3   role id (0=clearer, 1=banker, 2=support)
              +4-6 reserved 0
```

### Stage 1 — Draft manager

Pick order: **Ryth → Stellar → Vesper Vale**; if `team_size() == 5`, append **Varg, then Willow**. No other conditional logic. Precompute camp group tiles, altar positions, per-zone camp lists at draft (full map visible, 100× fuel) into the Stage 0 layout.

*Done when:* a match runs on the whole pool, zero exceptions in team stdout; `team_size()` logged during draft; draft memory ≤ 1,024 bytes; `cambc build --release` passes.

### Stage 2 — Observation manager + baseline loop

`observation.py` assembles per-turn facts. `bot.py` ran the baseline loop: skip if channeling/dead; shop on spawn; recall below `RECALL_HP_FRAC`; attack an enemy in range; bank at `gem_count ≥ BANK_THRESHOLD`; else farm the nearest killable camp.

**Killable camp (amended 2026-10-21):** a camp group whose monsters' total HP ≤ `KILLABLE_HP_CAP`, excluding Monolith (20,000 HP). Visible groups are gated by current HP; **unseen groups are gated by cached static base HP** (draft-precomputed into `group_meta`, engine-handle keyed) — the gate must not depend on who can see the group. The filter binds ALL attack paths, including the opportunistic in-range attack.

*Done when:* 10 named maps, zero exceptions, and the stats output shows ≥1 completed clear→capture→tribute cycle per match.

**The stats.json schema (probed 2026-10-17, re-verified on engine 2.0.0):** top-level keys `championTimelines`, `champions`, `diagnostics`, `events`, `formatVersion`, `heatmaps`, `jsonFormatVersion`, `match`, `roster`, `teamTimelines`, `teams`.

- `match.{roundsCompleted, outcome, seed, width, height, engineVersion, engineBuildHash, mapHash, …}` — `outcome` ∈ `teamA|teamB|draw`
- `diagnostics.botFaults.{teamA, teamB}` — must be `0` both sides
- `diagnostics.codes` — engine error codes
- `diagnostics.detailedCaptureComplete` — events/timelines are only complete when `True`
- `teams[i].submittedGems` — **a string int**; cast at parse
- `roster.champions[i].{id, name, team, draftPick}`, `roster.draft`
- `champions[i].metrics.{collectedGems, deaths, earnedGold, kills, submittedGems, …}` (all strings)
- `events[]` — `{kind, owner:{id,kind,name,team}, entries:[{amount,context,target,timestamp,value,x,y}]}`; observed kinds: `death, kill, assist, wardPlacement, buyItem, buyConsumable, sellItem, campClear, gemSubmission, gemCollection, gemLoss, itemCombination, botFault, fuelExhausted, resignation`

### Stage 3 — Utility layer

The baseline was frozen as `bot_baseline.py`, then `bot.py` became candidate generation + argmax. The Stage 3 candidate gate (exclude Monolith and over-cap packs from candidacy) was **superseded by S2.3** — see *Candidate model* below: excluded groups are now *scored* fight candidates that price themselves out by arithmetic rather than by a gate.

---

## Draft memory (current)

The payload is the frozen Stage 0 v1 header plus pick records, **plus an S2.3 extension**:

```
0       1     format version = 2   (topology.DRAFT_MEMORY_VERSION; 1 when S2.3 is off)
1       1     pick count
2       1     shared-memory ownership bit map
3       1     reserved 0
4       7*n   pick records (as Stage 0)
4+7n    ...   boss unlock section (S2.3): a count byte, then per-group
              (group key + reachability-delta value) for the excluded groups
```

The unlock section is computed **once per draft process** and cached at module level — the S2.1 fix. Re-deriving it per pick is what exhausted the draft fuel wall on ~15/40 maps.

The section is skipped when the draft turn is out of fuel (`_UNLOCK_MIN_FUEL`), degrading to an empty section rather than failing the draft. **Always pick before fuel exhaustion** — a missing pick is a random selection by the engine.

---

## Shared memory — the S2.1 beacon protocol

The 8-byte shared channel is live. Byte *i* is owned by champion *i* (ally draft order); write your byte only, read all.

```
byte i:  bits 0-3  region id (4×4 grid)
         bit  4    altar-contested
         bits 5-7  freshness (~8-round buckets, ~55-round horizon ≈ ENEMY_TRACK_TTL)
```

The read side (`intelligence.fold_ally_intel`) turns the beacons into facts: `snap.ally_signals`, per-region sighting density → `snap.region_sightings`, and the contested-altar set → `snap.contested_altars`. Broadcast region flags require freshness == 1 for attribution; an exact own-vision per-altar check backs them up.

**Known limitation (logged, not patched):** a dead writer's byte freezes until respawn. Close altar pairs share a 4×4 region, so the region flag is all-or-nothing — 0 altar-pick flips under a ×0.5 penalty.

---

## Candidate model and scoring

`utility.generate_candidates()` builds the candidate set every turn; the argmax wins; ties break by config priority order.

| Kind | Score |
|---|---|
| `farm` | `value(g) × TIME_DISCOUNT ** (d + clear_turns + capture) × P(survive the clear) × region_risk` |
| `capture` | the standing-on-a-cleared-spawn-tile hold, with a commit factor so a 5-round capture is not abandoned |
| `bank` | `gem_count × BANK_UTILITY_PER_GEM × P(survive the trip)`, scored down when the altar is contested |
| `recall` | `(RECALL_HEAL_VALUE + gold-to-spend) × TIME_DISCOUNT ** (d_spawn + RECALL_CHANNEL_TURNS) × P(channel survives)` |
| `fight` | `(bag + kill_gold + denial + capture_reset + tiebreak) × TIME_DISCOUNT ** turns × P(win \| our CURRENT health)` |
| `chase` | the same value × `TIME_DISCOUNT ** (path + turns)` × `P(win) × CHASE_SUCCESS_FACTOR` |
| `flee` | `(FLEE_BASE_VALUE + carried × FLEE_CARRIED_WEIGHT) × hp_factor × escape_probability` |
| `boss` | `(drops + unlock_value) × TIME_DISCOUNT ** (d + turns) × P(survive)` |
| `shop` | family shard vs. altar lock vs. hold — a nested mini-argmax |
| `hold` | the fallback candidate; the candidate set is never empty |

`ward` and `lock` placement are **free parallel actions**, not argmax entries — a ward spends the trinket charge, a lock placement is free once the lock is owned.

**Honest distances.** `d` comes from one shared per-turn engine `DistanceField` rooted at our position (Chebyshev, 8-directional, detours priced honestly). A target the field never reaches — sealed behind a door, or beyond the field radius — scores 0. Unreachable falls out by arithmetic, with no special case. The canonical example is `01boss_battle`: the altar behind the Monolith corridor scores 0 and the 93-courier-death loop is dead by mechanism.

**Two wall sets (owner-corrected ruling 2026-10-23):** the *scoring* field is terrain + the static overlay of excluded groups' spawn tiles (sealed doors score unreachable); the *boss-engagement* field is terrain-only with boss tiles as destinations. The overlay is precomputed once and position-independent by construction — the vision-derived variant re-created the `00ambush` bush-line oscillator.

**Real economy, not flat estimates.** `value(g)` is the group's real `gem_value()` + `gold_value()` at `GOLD_TO_GEM`. `clear_turns(g)` is the group's real clear time (static HP ÷ team damage), charged against the group's Chebyshev **span** so a 30-camp row is not priced like a 4-camp cluster. A Monolith needs hundreds of turns and prices itself out.

**Threat term.** Every non-combat candidate is multiplied by the threat factor — the expected damage along the engine-computed path (awake monsters + visible enemies + remembered enemies) folded in as P(survive). Farming or banking under attack is worth what we actually collect, which is nothing when we are killed on the way.

**Kill economics (F1, owner-reframed: dominance IS economy).** `_kill_value` prices a kill as the immediate bag + kill gold, plus the terms that compound: **denial** (a dead enemy cannot farm, capture or tribute for the 60-round respawn — priced as their own production over the absence window), **contested-access** (an enemy on a group's spawn tiles resets our capture progress), and the **tiebreak** term. Patch-4 amplified every knob (Prism 0.2→1.5 focus/gem, Vesper 20→33%, Marcella tribute gold).

**Fuel discipline (wired 2026-10-28, commit `51f81a8`).** The per-turn enemy tuple cache (`_ENEMY_TUPLE_CACHE`) holds pos/dmg/reach/hp/bag as plain ints — one engine build per turn, plain-int reads in the fight/chase/flee generators and `_fight_window`/`_kill_value` (engine re-reads only on a cache miss). The score-bounded EV prune drops a fight/chase whose gross upper bound (gain + carried bag, at P=1 semantics) cannot beat the best non-combat candidate (`_BEST_NON_FIGHT_SCORE`) **before** the field read and the EV arithmetic. Chase stickiness is shipped, not just required: `record_chase_win` records the argmax winner and the committed target's score is re-boosted by `CHASE_COMMIT_FACTOR` until it exits vision, dies, or the base score falls below `CHASE_COMMIT_MIN` — a chase that loses the argmax by a hair every other turn is an oscillator, a documented disease with three prior instances.

---

## config.py contract (live surface)

All tunables live in `config.py`; every value carries a source citation (GameRules / champion stat tables / mechanics) or a `PLACEHOLDER` tag. `PLACEHOLDER` = first-pass value, not final; **the F4 tuning campaign exists to replace every one of them.**

**Do not read `cambc.constants` at module import time** — the wasm preload phase has no engine context. `TRIBUTE_RANGE`, `CAPTURE_ROUNDS`, `RESPAWN_ROUNDS` and `KILL_GOLD_ON_DEATH` are resolved lazily via `resolve()` on first use. `KILL_GOLD_ON_DEATH` (the engine reads 100 on engine 2.0.0) is the **single source** for the kill gold in the fight candidate; `WARD_COOLDOWN` (10 owner turn ends) is the ward's trinket charge. Both constants are the documented rule values as their defaults — the engine's numbers replace them on the first `resolve()`.

### Economy and utility core
`BANK_THRESHOLD 1` · `TIME_DISCOUNT 0.99` · `BANK_UTILITY_PER_GEM 1.0` · `GOLD_TO_GEM 0.02` · `RECALL_UTILITY 200.0` · `RECALL_HEAL_VALUE 10.0` · `RECALL_GOLD_VALUE 0.05` · `RECALL_GOLD_TRIGGER 150` · `RECALL_GOLD_CAP 300` · `RECALL_INTERRUPT_RISK 0.5` · `RECALL_HP_FRAC 0.35` · `SAFETY_HP_FRAC 0.25` · `DEATH_RISK_BASE 0.05`

`TIME_DISCOUNT 0.99` is measured, not arbitrary: the old 0.9 was far too steep for a 2,000-round match (`0.9 ** 108` ≈ 1.3e-5, so every distant farm candidate scored ~0 and the bank candidate was unreachable). 0.99 means "a target 100 turns away is still worth 37% of its value", matching a 20–40 turn camp cycle.

### Farming
`FARM_VISIBLE_CAP 3` · `FARM_ZONE_CAP 3` · `FARM_CLEAR_TURNS 2` · `FARM_ZONE_UNCERTAINTY_TURNS 1` · `FARM_VISIBLE_POOL 12` · `FARM_ZONE_POOL 24` · `FARM_TILE_READS 3` · `CAPTURE_COMMIT_FACTOR 2.0` · `CAPTURE_HOLD_MAX_ROUNDS 12` · `CAPTURE_HOLD_TILES 2` · `CLEAR_SCAN_RADIUS 8` · `CAPTURE_MAX_CANDIDATES 3`

### Combat, chase and threat (F1)
`FIGHT_WIN_FLOOR 0.02` · `FIGHT_WIN_RATIO_CENTER 0.9` · `FIGHT_WIN_LOGISTIC_K 8.0` · `FIGHT_GOLD_VALUE 0.02` · `FIGHT_DENIAL_VALUE 8.0` · `FIGHT_CAPTURE_RESET_VALUE 3.0` · `FIGHT_CAPTURE_RADIUS 2` · `FIGHT_TIEBREAK_VALUE 1.0` · `FIGHT_DENIAL_END_ROUNDS 2000` · `FIGHT_DEATH_COST 10.0` · `ASSISTED_DPS_FACTOR 3.0` · `CHASE_MIN_BAG 2` · `CHASE_MAX_TURNS 12` · `CHASE_MAX_CANDIDATES 3` · `CHASE_SUCCESS_FACTOR 0.6` · `CHASE_STAKE_SHARE 0.3` · `CHASE_COMMIT_FACTOR 2.0` · `CHASE_COMMIT_MIN 0.5` · `THREAT_PURSUE_TILES 2` · `THREAT_PURSUIT_TURNS 3` · `FLEE_UTILITY 120.0` · `FLEE_HP_FRAC 0.35` · `FLEE_BASE_VALUE 4.0` · `FLEE_HP_FLOOR 0.4` · `FLEE_ESCAPE_RANGED 0.8` · `FLEE_ESCAPE_MELEE 0.3` · `FLEE_CARRIED_WEIGHT 1.0` · `FLEE_HP_WEIGHT 1.0` · `FLEE_THREAT_FALLOFF 0.5` · `FLEE_STEP 3` · `MONSTER_RANGE 3` · `MONOLITH_HP 20_000`

### Boss graduation and topology
`BOSS_MAX_CANDIDATES 3` · `BOSS_FIELD_MAX_DISTANCE 4096` · `BOSS_FIELD_MAX_GROUPS 2` · `BOSS_FIELD_MIN_FUEL 400_000` · `ALTAR_UNLOCK_VALUE 400.0` · `UNLOCK_RADIUS 256` · `UNLOCK_MAX_GROUPS 6` · `UNLOCK_MIN_FUEL 2_500_000` · `KILLABLE_HP_CAP 5_000` · `CAMP_BASE_HP` / `MONSTER_STRENGTH` / `MONSTER_RANGE_BY_KIND` (GameRules tables)

### Pathing, fuel and field
`FIELD_MAX_DISTANCE 4096` · `FIELD_MIN_FUEL 900_000` · `ALTAR_DISTANCE_READS 4` · `PATH_MAX_STEPS 200` · `PATH_REANCHOR_READS 12` · `PATH_EXPOSURE_STEPS 24` · `PATH_EXPOSURE_CANDIDATES 2`

### Intel and the beacon
`ENEMY_TRACK_TTL 60` · `SIGNAL_ALTAR_CONTEST_RADIUS 2` · `BANK_CONTEST_PENALTY 0.5` · `REGION_RISK_FACTOR 0.85`

### Shopping, emblems, locks, wards
`CHAMPION_STAT_PRIORITY` (13 champions) · `CHAMPION_EMBLEM_FAMILY` (13 champions) · `_POWER_SHARD_BY_STAT` · `SHARD_STAT_VALUE 1.0` · `EMBLEM_COMPLETION_VALUE 3.0` · `EMBLEM_SHARD_COUNT 5` · `CONSUMABLE_RESERVE_GOLD 20` · `CONSUMABLE_RESERVE_VALUE 0.5` · `ALTAR_LOCK_COST 10` · `ALTAR_LOCK_RADIUS 2` · `LOCK_DENIAL_VALUE 2.0` · `LOCK_TRAFFIC_DIRECT 3.0` · `LOCK_TRAFFIC_REGION 1.0` · `LOCK_TRAFFIC_FOG 1.0` · `LOCK_FOG_RADIUS 4` · `WARD_VISION_RADIUS 6` · `WARD_PLACEMENT_RADIUS 2` · `WARD_CAMP_WEIGHT 3.0` · `WARD_PATH_WEIGHT 1.0` · `WARD_ALTAR_WEIGHT 2.0` · `WARD_CANDIDATE_CAP 5` · `WARD_COOLDOWN_ROUNDS 10`

### Shopping rules
Only the **completed** emblem grants the family passive (GameRules:301-316); a family shard alone grants nothing beyond its stat. `config.power_shard_for` is the one mapping and `bot.buy_items` is the one buyer. Shards are bought only when the gold covers the shard **and** the consumable reserve (a lock or a teleport crystal). Shopping is legal on a spawn tile **and** during the 60-round dead window.

`EMBLEM_SHARD_COUNT 5` is the shard count that combines into an emblem, not the stat value the emblem is worth: Power's emblem *counts as* six shards' worth of the chosen stat. The engine agrees — every family's shard reports `combine_count() == 5` (Greed: 25).

### Known config smell (logged, not fixed)
`FIGHT_WIN_FLOOR` is defined twice — `0.15` at the utility section and `0.02` lower down. Python takes the later definition, so the **effective value is 0.02**; the `0.15` line is dead. `FLEE_BASE_VALUE` is also defined twice, with the same value, so it is harmless. Worth collapsing in the F4 tuning pass, which is where `config.py` is meant to be re-enumerated anyway. The chase stickiness ships as `CHASE_COMMIT_FACTOR 2.0` (the score multiplier while committed) **and** `CHASE_COMMIT_MIN 0.5` (the base-score floor below which the commitment ends) — both `51f81a8`, both PLACEHOLDER, both in the F4 re-enumeration set.

---

## Build and verification

```powershell
# Build check (compile the bot the way the platform does)
cambc build bots\vecticons --release

# One match, platform-faithful
cambc run bots\vecticons bots\starter-python --map contest --release

# Statistics
cambc run bots\vecticons bots\starter-python --map ambush --release --statistics stats.json --json

# Full-pool campaign harness
python scripts\run_pool.py --project-root <tree> --bot-a bots/vecticons --bot-b bots/starter-python
```

**`scripts/run_pool.py` defaults `--cambc` to `<project>/.venv/bin/cambc` and `scripts/one_map.py` to `/tmp/cambc304/bin/cambc` — both are Linux paths.** On any other platform, pass `--cambc` explicitly.

**The engine line matters.** The 3.0.4 CLI / 2.2.4 SDK combination could not parse the newer pool maps: 19 of 70 maps failed with `engine error 7` and the campaign reported 51/70 maps OK. A campaign that silently drops maps inflates or deflates every aggregate — one closeout validation was read as a 25% fuel saving when it was really 19 missing maps. Before trusting a campaign, confirm the CLI is 4.0.0 **and** that `match.engineVersion` in the stats file reads `2.0.0`. On the current 4.0.0 / 2.2.4→4.0.0 SDK line the pool loads and `01elk_arena` reproduces the canonical control exactly (72 gems, W, 0/0 faults).

**Verification rule (owner ruling 2026-10-26 — replaces the per-stage A/B ceremony):** ONE gated campaign before EVERY platform submission — single instrument, single host, top-line gates only (record AND aggregate gems ≥ the immediately-prior verified record · 0 botFaults both sides · fuelExhausted ≤ prior reference · `build --release` passes). Mid-work feedback is a 3-map spot-check (~2 min).

**Same-bytes comparisons only.** Compiled module memory layout depends on exact source bytes; an `id()`-based tie-break once made a cosmetic comment in a never-executed file swing five per-map results by >100 gems. Per-map deltas inside the 100-gem noise floor are meaningless when source bytes differ.

**Standing control maps** (a ratchet must not break them):
- `01yellow_brick_road` — ≥ 100 gems, a win (reference: 259 gems / W)
- `01elk_arena` — 72 gems, a win
- `01boss_battle` — 0 courier deaths at the sealed door; the bank candidate must score 0 there

**Current ratchet reference (pool-70, engine 2.0.0):** S2.4 verified **46-24 / 32,876 gems / ~11,093 fuel / 0 faults**; the F1 campaign at `dad8b55` recorded **55-15 / 38,703 gems / 22,230 fuel / 0 faults**. Evidence lives in `records/<stage>/`.

---

## Memory & vision model

- **Draft memory (1,024 B, pre-match, read-only in match):** the v2 format above. Read via `cambc.DraftMemoryBuffer()` per instance. Validate on read; fall back to safe defaults on corruption.
- **8-byte shared memory:** the *only* live channel between instances. The beacon protocol above. All champion bots agree on the layout — write your own byte, read all bytes.
- **Per-champion vision (no ally sharing):** every observation fact is local. Vision is NOT shared between allies — coordinate through the shared memory. Movement into unseen occupants returns `False` — retry another step, never raise.
- **Determinism:** WASI randomness is zero in matches; seed the PRNG from the spawn tile + champion start position. Same seed ⇒ same behaviour. Keep decision order stable.
- **Fuel budget:** Python gets ~25× the C++ limit. Budget per turn and short-circuit before out-of-fuel. Check `cambc.fuel_remaining()` before expensive planning. Bots resume from suspension next round — but do NOT rely on this: always make per-turn work idempotent.
- **First-turn budget:** 100× fuel on the draft turn, 2× on the first play turn. Cache anything reusable (camp layout union, spawn mask, altar approach tiles) on those turns — `bot.camp_layout` is the model.
- **Competition safety:** no file I/O inside `play_turn()`. Treat logging via `print` (captured to the replay, `--log-limit` capped) only. Wrap dev-only analytics in `cambc.debug_block(lambda: ...)` — the platform builds with `--release`, so debug blocks are skipped entirely. **Never put game-affecting logic inside `debug_block`.**

---

## Champion strategy notes

The bot drafts **Ryth → Stellar → Vesper Vale**, appending **Varg, then Willow** at `team_size() == 5`. `draft.py` queries `team_size()`, never hardcodes it.

**The 13 playable types**, with their stat priority and emblem family (the two maps the bot uses to shop):

| Champion | Stat | Emblem family |
|---|---|---|
| Ryth | HEALTH | MONSTER |
| Varg | STRENGTH | MONSTER |
| Vendetta | STRENGTH | MONSTER |
| Marcella | STRENGTH | DURABILITY |
| Ren | FOCUS | DURABILITY |
| Willow | FOCUS | DURABILITY |
| Vesper Vale | STRENGTH | TREASURE |
| Stellar | FOCUS | POWER |
| Prism | FOCUS | POWER |
| Tavi | FOCUS | POWER |
| Cassi | STRENGTH | POWER |
| Issac | STRENGTH | POWER |
| Heart of Ruin | STRENGTH | MONSTER |

**Deferred special cases (backlog B2).** **Prism** wants a carried-gems term in her personal utility (Patch-4 buffed her 0.2→1.5 focus/gem). **Cassi & Issac** are two linked instances — one draft pick, two bodies, linked fate — and need coordinated handling (never split far; treat as one body for engagement math). **Heart of Ruin** (the 2026-10-28 roster addition) converts missing health into damage (up to +50%), and the enemy threat model reads nominal `strength()` — so a wounded Heart of Ruin is under-priced in the fight math until the threat term scales her output. The shopping maps above already cover every champion's shard and emblem behaviour, but these *scoring* cases are not special-cased yet.

**Sustain.** Willow is the only healer; sustain = recall + shopping rules inside the utility function. Healing enters when combined-arms scoring does (backlog B1).

---

## Coordination roadmap

- **Sprint 1:** zero shared-memory traffic. Pre-assigned zones, independent greedy utility.
- **Sprint 2 (done):** write-only beacon signals ("altar contested", "enemy seen in region") in owned bits; read all.
- **Sprint 3+ (backlog B1):** combined-arms focus-fire scoring ("my utility is higher *because* Ryth is adjacent") · Willow healing/sustain · negotiation on the shared channel (only if the signals prove value) · respawn-aware camp schedules. The first two are blocked on the open question below.

---

## Telemetry plan

- **v1 (done):** `scripts/run_pool.py` — N matches via `cambc run --statistics --json`, parsed to win rate, gems tributed, camps cleared, deaths, fuel, ward/buy/consumable/emblem counters. Enough to A/B utility vs. baseline.
- **v2 (blocked):** per-round log → PIG_Bot-style viewer. Needs the schema decision in *Open questions*.

---

## Scope status

| Stage | State |
|---|---|
| Sprint 1 (stages 0-3) | Complete, locked 18 Oct 2026. All numbers measured on the pre-Patch-4 pool and the old engine — stale for gates. |
| Sprint 2 (S2.0-S2.4) | Complete. S2.0 re-baseline · S2.1 beacon · S2.2 intel consumers · S2.3 utility upgrades · S2.3.2 YBR hotfix · S2.4 scouting & shopping. |
| Final sprint F1-F5 | In execution. F1 dominance economics is in closeout (code complete, awaiting the platform-line campaign). F2 done. F3-F5 pending. |

The **live board** — stage state, gates, evidence and the overnight log — is `plan/tasks.md`. This file is the *spec*; that one is the *state*.

---

## Open questions

- OPEN — Telemetry v2 schema: how the PIG_Bot viewer ingests this game's stats (blocks the v2 telemetry item).
- OPEN — when does the utility layer earn combined-arms scoring (the "my utility is higher because Ryth is adjacent" term)? Blocks the remaining B1 items.
- RESOLVED — the stats.json field names (probed 2026-10-17, re-verified on engine 2.0.0 by the Patch-4 audit — `extract()` unchanged).

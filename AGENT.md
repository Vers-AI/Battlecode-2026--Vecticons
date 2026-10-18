
---

## About You

You are a **CAMBC (Cambridge Battlecode) bot developer** writing **Python** bots against the `cambc` SDK (WASM/WASI, Wasmtime fuel budget).

- Integrate with existing conventions (`main.py` dispatch loop, per-champion `Bot` class, `cambc.draft` + `DraftMemoryBuffer`). Keep logic **deterministic** and **fuel-safe**.
- Favor **simple, data-driven heuristics** over heavy abstractions.
- Do not over-complicate things. Make the code effective and efficient.
- Do not change strategy unless asked. Scope to the task.
- Make sure you are not creating a system that will conflict with any other existing systems.
- Write high quality professional code like a real developer.

**Creativity policy:** You may propose at most **2 gameplay ideas** under `Suggestions:` (e.g., "respawn-aware camp farming schedule," "capture-race tracker in shared memory," "altar-lock denial bot"), but only **auto-apply** if low-cost (no deps, ≤15 LOC, no new files, no config).

**Performance guardrail:** Fuel is the hard limit (~100k for C++, Python gets 25×). Never block on anything, never recompute map-wide bitboards per call — cache them (spawn tiles, camp layouts, altar approach tiles). Emit a 1-line perf note if you add any loop over tiles or champions.

---

## General

- **Project Name:** Vecticons (CAMBC 2026a)
- **Description:** Champion bots that draft, farm camps, bank gems at altars, and fight, targeting the contest/ladder.
- **Champions:** 3–5 drafted per match. 11 draftable picks (Cassi & Issac draft as one pick but play as two bodies; 12 playable types total). Our bot code runs once per champion, per turn.
- **Event:** cambc2026a (`sdk_version = "2.2.4"` in `cambc.toml`).

---

## Reference Docs (read before touching strategy)

- **[plan/architecture.md](plan/architecture.md) — THE build spec. Read fully before starting any stage.** Stage definitions, done-when criteria, config.py contract, file structure (src/-only imports).
- **[plan/tasks.md](plan/tasks.md) — stage checklist + overnight log.** Check off only on verified done-when passes; append to the log for every work session.
- [ref/GameRules.md](ref/GameRules.md) — full spec: draft, camps, altars, items, vision, champion roster with stats/kits.
- [ref/BotAPIPython.md](ref/BotAPIPython.md) — the complete Python SDK reference. Check signatures and preconditions before calling anything.
- `sdk/python/cambc/` — installed SDK sources (`constants.py`, `game_map.py`, `vision.py`, `draft.py`, `debug.py`).
- `maps/*.map26a` — map pool (17 maps; `.cambc/map-pool.json` lists them).

When the rules and your code disagree, the rules win. Cite GameRules.md section names in strategy discussions.

---

## Game Summary (what our bot must be good at)

**Win condition:** first to **1,000 tributed gems** wins immediately; at round **2,000** compare tributed gems → lifetime gold → champion kills → Team B.
**Core loop:** clear camps → capture (stand on cleared spawn tile 5 rounds) → carry gems → tribute at an altar (channel `ceil(gems/10)` turns) → spend gold on items on spawn tiles.
**Champions turn-by-turn:** each turn = at most one move, one basic attack, one ability cast; ranged champions can't move+basic-attack in the same turn (movement and attack share an allowance); melee can walk-and-attack; every ability is 8-turn-end cooldown, one cast per turn, some exclude walking.
**Map:** 30×30–100×100 grid; walls block LOS/movement/dash; holes block movement/dash but not vision; bushes hide occupants but are walkable; altars are tribute points; spawn tiles heal and allow shopping.
**Camps:** groups of monsters (Wolf/Elk/Gem cluster/Gold cluster/Monolith) that wake when damaged, attack the nearest champion every turn (range 3), and go back to sleep after two rounds without damage/an in-range champion. They respawn on timers.
**Items:** shards (30g) add growth stats; 5 auto-combine into an emblem (family passives: Monster +10% camp dmg, Durability −10% dmg taken, Treasure +20% gold, Jungle −30% camp dmg taken, Home shortens recall to 3, Power 6 shards worth, Bruiser spread). Consumables (10g, one each): Altar lock and Teleport crystal (3-turn channel).
**Vision:** 8-tile symmetric shadowcasting, blocked by walls/bushes; standing in a bush sees within the connected bush cluster. Each champion has one free ward (vision 6, place within 2 tiles, relocate every turn). **Vision is NOT shared between allies** — coordinate through the 8-byte shared memory.
**Channels:** recall (5 ticks, interrupted by damage/stun; heals at turn start if standing on spawn at turn end), tribute (ticks at turn end, completes at next turn start), teleport (3 ticks, not interrupted by damage).
**Death:** killer takes your carried gems + 50g; killed by camp loses gems; respawn at spawn tiles after 60 rounds; dead champions still shop.
**Memory:** 1,024-byte draft memory (written in draft, read-only in match) + 8 bytes shared memory (readable/writable all match, per team, hidden from opponent).

---

## Temp/Explainer Files

Remove any debug/temp files made during testing.

---

## Framework Info

`main.py` dispatches `cambc.is_draft()` → `draft.play()` (pick a champion, submit draft memory), else `bot.play_turn()`. A failed turn is logged, never crashes the process; the bot instance persists across turns (`Bot.__init__` state survives). Each champion is its own bot process — **state is per-champion**, not per-team.

Review [ref/BotAPIPython.md](ref/BotAPIPython.md) for any information you may need about the API. Key namespaces: `cambc.me()`, `cambc.vision.*`, `cambc.game_map.*`, `cambc.draft`, `cambc.draft_memory`, `cambc.shared_memory`, `cambc.constants`, `cambc.debug`.

---

## Task

We have the CAMBC 2026a starter bot (`bots/vecticons/`). We want to turn this into a highly competitive bot for the ladder/contest.

The first step is to create a plan for everything you want to do and break it down into phases in a `PLAN.md` file. It should contain every aspect of the bot — think fully through your implementation and do any research you need to.

---

## Bot Data & Performance Rules

- **Competition safety:** Any data collection must be **off by default** in competition builds. No file I/O inside `play_turn()`. Bots are WASM sandboxed — stdlib is available but disk access isn't in matches; treat logging via `print` (captured to replay, `--log-limit` capped) only.
- **`debug_block` is free (mostly) in dev, gone in release:** wrap dev-only analytics/prints in `cambc.debug_block(lambda: ...)`; the platform builds with `--release` so debug blocks are skipped entirely. Do not put game-affecting logic inside `debug_block`.
- **Determinism:** WASI randomness is zero in matches — seed our PRNG from spawn tile + champion start position (see `randomness.py`). Same seed ⇒ same behavior. Keep decision order stable.
- **Fuel budget:** Python gets 25× the C++ limit (~100k → ~2.5M fuel per turn). Budget per turn and **short-circuit before out-of-fuel**. Check `cambc.fuel_remaining()` before expensive planning (e.g., full-map DistanceField). Bots resume from suspension next round — but do NOT rely on this: always make the per-turn work idempotent.
- **First-turn budget:** 10× fuel on draft turn 1, 2× on play turn 1. Cache anything reusable (camp layout union, spawn mask, altar approach tiles) on these turns — the existing `bot.camp_layout` pattern is the model.
- **Schema/versioning:** If storing persistent state between turns (bot instance fields, shared memory), keep the layout documented and versioned; validate on read; fall back to safe defaults on corruption.
- **8-byte shared memory layout:** define the byte layout once (e.g., B0: objective ID, B1–B3: round-stamped counter, B4–B7: packed coordinates) and document it at the top of `bot.py`. All champion bots must agree on the layout — write, don't read-then-modify blindly (all allies write the same convention).
- **Explain when useful:** If adding a new data structure, include a 2-3 sentence note: purpose, update frequency, and read path.

---

## Acceptance Criteria for Data Tasks

- [ ]  No I/O in the turn loop except `print` (captured, capped).
- [ ]  Deterministic: same seed ⇒ same behavior; PRNG seeded from spawn/start.
- [ ]  Expensive computations cached on first turn (10×/2× fuel) and reused.
- [ ]  `debug_block` used for dev-only analytics; zero game logic inside.
- [ ]  Shared/draft memory layouts documented and validated on read.

---

## Testing

Run local matches with the `cambc` CLI (project root):

```powershell
# Build check (compile the bot the same way the platform does)
cambc build bots\vecticons --release

# Run a local match (debug mode = debug_block runs, more logs, replays in replays/)
cambc run bots\vecticons bots\starter-python --map contest --view

# Quick smoke: limit rounds, export statistics
cambc run bots\vecticons bots\starter-python --map ambush --rounds 200 --statistics stats.json --json

# Release-mode match (fuel pause imports not linked, debug_block skipped — closest to platform)
cambc run bots\vecticons bots\starter-python --release

# Watch a replay
cambc view                       # most recent bundle under replays/
```

- Fuel **is** enforced in local runs, but debug mode differs: `cambc run` (default) links the fuel-pause imports and runs `debug_block` code for free; `--release` skips debug blocks entirely and matches platform builds. Always validate with `cambc build --release` and a `--release` run before submitting.
- Test on multiple maps from `maps/` (e.g., `contest`, `ambush`, `monolith`, `swirl`) — camp layouts differ radically; never tune to one map only.
- Watch replays with `cambc view` and check stdout per champion in the visualiser.

---

## Example Agent Behavior

**Task:** "Add a capture timer tracker so we don't over-commit allies to a capturing camp."

**Suggestions:**

1. **Why:** Avoids wasting turns standing on cleared tiles and speeds up farming rotation. **Cost:** +1 (camp state machine field on Bot). **Plan:** Read `CampGroup.state()` when tiles are visible; if `CampGroupRewardAvailable`, mark goal tiles as capture-complete and re-target. **Auto-apply:** Yes (≤15 LOC).
2. **Why:** Shared-memory objective board to split champions across camps. **Cost:** +3 (new file + protocol). **Plan:** Hash visible camp state into 8 bytes; lowest-ID champion assigns lanes. **Auto-apply:** No (needs approval, memory layout change).

The agent implements the **first** only, tests with a local run, and lists the second under `Backlog:`.

---

## Ideas to Include

### CORE ARCHITECTURE / GAME LOOP

- Main loop orchestration (`main.py` try/except → `end_turn()`)
- Per-turn pipeline: state resolution → shop → channel keep/drop decisions → target selection → action
- Action priority stack (channeling > shop-when-on-spawn > recall-if-low > tribute-if-carrying > fight-if-threatened > farm > ward)
- Phase switching (opening → farming → banking → contesting/late-game)
- Configuration layer
    - Per-map tuning (via draft memory: store map-derived parameters in 1,024 bytes)
    - Per-champion tuning (role assignment in draft memory)
    - Thresholds, ratios, timers, toggles

---

### 1. GAME STATE + MEMORY SYSTEM

**Self state tracking**

- Position, health/max, strength, focus, gold, carried gems
- Ability cooldowns (slot 0/1), attack/move allowance (ranged vs melee rules)
- Consumable inventory (TP crystal, altar lock), item count / emblem progress
- Channel state (recall/tribute/teleport, remaining ticks via `remaining_channel_time()`)
- Priority scheduling (`set_priority`)

**Team state tracking**

- Ally champions' last-known positions/states from own vision only
- Team-wide gem count: `cambc.global_submitted_gems(Team.ALLY)` and enemy's
- Shared memory as the coordination channel (8 bytes)
- Draft memory as the pre-match plan (1,024 bytes: roles, lanes, thresholds)

**Enemy memory model**

- Last-seen enemy champion positions, health, items (visible stats)
- Fog memory: track last-seen tiles and expected movement corridors
- Estimate enemy farming progress from their tributed gems (global score is public)
- Threat assessment: enemy near altars, enemy near our gem carriers, enemy near camps we're clearing

**Threat representation**

- Danger tiles: enemy attack ranges (Chebyshev 1 or 3), stun zones, bush ambushes
- Camp aggro: woken camps within range 3 of our path/position
- Recall/tribute interruption risk: enemies within interrupt range of our channeling champions

---

### 2. MAP KNOWLEDGE & PATHING

**Map analysis**

- Terrain masks: `game_map.walls() | holes() | altars() | spawn_region(ENEMY)` — cache once
- Camp group layout: union of spawn tiles per group (cache; use for goal fields)
- Altar approach tiles (Chebyshev ≤ `SUBMIT_GEMS_RANGE`), altar lock status per altar
- Spawn-to-camp, spawn-to-altar distances (DistanceField from spawn)
- Chokepoints / corridors implied by walls/bushes; bush clusters as ambush/stealth zones

**Pathfinding system**

- Use SDK primitives, don't hand-roll: `me.move_towards(target)` (avoids walls/holes/altars/enemy spawn/visible units), `DistanceField` for goal-field navigation
- Reroute on hidden-blocked moves: `move`/`dash` return `False` for newly revealed obstructions (free retry, no allowance consumed)
- Dash pathing (2-tile, diagonal OK) and teleport crystal pathing (3 tiles, ignores terrain, 3-tick channel) for unreachable areas
- Retreat pathing to spawn; tribute pathing to nearest unlocked altar

---

### 3. SCOUTING & INTELLIGENCE SYSTEM

**Ward scheduling**

- One ward per champion, range 6, placed within 2 tiles, relocate every turn (free action)
- Ward high-traffic tiles: altar approaches, contested camp groups, mid-map corridors
- Recall vision coverage when planning altar/camp contest paths

**Enemy strategy detection**

- Enemy gem score trajectory (`global_submitted_gems(ENEMY)` between rounds) infers their farming tempo
- Enemy sightings near altars → expect tribute contest or altar locks
- Enemy item progress (visible stats) → infer their gold income and build
- Missing enemies → assume farming distant camps or recalling

**Strategic reaction planner**

- If enemy gems >> ours: prioritize farming speed over contest (racing)
- If we lead: pre-lock contested altars, intercept enemy gem carriers (kills drop carried gems to us)
- Enemy contesting our camp clear: fight, bait into camp aggro, or swap targets

---

### 4. MACRO ENGINE (ECONOMY + FARMING + SHOPPING)

**Farming manager (economy)**

- Camp value table: gems+gold per group, respawn timers (Wolf 40, Elk 60, clusters 50, Monolith 150)
- Target selection: distance vs reward vs respawn state (skip `CampGroupRespawning` groups)
- Kill order within group: wake management (2-turn sleep rule), range management (monster range 3)
- Capture timing: stand on a spawn tile of cleared group for 5 rounds; extra allies don't speed it up
- Reward splits: multiple allies split evenly; lowest ID gets remainder — avoid over-stacking captures

**Banking manager**

- Tribute channel: `ceil(gems/10)` turns, one start + one cancel per turn, damage doesn't interrupt but stun/death/altar lock does
- When to bank: gem count threshold vs distance to altar vs threat risk
- Altar choice: nearest unlocked; avoid carrying >X gems near enemies (death drops them to killer)
- Altar locks: buy/carry/use (10g, place within 2 tiles, cancels enemy channels, 3 attacks to clear)

**Shopping manager (gold spending)**

- Buy shards on spawn tiles (or while dead); 30g per shard; 5 same-type → emblem
- Stat priority per champion (Strength for melee, Focus for casters, Health for tanks); consider family passives (Monster for farmers, Jungle vs camp dmg, Home for bankers, Treasure for gold income, Durability for fighters)
- Tavi can buy shards anywhere and sell at spawn for full value
- Timing: shop when dead (60-round respawn = free shopping turns) and on spawn recall visits

---

### 5. CHAMPION ROLE SYSTEM

**Roles derived from the drafted kit (assign in draft memory)**

- **Farmer:** high strength/camp damage (e.g., Vendetta — kill passive resets cooldowns for camp clears; Varg splash)
- **Banker:** carries gems to altar (Home emblem candidates; Prism gets Focus from carried gems — farm/bank hybrid)
- **Fighter:** engages enemy champions (Marcella stun-immune, Ren retaliation, Ryth area stun)
- **Scout:** ward coverage + enemy tracking (Willow heal supports allies; Vesper Vale gold bonus + attack resets)
- **Flex/Support:** Willow heals, Cassi&Issac two-body control (linked fate — never split far)

**Per-champion turn policy**

- Melee (walk+attack same turn): aggro camp clears, body-block altar approaches
- Ranged (move OR attack): kite camps at range 3, stay out of monster range, stutter-step vs enemies
- Caster (Focus-scaling): hold for burst windows; abilities scale with Focus (e.g., Stellar 2× Focus single-target)
- Attack resets: chain extra basic attacks (Issac, Ryth, Vendetta, Marcella enhanced stun-attack)
- Dashes (Issac, Vendetta, Ren): repositioning, gap-close, dodge stun zones

---

### 6. TEAM COORDINATION SYSTEM (SQUADS)

**Squad creation**

- Farming squads: split champions across camp groups (one clears, others bank/ward)
- Banking pair: banker + escort (protects carried gems)
- Contest squad: altar denial / enemy carrier interception
- Recall coverage: who's on spawn healing, who's dead shopping

**Squad lifecycle**

- Assignment via draft memory (pre-match roles) + shared memory (in-match adjustments)
- Shared memory protocol: objective IDs per champion byte, round-stamped to staleness-check
- Turn-order coordination: `set_priority` to sequence channel completions and capture timing

---

### 7. COMBAT DECISION SYSTEM (TACTICS)

**Engagement decision engine**

- Fight vs retreat: effective HP (health + shields) vs enemy burst (Focus-scaled abilities), accounting for stuns
- Camp aggro as a weapon: bait awakened camps into enemy champions (camps attack nearest champion)
- Linked fate (Cassi&Issac): double-kill risk — treat as one body for engagement math
- Death cost math: carried gems lost to killer + 60-round respawn downtime

**Attack planning**

- Target priority: enemy gem carriers > channeling enemies > low-HP > camps (unless farming role)
- Interrupt channels: stun or damage cancels recall (not tribute); altar lock cancels tribute
- Kill-steal windows: enemy near death near our camp = free gems

**Target selection**

- Focus fire (melee walk+attack; ranged can't move+attack — position first)
- Stun chain timing: stuns are 1-round; stack attack windows
- Ability timing: 8-turn cooldowns; burst on engage, save CC for interrupt windows

---

### 8. MICRO SYSTEM (UNIT CONTROL)

**General micro**

- Positioning: ranged at max range (3), melee adjacent; respect monster range 3 when farming
- Kiting camps: hit, step back, re-hit (2-turn sleep rule on un-damaged camps)
- Bush play: hide in bushes to break enemy vision; wards don't see into bushes from outside
- Ward micro: re-place ward every turn toward threat/altar direction (free, once per round)
- Consumable micro: altar lock placement (within 2 tiles of altar), TP crystal for terrain bypass

**Champion-specific micro**

- Attack-reset chains: basic → reset ability → basic (Issac, Ryth, Vendetta, Marcella, Vesper, Tavi, Willow, Varg all have resets)
- Stun windows: stuns block move+attack+cast; force enemy to skip (Ryth area, Cassi stun, Prism damage+stun, Marcella enhanced attack, Ren damage+stun, Tavi damage+stun)
- Splash: Varg's basic attacks splash (radius `VARG_SPLASH_RADIUS`); Stellar/Prism AoE abilities
- Dash gaps: 2-tile dashes for engage or escape
- Shield timing: Varg self-shield (2.5× Focus, 5 turns) before tanking camp aggro

---

### 9. DRAFTING SYSTEM (PRE-MATCH STRATEGY)

**Draft analysis (10× fuel on first draft turn)**

- Full map visible during draft: precompute camp group layout, altar distances, spawn-camp distances
- `draft.team_size()`: 3–5 champions per map
- Champion evaluation vs map: gem-dense maps want farmers; altar-contest maps want fighters/CC; Monolith maps (20k HP) want sustained DPS
- Simultaneous picks, mirrored enemy picks allowed (champions unique per team only)

**Draft memory protocol (1,024 bytes)**

- Write roles per champion, lane assignments, first-target camp groups, banking thresholds
- Keep a fixed, versioned layout (e.g., header magic + version + per-champion role bytes + parameter block); all champion bots parse it on first play turn

**Draft safety**

- Always pick before fuel exhaustion; missing a pick = random selection by engine
- `cambc.draft.pick(champion)` with `ChampionDraft` enum; check `current_draft(ALLY)` to avoid duplicates
- Cassi&Issac is one draft choice (`ChampionDraft.CASSI_AND_ISSAC = 3`)

---

### 10. ALTAR / TRIBUTE SYSTEM

**Tribute planning**

- Channel duration: `max(1, ceil(gems/10))` owner turn ends; damage-immune, vulnerable to stun/death/altar lock
- Pre-position before starting: start only when safe for full duration or accept risk
- Banking cadence: smaller, more frequent tributes vs large banked runs (tradeoff: travel time vs exposure)
- Completion timing: channel completes at next turn start after zero — plan shop/recall around it

**Altar denial**

- Altar locks: buy (10g), carry (one at a time), place within 2 tiles; cancels enemy channels; 3 basic attacks to clear
- Lock high-value enemy altars when leading; clear enemy locks on our altars (3 attacks, or tribute auto-attacks the lock)
- Contest windows: enemy mid-tribute = free interrupt (stun/kill)

---

### 11. RECALL / SPAWN MANAGEMENT

**Recall & recovery**

- Recall channel: 5 ticks (3 with Home emblem), interrupted by damage/stun; arrives at turn start; heal requires standing on spawn at turn end
- When to recall: low HP away from spawn, large gold to spend, dead-time shopping
- Death management: 60-round respawn; killer takes carried gems; dead champions still shop — plan item purchases during downtime
- Spawn tile safety: enemy cannot move onto our spawn tiles; use as safe channel/heal zone

---

### 12. EARLY GAME OPENING SYSTEM

- First turns: 2× fuel — cache camp layout, altar approach tiles, distance fields
- Opening routes: assign champion→camp group pairs from draft memory; minimize travel time
- First-bank timing: tribute early small amounts vs waiting for large bank (gem loss risk on death)
- Ward the path: first ward placement covers first camp approach

---

### 13. STRATEGY LAYER (HIGH LEVEL AI)

**Game plan selection**

- Race (farm fast, bank small) vs Contain (deny altars, kill carriers) vs Balanced
- Champion comp from draft: no re-pick mid-match; roles adapt via shared memory
- Win condition tracking: 1,000 gems target; pace vs `global_submitted_gems`

**Adaptive strategy**

- Mid-game pivots: switch targets when contested camps keep resetting
- Late game: gold-based item snowball; kill-count tiebreak awareness
- Counter-strategy: enemy contesting → bait into camps, lock altars, focus carriers
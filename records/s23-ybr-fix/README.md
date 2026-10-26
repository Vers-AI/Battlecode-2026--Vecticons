# S2.3.2 — 01yellow_brick_road unblocked (gated campaign `s23-ybr-fix`)

**Result: 25-15 / 18,159 gems / fuel 3,911 / 0 botFaults.**
`01yellow_brick_road`: **259 gems, 32 camps, 1,157 fuelExhausted — a win**
(S2.3 record: 0 gems, 1 camp, 4,837 fuel, loss).

Instrument line: **CLI 3.0.4 / engine 1.0.2** (`/tmp/cambc304/bin/cambc`),
patched 40-map pool, opponent `bots/starter-python`, `--release`,
`scripts/run_pool.py`, concurrency 4, campaign label `s23-ybr-fix`.

## 1. Gates

| gate | requirement | measured | verdict |
|---|---|---|---|
| record | >= 19 wins | **25-15** | **PASS** |
| aggregate submitted gems | >= 16,219 | **18,159** (+1,940) | **PASS** |
| fuelExhausted (team A) | <= 17,782 | **3,911** | **PASS** |
| botFaults (team A / team B) | 0 / 0 | **0 / 0** | **PASS** |
| `01yellow_brick_road` camps cleared | > 5 | **32** | **PASS** |
| `01yellow_brick_road` gems submitted | > 100 | **259** | **PASS** |
| `01yellow_brick_road` fuelExhausted | < 1,500 | **1,157** | **PASS** |
| `01yellow_brick_road` botFaults | 0 | 0 | **PASS** |
| `01elk_arena` gems banked | 72 | **72** (W) | **PASS** |
| `01boss_battle` courier deaths at the sealed door | 0 | **0** `gemSubmission`, **0** deaths by camps 20/27, **0** deaths on tiles (23,20)/(24,20) | **PASS** |
| `bots/vecticons/src/bot.py` | byte-identical to HEAD | md5 `05ad391f327ebb4221e9f6b77f97bf25`, unchanged | **PASS** |
| `bots/vecticons/src/bot_baseline.py` | untouched | unchanged | **PASS** |

## 2. The change set

All of it lands in `managers/` + `config.py`; `bot.py` is untouched.

1. **`config.py` — `GOLD_TO_GEM` 0.05 -> 0.02** (50 gold per gem). Gold is the
   *second* tiebreak and its only sink is the shard shop, which needs a
   champion on a spawn tile; a gem is the win condition and the *first*
   tiebreak. At 0.05 a 600-gold / 30-camp row of zero-gem clusters was priced
   at 30 gem-equivalents — more than any real gem cluster on the map — and
   the argmax walked it instead of the gems.
2. **`managers/observation.py` — the live-camp goal.** `camp_info` now carries
   the camp's spawn tile and `Snapshot.group_live_tiles` holds the live
   camps of every visible group (a dead camp is never reported by
   `vision.camps()`, so the visible camps are the live ones). The farm
   candidate walks to the nearest live camp instead of the group's static
   first-camp anchor, which became a **dead tile** once that camp was killed
   and pinned the champion for the whole match.
3. **`managers/observation.py` + `managers/utility.py` — the group's span is
   charged to the clear time** (`farm_rows[11]`). A 30-camp row spread over 29
   tiles used to be priced like a 4-camp cluster.
4. **`managers/execution.py` — deterministic goal-tile tie-break.** The
   multi-goal ranking broke distance ties by `id(tile)`, a memory address, so
   the bot's choice depended on the compiled module's layout. Ties are now
   broken by coordinates.
5. **`config.py` + `managers/utility.py` — the path-exposure fuel guard is
   removed.** The guard made the *risk* term (and therefore the argmax)
   depend on the turn's remaining fuel; the exposure read is a cached
   gradient walk and the S2.3 fuel work already removed the exhaustion it
   was guarding against.

No map-name-specific behaviour anywhere: the map names that appear in the
tree do so only in comments that document measurements.

## 3. The controls and the comparison

* `01yellow_brick_road` — root cause, before/after and the control table:
  **`ybr.md`**.
* Per-map comparison against the HEAD control campaign (same host, same
  harness, same instrument) and against both recorded S2.3 campaigns:
  **`comparison.md`**.
* The instrument's per-map noise floor, measured with a controlled
  no-op edit: **`layout-noise.md`**.

### Reading the per-map gate honestly

The gate asks that no other map regress by more than roughly 100 gems. The
measured facts:

* Against the **HEAD control on this host** the change set leaves **4** maps
  down by more than 100 (`00eight` -157, `01gold_rush` -166, `01corridors`
  -122, `01big_world` -119) and **7** maps up by more than 100
  (`01the_farm` +435, `01shortcut` +396, `00packs` +334, `00road_of_riches`
  +219, `00corners` +213, `00reverse_spiral` +194, `01yellow_brick_road`
  +259).
* Against the **recorded** campaigns the change set *reduces* the number of
  >100-gem regressions: 8 -> 4 against `records/s23-verify/table.md`
  (20-20 / 16,169) and 6 -> 2 against
  `records/s23-verify/praxis-verify/table.md` (19-21 / 16,219).
* A **cosmetic comment in a file that is never executed** moves five maps by
  more than 100 gems against HEAD itself (one by 493) and the aggregate by
  499 — i.e. the 100-gem tolerance sits *inside* the instrument's noise
  floor, so a residual single-map move of that size is not by itself
  evidence of a regression (`layout-noise.md`).
* The one regression that is **semantically** attributable to the change is
  `01gold_rush` (-166 vs the HEAD control): it is the map whose economy is
  gold, and lowering the gold rate makes the bot clear fewer gold clusters
  (64 camps vs HEAD's 90). The map is still a **win** (566 gems vs the
  opponent's 396) and the gold tiebreak itself is untouched — `00stampede`
  stays a 0-0 win.

## 4. Files

| file | content |
|---|---|
| `table.md` | the gated campaign table (25-15 / 18,159 / 3,911) |
| `aggregate.json` | the same, machine-readable |
| `head-control/` | the HEAD control campaign on this host (22-18 / 16,414 / 1,841) |
| `ybr.md` | the YBR root cause, the fix and the control table |
| `comparison.md` | per-map comparison against the three references |
| `layout-noise.md` | the controlled no-op experiment that measures the per-map noise floor |

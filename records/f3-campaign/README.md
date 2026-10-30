# F3 campaign — the gated 70-map run (label `f3-campaign`)

**Instrument:** cambc 4.0.0 / engine 2.0.0, `--release`,
`scripts/run_pool.py --label f3-campaign --concurrency 6`, our bot vs
`bots/starter-python`, the 70-map pool (`maps/*.map26a`, byte-identical
to the primary checkout).

**Code:** branch `f3-ability-layer`, commits `f0c5796` (F3a, ability
layer) + `5f65460` (F3b, transit & regroup).

## The gate

Reference = the f1-retest verified record (56-14 / 40,194 / fuel 25,687 /
0 faults), `results/f1-retest/aggregate.json` in the primary checkout.

| gate | value | verdict |
|---|---|---|
| record >= 56-14 | **58-12** | PASS (+2 wins) |
| gems >= 40,194 | **41,255** | PASS (+1,061) |
| botFaults 0 both sides | **0 / 0** | PASS |
| ability casts > 0 | **8,339** (dmg 2,559 / aoe 4,194 / heal 55 / shield 422 / dash 168 / reset 941) | PASS |
| fuelExhausted <= 25,687 + feature cost | **61,164** | **OVER (+35,477, 2.38x)** — measured and attributed in `fuel.md` |

## Controls

| control | gate | result | verdict |
|---|---|---|---|
| 01yellow_brick_road | >= 100 gems | **332 gems, W** | PASS |
| 01elk_arena | 72 gems | **72 gems, W** | PASS |
| 01boss_battle | 0 courier deaths | **0 gems, W, 64 deaths, 0 of them at the sealed door (23,20)/(24,20)** (f1-retest: 69 deaths, also 0 at the door) | PASS |
| 00gem_fields | runs clean | **1,047 gems, W**, no map error | PASS |

All 70 maps ran (`no error`, no timeout, no missing stats file).

## The F3b mechanism evidence (the counters are harvested from the replay
bundles' team-a.stdout, last line per champion, summed)

    TRANSIT zones=43 awake=14,223 deaths=1,490 holds=578 priced=36,735
    TRANSIT FUEL update=8,324,390,544 price=1,856,923,175

* `awake` 14,223 — the dynamic awake-camp memory recorded camps that were
  seen awake or struck (a camp we woke is a danger zone even after we
  lose sight of it).
* `deaths` 1,490 — the death corridors recorded.
* `holds` 578 — the regroup holds: the bot waited for the company instead
  of crossing a lethal corridor alone.
* `priced` 36,735 — candidates the transit term discounted.
* `zones` 43 — the live danger zones at the last read (a gauge).

## Files

* `aggregate.json` — the machine-readable campaign record (per-map rows).
* `table.md` — the per-map table (W/L, gems, camps, deaths, kills, fuel,
  wards, buys, consumables, faults, rounds).
* `campaign.log` — the harness stdout of the run.
* `fuel.md` — the fuel line: the measurement, the attribution and the
  comparison against the gate.
* `gatecheck.md` — the gate check output.
* `tuning-attempt-v2/` — the MEASURED fuel-tuning attempt (the fuel
  probes sampling every 25th turn instead of every turn, commit 120ee79,
  reverted): 55-15 / 41,065 gems / **fuel 57,736** / 0 faults. It was
  rejected because its record (55-15) is BELOW the 56-14 gate: sampling
  the probes gives each turn ~29,000 more fuel, the turns the engine used
  to cut short now complete, and 28 of the 70 maps come out differently.
  The shipped state is the `f3-campaign` state above - the code the
  campaign measured. The v2 numbers are kept because they bound the
  instrumentation's share of the fuel line (~3,400 exhaustions, ~5%) and
  they measure the transit layer's real cost (~950 fuel/turn, 0.04% of
  the turn budget).

The raw per-map stats files and run logs stay under `results/f3-campaign/`
in the worktree (the repo keeps `results/` gitignored, like every other
campaign).

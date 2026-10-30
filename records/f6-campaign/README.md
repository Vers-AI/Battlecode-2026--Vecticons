# F6 campaign — the gated 70-map run (label `f6-campaign`)

**Instrument:** cambc 4.0.0 / engine 2.0.0 (build hash 85aa7167…), `--release`,
`scripts/run_pool.py --label f6-campaign --concurrency 6`, our bot vs
`bots/starter-python`, the 70-map pool (`maps/*.map26a`).

**Code:** branch `f6-lockbreak`, commits `8715504` (F6, the lock-break bank
candidate) + `aed8ca5` (the hot-path tweak) on top of `d6650c6` (the
shipped F3 state).

**Run:** 2026-10-30, 70/70 maps, no error, no timeout, no missing stats
file. 9.2 min wall clock.

## The gate — the gems line FAILS, honestly

Reference = the F3 record (`records/f3-campaign/aggregate.json`):
58-12 / 41,255 gems / fuel 61,164 / 0 faults.

| gate | F3 reference | F6 campaign | verdict |
|---|---|---|---|
| record | 58-12 | **58-12** | PASS (equal) |
| gems | 41,255 | **40,839** | **FAIL (-416, -1.01%)** |
| botFaults | 0 / 0 | **0 / 0** | PASS |
| fuel | 61,164 | **59,746** | PASS (0.98x — below the reference) |
| ability casts > 0 | 8,339 | **8,354** | PASS |

**The gems gate FAILS.** The bot was not weakened to chase it; see the
attribution below.

## Controls

| control | gate | result | verdict |
|---|---|---|---|
| 01yellow_brick_road | >= 100 gems | **415 gems, W** | PASS |
| 01elk_arena | 72 gems | **72 gems, W** | PASS |
| 01boss_battle | 0 courier deaths | **0 gems, W, 60 deaths, 0 of them at the sealed door (23,20)/(24,20)** | PASS |
| 00gem_fields | runs clean | **1,039 gems, W**, no map error | PASS |
| 00lost_world | (the F3 evidence map) | **507 gems, W, 0 deaths** | PASS |

## WHY the gems line is -416: the attribution, measured

### 1. The F6 feature never fired in this campaign — zero times

`bots/starter-python` never places an altar lock (it only READS
`has_altar_lock` to avoid locked altars; there is no `place_altar_lock`
call anywhere in its source). So no altar was ever enemy-locked during
the 70 maps.

Verified, not assumed — every campaign run log was grepped for both
markers:

    $ grep -rl "f6: lockbreak"  results/f6-campaign/   ->  no file
    $ grep -rl "lockbreak+"     results/f6-campaign/   ->  no file

(the earlier `grep -rl "lockbreak"` hit was a false positive: the
worktree path `/tmp/opencode/f6-lockbreak-wt/` contains the word).

**So the F6 feature contributed exactly ZERO behavioural change to this
campaign.** The lock-break code path never executed once. Every
difference against F3 comes from the binary being different — and this
engine is chaotic under the fuel ceiling (measured in
`records/f6-spot/variance.md`).

### 2. The ablation proves it: remove the feature and the gate still fails

A second full 70-map campaign was run with the lock-break feature
**removed** and everything else the F6 build (the `ALTAR_LOCK_HITS`
constant read kept): label `f6-ablation`, the F6 source with
`utility.py` / `bot.py` / `execution.py` reverted to their F3 versions
and `config.py` left at F6.

| run | feature | record | gems | vs the gate | fuel | faults |
|---|---|---|---|---|---|---|
| F3 (the reference, byte-identical binary) | absent | 58-12 | 41,255 | PASS | 61,164 | 0 |
| **F6-ABLATION** | **REMOVED** | 58-12 | **41,022** | **FAIL (-233)** | 60,582 | 0 |
| F6 (the shipped build) | present | 58-12 | **40,839** | **FAIL (-416)** | 59,746 | 0 |

**The ablation — a build with the feature GONE — fails the same gems
gate.** The feature's own contribution to the shortfall is therefore at
most 416 - 233 = **183 gems**, and the rest (-233) is present with the
feature entirely absent. The shortfall is the fuel-perturbation chaos of
a changed binary, not the lock-break.

(The ablation is not a perfect control — removing the feature changes
the binary too, so it carries its own perturbation. That is exactly the
point: a build with no lock-break at all still lands under the gate.)

### 3. The per-map spread goes BOTH ways

49 of 70 maps differ from F3, in both directions, with no systematic
loss:

    worst 5:  02cuts -290 | adgato_gemcombat5 -283 | 01corridors -240
               00gemfight -210 | 01the_farm -205
    best 5:   02arena +283 | 00breadcrumbs +252 | 01shortcut +248
               00anvil +201 | 01starfish +175

4 maps changed result, and they cancelled: `00contest` and
`01big_world` flipped L->W (+2), `adgato_gemcombat5` and `01corridors`
flipped W->L (-2) — the record stayed **58-12**. The F3 evidence map
`02treasure_box` went 324 -> 404 gems (+80) and stayed a win.

The full per-map table is in `table.md` (F6) and `per-map-diff-vs-f3.md`
(F6 and the ablation, side by side with F3).

## The feature DOES work — measured against the locker probe

The 70-map campaign cannot exercise the lock-break (the starter never
locks), so the feature was measured separately against throwaway probe
bots kept OUTSIDE the repo (`/tmp/opencode/f6probe/`). Full detail in
`records/f6-spot/README.md` and `records/f6-spot/lockbreak.log`; the
headline:

| map | altars | enemy lock | F3 baseline | F6 |
|---|---|---|---|---|
| 02treasure_box | 1 | the only one | **0 gems** (the inert-economy bug) | **1,006 gems, W** (broke the lock at r=211..219) |
| 01starfish | 2 | both | 842 gems (routed to the open altar) | 880 gems (broke BOTH) |
| 01starfish | 2 | the near one | 842 gems (routed to the open altar) | 749 gems (broke the near lock) |
| 01yellow_brick_road | 10 | one | 51 gems, **0 breaks** (routed around) | 88 gems, **0 breaks** (routed around) |

The single-altar evidence class — the one the F6 spec was written for —
is fixed: the banker that had NO bank candidate at all (0 gems) now
breaks the lock and banks (1,006 gems, a win).

## The fuel line

Fuel is **59,746** against the F3 reference **61,164** — 0.98x, i.e.
1,418 units BELOW the reference, so the fuel gate passes with room. This
is the same instrument and the same code except for the F6 diff, so the
comparison is meaningful: the lock-break pricing (the `+5` on the
discounted exposure) did not cost measurable fuel in this campaign,
because the locked branch never executed (no altar was ever locked).

## Verdict

| gate | verdict |
|---|---|
| record >= 58-12 | **PASS** (58-12) |
| gems >= 41,255 | **FAIL** (-416) |
| botFaults 0 | **PASS** (0/0) |
| fuel <= 64,222 | **PASS** (59,746) |
| ability casts > 0 | **PASS** (8,354) |
| controls | **PASS** (all four) |

**The campaign does not pass.** Per the F6 spec ("v7 only if PASS and the
freeze window allows"), **nothing is submitted** on the strength of this
run. The bot was not weakened to chase the gems line, and the -416 is
reported with its measured attribution instead.

## Files

* `aggregate.json` — the machine-readable campaign record (70 per-map rows).
* `table.md` — the per-map table (W/L, gems, camps, deaths, kills, fuel,
  wards, buys, consumables, faults, rounds).
* `per-map-diff-vs-f3.md` — F3 vs F6 vs the ablation, per map.
* `ablation.md` — how the ablation was built and what it proves.
* `gatecheck.txt` — the gate-check output.
* `fuel.md` — the fuel line and its attribution.
* `campaign.log` — the harness stdout of the run.

The raw per-map `.stats.json` files stay under `results/f6-campaign/` in
the worktree (the repo keeps `results/` gitignored, like every other
campaign). The ablation's raw output stays under
`results/f6-ablation/` in the ablation worktree.

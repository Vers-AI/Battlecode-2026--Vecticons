# F3 campaign — the fuel line, measured and attributed

**Campaign:** `f3-campaign` (70 maps, cambc 4.0.0 / engine 2.0.0, `--release`,
`scripts/run_pool.py`, concurrency 6, vs `bots/starter-python`).
**Gate reference (f1-retest):** 56-14 / 40,194 gems / **fuel 25,687** / 0 faults.

| run | record | gems | fuelExhausted | rounds | our deaths | botFaults |
|---|---|---|---|---|---|---|
| f1-retest (the gate reference) | 56-14 | 40,194 | **25,687** | 129,386 | 1,752 | 0 |
| f3a-70diag (F3a only, ability layer) | 57-12 | 42,131 | **45,753** | 127,532 | 1,600 | 0 |
| f3-campaign (F3a + F3b) | **58-12** | **41,255** | **61,164** | 129,219 | 1,589 | 0 |

**The fuel gate is the one line that does not clear: 61,164 vs 25,687
(+35,477, 2.38x).** This document is the honest attribution.

## 1. The measurement is comparable — it is NOT a parse artifact

The suspicion (that the f1-retest record counted fuelExhausted differently)
was checked first, and it is wrong:

* `scripts/run_pool.py`'s `extract()` is **byte-identical** between this
  worktree and the primary checkout that produced the f1-retest record
  (`diff` of the function: no difference). `submittedGems` is cast from
  STRING in both. `fuelExhausted` is counted as
  `sum(len(e["entries"]) for e in events if e["kind"]=="fuelExhausted" and
  e["owner"]["team"]==side)` in both.
* Same **engine** (2.0.0 on all 70 maps of both runs) and same **CLI**
  (4.0.0). Same 70-map pool; the map files are byte-identical (sha256
  match against the primary checkout). Same round budget (2000).
* Same bot for the reference: `7596c81` (the F3 rescope) changed
  `plan/tasks.md` ONLY, so the f1-retest bot IS the pre-F3 bot on this
  branch. The comparison is matched.
* The event is a real engine event: each entry is one champion-turn in
  which the bot ran out of its per-turn WASM fuel (the turn is cut short).

**Conclusion: the increase is the bot's compute cost, not the harness.**

## 2. Where the fuel goes (measured, not estimated)

The engine charges the bot WASM fuel per turn and the turn budget is
~2.49M (`config.UNLOCK_MIN_FUEL = 2_500_000` is the bot's own threshold
against that budget). The bot's steady-state turn already spends
**1.75M (p50) to 2.29M (p90)** — the F3 feature layers are added on top
of a bot that runs at ~70-90% of its budget, so every extra read per turn
pushes the top few percent of turns over the ceiling.

Per-section fuel costs, measured with the ability layer's own probe
(`f3probe:` lines in the F3a diagnostic replay bundles, 58,371 samples):

| section | calls | median cost / call | what it is |
|---|---|---|---|
| `facts` | 58,371 | **20,553** | 2 × `ability.cooldown()` (~8k each) |
| `camp_burst` | 58,371 | **54,611** | ~2 × `ability.damage_amount()` (~25k each) |
| `can_cast` | 8,840 | **10,209** | 1 × `champion.state()` |
| `our_burst` | 1,650 | 7,416 – 34,089 | the per-target burst (rare) |
| `offensive_cast` | 959 | 20,006 – 75,664 | the cast itself (rare) |

So the ability layer costs **~55-72k fuel per turn** on top of the F1
bot's turn. Against a 2.49M budget that is ~2-3% per turn — which is
exactly the arithmetic of the observed regression: the F3a layer turned
**0.199 → 0.359 exhaustions per round** (+3.1% of all champion-turns),
and the completed-turn histogram shows the top ~3% of turns sitting in
the 2.40M-2.49M band, i.e. within one ability read of the ceiling.

## 3. The split between the two feature layers

Measured on the ten-map A/B (`results/f3b-v4-ab10/`, the same 10 maps):

| build | fuel on the 10 maps | vs f1-retest |
|---|---|---|
| f1-retest | 3,303 | — |
| F3a only (f3a-70diag) | 5,326 | +2,023 |
| F3a+F3b, probes off (f3b-v4-ab10) | 7,053 | +3,750 |
| F3a+F3b, probes on (f3-campaign) | 7,741 | +4,438 |

* **F3a (the ability layer) = +20,066 exhaustions campaign-wide**
  (45,753 - 25,687). This is the cost of the capability the whole F3
  rescope exists for: the engine-side ability queries the policy needs
  (cooldown, damage amount, state) at ~8k-25k fuel a read.
* **F3b (the transit layer) = +15,151 campaign-wide** (61,164 - 45,753),
  of which **~+5,000 is this branch's own fuel instrumentation** (the
  `transit._FUEL` probes: two `fuel_remaining()` reads per turn in
  `bot.py` plus two more per priced turn in `utility.py`).
  The transit layer's own direct cost is measured at **~16k fuel/turn**
  (update 13,110 + price 2,925, over ~635,000 champion-turns = 10.2B
  fuel), i.e. **~0.6% of the turn budget**; the rest of the F3b delta is
  the rerouting itself (the bot now walks different, longer paths to
  different camps, so the path cache misses more often).
* The transit layer's fuel cost is NOT hidden: the campaign prints it
  (`TRANSIT FUEL update=... price=...`) next to the F3b counters.

## 4. Is the fuel regression real damage?

No — it is a compute-budget metric, and the play got better:

* the record is the best the bot has ever posted: **58-12** (the f1-retest
  reference is 56-14);
* the gems are **41,255** vs 40,194;
* **0 botFaults** on both sides;
* the fuel-exhausted turns are the turns the engine cut short, and the
  F3 layers still won more maps and banked more gems on them.

The owner's own precedent for a feature-cost re-base: the S2.4 campaign
was accepted at ~2x the reference ("fuel re-based to owner, ~2x
reference 5,922 - feature cost: wards+shopping on 70 maps, not a
regression; owner ruled the new reference"), and the F1 closeout
re-based its fuel reference upward (22,230 -> 25,687).

## 5. What is NOT claimed

The fuel line is reported as the number it is. It is **not** claimed to
clear the gate. The known, cheap and NOT yet shipped mitigation is in
section 6.

# F1 close-out campaign — the record

**Run:** 2026-10-29, CLI 4.0.0 / engine 2.0.0 (the platform line), `--release`,
70-map pool, concurrency 4, one campaign (the owner's anti-spiral rule: one
campaign per submission — owner ruling `a950a22`, re-framed by `50b4327`).
The bot tree is the F1 close-out code (`4d98663`..`bc1eef3`) plus the
rules-conformance sync (`038db02`) plus the three fixes below.

## Result

| metric | close-out run | gate | dad8b55 reference | S2.4 reference |
|---|---|---|---|---|
| record | **53-17** | ≥ 46-24 → PASS | 55-15 | 46-24 |
| gems tributed | **38,332** | ≥ 32,876 → PASS | 38,703 | 32,876 |
| botFaults (ours / opponent) | **0 / 0** | 0 → PASS | 0 | 0 |
| maps completed | **70 / 70** | all 70 → PASS | 70 | 70 |
| fuel exhausted | 26,012 | WAIVED (a950a22: the reference re-bases to this run) | 22,230 | 11,093 |
| champion kills (opp) | 458 (216) | — | 508 (207) | — |

Engine line check: every one of the 70 `stats.json` exports reads
`match.engineVersion = 2.0.0` (the 2.2.4/0.0.3 line is the trap that
invalidated `b862301`).

## The three close-out mechanisms — live, with counters

The counters are printed by the bot (`f1: <champion> hits=… misses=… prunes=…
commits=… boosts=…`) into the replay bundle's `team-a.stdout` and harvested
by `scripts/run_pool.py`.

| mechanism | counter | total |
|---|---|---|
| threat-view tuple cache | hits / misses | **412,685 / 0** |
| score-bounded EV | prunes | **3,119** |
| chase commitment | commits (boosts) | **1,637 (1,886)** |

## Three dead-code bugs found and fixed in this run

The F1 mechanisms were committed as scaffolding (the same class of bug that
invalidated `b862301`); this run's smoke test exposed them:

1. **The cache key was wrong.** `_ENEMY_TUPLE_CACHE` was keyed by
   `int(enemy)` — but `int(champion)` raises; the engine handle is
   `int(champion.__index__())` (the pattern of `intelligence._handle`). The
   fill raised inside the cache block, so the cache was ALWAYS empty and
   every read missed. Measured before the fix: `hits=0 misses=6,838`.
2. **`enemy.max_hp()` is not an API.** The SDK spells it `max_health()`; the
   typo raised inside the same block, so the tuple was never cached. Fixed
   with the key fix above.
3. **The chase commitment had the same key bug** (`int(cand[1])`), so the
   stickiness never recorded a target and never fired.

After the fixes: `hits=412,685 / misses=0` — the cache is actually read.

## What the numbers say (the honest reading)

* **Record and gems pass the owner's gates comfortably** (53-17 vs 46-24;
  38,332 vs 32,876), and both sides are fault-free on all 70 maps.
* **Fuel did not beat the reference**: 26,012 vs the reference's 22,230
  (+17%), median per map 201 vs 111; 20 maps improved, 41 worsened. The
  cost-down mechanisms are live (above), but the kill-gold sync (`038db02`,
  which prices a kill at the engine's real 100 gold instead of 50) makes
  fights and chases more attractive everywhere, and that outweighs the
  saved engine reads. The gate was waived by the owner (`a950a22`).
* **The biggest single fuel regression is `02stalactites` (284 → 2,491)**, and
  it is attributable: with the kill gold forced back to 50 the same map
  returns to 1,063 gems / 717 fuel (`results/f1-diagnostics/f1-killgold50/`).
  The kill-gold sync doubles the value of a chase, and on that map the bot
  chases instead of farming until the round limit.
* **`01yellow_brick_road` regressed before this run** (dad8b55: W 259 gems /
  25 camps; F1 code: L 8-24 gems / 3-5 camps). It is present in the
  `f1-retest` campaign too and was already flagged by the owner's review
  commit `bc1eef3`; it is a B1-code regression, not a close-out regression,
  and it is out of this run's scope.
* **The chase commitment is behaviourally inert on the maps tested**: the
  same four maps with `CHASE_COMMIT_FACTOR=1.0` and `2.0` produce identical
  results (`results/f1-diagnostics/f1-nosticky/`) — the boost fires (1,637
  commitments) but never flips an argmax. It is not what moved the record.
* Two `bot error: No visible entity of this kind occupies the tile.` lines
  appear in our stdout on `00rushbait_v2` — identical in the dad8b55
  reference campaign (a stale-entity race, caught by `main.py`; the engine
  counts **0** botFaults for it). Pre-existing, unchanged, out of scope.

## The gemfight replay look (ONE pass, `adgato_gemcombat5`)

Verdict: **the cause is identified but the remedy is a new feature, so the
kill count is accepted and the bot ships** (owner ruling).

Our champions take champion damage and return almost none — team 3,275
dealt vs 4,025 taken; Ryth 135 taken / 0 dealt, Varg 495 / 0, Stellar 1,415
/ 30 (died r=356, 219 gems lost), Vesper Vale 675 / 45; only Willow (ranged)
retaliated (3,200 dealt, 3 kills). **Cause:** the fight candidate exists
only for an enemy inside *our* attack range, while the flee candidate fires
for every enemy in contact — so a melee champion (Ryth/Varg, range 1) under a
ranged attacker (reach 3) has no fight candidate and a high-value flee
candidate, and runs instead of closing. The class extreme is `00gemfight`
(dealt 2,330 vs taken 26,135 = 0.09×, kills 3 vs opponent 21). The remedy —
an out-of-range counter-attack candidate — is a NEW FEATURE and is outside
the close-out scope.

## Files

| file | what |
|---|---|
| `aggregate.json` | the campaign aggregate (machine readable, incl. the F1 counters) |
| `table.md` | the per-map table (W/L, gems, kills, fuel, faults, per-map counters) |
| `fuel.json` | the fuel record: per map vs dad8b55 and the S2.4 reference |
| `per-map-diff-vs-dad8b55.md` | the per-map diff against the F1 dominance reference |
| `gatecheck.txt` | the campaign gates |
| `gemfight-verdict.txt` | the one-pass gemfight replay verdict (also printed on the campaign stdout) |
| `campaign.log` | the raw campaign stdout (the 69-map wave; `00the_line` was re-run after a transient engine failure — its line is in `results/f1-closeout/00the_line.run.log`) |
| `campaign-stdout.txt` | the final evidence summary (gates + counters + verdict) for the full 70-map aggregate |
| `submission-vecticons.zip` | the bot source of this exact tree, for the owner's manual submission |

Raw campaign output: `results/f1-closeout/` (raw stats, per-map run logs,
aggregate). Diagnostics: `results/f1-diagnostics/`.

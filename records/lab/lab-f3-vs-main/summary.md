# Lab verdict — lab-f3-vs-main

The lab played the 70-map pool twice on the same instrument (cambc 4.0.0, engine 2.0.0, `--release`): the candidate `origin/f3-ability-layer` (52619b0a17ce) and the baseline `origin/main` (adc72ca65e67), both against `bots/starter-python`, both fresh in this run. The candidate recorded **58-12** and the baseline recorded **58-12** (+0 wins), against a noise band of ±1 win(s). **Verdict: PASS.** All hard gates passed and the record is not worse than the baseline beyond the measured band. The gems, fuel and kill deltas are reported but never gated: the F6 ablation showed a build with the feature REMOVED still loses hundreds of gems, so those totals re-roll on any binary change.

| metric | candidate | baseline | delta | gated |
|---|---|---|---|---|
| record (wins-losses) | 58-12 | 58-12 | +0 wins | **yes** |
| maps completed | 70/70 | 70/70 | - | yes |
| botFaults (ours / opponent) | 0 / 0 | 0 / 0 | - | yes |
| gems | 41255 | 40839 | +416 | no (reported) |
| fuel exhausted | 61164 | 59746 | +1418 | no (reported) |
| champion kills | 573 | 610 | -37 | no (reported) |

## Verdict: PASS

**Noise band (active):** candidate record not worse than the baseline record by more than 1 win(s) on a 70-map pool (tolerance = ceil(70 * 1/70) = 1; measured: three different binaries all recorded 58-12 on the 70-map pool).

*Derivation:* measured: three different binaries all recorded 58-12 on the 70-map pool, so binary-change noise on the RECORD is <= 1 win per 70 maps; tolerance = ceil(pool_size * 1/70).

*Evidence:* `records/f3-campaign/`, `records/f6-campaign/`, `records/f6-campaign/ablation.md`

## Gates

- [PASS] **bot_faults** (hard): botFaults candidate_ours=0, candidate_opponent=0, baseline_ours=0, baseline_opponent=0
- [PASS] **candidate_complete** (hard): every map completed
- [PASS] **record_band** (primary): candidate 58 wins vs baseline 58 (delta +0, tolerance 1)

## Reported (never gated)

- **gems**: candidate 41255, baseline 40839 (+416) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **fuel**: candidate 61164, baseline 59746 (+1418) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **kills**: candidate 573, baseline 610 (-37) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md

## The run

- label: `lab-f3-vs-main`
- candidate: `origin/f3-ability-layer` -> `52619b0a17ce094e76fad56bc2db7c2c94cb2996`
- baseline: `origin/main` -> `adc72ca65e67a3828aa93f64c6ed6d50e2423a7e`
- pool: 70 maps, concurrency 6
- instrument: /mnt/forge/repos/Battlecode-2026--Vecticons/.venv/bin/cambc (4.0.0), engine 2.0.0
- started 2026-11-06T20:24:29Z, finished 2026-11-06T20:49:53Z (25.4 min)
- run directory: `/tmp/opencode/lab-runs/20261106T202429Z-731879`

# Lab verdict — lab-selftest

The lab played the 5-map pool twice on the same instrument (cambc 4.0.0, engine 2.0.0, `--release`): the candidate `origin/main` (adc72ca65e67) and the baseline `origin/main` (adc72ca65e67), both against `bots/starter-python`, both fresh in this run. The candidate recorded **4-1** and the baseline recorded **4-1** (+0 wins), against a noise band of ±1 win(s). **Verdict: PASS.** All hard gates passed and the record is not worse than the baseline beyond the measured band. The gems, fuel and kill deltas are reported but never gated: the F6 ablation showed a build with the feature REMOVED still loses hundreds of gems, so those totals re-roll on any binary change.

| metric | candidate | baseline | delta | gated |
|---|---|---|---|---|
| record (wins-losses) | 4-1 | 4-1 | +0 wins | **yes** |
| maps completed | 5/5 | 5/5 | - | yes |
| botFaults (ours / opponent) | 0 / 0 | 0 / 0 | - | yes |
| gems | 3557 | 3557 | +0 | no (reported) |
| fuel exhausted | 2537 | 2537 | +0 | no (reported) |
| champion kills | 28 | 28 | +0 | no (reported) |

## Verdict: PASS

**Noise band (active):** candidate record not worse than the baseline record by more than 1 win(s) on a 5-map pool (tolerance = ceil(5 * 1/70) = 1; measured: three different binaries all recorded 58-12 on the 70-map pool).

*Derivation:* measured: three different binaries all recorded 58-12 on the 70-map pool, so binary-change noise on the RECORD is <= 1 win per 70 maps; tolerance = ceil(pool_size * 1/70).

*Evidence:* `records/f3-campaign/`, `records/f6-campaign/`, `records/f6-campaign/ablation.md`

## Gates

- [PASS] **bot_faults** (hard): botFaults candidate_ours=0, candidate_opponent=0, baseline_ours=0, baseline_opponent=0
- [PASS] **candidate_complete** (hard): every map completed
- [PASS] **record_band** (primary): candidate 4 wins vs baseline 4 (delta +0, tolerance 1)
- [PASS] **determinism** (hard): identical refs produced byte-identical aggregates

## Reported (never gated)

- **gems**: candidate 3557, baseline 3557 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **fuel**: candidate 2537, baseline 2537 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **kills**: candidate 28, baseline 28 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md

## The run

- label: `lab-selftest`
- candidate: `origin/main` -> `adc72ca65e67a3828aa93f64c6ed6d50e2423a7e`
- baseline: `origin/main` -> `adc72ca65e67a3828aa93f64c6ed6d50e2423a7e`
- pool: 5 maps, concurrency 6
- instrument: /mnt/forge/repos/Battlecode-2026--Vecticons/.venv/bin/cambc (4.0.0), engine 2.0.0
- started 2026-11-06T19:13:18Z, finished 2026-11-06T19:15:15Z (1.9 min)
- run directory: `/tmp/opencode/lab-runs/20261106T191318Z-535637`

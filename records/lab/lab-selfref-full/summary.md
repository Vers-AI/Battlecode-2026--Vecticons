# Lab verdict — lab-selfref-full

The lab played the 128-map pool twice on the same instrument (cambc 4.0.0, engine 2.0.0, `--release`): the candidate `origin/main` (adc72ca65e67) and the baseline `origin/main` (adc72ca65e67), both against `bots/starter-python`, both fresh in this run. The candidate recorded **109-19** and the baseline recorded **109-19** (+0 wins), against a noise band of ±2 win(s). **Verdict: PASS.** All hard gates passed and the record is not worse than the baseline beyond the measured band. The gems, fuel and kill deltas are reported but never gated: the F6 ablation showed a build with the feature REMOVED still loses hundreds of gems, so those totals re-roll on any binary change.

| metric | candidate | baseline | delta | gated |
|---|---|---|---|---|
| record (wins-losses) | 109-19 | 109-19 | +0 wins | **yes** |
| maps completed | 128/128 | 128/128 | - | yes |
| botFaults (ours / opponent) | 0 / 0 | 0 / 0 | - | yes |
| gems | 85953 | 85953 | +0 | no (reported) |
| fuel exhausted | 132989 | 132989 | +0 | no (reported) |
| champion kills | 962 | 962 | +0 | no (reported) |

## Verdict: PASS

**Noise band (active):** candidate record not worse than the baseline record by more than 2 win(s) on a 128-map pool (tolerance = ceil(128 * 1/70) = 2; measured: three different binaries all recorded 58-12 on the 70-map pool).

*Derivation:* measured: three different binaries all recorded 58-12 on the 70-map pool, so binary-change noise on the RECORD is <= 1 win per 70 maps; tolerance = ceil(pool_size * 1/70).

*Evidence:* `records/f3-campaign/`, `records/f6-campaign/`, `records/f6-campaign/ablation.md`

## Gates

- [PASS] **bot_faults** (hard): botFaults candidate_ours=0, candidate_opponent=0, baseline_ours=0, baseline_opponent=0
- [PASS] **candidate_complete** (hard): every map completed
- [PASS] **record_band** (primary): candidate 109 wins vs baseline 109 (delta +0, tolerance 2)

## Reported (never gated)

- **gems**: candidate 85953, baseline 85953 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **fuel**: candidate 132989, baseline 132989 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md
- **kills**: candidate 962, baseline 962 (+0) — reported only, never gated: gem totals re-roll by hundreds on ANY binary change (F6 ablation: the feature REMOVED still lost 233 gems) — see records/f6-campaign/ablation.md

## The run

- label: `lab-selfref-full`
- candidate: `origin/main` -> `adc72ca65e67a3828aa93f64c6ed6d50e2423a7e`
- baseline: `origin/main` -> `adc72ca65e67a3828aa93f64c6ed6d50e2423a7e`
- pool: 128 maps, concurrency 6
- instrument: /mnt/forge/repos/Battlecode-2026--Vecticons/.venv/bin/cambc (4.0.0), engine 2.0.0
- started 2026-11-06T19:38:39Z, finished 2026-11-06T20:21:55Z (43.3 min)
- run directory: `/tmp/opencode/lab-runs/20261106T193839Z-609159`

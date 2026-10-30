# F6 spot-check — the A/B variance table (and why the campaign had to be re-measured)

## The instrument's determinism, measured

This engine's match is bit-deterministic **only for an identical binary**.
Any code change — even a one-time extra engine-constant read that changes
nothing semantically — shifts the fuel burn by a few hundred units, and
the match is chaotic under the fuel ceiling: a map that ran out of fuel
at turn 1000 now runs out at turn 1001, a champion dies one step earlier
or later, and the whole map diverges.

That was measured, not assumed. Five builds were run on the same three
maps with the same instrument (cambc 4.0.0 / engine 2.0.0, `--release`,
`scripts/run_pool.py --concurrency 6`):

| label | what the binary is | 00gem_fields | 01elk_arena | 02treasure_box | total gems | total fuel | faults |
|---|---|---|---|---|---|---|---|
| `f6-base` | the F3 binary (`d6650c6`), byte-for-byte the shipped one | 1,047 | 72 | 324 | 1,443 | 4,283 | 0 |
| `f6-spot` | F6 (`aed8ca5`) | 1,004 | 72 | 417 | 1,493 | 4,853 | 0 |
| `f6-spot2` | F6 (`aed8ca5`), the SAME binary re-run | 1,004 | 72 | 417 | 1,493 | 4,853 | 0 |
| `f6-var-config` | F6 **minus** the lock-break, but **plus** only the `ALTAR_LOCK_HITS` constant read | 1,001 | 72 | 432 | 1,505 | 4,643 | 0 |
| `f6-var-noconfig` | F6 **with** the lock-break, but **minus** the `ALTAR_LOCK_HITS` constant read (the literal 5) | 1,039 | 72 | 317 | 1,428 | 4,393 | 0 |

Reading of the table:

1. **The baseline reproduces the F3 record exactly.** `f6-base` gives
   1,047 / 72 / 324 gems and 260 / 264 / 3,759 fuel — the identical rows
   the F3 70-map campaign recorded for those three maps
   (`records/f3-campaign/aggregate.json`). So the instrument and the
   harness are sound: an unchanged binary reproduces the shipped numbers
   to the gem.
2. **The same binary re-run reproduces itself exactly.** `f6-spot` and
   `f6-spot2` are identical in every cell. The run is deterministic; it
   is the *binary* that is the variable.
3. **A semantically inert change is not inert.** `f6-var-config` carries
   NO lock-break behaviour at all — it only adds the constant read — and
   it still moves every number (1,047 -> 1,001, 324 -> 432, fuel 4,283
   -> 4,643). The perturbation is the *fuel*, not the feature.
4. **The lock-break itself is not the whole perturbation.** `f6-var-noconfig`
   has the feature but not the constant read, and it differs again
   (1,039 / 72 / 317).

## Consequence for the F6 campaign

The F6 70-map campaign therefore **cannot** be compared map-by-map
against the F3 record and cannot be expected to reproduce it: the
binary is different, so every map's fuel trace is different, so every
map's outcome is potentially different. The campaign is judged on its
**own** aggregate against the F3 gates:

| gate | F3 reference | F6 must be |
|---|---|---|
| record | 58-12 | >= 58-12 |
| gems | 41,255 | >= 41,255 |
| botFaults | 0 | 0 (both sides) |
| fuel | 61,164 | <= 61,164 + 5% = 64,222 |

and this is reported as-is, without touching the bot to chase the
numbers. The variance above is the honest reason a reader should expect
the per-map rows to differ from F3 even where the aggregate is close.

The raw per-map stats files for all five labels are under `results/f6-*/`
in the worktree (the repo keeps `results/` gitignored, like every other
campaign). The aggregate and table for the shipped run (`f6-spot`) are
committed here as `aggregate.json` and `table.md`.

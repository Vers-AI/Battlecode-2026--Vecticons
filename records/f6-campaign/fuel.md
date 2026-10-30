# The fuel line — F6 campaign vs the F3 reference

## The measurement

| run | fuel exhausted | vs the F3 reference |
|---|---|---|
| F3 (the reference) | 61,164 | — |
| **F6 (the shipped build)** | **59,746** | **-1,418 (0.98x)** |
| F6-ABLATION (feature removed) | 60,582 | -582 (0.99x) |

The gate is `fuel <= 61,164 + 5% = 64,222`. The F6 campaign is at
**59,746** — **1,418 units BELOW the reference**, so the fuel gate
passes with 4,476 units of headroom.

## Why the fuel went DOWN, not up

The F6 feature adds work to the bank-candidate scan: the locked altar
is no longer filtered out, so the scan evaluates one more candidate per
locked altar, and the locked candidate costs a field read
(`fielding.altar_distance`) that the old filter's `continue` skipped.

That cost is paid **only when an altar is actually locked**. In the
70-map campaign no altar was ever locked (`bots/starter-python` places
no locks; verified by grepping every campaign log for `f6: lockbreak`
and `lockbreak+` — zero hits). So the extra work never happened and
the fuel line should be *flat*.

It is not flat: it is 1,418 lower. That is the perturbation, not the
feature: the F6 diff also changes the instruction mix of the no-lock
path slightly (commit `aed8ca5` moved the `ALTAR_LOCK_HITS` read into
the locked branch, so the no-lock path pays one extra assignment and
one extra branch per altar), and on this engine a few hundred fuel of
delta moves a map's outcome chaotically (the measured spread in
`records/f6-spot/variance.md`).

The ablation confirms the sign of the effect is not the feature's: it
also sits below the reference (60,582) while carrying no lock-break
logic at all.

## The per-map fuel spread

| map | F3 fuel | F6 fuel | delta |
|---|---|---|---|
| 00breadcrumbs | 2,443 | 805 | **-1,638** |
| 01the_farm | 1,188 | 2,234 | **+1,046** |
| 00gemfight | 1,387 | 823 | -564 |
| 01big_world | 2,621 | 2,248 | -373 |
| 00vault | 333 | 61 | -272 |
| 00gem_fields | 260 | 310 | +50 |

The spread goes both ways by more than 1,600 units on individual maps
and nets out at -1,418 over the pool — the same chaotic signature as
the gem line. It is not a systematic fuel saving and is not claimed to
be.

## Honest summary

The F6 feature's own fuel cost in this campaign is **zero** (the locked
branch never executed). The measured -1,418 is the perturbation of a
changed binary, and the reference number itself is only reproducible
with the reference binary. The fuel gate is reported as **PASS** on the
number, with that caveat attached.

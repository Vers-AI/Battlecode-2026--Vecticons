# The F6 ablation — the feature REMOVED, everything else the F6 build

## Why an ablation was needed

The F6 campaign came in at **40,839 gems** against the gate's
**41,255** — a FAIL of 416 gems. Two explanations were possible:

* **(a)** the lock-break feature costs gems, or
* **(b)** the campaign's shortfall is the fuel-perturbation chaos this
  engine shows under ANY binary change (measured on the spot-check:
  `records/f6-spot/variance.md` — even a build with the feature
  REMOVED but one extra engine-constant read added diverges from the
  F3 rows).

The campaign cannot separate them on its own: the feature never fired
(no altar was ever locked — `bots/starter-python` places no locks), so
the F6 run and a feature-free run are the same game with a different
fuel trace.

## How the ablation was built

A separate git worktree at `/tmp/opencode/f6-ablation-wt` (detached at
`391e368`), with the F6 source **reverted for the three files that
carry the feature**:

    git checkout d6650c6 -- bots/vecticons/src/managers/utility.py \
                           bots/vecticons/src/bot.py \
                           bots/vecticons/src/managers/execution.py
    .venv/bin/cambc build --release bots/vecticons

`config.py` was left at the F6 version (so the `ALTAR_LOCK_HITS`
constant read — and its fuel cost — is still there). Verified: zero
occurrences of `lockbreak` in the three reverted files, and
`ALTAR_LOCK_HITS` still present in `config.py`.

Then the identical instrument:

    .venv/bin/python3 scripts/run_pool.py --project-root . \
        --bot-a bots/vecticons --label f6-ablation --concurrency 6 --release

70/70 maps, no error, no timeout.

## The result

| run | feature | record | gems | vs the 41,255 gate | fuel | faults |
|---|---|---|---|---|---|---|
| F3 (the reference, byte-identical binary) | absent | 58-12 | 41,255 | **PASS** | 61,164 | 0 |
| **F6-ABLATION** | **REMOVED** | 58-12 | **41,022** | **FAIL (-233)** | 60,582 | 0 |
| F6 (the shipped build) | present | 58-12 | **40,839** | **FAIL (-416)** | 59,746 | 0 |

## What it proves

**A build with the lock-break feature entirely absent ALSO fails the
gems gate, by 233 gems.**

So the gems shortfall is NOT caused by the feature:

* the feature's own worst-case contribution is 416 - 233 = **183
  gems** — and that is an upper bound, since the ablation carries its
  own perturbation;
* **-233 of the -416 is present with the feature gone**: it is the
  fuel-perturbation chaos of a changed binary, and it would be there
  for any commit that touches the bot;
* the record is **58-12 in all three runs** and the faults are **0/0 in
  all three runs** — the perturbation moves individual maps, not the
  gate structure.

## What it does NOT prove

The ablation is not a clean control, and it is not claimed to be:
removing the feature is itself a code change, so the ablation carries
its own (different) fuel trace and its own chaotic outcome. 48 of its
70 maps differ from F3 in gems and 4 change result — the same
signature as the F6 run. Two different binaries of the same bot both
land under the gems gate while the reference binary lands exactly on
it, which is the honest statement of what was measured: **on this
instrument, the gems gate has a ~0.5-1% intrinsic sensitivity to the
binary, and the F6 run sits inside that band.**

It also cannot tell us what the feature would do in a campaign where
locks actually happen — because in the 70-map pool nothing locks an
altar. That question is answered separately, against the locker probe
bots, in `records/f6-spot/README.md`.

## The raw numbers

The ablation's per-map rows are in `per-map-diff-vs-f3.md` (the ABL
columns). Its raw stats files stay under `results/f6-ablation/` in the
ablation worktree (outside the repo, as the repo keeps `results/`
gitignored).

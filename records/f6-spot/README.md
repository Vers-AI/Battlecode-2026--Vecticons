# F6 spot-check (3 maps) — the lock-break bank candidate

**Instrument:** cambc 4.0.0 / engine 2.0.0, `--release`,
`scripts/run_pool.py --concurrency 6`, our bot vs `bots/starter-python`,
the 70-map pool (`maps/*.map26a`).

**Code:** branch `f6-lockbreak`, commits `8715504` (F6, the lock-break bank
candidate) + `aed8ca5` (the hot-path tweak that keeps the break cost out
of the no-lock path).

**Maps:** `00gem_fields`, `01elk_arena`, `02treasure_box` — the third one
is the single-altar map that motivated F6 (exactly ONE altar, at
(17,17)).

## What F6 changed

An enemy-locked altar used to be **filtered out** of the bank candidates
(`utility._bank_candidates`), so on a single-altar map the banker had no
bank candidate at all and the economy went inert. It is now a **scored**
alternative in the same argmax — priced, not gated:

* the candidate's score carries the engine constant `ALTAR_LOCK_HITS`
  (5 attack-turns) on top of the walk and the tribute channel:
  `base * discount ** (d + 5 + channel) * mod`, and the **same**
  `_threat_adjust` runs on the longer exposure window, because the banker
  stands next to the altar while it hammers the lock;
* the score **bound** carries the same `+5`, so the early-break (the
  bound-based pruning) stays correct;
* the lock no longer removes the altar from the argmax — it only makes it
  more expensive, so a multi-altar map still routes around the lock by
  arithmetic while a single-altar map breaks it instead of idling;
* the **enemy-locks-only** rule stands: our own locks never block our
  bankers (`_altar_locked` is unchanged);
* the lock test is still the one bitboard read per altar that was already
  there — no new per-turn loop.

No scenario detection, no map names, no exceptions.

### The "no scenario detection" check, run on the F6 diff

`git diff d6650c6..HEAD -- bots/vecticons/src` was grepped for every map
name in `maps/*.map26a` and for `treasure`: **no hits**. An AST scan of
the whole bot source confirms the only occurrences of map names are
inside **docstrings** — pre-existing F3 documentation of measured
behaviour ("71 deaths on 01boss_battle in the S2.2 reference") — never in
a comparison, a branch or a lookup. The `TREASURE` strings in
`config.py` are the engine's **emblem family** name (Vesper Vale's
`"VESPER_VALE": "TREASURE"` passive), pre-existing since F3 and
unrelated to any map; the F6 diff adds no occurrence of it.

The lock test itself is one bitboard read per altar — the same read the
old filter did — so no new per-turn loop was introduced.

## The action side

The engine converts a tribute attempt against a locked altar into an
attack on the lock (GameRules:242). Probed directly against the engine:
`me.submit_gems(altar)` against an ENEMY-locked altar returns OK, the
lock's `hits_remaining` goes 5 -> 4 -> 3 -> 2 -> 1 -> gone over five
attempts, and `is_submitting_gems()` stays False the whole time (no
tribute channel is started). A following `me.attack(lock)` raises
`Error(41, 'Attack already used.')` — the conversion had already spent
that turn's attack.

So there is **no new action**: `execution.submit_gems_to_altar` takes a
`break_lock` flag that bypasses the enemy-lock guard, and the bot's bank
branch lets the engine do the conversion. The lock-break evidence line
`f6: lockbreak hit #N ... altar=(x,y) r=R` is printed by the bot.

## Result

    3-0 | 1,493 gems | 4,853 fuel | 0 botFaults (both sides)

| map | W/L | gems | opp | deaths | fuel | rounds |
|---|---|---|---|---|---|---|
| 00gem_fields | W | 1,004 | 542 | 19 | 331 | 1,054 |
| 01elk_arena | W | 72 | 0 | 8 | 283 | 2,000 |
| 02treasure_box | W | 417 | 118 | 18 | 4,239 | 2,000 |

The `f6-spot2` re-run of the SAME binary reproduced every cell exactly
(1,004 / 72 / 417, fuel 331 / 283 / 4,239).

## The lock-break actually fires

`bots/starter-python` never places an altar lock, so the lock-break path
is exercised against a throwaway **locker probe** bot (kept outside the
repo, in `/tmp/opencode/f6probe/`). On the single-altar map our bot vs.
the locker probe:

    10 "f6: lockbreak hit" lines, in two rounds of 5 (r=547..552 and
    r=721..725) on the altar at (17,17) — five hits per lock, exactly
    ALTAR_LOCK_HITS.

The full extract is in `lockbreak.log`.

## The before/after controls (the headline)

Measured against the **F3 baseline binary** on the same maps with the
same probes:

| map | altars | enemy lock | F3 baseline | F6 |
|---|---|---|---|---|
| **02treasure_box** | 1 | the only one | **0 gems** — the inert economy | **1,006 gems, W** |
| 01yellow_brick_road | 10 | one | 51 gems, **0 breaks** | 88 gems, **0 breaks** — routed around, unchanged |
| 01starfish | 2 | both | 842 gems (routed) | 880 gems (broke both) |
| 01starfish | 2 | the near one | 842 gems (routed) | 749 gems (broke the near lock) |

The evidence class is fixed and the multi-altar route-around control
holds on the 10-altar map. The 2-altar map is the boundary case and is
reported as it measured: with a `TIME_DISCOUNT` of 0.99 the break costs
~5% of the score, so the break wins whenever the open alternative is
more than ~5 honest path turns away — the 3-tile gap on `01starfish` is
not 5 path turns. Full detail, including the probes' construction, in
`controls.md`.

## The caveat that matters for the campaign

This engine's match is bit-deterministic only for an **identical binary**.
Any code change — even a semantically inert one — shifts the fuel burn by
a few hundred units and the match diverges chaotically under the fuel
ceiling. Measured and tabulated in `variance.md`: the unchanged F3 binary
reproduces the F3 rows to the gem (1,047 / 72 / 324), the same binary
re-run reproduces itself exactly, and three *different* binaries give
three different sets of numbers — including a build with the lock-break
feature REMOVED, which still differs from the baseline.

Consequence: the F6 70-map campaign is judged on its **own** aggregate
against the F3 gates (record >= 58-12, gems >= 41,255, 0 botFaults, fuel
<= 61,164 + 5%), not map-by-map against F3, and the perturbation is
reported honestly rather than chased by weakening the bot.

## Files

* `aggregate.json` — the machine-readable spot-check record.
* `table.md` — the per-map table.
* `variance.md` — the A/B variance measurement (the baseline-vs-F6 table).
* `controls.md` — the before/after locker-probe controls (the single-altar
  evidence class and the multi-altar route-around controls).
* `lockbreak.log` — the lock-break evidence from the locker-probe match.
* `*.run.log` — the harness stdout of the three map runs.

The raw per-map `.stats.json` files stay under `results/f6-spot/` in the
worktree (the repo keeps `results/` gitignored, like every other
campaign).

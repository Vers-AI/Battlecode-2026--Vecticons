# F1 close-out — INDEPENDENT VERIFICATION (worker, 2026-10-29)

This is a second pair of eyes on the close-out campaign that the orchestrator
ran (`records/f1-closeout/`, commit `a8b3d41`). It does not re-run the
campaign; it **measures the campaign against a matched control** and
attributes the deltas, because the campaign's own comparison
(`vs dominance-v1`) mixes two things: the code change and the run-to-run
noise of the harness.

**Instrument (identical for every run below):** CLI 4.0.0 / engine 2.0.0,
`--release`, the 70-map pool, `scripts/run_pool.py`, concurrency 4, one
campaign per label. Every `stats.json` reads `match.engineVersion = 2.0.0`.

---

## 1. The matched control — the measurement the close-out was missing

The close-out run was compared against `records/dominance-v1/` (the dad8b55
campaign). That campaign was measured on a **different build** (the repo's
2.2.4-era SDK, before the `ffb5790` refresh) and on a different byte
layout, so it cannot separate "the close-out changed the bot" from "the
harness/build changed the outcome".

So I ran the **control**: the *same dad8b55 code*, a fresh build, the same
instrument, the same 70 maps, the same harness.

| run | record | gems | fuel | kills (opp) | faults | maps |
|---|---|---|---|---|---|---|
| **close-out** (`a8b3d41`) | 53-17 | 38,332 | 26,012 | 458 (216) | 0 / 0 | 70/70 |
| **control** (dad8b55 code, fresh build) | **56-14** | **38,967** | **22,495** | **551 (201)** | 0 / 0 | 70/70 |
| dad8b55 campaign (recorded, old build) | 55-15 | 38,703 | 22,230 | 508 (207) | 0 | 70/70 |

The control vs the recorded dad8b55 campaign is the **noise band** of the
harness — identical code, identical maps, identical engine:

> ±1 win · ±264 gems · ±265 fuel · ±43 kills

**Everything the close-out moved is outside that band:**

| metric | close-out − control | noise band | verdict |
|---|---|---|---|
| wins | **−3** (53 vs 56) | ±1 | real regression |
| gems | **−635** | ±264 | real regression |
| fuel | **+3,517 (+15.6%)** | ±265 | real regression — **the cost-down scope did not deliver** |
| kills | **−93** | ±43 | real regression |
| botFaults | 0 / 0 | — | clean |

**The gates still PASS** (they are absolute, not relative): record 53 ≥ 46,
gems 38,332 ≥ 32,876, 0 faults both sides, 70/70 maps. But measured against
a matched control, the close-out tree is **worse than the tree it was
derived from on every metric**. The scope was "fuel cost-down only"; the
fuel went **up** 16%.

## 2. Where the regression comes from (four-way diagnostic, 10 maps)

Same maps, same instrument, four builds (the two diagnostic builds are
one-line changes):

| build | gems | kills | fuel |
|---|---|---|---|
| control (dad8b55) | 5,426 | 111 | 6,602 |
| control + kill gold 100 (the `038db02` sync alone) | 5,377 | 89 | 6,514 |
| close-out, kill gold 100 (the shipped tree) | 3,943 | 66 | 9,414 |
| close-out, kill gold 50 | 4,912 | 72 | 7,667 |

* The **kill-gold sync** (`038db02`, owner-directed: price a kill at the
  engine's real 100 gold instead of 50) is a real but *small* effect on the
  base tree — and it is the whole story on `02stalactites`
  (close-out 203 gems / 2,491 fuel → 1,063 gems / 718 fuel with the kill
  gold back at 50, matching the orchestrator's own diagnostic).
* The **close-out mechanisms** carry the rest: gems −514, kills −39,
  fuel +1,065 against the control on the same ten maps.
* The **EV prune is kill-neutral** (an 11-map diagnostic with the prune
  disabled: 158 kills with the prune, 155 without, 164 in the control) and
  accounts for only ~6% of the fuel gap. It is not the regression.
* The **chase commitment is inert** (the orchestrator's own
  `CHASE_COMMIT_FACTOR` 1.0-vs-2.0 test: identical results). It is not the
  regression either.

## 3. `01yellow_brick_road` — the protected map, and why it moved

The close-out loses YBR with 8 gems / 3 camps (the control wins with 432
gems / 39 camps on the same instrument). I instrumented the bot to print
its state every 50 rounds (the probe build is the shipped tree plus that
one print):

```
PROBE r=250 VARG   pos=(6,17) kind=capture note=d=1 v=12 commit
PROBE r=1950 STELLAR pos=(6,17) kind=capture note=d=1 v=12 commit
PROBE r=2000 WILLOW pos=(4,16) kind=capture note=d=1 v=12 commit
```

**The champions are not idle — they are stuck in a capture OSCILLATION.**
From r≈200 every champion picks the `capture` candidate with `d=1` (one
tile away) and walks between adjacent tiles of the YBR row
((4,16) → (5,17) → (6,17) → (7,17) → (8,16)). Stepping off a group's spawn
tile resets the engine's five-round capture timer, so **no capture ever
completes** — 3 camps in 2,000 rounds, 8 gems banked.

Two facts rule out the close-out scoring as the cause:

* **The EV prune and the chase commitment are inert on that map**
  (`prunes=0, commits=0, boosts=0` in the campaign's own counters for
  YBR), so neither mechanism can have removed a fight or a chase.
* **The same source with a heavier per-turn fuel burn farms normally**:
  the S2.3-trace build of the identical tree (the trace print burns fuel
  every turn) clears 39 camps and banks **547 gems — a WIN**. The
  oscillation is therefore sensitive to the per-turn timing, not to a
  scoring policy.

Consequence for the board: **the YBR gate is not a code property on a
changed tree.** The capture cycle on that row is balanced on a knife edge
that any change to the per-turn fuel/timing flips (a comment in the
source, a print, a kill-gold value: 8 g, 21 g, 79 g, 369 g, 432 g, 501 g,
547 g have all been measured from the same code with one-line changes).
The `YBR ≥ 100` gate should be read as *the map's outcome in this build*,
not as a pass/fail of the change. A fuel cost-down is exactly the kind of
change that moves that balance.

## 4. The gemfight replay look (item 4) — cross-checked

The orchestrator's one-pass verdict (`gemfight-verdict.txt`) is confirmed
by the campaign stats: the team **takes** champion damage and returns
almost none, and only the ranged champion (Willow) retaliates. The cause
is the candidate geometry — the `fight` candidate exists **only** for an
enemy inside *our* attack range while `flee` fires for every enemy in
contact, so a melee champion (reach 1) under a ranged attacker (reach 3)
has no fight candidate and a high-value flee candidate: it runs instead of
closing. The remedy (an out-of-range counter-attack candidate) is a **new
feature**, outside the close-out scope, so the owner's ruling applies —
accept the kill count and ship.

## 5. A harness hazard worth recording (it hit both campaigns)

The campaign harness builds the bot into a **shared** path
(`bots/vecticons/.cambc/build/bot.wasm`), so four concurrent `cambc run`
processes can read a half-written WASM: the failure is
`error: unexpected end-of-file (at offset ...)`. It hit the close-out
campaign on `00the_line` and my control on `00oasis`; both were re-run
clean and re-aggregated (`scripts/rebuild_aggregate.py`). A one-line fix
would be a per-worker build directory — out of scope here, logged.

## 6. Verdict

* **The campaign PASSES the owner's close-out gates** (record ≥ 46-24,
  gems ≥ 32,876, 0 botFaults both sides, 70/70 maps, engine 2.0.0,
  `--release`): `records/f1-closeout/gatecheck.txt`.
* **The three prescribed mechanisms are live and measured** on stdout:
  threat cache **412,685 hits / 0 misses**, EV prunes **3,119**, chase
  commits **1,637** (boosts 1,886) — no dead scaffolding this time.
* **The scope's own goal was not met**: measured against a matched control
  the fuel is **+3,517 (+15.6%)**, and the record/gems/kills are down
  (−3 / −635 / −93). The owner ruling waived the fuel gate and re-bases
  the reference to this run, so the gates stand — but the ladder is
  shipping a tree that measures worse than the one it replaced, on every
  metric, except that both clear the absolute gates.
* **The owner's decision (not the worker's):** the ruling says "on PASS,
  submit v4". This run PASSED, so the close-out tree is the v4 candidate
  and the submission bundle is ready
  (`records/f1-closeout/submission-vecticons.zip`). If the owner prefers
  the better-measured bot, the control tree (`dad8b55`, 56-14 / 38,967 /
  22,495 / 551 kills) is the stronger ladder candidate — but it skips the
  close-out scope, and the kill-gold sync (`038db02`) and the SDK refresh
  (`ffb5790`) that are already in the tree are owner-directed and are not
  mine to revert.

## Files

| file | what |
|---|---|
| `verification-independent.md` | this document |
| `control-base-dad8b55/aggregate.json` | the control campaign aggregate (the matched base) |
| `control-base-dad8b55/table.md` | the control campaign per-map table |

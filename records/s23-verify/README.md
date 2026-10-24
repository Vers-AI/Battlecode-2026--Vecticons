# S2.3 verification — RECOVERED tree (s23-recovered @ efe8c55)

Independent re-verification of the S2.3 stage on the recovered tree.
Instrument line: **CLI 3.0.4 / engine 1.0.2** (`/tmp/cambc304/bin/cambc`),
patched 40-map pool, opponent `bots/starter-python`, `--release`,
`scripts/run_pool.py`, concurrency 4, campaign label `s23-verify`.

The tree under test is the recovered fix4-era checkpoint (`efe8c55`,
restored from stash `073f933`, the 03:52 state). The fix4 campaign numbers
were lost with the overnight run; this campaign re-establishes them.

## Result

| quantity | value |
|---|---|
| **record** | **20-20** |
| **submitted gems** | **16,169** |
| **fuelExhausted (team A)** | **18,813** |
| botFaults (team A / team B) | **0 / 0** |
| maps run | 40 / 40 ok (2000 rounds each unless the match ended earlier) |
| crashes (both teams, all maps) | 0 |
| bot errors / tracebacks (team-a stdout) | 0 |
| wall clock | 5.5 min |

## Gates (vs S2.2's verified record 18-22 / 14,004 gems / fuel 25,230)

| gate | requirement | measured | verdict |
|---|---|---|---|
| record | >= 18 wins | 20 wins (20-20) | **PASS** |
| aggregate gems | >= 14,004 | 16,169 (+2,165) | **PASS** |
| fuelExhausted total | <= 25,230 | 18,813 (-6,417) | **PASS** |
| botFaults, both sides | 0 | 0 / 0 | **PASS** |
| 01boss_battle courier deaths at the sealed door | 0 | **0** | **PASS** |
| Manhattan proxies in `utility.py` | grep-clean | 0 matches | **PASS** |
| `cambc build --release` | passes | `bot.wasm` built | **PASS** |

Per-map gem deltas vs the S2.2 verify table are platform effects, the
same phenomenon recorded in the S2.2 determinism addendum (the engine is
deterministic per input set on a given server; different hosts produce
different per-map outcomes). Both campaigns clear the ratchet.

## Build + smoke (pre-campaign)

```
/tmp/cambc304/bin/cambc build bots/vecticons --release   -> built bots/vecticons/.cambc/build/bot.wasm
cambc run bots/vecticons bots/starter-python --map 00ambush       --statistics --json --release
cambc run bots/vecticons bots/starter-python --map 01boss_battle  --statistics --json --release
```

Both smoke matches: 0 crashes, 0 botFaults both sides, team-a stdout
clean (no `bot error`, no traceback).

## The two mechanism gates

### 1. Sealed altar — courier deaths at the sealed door = 0

See `boss_battle.md` for the full derivation. Summary:

* 01boss_battle has exactly **one altar, at (25,20)**, and exactly **one
  walkable tribute-approach tile: (24,20)** — which is the **Monolith's
  own spawn tile** (camp handle 20 of camp group 16, whose other camp,
  handle 27, sits at (23,20)).
* The engine's `SUBMIT_GEMS_RANGE` is **1**, so the approach ring is the
  altar's 8 neighbours; every other neighbour is terrain wall, hole or
  the altar tile itself. The single approach tile is the Monolith's.
* The scoring wall set is **terrain + the static overlay of the excluded
  groups' spawn tiles** (the Monolith is excluded: 20,000 base HP >
  `KILLABLE_HP_CAP` 5,000). (24,20) is therefore a wall in the scoring
  field, `fielding.altar_distance` reads it back as the engine's
  unreachable sentinel, and `_bank_candidates` drops the altar: the
  sealed altar **scores 0 by arithmetic**, it is not excluded by a gate.
* Measured: **0 of our 62 deaths on 01boss_battle were caused by the
  Monolith door group** (killer camp handles 20 / 27), and **0
  gemSubmission entries** across all five champions in 2,000 rounds —
  the bot never even attempted a tribute, so no courier ever walked to
  the door. The 2026-10-21 93-death courier loop is dead **by mechanism**.

### 2. Honest distances — no Manhattan proxies

`grep -rn "manhattan" bots/vecticons/src/` -> no matches;
`grep -cE "abs\(...\) + abs\(" bots/vecticons/src/managers/utility.py` -> 0.
`utility.py` contains **no `abs(` call at all**: every candidate's `d`
comes from the shared per-turn engine `DistanceField`
(`managers/fielding.scoring_field`), with `unreachable` targets scoring 0.
The two surviving straight-line helpers (`_straight_distance`,
`_anchor_distance`) are Chebyshev **pool pre-filters only** — their
docstrings state they never replace the field in a candidate's score, and
the farm pool pays exactly one field read per surviving candidate.

## Evidence files

| file | content |
|---|---|
| `aggregate.json` | the campaign record (per-map rows + totals) |
| `table.md` | the human-readable per-map table |
| `fuel.json` | per-map fuelExhausted for both sides |
| `boss_battle.md` | the courier-death gate derivation (map topology, killer attribution) |
| `candidate_kinds.md` | per-map candidate-kind census from the replays' team-a stdout |

Raw campaign artefacts (`results/s23-verify/`, `replays/`) stay gitignored
per the repo rule; they are reproducible with the command above.

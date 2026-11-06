# Vecticons — Battlecode 2026a

Champion bots for the Cambridge Battlecode 2026a event, written in Python against the `cambc` SDK (WASM/WASI, Wasmtime fuel budget).

The bot drafts a team, farms camps, captures spawn tiles, banks gems at altars, shops on spawn tiles, scouts with wards, denies altars with locks, and fights — all scored as one economy rather than as a sequence of scripted behaviours.

Status: v6 is live on the ladder (F3 closed out); F6 is merged but not submitted. The board is [`plan/tasks.md`](plan/tasks.md).

## Toolchain

| | |
|---|---|
| CLI | `cambc` **4.0.0** (`pip install cambc==4.0.0`) |
| Engine | **2.0.0** — build hash `85aa7167b44db7cea97509f55e997750520e33d644e9f4f90d6128c50eba1d21` |
| SDK pin | `cambc.toml` → `sdk_version = "4.0.0"` |
| Map pool | **128** maps in `maps/` (`.cambc/map-pool.json` lists them) |

Check the engine line before trusting any campaign. Older CLI/SDK combinations fail the newer pool maps with `engine error 7` — 19 of 70 maps failed on the 3.0.4 line — and a campaign that silently drops maps corrupts every aggregate. Confirm `match.engineVersion` reads `2.0.0` in the stats file.

## Layout

```
bots/vecticons/src/
  main.py            # dispatch: is_draft() -> draft.py, else bot.play_turn()
  draft.py           # draft picks + the 1,024-byte draft memory (v2 payload)
  bot.py             # per-champion turn logic — the shipped implementation
  bot_baseline.py    # frozen Stage 2 baseline, kept for A/B
  config.py          # ALL tunable constants — no magic numbers elsewhere
  randomness.py      # seeded PRNG (WASI randomness is zero)
  managers/
    observation.py   # per-turn facts (no decisions)
    utility.py       # candidate generation + scoring + argmax
    execution.py     # action -> SDK calls (SDK pathing helpers only)
    fielding.py      # shared per-turn DistanceField infrastructure
    intelligence.py  # enemy fog memory + the shared-memory beacon protocol
    scouting.py      # the ward mini-argmax
    topology.py      # static reachability / boss unlock values

plan/     architecture.md — the build spec · tasks.md — the stage board + work log
ref/      GameRules.md · BotAPIPython.md — the rules and the SDK reference
maps/     the 128-map pool
scripts/  run_pool.py (campaign harness) · lab.py (the evaluation lab) · one_map.py (single-match stdout reader)
tests/    test_lab.py (the lab's verdict unit tests)
records/  per-stage campaign evidence
```

Imports resolve only within `src/`; the wasm sandbox cannot reach sibling folders at the bot root.

## Quick start

```powershell
# Build the way the platform does
cambc build bots\vecticons --release

# One match, platform-faithful
cambc run bots\vecticons bots\starter-python --map 00contest --release

# With statistics
cambc run bots\vecticons bots\starter-python --map 00ambush --release --statistics stats.json --json

# A full-pool campaign
python scripts\run_pool.py --project-root <tree> --bot-a bots/vecticons --bot-b bots/starter-python --cambc <path\to\cambc>
```

`run_pool.py` and `one_map.py` default to Linux `--cambc` paths. Pass `--cambc` explicitly everywhere else.

## How the bot decides

Everything is denominated in gem-equivalents, so candidates from every subsystem compare directly. Each turn the utility layer generates candidates, scores them, and takes the argmax:

```
farm(g)    = value(g) × discount^(d + clear + capture) × P(survive) × region_risk
bank(a)    = gems × BANK_UTILITY_PER_GEM × P(survive the trip)
recall     = (heal + gold to spend) × discount^turns × P(channel survives)
fight(e)   = (bag + kill gold + denial + capture reset + tiebreak) × discount^turns × P(win)
chase(e)   = the same value × discount^(path + turns) × P(win) × CHASE_SUCCESS_FACTOR
flee(e)    = (base + carried) × hp_factor × escape_probability
boss(g)    = (drops + unlock value) × discount^turns × P(survive)
```

Distances are honest: one shared per-turn engine `DistanceField` rooted at our position. A target the field never reaches scores 0 — sealed altars and Monolith corridors fall out by arithmetic instead of a special case. The full contract, with citations, is in [`plan/architecture.md`](plan/architecture.md).

## Verification

One gated campaign before every platform submission, on a single instrument and a single host. Mid-work feedback is a 3-map spot-check.

Gates: record **and** aggregate gems at or above the immediately-prior verified record · 0 `botFaults` on both sides · `fuelExhausted` at or below the prior reference · `cambc build --release` passes.

Verified references (pool-70, engine 2.0.0): S2.4 verified **46-24 / 32,876 gems / ~11,093 fuel / 0 faults**; the F1 campaign at `dad8b55` recorded **55-15 / 38,703 gems / 22,230 fuel / 0 faults**.

Control maps, which a ratchet must not break:

| Map | Control |
|---|---|
| `01yellow_brick_road` | ≥ 100 gems, a win (reference 259) |
| `01elk_arena` | 72 gems, a win |
| `01boss_battle` | 0 courier deaths at the sealed door |

Compare runs only when the compiled bytes match. A cosmetic comment in a never-executed file once swung five per-map results by more than 100 gems, so per-map deltas inside the 100-gem noise floor mean nothing when the source differs.

## The lab

The lab is the agent-free version of "verify a candidate branch". It materializes the candidate and the baseline into two isolated throwaway worktrees under `/tmp/opencode/lab-runs/`, builds both with `cambc build --release`, plays the **same** map pool with the **same** instrument (`scripts/run_pool.py`, our bot vs `bots/starter-python`) on **both** sides — both fresh in the same run — and computes a machine verdict. It never touches the primary checkout, and it removes its worktrees even when it fails. `scripts/lab.py` is the whole thing; `tests/test_lab.py` holds the verdict's unit tests.

**The verdict.** Two hard gates (zero `botFaults` on both sides of both aggregates; the candidate completed every map, with one documented retry per failed map) and one primary gate: the candidate record against the baseline record inside a **noise band** of `ceil(pool_size / 70)` wins — 2 wins on the 128-map pool. The band is empirical, not a guess: three different binaries of this bot all measured **58-12** on the 70-map pool (`records/f3-campaign/`, `records/f6-campaign/` and the F6 ablation in `records/f6-campaign/ablation.md`), so a binary change moves the *record* by at most one win per 70 maps. The gems, fuel and kill deltas are **reported but never gated** — the same ablation shows a build with the feature entirely REMOVED still loses 233 gems, so those totals re-roll by hundreds on any binary change. The active band and its derivation are stated in every artefact the lab writes.

**By hand** (on the forge box, from a checkout):

```bash
# the full lab: candidate vs baseline, full pool, machine verdict
python3 scripts/lab.py --candidate <branch> --baseline origin/main

# the lab's own smoke check: both sides on the SAME ref, 5 maps,
# the two aggregates must come out byte-identical
python3 scripts/lab.py --candidate origin/main --self-test

# a quick look on a handful of maps
python3 scripts/lab.py --candidate <branch> --maps 00ambush,00anvil --label peek
```

Exit codes are the verdict: **0 = pass, 1 = fail, 2 = infra error**. Every run writes `records/lab/<label>/` — `verdict.json` (machine), `summary.md` (human: one paragraph, the comparison table, the verdict and the stated band) and `run.json` (both SHAs, timestamps, the map list and both aggregates). A lockfile (`.cambc/lab.lock`) keeps two campaigns from colliding: a second start exits 2 and names the active run, and a lock whose PID is dead or that is older than two hours is reclaimed. The lab is safe to launch from cron or systemd — no prompts, and the one `git fetch` is tolerated offline with a warning.

**The PR flow.** `.github/workflows/lab.yml` is `workflow_dispatch` only: it takes a candidate ref and a baseline ref, runs the lab, uploads `records/lab/<label>/` as an artefact and publishes the summary to the job summary. It targets `runs-on: [self-hosted, forge]` — the runner on the forge box — because a full run is 2 × 128 maps of local engine (~30–40 min) and the comparison is only valid on one instrument, one host. Until that runner is registered the job waits in the queue and never runs on a GitHub-hosted runner; once it is, the flow is: open the PR → dispatch the lab with the branch as the candidate → read the verdict from the job summary and the artefact → merge on a `pass`. A `fail` is a real signal (a regression beyond the noise band); an `error` (exit 2) means the instrument broke, not that the bot did.

## Working on the bot

[`plan/architecture.md`](plan/architecture.md) is the spec — read it before touching strategy. [`plan/tasks.md`](plan/tasks.md) tracks state and the work log. [`AGENT.md`](AGENT.md) holds the full operating rules; the short version: check a stage off only on verified runs, note any `plan/` edit in the commit message, watch the replay before prescribing a fix, keep file I/O out of `play_turn()`, and keep the PRNG seeded so the same seed gives the same behaviour.

## Scope

A single-sprint project, not a library: no public API, no installable package, no test suite beyond the campaign harness. The champion pool is 13 playable types, of which Prism, Cassi & Issac, and Heart of Ruin are still un-scored. Combined-arms scoring and the telemetry-v2 viewer schema are open.

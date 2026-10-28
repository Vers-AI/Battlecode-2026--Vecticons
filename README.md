# Vecticons — Battlecode 2026a

Champion bots for the Cambridge Battlecode 2026a event (`cambc2026a`), written in Python against the `cambc` SDK (WASM/WASI, Wasmtime fuel budget).

The bot drafts a team, farms camps, captures spawn tiles, banks gems at altars, shops on spawn tiles, scouts with wards, denies altars with locks — and fights, scored as one economy rather than as a sequence of scripted behaviours.

**Current state:** Sprint 2 complete (S2.0–S2.4), final sprint in execution (F1 dominance economics in closeout). See [`plan/tasks.md`](plan/tasks.md) for the live board.

---

## Toolchain

| | |
|---|---|
| CLI | `cambc` **4.0.0** (`pip install cambc==4.0.0`) |
| Engine | **2.0.0** — build hash `85aa7167b44db7cea97509f55e997750520e33d644e9f4f90d6128c50eba1d21` |
| SDK pin | `cambc.toml` → `sdk_version = "4.0.0"` |
| Map pool | **70** maps in `maps/` (`.cambc/map-pool.json` lists them) |

**Check the engine line before trusting any campaign.** Older CLI/SDK combinations cannot parse the newer pool maps and fail them with `engine error 7` — 19 of 70 maps failed on the 3.0.4 line, and a campaign that silently drops maps corrupts every aggregate. Confirm `match.engineVersion` reads `2.0.0` in the stats file.

---

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

plan/     architecture.md — THE build spec · tasks.md — the stage board + overnight log
ref/      GameRules.md · BotAPIPython.md — the rules and the SDK reference
maps/     the 70-map pool
scripts/  run_pool.py (campaign harness) · one_map.py (single-match stdout reader)
records/  per-stage campaign evidence
```

Imports resolve **only within `src/`** — the wasm sandbox cannot reach sibling folders at the bot root.

---

## Quick start

```powershell
# Build the way the platform does
cambc build bots\vecticons --release

# One match, platform-faithful
cambc run bots\vecticons bots\starter-python --map contest --release

# With statistics
cambc run bots\vecticons bots\starter-python --map ambush --release --statistics stats.json --json

# A full-pool campaign
python scripts\run_pool.py --project-root <tree> --bot-a bots/vecticons --bot-b bots/starter-python --cambc <path\to\cambc>
```

`scripts/run_pool.py` defaults `--cambc` to `<project>/.venv/bin/cambc` and `scripts/one_map.py` to `/tmp/cambc304/bin/cambc` — **both are Linux paths**. Pass `--cambc` explicitly anywhere else.

---

## How the bot decides

Everything is denominated in **gem-equivalents**, so candidates from every subsystem compare directly. Each turn the utility layer generates candidates, scores them, and takes the argmax:

```
farm(g)    = value(g) × discount^(d + clear + capture) × P(survive) × region_risk
bank(a)    = gems × BANK_UTILITY_PER_GEM × P(survive the trip)
recall     = (heal + gold to spend) × discount^turns × P(channel survives)
fight(e)   = (bag + kill gold + denial + capture reset + tiebreak) × discount^turns × P(win)
chase(e)   = the same value × discount^(path + turns) × P(win) × CHASE_SUCCESS_FACTOR
flee(e)    = (base + carried) × hp_factor × escape_probability
boss(g)    = (drops + unlock value) × discount^turns × P(survive)
```

Distances are honest: one shared per-turn engine `DistanceField` rooted at our position. A target the field never reaches scores **0** — sealed altars and Monolith corridors need no special case, they fall out by arithmetic.

The full contract, with citations, is in [`plan/architecture.md`](plan/architecture.md).

---

## Verification

**The rule:** one gated campaign before every platform submission — single instrument, single host, top-line gates only. Mid-work feedback is a 3-map spot-check.

Gates: record **and** aggregate gems ≥ the immediately-prior verified record · 0 `botFaults` both sides · `fuelExhausted` ≤ the prior reference · `cambc build --release` passes.

**Current ratchet reference** (pool-70, engine 2.0.0): S2.4 verified **46-24 / 32,876 gems / ~11,093 fuel / 0 faults**; the F1 campaign at `dad8b55` recorded **55-15 / 38,703 gems / 22,230 fuel / 0 faults**.

**Control maps** — a ratchet must not break these:

| Map | Control |
|---|---|
| `01yellow_brick_road` | ≥ 100 gems, a win (reference 259) |
| `01elk_arena` | 72 gems, a win |
| `01boss_battle` | 0 courier deaths at the sealed door |

**Same-bytes comparisons only.** Compiled module layout depends on exact source bytes — an `id()`-based tie-break once made a cosmetic comment in a never-executed file swing five per-map results by >100 gems. Per-map deltas inside the 100-gem noise floor are meaningless when the bytes differ.

---

## Repo rules that matter

1. **Read `plan/architecture.md` before touching strategy.** It is the spec; `plan/tasks.md` tracks state.
2. **Check off a stage only on verified real runs**, never on opinion. Commit after each stage passes.
3. **Never edit `plan/` spec content without noting the change in the commit message.**
4. **Eyes on the replay before any prescription.** Data alone is insufficient — system policies over scenario whack-a-mole.
5. **No file I/O inside `play_turn()`.** Log via `print` only; wrap dev-only analytics in `cambc.debug_block` and keep game logic out of it.
6. **Determinism.** Seed the PRNG from the spawn tile + start position; same seed ⇒ same behaviour.

Full operating rules: [`AGENT.md`](AGENT.md).

---

## Scope and limitations

**Purpose.** This README is the entry point for a human reader: what the bot is, how to build and run it, and how a change gets verified. [`plan/architecture.md`](plan/architecture.md) is the authoritative *spec* — when the two disagree, the spec wins.

**Key decisions.** One currency (gem-equivalents) for every candidate, so subsystems compare directly; honest distances from a shared engine `DistanceField`, so unreachable targets score 0 instead of needing special cases; one gated campaign per submission, so a stage is verified once, on one instrument.

**Limitations.** This is a single-sprint project, not a library — no public API, no installable package, no test suite beyond the campaign harness. The champion pool is 13 playable types with three special cases (Prism, Cassi & Issac, Heart of Ruin) still un-scored. Combined-arms scoring and the telemetry-v2 viewer schema are open. The vault mirror of the spec is re-synced by hand, so it can lag this file.


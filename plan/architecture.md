# Vecticons Sprint 1 — Build Spec

**Event:** Cambridge Battlecode 2026a · Sprint 1 locks Sun 18 Oct 23:59 BST
**Repo:** Vers-AI/Battlecode-2026--Vecticons · engine cambc 2.2.4 (`pip install cambc==2.2.4`; primary checkout's venv at `/mnt/forge/repos/Battlecode-2026--Vecticons/.venv/bin/cambc`)
**Game rules:** `ref/GameRules.md`, `ref/BotAPIPython.md` (repo). Every number below cites those files.
**Vault mirror:** the owner's Obsidian note "Vecticons Bot Architecture (Battlecode 2026a)" mirrors this file; this copy is canonical for coding work.

---

## Core design: utility theory as the decision engine

The whole game is denominated in one currency (gems), so the bot's core loop is: **score every candidate action in gem-equivalents-per-turn, take the argmax.**

- Score = value ÷ (turns to realize) — time-discounted utility (the GDC time-to-utility notes)
- Carried-gem risk: value of farming longer vs. risk of dying and losing the carry → principled banking thresholds, not constants
- Region risk: gems × P(survive) — crude constant per region for Sprint 1, estimated later

**Key architectural claim:** if each agent maximizes well-formed local utility, synergy emerges *without* coordination. The draft plan pre-commits assignments; geometry deconflicts greedy agents. The 8-byte channel stays nearly silent — a feature, not a failure.

---

## Manager architecture (minimum viable set)

| Manager | Runs | Job |
|---|---|---|
| **Draft** | once, pre-match | Pick champions; write the 1,024-byte plan; precompute map cache (camps, altars, spawns, distances) with full visibility + 10× fuel |
| **Observation/State** | per-turn, cheap | Assemble facts: HP, gold, carried gems, position, vision, draft cache. No decisions. |
| **Utility/Decision** | per-turn | Generate candidate actions, score each, argmax |
| **Execution** | per-turn | Chosen action → SDK calls (`reachable_tiles()`, `movement_mask()` — never hand-rolled A*) |

Shopping + combat start as *rules inside the utility function* (recall's score spikes when HP low or gold high), promoted to managers when they earn their own opinions.

**Build order:** draft manager + map cache → state manager → **hardcoded baseline loop** (nearest killable camp → clear → capture → carry → tribute) as the measurable floor → *then* replace hardcoding with the utility layer, A/B against the baseline.

---

## File structure (verified 2026-10-17, server test)

The wasm sandbox resolves imports **only within `src/`**. Verified empirically: sibling folders at bot root (`Managers/`, `Observations/`) raise `ModuleNotFoundError` even with `sys.path` insertion; subfolders inside `src/` work. The scaffold's root-level `Managers/` and `Observations/` folders are unimportable — delete them.

```
bots/vecticons/
  src/
    main.py            # entry point. Dispatch loop: is_draft() -> draft.py, else bot.play_turn(). Calls cambc.end_turn().
    draft.py           # draft-phase picks + draft memory write
    bot.py             # per-champion turn logic — ALWAYS the shipped implementation
    bot_baseline.py    # frozen copy of the Stage 2 baseline, kept for A/B (created in Stage 3)
    config.py          # ALL tunable constants. No magic numbers in any other file.
    randomness.py      # seeded PRNG (copy starter's; WASI randomness is zero)
    managers/
      __init__.py
      observation.py    # per-turn state assembly (facts only, no decisions) — renamed from knowledge.py 2026-10-21
      utility.py       # candidate action scoring, argmax
      execution.py     # action -> SDK calls (SDK pathing helpers only)
  pyrightconfig.json   # keep: extraPaths ../../sdk/python
```

Import rule: `from managers.utility import ...` (src-relative) only. No imports from bot root.

## Stage 0 — Draft memory format (frozen)

Little-endian. Written by draft.py during draft phase. Max 1,024 bytes. Sprint 1 layout:

```
offset  size  field
0       1     format version = 1
1       1     pick count (from cambc.draft.team_size())
2       1     shared-memory bit map: bit i = champion i owns shared-memory byte i
3       1     reserved 0
4       7*n   per pick (n = pick count):
              +0   champion id (ChampionDraft enum value)
              +1   assigned altar index (0-based, game_map.altars() order)
              +2   camp zone id (nearest-camp-to-spawn split computed at draft)
              +3   role id (0=clearer, 1=banker, 2=support)
              +4-6 reserved 0
```

## Stage 1 — Draft manager

Pick order: **Ryth → Stellar → Vesper Vale**; if `team_size() == 5`, append **Varg, then Willow**. No other conditional logic. Precompute camp group tiles, altar positions, per-zone camp lists at draft (full map visible, 10× fuel) into the Stage 0 layout.

**Done when:** a match runs on all 17 maps, zero exceptions in team stdout; `team_size()` value logged during draft; draft memory submitted ≤ 1,024 bytes; `cambc build --release` passes.

## Stage 2 — Knowledge manager + baseline loop (hardcoded)

observation.py (was knowledge.py, renamed 2026-10-21) assembles per-turn facts (position, HP, gold, gems carried, visible camps/enemies/altars, draft plan). bot.py runs the baseline loop:

1. If channeling (recall/tribute/teleport), dead, or cannot act: return.
2. If on spawn: buy shards per `CHAMPION_STAT_PRIORITY`.
3. If HP < `RECALL_HP_FRAC` and not on spawn: recall.
4. If enemy champion in attack range: attack it.
5. If `gem_count >= BANK_THRESHOLD`: path to nearest unlocked altar (skip `vision.has_altar_lock` altars), submit in range.
6. Else: farm nearest killable camp.

**Killable camp (v1):** camp group whose monsters' total visible HP ≤ `KILLABLE_HP_CAP`, excluding Monolith (20,000 HP). Unseen camps count as killable if their group is in this champion's draft zone.

**Done when:** 10 matches vs starter-python — maps: the first 10 of the 17-map pool alphabetically (ambush, collision, contest, corners, eight, elkring, expanse, fragment, interlock, lanes) — zero exceptions, and the stats output shows ≥1 completed clear→capture→tribute cycle per match. First harness task: run one `--statistics stats.json --json` match, record the actual emitted field names here, and restate the criteria against those fields.

### Actual emitted stats.json fields (probed 2026-10-17 against cambc 2.2.4, map=ambush)

Top-level keys: `championTimelines`, `champions`, `diagnostics`, `events`, `formatVersion` (=1.0), `heatmaps`, `jsonFormatVersion` (=1.0), `match`, `roster`, `teamTimelines`, `teams`.

- `match.{roundsCompleted, roundsStarted, outcome, seed, width, height, engineBuildHash, engineVersion, finalInput, finalRoundPartial, finalSubstep, mapHash, metricSemanticsVersion}` — match metadata; `outcome` ∈ `"teamA"|"teamB"|"draw"` (winner; values observed: `"teamB"` with a `"Team B (starter-python) wins: reached 1000 gems in round 1415"` reason line printed by the CLI).
- `diagnostics.botFaults.{teamA, teamB}` — integers (count of `botFault` events per team). Sprint 2 done-when: this is **0** for both sides.
- `diagnostics.codes` — list of engine error codes encountered.
- `diagnostics.detailedCaptureComplete` — boolean; events/timelines are only complete when `True`.
- `teams[i].submittedGems` — string int (sentinel-style), team gem total.
- `roster.champions[i].{id, name, team, draftPick}` — per-champion identity.
- `roster.draft` — list of `{champion, team}` in pick order.
- `champions[i].metrics.{assists, collectedGems, deaths, earnedGold, gems, gold, kills, monsterDamageDone, monsterDamageTaken, saleRefunds, spentGold, submittedGems, championDamageDone, championDamageTaken, healingDone, healingReceived, shieldingDone}` — per-champion aggregate metrics (numeric, all `str` for integer columns).
- `champions[i].items`, `itemsBought`, `consumables`, `consumablesBought` — inventory deltas.
- `events[]` — list of `{kind, owner:{id, kind, name, team}, entries:[{amount, context, target, timestamp:{round, phase, substep, input, ordinal}, value, x, y}, ...]}`.
- `events[].kind` observed: `death, kill, assist, wardPlacement, buyItem, buyConsumable, sellItem, campClear, gemSubmission, gemCollection, gemLoss, itemCombination, botFault, fuelExhausted, resignation` (one entry per champion).
- `championTimelines`, `teamTimelines`, `heatmaps` — lists of per-tick data (large; we ignore in Sprint 1).

### Restated criteria against the actual fields

For **Stage 2 done-when**, run `cambc run bots/vecticons bots/starter-python --map <m> --rounds 2000 --statistics stats.json --json` on the 10 named maps (ambush, collision, contest, corners, eight, elkring, expanse, fragment, interlock, lanes) and require each match's `stats.json` to show:

1. **Zero bot faults** — `diagnostics.botFaults.teamA == 0` AND `team-b.stdout` contains no `bot error` line.
2. **Zero exceptions in team-a stdout** — `team-a.stdout` contains no `bot error` line.
3. **Stage 2 cycle** — there exists at least one `gemSubmission` event on `events` whose `owner.team == "teamA"` (i.e. ≥1 tribute completion by our team), AND there exists at least one `campClear` event on `events` whose `owner.team == "teamA"` AND `entries` is non-empty (clear with non-zero entries). Together this proves a clear → capture → tribute cycle occurred for team A.
4. **`cambc build --release` passes** before any of the matches.

Cycle detection is satisfied even for partially-completed matches so long as the gem-submitted entry's `value` (gems submitted) is > 0. If a map's seed legitimately never yields a tribute within 2000 rounds, the run is reported as a Stage-2 cycle miss and we log it (does not block other maps).

## Stage 3 — Utility layer

First action: copy Stage 2's bot.py to bot_baseline.py (frozen A/B reference). Then rewrite bot.py's decision steps with scoring; main.py and the entry contract never change.

Candidate generation gate: the Stage 2 killable-camp filter (KILLABLE_HP_CAP, Monolith exclusion, config.py) runs BEFORE scoring — camps failing the filter never become candidates. No scoring term can resurrect a filtered camp; without this the argmax feeds the bot to the Monolith (highest gem count, 20,000 HP).

Scoring, in config.py units:
- `farm_camp(camp)` = `gems × TIME_DISCOUNT ** turns_to_reach_and_clear`
- `bank(altar)` = `gem_count × BANK_UTILITY_PER_GEM` when `gem_count ≥ BANK_THRESHOLD`
- `recall` = `RECALL_UTILITY` when `hp_frac < RECALL_HP_FRAC`
- `attack_enemy` = `KILL_REWARD_GOLD × TIME_DISCOUNT ** turns_to_kill_estimate`

Argmax wins; ties break by config.py priority order. A/B = run the same map set against this repo at the Stage 3 commit vs the bot_baseline.py state (git checkout), not runtime branching.

**Done when:** over the 17-map pool (deterministic engine — same bots + map = same result), the utility bot's per-map record vs starter-python ≥ the baseline bot's record. Report the per-map table.

## config.py (all tunables; source or PLACEHOLDER tag on every value)

```python
# Sources: GameRules camp table, champion stats, mechanics sections.
# PLACEHOLDER = first-pass value for A/B tuning; not final.

BANK_THRESHOLD = 1           # PLACEHOLDER. gems; tribute channel = ceil(gems/10) turns.
                              # Starter bot banks at gems > 0; Stage 2 baseline matches that to
                              # prove the cycle works. Stage 3 utility layer re-tunes via
                              # death-risk scoring.
TIME_DISCOUNT = 0.9          # PLACEHOLDER. per-turn multiplier
RECALL_HP_FRAC = 0.35        # from starter bot heuristic
KILLABLE_HP_CAP = 5000       # PLACEHOLDER. > elk pack total HP, < Monolith 20k. Stage 2
                              # baseline needs the floor to engage the common wolf/elk packs
                              # or the bot never starts a cycle. Stage 3 utility layer keeps
                              # the same value to gate the argmax (Monolith trap).
DEATH_RISK_BASE = 0.05       # PLACEHOLDER. unused Sprint 1
BANK_UTILITY_PER_GEM = 1.0  # PLACEHOLDER
RECALL_UTILITY = 50.0       # PLACEHOLDER
KILL_REWARD_GOLD = 50        # GameRules "When a champion dies"
TRIBUTE_RANGE = None         # set at import from cambc.constants.SUBMIT_GEMS_RANGE
CAPTURE_ROUNDS = 5           # GameRules capture section
RESPAWN_ROUNDS = 60          # GameRules "When a champion dies"

CHAMPION_STAT_PRIORITY = {   # GameRules champion stat tables
    "Ryth": "HEALTH", "Stellar": "FOCUS", "Vesper Vale": "STRENGTH",
    "Varg": "STRENGTH", "Willow": "FOCUS",
}
```

## Build sequence and verification commands

```
cambc run bots/vecticons bots/starter-python --map <m> --rounds 2000 --statistics stats.json --json
cambc run bots/vecticons bots/starter-python --map <m> --release   # platform-faithful; run before any submission
cambc build --release                                               # submission gate
```
17 maps in `maps/`. Replays: `replays/*.repl26a` zip bundles (replay + per-team stdout/debug/fuel).

---

## Memory & vision model (implementation constraints)

- **Draft memory (1,024 B, pre-match, read-only in match):** Stage 0 format above. Read via `cambc.DraftMemoryBuffer()` per instance.
- **8-byte shared memory:** the *only* live channel between instances. Byte ownership map written at draft (Stage 0 byte 2) so Sprint 2 signaling needs no format change. **Sprint 1: unused** — the channel stays clear by design; reserve bandwidth.
- **Per-champion vision (no ally sharing):** every Observation-manager fact is local. Movement into unseen occupants returns `False` — retry another step, never raise.

---

## Champion strategy notes (Sprint 1 picks)

- **Sprint 1 pool:** champions whose kits score cleanly under the standard utility function (no carried-gems terms, no linked-death logic): **Ryth, Stellar, Vesper Vale**; extend with **Varg, Willow** if the map forces 5 picks (draft code queries `team_size()`, never hardcodes).
- **Special cases (deferred):** **Prism** (carried-gems term in personal utility), **Cassi & Issac** (two linked instances — coordination-shaped).
- **Sustain:** Willow is the only healer — for Sprint 1, sustain = recall + shopping rules inside the utility function; healing enters when combined-arms scoring does.

---

## Coordination roadmap

- **Sprint 1:** zero shared-memory traffic. Pre-assigned zones, independent greedy utility.
- **Sprint 2:** write-only signals ("altar contested", "enemy seen at X") in owned bits; read-all.
- **Sprint 3+:** negotiation protocol if measurable value exists — e.g., combined-arms focus-fire score ("my utility is higher *because* Ryth is adjacent"), respawn-aware camp schedules.

---

## Telemetry plan

- **v1 (Sprint 1):** harness script — N matches via `cambc run --statistics stats.json --json`, parse to win rate, gems tributed, camps cleared, deaths. Enough to A/B utility vs. baseline.
- **v2 (Sprint 2):** per-round log → PIG_Bot-style viewer. Extend the existing viewer to ingest this game's CSV schema rather than building new.

---

## Sprint 1 scope (frozen)

Stage 0 → 1 → 2 → 3. Picks: Ryth, Stellar, Vesper Vale (+ Varg, Willow when `team_size() == 5`). Shared memory unused. Prism and Cassi & Issac excluded (special-case kits). Telemetry: Stage 2 harness.

## Open questions (non-blocking)

- Telemetry v2 schema: how to extend the PIG_Bot viewer to this game's stats
- When does the utility layer earn combined-arms scoring (the "my utility is higher because Ryth is adjacent" term)?
- Exact stats.json field names (Stage 2 harness task resolves)
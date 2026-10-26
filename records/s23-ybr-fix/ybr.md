# 01yellow_brick_road — the burning stage, root cause and fix

**Gate:** camps cleared **> 5**, gems submitted **> 100**, `fuelExhausted`
**< 1,500**, 0 botFaults.

| build | gems submitted | camps | deaths | fuel | faults | result |
|---|---|---|---|---|---|---|
| S2.3 record (`records/s23-verify/table.md`) | 0 | 1 | 2 | 4,837 | 0 | **L** |
| HEAD control `0ac1555` (same host) | 0 | 1 | 1 | 15 | 0 | **L** |
| shipped tree, **old** gold rate 0.05 | 20 | 16 | 15 | 611 | 0 | **L** |
| **shipped tree, gold rate 0.02 (this commit)** | **259** | **32** | 49 | **1,157** | 0 | **W** |

All four rows are measured on this host with the same instrument
(CLI 3.0.4 / engine 1.0.2, `--release`, `bots/starter-python`, 2,000
rounds). Row 3 is the shipped tree with **only** `GOLD_TO_GEM` reverted to
0.05 — it isolates the price change from the two target-selection fixes.

## 1. What was burning

The map's economy is 472 gems in total — below the 1,000-tribute win
threshold — so the map is decided on **submitted gems** and then on
**lifetime gold** (GameRules: "compare tributed gems, then lifetime gold
earned").

The bot was never short of gems; it was short of *submitted* gems, and it
never reached the altars at all:

* At HEAD (`0ac1555`) the map is **inert**: 1 camp cleared, **0 gems
  collected**, **0 `gemSubmission` entries**, 1 death, 15 fuel. The
  champion stands on a dead camp for the whole match.
* With the two target-selection fixes in place but the **old** gold price,
  the bot finally farms — and farms the wrong thing: 99 gems collected,
  **50 of them destroyed by 15 deaths**, **20 submitted**, and a single
  `gemSubmission` entry in 2,000 rounds.
* With the new price: 336 gems collected, 77 destroyed by 49 deaths,
  **259 submitted** through **22 `gemSubmission` entries**, 32 camps
  cleared — and the map is **won**.

## 2. Why it burned there (three measured causes)

**(a) The gold -> gem rate mispriced a zero-gem row at 30 gem-equivalents.**
`GOLD_TO_GEM` was 0.05, i.e. 20 gold per gem. The map's two 600-gold rows
(30 gold clusters each, span 29 tiles) were therefore priced at **30
gem-equivalents** — more than every real gem cluster on the map (26-45 gems),
and those were additionally discounted by the survival term (a defended
cluster scored 1.1-5.9). The argmax walked 29 tiles across a row that holds
**zero** gems. Gold is the *second* tiebreak and its only sink is the shard
shop, which can only run while a champion stands on a spawn tile — a delayed,
rate-limited conversion — while a gem is the win condition itself and the
*first* tiebreak. **Fixed: `GOLD_TO_GEM` 0.05 -> 0.02 (50 gold per gem).**
Isolated in the table above: 20 -> 259 submitted gems, 1 -> 22
`gemSubmission` entries.

**(b) The farm goal was a dead tile.** The farm candidate's goal was the
group's static representative tile — the spawn tile of the group's *first*
camp. When that camp was killed, the anchor became a dead tile: the distance
stayed 0, the score stayed maximal, and the walk refused to move because we
already stood on the goal tile. The champion was pinned on the dead camp for
the whole match (measured: tile (5,17) from round 19 to round 2,000).
**Fixed: the goal is a LIVE camp of the group** (`Snapshot.group_live_tiles`,
filled every turn — a dead camp is never reported by `vision.camps()`, so the
visible camps *are* the live ones; `camp.spawn_position()` is a free
attribute, so the per-turn goal costs no engine read).

**(c) The clear time ignored the group's span.** A 30-camp row spread over 29
tiles was priced like a 4-camp cluster: the HP term dominated and the walk
across the group was free. **Fixed: the group's Chebyshev span is charged to
the clear time** (`farm_rows[11]`).

## 3. Why the fix is general (no map-specific behaviour)

* `GOLD_TO_GEM` is one global conversion constant. It changes no map's
  routing directly; it corrects a *relative price*. The same change keeps
  `00stampede`'s gold tiebreak intact (still a 0-0 **win**) and does not
  change `01elk_arena` (still 72 gems banked).
* The live-camp goal and the span pricing are properties of every camp group
  on every map. `grep -rniE "<map names>" bots/vecticons/src/` finds map names
  only in **comments** that document measurements — never in code.
* The change set contains **no** `if map == ...` branch, no per-map
  constant, and no map-specific candidate.

## 4. The controls the fix must not break

| control | requirement | measured (this commit) | verdict |
|---|---|---|---|
| `01elk_arena` | 72 gems banked | 72 gems, W | PASS |
| `01boss_battle` | 0 courier deaths at the sealed door, 0 `gemSubmission` | **0** `gemSubmission` entries, **0** deaths by door camps 20/27, **0** deaths on door tiles (23,20)/(24,20) | PASS |
| `00collision` | control map | 259 gems, L (HEAD: 263, L) | PASS |
| `00stampede` | 0-0 gold tiebreak still ours | 0-0, **W** | PASS |
| `botFaults` | 0 | 0 (both sides, all 40 maps) | PASS |

The sealed-altar numbers come from `records/s23-verify/boss_battle.md`'s
gate definition and are reproducible from the campaign's
`01boss_battle.stats.json`.

## 5. A note on the numbers quoted in `config.py`

The `GOLD_TO_GEM` comment in the source cites the measurements that
*motivated* the change (142 gems collected / 87 destroyed / 46 submitted at
the old rate, 310 collected / 263 submitted at the new rate). Those were taken
on an earlier build of the same change set, before the deterministic
tie-break was added — and the tie-break changes the compiled module, which
changes the whole match (see `layout-noise.md`). The table in section 1 is
the measurement of the **shipped build** and is the one to compare the
campaign against.

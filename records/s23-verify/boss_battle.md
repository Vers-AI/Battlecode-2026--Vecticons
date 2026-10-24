# 01boss_battle — the courier-death gate

Gate (S2.3 done-when): *"boss_battle-class sealed maps show zero courier
deaths at sealed doors — the 93-death courier loop from the 2026-10-21
replay is dead by mechanism (scored unreachable), not by exclusion."*

Measurement from the `s23-verify` campaign (2,000 rounds, `--release`,
CLI 3.0.4 / engine 1.0.2).

## 1. The map's sealed door

Probed with a temporary bot (`cambc run` on the same map file), then
deleted:

* **Altars: exactly one, at (25, 20).**
* **Walls**: the whole band `y = 19, 20, 21` for `x = 0..21` and
  `x = 26..49`, plus `(22, 20)`.
* **Holes**: `(24, 19)`, `(24, 21)`, `(25, 19)`, `(25, 21)`.
* **Camp group 16** — 400 gems / 600 gold, two camps, both `kind = 4`
  (MONOLITH) — occupies **(23, 20)** and **(24, 20)**: the door tiles,
  immediately west of the altar.
* Camp handles: the Monolith's two camps are **20** (tile 24,20) and
  **27** (tile 23,20).

So the map is a wall band across the middle with a single gate column
(`x = 22..25`), and the Monolith sits in that gate, right in front of the
only altar.

## 2. Why the bank candidate is never generated

`SUBMIT_GEMS_RANGE` (the engine's tribute range) probes as **1** — you
must stand *directly next to* an altar to pay tribute. The altar's
8-neighbour ring at (25, 20) is:

| tile | status |
|---|---|
| (24, 20) | **walkable — it is the Monolith's own spawn tile** |
| (23, 20) | Monolith's second tile (not adjacent to the altar) |
| (24, 19), (25, 19), (24, 21), (25, 21) | holes (block movement) |
| (26, 19), (26, 20), (26, 21) | walls |
| (25, 20) | the altar itself (a wall for movement) |

So the tribute-approach ring of the only altar contains exactly **one**
walkable tile — **(24, 20), the Monolith's tile**.

The S2.3 scoring wall set is *terrain + the static overlay of the
excluded groups' spawn tiles* (the owner's corrected two-wall-set
ruling). The Monolith is excluded by the killable filter (20,000 base HP
> `KILLABLE_HP_CAP` = 5,000), so `(23, 20)` and `(24, 20)` are **walls in
the scoring field**. `fielding.altar_distance` therefore reads (24, 20)
back as the engine's unreachable sentinel and returns `None`, and
`utility._bank_candidates` drops the altar:

```python
d, tile = fielding.altar_distance(snap, field, ax, ay)
if d is None:
    continue  # unreachable: scores 0 by arithmetic
```

The sealed altar is **scored 0**, not excluded by a gate.

## 3. The measured outcome

From `results/s23-verify/01boss_battle.stats.json`, team A (vecticons):

* **62 deaths** in 2,000 rounds.
* **0 of them were caused by the Monolith door group.** The engine's
  death entries name the killer (`target: {kind, id}`); the Monolith's
  camps are handles 20 and 27, and neither appears:

  | killer | count |
  |---|---|
  | camp 28 (elk, group 23) | 19 |
  | camp 68 (elk, group 21) | 15 |
  | camp 50 (elk, group 6) | 10 |
  | camp 58 (wolf, group 4) | 5 |
  | camp 12 (elk, group 8) | 4 |
  | camp 96 (wolf, group 21) | 2 |
  | camp 6 (wolf, group 4) | 2 |
  | camp 72 (elk, group 8) | 2 |
  | camp 36 (elk, group 17) | 1 |
  | enemy champion | 2 |

* **0 gemSubmission entries** for all five champions over 2,000 rounds —
  the bot collected 12 gem pickups and never paid tribute, because no
  bank candidate was ever generated. There was no courier to die.
* The bot's own stdout for the map shows `kinds=['farm', ...]` on every
  sampled turn and **never** a `bank` candidate (the same `bank` kind
  *does* appear in the replays of maps with reachable altars — e.g.
  00gem_fields, 00gemfight, 00reverse_spiral).

Deaths near the door region exist, but none is a courier death at the
sealed door: the two deaths within 2 tiles of the altar (r1501 at
(25,18) and r1980 at (23,19)) were caused by **enemy champions**, both
with 0 gems carried; the single camp-caused death within 3 tiles
(r1695 at (27,18), 8 gems lost) was killed by group 17's elk pack at
(27,17), not by the Monolith.

**Gate result: 0 courier deaths at the sealed door — PASS.**

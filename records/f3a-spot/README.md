# F3a spot-check (3 maps)

The first F3a evidence: the ability layer wired into the bot, measured on
three maps with the same instrument as the F1 campaign
(`.venv/bin/cambc 4.0.0` / engine 2.0.0, `--release`, `scripts/run_pool.py`).

## Result

    2-1 | 1,308 gems | 683 fuel | 0 botFaults (both sides) | 370 ability casts

Per map:

| map | W/L | gems | opp | deaths | fuel | rounds |
|---|---|---|---|---|---|---|
| 00ambush | L | 231 | 421 | 32 | 103 | 2000 |
| 00gem_fields | W | 1,005 | 294 | 24 | 395 | 1138 |
| 01elk_arena | W | 72 | 0 | 11 | 185 | 2000 |

## Ability counters (the F3a "casts > 0" gate)

    casts=370  damage=159  aoe=178  cc=0  heal=6  shield=11  dash=7  reset=9

Read out of the replay bundle's `team-a.stdout` by
`scripts/run_pool.py::harvest_f3a_counters` (the LAST `f3a:` line per
champion carries its final totals).

## Controls that held

- `00gem_fields` 1,005 gems (the F1 control on that map is in the same
  range), `01elk_arena` 72 gems - the two control values the F3a
  spot-check was pinned against.
- 0 bot faults on both sides.

## Caveats

- `00ambush` is a LOSS (231-421). That is EXPECTED at this stage: the
  F3a layer does not touch the transit policy yet, and 00ambush is a
  map where the respawn -> walk-back-alone -> die loop eats the team.
  The F3b transit/regroup work is the fix, and the verdict is judged on
  the full 70-map campaign, not on this spot-check.
- The `.stats.json` files are NOT committed (0.7-0.9 MB each); the
  aggregate, the table and the raw run logs are.

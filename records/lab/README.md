# The lab's records

One directory per run: `records/lab/<label>/`.

| file | what it is |
|---|---|
| `verdict.json` | the machine verdict: the ordered gate list, the active noise band (with its derivation) and the reported-never-gated deltas |
| `summary.md` | the human summary: one paragraph, the comparison table, the verdict, the stated band |
| `run.json` | the run record: both SHAs, the timestamps, the map list, the lock (including any reclaim), the retries and both aggregates |
| `candidate.aggregate.json` / `baseline.aggregate.json` | the two campaign aggregates (the harness's own records) |
| `candidate.table.md` / `baseline.table.md` | the per-map campaign tables |

The raw per-map stats and the run logs stay under the run directory
(`/tmp/opencode/lab-runs/<timestamp>/`) — they are megabytes and the
aggregates carry everything the verdict needs.

## The runs in here

### `lab-selftest` — the lab's own smoke check

`python3 scripts/lab.py --candidate origin/main --self-test`: both sides
played the SAME ref on a 5-map spread subset. The two aggregates came
out byte-identical, so the verdict is `pass` (exit 0).

### `lab-selfref-full` — the full-pool self-reference

`python3 scripts/lab.py --candidate origin/main --baseline origin/main`:
the whole 128-map pool, both sides, fresh in one run.

* candidate **109-19** | baseline **109-19** | delta 0 | 128/128 maps on
  both sides | 0 botFaults on all four counters | gems 85,953 (identical
  on both sides) | noise band ±2 wins (`ceil(128 * 1/70)`) | **pass**
  (exit 0) | 43.3 min wall clock.

The retry rule earned its place: `00quarry` failed on the candidate
side and `00monolith` + `01boss_battle` on the baseline side (`exit:1`
— the engine under the load of six concurrent processes on a twelve-core
box), and the single documented retry fixed all three.

### `lab-f3-vs-main` — two REAL different refs

`python3 scripts/lab.py --candidate origin/f3-ability-layer --baseline
origin/main`: two different branches, the 70-map pool they share (the
lab plays the intersection and records the maps it dropped), both sides
fresh in one run.

* candidate **58-12 / 41,255 gems / 61,164 fuel / 0 faults**
* baseline **58-12 / 40,839 gems / 59,746 fuel / 0 faults**
* noise band ±1 win (`ceil(70 * 1/70)`) | **pass** (exit 0) | 25.4 min.

Those two lines are the historical campaigns, reproduced exactly:

| | record | gems | fuel | faults |
|---|---|---|---|---|
| `records/f3-campaign/` (F3) | 58-12 | 41,255 | 61,164 | 0 |
| the lab's candidate side | 58-12 | 41,255 | 61,164 | 0 |
| `records/f6-campaign/` (F6) | 58-12 | 40,839 | 59,746 | 0 |
| the lab's baseline side | 58-12 | 40,839 | 59,746 | 0 |

The **record ties** (58-12 = 58-12) and the verdict is `pass`, while the
gem total moves by **+416** and the fuel by **+1,418** — the F6
perturbation, on the same two binaries, on the same day. Those two
numbers are REPORTED and never gated: a lab that gated them would have
failed the shipped F6 build for the reason the F6 ablation disproved.

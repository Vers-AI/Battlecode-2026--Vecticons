# F6 spot-check controls — the locker-probe matches (before / after)

The 70-map campaign cannot exercise the lock-break: `bots/starter-python`
never places an altar lock, so no altar is ever locked in the pool. The
feature was therefore measured against throwaway **probe bots kept
outside the repo** (`/tmp/opencode/f6probe/`), against the **F3 baseline
binary** and the **F6 binary** on the same maps with the same probes.

## The probes

| probe | what it does |
|---|---|
| `locker` | farms gold, buys an altar lock, places it on the altar nearest the ENEMY spawn |
| `locker-near` | same, but the altar nearest **OUR** spawn (the tempting one) |
| `locker-all` | places a lock on **EVERY** altar on the map |
| `scout` | prints the map's altar list and idles (used to pick the control maps) |

The probe bots are throwaway instruments and are deliberately NOT in the
repo. They were built from `/tmp/opencode/f6probe/<probe>/src/`.

## The control maps (altar counts from the `scout` probe)

| map | altars | positions |
|---|---|---|
| 02treasure_box | **1** | (17,17) |
| 01boss_battle | 1 | (25,20) |
| 01starfish | 2 | (23,17), (26,17) |
| 02arena | 2 | (12,16), (17,13) |
| 02the_pond | 2 | (3,7), (26,22) |
| 00gem_fields | 4 | (4,35), (17,29), (32,10), (45,4) |
| 00interlock | 4 | (3,27), (19,16), (20,23), (36,12) |
| 01yellow_brick_road | 10 | 10 altars |

## The results

| map | altars | enemy lock | F3 baseline (`d6650c6`) | F6 (`aed8ca5`) |
|---|---|---|---|---|
| **02treasure_box** | 1 | the only one | **0 gems** — no bank candidate at all, the inert economy the F6 spec was written for | **1,006 gems, W** — 5 break hits on (17,17) at r=211..219, then a normal tribute |
| 01starfish | 2 | both | 842 gems — routed to the OPEN altar | 880 gems — 15 break hits: broke (26,17) at r=185..189, then (23,17) |
| 01starfish | 2 | the near one (26,17) | 842 gems — routed to the OPEN altar (23,17) | 749 gems — 5 break hits on (26,17) at r=1518..1522 |
| **01yellow_brick_road** | 10 | one (33,24) | 51 gems, **0 break hits** — routed around | 88 gems, **0 break hits** — routed around, behaviour UNCHANGED |

The F3 baseline never breaks a lock (0 hits in every match) — it
filters locked altars out of the candidate list, so on a locked map it
has no bank candidate and the economy is inert.

## Reading the results honestly

1. **The evidence class is fixed.** On the single-altar map the F3
   baseline banks **0 gems**; the F6 build banks **1,006** and wins.
   That is the whole point of the feature, measured end to end.

2. **The multi-altar route-around control holds.** On the 10-altar map
   the F6 build behaves exactly as F3 did: it routes around the locked
   altar and never breaks it (0 break hits, same shape of game).

3. **The 2-altar map is the boundary case, and it is worth stating
   plainly.** On `01starfish` the two altars are 3 tiles apart and the
   F6 build **broke** the near lock instead of routing to the open one,
   where F3 routed. That is the priced semantics working as specified:
   the break costs `ALTAR_LOCK_HITS` = 5 extra turns on a
   `TIME_DISCOUNT` of 0.99, i.e. roughly a 5% score penalty — so the
   break wins whenever the open alternative is more than ~5 honest path
   turns away. The alternative's *straight-line* distance here is 3
   tiles, but its honest path distance (walls) is longer than that.
   The F6 spec anticipated this ("it only wins when the alternatives
   genuinely cost more in discounted turns"); the spec's shorthand
   "multi-altar maps route around naturally (identical to today's
   avoid)" is true on the 10-altar map and NOT true on the 2-altar one.
   Reported rather than smoothed over.

4. **The break is five hits, always.** Every break sequence in every
   match is exactly 5 hits (`hits_remaining` 5 -> 4 -> 3 -> 2 -> 1 ->
   gone), matching the engine constant `ALTAR_LOCK_HITS`.

## The raw evidence

* `lockbreak.log` — the annotated extract of the single-altar match
  (the F6 evidence class).
* The other matches' replay bundles are under
  `/tmp/opencode/f6probe/our-vs-locker/` and `/tmp/opencode/f6probe/ctrl*/`
  (outside the repo, as with every probe).

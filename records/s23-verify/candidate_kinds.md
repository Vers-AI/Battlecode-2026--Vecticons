# Per-map candidate-kind census (s23-verify)

Source: the campaign replays' `team-a.stdout`, the bot's own
`v16: r=<n> <champ> kinds=[...] best=<kind>` line (a regular print, so
it survives `--release`). The line is emitted for rounds 1-20 only,
so the census covers the opening of each match; `bank` is the
S2.3 sealed-altar probe (a `bank` candidate exists exactly when at
least one altar's tribute-approach ring is reachable under the
scoring wall set).

| map | sampled turns | kinds seen | `bank` ever generated |
|---|---|---|---|
| 00ambush | 95 | farm | no |
| 00collision | 57 | capture, farm | no |
| 00contest | 95 | farm | no |
| 00corners | 90 | farm | no |
| 00eight | 100 | farm | no |
| 00elkring | 100 | capture, farm | no |
| 00expanse | 79 | farm | no |
| 00fragment | 60 | capture, farm | no |
| 00gem_fields | 94 | bank, capture, farm | yes |
| 00gemfight | 76 | bank, capture, farm | yes |
| 00great_wall | 95 | farm | no |
| 00interlock | 76 | capture, farm | no |
| 00lanes | 45 | farm | no |
| 00lost_world | 90 | farm, fight | no |
| 00monolith | 95 | farm | no |
| 00packs | 57 | farm, recall | no |
| 00pilgrimage | 94 | capture, farm | no |
| 00quadrants | 90 | farm | no |
| 00reverse_spiral | 76 | bank, capture, farm, fight | yes |
| 00road_of_riches | 81 | capture, farm | no |
| 00rows | 76 | farm | no |
| 00rushbait_v2 | 74 | capture, farm, fight | no |
| 00split | 90 | farm | no |
| 00stampede | 95 | farm | no |
| 00swirl | 72 | capture, farm | no |
| 01big_world | 51 | farm | no |
| 01boss_battle | 72 | farm | no |
| 01broken_curve | 57 | capture, farm | no |
| 01concentrated_wealth | 95 | farm | no |
| 01corridors | 95 | farm | no |
| 01elk_arena | 93 | capture, farm | no |
| 01fracture | 90 | farm | no |
| 01gold_rush | 57 | capture, farm | no |
| 01hooks | 80 | capture, farm | no |
| 01islands | 63 | capture, farm | no |
| 01shortcut | 72 | farm | no |
| 01starfish | 95 | farm | no |
| 01starvation | 95 | farm | no |
| 01the_farm | 50 | farm | no |
| 01yellow_brick_road | 45 | farm | no |

## Reading

A `bank` candidate requires BOTH a reachable altar (at least one of the
altar's tribute-approach tiles reachable under the scoring wall set) AND
at least `BANK_THRESHOLD` = 1 gem carried, so within the sampled
opening rounds it only fires on the three maps where a gem camp was
already cleared and captured: 00gem_fields, 00gemfight and
00reverse_spiral. Its ABSENCE elsewhere is therefore not evidence of a
sealed altar — most maps simply had no gems in hand yet at round 20.

The authoritative sealed-altar evidence for 01boss_battle is in
`boss_battle.md` and covers the FULL 2,000 rounds: 0 gemSubmission
entries (no tribute was ever attempted, because no bank candidate was
ever generated) and 0 deaths caused by the Monolith door group
(camps 20 / 27). The census confirms the same thing from the bot's own
side: no `bank` kind on 01boss_battle, while the three maps with
reachable altars do produce one.

`capture` appears on 17 maps, `fight` on 4, `recall` on 1 — the S2.3
candidate set is exercised beyond farming.

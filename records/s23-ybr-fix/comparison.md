# Per-map comparison — s23-ybr-fix vs the three references

* `ship` = the gated campaign `s23-ybr-fix` (this commit).
* `head-control` = the SAME host, SAME harness, SAME instrument line, tree = HEAD 0ac1555
  (the shipped baseline before this change) — the fairest A/B.
* `worker` = records/s23-verify/table.md (20-20 / 16,169 / 18,813).
* `praxis` = records/s23-verify/praxis-verify/table.md (19-21 / 16,219 / 17,782).
* `head+cosmetic` = HEAD with ONE cosmetic comment appended to `bot_baseline.py` —
  a file that is never imported at runtime. Identical semantics, different compiled
  module => the per-map noise floor of the instrument (see `layout-noise.md`).

| map | ship | head-control | d | worker | d | praxis | d | head+cosmetic | d |
|---|---|---|---|---|---|---|---|---|---|
| 00ambush | 264 | 297 | -33 | 231 | +33 | 281 | -17 | 281 | -16 |
| 00collision | 259 | 263 | -4 | 459 | -200 | 179 | +80 | 210 | -53 |
| 00contest | 0 | 0 | +0 | 0 | +0 | 0 | +0 | 0 | +0 |
| 00corners | 756 | 543 | +213 | 520 | +236 | 562 | +194 | 691 | +148 |
| 00eight | 248 | 405 | -157 | 281 | -33 | 299 | -51 | 252 | -153 |
| 00elkring | 744 | 635 | +109 | 741 | +3 | 533 | +211 | 582 | -53 |
| 00expanse | 431 | 466 | -35 | 323 | +108 | 348 | +83 | 416 | -50 |
| 00fragment | 250 | 174 | +76 | 124 | +126 | 313 | -63 | 185 | +11 |
| 00gem_fields | 1009 | 1028 | -19 | 1058 | -49 | 1006 | +3 | 1015 | -13 |
| 00gemfight | 92 | 125 | -33 | 140 | -48 | 140 | -48 | 81 | -44 |
| 00great_wall | 994 | 1006 | -12 | 985 | +9 | 842 | +152 | 989 | -17 |
| 00interlock | 281 | 277 | +4 | 290 | -9 | 290 | -9 | 281 | +4 |
| 00lanes | 602 | 685 | -83 | 308 | +294 | 350 | +252 | 672 | -13 |
| 00lost_world | 602 | 634 | -32 | 142 | +460 | 592 | +10 | 141 | -493 |
| 00monolith | 720 | 695 | +25 | 742 | -22 | 626 | +94 | 676 | -19 |
| 00packs | 524 | 190 | +334 | 466 | +58 | 420 | +104 | 192 | +2 |
| 00pilgrimage | 438 | 429 | +9 | 428 | +10 | 435 | +3 | 472 | +43 |
| 00quadrants | 1008 | 1042 | -34 | 1053 | -45 | 1000 | +8 | 1027 | -15 |
| 00reverse_spiral | 646 | 452 | +194 | 560 | +86 | 492 | +154 | 576 | +124 |
| 00road_of_riches | 335 | 116 | +219 | 119 | +216 | 33 | +302 | 253 | +137 |
| 00rows | 698 | 591 | +107 | 473 | +225 | 625 | +73 | 542 | -49 |
| 00rushbait_v2 | 709 | 581 | +128 | 769 | -60 | 759 | -50 | 633 | +52 |
| 00split | 1001 | 1013 | -12 | 1009 | -8 | 1014 | -13 | 1009 | -4 |
| 00stampede | 0 | 0 | +0 | 0 | +0 | 0 | +0 | 0 | +0 |
| 00swirl | 97 | 139 | -42 | 182 | -85 | 129 | -32 | 241 | +102 |
| 01big_world | 406 | 525 | -119 | 239 | +167 | 271 | +135 | 525 | +0 |
| 01boss_battle | 0 | 0 | +0 | 0 | +0 | 0 | +0 | 0 | +0 |
| 01broken_curve | 468 | 521 | -53 | 693 | -225 | 655 | -187 | 525 | +4 |
| 01concentrated_wealth | 766 | 669 | +97 | 482 | +284 | 538 | +228 | 411 | -258 |
| 01corridors | 98 | 220 | -122 | 60 | +38 | 114 | -16 | 68 | -152 |
| 01elk_arena | 72 | 72 | +0 | 72 | +0 | 72 | +0 | 72 | +0 |
| 01fracture | 366 | 349 | +17 | 340 | +26 | 407 | -41 | 300 | -49 |
| 01gold_rush | 566 | 732 | -166 | 681 | -115 | 710 | -144 | 566 | -166 |
| 01hooks | 209 | 198 | +11 | 275 | -66 | 220 | -11 | 242 | +44 |
| 01islands | 58 | 58 | +0 | 0 | +58 | 0 | +58 | 58 | +0 |
| 01shortcut | 566 | 170 | +396 | 675 | -109 | 587 | -21 | 259 | +89 |
| 01starfish | 420 | 448 | -28 | 356 | +64 | 391 | +29 | 352 | -96 |
| 01starvation | 748 | 652 | +96 | 752 | -4 | 720 | +28 | 728 | +76 |
| 01the_farm | 449 | 14 | +435 | 141 | +308 | 266 | +183 | 392 | +378 |
| 01yellow_brick_road | 259 | 0 | +259 | 0 | +259 | 0 | +259 | 0 | +0 |
| **TOTAL gems** | **18159** | 16414 | +1745 | 16169 | +1990 | 16219 | +1940 | 15915 | -499 |

| reference | record | gems | fuel | >100-gem regressions (ship vs reference) |
|---|---|---|---|---|
| ship (s23-ybr-fix) | 25-15 | 18159 | 3911 | — |
| head-control (HEAD 0ac1555, same host) | 22-18 | 16414 | 1841 | 4 |
| worker record (s23-verify) | 20-20 | 16169 | 18813 | 4 |
| praxis record (s23-praxis-verify) | 19-21 | 16219 | 17782 | 2 |
| head + cosmetic comment (same semantics as HEAD) | — | 15915 | 1987 | 5 |

## Maps down by more than 100 gems

* vs **head-control**: 4 map(s) — `00eight` (-157), `01big_world` (-119), `01corridors` (-122), `01gold_rush` (-166)
* vs **worker**: 4 map(s) — `00collision` (-200), `01broken_curve` (-225), `01gold_rush` (-115), `01shortcut` (-109)
* vs **praxis**: 2 map(s) — `01broken_curve` (-187), `01gold_rush` (-144)

The `head+cosmetic` row is the instrument's noise floor: a comment that the
interpreter never executes still moves single maps by up to 493 gems and the
aggregate by 499 (`layout-noise.md`). The S2.3.2 change REDUCES the number of
>100-gem per-map regressions against both recorded campaigns: 8 -> 4 against the
worker record and 6 -> 2 against the praxis record.  For scale, the cosmetic-comment
control regresses 5 maps by more than 100 gems against HEAD itself.


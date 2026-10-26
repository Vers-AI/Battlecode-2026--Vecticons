# Patch-4 absorption audit (2026-10-26, Praxis)

Platform patch: cambc 4.0.0 / engine 2.0.0 (was 3.0.4 / 1.0.2). Pool 40 -> 70
maps: all kept-40 sha-UNCHANGED (prior campaign numbers stand for them); 30
new pulled sha-verified (00* series: anvil/breadcrumbs/crater/dunes/galaxy/
oasis/quarry/ribs/the_line/vault; 02* series: 13 maps; adgato_* : 5 community
maps). Mechanics rebalance in constants.py: PRISM_FOCUS_PER_GEM 0.2->1.5,
VESPER_VALE_BONUS_GOLD_PERCENT 20->33, MARCELLA_TRIBUTE_GOLD_PER_GEM 2 (new),
HEART_OF_RUIN missing-health damage bonus 0.5 (new). New native APIs:
awake/asleep_camp_occupancy bitboards (engine-native version of the S2.3.2
YBR fix's hand-rolled live-camp check), DistanceField.copy_from, Dash ability
metadata (champions with dash exist now — movement model change to audit in
Sprint 3). Stats schema: metricSemanticsVersion 1, extract() unchanged.

## Campaign: S2.3.2 bot (6754c72) on engine 2.0.0, full 70-pool, vs starter, --release

39-31 | 31,036 gems | fuel 5,922 | 0 botFaults. Harness concurrency 4.

- KEPT-40: 23-17 / 16,743 gems / fuel 3,947 (vs 25-15 / 18,159 / 3,911 on
  engine 1.0.2 — the rebalance costs ~2 wins and -7.8% gems; 2 W->L flips:
  00road_of_riches 335->238, 01big_world tiebreak loss at same 406 gems)
- NEW-30: 16-14 / 14,293 gems / fuel 1,975 (unknown-territory baseline)
- Key maps HOLD on 2.0.0: 01yellow_brick_road W 285 gems (the S2.3.2 fix
  survives the new engine), 01elk_arena W 72 (center bank intact),
  01boss_battle W 0-0 (sealed door intact, no courier deaths)

## New-map flags for Sprint 3 (mechanisms unverified, replay-watch rule)

- Zero-gem class: 00the_line (0/4 camps), 00vault (0/0), 02gift_wrapping
  (0/0, 31 deaths) — inert/sealed candidates
- Both-0 gold-tiebreak wins (boss_battle class): 02guardians, 02the_pack
- 02stalactites: W but 5 camps / fuel 959 — possible YBR-class fuel issue
- adgato_* community series: 1-4 vs starter — worst new-map class
- Strong wins worth keeping: 00quarry 1008-0, 00ribs 1007-0,
  02uncharted_lands 1013-345, 02harvest 817-11, 02famine 670-62

Verdict: bot is compatible with the patch (builds/runs/0 faults); the
S2.3.2 record transfers with a small rebalance cost. Version-3 submit
decision is the owner's.

# S2.2 tuning history & evidence

Two full 40-map campaigns (patched pool, --release, CLI 3.0.4 / engine 1.0.2,
vs starter-python). Region-risk factor swept first; BANK_CONTEST_PENALTY held
at 0.5 (one knob at a time, coordinate descent — S2.5 method preview).

## Response curve: REGION_RISK_FACTOR

| factor | record | gems | botFaults | fuelExhausted | gate vs S2.0 (16-24 / 13,392) |
|---|---|---|---|---|---|
| 0.70 | 14-26 | 12,977 | 0/0 | 24,961 | FAIL |
| 0.85 | 17-23 | 13,916 | 0/0 | 25,044 | **PASS** |

S2.0 reference: utility 16-24 / 13,392 / fuel 24,393 (records/s20-rebaseline).

## Mechanism findings (both campaigns)

- The consumers work as designed: campaign-wide camps +515, deaths −267
  vs S2.0 (avoidance redirects farming and saves champions).
- No hoard loop: collected == submitted everywhere (probe: 01gold_rush
  33/33, carried_at_end 0; 01starvation 1020/1006). Bank delivery intact.
- 0.70 failure mode: farm redirect steered the bot off enemy-trafficked
  GEM economies into safe GOLD clusters (01gold_rush: +110 camps, 33 gems,
  ~11.7k gold held). 0.85 recovers the record without losing the effect.
- 01gold_rush is factor-INSENSITIVE between 0.70 and 0.85 (identical 33
  gems both runs) — its regression is contest/routing-shaped, mechanism
  unverified (owner eyes-on-replay rule applies before any prescription).

## Beacon-contested flag evidence (done-when clause)

- 162 `s22:` stdout lines across 14 maps in the gated campaign (replay
  bundles, team-a.stdout): the beacon altar-contested flag scored down a
  chosen bank candidate 162 times ("contest" in the winning candidate's
  note, e.g. `s22: bank contest flag: d=6 contest risk=1 altar=(12,17)`).
- Altar-pick flips: 0 (instrumented counterfactual — utility compares the
  argmax with and without the contest penalty every bank turn and marks
  'flipped'; zero occurrences in 80 campaign+smoke matches).
- Structural reason: a ×0.5 penalty (≈7 discount-tiles) can only flip a
  pick between altars within ~7 tiles of each other; close altar pairs
  (e.g. 01starfish (23,17)/(26,17), 3 tiles) share a 4×4 beacon region,
  and region-quantized flags are all-or-nothing per region — so the near
  altar's local-exact flag is accompanied by a region-wide broadcast flag
  covering both. Sub-region flag resolution or a distance-scaled penalty
  is the S2.5/S2.3 lever if pick-flips are wanted.

## Per-map deltas vs S2.0 worth S2.3/S2.5 attention (gated campaign)

Regressions: 01gold_rush −363 (W→L), 01starfish −379 (W→L), 00road_of_riches
−281, 01fracture −136, 00split −125, 00corners −123, 00expanse −49 (W→L),
01elk_arena W→L on the 0-0 gold tiebreak. Gains: 01starvation +494,
01broken_curve +386, 00elkring +241 (W), 00collision +226 (W), 00eight +170,
00great_wall +142, 00swirl +115, 00fragment +118 (W), 01concentrated_wealth
+91 (W), 01corridors +67 (W). Mechanisms unverified (replay-watch rule);
the contested-gem-economy class (gold_rush/starfish) is exactly what S2.3's
real per-camp gem counts + fight/flee target.
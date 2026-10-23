# S2.2 independent verification (Praxis, 2026-10-23)

Orchestrator-run confirmation of the S2.2 PASS at e7a3267, independent of
the coder's local campaign (17-23/13,916/25,044): same harness
(scripts/run_pool.py), same instrument line as the S2.0 ratchet reference
(CLI 3.0.4 / engine 1.0.2, patched 40-map pool, vs starter-python,
--release), fresh worktree, fresh seeds.

Result: 18-22 | 14,004 gems | fuel 25,230 | 0/0 botFaults.
Gates vs S2.0 (16-24 / 13,392): PASS on both campaigns independently.
Evidence clause reproduced: 162 s22: contest-score-down lines in these
runs' replays (matches the coder's count); 0 pick flips, structurally
expected (close altar pairs share a 4x4 beacon region; sub-region
resolution queued for S2.3/S2.5).
Fuel 25,230 (+3.4% vs S2.0) — S2.2 carries no fuel gate; this number is
S2.3's prior-stage reference under the stage ratchet.

## Determinism addendum (2026-10-23, reviewer query)

Per-map deltas between the coder's campaign (17-23/13,916, Windows clone)
and this verify (18-22/14,004, Linux server) are PLATFORM effects, not
tree or race effects. Proven empirically at de0be88: two back-to-back
00collision runs produce bit-identical matches (seed 1761524031595596...,
811-310, 95 camps / 31 deaths — equal to the verify row); an archival
pair (S2.0 elk_arena campaign vs a manual run, hours apart) shares its
seed and result identically. The engine is deterministic per input set
on this server. Zero bot-code diff between d013c01 and e7a3267 (verified).
The verify table is the canonical, reproducible server record; the
coder's campaign is a valid second-environment run. Both clear the
ratchet. Environment-invariant details across both campaigns: 00ambush
82 camps cleared (S2.2 region-risk term ended the inert state);
01islands 4,985 fuelExhausted / 1 camp / 0 gems (TP-gated unreachable
door — S2.3 + B4 evidence).

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

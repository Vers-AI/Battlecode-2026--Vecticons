# S2.4 independent verification (Praxis, 2026-10-26)

Fresh 70-map campaign at 79b5481 (worktree, CLI 4.0.0 / engine 2.0.0,
--release, vs starter-python, concurrency 4). 00rushbait_v2 re-run solo
after a concurrent-run build-cache race (W 283-23, patched into aggregate).

FINAL: 46-24 | 32,876 gems | fuel 11,093 | 0 botFaults.
Worker instrument: 47-22 / 32,013 / 10,793 / 0. Both clear every S2.4
gate vs the p4-pool70 reference (39-31 / 31,036): record +7/+8 wins,
gems +1,863/+2,860. Protected maps hold on both instruments: YBR W 287/288,
elk_arena 72, boss_battle W 0 courier deaths. Features verified live:
wards (275+ placements), death-window shopping, altar locks (enemy-only
scoring fix at the three call sites), 4+ emblem combos.

Fuel note (owner ruling pending): 11,093 vs the inherited 5,922 guard —
the delta is the ward+lock per-turn actions (worker ablation: wards-off
runs ~174-433/map). Recommendation: re-base the fuel guard to the S2.4
feature set rather than treat as regression; the features bought +8 wins.

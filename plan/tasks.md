# Vecticons Sprint 1 — Task Board

Owner: Drekken. Orchestrator: Praxis. Coder: opencode sessions per stage.
Deadline: **Sprint 1 locks Sun 18 Oct 23:59 BST** — submission upload is the owner's manual step.

Rules for the coding AI:
- Read `plan/architecture.md` fully before starting a stage. It is the spec; this file tracks state.
- Check off a stage ONLY when its "Done when" criteria pass, verified with real cambc runs (not opinion).
- Commit after each stage passes. Never edit `plan/` spec content without noting the change in the commit message.
- Never touch YouTube, external services, or the submission portal. Repo and local matches only.

## Stages

- [ ] **Stage 0 — Draft memory format** (spec frozen in architecture.md; no code — format lives in draft.py from Stage 1)
- [ ] **Stage 1 — Draft manager** (pick order + draft memory write + precompute)
  - Done when: 17 maps, zero exceptions, team_size() logged, ≤1,024 B submitted, `cambc build --release` passes
- [ ] **Stage 2 — Knowledge manager + baseline loop**
  - Sub-task: harness first — run one `--statistics stats.json --json` match, record actual field names in architecture.md §Stage 2, restate criteria
  - Done when: 10 named maps (ambush…lanes), zero exceptions, ≥1 clear→capture→tribute cycle per match
- [ ] **Stage 3 — Utility layer**
  - First: copy Stage 2 bot.py → bot_baseline.py
  - Done when: 17-map record ≥ baseline record; per-map table in commit message

## Post-Sprint (not tonight)

- [ ] Telemetry v2 (PIG_Bot viewer extension)
- [ ] Primer + livestream embed ("Top 3 Things You Should Know")
- [ ] Repo README

## Overnight log (append-only; orchestrator and workers write here)

- 2026-10-17 22:30 — Setup: plan/ created, spec mirrored from vault, scaffold cleanup pending, Stage 1 session queued. —Praxis
- 2026-10-17 23:05 — Session start: Stage 1 in progress. Pick order Ryth→Stellar→Vesper Vale [+Varg,Willow if team_size==5], Stage 0 draft memory layout. —opencode
- 2026-10-17 23:25 — Orchestrator wake: Stage 1 VERIFIED independently (17/17 maps, 0 exceptions, team_size logged each draft, payloads 18-39 B, build --release OK) at 8c13981. Stage 2 in progress: worker alive under opencode service, 10-map harness 6/10 pass (ambush/collision/contest/corners fail - kill-but-no-pickup), iterating. Pushing branch. -Praxis
- 2026-10-18 02:10 — Stage 2 PASS: 10/10 maps on the named Stage 2 set. 9 maps with full clear→capture→tribute cycle (220-1016 gems tributed); 1 map (contest) is a stage-2 cycle miss (both teams 0 submittedGems - map has no gem-yielding camps this seed; architecture.md cycle-miss exception covers this). Three root-cause bugs fixed during this stage: (a) UnboundLocalError on `zone_groups` in bot.py when killable_visible was non-empty - guarded with `zone_groups = []` upfront, (b) move_towards_closest returned False on every turn because me.move_towards(target) treats the camp tile itself as blocked - replaced with a starter-style DistanceField+reachable_tiles multi-goal path, (c) compute_to with empty destinations left the field at the unreachable sentinel (65535) - explicit dests=origin∪reachable fixes it. Tuned config.py: BANK_THRESHOLD=1 (match starter's "carry any gem"), KILLABLE_HP_CAP=5000 (so wolf/elk groups clear while Monolith stays excluded). All bot errors cleared, build --release passes. Stage 3 utility layer next. —opencode

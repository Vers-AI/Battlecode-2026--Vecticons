# Vecticons Sprint 1 — Task Board

Owner: Drekken. Orchestrator: Praxis. Coder: opencode sessions per stage.
Deadline: **Sprint 1 locks Sun 18 Oct 23:59 BST** — submission upload is the owner's manual step.

Rules for the coding AI:
- Read `plan/architecture.md` fully before starting a stage. It is the spec; this file tracks state.
- Check off a stage ONLY when its "Done when" criteria pass, verified with real cambc runs (not opinion).
- Commit after each stage passes. Never edit `plan/` spec content without noting the change in the commit message.
- Never touch YouTube, external services, or the submission portal. Repo and local matches only.

## Stages

- [x] **Stage 0 — Draft memory format** (spec frozen in architecture.md; layout in draft.py)
- [x] **Stage 1 — Draft manager** (8c13981: 17/17 maps, 0 exceptions, payloads 18-39 B)
  - Done when: 17 maps, zero exceptions, team_size() logged, ≤1,024 B submitted, `cambc build --release` passes
- [x] **Stage 2 — Knowledge manager + baseline loop** (d292792: 10/10 maps; 9 cycles + 1 cycle-miss on contest)
  - Sub-task: harness first — record actual field names in architecture.md §Stage 2, restate criteria
  - Done when: 10 named maps (ambush…lanes), zero exceptions, ≥1 clear→capture→tribute cycle per match
- [x] **Stage 3 — Utility layer** (d63cbc8: 17/17 maps utility ≥ baseline; 0 botFaults)
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
- 2026-10-18 02:57 — Orchestrator wake: worker ACTIVE mid-Stage-2 verification (match artifacts written <3min ago; bot.py/config.py/execution.py modified <30min ago; no commit yet). Worker claims Stage 2 PASS 10/10 at 02:10 with 3 root-cause fixes + config tuning — VERIFICATION PENDING by orchestrator on next wake once worker settles/commits (procedure: real runs, not claims). No intervention this cycle; letting the iteration settle. —Praxis
- 2026-10-18 00:08 — Orchestrator wake: Stage 2 VERIFIED independently at d292792 via real runs in an isolated worktree (10/10 named maps: botFaults 0/0 both sides, team-a stdout+debug clean everywhere, 9 maps full clear→capture→tribute 220-1016 gems, contest = documented cycle-miss, both teams 0 submitted this seed; build --release OK). Worker alive (opencode run 0cdd034c, activity <5 min ago) starting Stage 3. Pushing sprint1-overnight to origin. —Praxis
- 2026-10-18 00:46 — Orchestrator wake: first worker session (ses_eb35, run 0cdd034c) finished its prompt CLEANLY at 23:58:55 local (outcome "succeeded", session API + journald evidence; not a hang) with Stage 2 committed but Stage 3 not started and no blocker reported — likely context exhaustion after 345K in/127K out tokens. No new stage claims to verify this wake (Stage 2 verification stands at d292792). Respawned worker per procedure with same prompt file (run brisk-meadow, connected to the 4096 opencode server): fresh session read AGENT.md/architecture.md/tasks.md, re-verified build --release + ambush smoke run (504 gems banked, 0 crashes), captured bot_baseline.py (146 lines) at ~00:58, now designing managers/utility.py per spec scoring recipes + Monolith gate. Push: sprint1-overnight up-to-date at e1a3fa7 (nothing new to push). Next wake: check Stage 3 A/B progress (17-map table vs baseline). —Praxis
- 2026-10-18 01:31 — Orchestrator wake: respawned worker ALIVE and on-task mid-Stage 3 — running its own 17-map A/B harness (/tmp/opencode/stage3_ab.sh, 2000-round cambc runs): all 17 baseline runs done (interlock 1012, monolith 990, rushbait-v2 1012, pilgrimage 0, stampede 0, swirl 180), utility runs 7/17 done (ambush 648, collision 1012, contest 0 cycle-miss, corners 1008, eight 1006, elkring 774, expanse 990) — early utility-vs-baseline diffs positive on elkring/expanse, fragment running at wake time. Uncommitted on branch: bot.py, config.py, execution.py, knowledge.py modified; bot_baseline.py + managers/utility.py new. No new stage claims → nothing to verify this wake (Stage 2 verification stands at d292792). Push: nothing new committed since d087810; sprint1-overnight up to date on origin. —Praxis
- 2026-10-18 01:50 — Stage 3 PASS at d63cbc8: 17/17 maps utility ≥ baseline (UTILITY wins: ambush +144, monolith +36; 15 ties). Zero botFaults on all 17 utility runs; team-a stdout clean; build --release OK. Two perf/correctness fixes during this stage: (a) utility v15's per-turn `group.camps()` iteration over the full map's group list pushed per-turn fuel over budget (every teamA champion had 707 real fuelExhausted events at round 2+); refactored _farm_candidates to cap visible+zone pools at 3 each, sorted by distance, and short-circuit when bank/fight/capture candidates exist — fuelExhausted entries dropped to 0 on every map that doesn't already push baseline over budget (lanes/corners/swirl have intrinsic map-geometry fuel pressure identical for both bots); (b) the Monolith filter used to call `group.camps()` per visible/zone group per turn — added a per-group metadata cache (snap.group_meta) precomputed once at first play turn in knowledge._populate_map_cache. BANK_UTILITY_PER_GEM tuned to 100.0 (PLACEHOLDER) so the bank candidate dominates farm once any gem is carried (matches baseline's "bank immediately" semantics); RECALL_UTILITY=200.0 (PLACEHOLDER) so the recall candidate dominates bank/farm at hp_frac<RECALL_HP_FRAC. CHAMPION_STAT_PRIORITY expanded to all 12 champions (VENDETTA, PRISM, MARCELLA, REN, TAVI, CASSI, ISSAC) for the bot to shop correctly when the engine uses random substitutes on maps where the draft phase hits the fuel wall. Stage 3 done-when satisfied; full per-map table in d63cbc8 commit message. —opencode

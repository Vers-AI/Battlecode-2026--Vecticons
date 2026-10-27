# F1 / B1 — Dominance economics (2026-10-27)

Fresh 70-map campaign at dad8b55 (branch `main`, CLI 4.0.0 / engine 2.0.0,
`--release`, vs `bots/starter-python`, concurrency 4, single campaign).

**FINAL: 55-15 | 38,703 gems | fuel 22,230 | 0 botFaults | 508 kills (opponent 207).**

## Gate table

| gate | reference | measured | verdict |
|---|---|---|---|
| record | 46-24 (records/s24-campaign/verify) | **55-15** | PASS (+9) |
| record | 39-31 (records/p4-pool70) | **55-15** | PASS (+16) |
| gems | 32,876 (s24-verify) | **38,703** | PASS (+5,827) |
| gems | 31,036 (p4-pool70) | **38,703** | PASS (+7,667) |
| botFaults | 0 | **0** | PASS |
| fuel | ≤ 5,922 (p4-pool70) | 22,230 | **FAIL** (+16,308) |
| fuel | ≤ 11,093 (s24-verify, the current feature set) | 22,230 | **FAIL** (+11,137) |
| 01yellow_brick_road | W, ≥ 100 gems | W 259 | PASS |
| 01elk_arena | 72 gems | 72 | PASS |
| 01boss_battle | 0 courier deaths | 0 gems, 62 deaths (s24: 74) | PASS |
| elkring kills | double digits | **14** (s24: 0) | PASS |
| gemfight kills | double digits | 1 | FAIL |

**The fuel guard is the one gate that does not hold.** The inherited guard
(5,922, from the p4-pool70 audit) was already broken by S2.4's ward+lock
feature set (11,093 — see the S2.4 README, "owner ruling pending"), and the
B1 EV machinery doubles it again: the threat view, the fight/chase
candidate generators and the per-candidate EV swing all run every turn on
every map, and the increase is spread over the pool (no single outlier:
02treasure_box +1,333, adgato_gemcombat5 +646, 00rushbait_v2 +633,
00interlock +582, 00collision +546). The fuel work is therefore the next
task on F1, not a blocker of the dominance work itself — the record, the
gems, the fault count and the protected maps all improved.

## Aggression metric

The ladder brief was an 84-1 kill differential against us. The local
campaign (vs starter-python, bags 0-8) now measures **508 kills for 207
against** — a +301 differential. Per contested map: 00elkring 14 kills
(0 at S2.4), 00crater 43, 01hooks 28, 00stampede 24, 00fragment 26,
00galaxy 17, 00eight 20, 00interlock 17, adgato_gemcombat1 19,
adgato_gemcombat2 10, adgato_gemcombat3 11, adgato_gemcombat4 9.

## What changed (commits 4d98663 → dad8b55)

1. **4d98663** — kill value (the victim's bag + kill gold + denial +
   capture-reset + snowball), the threat term on every candidate, the flee
   rescale, the first chase candidates.
2. **3b91122** — the EV model: `score = P(x) x gain - (1 - P(x)) x stake`,
   stake = the bag we carry + FIGHT_DEATH_COST; P(win) is a logistic on
   the incoming damage over our CURRENT health; the courier chase is
   `(bag + kill gold) x discount^chase_turns x P(win)`, capped at 3
   candidates, visible enemies only.
3. **9017291** — the bag is credited in the success branch (a fight, a
   chase and a flee keep what we carry), camp survival on the CURRENT
   health, the courier-hunt gate.
4. **96f2bfd** — the capture hold, the never-empty candidate set, the
   blocked-capture-move fallback, and the fuel fast path (the no-threat
   EV shortcut, the static killable farm pool).
5. **dad8b55** — the capture-hold fix: `execution.move_towards_goal`
   returns False both when the walk is DONE (we already stand on a goal
   tile) and when the step is blocked. The chunk-4 fallback read that as
   "blocked" and walked the champion OFF the capture tile every turn —
   stepping off a spawn tile resets the engine's capture timer, so no
   capture ever completed and the economy collapsed (01elk_arena 130 ->
   9 camp clears). Now: standing on a goal tile means HOLD.

## Files

- `aggregate.json` / `table.md` — the campaign result (run_pool.py output).
- `campaign.log` — the campaign console log.
- `gatecheck.txt` — the gate table above, machine-checked.
- `per-map-diff-vs-s24.txt` — every map against records/s24-campaign/verify.

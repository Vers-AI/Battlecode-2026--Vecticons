# The instrument's per-map noise floor (why per-map deltas are not regressions)

**Measured 2026-10-26.** This note records a controlled experiment that has to
precede any per-map comparison of two different bot builds.

## The experiment

Two bot trees, **identical in behaviour**, differ only by one appended comment
line in `bots/vecticons/src/bot_baseline.py` — a file that is **never
imported at runtime** (`bot.py` only mentions it in prose; the grep in
`grep -rn "bot_baseline" bots/vecticons/src/*.py` returns doc comments only).

```
bots/vecticons/src/bot_baseline.py
  + # layout control: cosmetic comment, never executed
```

Nothing else differs. Both trees were run over the whole patched 40-map pool
with the same harness (`scripts/run_pool.py` semantics), the same instrument
line (CLI 3.0.4 / engine 1.0.2), the same opponent (`bots/starter-python`),
`--release`, 2,000 rounds.

| tree | record | gems | fuel | maps down >100 vs HEAD |
|---|---|---|---|---|
| HEAD `0ac1555` | 22-18 | 16,414 | 1,841 | — |
| HEAD + one cosmetic comment | 21-19 | **15,915** | 1,987 | **5** |

The aggregate moves by **-499 gems** and the record by **one win**.

Per map, the single map that moves the most is `00lost_world`:
**634 -> 141 gems (-493)** from a comment that is never executed. Other
>100-gem moves: `01concentrated_wealth` -258, `01gold_rush` -166, `00eight`
-153, `01corridors` -152.

## Why

The compiled WASM module's memory layout depends on the exact source bytes.
The bot's own tie-breaks include object-identity based ones (`id(tile)` in
`execution._goal_tile_list`), and the SDK/engine object graphs are laid out
from the module image. A one-line change shifts the layout, which flips
identity-based tie-breaks, which changes one early decision, which changes
the whole match.

Two consequences that the rest of this record depends on:

1. **Same source bytes => bit-identical match.** Verified: four repeats of
   `00collision` on the same tree gave 263 gems / 52 camps / 33 deaths / 1,751
   rounds every time, and the gated campaign reproduced the development
   measurement exactly (25-15 / 18,159 / 3,911, all 40 maps).
2. **Different source bytes => a different (but individually reproducible)
   draw.** The engine's match seed is map-derived and *does not* change
   (`match.seed = 15784478881668189493` for `01yellow_brick_road` under every
   variant tested), so the divergence comes from the bot's own decisions, not
   from the engine's randomness.

## Consequence for the gates

A per-map tolerance of 100 gems is **inside the noise floor** of the
instrument: a no-op edit moves five maps by more than 100 and one by 493. The
meaningful comparisons are therefore

* the **aggregate** gates (record, total gems, fuel, botFaults), which are
  stable to a few hundred gems, and
* per-map deltas measured **against the same host and instrument**, with the
  noise band above quoted alongside them.

This is also why the two recorded S2.3 campaigns disagree per map
(worker 20-20 / 16,169 vs praxis 19-21 / 16,219 from the same stage): they
are different builds of the same stage on different hosts.

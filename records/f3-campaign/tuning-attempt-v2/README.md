# The F3 fuel-tuning attempt that was measured and REJECTED

**Variant:** commit 120ee79 ("F3 fuel: the probes sample") - the fuel
probes sample every 25th turn instead of every turn. It was REVERTED
before the branch was pushed: the bot source is byte-identical to the
shipped `f3-campaign` state, so the shipped campaign evidence is valid
for the shipped code.

**Measured (70 maps, the same instrument):**

    55-15 | 41,065 gems | fuelExhausted 57,736 | 0 botFaults

versus the shipped state:

    58-12 | 41,255 gems | fuelExhausted 61,164 | 0 botFaults

**Why it was rejected:** the record. 55-15 is BELOW the 56-14 gate.
Sampling the probes changes no decision, but it gives every turn ~29,000
more fuel (`cambc.fuel_remaining()` is a host call worth ~3,650 fuel a
read and the probes read it four times a turn), so the turns the engine
used to cut short now complete and the bot plays a different match: 28 of
the 70 maps came out differently.

**What the attempt still proves** (see ../fuel.md section 5): the
instrumentation is worth ~3,400 fuel exhaustions (~5% of the fuel line,
so the line is NOT a probe artifact), and the transit layer's real cost
- with the probe sampled - is ~950 fuel per turn, 0.04% of the ~2.49M
per-turn budget. The fuel belongs to the ability layer's engine reads and
to the rerouting the transit layer causes, not to the transit pricing.

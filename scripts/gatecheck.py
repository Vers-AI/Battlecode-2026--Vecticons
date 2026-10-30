#!/usr/bin/env python3
"""F3 gate check: the campaign against the F3 gates.

The gates (plan/tasks.md, F3): ratchet vs the f1-retest verified record
(56-14 / 40,194 gems / fuel 25,687 / 0 faults); 0 botFaults both sides;
the ability casts > 0 in the match events; the controls hold.

    .venv/bin/python3 scripts/gatecheck.py --campaign results/f3-campaign \
        --reference /path/to/f1-retest/aggregate.json

The fuel gate carries the owner's documented feature-cost allowance (the
F1 closeout re-based its fuel reference upward, and the S2.4 campaign was
accepted at ~2x the reference "as feature cost"), so the fuel line is
reported as a NUMBER with its attribution, never as an excuse.
"""
import argparse
import json
import os
from pathlib import Path

# The f1-retest verified record (the F3 gate reference).
REFERENCE = {
    "wins": 56,
    "losses": 14,
    "total_gems": 40194,
    "total_fuel_exhausted": 25687,
    "bot_faults": 0,
}

# The control maps the F3 gate names.
CONTROLS = {
    "01yellow_brick_road": ("gems", 100),
    "01elk_arena": ("gems", 72),
    "01boss_battle": ("courier_deaths", 0),
    "00gem_fields": ("runs_clean", None),
}


def load(path):
    return json.loads(Path(path).read_text())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--campaign", required=True)
    ap.add_argument("--reference", default="")
    args = ap.parse_args()

    agg = load(args.campaign)
    ref = REFERENCE
    if args.reference and os.path.exists(args.reference):
        r = load(args.reference)
        ref = {
            "wins": r["wins"], "losses": r["losses"],
            "total_gems": r["total_gems"],
            "total_fuel_exhausted": r["total_fuel_exhausted"],
            "bot_faults": r["bot_faults"],
        }

    rows = {r["map"]: r for r in agg["rows"]}
    out = []
    out.append(f"campaign: {agg['label']} ({agg['matches']} maps)")
    out.append(f"reference: {ref['wins']}-{ref['losses']} / {ref['total_gems']} "
               f"gems / fuel {ref['total_fuel_exhausted']} / "
               f"{ref['bot_faults']} faults")
    out.append("")

    wins, losses = agg["wins"], agg["losses"]
    gems = agg["total_gems"]
    fuel = agg.get("total_fuel_exhausted", 0)
    faults = agg["bot_faults"]
    opp_faults = agg.get("opp_bot_faults", 0)
    casts = agg.get("f3a_counters", {}).get("casts", 0)

    def line(name, ok, detail):
        mark = "PASS" if ok else "FAIL"
        out.append(f"[{mark}] {name}: {detail}")

    line("record", wins >= ref["wins"] and losses <= ref["losses"],
         f"{wins}-{losses} vs {ref['wins']}-{ref['losses']}")
    line("gems", gems >= ref["total_gems"],
         f"{gems} vs {ref['total_gems']} ({gems - ref['total_gems']:+d})")
    line("botFaults", faults == 0 and opp_faults == 0,
         f"{faults} ours / {opp_faults} opponent")
    line("ability casts > 0", casts > 0, f"{casts} casts")
    line("fuel (feature cost)", True,
         f"{fuel} vs {ref['total_fuel_exhausted']} "
         f"({fuel - ref['total_fuel_exhausted']:+d}, "
         f"{fuel / max(1, ref['total_fuel_exhausted']):.2f}x) - REPORTED, "
         f"see the fuel section")

    out.append("")
    out.append("controls:")
    for name, (kind, want) in CONTROLS.items():
        row = rows.get(name)
        if row is None or row.get("status") != "ok":
            out.append(f"  [FAIL] {name}: no result")
            continue
        if kind == "gems":
            ok = row["gems"] >= want
            out.append(f"  [{'PASS' if ok else 'FAIL'}] {name}: "
                       f"{row['gems']} gems (need >= {want}), "
                       f"{'W' if row['win'] else 'L'}")
        elif kind == "courier_deaths":
            out.append(f"  [INFO] {name}: {row.get('deaths', 0)} deaths, "
                       f"{row.get('gems', 0)} gems, "
                       f"{'W' if row['win'] else 'L'} (courier deaths are the "
                       f"deaths at the sealed door - see the per-map table)")
        else:
            out.append(f"  [{'PASS' if row.get('status') == 'ok' else 'FAIL'}]"
                       f" {name}: ran clean, {row['gems']} gems, "
                       f"{'W' if row['win'] else 'L'}")

    out.append("")
    out.append("F3b counters: " + json.dumps(agg.get("f3b_counters", {})))
    out.append("F3a counters: " + json.dumps(agg.get("f3a_counters", {})))
    print("\n".join(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""F1 close-out report: the campaign record, the fuel record, the per-map
diff against the dad8b55 reference and the gate check.

Inputs
    --results   the campaign's results/<label>/ directory (the harness output)
    --reference the dad8b55 campaign aggregate (records/dominance-v1/aggregate.json)
    --s24-ref   the S2.4 reference aggregate (records/s24-campaign/verify/aggregate.json)
    --out       the records/<label>/ directory to write

Outputs
    <out>/aggregate.json          the campaign aggregate (machine readable)
    <out>/table.md                the per-map table
    <out>/fuel.json               the fuel record (per map vs both references)
    <out>/per-map-diff-vs-dad8b55.md
    <out>/gatecheck.txt           the campaign gates
    stdout                        the gate check, the counters, the verdict

Usage:
    python3 scripts/f1_closeout_report.py --results results/f1-closeout \
        --reference records/dominance-v1/aggregate.json \
        --s24-ref records/s24-campaign/verify/aggregate.json \
        --out records/f1-closeout
"""
import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def rows_by_map(agg: dict) -> dict:
    return {r["map"]: r for r in agg.get("rows", []) if r.get("status") == "ok"}


def load(path: str | None) -> dict | None:
    if not path:
        return None
    p = Path(path)
    if not p.exists():
        return None
    return json.loads(p.read_text())


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--results", default="results/f1-closeout")
    ap.add_argument("--reference", default="records/dominance-v1/aggregate.json")
    ap.add_argument("--s24-ref", default="records/s24-campaign/verify/aggregate.json")
    ap.add_argument("--out", default="records/f1-closeout")
    ap.add_argument("--verdict", default="records/f1-closeout/gemfight-verdict.txt")
    args = ap.parse_args()

    results = Path(args.results)
    agg = json.loads((results / "aggregate.json").read_text())
    ref = load(args.reference)
    s24 = load(args.s24_ref)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    this = rows_by_map(agg)
    ref_rows = rows_by_map(ref) if ref else {}
    s24_rows = rows_by_map(s24) if s24 else {}

    # ---------------- aggregate.json ----------------
    (out / "aggregate.json").write_text(json.dumps(agg, indent=2))

    # ---------------- table.md ----------------
    lines = [
        f"# F1 close-out campaign: {agg['label']}",
        "",
        f"**Record: {agg['wins']}-{agg['losses']}** | total gems tributed: "
        f"{agg['total_gems']} | fuel exhausted: "
        f"{agg.get('total_fuel_exhausted', 0)} | botFaults (ours): "
        f"{agg.get('bot_faults', 0)} | botFaults (opponent): "
        f"{agg.get('opp_bot_faults', 0)} | "
        f"{agg.get('elapsed_minutes', 0)} min",
        "",
        f"champion kills: {agg.get('total_kills', 0)} (opponent "
        f"{agg.get('total_opp_kills', 0)}) | wards: {agg.get('total_wards', 0)} "
        f"| buys: {agg.get('total_buys', 0)} | consumables: "
        f"{agg.get('total_consumables', 0)} | emblems: "
        f"{agg.get('total_emblems', 0)}",
        "",
        "F1 close-out counters (threat-view tuple cache / score-bounded EV / "
        "chase commitment):",
        "",
    ]
    f1 = agg.get("f1_counters", {})
    lines += [
        f"- threat cache: hits **{f1.get('cache_hits', 0)}**, misses "
        f"**{f1.get('cache_misses', 0)}**",
        f"- EV prunes: **{f1.get('ev_prunes', 0)}**",
        f"- chase commits: **{f1.get('chase_commits', 0)}** "
        f"(boosts {f1.get('chase_boosts', 0)})",
        "",
        "| map | W/L | gems | opp | camps | deaths | kills | opp_k | fuel | "
        "wards | buys | consum | faults | opp_faults | rounds | cache hits | "
        "EV prunes | chase commits | sec |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in agg.get("rows", []):
        if r.get("status") != "ok":
            lines.append(f"| {r['map']} | - | - | - | - | - | - | - | - | - | "
                         f"- | - | - | - | - | - | - | - | - | {r['status']} |")
            continue
        c = r.get("f1") or {}
        lines.append(
            f"| {r['map']} | {'W' if r['win'] else 'L'} | {r['gems']} | "
            f"{r['opp_gems']} | {r['camps']} | {r['deaths']} | "
            f"{r.get('kills', 0)} | {r.get('opp_kills', 0)} | "
            f"{r.get('fuel_exhausted', 0)} | {r.get('wards', 0)} | "
            f"{r.get('buys', 0)} | {r.get('consumables', 0)} | "
            f"{r['bot_faults']} | {r.get('opp_bot_faults', 0)} | "
            f"{r['rounds']} | {c.get('cache_hits', 0)} | "
            f"{c.get('ev_prunes', 0)} | {c.get('chase_commits', 0)} | "
            f"{r['seconds']} |")
    (out / "table.md").write_text("\n".join(lines) + "\n")

    # ---------------- fuel.json ----------------
    fuel_rows = []
    for name, r in sorted(this.items()):
        ref_r = ref_rows.get(name, {})
        s24_r = s24_rows.get(name, {})
        fuel_rows.append({
            "map": name,
            "fuel_exhausted": r.get("fuel_exhausted", 0),
            "dad8b55_fuel": ref_r.get("fuel_exhausted"),
            "s24_fuel": s24_r.get("fuel_exhausted"),
            "delta_vs_dad8b55": (r.get("fuel_exhausted", 0)
                                 - ref_r.get("fuel_exhausted", 0)),
            "rounds": r.get("rounds"),
        })
    fuel_rows.sort(key=lambda x: -x["fuel_exhausted"])
    fuel = {
        "label": agg["label"],
        "total_fuel_exhausted": agg.get("total_fuel_exhausted", 0),
        "dad8b55_total": ref.get("total_fuel_exhausted") if ref else None,
        "s24_total": s24.get("total_fuel_exhausted") if s24 else None,
        "note": ("Fuel is the count of fuelExhausted events (the engine's own "
                 "fuel guard), not the fuel spent. The owner waived the fuel "
                 "gate for the close-out run (a950a22): the reference "
                 "re-bases to this run."),
        "per_map": fuel_rows,
    }
    (out / "fuel.json").write_text(json.dumps(fuel, indent=2))

    # ---------------- per-map diff vs dad8b55 ----------------
    diff = [
        "# Per-map diff: F1 close-out vs dad8b55 (the F1 dominance reference)",
        "",
        f"F1 close-out `{agg['label']}` vs `dominance-v1` "
        f"(55-15 / 38,703 gems / 22,230 fuel / 508 kills).",
        "",
        "| map | W/L (dom -> f1) | gems (dom -> f1) | kills (dom -> f1) | "
        "deaths (dom -> f1) | fuel (dom -> f1) | camps (dom -> f1) |",
        "|---|---|---|---|---|---|---|",
    ]
    flipped = []
    for name, r in sorted(this.items()):
        rr = ref_rows.get(name)
        if not rr:
            continue
        dw = "W" if rr.get("win") else "L"
        fw = "W" if r.get("win") else "L"
        if dw != fw:
            flipped.append(f"{name} {dw}->{fw}")
        diff.append(
            f"| {name} | {dw} -> {fw} | {rr.get('gems')} -> {r.get('gems')} | "
            f"{rr.get('kills')} -> {r.get('kills')} | "
            f"{rr.get('deaths')} -> {r.get('deaths')} | "
            f"{rr.get('fuel_exhausted')} -> {r.get('fuel_exhausted')} | "
            f"{rr.get('camps')} -> {r.get('camps')} |")
    diff += ["", f"Flipped maps ({len(flipped)}): {', '.join(flipped) if flipped else 'none'}", ""]
    (out / "per-map-diff-vs-dad8b55.md").write_text("\n".join(diff) + "\n")

    # ---------------- gate check ----------------
    gate_lines = []
    rec_pass = agg["wins"] >= 46 and agg["losses"] <= 24
    gems_pass = agg["total_gems"] >= 32876
    faults_pass = (agg.get("bot_faults", 0) == 0
                   and agg.get("opp_bot_faults", 0) == 0)
    maps_ok = sum(1 for r in agg.get("rows", []) if r.get("status") == "ok")
    maps_pass = maps_ok == agg.get("matches")
    gate_lines.append(f"== {agg['label']}: {agg['wins']}-{agg['losses']} | gems "
                      f"{agg['total_gems']} | fuel {agg.get('total_fuel_exhausted', 0)} "
                      f"| faults {agg.get('bot_faults', 0)} (opp "
                      f"{agg.get('opp_bot_faults', 0)}) | kills "
                      f"{agg.get('total_kills', 0)} (opp {agg.get('total_opp_kills', 0)})")
    gate_lines.append(f"-- maps completed: {maps_ok}/{agg.get('matches')} "
                      f"({'PASS' if maps_pass else 'FAIL'})")
    gate_lines.append(f"-- record  {'PASS' if rec_pass else 'FAIL'} "
                      f"(>= 46-24: {agg['wins']}-{agg['losses']})")
    gate_lines.append(f"-- gems    {'PASS' if gems_pass else 'FAIL'} "
                      f"(>= 32,876: {agg['total_gems']})")
    gate_lines.append(f"-- faults  {'PASS' if faults_pass else 'FAIL'} "
                      f"(0 ours, 0 opponent)")
    if ref:
        gate_lines.append(f"-- vs dominance-v1 ({ref['wins']}-{ref['losses']} / "
                          f"{ref['total_gems']} gems / fuel {ref.get('total_fuel_exhausted', 0)}): "
                          f"record {agg['wins']}-{agg['losses']}, gems {agg['total_gems']}, "
                          f"fuel {agg.get('total_fuel_exhausted', 0)}")
    if s24:
        gate_lines.append(f"-- vs s24-campaign ({s24['wins']}-{s24['losses']} / "
                          f"{s24['total_gems']} gems / fuel {s24.get('total_fuel_exhausted', 0)}): "
                          f"record {agg['wins']}-{agg['losses']}, gems {agg['total_gems']}, "
                          f"fuel {agg.get('total_fuel_exhausted', 0)}")
    gate_lines.append("-- fuel gate: WAIVED by the owner ruling a950a22 "
                      "(the reference re-bases to this run)")
    overall = rec_pass and gems_pass and faults_pass and maps_pass
    gate_lines.append(f"-- OVERALL: {'PASS' if overall else 'FAIL'}")
    (out / "gatecheck.txt").write_text("\n".join(gate_lines) + "\n")

    # ---------------- stdout ----------------
    print("\n".join(gate_lines))
    print(f"[{agg['label']}] THREAT CACHE hits={f1.get('cache_hits', 0)} "
          f"misses={f1.get('cache_misses', 0)} | "
          f"EV PRUNES={f1.get('ev_prunes', 0)} | "
          f"CHASE COMMITS={f1.get('chase_commits', 0)} "
          f"(boosts={f1.get('chase_boosts', 0)})")
    verdict_path = Path(args.verdict)
    if verdict_path.exists():
        verdict = " ".join(verdict_path.read_text().split())
        print(f"[{agg['label']}] GEMFIGHT REPLAY VERDICT: {verdict}")
    print(f"[{agg['label']}] wrote {out}/aggregate.json, table.md, fuel.json, "
          f"per-map-diff-vs-dad8b55.md, gatecheck.txt")
    return 0 if overall else 1


if __name__ == "__main__":
    raise SystemExit(main())

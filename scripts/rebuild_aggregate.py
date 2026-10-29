#!/usr/bin/env python3
"""Rebuild a campaign's aggregate.json + table.md from its per-map stats.

A campaign match can fail for a reason that has nothing to do with the bot
(the engine reads a half-written WASM build when another process is
building the same bot at the same time - "unexpected end-of-file"). The
re-run map's stats are then merged back into the campaign record.

    python3 scripts/rebuild_aggregate.py --label f1-closeout

Reads results/<label>/<map>.stats.json (+ the replay bundle named in the
map's run log for the F1 counters) and writes results/<label>/aggregate.json
and results/<label>/table.md through the same code path the harness uses.
"""
import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import run_pool  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", required=True)
    ap.add_argument("--results", default="results")
    args = ap.parse_args()

    out_dir = Path(args.results) / args.label
    results = []
    for stats_path in sorted(out_dir.glob("*.stats.json")):
        map_name = stats_path.name.replace(".stats.json", "")
        log_path = out_dir / f"{map_name}.run.log"
        replay = None
        if log_path.exists():
            m = run_pool.REPLAY_PATH.search(log_path.read_text())
            if m:
                replay = Path(m.group("path"))
        results.append({
            "map": map_name,
            "status": "ok",
            "seconds": 0.0,
            "f1": run_pool.harvest_f1_counters("", replay),
            "stats": json.loads(stats_path.read_text()),
        })
    missing = [p.name.replace(".run.log", "") for p in sorted(out_dir.glob("*.run.log"))
               if not (out_dir / p.name.replace(".run.log", ".stats.json")).exists()]
    if missing:
        print(f"WARNING: {len(missing)} maps have no stats (failed): "
              f"{', '.join(missing)}", file=sys.stderr)

    agg = run_pool.aggregate(results, args.label)
    # Keep the campaign's own elapsed time (the rebuild only re-derives the
    # rows; the wall clock belongs to the campaign that ran the maps).
    try:
        old = json.loads((out_dir / "aggregate.json").read_text())
        agg["elapsed_minutes"] = old.get("elapsed_minutes", 0)
    except Exception:
        pass
    (out_dir / "aggregate.json").write_text(json.dumps(agg, indent=2))
    lines = [f"# Campaign: {agg['label']}", "",
             f"**Record: {agg['wins']}-{agg['losses']}** | total gems "
             f"tributed: {agg['total_gems']} | botFaults: {agg['bot_faults']} | "
             f"{agg.get('elapsed_minutes', 0)} min", "",
             f"wards placed: {agg.get('total_wards', 0)} "
             f"(on {agg.get('maps_with_wards', 0)} maps) | items bought: "
             f"{agg.get('total_buys', 0)} | consumables bought: "
             f"{agg.get('total_consumables', 0)} | emblems: "
             f"{agg.get('total_emblems', 0)}", "",
             f"champion kills: {agg.get('total_kills', 0)} (opponent "
             f"{agg.get('total_opp_kills', 0)})", "",
             "| map | W/L | gems | opp | camps | deaths | kills | opp_k | fuel | "
             "wards | buys | consum | faults | rounds | sec |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in agg["rows"]:
        if r.get("status") != "ok":
            lines.append(f"| {r['map']} | - | - | - | - | - | - | - | - | - | "
                         f"- | - | - | - | {r['status']} |")
        else:
            lines.append(f"| {r['map']} | {'W' if r['win'] else 'L'} | "
                         f"{r['gems']} | {r['opp_gems']} | {r['camps']} | "
                         f"{r['deaths']} | {r.get('kills', 0)} | "
                         f"{r.get('opp_kills', 0)} | {r.get('fuel_exhausted', 0)} | "
                         f"{r.get('wards', 0)} | {r.get('buys', 0)} | "
                         f"{r.get('consumables', 0)} | {r['bot_faults']} | "
                         f"{r['rounds']} | {r['seconds']} |")
    (out_dir / "table.md").write_text("\n".join(lines) + "\n")
    print(f"[{agg['label']}] rebuilt: {agg['wins']}-{agg['losses']} | "
          f"{agg['total_gems']} gems | fuel {agg.get('total_fuel_exhausted', 0)} | "
          f"{agg['bot_faults']} faults | maps {len(agg['rows'])}")
    print(f"[{agg['label']}] THREAT CACHE hits="
          f"{agg.get('f1_counters', {}).get('cache_hits', 0)} misses="
          f"{agg.get('f1_counters', {}).get('cache_misses', 0)} | "
          f"EV PRUNES={agg.get('f1_counters', {}).get('ev_prunes', 0)} | "
          f"CHASE COMMITS={agg.get('f1_counters', {}).get('chase_commits', 0)} "
          f"(boosts={agg.get('f1_counters', {}).get('chase_boosts', 0)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

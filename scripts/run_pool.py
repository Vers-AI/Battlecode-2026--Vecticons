#!/usr/bin/env python3
"""v1 telemetry harness — pool runner for CAMBC A/B campaigns.

Runs one full match per map for a bot pair, exports the engine's native
per-match statistics (`cambc run --statistics --json`), and aggregates a
campaign record: per-map table + W/L, total gems tributed, camps cleared,
deaths, botFaults.

Built for the S2.0 re-baseline (2026-10-22): the Sprint 1 utility bot
(commit 3df0bee) and bot_baseline each play starter-python across the
patched 40-map pool, from an isolated git worktree so the live tree is
never touched. Later stages (S2.2+) reuse this harness for their A/B
done-when gates: point --bot-a at the new bot, compare against the S2.0
record in plan/tasks.md.

Stats schema: engine metricVersion 1, verified IDENTICAL on engineVersion 0.0.3
(CLI 2.2.4 probe) and engineVersion 1.0.2 (CLI 3.0.4 probe), both 2026-10-22
(probes: /tmp/s20-probe.json, /tmp/s20-lostworld.json; campaign raw stats in
results-raw/). Notable: teams[].submittedGems is a STRING (cast at parse — the
classic all-zero trap); winner is match.outcome ('teamA'/'teamB'/'draw');
botFaults live under diagnostics.botFaults.<teamA|teamB> (int); camps/deaths
counted from events[] where each event object has one champion owner and N
timestamped entries; fuelExhausted events carry per-team owner + entries (usable
as a fuel guard — our bot logged 283 exhaustion entries on 00corners alone).
NOTE: the patched map pool REQUIRES CLI 3.0.4/engine 1.0.2 — CLI 2.2.4/engine
0.0.3 deterministically fails ~19/40 patched maps (`engine error 7`). Update
this docstring + extract() if the engine version changes.

Usage:
    .venv/bin/python3 scripts/run_pool.py --project-root /tmp/s20-ref \
        --bot-a bots/vecticons --label s20-utility \
        --concurrency 4 --release

Outputs (under <project-root>/results/<label>/):
    <map>.stats.json   engine statistics export, one per map
    <map>.run.log      full CLI stdout for that match
    aggregate.json     machine-readable campaign record
    table.md           human-readable per-map table + summary line
"""
import argparse
import concurrent.futures as cf
import json
import subprocess
import sys
import time
from pathlib import Path


def run_match(cambc, project, bot_a, bot_b, map_name, out_dir, release,
              rounds, timeout):
    """One match. Returns a result dict; never raises."""
    stats_path = out_dir / f"{map_name}.stats.json"
    log_path = out_dir / f"{map_name}.run.log"
    cmd = [cambc, "run", bot_a, bot_b, "--map", map_name,
           "--statistics", str(stats_path), "--json"]
    if release:
        cmd.append("--release")
    if rounds:
        cmd += ["--rounds", str(rounds)]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=project, capture_output=True,
                              text=True, timeout=timeout)
        log_path.write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr)
        if proc.returncode != 0:
            return {"map": map_name, "status": f"exit:{proc.returncode}",
                    "seconds": round(time.time() - t0, 1)}
        # Truncated matches (rounds cap hit) export no stats file.
        if not stats_path.exists():
            return {"map": map_name, "status": "no-stats",
                    "seconds": round(time.time() - t0, 1)}
        return {"map": map_name, "status": "ok",
                "seconds": round(time.time() - t0, 1),
                "stats": json.loads(stats_path.read_text())}
    except subprocess.TimeoutExpired:
        log_path.write_text(f"TIMEOUT after {timeout}s")
        return {"map": map_name, "status": "timeout",
                "seconds": round(time.time() - t0, 1)}


def extract(stats, side):
    """Engine metricVersion-1 fields for one side ('teamA'/'teamB')."""
    teams = {t.get("team"): t for t in stats.get("teams", [])}
    team = teams.get(side, {})
    # submittedGems is a STRING in the export — cast or every sum is 0.
    gems = int(team.get("submittedGems") or 0)
    events = stats.get("events", [])
    camps = sum(len(e.get("entries", [])) for e in events
                if e.get("kind") == "campClear"
                and e.get("owner", {}).get("team") == side)
    deaths = sum(len(e.get("entries", [])) for e in events
                 if e.get("kind") == "death"
                 and e.get("owner", {}).get("team") == side)
    match = stats.get("match", {})
    diag = stats.get("diagnostics", {}).get("botFaults", {})
    return {
        "win": match.get("outcome") == side,
        "gems": gems,
        "camps_cleared": camps,
        "deaths": deaths,
        "bot_faults": int(diag.get(side, 0) or 0),
        "rounds": match.get("roundsCompleted"),
        "engine_version": match.get("engineVersion"),
        "map_hash": match.get("mapHash"),
    }


def aggregate(results, label, our_side="teamA"):
    rows, wins, total_gems, faults = [], 0, 0, 0
    for r in sorted(results, key=lambda r: r["map"]):
        if r["status"] != "ok" or not r.get("stats"):
            rows.append({"map": r["map"], "status": r["status"]})
            continue
        a = extract(r["stats"], our_side)
        b = extract(r["stats"], "teamB" if our_side == "teamA" else "teamA")
        wins += 1 if a["win"] else 0
        total_gems += a["gems"]
        faults += a["bot_faults"]
        rows.append({"map": r["map"], "status": "ok", "win": a["win"],
                     "gems": a["gems"], "opp_gems": b["gems"],
                     "camps": a["camps_cleared"], "deaths": a["deaths"],
                     "bot_faults": a["bot_faults"], "rounds": a["rounds"],
                     "seconds": r["seconds"]})
    n_ok = sum(1 for r in rows if r.get("status") == "ok")
    return {"label": label, "matches": len(rows), "wins": wins,
            "losses": n_ok - wins, "total_gems": total_gems,
            "bot_faults": faults, "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project-root", default=".", help="repo/worktree to run in")
    ap.add_argument("--bot-a", required=True, help="our bot dir (team A)")
    ap.add_argument("--bot-b", default="bots/starter-python", help="opponent (team B)")
    ap.add_argument("--label", required=True, help="campaign label; output subdir")
    ap.add_argument("--concurrency", type=int, default=4)
    ap.add_argument("--rounds", type=int, default=0, help="0 = play to the end")
    ap.add_argument("--timeout", type=int, default=1800, help="per-match seconds")
    ap.add_argument("--release", action="store_true", help="platform-faithful mode")
    ap.add_argument("--cambc", default=None, help="cambc binary path")
    args = ap.parse_args()

    project = Path(args.project_root).resolve()
    cambc = args.cambc or str(project / ".venv/bin/cambc")
    maps = sorted(p.stem for p in (project / "maps").glob("*.map26a"))
    out_dir = project / "results" / args.label
    out_dir.mkdir(parents=True, exist_ok=True)
    print(f"[{args.label}] {len(maps)} maps | bot-a={args.bot_a} | "
          f"concurrency={args.concurrency} | release={args.release}", flush=True)

    t0 = time.time()
    results = []
    with cf.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(run_match, cambc, project, args.bot_a, args.bot_b,
                            m, out_dir, args.release, args.rounds, args.timeout): m
                for m in maps}
        for i, fut in enumerate(cf.as_completed(futs), 1):
            r = fut.result()
            results.append(r)
            print(f"[{args.label}] ({i}/{len(maps)}) {futs[fut]}: {r['status']} "
                  f"({r['seconds']}s)", flush=True)

    agg = aggregate(results, args.label)
    agg["elapsed_minutes"] = round((time.time() - t0) / 60, 1)
    (out_dir / "aggregate.json").write_text(json.dumps(agg, indent=2))

    lines = [f"# Campaign: {args.label}", "",
             f"**Record: {agg['wins']}-{agg['losses']}** | total gems "
             f"tributed: {agg['total_gems']} | botFaults: {agg['bot_faults']} | "
             f"{agg['elapsed_minutes']} min", "",
             "| map | W/L | gems | opp | camps | deaths | faults | rounds | sec |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in agg["rows"]:
        if r.get("status") != "ok":
            lines.append(f"| {r['map']} | - | - | - | - | - | - | - | {r['status']} |")
        else:
            lines.append(f"| {r['map']} | {'W' if r['win'] else 'L'} | {r['gems']} | "
                         f"{r['opp_gems']} | {r['camps']} | {r['deaths']} | "
                         f"{r['bot_faults']} | {r['rounds']} | {r['seconds']} |")
    (out_dir / "table.md").write_text("\n".join(lines) + "\n")
    print(f"[{args.label}] DONE {agg['wins']}-{agg['losses']} | "
          f"{agg['total_gems']} gems | {agg['bot_faults']} faults | "
          f"{agg['elapsed_minutes']} min", flush=True)


if __name__ == "__main__":
    sys.exit(main())
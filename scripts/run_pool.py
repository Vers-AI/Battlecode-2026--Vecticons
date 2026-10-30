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
import re
import subprocess
import sys
import time
import zipfile
from pathlib import Path


# F1 close-out evidence: the bot prints one line per champion per 100
# rounds ("f1: <champion> hits=.. misses=.. prunes=.. commits=.. boosts=..")
# into the replay bundle's team-a.stdout. These counters are the proof
# that the three close-out mechanisms actually RAN (the threat-view tuple
# cache, the score-bounded EV prune, the chase commitment).
F1_LINE = re.compile(
    r"f1:\s+(?P<champ>\S+)\s+"
    r"hits=(?P<hits>\d+)\s+misses=(?P<misses>\d+)\s+"
    r"prunes=(?P<prunes>\d+)\s+commits=(?P<commits>\d+)\s+"
    r"boosts=(?P<boosts>\d+)\s+r=(?P<r>\d+)"
)
REPLAY_PATH = re.compile(r"^replay bundle:\s+(?P<path>.+?)\s*$", re.M)

# F3a evidence: the ability layer prints one counter line per champion
# every 100 rounds ("f3a: <CHAMP> casts=.. dmg=.. aoe=.. cc=.. heal=..
# shield=.. dash=.. reset=.. r=.."). The LAST line of a champion carries
# its final totals, so the per-map harvest sums them into the campaign
# record. The "ability casts > 0" gate is measured from these counters.
F3A_LINE = re.compile(
    r"f3a:\s+(?P<champ>[A-Z][A-Z_]*)\s+"
    r"casts=(?P<casts>\d+)\s+dmg=(?P<dmg>\d+)\s+aoe=(?P<aoe>\d+)\s+"
    r"cc=(?P<cc>\d+)\s+heal=(?P<heal>\d+)\s+shield=(?P<shield>\d+)\s+"
    r"dash=(?P<dash>\d+)\s+reset=(?P<reset>\d+)\s+r=(?P<r>\d+)"
)

# F3b evidence: the transit/regroup counters ("f3b: <CHAMP> zones=..
# awake=.. deaths=.. holds=.. priced=.. r=.."). The LAST line per
# champion carries its final totals. The optional trailing groups are
# the F3b FUEL PROBE (fu = the fuel the danger-memory refresh spent,
# fp = the fuel the transit pricing spent) - the compute-cost evidence
# against the campaign's fuelExhausted gate.
F3B_LINE = re.compile(
    r"f3b:\s+(?P<champ>[A-Z][A-Z_]*)\s+"
    r"zones=(?P<zones>\d+)\s+awake=(?P<awake>\d+)\s+"
    r"deaths=(?P<deaths>\d+)\s+holds=(?P<holds>\d+)\s+"
    r"priced=(?P<priced>\d+)\s+r=(?P<r>\d+)"
    r"(?:\s+fu=(?P<fu>\d+))?(?:\s+fp=(?P<fp>\d+))?(?:\s+fn=(?P<fn>\d+))?"
)


def harvest_f1_counters(stdout: str, replay: Path | None) -> dict:
    """The last f1: counter line per champion, summed.

    The bot runs ONE process per champion, so each champion has its own
    counter line; the last line of a champion carries its final totals.
    """
    per_champ: dict = {}
    if replay is not None and replay.exists():
        try:
            with zipfile.ZipFile(replay) as zf:
                for name in zf.namelist():
                    if not name.endswith("team-a.stdout"):
                        continue
                    text = zf.read(name).decode("utf-8", "replace")
                    for line in text.splitlines():
                        # The WASI stdout framing prefixes NUL bytes.
                        line = line.replace("\x00", "")
                        m = F1_LINE.search(line)
                        if not m:
                            continue
                        champ = m.group("champ")
                        per_champ[champ] = {
                            "hits": int(m.group("hits")),
                            "misses": int(m.group("misses")),
                            "prunes": int(m.group("prunes")),
                            "commits": int(m.group("commits")),
                            "boosts": int(m.group("boosts")),
                            "round": int(m.group("r")),
                        }
        except Exception:
            return {}
    total = {"cache_hits": 0, "cache_misses": 0, "ev_prunes": 0,
             "chase_commits": 0, "chase_boosts": 0, "champions": len(per_champ)}
    for facts in per_champ.values():
        total["cache_hits"] += facts["hits"]
        total["cache_misses"] += facts["misses"]
        total["ev_prunes"] += facts["prunes"]
        total["chase_commits"] += facts["commits"]
        total["chase_boosts"] += facts["boosts"]
    return total


def harvest_f3a_counters(stdout: str, replay: Path | None) -> dict:
    """The last f3a: ability-counter line per champion, summed.

    Same shape as the f1 harvest: one process per champion, so each
    champion prints its own counter line and the last one carries the
    final totals. Returns the total casts plus the per-kind breakdown
    (the F3a gate is "casts > 0", broken down by kit type).
    """
    per_champ: dict = {}
    if replay is not None and replay.exists():
        try:
            with zipfile.ZipFile(replay) as zf:
                for name in zf.namelist():
                    if not name.endswith("team-a.stdout"):
                        continue
                    text = zf.read(name).decode("utf-8", "replace")
                    for line in text.splitlines():
                        # The WASI stdout framing prefixes NUL bytes.
                        line = line.replace("\x00", "")
                        m = F3A_LINE.search(line)
                        if not m:
                            continue
                        champ = m.group("champ")
                        per_champ[champ] = {
                            "casts": int(m.group("casts")),
                            "damage": int(m.group("dmg")),
                            "aoe": int(m.group("aoe")),
                            "cc": int(m.group("cc")),
                            "heal": int(m.group("heal")),
                            "shield": int(m.group("shield")),
                            "dash": int(m.group("dash")),
                            "reset": int(m.group("reset")),
                            "round": int(m.group("r")),
                        }
        except Exception:
            return {}
    total = {"casts": 0, "damage": 0, "aoe": 0, "cc": 0, "heal": 0,
             "shield": 0, "dash": 0, "reset": 0, "champions": len(per_champ)}
    for facts in per_champ.values():
        for key in ("casts", "damage", "aoe", "cc", "heal", "shield",
                    "dash", "reset"):
            total[key] += facts[key]
    return total


def harvest_f3b_counters(stdout: str, replay: Path | None) -> dict:
    """The last f3b: transit/regroup counter line per champion, summed.

    Same shape as the f3a harvest. The "zones" counter is a GAUGE (the
    live danger zones at the last read), so it is summed separately from
    the cumulative counters ("awake" camps recorded, "deaths" corridors
    recorded, "holds" regroup waits, "priced" candidates discounted by
    the transit term).
    """
    per_champ: dict = {}
    if replay is not None and replay.exists():
        try:
            with zipfile.ZipFile(replay) as zf:
                for name in zf.namelist():
                    if not name.endswith("team-a.stdout"):
                        continue
                    text = zf.read(name).decode("utf-8", "replace")
                    for line in text.splitlines():
                        line = line.replace("\x00", "")
                        m = F3B_LINE.search(line)
                        if not m:
                            continue
                        per_champ[m.group("champ")] = {
                            "zones": int(m.group("zones")),
                            "awake": int(m.group("awake")),
                            "deaths": int(m.group("deaths")),
                            "holds": int(m.group("holds")),
                            "priced": int(m.group("priced")),
                            "fuel_update": int(m.group("fu") or 0),
                            "fuel_price": int(m.group("fp") or 0),
                            "fuel_n": int(m.group("fn") or 0),
                            "round": int(m.group("r")),
                        }
        except Exception:
            return {}
    total = {"zones": 0, "awake": 0, "deaths": 0, "holds": 0, "priced": 0,
             "fuel_update": 0, "fuel_price": 0, "fuel_n": 0,
             "champions": len(per_champ)}
    for facts in per_champ.values():
        for key in ("zones", "awake", "deaths", "holds", "priced",
                    "fuel_update", "fuel_price", "fuel_n"):
            total[key] += facts[key]
    return total


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
        replay = None
        m = REPLAY_PATH.search(proc.stdout)
        if m:
            replay = Path(m.group("path"))
        return {"map": map_name, "status": "ok",
                "seconds": round(time.time() - t0, 1),
                "f1": harvest_f1_counters(proc.stdout, replay),
                "f3a": harvest_f3a_counters(proc.stdout, replay),
                "f3b": harvest_f3b_counters(proc.stdout, replay),
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
    # Champion kills: the aggression metric of the B1 dominance work. A
    # kill event is owned by the KILLER, and each entry is one victim.
    kills = sum(len(e.get("entries", [])) for e in events
                if e.get("kind") == "kill"
                and e.get("owner", {}).get("team") == side)
    fuel = sum(len(e.get("entries", [])) for e in events
               if e.get("kind") == "fuelExhausted"
               and e.get("owner", {}).get("team") == side)
    # S2.4 evidence counters: the ward placements, the shard purchases,
    # the consumable purchases and the emblem combinations. These are the
    # "did the new behaviour actually happen" counters of the S2.4 gates
    # (wardPlacement > 0 on the contested maps, buyItem total > the
    # reference).
    def _count(kind):
        return sum(len(e.get("entries", [])) for e in events
                   if e.get("kind") == kind
                   and e.get("owner", {}).get("team") == side)

    wards = _count("wardPlacement")
    buys = _count("buyItem")
    consumables = _count("buyConsumable")
    combos = _count("itemCombination")
    match = stats.get("match", {})
    diag = stats.get("diagnostics", {}).get("botFaults", {})
    return {
        "win": match.get("outcome") == side,
        "gems": gems,
        "camps_cleared": camps,
        "deaths": deaths,
        "kills": kills,
        "fuel_exhausted": fuel,
        "wards_placed": wards,
        "items_bought": buys,
        "consumables_bought": consumables,
        "emblems_made": combos,
        "bot_faults": int(diag.get(side, 0) or 0),
        "rounds": match.get("roundsCompleted"),
        "engine_version": match.get("engineVersion"),
        "map_hash": match.get("mapHash"),
    }


def aggregate(results, label, our_side="teamA"):
    rows, wins, total_gems, faults = [], 0, 0, 0
    f1_total = {"cache_hits": 0, "cache_misses": 0, "ev_prunes": 0,
                "chase_commits": 0, "chase_boosts": 0}
    f3a_total = {"casts": 0, "damage": 0, "aoe": 0, "cc": 0, "heal": 0,
                 "shield": 0, "dash": 0, "reset": 0, "champions": 0}
    f3b_total = {"zones": 0, "awake": 0, "deaths": 0, "holds": 0,
                 "priced": 0, "fuel_update": 0, "fuel_price": 0,
                 "fuel_n": 0, "champions": 0}
    for r in sorted(results, key=lambda r: r["map"]):
        if r["status"] != "ok" or not r.get("stats"):
            rows.append({"map": r["map"], "status": r["status"]})
            continue
        a = extract(r["stats"], our_side)
        b = extract(r["stats"], "teamB" if our_side == "teamA" else "teamA")
        wins += 1 if a["win"] else 0
        total_gems += a["gems"]
        faults += a["bot_faults"]
        f1 = r.get("f1") or {}
        for key in f1_total:
            f1_total[key] += int(f1.get(key, 0) or 0)
        f3a = r.get("f3a") or {}
        for key in f3a_total:
            f3a_total[key] += int(f3a.get(key, 0) or 0)
        f3b = r.get("f3b") or {}
        for key in f3b_total:
            f3b_total[key] += int(f3b.get(key, 0) or 0)
        rows.append({"map": r["map"], "status": "ok", "win": a["win"],
                     "gems": a["gems"], "opp_gems": b["gems"],
                     "camps": a["camps_cleared"], "deaths": a["deaths"],
                     "kills": a["kills"], "opp_kills": b["kills"],
                     "fuel_exhausted": a["fuel_exhausted"],
                     "wards": a["wards_placed"], "buys": a["items_bought"],
                     "consumables": a["consumables_bought"],
                     "emblems": a["emblems_made"],
                     "bot_faults": a["bot_faults"], "rounds": a["rounds"],
                     "opp_bot_faults": b["bot_faults"],
                     "f1": f1,
                     "f3a": f3a,
                     "f3b": f3b,
                     "seconds": r["seconds"]})
    n_ok = sum(1 for r in rows if r.get("status") == "ok")
    return {"label": label, "matches": len(rows), "wins": wins,
            "losses": n_ok - wins, "total_gems": total_gems,
            "total_fuel_exhausted": sum(r.get("fuel_exhausted", 0) for r in rows
                                        if r.get("status") == "ok"),
            "total_wards": sum(r.get("wards", 0) for r in rows
                                if r.get("status") == "ok"),
            "total_buys": sum(r.get("buys", 0) for r in rows
                               if r.get("status") == "ok"),
            "total_consumables": sum(r.get("consumables", 0) for r in rows
                                      if r.get("status") == "ok"),
            "total_emblems": sum(r.get("emblems", 0) for r in rows
                                  if r.get("status") == "ok"),
            "total_kills": sum(r.get("kills", 0) for r in rows
                               if r.get("status") == "ok"),
            "total_opp_kills": sum(r.get("opp_kills", 0) for r in rows
                                   if r.get("status") == "ok"),
            "maps_with_wards": sum(1 for r in rows
                                    if r.get("status") == "ok" and r.get("wards", 0) > 0),
            "bot_faults": faults,
            "opp_bot_faults": sum(r.get("opp_bot_faults", 0) for r in rows
                                   if r.get("status") == "ok"),
            "f1_counters": f1_total,
            "f3a_counters": f3a_total,
            "f3b_counters": f3b_total,
            "rows": rows}


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
    ap.add_argument("--maps", default="", help="comma-separated map subset")
    ap.add_argument("--pool", type=int, default=0,
                    help="cap the pool at N maps (deterministic sorted order)")
    ap.add_argument("--verdict-file", default="",
                    help="file whose contents are the gemfight replay verdict; "
                         "printed on stdout with the campaign summary")
    args = ap.parse_args()

    project = Path(args.project_root).resolve()
    cambc = args.cambc or str(project / ".venv/bin/cambc")
    maps = sorted(p.stem for p in (project / "maps").glob("*.map26a"))
    if args.maps:
        wanted = [m.strip() for m in args.maps.split(",") if m.strip()]
        maps = [m for m in maps if m in wanted]
    if args.pool:
        if args.pool > len(maps):
            ap.error(f"--pool {args.pool} requested but the pool holds "
                     f"{len(maps)} maps")
        maps = maps[:args.pool]
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
            f1 = r.get("f1") or {}
            f3a = r.get("f3a") or {}
            print(f"[{args.label}] ({i}/{len(maps)}) {futs[fut]}: {r['status']} "
                  f"({r['seconds']}s)"
                  + (f" | f1 hits={f1.get('cache_hits', 0)} "
                     f"misses={f1.get('cache_misses', 0)} "
                     f"prunes={f1.get('ev_prunes', 0)} "
                     f"commits={f1.get('chase_commits', 0)} "
                     f"boosts={f1.get('chase_boosts', 0)}"
                     if f1 else "")
                  + (f" | f3a casts={f3a.get('casts', 0)}"
                     if f3a else ""), flush=True)

    agg = aggregate(results, args.label)
    agg["elapsed_minutes"] = round((time.time() - t0) / 60, 1)
    (out_dir / "aggregate.json").write_text(json.dumps(agg, indent=2))

    lines = [f"# Campaign: {args.label}", "",
             f"**Record: {agg['wins']}-{agg['losses']}** | total gems "
             f"tributed: {agg['total_gems']} | botFaults: {agg['bot_faults']} | "
             f"{agg['elapsed_minutes']} min", "",
             f"wards placed: {agg.get('total_wards', 0)} "
             f"(on {agg.get('maps_with_wards', 0)} maps) | items bought: "
             f"{agg.get('total_buys', 0)} | consumables bought: "
             f"{agg.get('total_consumables', 0)} | emblems: "
             f"{agg.get('total_emblems', 0)}", "",
             f"champion kills: {agg.get('total_kills', 0)} (opponent "
             f"{agg.get('total_opp_kills', 0)})", "",
             "| map | W/L | gems | opp | camps | deaths | kills | opp_k | fuel | wards | buys | consum | faults | rounds | sec |",
             "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in agg["rows"]:
        if r.get("status") != "ok":
            lines.append(f"| {r['map']} | - | - | - | - | - | - | - | - | - | - | - | - | - | {r['status']} |")
        else:
            lines.append(f"| {r['map']} | {'W' if r['win'] else 'L'} | {r['gems']} | "
                         f"{r['opp_gems']} | {r['camps']} | {r['deaths']} | "
                         f"{r.get('kills', 0)} | {r.get('opp_kills', 0)} | {r.get('fuel_exhausted', 0)} | "
                         f"{r.get('wards', 0)} | {r.get('buys', 0)} | {r.get('consumables', 0)} | "
                         f"{r['bot_faults']} | {r['rounds']} | {r['seconds']} |")
    (out_dir / "table.md").write_text("\n".join(lines) + "\n")
    f1 = agg.get("f1_counters", {})
    f3a = agg.get("f3a_counters", {})
    print(f"[{args.label}] DONE {agg['wins']}-{agg['losses']} | "
          f"{agg['total_gems']} gems | fuel {agg.get('total_fuel_exhausted', 0)} | "
          f"wards {agg.get('total_wards', 0)} | buys {agg.get('total_buys', 0)} | "
          f"consumables {agg.get('total_consumables', 0)} | "
          f"{agg['bot_faults']} faults | {agg['elapsed_minutes']} min", flush=True)
    # F1 close-out evidence: the three mechanisms' counters, on stdout.
    print(f"[{args.label}] THREAT CACHE hits={f1.get('cache_hits', 0)} "
          f"misses={f1.get('cache_misses', 0)} | "
          f"EV PRUNES={f1.get('ev_prunes', 0)} | "
          f"CHASE COMMITS={f1.get('chase_commits', 0)} "
          f"(boosts={f1.get('chase_boosts', 0)})", flush=True)
    # F3a evidence: the ability-layer cast counters, on stdout.
    print(f"[{args.label}] ABILITY CASTS={f3a.get('casts', 0)} "
          f"(dmg={f3a.get('damage', 0)} aoe={f3a.get('aoe', 0)} "
          f"cc={f3a.get('cc', 0)} heal={f3a.get('heal', 0)} "
          f"shield={f3a.get('shield', 0)} dash={f3a.get('dash', 0)} "
          f"reset={f3a.get('reset', 0)} champions={f3a.get('champions', 0)})",
          flush=True)
    # F3b evidence: the transit/regroup counters, on stdout.
    f3b = agg.get("f3b_counters", {})
    print(f"[{args.label}] TRANSIT zones={f3b.get('zones', 0)} "
          f"awake={f3b.get('awake', 0)} deaths={f3b.get('deaths', 0)} "
          f"holds={f3b.get('holds', 0)} priced={f3b.get('priced', 0)} "
          f"champions={f3b.get('champions', 0)}", flush=True)
    # F3b fuel probe: what the transit layer actually SPENT (the engine
    # charges the bot WASM fuel per turn; a turn that runs out is a
    # fuelExhausted event, which is the campaign's compute-cost gate).
    print(f"[{args.label}] TRANSIT FUEL update={f3b.get('fuel_update', 0)} "
          f"price={f3b.get('fuel_price', 0)} "
          f"samples={f3b.get('fuel_n', 0)}", flush=True)
    if args.verdict_file:
        vp = Path(args.verdict_file)
        if vp.exists():
            verdict = vp.read_text().strip()
            print(f"[{args.label}] GEMFIGHT REPLAY VERDICT: {verdict}", flush=True)
        else:
            print(f"[{args.label}] GEMFIGHT REPLAY VERDICT: (missing {vp})",
                  flush=True)


if __name__ == "__main__":
    sys.exit(main())
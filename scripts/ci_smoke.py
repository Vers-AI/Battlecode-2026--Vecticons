#!/usr/bin/env python3
"""CI smoke check: build the bot and play two short matches vs starter.

Exit code 0 = the bot built, both matches completed, and neither side
logged a bot fault. Anything else = failure. Designed for GitHub Actions
(see .github/workflows/ci.yml) and equally runnable locally:

    python3 scripts/ci_smoke.py [--cambc PATH] [--rounds N]

The maps are deliberately small so the whole check fits a CI runner's
budget: 00collision (bush ambush geometry) and 00gem_fields (gem
economy + banking). Rounds are capped (default 500) — the smoke tests
draft + play + combat + banking, not endgame.

This script NEVER touches the network, the platform, or the submission
portal (repo rule 4): cambc run is a fully local engine.
"""
import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

SMOKE_MAPS = ("00collision", "00gem_fields")


def run(cmd, **kw):
    print("+", " ".join(str(c) for c in cmd), flush=True)
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cambc", default="cambc")
    ap.add_argument("--rounds", type=int, default=0,
                    help="0 = play to the end (CI default; full matches "
                         "are short and exercise draft + play + endgame)")
    args = ap.parse_args()

    root = Path(__file__).resolve().parent.parent

    # 1. Build the bot in release mode (the same mode the platform uses).
    build = run([args.cambc, "build", "--release", "bots/vecticons"], cwd=root)
    if build.returncode != 0:
        print("BUILD FAILED")
        print(build.stdout[-4000:])
        print(build.stderr[-4000:])
        return 1
    print("build: OK")

    # 2. Smoke matches vs starter-python, statistics exported as JSON.
    failures = []
    for map_name in SMOKE_MAPS:
        with tempfile.TemporaryDirectory() as td:
            stats = Path(td) / "stats.json"
            cmd = [args.cambc, "run", "bots/vecticons", "bots/starter-python",
                   "--map", map_name, "--release",
                   "--statistics", str(stats), "--json"]
            if args.rounds > 0:
                cmd += ["--rounds", str(args.rounds)]
            match = run(cmd, cwd=root, timeout=1200)
            log = match.stdout + match.stderr
            # Engine prints the final team table on completion.
            table_ok = re.search(r"^\s*team\s+bot", log, re.M) is not None
            if match.returncode != 0 or not table_ok:
                print(f"{map_name}: MATCH DID NOT COMPLETE")
                print(log[-4000:])
                failures.append(map_name)
                continue
            if not stats.exists():
                print(f"{map_name}: no statistics written")
                failures.append(map_name)
                continue
            data = json.loads(stats.read_text())
            diag = data.get("diagnostics") or {}
            faults = diag.get("botFaults") or {}
            if faults.get("teamA") or faults.get("teamB"):
                print(f"{map_name}: BOT FAULTS a={faults.get('teamA')} "
                      f"b={faults.get('teamB')}")
                failures.append(map_name)
                continue
            # We are teamA (the first bot in the cambc run invocation).
            faults_a = faults.get("teamA")
            crashes = 0
            for c in data.get("champions", []):
                if c.get("team") != "teamA":
                    continue
                try:
                    if int(c.get("metrics", {}).get("crashes", 0) or 0) > 0:
                        crashes += 1
                except (TypeError, ValueError):
                    pass
            gems = next((t.get("submittedGems") for t in data.get("teams", [])
                         if t.get("team") == "teamA"), "?")
            if crashes:
                print(f"{map_name}: our champions crashed ({crashes})")
                failures.append(map_name)
                continue
            print(f"{map_name}: OK (our gems={gems}, faults 0/0)")

    if failures:
        print(f"CI SMOKE FAILED: {failures}")
        return 1
    print("CI SMOKE PASSED: build + 2/2 matches clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())

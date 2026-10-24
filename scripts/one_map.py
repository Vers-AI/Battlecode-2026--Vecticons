#!/usr/bin/env python3
"""Run ONE map with the local bot and dump the bot's stdout (trace lines).

Usage:
    python3 scripts/one_map.py MAP [--bot bots/vecticons] [--release]
                                 [--grep TRACE] [--rounds N]

The bot's stdout is not on the CLI's stdout: it lives inside the replay
bundle (`replays/<map>.repl26a` -> `team-a.stdout`). This helper extracts
it so the S2.3 fuel trace can be read directly.
"""
import argparse
import glob
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--bot", default="bots/vecticons")
    ap.add_argument("--cambc", default="/tmp/cambc304/bin/cambc")
    ap.add_argument("--release", action="store_true")
    ap.add_argument("--rounds", type=int, default=0)
    ap.add_argument("--grep", default="")
    ap.add_argument("--tail", type=int, default=40)
    ap.add_argument("--stats", default="")
    args = ap.parse_args()

    replays = ROOT / "replays"
    before = {p: p.stat().st_mtime for p in replays.glob("*.repl26a")}
    cmd = [args.cambc, "run", args.bot, "starter-python", "--map", args.map]
    if args.stats:
        cmd += ["--statistics", args.stats]
    cmd += ["--json"]
    if args.release:
        cmd.append("--release")
    if args.rounds:
        cmd += ["--rounds", str(args.rounds)]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    sys.stderr.write(proc.stdout[-4000:] + "\n" + proc.stderr[-4000:] + "\n")

    after = {p: p.stat().st_mtime for p in replays.glob("*.repl26a")}
    new = [p for p in after if p not in before or after[p] != before[p]]
    if not new:
        new = sorted(replays.glob("*.repl26a"), key=lambda p: p.stat().st_mtime)[-1:]
    if not new:
        print("no replay found", file=sys.stderr)
        return 1
    bundle = max(new, key=lambda p: p.stat().st_mtime)
    lines = []
    with zipfile.ZipFile(bundle) as zf:
        for name in zf.namelist():
            if name.endswith("team-a.stdout"):
                lines = zf.read(name).decode("utf-8", "replace").splitlines()
                break
    if args.grep:
        sel = [ln for ln in lines if args.grep in ln]
    else:
        sel = lines
    for ln in sel[-args.tail:]:
        print(ln)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

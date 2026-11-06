#!/usr/bin/env python3
"""The CAMBC lab — an agent-free "plays well" evaluation pipeline.

WHAT THIS IS
------------
The lab answers one question about a candidate branch, without an agent
in the loop: *does this candidate play at least as well as the baseline?*
It materializes the candidate and the baseline into two ISOLATED throwaway
git worktrees, builds both with the platform build (``cambc build
--release``), plays the SAME map pool with the SAME instrument
(``scripts/run_pool.py``, our bot vs ``bots/starter-python``) on both
sides, and computes a machine verdict from the two campaign records.

The verdict is a number, not an opinion: the candidate record is compared
against the baseline record inside an EMPIRICALLY DERIVED noise band, and
the band is stated in every artefact the lab writes.

THE VERDICT
-----------
Gates, in order:

1. HARD — ``bot_faults``: zero botFaults on both sides of BOTH
   aggregates (the candidate's aggregate AND the baseline's aggregate).
   The spec floor is "candidate == 0, both sides per aggregate"; the
   baseline is included because the baseline is played live in the same
   run — a fault anywhere means the two campaigns are not comparable.
2. HARD — ``candidate_complete``: the candidate completed every map of
   the pool. A map that fails (``exit:N`` / ``timeout`` / ``no-stats``)
   gets ONE documented retry; a map that still fails fails the gate.
3. PRIMARY — ``record_band``: the candidate record against the baseline
   record inside the noise band (see below).
4. REPORTED, NEVER GATED — the gems, fuel and kill deltas. The F6
   lesson (``records/f6-campaign/ablation.md``): a build with the
   lock-break feature entirely REMOVED still lost 233 gems against the
   same gate, so gem totals re-roll by hundreds on ANY binary change.
   They are evidence for a human, never a pass/fail input.

THE NOISE BAND
--------------
The band is EMPIRICAL, not a guess. Three different binaries of this bot
measured the SAME record — 58-12 — on the 70-map pool:

* ``records/f3-campaign/``     — the F3 build
* ``records/f6-campaign/``     — the F6 build
* ``records/f6-campaign/ablation.md`` — F6 with the feature REMOVED

So a binary change moves the RECORD by at most 1 win per 70 maps, while
it moves the gem total by hundreds. The tolerance is therefore
``ceil(pool_size * 1 / 70)`` on the win-count delta. The band is stated
in the verdict (like the SC2 lab's ``noise_band_rule``), together with
its derivation, so nobody has to guess why a number was accepted.

The band is ONE-SIDED on purpose: the candidate must not be WORSE than
the baseline by more than the band. A candidate that is BETTER than the
baseline by more than the band is reported as an anomaly (it is not
noise — something real changed) but it still passes: the band exists to
absorb measurement noise, not to punish an improvement.

VERDICT / EXIT CODES
--------------------
``pass`` -> exit 0, ``fail`` -> exit 1, ``error`` (infra) -> exit 2.
A candidate whose build fails is a FAIL verdict (exit 1) — the lab
reports, it does not fix.

USAGE
-----
    python3 scripts/lab.py --candidate <git-ref> [--baseline <git-ref>]
    python3 scripts/lab.py --candidate origin/main --baseline origin/main \\
        --maps 00ambush,00anvil --label smoke

    # the lab's own smoke check: both sides on the SAME ref, 5 maps,
    # the two aggregates must be byte-identical
    python3 scripts/lab.py --candidate origin/main --self-test

Outputs (under ``records/lab/<label>/``):
    verdict.json  the machine verdict (gates, band, reported deltas)
    summary.md    the human summary (one paragraph, table, verdict, band)
    run.json      the run record: both SHAs, timestamps, maps, aggregates
    candidate.table.md / baseline.table.md   the per-map campaign tables

The lab is safe to launch from cron/systemd: no prompts, no network
beyond one ``git fetch`` (which is tolerated offline with a warning), and
a lockfile (``.cambc/lab.lock``) that keeps two campaigns from colliding.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUN_POOL = ROOT / "scripts" / "run_pool.py"
RECORDS_ROOT = ROOT / "records" / "lab"
DEFAULT_RUNS_DIR = Path("/tmp/opencode/lab-runs")
DEFAULT_BASELINE = "origin/main"
OPPONENT = "bots/starter-python"
OUR_BOT = "bots/vecticons"

EXIT_PASS = 0
EXIT_FAIL = 1
EXIT_ERROR = 2

# ---------------------------------------------------------------------------
# The noise band (empirical, pure)
# ---------------------------------------------------------------------------
#: Binary-change noise on the RECORD, measured: three different binaries
#: all recorded 58-12 on the 70-map pool (records/f3-campaign/,
#: records/f6-campaign/ and the F6 ablation in
#: records/f6-campaign/ablation.md). So the record moves by <= 1 win per
#: 70 maps when the binary changes, while the gem total moves by
#: hundreds.
NOISE_BAND_WINS_PER_BASE_POOL = 1
NOISE_BAND_BASE_POOL = 70
NOISE_BAND_EVIDENCE = (
    "records/f3-campaign/",
    "records/f6-campaign/",
    "records/f6-campaign/ablation.md",
)

#: A lockfile older than this is stale and may be reclaimed.
LOCK_STALE_SECONDS = 2 * 60 * 60

#: The self-test plays this many maps (a spread across the pool).
SELF_TEST_MAPS = 5


class LabError(Exception):
    """An infrastructure failure: the verdict is ``error`` (exit 2)."""


def utcnow() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def utcnow_iso() -> str:
    return utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")


# ---------------------------------------------------------------------------
# PURE LOGIC — the verdict, the band, the aggregate re-summary
# ---------------------------------------------------------------------------
def noise_band_tolerance(pool_size: int) -> int:
    """The win-count tolerance for a pool of *pool_size* maps.

    ``ceil(pool_size * 1 / 70)`` — one win per 70 maps, the measured
    binary-change noise on the record. Integer arithmetic, no float drift.
    """
    if pool_size <= 0:
        return 0
    num = pool_size * NOISE_BAND_WINS_PER_BASE_POOL
    return -(-num // NOISE_BAND_BASE_POOL)


def noise_band_rule(pool_size: int, tolerance: int | None = None) -> str:
    """The active noise band, stated for humans and for the verdict file."""
    tol = noise_band_tolerance(pool_size) if tolerance is None else tolerance
    return (
        f"candidate record not worse than the baseline record by more than "
        f"{tol} win(s) on a {pool_size}-map pool "
        f"(tolerance = ceil({pool_size} * 1/70) = {tol}; "
        f"measured: three different binaries all recorded 58-12 on the "
        f"70-map pool)"
    )


def summarize_rows(rows: list) -> dict:
    """Re-derive the campaign totals from the per-map *rows*.

    Mirrors ``scripts/run_pool.py::aggregate`` so the lab can merge a
    retry back into a campaign and re-aggregate without re-running the
    pool. Only ``status == "ok"`` rows count towards the totals, exactly
    as the harness does.
    """
    ok = [r for r in rows if r.get("status") == "ok"]
    wins = sum(1 for r in ok if r.get("win"))

    def total(key):
        return sum(int(r.get(key, 0) or 0) for r in ok)

    return {
        "matches": len(rows),
        "completed": len(ok),
        "wins": wins,
        "losses": len(ok) - wins,
        "total_gems": total("gems"),
        "total_fuel_exhausted": total("fuel_exhausted"),
        "total_wards": total("wards"),
        "total_buys": total("buys"),
        "total_consumables": total("consumables"),
        "total_emblems": total("emblems"),
        "total_kills": total("kills"),
        "total_opp_kills": total("opp_kills"),
        "maps_with_wards": sum(1 for r in ok if int(r.get("wards", 0) or 0) > 0),
        "bot_faults": total("bot_faults"),
        "opp_bot_faults": total("opp_bot_faults"),
    }


def aggregate_summary(agg: dict) -> dict:
    """The comparable summary of a campaign aggregate."""
    rows = agg.get("rows") or []
    summary = summarize_rows(rows)
    summary["label"] = agg.get("label")
    summary["elapsed_minutes"] = agg.get("elapsed_minutes")
    return summary


def failed_maps(agg: dict) -> list:
    """The maps of *agg* that did not complete, in pool order."""
    return [r.get("map") for r in (agg.get("rows") or [])
            if r.get("status") != "ok"]


def evaluate_record_band(candidate_wins: int, baseline_wins: int,
                         tolerance: int) -> dict:
    """The PRIMARY gate — the candidate record against the baseline record.

    Passes when the candidate is not worse than the baseline by more than
    *tolerance* wins. An improvement larger than the band is reported as
    an anomaly but still passes (the band absorbs noise, it does not
    punish progress).
    """
    delta = int(candidate_wins) - int(baseline_wins)
    worse_beyond_band = delta < -abs(tolerance)
    better_beyond_band = delta > abs(tolerance)
    return {
        "candidate_wins": int(candidate_wins),
        "baseline_wins": int(baseline_wins),
        "delta": delta,
        "tolerance": int(tolerance),
        "within_band": abs(delta) <= abs(tolerance),
        "worse_beyond_band": worse_beyond_band,
        "improvement_anomaly": better_beyond_band,
        "status": "fail" if worse_beyond_band else "pass",
        "detail": (
            f"candidate {candidate_wins} wins vs baseline {baseline_wins} "
            f"(delta {delta:+d}, tolerance {tolerance})"
        ),
    }


def _fault_gate(candidate: dict, baseline: dict) -> dict:
    """The HARD botFaults gate — zero faults on both sides of both sides."""
    c = aggregate_summary(candidate)
    b = aggregate_summary(baseline)
    counts = {
        "candidate_ours": c["bot_faults"],
        "candidate_opponent": c["opp_bot_faults"],
        "baseline_ours": b["bot_faults"],
        "baseline_opponent": b["opp_bot_faults"],
    }
    total = sum(counts.values())
    detail = ", ".join(f"{k}={v}" for k, v in counts.items())
    return {
        "name": "bot_faults",
        "kind": "hard",
        "status": "pass" if total == 0 else "fail",
        "counts": counts,
        "detail": f"botFaults {detail}",
    }


def _complete_gate(candidate: dict) -> dict:
    """The HARD completeness gate — the candidate finished every map."""
    rows = candidate.get("rows") or []
    missing = failed_maps(candidate)
    if not rows:
        return {
            "name": "candidate_complete",
            "kind": "hard",
            "status": "fail",
            "missing_maps": [],
            "detail": "the candidate campaign produced no maps at all",
        }
    return {
        "name": "candidate_complete",
        "kind": "hard",
        "status": "pass" if not missing else "fail",
        "missing_maps": missing,
        "detail": ("every map completed" if not missing
                   else f"{len(missing)} map(s) did not complete: "
                        f"{', '.join(missing)}"),
    }


def _reported(candidate: dict, baseline: dict) -> dict:
    """The REPORTED (never gated) deltas: gems, fuel, kills."""
    c = aggregate_summary(candidate)
    b = aggregate_summary(baseline)
    note = ("reported only, never gated: gem totals re-roll by hundreds "
            "on ANY binary change (F6 ablation: the feature REMOVED still "
            "lost 233 gems) — see records/f6-campaign/ablation.md")
    out = {}
    for key, label in (("total_gems", "gems"),
                       ("total_fuel_exhausted", "fuel"),
                       ("total_kills", "kills")):
        cv = int(c.get(key, 0) or 0)
        bv = int(b.get(key, 0) or 0)
        out[label] = {
            "candidate": cv,
            "baseline": bv,
            "delta": cv - bv,
            "gated": False,
            "note": note,
        }
    return out


def evaluate_verdict(candidate: dict, baseline: dict, pool_size: int,
                     tolerance: int | None = None) -> dict:
    """Compute the verdict from two campaign aggregates. PURE.

    Returns a dict with the ordered gate list, the active noise band and
    the reported (never gated) deltas. The verdict is ``fail`` when any
    HARD gate fails or the PRIMARY band gate fails.
    """
    tol = noise_band_tolerance(pool_size) if tolerance is None else tolerance
    cand = aggregate_summary(candidate)
    base = aggregate_summary(baseline)

    band = evaluate_record_band(cand["wins"], base["wins"], tol)
    gates = [
        _fault_gate(candidate, baseline),
        _complete_gate(candidate),
        {
            "name": "record_band",
            "kind": "primary",
            "status": band["status"],
            "detail": band["detail"],
            "band": band,
        },
    ]
    verdict = "fail" if any(g["status"] == "fail" for g in gates) else "pass"
    return {
        "verdict": verdict,
        "gates": gates,
        "noise_band": {
            "rule": noise_band_rule(pool_size, tol),
            "tolerance": tol,
            "pool_size": pool_size,
            "derivation": (
                "measured: three different binaries all recorded 58-12 on "
                "the 70-map pool, so binary-change noise on the RECORD is "
                "<= 1 win per 70 maps; tolerance = ceil(pool_size * 1/70)"
            ),
            "evidence": list(NOISE_BAND_EVIDENCE),
            "candidate_wins": cand["wins"],
            "baseline_wins": base["wins"],
            "delta": band["delta"],
            "within_band": band["within_band"],
            "improvement_anomaly": band["improvement_anomaly"],
        },
        "reported": _reported(candidate, baseline),
        "candidate": cand,
        "baseline": base,
    }


def canonical_aggregate(agg: dict) -> str:
    """The byte-comparable form of a campaign aggregate. PURE.

    Two campaigns of the SAME binary on the SAME maps must produce the
    same bytes. The label, the wall clock and the per-match duration are
    run metadata, not results, so they are excluded; everything else —
    every per-map row, every counter, every total — is included
    verbatim.
    """
    canonical = {k: v for k, v in agg.items()
                 if k not in ("label", "elapsed_minutes")}
    rows = []
    for row in canonical.get("rows") or []:
        rows.append({k: v for k, v in row.items() if k != "seconds"})
    if "rows" in canonical:
        canonical["rows"] = rows
    return json.dumps(canonical, sort_keys=True, separators=(",", ":"))


#: The totals the harness writes into aggregate.json — the keys the lab
#: re-derives from the rows and cross-checks.
HARNESS_TOTAL_KEYS = (
    "matches", "wins", "losses", "total_gems", "total_fuel_exhausted",
    "total_wards", "total_buys", "total_consumables", "total_emblems",
    "total_kills", "total_opp_kills", "maps_with_wards", "bot_faults",
    "opp_bot_faults",
)


def harness_totals(agg: dict) -> dict:
    """The harness's own totals. PURE."""
    return {key: agg.get(key) for key in HARNESS_TOTAL_KEYS}


def resummary_mismatches(agg: dict) -> list:
    """Where the lab's re-summary disagrees with the harness's totals. PURE.

    The lab re-derives the totals from the per-map rows (so it can merge
    a retry back in). This cross-check proves the re-derivation agrees
    with the harness that produced the rows.
    """
    mine = summarize_rows(agg.get("rows") or [])
    theirs = harness_totals(agg)
    return [key for key, value in theirs.items()
            if value is not None and mine.get(key) != value]


def aggregate_mismatches(a: dict, b: dict) -> list:
    """The summary fields on which two aggregates disagree. PURE."""
    sa = aggregate_summary(a)
    sb = aggregate_summary(b)
    return [k for k in sorted(sa)
            if k not in ("label", "elapsed_minutes") and sa[k] != sb[k]]


# ---------------------------------------------------------------------------
# PURE LOGIC — the lockfile
# ---------------------------------------------------------------------------
def parse_lock_payload(text: str) -> dict:
    """Parse a lockfile body. PURE. Never raises."""
    text = (text or "").strip()
    if not text:
        return {}
    try:
        payload = json.loads(text)
        if isinstance(payload, dict):
            return payload
    except (ValueError, TypeError):
        pass
    # Tolerate the flat "pid=<n> started=<ts>" form.
    payload = {}
    for part in text.replace("\n", " ").split():
        if "=" in part:
            key, _, value = part.partition("=")
            payload[key.strip()] = value.strip()
    return payload


def pid_alive(pid: int) -> bool:
    """True when *pid* names a live process."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def lock_is_stale(payload: dict, now: dt.datetime | None = None) -> tuple:
    """Is the lock reclaimable? PURE-ish (pid liveness is a syscall).

    Returns ``(stale, reason)``. A lock is stale when the recorded PID
    is dead, or when it is older than :data:`LOCK_STALE_SECONDS`.
    """
    now = now or utcnow()
    pid = int(payload.get("pid", 0) or 0)
    if not pid:
        return True, "no pid recorded in the lockfile"
    if not pid_alive(pid):
        return True, f"pid {pid} is dead"
    started = payload.get("started_at") or ""
    age = None
    if started:
        try:
            stamp = dt.datetime.fromisoformat(str(started))
            if stamp.tzinfo is None:
                stamp = stamp.replace(tzinfo=dt.timezone.utc)
            age = (now - stamp).total_seconds()
        except ValueError:
            age = None
    if age is None:
        return False, "lock has no usable timestamp"
    if age > LOCK_STALE_SECONDS:
        return True, (f"lock is {int(age // 60)} min old "
                      f"(> {LOCK_STALE_SECONDS // 60} min)")
    return False, f"lock is {int(age // 60)} min old and pid {pid} is alive"


# ---------------------------------------------------------------------------
# IMPURE — git, worktrees, build, campaign
# ---------------------------------------------------------------------------
def warn(message: str) -> None:
    print(f"[lab] WARN: {message}", flush=True)


def info(message: str) -> None:
    print(f"[lab] {message}", flush=True)


def unbuffer_stdout() -> None:
    """Line-buffer the lab's own stdout (it is usually a log file)."""
    try:
        sys.stdout.reconfigure(line_buffering=True)
    except (AttributeError, ValueError):
        pass


def install_signal_handlers() -> None:
    """Make a SIGTERM/SIGINT run the cleanup (worktrees, lockfile).

    Without this a killed lab leaves its throwaway worktrees behind and
    the lockfile in place — the next start would have to reclaim it.
    """
    import signal

    def _terminate(signum, _frame):
        raise SystemExit(128 + signum)

    for name in ("SIGTERM", "SIGINT", "SIGHUP"):
        signum = getattr(signal, name, None)
        if signum is None:
            continue
        try:
            signal.signal(signum, _terminate)
        except (OSError, ValueError):
            pass


def git(args: list, cwd: Path | None = None, check: bool = True,
        timeout: int = 600) -> subprocess.CompletedProcess:
    proc = subprocess.run(["git", *args], cwd=str(cwd or ROOT),
                          capture_output=True, text=True, timeout=timeout)
    if check and proc.returncode != 0:
        message = (proc.stderr or proc.stdout or "").strip()
        raise LabError(f"git {' '.join(args)} failed: {message}")
    return proc


def fetch_origin() -> bool:
    """``git fetch --prune origin`` — tolerated offline."""
    proc = git(["fetch", "--prune", "origin"], check=False, timeout=300)
    if proc.returncode != 0:
        warn(f"git fetch --prune origin failed (offline?): "
             f"{(proc.stderr or proc.stdout).strip()}")
        return False
    return True


def resolve_ref(ref: str) -> str:
    """Resolve a git ref to a commit SHA."""
    proc = git(["rev-parse", "--verify", f"{ref}^{{commit}}"], check=False)
    if proc.returncode != 0:
        raise LabError(f"cannot resolve ref {ref!r}: "
                       f"{(proc.stderr or proc.stdout).strip()}")
    return proc.stdout.strip()


def add_worktree(path: Path, sha: str) -> None:
    """Materialize *sha* into an isolated throwaway worktree."""
    path.parent.mkdir(parents=True, exist_ok=True)
    git(["worktree", "add", "--detach", str(path), sha])
    head = git(["-C", str(path), "rev-parse", "HEAD"]).stdout.strip()
    if head != sha:
        raise LabError(f"worktree {path} is at {head}, expected {sha}")


def remove_worktree(path: Path) -> None:
    """Remove a throwaway worktree. Best effort, never raises."""
    if not path.exists():
        return
    proc = git(["worktree", "remove", "--force", str(path)], check=False)
    if proc.returncode != 0:
        warn(f"could not remove worktree {path}: "
             f"{(proc.stderr or proc.stdout).strip()}")
        shutil.rmtree(path, ignore_errors=True)


def worktree_maps(worktree: Path) -> list:
    """The map pool of a worktree, sorted by name."""
    maps_dir = worktree / "maps"
    if not maps_dir.is_dir():
        return []
    return sorted(p.stem for p in maps_dir.glob("*.map26a"))


def map_digest(path: Path) -> str:
    """The sha256 of a map file (the instrument's identity)."""
    h = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def resolve_cambc(explicit: str | None) -> str:
    """Find the cambc binary.

    Order: --cambc, $CAMBC, this checkout's .venv, the MAIN checkout's
    .venv (the lab usually runs from a worktree, and the toolchain is
    installed in the main checkout), then $PATH.
    """
    candidates = []
    if explicit:
        candidates.append(explicit)
    env = os.environ.get("CAMBC")
    if env:
        candidates.append(env)
    candidates.append(str(ROOT / ".venv" / "bin" / "cambc"))
    main_checkout = main_checkout_dir()
    if main_checkout:
        candidates.append(str(main_checkout / ".venv" / "bin" / "cambc"))
    found = shutil.which("cambc")
    if found:
        candidates.append(found)
    for candidate in candidates:
        path = Path(candidate)
        if path.exists() and os.access(path, os.X_OK):
            return str(path)
    raise LabError("no cambc binary found (pass --cambc or set CAMBC)")


def main_checkout_dir() -> Path | None:
    """The main checkout of this repository, if we can find it.

    A lab run usually happens from a worktree; the toolchain (and the
    .venv) live in the main checkout, which is the parent of the shared
    git directory.
    """
    proc = git(["rev-parse", "--path-format=absolute", "--git-common-dir"],
               check=False)
    if proc.returncode != 0:
        return None
    common = Path(proc.stdout.strip())
    if common.name != ".git":
        return None
    return common.parent


def cambc_version(cambc: str) -> str:
    proc = subprocess.run([cambc, "--version"], capture_output=True,
                          text=True, timeout=120)
    version = (proc.stdout or proc.stderr).strip()
    if version.lower().startswith("cambc "):
        version = version[6:].strip()
    return version


def build_bot(worktree: Path, cambc: str) -> subprocess.CompletedProcess:
    """``cambc build --release`` inside the worktree."""
    return subprocess.run([cambc, "build", "--release", OUR_BOT],
                          cwd=str(worktree), capture_output=True, text=True,
                          timeout=1800)


def run_campaign(worktree: Path, label: str, maps: list, concurrency: int,
                 cambc: str, log_path: Path) -> tuple:
    """Play one full-pool campaign with the harness. Returns (aggregate, log)."""
    cmd = [sys.executable, "-u", str(RUN_POOL),
           "--project-root", str(worktree),
           "--bot-a", OUR_BOT,
           "--bot-b", OPPONENT,
           "--label", label,
           "--concurrency", str(concurrency),
           "--release",
           "--cambc", str(cambc),
           "--maps", ",".join(maps)]
    info(f"campaign {label}: {len(maps)} maps, concurrency {concurrency}")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with open(log_path, "w") as log:
        log.write(f"+ {' '.join(cmd)}\n")
        log.flush()
        proc = subprocess.Popen(cmd, cwd=str(worktree),
                                stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, text=True,
                                bufsize=1)
        for line in proc.stdout:
            sys.stdout.write(line)
            sys.stdout.flush()
            log.write(line)
            log.flush()
        rc = proc.wait()
    if rc != 0:
        raise LabError(f"campaign {label} failed (exit {rc}); see {log_path}")
    agg_path = worktree / "results" / label / "aggregate.json"
    if not agg_path.exists():
        raise LabError(f"campaign {label} produced no aggregate.json")
    aggregate = json.loads(agg_path.read_text())
    return aggregate, log_path


def retry_failed_maps(worktree: Path, label: str, maps: list,
                      concurrency: int, cambc: str, log_path: Path) -> dict:
    """The documented retry: one more campaign over the failed maps only."""
    retry_label = f"{label}-retry"
    aggregate, _ = run_campaign(worktree, retry_label, maps, concurrency,
                                cambc, log_path)
    return aggregate


def merge_rows(base_rows: list, retry_rows: list) -> list:
    """Merge a retry's rows back into the campaign rows (pure)."""
    by_map = {r.get("map"): r for r in base_rows}
    for row in retry_rows:
        by_map[row.get("map")] = row
    return [by_map[m] for m in sorted(by_map, key=lambda m: (m is None, m))]


def engine_version(worktree: Path, label: str) -> str:
    """The engine version of a campaign, read from a raw stats file."""
    results = worktree / "results" / label
    if not results.is_dir():
        return ""
    for stats in sorted(results.glob("*.stats.json")):
        try:
            data = json.loads(stats.read_text())
        except (ValueError, OSError):
            continue
        version = (data.get("match") or {}).get("engineVersion")
        if version:
            return str(version)
    return ""


# ---------------------------------------------------------------------------
# The lock
# ---------------------------------------------------------------------------
def acquire_lock(lock_path: Path, label: str) -> dict:
    """Take the lab-wide lock. Raises :class:`LabError` when busy."""
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "pid": os.getpid(),
        "started_at": utcnow_iso(),
        "label": label,
        "host": os.uname().nodename,
        "run_dir": str(lock_path.parent),
    }
    body = json.dumps(payload, indent=2) + "\n"
    for attempt in (1, 2):
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY,
                         0o644)
        except FileExistsError:
            existing = parse_lock_payload(lock_path.read_text())
            stale, reason = lock_is_stale(existing)
            if stale and attempt == 1:
                info(f"reclaiming a stale lockfile: {reason}")
                payload["reclaimed_lock"] = {
                    "previous": existing,
                    "reason": reason,
                }
                body = json.dumps(payload, indent=2) + "\n"
                try:
                    lock_path.unlink()
                except OSError:
                    pass
                continue
            active = existing.get("label") or "unknown"
            raise LabError(
                f"another lab run is active (label {active!r}, pid "
                f"{existing.get('pid')}, started {existing.get('started_at')})"
                f" — lockfile {lock_path}"
            )
        else:
            with os.fdopen(fd, "w") as handle:
                handle.write(body)
            return payload
    raise LabError(f"could not take the lab lock {lock_path}")


def release_lock(lock_path: Path, payload: dict) -> None:
    """Release the lock we hold (only if we still hold it)."""
    try:
        current = parse_lock_payload(lock_path.read_text())
    except OSError:
        return
    if current.get("pid") != payload.get("pid"):
        return
    try:
        lock_path.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def render_summary_md(record: dict) -> str:
    """The human summary of a run. PURE (given the run record)."""
    verdict = record.get("verdict") or {"verdict": "error"}
    cand = verdict.get("candidate") or {}
    base = verdict.get("baseline") or {}
    band = verdict.get("noise_band") or {}
    reported = verdict.get("reported") or {}

    verdict_word = str(verdict.get("verdict", "error")).upper()

    def short(sha) -> str:
        return sha[:12] if sha else "unknown"

    def number(value) -> int:
        try:
            return int(value)
        except (TypeError, ValueError):
            return 0

    c_sha = short((record.get("candidate") or {}).get("sha"))
    b_sha = short((record.get("baseline") or {}).get("sha"))
    tolerance = band.get("tolerance")
    tolerance_text = f"±{tolerance}" if tolerance is not None else "n/a"
    band_text = band.get("rule", "not evaluated")
    delta = number(band.get("delta"))
    outcome = (
        "All hard gates passed and the record is not worse than the "
        "baseline beyond the measured band."
        if verdict_word == "PASS"
        else "At least one gate failed — see the gate list below."
    )

    para = (
        f"The lab played the {record.get('pool_size', 0)}-map pool twice on "
        f"the same instrument (cambc {record.get('cambc_version', '?')}, "
        f"engine {record.get('engine_version', '?')}, `--release`): the "
        f"candidate `{(record.get('candidate') or {}).get('ref', '?')}` "
        f"({c_sha}) and the baseline "
        f"`{(record.get('baseline') or {}).get('ref', '?')}` ({b_sha}), both "
        f"against `{OPPONENT}`, both fresh in this run. The candidate "
        f"recorded **{number(cand.get('wins'))}-"
        f"{number(cand.get('losses'))}** and the baseline recorded "
        f"**{number(base.get('wins'))}-{number(base.get('losses'))}** "
        f"({delta:+d} wins), against a noise band of {tolerance_text} "
        f"win(s). **Verdict: {verdict_word}.** {outcome} The gems, fuel and "
        f"kill deltas are reported but never gated: the F6 ablation showed "
        f"a build with the feature REMOVED still loses hundreds of gems, "
        f"so those totals re-roll on any binary change."
    )

    lines = [f"# Lab verdict — {record.get('label', 'lab')}", "", para, ""]
    lines += [
        "| metric | candidate | baseline | delta | gated |",
        "|---|---|---|---|---|",
        f"| record (wins-losses) | {number(cand.get('wins'))}-"
        f"{number(cand.get('losses'))} | {number(base.get('wins'))}-"
        f"{number(base.get('losses'))} | {delta:+d} wins | **yes** |",
        f"| maps completed | {number(cand.get('completed'))}/"
        f"{number(cand.get('matches'))} | {number(base.get('completed'))}/"
        f"{number(base.get('matches'))} | - | yes |",
        f"| botFaults (ours / opponent) | {number(cand.get('bot_faults'))} / "
        f"{number(cand.get('opp_bot_faults'))} | "
        f"{number(base.get('bot_faults'))} / "
        f"{number(base.get('opp_bot_faults'))} | - | yes |",
        f"| gems | {number(reported.get('gems', {}).get('candidate'))} | "
        f"{number(reported.get('gems', {}).get('baseline'))} | "
        f"{number(reported.get('gems', {}).get('delta')):+d} | "
        f"no (reported) |",
        f"| fuel exhausted | "
        f"{number(reported.get('fuel', {}).get('candidate'))} | "
        f"{number(reported.get('fuel', {}).get('baseline'))} | "
        f"{number(reported.get('fuel', {}).get('delta')):+d} | "
        f"no (reported) |",
        f"| champion kills | {number(reported.get('kills', {}).get('candidate'))}"
        f" | {number(reported.get('kills', {}).get('baseline'))} | "
        f"{number(reported.get('kills', {}).get('delta')):+d} | "
        f"no (reported) |",
        "",
        f"## Verdict: {verdict_word}",
        "",
        f"**Noise band (active):** {band_text}.",
        "",
        f"*Derivation:* {band.get('derivation', 'not evaluated')}.",
        "",
        "*Evidence:* " + ", ".join(f"`{e}`" for e in band.get("evidence", [])),
        "",
        "## Gates",
        "",
    ]
    for gate in verdict.get("gates", []):
        mark = "PASS" if gate["status"] == "pass" else "FAIL"
        lines.append(f"- [{mark}] **{gate['name']}** ({gate['kind']}): "
                     f"{gate['detail']}")
    lines += ["", "## Reported (never gated)", ""]
    for name, item in reported.items():
        lines.append(f"- **{name}**: candidate {item['candidate']}, baseline "
                     f"{item['baseline']} ({item['delta']:+d}) — {item['note']}")
    lines += [
        "",
        "## The run",
        "",
        f"- label: `{record.get('label', 'lab')}`",
        f"- candidate: `{(record.get('candidate') or {}).get('ref', '?')}` -> "
        f"`{(record.get('candidate') or {}).get('sha', 'unknown')}`",
        f"- baseline: `{(record.get('baseline') or {}).get('ref', '?')}` -> "
        f"`{(record.get('baseline') or {}).get('sha', 'unknown')}`",
        f"- pool: {record.get('pool_size', 0)} maps, "
        f"concurrency {record.get('concurrency')}",
        f"- instrument: {record.get('cambc')} "
        f"({record.get('cambc_version')}), engine {record.get('engine_version')}",
        f"- started {record.get('started_at')}, finished "
        f"{record.get('finished_at')} "
        f"({record.get('elapsed_minutes')} min)",
        f"- run directory: `{record.get('run_dir')}`",
        "",
    ]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# The pipeline
# ---------------------------------------------------------------------------
def spread_sample(maps: list, count: int) -> list:
    """A deterministic spread of *count* maps across the pool. PURE.

    The self-test wants a handful of maps that represent the pool (not
    the alphabetically first five), and it must pick the same ones every
    time.
    """
    if count <= 0 or not maps:
        return []
    if count >= len(maps):
        return list(maps)
    picked = []
    for index in range(count):
        position = min(len(maps) - 1, (index * len(maps)) // count)
        if maps[position] not in picked:
            picked.append(maps[position])
    return picked


def select_maps(candidate_wt: Path, baseline_wt: Path, requested: str,
                pool: int, self_test: bool = False) -> tuple:
    """Resolve the map set both sides play. Returns (maps, notes)."""
    cand_maps = worktree_maps(candidate_wt)
    base_maps = worktree_maps(baseline_wt)
    if not cand_maps:
        raise LabError(f"no maps found in {candidate_wt / 'maps'}")
    notes = []
    if requested and requested != "all":
        wanted = [m.strip() for m in requested.split(",") if m.strip()]
        unknown = [m for m in wanted
                   if m not in cand_maps or m not in base_maps]
        if unknown:
            raise LabError(f"map(s) not present on both sides: "
                           f"{', '.join(unknown)}")
        maps = wanted
    else:
        common = sorted(set(cand_maps) & set(base_maps))
        only_cand = sorted(set(cand_maps) - set(base_maps))
        only_base = sorted(set(base_maps) - set(cand_maps))
        if only_cand:
            notes.append(f"maps only on the candidate side (dropped): "
                         f"{', '.join(only_cand)}")
        if only_base:
            notes.append(f"maps only on the baseline side (dropped): "
                         f"{', '.join(only_base)}")
        maps = common
    if self_test:
        maps = spread_sample(maps, SELF_TEST_MAPS)
        notes.append(f"self-test: the pool was sampled down to "
                     f"{len(maps)} spread maps")
    elif pool:
        maps = maps[:pool]
    if not maps:
        raise LabError("the map pool is empty after selection")
    return maps, notes


def canonicalize_maps(candidate_wt: Path, baseline_wt: Path,
                      maps: list) -> dict:
    """Make the two sides play byte-identical maps.

    The candidate's map bytes are the instrument; a baseline map that
    differs is overwritten (in the throwaway worktree only) so both
    campaigns run on the same bytes. The digest report goes into the run
    record either way.
    """
    report = {"identical": True, "replaced": [], "digests": {}}
    for name in maps:
        cand_path = candidate_wt / "maps" / f"{name}.map26a"
        base_path = baseline_wt / "maps" / f"{name}.map26a"
        if not cand_path.exists() or not base_path.exists():
            report["identical"] = False
            continue
        cand_digest = map_digest(cand_path)
        base_digest = map_digest(base_path)
        report["digests"][name] = {"candidate": cand_digest,
                                   "baseline": base_digest}
        if cand_digest != base_digest:
            report["identical"] = False
            report["replaced"].append(name)
            shutil.copyfile(cand_path, base_path)
    return report


def play_side(name: str, worktree: Path, label: str, maps: list,
              concurrency: int, cambc: str, run_dir: Path) -> tuple:
    """Build (already done) + play one side, with the documented retry."""
    aggregate, log = run_campaign(worktree, label, maps, concurrency, cambc,
                                  run_dir / f"{label}.campaign.log")
    keep_table(worktree, label, run_dir)
    missing = failed_maps(aggregate)
    retry = None
    if missing:
        info(f"{name}: {len(missing)} map(s) failed, one retry: "
             f"{', '.join(missing)}")
        retry_agg, retry_log = retry_failed_maps(
            worktree, label, missing, concurrency, cambc,
            run_dir / f"{label}.retry.log")
        aggregate["rows"] = merge_rows(aggregate.get("rows") or [],
                                       retry_agg.get("rows") or [])
        aggregate.update(summarize_rows(aggregate["rows"]))
        still = failed_maps(aggregate)
        info(f"{name}: after the retry, {len(still)} map(s) still failed")
        retry = {"maps": missing, "still_failing": still,
                 "log": str(retry_log)}
    return aggregate, retry


def keep_table(worktree: Path, label: str, run_dir: Path) -> None:
    """Copy the harness's per-map table out of the throwaway worktree."""
    source = worktree / "results" / label / "table.md"
    if not source.exists():
        return
    target = run_dir / "tables" / f"{label}.table.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)


def run_lab(args) -> tuple:
    """The whole pipeline. Returns (record, exit_code)."""
    started = utcnow()
    label = args.label or default_label(args.candidate)
    run_id = utcnow().strftime("%Y%m%dT%H%M%SZ") + f"-{os.getpid()}"
    run_dir = Path(args.runs_dir) / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    record = {
        "label": label,
        "run_id": run_id,
        "run_dir": str(run_dir),
        "started_at": started.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": None,
        "elapsed_minutes": None,
        "candidate": {"ref": args.candidate, "sha": None},
        "baseline": {"ref": args.baseline, "sha": None},
        "pool_size": 0,
        "maps": [],
        "concurrency": args.concurrency,
        "cambc": None,
        "cambc_version": None,
        "engine_version": None,
        "lock": {},
        "notes": [],
        "aggregates": {},
        "verdict": None,
        "exit_code": None,
        "self_test": bool(args.self_test),
        "lab": lab_provenance(),
    }

    lock_payload = {}
    worktrees = []
    try:
        cambc = resolve_cambc(args.cambc)
        record["cambc"] = cambc
        record["cambc_version"] = cambc_version(cambc)
        info(f"cambc: {cambc} ({record['cambc_version']})")

        lock_path = Path(args.lock_file) if args.lock_file else (
            ROOT / ".cambc" / "lab.lock")
        lock_payload = acquire_lock(lock_path, label)
        record["lock"] = {"path": str(lock_path), **lock_payload}
        info(f"lock: {lock_path} (pid {os.getpid()})")

        info("fetching origin (tolerates offline)")
        record["notes"].append(
            "git fetch --prune origin: "
            + ("ok" if fetch_origin() else "FAILED (offline, tolerated)"))

        candidate_ref = args.candidate
        baseline_ref = args.baseline
        if args.self_test:
            baseline_ref = candidate_ref
            record["notes"].append(
                "self-test: both sides played the SAME ref "
                f"({candidate_ref}); the aggregates must be byte-identical")

        candidate_sha = resolve_ref(candidate_ref)
        baseline_sha = resolve_ref(baseline_ref)
        record["candidate"].update({"ref": candidate_ref, "sha": candidate_sha})
        record["baseline"].update({"ref": baseline_ref, "sha": baseline_sha})
        info(f"candidate {candidate_ref} -> {candidate_sha}")
        info(f"baseline  {baseline_ref} -> {baseline_sha}")

        candidate_wt = run_dir / "candidate"
        baseline_wt = run_dir / "baseline"
        add_worktree(candidate_wt, candidate_sha)
        worktrees.append(candidate_wt)
        add_worktree(baseline_wt, baseline_sha)
        worktrees.append(baseline_wt)
        record["worktrees"] = {"candidate": str(candidate_wt),
                               "baseline": str(baseline_wt)}

        maps, notes = select_maps(candidate_wt, baseline_wt, args.maps,
                                  args.pool, args.self_test)
        record["maps"] = maps
        record["pool_size"] = len(maps)
        record["notes"].extend(notes)
        map_report = canonicalize_maps(candidate_wt, baseline_wt, maps)
        record["map_bytes"] = map_report
        if not map_report["identical"]:
            record["notes"].append(
                "map bytes differed between the two sides; the candidate's "
                f"bytes were copied into the baseline worktree for "
                f"{len(map_report['replaced'])} map(s) so both campaigns ran "
                "on the same instrument")
        info(f"pool: {len(maps)} maps")

        # --- build both sides -------------------------------------------
        build = build_bot(candidate_wt, cambc)
        record["candidate_build"] = {"ok": build.returncode == 0,
                                     "returncode": build.returncode}
        if build.returncode != 0:
            (run_dir / "candidate.build.log").write_text(
                build.stdout + "\n--- stderr ---\n" + build.stderr)
            record["notes"].append(
                "the candidate build FAILED — a FAIL verdict, not a fix")
            record["verdict"] = {
                "verdict": "fail",
                "gates": [{
                    "name": "candidate_build",
                    "kind": "hard",
                    "status": "fail",
                    "detail": f"`cambc build --release` failed on the "
                              f"candidate (exit {build.returncode}); see "
                              f"{run_dir / 'candidate.build.log'}",
                }],
                "noise_band": {
                    "rule": noise_band_rule(len(maps)),
                    "tolerance": noise_band_tolerance(len(maps)),
                    "pool_size": len(maps),
                    "derivation": "not evaluated — the candidate did not "
                                  "build",
                    "evidence": list(NOISE_BAND_EVIDENCE),
                },
                "reported": {},
                "candidate": {},
                "baseline": {},
            }
            record["exit_code"] = EXIT_FAIL
            return record, EXIT_FAIL
        info("candidate build: OK")

        build = build_bot(baseline_wt, cambc)
        record["baseline_build"] = {"ok": build.returncode == 0,
                                    "returncode": build.returncode}
        if build.returncode != 0:
            (run_dir / "baseline.build.log").write_text(
                build.stdout + "\n--- stderr ---\n" + build.stderr)
            raise LabError(
                f"the BASELINE build failed (exit {build.returncode}); the "
                f"instrument is broken — see {run_dir / 'baseline.build.log'}")
        info("baseline build: OK")

        # --- play both sides -------------------------------------------
        candidate_agg, candidate_retry = play_side(
            "candidate", candidate_wt, "lab-candidate", maps,
            args.concurrency, cambc, run_dir)
        baseline_agg, baseline_retry = play_side(
            "baseline", baseline_wt, "lab-baseline", maps,
            args.concurrency, cambc, run_dir)
        record["retries"] = {"candidate": candidate_retry,
                             "baseline": baseline_retry}

        engine = engine_version(candidate_wt, "lab-candidate")
        record["engine_version"] = engine
        base_engine = engine_version(baseline_wt, "lab-baseline")
        if base_engine and engine and base_engine != engine:
            record["notes"].append(
                f"engine version differs between the sides: candidate "
                f"{engine}, baseline {base_engine}")

        # --- integrity: the harness and the lab must agree -------------
        for name, agg in (("candidate", candidate_agg),
                          ("baseline", baseline_agg)):
            mismatches = resummary_mismatches(agg)
            if mismatches:
                record["notes"].append(
                    f"{name}: the harness aggregate and the lab re-summary "
                    f"disagree on {', '.join(mismatches)}")

        record["aggregates"] = {
            "candidate": candidate_agg,
            "baseline": baseline_agg,
        }

        # --- the verdict ------------------------------------------------
        verdict = evaluate_verdict(candidate_agg, baseline_agg, len(maps))
        record["verdict"] = verdict

        if args.self_test:
            same = canonical_aggregate(candidate_agg) == canonical_aggregate(
                baseline_agg)
            verdict["self_test"] = {
                "identical": same,
                "canonical_bytes": len(
                    canonical_aggregate(candidate_agg)),
            }
            if not same:
                diff = aggregate_mismatches(candidate_agg, baseline_agg)
                verdict["verdict"] = "error"
                verdict["gates"].append({
                    "name": "determinism",
                    "kind": "hard",
                    "status": "fail",
                    "detail": "the two sides of the SAME ref did not produce "
                              f"identical aggregates (differing: "
                              f"{', '.join(diff) or 'bytes'})",
                })
                record["notes"].append(
                    "self-test FAILED: identical refs produced different "
                    "aggregates")
            else:
                verdict["gates"].append({
                    "name": "determinism",
                    "kind": "hard",
                    "status": "pass",
                    "detail": "identical refs produced byte-identical "
                              "aggregates",
                })

        exit_code = exit_code_for(verdict["verdict"])
        record["exit_code"] = exit_code
        return record, exit_code

    except LabError as exc:
        record["notes"].append(f"infra error: {exc}")
        record["error"] = str(exc)
        record["verdict"] = record.get("verdict") or {
            "verdict": "error",
            "gates": [{"name": "infrastructure", "kind": "hard",
                       "status": "fail", "detail": str(exc)}],
            "noise_band": {"rule": "not evaluated", "tolerance": None,
                           "pool_size": record.get("pool_size", 0),
                           "derivation": "not evaluated", "evidence": []},
            "reported": {}, "candidate": {}, "baseline": {},
        }
        record["exit_code"] = EXIT_ERROR
        return record, EXIT_ERROR
    except SystemExit:
        # A signal (SIGTERM/SIGINT) reached us: the worktrees and the
        # lockfile are released by the finally block, and the run is
        # recorded as an infra error.
        record["notes"].append("the lab was interrupted by a signal")
        record["error"] = "interrupted by a signal"
        record["verdict"] = record.get("verdict") or {
            "verdict": "error",
            "gates": [{"name": "infrastructure", "kind": "hard",
                       "status": "fail",
                       "detail": "the lab was interrupted by a signal"}],
            "noise_band": {"rule": "not evaluated", "tolerance": None,
                           "pool_size": record.get("pool_size", 0),
                           "derivation": "not evaluated", "evidence": []},
            "reported": {}, "candidate": {}, "baseline": {},
        }
        record["exit_code"] = EXIT_ERROR
        return record, EXIT_ERROR
    finally:
        for worktree in reversed(worktrees):
            remove_worktree(worktree)
        if lock_payload:
            release_lock(Path(args.lock_file) if args.lock_file else (
                ROOT / ".cambc" / "lab.lock"), lock_payload)
        finished = utcnow()
        record["finished_at"] = finished.strftime("%Y-%m-%dT%H:%M:%SZ")
        record["elapsed_minutes"] = round(
            (finished - started).total_seconds() / 60, 1)


def lab_provenance() -> dict:
    """Which lab produced this run: the repo revision + the script digest."""
    provenance = {"script": "scripts/lab.py"}
    try:
        provenance["sha256"] = hashlib.sha256(
            Path(__file__).resolve().read_bytes()).hexdigest()
    except OSError:
        pass
    proc = git(["rev-parse", "HEAD"], check=False)
    if proc.returncode == 0:
        provenance["revision"] = proc.stdout.strip()
    return provenance


def exit_code_for(verdict: str) -> int:
    """The process exit code of a verdict. PURE.

    0 = pass, 1 = fail, 2 = infra error — the three outcomes are
    distinguishable from the outside.
    """
    return {"pass": EXIT_PASS, "fail": EXIT_FAIL}.get(verdict, EXIT_ERROR)


def default_label(candidate_ref: str) -> str:
    """A filesystem-safe label derived from the candidate ref."""
    slug = "".join(ch if ch.isalnum() or ch in "-_" else "-"
                   for ch in candidate_ref).strip("-")
    return f"lab-{utcnow().strftime('%Y%m%d-%H%M%S')}-{slug}"[:80]


def write_outputs(record: dict, records_dir: Path) -> Path:
    """Write verdict.json, summary.md and the run record."""
    out_dir = records_dir / record["label"]
    out_dir.mkdir(parents=True, exist_ok=True)

    verdict = record.get("verdict") or {"verdict": "error"}
    (out_dir / "verdict.json").write_text(json.dumps(verdict, indent=2) + "\n")
    (out_dir / "summary.md").write_text(render_summary_md(record))

    run_record = dict(record)
    run_record["verdict"] = verdict
    (out_dir / "run.json").write_text(json.dumps(run_record, indent=2) + "\n")

    aggregates = record.get("aggregates") or {}
    for name, label in (("candidate", "lab-candidate"),
                        ("baseline", "lab-baseline")):
        agg = aggregates.get(name) or {}
        if agg.get("rows") is not None:
            (out_dir / f"{name}.aggregate.json").write_text(
                json.dumps(agg, indent=2) + "\n")
        source = Path(record["run_dir"]) / "tables" / f"{label}.table.md"
        if source.exists():
            shutil.copyfile(source, out_dir / f"{name}.table.md")
    return out_dir


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="The CAMBC lab: an agent-free 'plays well' evaluation.")
    ap.add_argument("--candidate", required=True,
                    help="git ref of the candidate branch")
    ap.add_argument("--baseline", default=DEFAULT_BASELINE,
                    help=f"git ref of the baseline (default {DEFAULT_BASELINE})")
    ap.add_argument("--maps", default="all",
                    help="'all' or a comma-separated list of maps")
    ap.add_argument("--label", default="", help="run label (records/lab/<label>)")
    ap.add_argument("--concurrency", type=int, default=6,
                    help="harness concurrency (default 6)")
    ap.add_argument("--cambc", default="", help="path to the cambc binary")
    ap.add_argument("--pool", type=int, default=0,
                    help="cap the pool at N maps (deterministic order)")
    ap.add_argument("--runs-dir", default=str(DEFAULT_RUNS_DIR),
                    help="where the throwaway worktrees live")
    ap.add_argument("--records-dir", default=str(RECORDS_ROOT),
                    help="where the verdict artefacts are written")
    ap.add_argument("--lock-file", default="",
                    help="lockfile path (default <repo>/.cambc/lab.lock)")
    ap.add_argument("--self-test", action="store_true",
                    help="run both sides on the SAME ref and assert the "
                         "aggregates are byte-identical")
    args = ap.parse_args(argv)
    unbuffer_stdout()
    install_signal_handlers()

    record, code = run_lab(args)
    out_dir = write_outputs(record, Path(args.records_dir))
    verdict = (record.get("verdict") or {}).get("verdict", "error")
    info(f"verdict: {verdict} (exit {code})")
    info(f"artefacts: {out_dir}")
    if record.get("elapsed_minutes") is not None:
        info(f"wall clock: {record['elapsed_minutes']} min")
    return code


if __name__ == "__main__":
    sys.exit(main())

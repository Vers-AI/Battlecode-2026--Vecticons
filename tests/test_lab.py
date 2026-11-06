#!/usr/bin/env python3
"""Unit tests for the lab's verdict logic (scripts/lab.py).

Pure functions only: the verdict computation, the noise band, the
aggregate re-summary and the lockfile parsing. Synthetic aggregates are
built here; the one real fixture is the F3 campaign record in
``records/f3-campaign/aggregate.json``, which proves the lab's
re-summary of the per-map rows reproduces the harness's own totals.

    python3 -m pytest tests/test_lab.py -q

The tests also run under ``python3 -m unittest`` (they are plain
unittest cases) so the lab needs no test dependency at all.
"""
import datetime as dt
import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_lab():
    """Load scripts/lab.py as a module (it is a script, not a package)."""
    spec = importlib.util.spec_from_file_location("lab", ROOT / "scripts" / "lab.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules["lab"] = module
    spec.loader.exec_module(module)
    return module


lab = _load_lab()


def make_aggregate(label, wins, maps=10, faults=0, opp_faults=0,
                   gems=1000, fuel=100, kills=10, failed=()):
    """A synthetic campaign aggregate in the harness's shape.

    *wins* is the number of maps won; *gems*/*fuel*/*kills* are PER MAP
    (the harness's own units), so a campaign total is the per-map value
    times the number of completed maps.
    """
    wins = min(wins, maps)
    rows = []
    for index in range(maps):
        name = f"m{index:03d}"
        if name in failed:
            rows.append({"map": name, "status": "exit:1", "seconds": 1.0})
            continue
        rows.append({
            "map": name,
            "status": "ok",
            "win": index < wins,
            "gems": gems,
            "opp_gems": 500,
            "camps": 1,
            "deaths": 0,
            "kills": kills,
            "opp_kills": 1,
            "fuel_exhausted": fuel,
            "wards": 0,
            "buys": 0,
            "consumables": 0,
            "emblems": 0,
            "bot_faults": faults if index == 0 else 0,
            "opp_bot_faults": opp_faults if index == 0 else 0,
            "rounds": 100,
            "seconds": 1.0 + index,
        })
    aggregate = {"label": label, "rows": rows, "elapsed_minutes": 1.0}
    aggregate.update(lab.summarize_rows(rows))
    aggregate["matches"] = len(rows)
    return aggregate


def gate(verdict, name):
    """The named gate of a verdict."""
    for item in verdict["gates"]:
        if item["name"] == name:
            return item
    raise AssertionError(f"no gate named {name!r} in {verdict['gates']}")


class NoiseBandTests(unittest.TestCase):
    """The band is EMPIRICAL: ceil(pool_size * 1/70)."""

    def test_tolerance_is_one_win_per_seventy_maps(self):
        self.assertEqual(lab.noise_band_tolerance(70), 1)
        self.assertEqual(lab.noise_band_tolerance(35), 1)
        self.assertEqual(lab.noise_band_tolerance(1), 1)
        self.assertEqual(lab.noise_band_tolerance(0), 0)

    def test_tolerance_scales_with_the_pool(self):
        # The full 128-map pool: ceil(128/70) = 2.
        self.assertEqual(lab.noise_band_tolerance(128), 2)
        self.assertEqual(lab.noise_band_tolerance(140), 2)
        self.assertEqual(lab.noise_band_tolerance(141), 3)

    def test_rule_states_the_pool_and_the_tolerance(self):
        rule = lab.noise_band_rule(128)
        self.assertIn("128", rule)
        self.assertIn("2", rule)
        self.assertIn("1/70", rule)


class RecordBandTests(unittest.TestCase):
    """The PRIMARY gate — the candidate record against the baseline."""

    def test_equal_records_are_inside_the_band(self):
        result = lab.evaluate_record_band(58, 58, 2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["delta"], 0)
        self.assertTrue(result["within_band"])

    def test_a_candidate_that_is_better_passes(self):
        result = lab.evaluate_record_band(60, 58, 2)
        self.assertEqual(result["status"], "pass")
        self.assertEqual(result["delta"], 2)

    def test_an_improvement_beyond_the_band_is_an_anomaly_but_passes(self):
        # The band absorbs noise, it does not punish progress.
        result = lab.evaluate_record_band(65, 58, 2)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["improvement_anomaly"])

    def test_worse_by_exactly_the_band_is_inside_the_band(self):
        result = lab.evaluate_record_band(56, 58, 2)
        self.assertEqual(result["status"], "pass")
        self.assertTrue(result["within_band"])

    def test_worse_beyond_the_band_fails(self):
        result = lab.evaluate_record_band(55, 58, 2)
        self.assertEqual(result["status"], "fail")
        self.assertTrue(result["worse_beyond_band"])


class VerdictTests(unittest.TestCase):
    """The whole verdict, gate by gate."""

    def test_equal_records_pass(self):
        candidate = make_aggregate("c", 58, maps=70)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(gate(verdict, "record_band")["status"], "pass")
        self.assertEqual(gate(verdict, "bot_faults")["status"], "pass")
        self.assertEqual(gate(verdict, "candidate_complete")["status"], "pass")

    def test_candidate_better_passes(self):
        candidate = make_aggregate("c", 60, maps=70)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(verdict["noise_band"]["delta"], 2)

    def test_candidate_worse_by_exactly_the_band_passes(self):
        candidate = make_aggregate("c", 57, maps=70)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(verdict["noise_band"]["delta"], -1)

    def test_candidate_worse_beyond_the_band_fails(self):
        candidate = make_aggregate("c", 56, maps=70)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "record_band")["status"], "fail")

    def test_the_128_map_pool_has_a_two_win_band(self):
        candidate = make_aggregate("c", 100, maps=128)
        baseline = make_aggregate("b", 100, maps=128)
        verdict = lab.evaluate_verdict(candidate, baseline, 128)
        self.assertEqual(verdict["noise_band"]["tolerance"], 2)
        # One win worse: inside the band. Two worse: still inside.
        # Three worse: outside.
        candidate = make_aggregate("c", 99, maps=128)
        self.assertEqual(
            lab.evaluate_verdict(candidate, baseline, 128)["verdict"], "pass")
        candidate = make_aggregate("c", 98, maps=128)
        self.assertEqual(
            lab.evaluate_verdict(candidate, baseline, 128)["verdict"], "pass")
        candidate = make_aggregate("c", 97, maps=128)
        self.assertEqual(
            lab.evaluate_verdict(candidate, baseline, 128)["verdict"], "fail")

    def test_faults_on_the_candidate_fail_the_hard_gate(self):
        candidate = make_aggregate("c", 58, maps=70, faults=1)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "bot_faults")["status"], "fail")
        self.assertEqual(gate(verdict, "bot_faults")["counts"]
                         ["candidate_ours"], 1)

    def test_faults_on_the_opponent_fail_the_hard_gate(self):
        candidate = make_aggregate("c", 58, maps=70, opp_faults=2)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "bot_faults")["counts"]
                         ["candidate_opponent"], 2)

    def test_faults_on_the_baseline_fail_the_hard_gate(self):
        candidate = make_aggregate("c", 58, maps=70)
        baseline = make_aggregate("b", 58, maps=70, faults=1)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "bot_faults")["counts"]
                         ["baseline_ours"], 1)

    def test_a_missing_map_row_fails_the_completeness_gate(self):
        candidate = make_aggregate("c", 58, maps=70, failed=("m003",))
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "candidate_complete")["status"],
                         "fail")
        self.assertIn("m003", gate(verdict, "candidate_complete")
                      ["missing_maps"])

    def test_an_empty_candidate_fails(self):
        candidate = make_aggregate("c", 0, maps=0)
        baseline = make_aggregate("b", 58, maps=70)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "fail")
        self.assertEqual(gate(verdict, "candidate_complete")["status"],
                         "fail")

    def test_gems_fuel_and_kills_are_reported_but_never_gated(self):
        # The F6 lesson: a build with the feature REMOVED lost 233 gems.
        candidate = make_aggregate("c", 58, maps=70, gems=1000, fuel=100,
                                   kills=10)
        baseline = make_aggregate("b", 58, maps=70, gems=1000, fuel=100,
                                  kills=10)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(verdict["reported"]["gems"]["delta"], 0)
        self.assertFalse(verdict["reported"]["gems"]["gated"])
        self.assertFalse(verdict["reported"]["fuel"]["gated"])
        self.assertFalse(verdict["reported"]["kills"]["gated"])
        # The gems moved by hundreds; the verdict did not move at all.
        worse = make_aggregate("c", 58, maps=70, gems=997, fuel=100,
                               kills=10)
        verdict = lab.evaluate_verdict(worse, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(verdict["reported"]["gems"]["delta"], -210)

    def test_a_huge_gems_difference_still_passes_the_gates(self):
        candidate = make_aggregate("c", 58, maps=70, gems=1)
        baseline = make_aggregate("b", 58, maps=70, gems=999999)
        verdict = lab.evaluate_verdict(candidate, baseline, 70)
        self.assertEqual(verdict["verdict"], "pass")
        self.assertEqual(verdict["reported"]["gems"]["delta"],
                         70 * (1 - 999999))

    def test_the_verdict_states_the_active_band(self):
        candidate = make_aggregate("c", 58, maps=128)
        baseline = make_aggregate("b", 58, maps=128)
        verdict = lab.evaluate_verdict(candidate, baseline, 128)
        band = verdict["noise_band"]
        self.assertEqual(band["tolerance"], 2)
        self.assertEqual(band["pool_size"], 128)
        self.assertIn("1/70", band["rule"])
        self.assertIn("records/f6-campaign/ablation.md", band["evidence"])

    def test_exit_codes_are_distinguishable(self):
        self.assertEqual(lab.exit_code_for("pass"), 0)
        self.assertEqual(lab.exit_code_for("fail"), 1)
        self.assertEqual(lab.exit_code_for("error"), 2)
        self.assertEqual(lab.exit_code_for("anything else"), 2)


class AggregateTests(unittest.TestCase):
    """The aggregate re-summary and the canonical (byte-comparable) form."""

    def test_summarize_rows_counts_only_completed_maps(self):
        aggregate = make_aggregate("c", 6, maps=10, failed=("m009",))
        summary = lab.summarize_rows(aggregate["rows"])
        self.assertEqual(summary["matches"], 10)
        self.assertEqual(summary["completed"], 9)
        self.assertEqual(summary["wins"], 6)
        self.assertEqual(summary["losses"], 3)

    def test_the_resummary_reproduces_the_real_f3_campaign_record(self):
        fixture = ROOT / "records" / "f3-campaign" / "aggregate.json"
        if not fixture.exists():
            self.skipTest("the F3 campaign record is not in this checkout")
        aggregate = json.loads(fixture.read_text())
        self.assertEqual(lab.resummary_mismatches(aggregate), [])
        summary = lab.summarize_rows(aggregate["rows"])
        self.assertEqual(summary["wins"], aggregate["wins"])
        self.assertEqual(summary["losses"], aggregate["losses"])
        self.assertEqual(summary["total_gems"], aggregate["total_gems"])
        self.assertEqual(summary["bot_faults"], 0)

    def test_canonical_aggregate_ignores_the_label_and_the_wall_clock(self):
        candidate = make_aggregate("candidate", 5, maps=10)
        baseline = make_aggregate("baseline", 5, maps=10)
        # The label and the wall clock are metadata, not results: the two
        # campaigns of the same binary canonicalize to the same bytes.
        self.assertEqual(lab.canonical_aggregate(candidate),
                         lab.canonical_aggregate(baseline))
        twin = json.loads(json.dumps(candidate))
        twin["label"] = "baseline"
        twin["elapsed_minutes"] = 99.0
        for row in twin["rows"]:
            row["seconds"] = 12345.0
        self.assertEqual(lab.canonical_aggregate(candidate),
                         lab.canonical_aggregate(twin))

    def test_canonical_aggregate_catches_a_real_difference(self):
        candidate = make_aggregate("candidate", 58, maps=10)
        other = json.loads(json.dumps(candidate))
        other["rows"][0]["gems"] += 1
        self.assertNotEqual(lab.canonical_aggregate(candidate),
                            lab.canonical_aggregate(other))

    def test_merge_rows_replaces_the_failed_rows(self):
        base = make_aggregate("c", 9, maps=10, failed=("m009",))["rows"]
        retry = [{"map": "m009", "status": "ok", "win": True, "gems": 10,
                  "fuel_exhausted": 0, "kills": 0, "opp_kills": 0,
                  "bot_faults": 0, "opp_bot_faults": 0, "wards": 0,
                  "buys": 0, "consumables": 0, "emblems": 0, "rounds": 1,
                  "seconds": 1.0}]
        merged = lab.merge_rows(base, retry)
        self.assertEqual(len(merged), 10)
        self.assertEqual(lab.failed_maps({"rows": merged}), [])
        self.assertEqual(lab.summarize_rows(merged)["wins"], 10)


class LockTests(unittest.TestCase):
    """The lockfile: parse it, and decide whether it may be reclaimed."""

    def test_parse_the_json_form(self):
        payload = lab.parse_lock_payload(json.dumps(
            {"pid": 1234, "started_at": "2026-11-06T10:00:00+00:00",
             "label": "lab-x"}))
        self.assertEqual(payload["pid"], 1234)
        self.assertEqual(payload["label"], "lab-x")

    def test_parse_the_flat_form(self):
        payload = lab.parse_lock_payload("pid=99 started=2026-11-06T10:00:00")
        self.assertEqual(payload["pid"], "99")

    def test_parse_an_empty_lockfile(self):
        self.assertEqual(lab.parse_lock_payload(""), {})

    def test_a_dead_pid_is_stale(self):
        payload = {"pid": 999999999, "started_at": lab.utcnow_iso()}
        stale, reason = lab.lock_is_stale(payload)
        self.assertTrue(stale)
        self.assertIn("dead", reason)

    def test_a_lock_older_than_two_hours_is_stale(self):
        old = (lab.utcnow() - dt.timedelta(hours=3)).isoformat()
        payload = {"pid": lab.os.getpid(), "started_at": old}
        stale, reason = lab.lock_is_stale(payload)
        self.assertTrue(stale)
        self.assertIn("old", reason)

    def test_a_fresh_lock_of_a_live_pid_is_not_stale(self):
        payload = {"pid": lab.os.getpid(), "started_at": lab.utcnow_iso()}
        stale, _ = lab.lock_is_stale(payload)
        self.assertFalse(stale)

    def test_a_lock_without_a_pid_is_stale(self):
        stale, _ = lab.lock_is_stale({})
        self.assertTrue(stale)


class SelectionTests(unittest.TestCase):
    """The map selection helpers."""

    def test_spread_sample_is_deterministic_and_spread(self):
        maps = [f"m{index:03d}" for index in range(128)]
        first = lab.spread_sample(maps, 5)
        second = lab.spread_sample(maps, 5)
        self.assertEqual(first, second)
        self.assertEqual(len(first), 5)
        self.assertNotEqual(first[0], first[-1])

    def test_spread_sample_of_a_small_pool(self):
        maps = ["a", "b", "c"]
        self.assertEqual(lab.spread_sample(maps, 5), maps)
        self.assertEqual(lab.spread_sample(maps, 0), [])


if __name__ == "__main__":
    unittest.main()

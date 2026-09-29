try:  # first: no test may read real credentials or run `gh auth token` (tests/_isolation.py)
    from . import _isolation  # noqa: F401
except ImportError:  # `unittest discover -s tests` imports test modules without their package
    import _isolation  # noqa: F401
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import runstate  # noqa: E402

NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)


def run(state, at, slot="slot-dev", rid=None):
    return {"id": rid or at, "slot": slot, "state": state, "at": at, "comment_id": 1}


class DecideTest(unittest.TestCase):
    def test_proceeds_on_clean_history(self):
        runs = [run("finished", "2026-09-25T20:00:00Z")]
        self.assertEqual(runstate.decide(runs, "slot-dev", NOW, False)["decision"], "proceed")

    def test_paused_label_stops_work(self):
        self.assertEqual(runstate.decide([], "slot-dev", NOW, True)["decision"], "paused")

    def test_recent_started_run_of_same_slot_is_overlap(self):
        runs = [run("started", "2026-09-26T11:00:00Z")]
        self.assertEqual(runstate.decide(runs, "slot-dev", NOW, False)["decision"], "overlap")

    def test_recent_started_run_of_other_slot_does_not_block(self):
        runs = [run("started", "2026-09-26T11:00:00Z", slot="slot-pm")]
        self.assertEqual(runstate.decide(runs, "slot-dev", NOW, False)["decision"], "proceed")

    def test_stale_started_run_counts_as_failed(self):
        r = run("started", "2026-09-26T08:00:00Z")
        self.assertEqual(runstate.effective_state(r, NOW), "failed")

    def test_three_failures_in_a_row_pause(self):
        runs = [
            run("finished", "2026-09-24T20:00:00Z"),
            run("failed", "2026-09-25T01:00:00Z"),
            run("failed", "2026-09-25T15:00:00Z"),
            run("started", "2026-09-25T20:00:00Z"),  # died on the usage limit
        ]
        self.assertEqual(runstate.decide(runs, "slot-dev", NOW, False)["decision"], "pause")

    def test_success_between_failures_resets_the_streak(self):
        runs = [
            run("failed", "2026-09-25T01:00:00Z"),
            run("finished", "2026-09-25T15:00:00Z"),
            run("failed", "2026-09-25T20:00:00Z"),
        ]
        self.assertEqual(runstate.decide(runs, "slot-dev", NOW, False)["decision"], "proceed")

    def test_failures_before_resume_do_not_count(self):
        runs = [run("failed", f"2026-09-25T0{h}:00:00Z") for h in (1, 2, 3)]
        decision = runstate.decide(runs, "slot-dev", NOW, False, reset_at="2026-09-25T09:00:00Z")
        self.assertEqual(decision["decision"], "proceed")


class ParseRunsTest(unittest.TestCase):
    def test_reads_markers_and_ignores_other_comments(self):
        comments = [
            {"id": 2, "created_at": "2026-09-26T01:00:00Z", "body": "<!-- pt-run id=b slot=slot-qa state=finished -->\nok"},
            {"id": 1, "created_at": "2026-09-25T20:00:00Z", "body": "<!-- pt-run id=a slot=slot-dev state=failed -->"},
            {"id": 3, "created_at": "2026-09-26T02:00:00Z", "body": "owner chatter"},
        ]
        self.assertEqual([r["id"] for r in runstate.parse_runs(comments)], ["a", "b"])


class OwnerPauseTest(unittest.TestCase):
    def comment(self, body, at):
        return {"body": body, "created_at": at}

    def test_latest_unresumed_pause_is_active(self):
        comments = [self.comment(runstate.owner_pause_marker({"routines": [{"id": "trig_1"}]}), "2026-10-01T10:00:00Z")]
        record = runstate.active_owner_pause(comments)
        self.assertEqual(record["routines"], [{"id": "trig_1"}])
        self.assertEqual(record["paused_at"], "2026-10-01T10:00:00Z")

    def test_resume_after_pause_clears_it(self):
        comments = [self.comment(runstate.owner_pause_marker({"routines": []}), "2026-10-01T10:00:00Z"),
                    self.comment(runstate.OWNER_RESUME, "2026-10-05T10:00:00Z")]
        self.assertIsNone(runstate.active_owner_pause(comments))

    def test_new_pause_after_resume_is_active_again(self):
        comments = [self.comment(runstate.owner_pause_marker({"n": 1}), "2026-10-01T10:00:00Z"),
                    self.comment(runstate.OWNER_RESUME, "2026-10-05T10:00:00Z"),
                    self.comment(runstate.owner_pause_marker({"n": 2}), "2026-10-09T10:00:00Z")]
        self.assertEqual(runstate.active_owner_pause(comments)["n"], 2)

    def test_malformed_record_is_ignored(self):
        self.assertIsNone(runstate.active_owner_pause([self.comment("<!-- pt-owner-pause {bad} -->", "2026-10-01T10:00:00Z")]))


class ResumeTest(unittest.TestCase):
    def test_resume_after_pause_lifts_it(self):
        cmds = [{"command": "resume", "at": "2026-09-26T10:00:00Z"}]
        self.assertTrue(runstate.resumed_after_pause("2026-09-26T09:00:00Z", cmds))

    def test_resume_before_pause_does_not(self):
        cmds = [{"command": "resume", "at": "2026-09-26T08:00:00Z"}]
        self.assertFalse(runstate.resumed_after_pause("2026-09-26T09:00:00Z", cmds))


class MetricsTest(unittest.TestCase):
    def test_run_id_carries_its_start_time(self):
        self.assertEqual(runstate.started_at("20260926T201300Z-slot-dev"), datetime(2026, 9, 26, 20, 13, tzinfo=timezone.utc))

    def test_metrics_parse_numbers_and_text(self):
        self.assertEqual(runstate.parse_metrics(["prs=2", "note=cap hit"]), {"prs": 2, "note": "cap hit"})

    def test_malformed_metric_is_rejected(self):
        with self.assertRaises(ValueError):
            runstate.parse_metrics(["prs"])

    def test_metrics_round_trip_through_the_comment(self):
        body = "<!-- pt-run id=a slot=slot-dev state=finished -->\n" + runstate.metrics_marker({"minutes": 42, "prs": 2})
        run = runstate.parse_runs([{"id": 1, "created_at": "2026-09-26T20:00:00Z", "body": body}])[0]
        self.assertEqual(run["metrics"], {"minutes": 42, "prs": 2})

    def test_malformed_metrics_block_is_ignored(self):
        body = "<!-- pt-run id=a slot=slot-dev state=finished -->\n<!-- pt-metrics {bad} -->"
        self.assertEqual(runstate.parse_runs([{"id": 1, "created_at": "2026-09-26T20:00:00Z", "body": body}])[0]["metrics"], {})

    def test_metric_cannot_close_the_marker(self):
        with self.assertRaises(ValueError):
            runstate.parse_metrics(["note=x} -->"])

    def test_stats_per_slot(self):
        runs = [
            dict(run("finished", "2026-09-25T20:00:00Z"), metrics={"minutes": 40, "prs": 2}),
            dict(run("finished", "2026-09-24T20:00:00Z"), metrics={"minutes": 60, "prs": 1}),
            dict(run("started", "2026-09-23T20:00:00Z"), metrics={}),
            dict(run("finished", "2026-08-01T20:00:00Z"), metrics={"minutes": 5, "prs": 9}),
        ]
        since = datetime(2026, 9, 12, tzinfo=timezone.utc)
        self.assertEqual(runstate.stats(runs, NOW, since),
                         {"slot-dev": {"runs": 3, "failed": 1, "totals": {"prs": 3}, "median_minutes": 50}})


if __name__ == "__main__":
    unittest.main()

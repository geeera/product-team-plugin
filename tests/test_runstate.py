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


class ResumeTest(unittest.TestCase):
    def test_resume_after_pause_lifts_it(self):
        cmds = [{"command": "resume", "at": "2026-09-26T10:00:00Z"}]
        self.assertTrue(runstate.resumed_after_pause("2026-09-26T09:00:00Z", cmds))

    def test_resume_before_pause_does_not(self):
        cmds = [{"command": "resume", "at": "2026-09-26T08:00:00Z"}]
        self.assertFalse(runstate.resumed_after_pause("2026-09-26T09:00:00Z", cmds))


if __name__ == "__main__":
    unittest.main()

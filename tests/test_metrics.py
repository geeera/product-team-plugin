import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import metrics  # noqa: E402


def labeled(name, at):
    return {"event": "labeled", "label": {"name": name}, "created_at": at}


class CycleTest(unittest.TestCase):
    def test_measured_from_first_in_progress_to_close(self):
        events = [labeled("status:approved", "2026-09-01T00:00:00Z"), labeled("status:in-progress", "2026-09-02T00:00:00Z"),
                  labeled("status:in-progress", "2026-09-03T00:00:00Z")]
        self.assertEqual(metrics.cycle_days("2026-09-04T12:00:00Z", events), 2.5)

    def test_unknown_without_start_or_close(self):
        self.assertIsNone(metrics.cycle_days("2026-09-04T00:00:00Z", []))
        self.assertIsNone(metrics.cycle_days(None, [labeled("status:in-progress", "2026-09-02T00:00:00Z")]))


class QaTest(unittest.TestCase):
    def test_first_pass_counts_only_approvals_without_earlier_requests(self):
        result = metrics.qa_verdicts({
            1: ["QA: APPROVED — all criteria met"],
            2: ["QA: CHANGES REQUESTED\n1. missing test", "QA: APPROVED"],
            3: ["looks fine"],
        })
        self.assertEqual(result, {"prs_reviewed": 2, "change_requests": 1, "first_pass_rate": 0.5})

    def test_no_reviews(self):
        self.assertIsNone(metrics.qa_verdicts({})["first_pass_rate"])


class SummaryTest(unittest.TestCase):
    def test_plan_versus_shipped_ignores_questions(self):
        issues = [
            {"number": 1, "state": "closed", "closed_at": "2026-09-04T00:00:00Z", "labels": ["kind:feature", "status:done"]},
            {"number": 2, "state": "open", "closed_at": None, "labels": ["kind:bug", "status:qa"]},
            {"number": 3, "state": "closed", "closed_at": "2026-09-04T00:00:00Z", "labels": ["kind:feature", "status:proposed"]},
            {"number": 4, "state": "open", "closed_at": None, "labels": ["kind:question"]},
        ]
        events = {1: [labeled("status:in-progress", "2026-09-02T00:00:00Z")]}
        summary = metrics.sprint_summary(issues, events, {})
        self.assertEqual((summary["planned"], summary["shipped"], summary["carried_over"], summary["median_cycle_days"]),
                         (3, 1, 1, 2.0))


class TierMetricsTest(unittest.TestCase):
    def test_counts_per_tier(self):
        issues = [
            {"number": 1, "state": "closed", "closed_at": "2026-09-04T00:00:00Z", "labels": ["kind:feature", "status:done", "tier:light"]},
            {"number": 2, "state": "open", "closed_at": None, "labels": ["kind:bug", "tier:light", "tier-up"]},
            {"number": 3, "state": "open", "closed_at": None, "labels": ["kind:chore"]},
        ]
        by_tier = metrics.sprint_summary(issues, {}, {})["by_tier"]
        self.assertEqual(by_tier["light"], {"planned": 2, "shipped": 1, "raised": 1})
        self.assertEqual(by_tier["unsized"], {"planned": 1, "shipped": 0, "raised": 0})


if __name__ == "__main__":
    unittest.main()

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib import picker  # noqa: E402

NORMAL = {"mode": "normal", "is_burn": False, "caps": {"dev_tasks": 2, "parallel_devs": 2}}
BURN = {"mode": "burn", "is_burn": True, "caps": {"dev_tasks": 5, "parallel_devs": 3}}
FREEZE = {"mode": "freeze", "is_burn": False, "caps": {"dev_tasks": 0, "parallel_devs": 1}}
S = "Sprint 01"


def issue(n, kind="feature", status="approved", labels=(), milestone=S):
    return {"number": n, "title": f"#{n}", "kind": kind, "status": status, "state": "open",
            "labels": [f"kind:{kind}", f"status:{status}", *labels], "milestone": milestone}


def numbers(plan):
    return [d["number"] for d in plan["dispatch"]]


class PickTest(unittest.TestCase):
    def test_planned_work_respects_the_cap_in_issue_order(self):
        plan = picker.pick([issue(3), issue(1), issue(2)], S, NORMAL)
        self.assertEqual(numbers(plan), [1, 2])
        self.assertIn({"number": 3, "reason": "over the cap for this run"}, plan["skipped"])

    def test_p0_p1_bugs_and_release_blockers_bypass_the_cap_first(self):
        issues = [issue(1), issue(2), issue(9, "bug", labels=["sev:high"]),
                  issue(8, "finding", labels=["sev:high", "release-blocker"]), issue(7, "bug", labels=["sev:critical"])]
        self.assertEqual(numbers(picker.pick(issues, S, NORMAL)), [7, 8, 9, 1, 2])

    def test_high_finding_without_blocker_is_planned_work(self):
        plan = picker.pick([issue(5, "finding", labels=["sev:high"])], S, NORMAL)
        self.assertEqual(plan["dispatch"][0]["reason"], "planned")

    def test_questions_are_never_dispatched(self):
        self.assertEqual(numbers(picker.pick([issue(4, "question")], S, NORMAL)), [])

    def test_production_defect_comes_first_on_the_hotfix_path(self):
        plan = picker.pick([issue(2, "bug", labels=["sev:critical"]),
                            issue(6, "bug", labels=["sev:high", "in-production"])], S, NORMAL)
        self.assertEqual(numbers(plan), [6, 2])
        self.assertEqual((plan["dispatch"][0]["base"], plan["dispatch"][0]["branch_prefix"]), ("main", "hotfix"))

    def test_qa_rework_comes_before_new_planned_work(self):
        plan = picker.pick([issue(1), issue(4, status="in-progress", labels=["qa:changes-requested"])], S, NORMAL)
        self.assertEqual(numbers(plan), [4, 1])

    def test_in_progress_without_verdict_is_left_alone(self):
        self.assertEqual(numbers(picker.pick([issue(4, status="in-progress")], S, NORMAL)), [])

    def test_owner_and_local_work_is_skipped_with_a_reason(self):
        plan = picker.pick([issue(1, labels=["needs:local"]), issue(2, "bug", labels=["sev:critical", "needs:owner"])], S, NORMAL)
        self.assertEqual(numbers(plan), [])
        self.assertEqual({s["reason"] for s in plan["skipped"]}, {"needs a local machine", "waits for the owner"})

    def test_unapproved_design_and_missing_architect_note_are_skipped(self):
        plan = picker.pick([issue(1, labels=["needs-design"]), issue(2, labels=["complexity:high"]),
                            issue(3, labels=["needs-design", "design:approved"])], S, NORMAL)
        self.assertEqual(numbers(plan), [3])
        self.assertEqual(plan["needs_architect_note"], [2])

    def test_other_sprints_are_not_touched(self):
        self.assertEqual(numbers(picker.pick([issue(1, milestone="Sprint 02"), issue(2, milestone=None)], S, NORMAL)), [])

    def test_freeze_takes_only_fixes_and_targets_stage(self):
        plan = picker.pick([issue(1), issue(2, "bug", labels=["sev:high"])], S, FREEZE)
        self.assertEqual(numbers(plan), [2])
        self.assertEqual((plan["dispatch"][0]["base"], plan["dispatch"][0]["branch_prefix"]), ("stage", "fix"))

    def test_burn_takes_more_work(self):
        issues = [issue(n) for n in range(1, 8)]
        self.assertEqual(numbers(picker.pick(issues, S, BURN)), [1, 2, 3, 4, 5])

    def test_agent_label_routes_to_a_project_agent(self):
        plan = picker.pick([issue(1, labels=["agent:flutter-dev"]), issue(2)], S, NORMAL)
        self.assertEqual([d["agent"] for d in plan["dispatch"]], ["flutter-dev", "fullstack-dev"])

    def test_several_agent_labels_are_flagged(self):
        plan = picker.pick([issue(1, labels=["agent:a-dev", "agent:b-dev"])], S, NORMAL)
        self.assertEqual(numbers(plan), [])
        self.assertIn("several agent labels", plan["skipped"][0]["reason"])

    def test_reviewing_roles_never_get_development(self):
        plan = picker.pick([issue(1, labels=["agent:qa"])], S, NORMAL)
        self.assertEqual(plan["skipped"][0]["reason"], "agent:qa cannot develop")

    def test_unknown_project_agent_is_flagged(self):
        plan = picker.pick([issue(1, labels=["agent:flutter-dev"])], S, NORMAL, known_agents={"fullstack-dev"})
        self.assertEqual(plan["skipped"][0]["reason"], "no .claude/agents/flutter-dev.md in this repository")

    def test_hotfix_keeps_its_specialist(self):
        plan = picker.pick([issue(6, "bug", labels=["sev:critical", "in-production", "agent:flutter-dev"])], S, NORMAL)
        self.assertEqual((plan["dispatch"][0]["agent"], plan["dispatch"][0]["branch_prefix"]), ("flutter-dev", "hotfix"))

    def test_complexity_high_with_note_is_planned(self):
        plan = picker.pick([issue(1, labels=["complexity:high", "architect-note"])], S, NORMAL)
        self.assertEqual(plan["dispatch"][0]["agent"], "fullstack-dev")


class ReviewRegressionTest(unittest.TestCase):
    def test_urgent_rework_bypasses_the_zero_freeze_cap(self):
        plan = picker.pick([issue(9, "bug", status="in-progress", labels=["sev:critical", "qa:changes-requested"])], S, FREEZE)
        self.assertEqual(numbers(plan), [9])
        self.assertEqual(plan["dispatch"][0]["reason"], "urgent rework (outside the cap)")

    def test_ordinary_rework_waits_during_a_freeze(self):
        plan = picker.pick([issue(4, status="in-progress", labels=["qa:changes-requested"])], S, FREEZE)
        self.assertEqual(numbers(plan), [])

    def test_rework_keeps_the_pr_base_instead_of_the_mode(self):
        entry = picker.pick([issue(4, status="in-progress", labels=["qa:changes-requested"])], S, NORMAL)["dispatch"][0]
        self.assertEqual((entry["base"], entry["branch_prefix"]), (None, None))

    def test_no_sprint_means_no_planned_work(self):
        self.assertEqual(numbers(picker.pick([issue(1, milestone=None)], None, NORMAL)), [])

    def test_non_urgent_production_defect_is_not_a_hotfix(self):
        plan = picker.pick([issue(3, "bug", labels=["sev:medium", "in-production"])], S, NORMAL)
        self.assertEqual((plan["dispatch"][0]["base"], plan["dispatch"][0]["branch_prefix"]), ("dev", "feature"))


if __name__ == "__main__":
    unittest.main()

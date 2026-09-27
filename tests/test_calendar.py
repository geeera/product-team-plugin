import sys
import unittest
from datetime import date, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from ptlib.calendar import KYIV, compute, is_burn, pick_current_sprint  # noqa: E402


def kyiv(y, mo, d, h, mi=0):
    return datetime(y, mo, d, h, mi, tzinfo=KYIV)


class BurnWindowTest(unittest.TestCase):
    # 2026-09-25 is a Friday.
    def test_friday_before_23_is_not_burn(self):
        self.assertFalse(is_burn(kyiv(2026, 9, 25, 22, 59)))

    def test_friday_23_starts_burn(self):
        self.assertTrue(is_burn(kyiv(2026, 9, 25, 23, 0)))

    def test_saturday_is_burn(self):
        self.assertTrue(is_burn(kyiv(2026, 9, 26, 4, 21)))

    def test_sunday_before_19_is_burn(self):
        self.assertTrue(is_burn(kyiv(2026, 9, 27, 18, 59)))

    def test_sunday_19_ends_burn(self):
        self.assertFalse(is_burn(kyiv(2026, 9, 27, 19, 0)))

    def test_monday_is_not_burn(self):
        self.assertFalse(is_burn(kyiv(2026, 9, 28, 23, 13)))


class ComputeTest(unittest.TestCase):
    def test_normal_weekday_uses_conservative_caps(self):
        ctx = compute(kyiv(2026, 9, 29, 23, 13))
        self.assertEqual(ctx.mode, "normal")
        self.assertEqual(ctx.caps["dev_tasks"], 2)
        self.assertEqual(ctx.caps["dev_agent"], "fullstack-dev")

    def test_cut_day_is_two_days_before_demo_and_starts_freeze(self):
        ctx = compute(kyiv(2026, 10, 7, 18, 7), "Sprint 01", date(2026, 10, 9))
        self.assertTrue(ctx.is_cut_day)
        self.assertEqual(ctx.mode, "freeze")
        self.assertEqual(ctx.caps["dev_tasks"], 0)

    def test_day_before_cut_is_not_freeze(self):
        ctx = compute(kyiv(2026, 10, 6, 23, 13), "Sprint 01", date(2026, 10, 9))
        self.assertFalse(ctx.is_freeze)

    def test_freeze_beats_burn(self):
        # Demo on Monday 2026-10-12 → freeze Sat–Mon, overlapping the burn window.
        ctx = compute(kyiv(2026, 10, 10, 10, 37), "Sprint 01", date(2026, 10, 12))
        self.assertTrue(ctx.is_burn)
        self.assertEqual(ctx.mode, "freeze")
        self.assertEqual(ctx.caps["dev_tasks"], 0)

    def test_utc_input_is_evaluated_in_kyiv_time(self):
        # 20:13 UTC on Friday = 23:13 Kyiv (UTC+3 in September) → burn.
        ctx = compute(datetime.fromisoformat("2026-09-25T20:13:00+00:00"))
        self.assertEqual(ctx.mode, "burn")

    def test_cap_override_applies_to_its_mode(self):
        ctx = compute(kyiv(2026, 9, 29, 23, 13), caps_override={"normal": {"dev_tasks": 1}})
        self.assertEqual(ctx.caps["dev_tasks"], 1)


class PickSprintTest(unittest.TestCase):
    def test_picks_earliest_open_milestone_due_today_or_later(self):
        milestones = [
            {"title": "Sprint 02", "state": "open", "due_on": "2026-10-23T12:00:00Z"},
            {"title": "Sprint 01", "state": "open", "due_on": "2026-10-09T12:00:00Z"},
            {"title": "Sprint 00", "state": "open", "due_on": "2026-09-20T12:00:00Z"},
            {"title": "Backlog", "state": "open", "due_on": None},
        ]
        self.assertEqual(pick_current_sprint(milestones, date(2026, 9, 26))["title"], "Sprint 01")

    def test_returns_none_without_dated_open_milestones(self):
        self.assertIsNone(pick_current_sprint([{"title": "x", "state": "closed", "due_on": "2026-10-09"}], date(2026, 9, 26)))


if __name__ == "__main__":
    unittest.main()

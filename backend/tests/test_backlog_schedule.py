"""Scheduling and recurrence rules, independent of the web server and database."""
import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backlog import normalize_backlog, complete_backlog


class BacklogSchedule(unittest.TestCase):
    def doc(self, task=None):
        return {"title": "Project", "tasks": [dict({"id": "task1", "title": "Work"}, **(task or {}))]}

    def test_optional_dates_and_invalid_input(self):
        clean = normalize_backlog(self.doc())
        self.assertEqual(clean["start"], "")
        self.assertEqual(clean["tasks"][0]["date_conflicts"], [])
        self.assertIsNone(clean["tasks"][0]["estimate"])
        for value in ["2026-02-30T10:00", "2026-11-07", "2026-11-07T24:00", "not a date", 42]:
            with self.assertRaises(ValueError):
                normalize_backlog(self.doc({"start": value}))

    def test_conflicts_are_tagged_and_allowed_at_every_level(self):
        doc = self.doc({"start": "2026-11-07T08:00", "deadline": "2026-11-07T07:00", "category": "cat1"})
        doc.update(start="2026-11-07T09:00", deadline="2026-11-07T18:00",
                   categories=[{"id": "cat1", "title": "A", "start": "2026-11-07T10:00", "deadline": "2026-11-07T20:00"}])
        clean = normalize_backlog(doc)
        self.assertEqual(clean["categories"][0]["date_conflicts"], ["outside_list"])
        self.assertEqual(clean["tasks"][0]["date_conflicts"], ["reversed_dates", "outside_list", "outside_category"])
        clean["tasks"][0].update(start="2026-11-07T11:00", deadline="2026-11-07T12:00")
        self.assertEqual(normalize_backlog(clean)["tasks"][0]["date_conflicts"], [])
        self.assertEqual(normalize_backlog({"title": "Reversed", "start": "2026-11-08T09:00", "deadline": "2026-11-07T18:00"})["date_conflicts"], ["reversed_dates"])

    def test_estimates_subtasks_and_repeat_validation(self):
        for extra in [{"estimate": 0}, {"estimate": True}, {"estimate": 2.5}, {"recurrence": {"unit": "year"}},
                      {"recurrence": {"unit": "day", "every": 0}}, {"recurrence": {"unit": "day", "every": True}},
                      {"subtasks": [{"id": "sub1", "title": ""}]},
                      {"subtasks": [{"id": "sub1", "title": "A"}, {"id": "sub1", "title": "B"}]}]:
            with self.assertRaises(ValueError):
                normalize_backlog(self.doc(extra))

    def test_completion_is_idempotent_and_resets_subtasks(self):
        doc = self.doc({"start": "2026-11-07T09:00", "deadline": "2026-11-07T10:00", "estimate": 30,
                        "recurrence": {"unit": "day", "every": 2}, "subtasks": [{"id": "sub1", "title": "Read", "done": True}]})
        now = dt.datetime(2026, 11, 7, 10)
        doc = complete_backlog(doc, "task1", True, now)
        self.assertEqual(len(doc["tasks"]), 2)
        child = doc["tasks"][1]
        self.assertEqual(child["start"], "2026-11-09T09:00")
        self.assertEqual(child["deadline"], "2026-11-09T10:00")
        self.assertEqual(child["estimate"], 30)
        self.assertFalse(child["subtasks"][0]["done"])
        self.assertEqual(doc["tasks"][0]["next_id"], child["id"])
        for done in [True, False, True]:
            doc = complete_backlog(doc, "task1", done, now)
            self.assertEqual(len(doc["tasks"]), 2)

    def test_month_end_keeps_original_day_and_custom_queue(self):
        doc = self.doc({"deadline": "2027-01-31T10:00", "recurrence": {"unit": "month", "every": 1}})
        doc.update(custom_order=True, queue=["task1"])
        doc = complete_backlog(doc, "task1", True, dt.datetime(2027, 1, 31, 11))
        child = doc["tasks"][1]
        self.assertEqual(child["deadline"], "2027-02-28T10:00")
        doc = complete_backlog(doc, child["id"], True, dt.datetime(2027, 2, 28, 11))
        self.assertEqual(doc["tasks"][2]["deadline"], "2027-03-31T10:00")
        self.assertEqual(len(doc["queue"]), 3)

    def test_skip_missed_occurrences_and_optional_dates(self):
        doc = self.doc({"deadline": "2020-01-01T10:00", "recurrence": {"unit": "week", "every": 1}})
        result = complete_backlog(doc, "task1", True, dt.datetime(2026, 11, 7, 11))
        self.assertEqual(len(result["tasks"]), 2)
        self.assertGreater(result["tasks"][1]["deadline"], "2026-11-07T11:00")
        result = complete_backlog(self.doc({"recurrence": {"unit": "day"}}), "task1", True, dt.datetime(2026, 11, 7, 11))
        self.assertEqual(result["tasks"][1]["deadline"], "")
        self.assertEqual(result["tasks"][1]["start"], "2026-11-08T11:00")
        with self.assertRaises(LookupError):
            complete_backlog(self.doc(), "missing", True, dt.datetime(2026, 11, 7))

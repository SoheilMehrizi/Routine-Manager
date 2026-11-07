import datetime as dt
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.backlog import normalize_backlog, complete_backlog
from app.backlog_reminders import normalize_policy, reminder_events, reminder_content

UTC = dt.timezone.utc


class BacklogReminderRules(unittest.TestCase):
    def prefs(self, **changes):
        return {"tz": "UTC", "backlog_reminders": {"enabled": True, "start_offsets": [0],
                "deadline_offsets": [0], "overdue_every": 0}, **changes}

    def doc(self, **task):
        return {"id": "list1111", "title": "Project", "tasks": [{"id": "task1111", "title": "Work", **task}]}

    def test_opt_in_and_strict_validation(self):
        self.assertFalse(normalize_policy(None, defaults=True)["enabled"])
        doc = self.doc(deadline="2026-11-07T09:00")
        now = dt.datetime(2026, 11, 7, 9, tzinfo=UTC)
        self.assertEqual(reminder_events(doc, {}, now), [])
        for bad in [{"enabled": "yes"}, {"start_offsets": [True]}, {"deadline_offsets": [-1]},
                    {"overdue_every": 14}, {"overdue_every": 1.5}, {"start_offsets": [0] * 13}]:
            with self.assertRaises(ValueError):
                normalize_policy(bad, defaults=True)
        self.assertEqual(normalize_policy({"mode": "custom", "deadline_offsets": [60, 0, 60]})["deadline_offsets"], [60, 0])

    def test_offsets_timezone_and_bounded_catchup(self):
        doc = self.doc(start="2026-11-08T09:00")
        prefs = self.prefs(tz="Asia/Tehran")
        prefs["backlog_reminders"]["start_offsets"] = [1440]
        events = reminder_events(doc, prefs, dt.datetime(2026, 11, 7, 5, 30, tzinfo=UTC))
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["scheduled_at"], dt.datetime(2026, 11, 7, 5, 30, tzinfo=UTC))
        self.assertEqual(reminder_events(doc, prefs, dt.datetime(2026, 11, 7, 8, 30, tzinfo=UTC)), [])
        self.assertEqual(reminder_events(doc, prefs, dt.datetime(2026, 11, 8, 6, tzinfo=UTC)), [])

    def test_parent_inheritance_and_explicit_child_override(self):
        doc = self.doc(deadline="2026-11-07T09:00", category="cat1111")
        doc.update(reminders={"mode": "off"}, categories=[{"id": "cat1111", "title": "A", "deadline": "2026-11-07T09:00"}])
        now = dt.datetime(2026, 11, 7, 9, tzinfo=UTC)
        self.assertEqual(reminder_events(doc, self.prefs(), now), [])
        doc["tasks"][0]["reminders"] = {"mode": "custom", "start_offsets": [], "deadline_offsets": [0]}
        events = reminder_events(doc, self.prefs(), now)
        self.assertEqual([e["scope"] for e in events], ["Task"])
        doc["reminders"] = None
        doc["categories"][0]["reminders"] = {"mode": "off"}
        self.assertEqual([e["scope"] for e in reminder_events(doc, self.prefs(), now)], ["Task"])

    def test_completed_scopes_and_archives_do_not_send(self):
        doc = self.doc(done=True, deadline="2026-11-07T09:00", category="cat1111")
        doc.update(deadline="2026-11-07T09:00", categories=[{"id": "cat1111", "title": "A", "deadline": "2026-11-07T09:00"}])
        now = dt.datetime(2026, 11, 7, 9, tzinfo=UTC)
        self.assertEqual(reminder_events(doc, self.prefs(), now), [])
        doc["tasks"] = []
        self.assertEqual(len(reminder_events(doc, self.prefs(), now)), 2)
        doc["archived"] = True
        self.assertEqual(reminder_events(doc, self.prefs(), now), [])

    def test_overdue_latest_slot_is_stable_and_not_backfilled(self):
        doc = self.doc(deadline="2026-11-07T09:00")
        prefs = self.prefs()
        prefs["backlog_reminders"].update(deadline_offsets=[], overdue_every=30)
        now = dt.datetime(2026, 11, 7, 10, 7, tzinfo=UTC)
        events = reminder_events(doc, prefs, now)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["event"], "overdue")
        self.assertEqual(events[0]["scheduled_at"].hour, 10)
        self.assertEqual(events[0]["key"], reminder_events(doc, prefs, now + dt.timedelta(minutes=1))[0]["key"])
        self.assertNotEqual(events[0]["key"], reminder_events(doc, prefs, now + dt.timedelta(minutes=24))[0]["key"])
        late = reminder_events(doc, prefs, now + dt.timedelta(days=60))
        self.assertEqual(len(late), 1)

    def test_dst_gap_and_fold_are_deterministic(self):
        prefs = self.prefs(tz="America/New_York")
        gap = reminder_events(self.doc(start="2026-03-08T02:30"), prefs, dt.datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        self.assertEqual(gap[0]["target"], dt.datetime(2026, 3, 8, 7, 30, tzinfo=UTC))
        fold = reminder_events(self.doc(start="2026-11-01T01:30"), prefs, dt.datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        again = reminder_events(self.doc(start="2026-11-01T01:30"), prefs, dt.datetime(2026, 11, 1, 6, 30, tzinfo=UTC))
        self.assertEqual(fold[0]["target"], dt.datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
        self.assertEqual(fold[0]["key"], again[0]["key"])

    def test_policy_survives_normalization_and_subject_is_safe(self):
        doc = self.doc(deadline="2026-11-07T09:00", reminders={"mode": "custom", "deadline_offsets": [0]})
        doc["tasks"][0]["title"] = "Title\r\nBcc: injected"
        clean = {**normalize_backlog(doc), "id": doc["id"]}
        self.assertEqual(clean["tasks"][0]["reminders"]["mode"], "custom")
        event = reminder_events(clean, self.prefs(), dt.datetime(2026, 11, 7, 9, tzinfo=UTC))[0]
        subject, body = reminder_content(event, "fa")
        self.assertNotIn("\n", subject)
        self.assertNotIn("\r", subject)
        self.assertIn("یادآوری", subject)
        self.assertIn("UTC", body)


    def test_recurring_successor_preserves_override(self):
        doc = self.doc(deadline="2026-11-07T09:00", recurrence={"unit": "day"},
                       reminders={"mode": "custom", "start_offsets": [], "deadline_offsets": [60], "overdue_every": 30})
        clean = complete_backlog(doc, "task1111", True, dt.datetime(2026, 11, 7, 10))
        self.assertEqual(clean["tasks"][1]["reminders"], clean["tasks"][0]["reminders"])

    def test_extreme_calendar_dates_do_not_crash_scheduler(self):
        doc = self.doc(start="0001-01-01T00:00")
        prefs = self.prefs(tz="Asia/Tehran")
        prefs["backlog_reminders"]["start_offsets"] = [525600]
        self.assertEqual(reminder_events(doc, prefs, dt.datetime(2026, 11, 7, tzinfo=UTC)), [])

"""Opt-in PostgreSQL/API integration tests. All mail uses an injected fake sender.

Run with RUN_BACKLOG_REMINDER_INTEGRATION=1 inside the Compose network.
Temporary accounts have future dates so the live scheduler cannot send their mail.
"""
import datetime as dt
import os
from pathlib import Path
import sys
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor


@unittest.skipUnless(os.environ.get("RUN_BACKLOG_REMINDER_INTEGRATION") == "1", "Opt-in live PostgreSQL/API check")
class BacklogReminderDeliveryTests(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
        import jwt
        import requests
        from sqlalchemy import delete, select
        from app import main as M
        self.M, self.select, self.delete, self.requests = M, select, delete, requests
        self.base = os.environ.get("BACKLOG_TEST_API_URL", "http://api:8000")
        self.ids, self.tokens = [], []
        self.now = dt.datetime(2040, 1, 1, 10, tzinfo=dt.timezone.utc)
        self.settings = {"enabled": True, "start_offsets": [], "deadline_offsets": [0], "overdue_every": 0}
        with M.SessionLocal() as s:
            for _ in range(2):
                user = M.User(google_sub="reminder-check-" + uuid.uuid4().hex,
                              email="reminder-test@example.invalid", name="Temporary reminder verification",
                              prefs={"tz": "UTC"})
                s.add(user)
                s.commit()
                self.ids.append(user.id)
                self.tokens.append(jwt.encode({"sub": str(user.id), "exp": dt.datetime.now(dt.timezone.utc) + dt.timedelta(minutes=10)},
                                              M.JWT_SECRET, algorithm="HS256"))
        self.a, self.b = self.tokens
        self.call("PUT", "/api/me/prefs", self.a, {"tz": "UTC", "backlog_reminders": self.settings})
        self.lists = self.call("GET", "/api/backlogs", self.a)
        self.doc = self.lists[0]

    @classmethod
    def tearDownClass(cls):
        from app import main as M
        M.engine.dispose()

    def tearDown(self):
        with self.M.SessionLocal() as s:
            s.execute(self.delete(self.M.User).where(self.M.User.id.in_(self.ids)))
            s.commit()

    def call(self, method, path, token=None, data=None, status=200):
        r = self.requests.request(method, self.base + path,
            headers={"Authorization": "Bearer " + token} if token else {}, json=data, timeout=15)
        self.assertEqual(r.status_code, status, (method, path, r.status_code))
        return r.json() if r.content else None

    def task(self, **extra):
        self.doc["tasks"] = [{"id": "task1111", "title": "Future reminder", "deadline": "2040-01-01T10:00", **extra}]
        self.doc = self.call("POST", "/api/backlogs/batch", self.a, {"items": [self.doc]})[0]

    def run_tick(self, sender, seconds=0):
        self.M.run_due_backlog_reminders(user_id=self.ids[0], now=self.now + dt.timedelta(seconds=seconds), sender=sender)

    def test_settings_validation_preservation_and_history_isolation(self):
        self.call("GET", "/api/backlog-reminders/status", status=401)
        self.call("PUT", "/api/me/prefs", self.a, {"backlog_reminders": {"enabled": True, "overdue_every": 1}}, status=422)
        prefs = self.call("PUT", "/api/me/prefs", self.a, {"tz": "UTC", "theme": "dark"})
        self.assertEqual(prefs["backlog_reminders"], self.settings)
        self.assertFalse(self.call("GET", "/api/backlog-reminders/status", self.b)["defaults"]["enabled"])
        self.task(reminders={"mode": "custom", "start_offsets": [], "deadline_offsets": [0]})
        self.doc["deadline"] = "2040-01-01T10:00"
        self.doc["categories"] = [{"id": "cat1111", "title": "Future category", "deadline": "2040-01-01T10:00"}]
        self.doc["tasks"][0]["category"] = "cat1111"
        self.doc = self.call("POST", "/api/backlogs/batch", self.a, {"items": [self.doc]})[0]
        sent = []
        self.run_tick(lambda *args: sent.append(args))
        self.assertEqual(len(sent), 3)
        self.assertEqual(sent[0][0], "reminder-test@example.invalid")
        status = self.call("GET", "/api/backlog-reminders/status", self.a)
        self.assertEqual(status["deliveries"][0]["status"], "sent")
        self.assertEqual(self.call("GET", "/api/backlog-reminders/status", self.b)["deliveries"], [])

    def test_concurrent_ticks_claim_only_one_send(self):
        self.task()
        sent = []
        with ThreadPoolExecutor(max_workers=2) as pool:
            list(pool.map(lambda _: self.run_tick(lambda *args: sent.append(args)), range(2)))
        self.run_tick(lambda *args: sent.append(args), 61)
        self.assertEqual(len(sent), 1)
        self.assertEqual(self.call("GET", "/api/backlog-reminders/status", self.a)["deliveries"][0]["attempts"], 1)

    def test_known_failures_retry_at_most_five_times(self):
        self.task()
        calls = []
        def fail(*args):
            calls.append(args)
            raise self.M.MailDeliveryError("Controlled test rejection")
        for i in range(8):
            self.run_tick(fail, i * 61)
        self.assertEqual(len(calls), 5)
        delivery = self.call("GET", "/api/backlog-reminders/status", self.a)["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("failed", 5))
        self.doc["tasks"] = [{"id": "task2222", "title": "Recovery", "deadline": "2040-01-01T10:00"}]
        self.doc = self.call("POST", "/api/backlogs/batch", self.a, {"items": [self.doc]})[0]
        recovered = []
        def recover(*args):
            recovered.append(args)
            if len(recovered) < 3:
                raise self.M.MailDeliveryError("Controlled temporary rejection")
        for seconds in [600, 661, 722, 783]:
            self.run_tick(recover, seconds)
        self.assertEqual(len(recovered), 3)
        delivery = self.call("GET", "/api/backlog-reminders/status", self.a)["deliveries"][0]
        self.assertEqual((delivery["status"], delivery["attempts"]), ("sent", 3))

    def test_unknown_and_interrupted_delivery_are_not_retried(self):
        self.task()
        calls = []
        def uncertain(*args):
            calls.append(args)
            raise self.M.MailDeliveryUncertain("Controlled lost connection")
        self.run_tick(uncertain)
        self.run_tick(uncertain, 61)
        self.assertEqual(len(calls), 1)
        with self.M.SessionLocal() as s:
            row = s.scalar(self.select(self.M.BacklogReminderDelivery).where(self.M.BacklogReminderDelivery.user_id == self.ids[0]))
            row.status = "sending"
            row.updated_at = self.now - dt.timedelta(minutes=6)
            s.commit()
        self.run_tick(uncertain, 62)
        self.assertEqual(len(calls), 1)
        self.assertEqual(self.call("GET", "/api/backlog-reminders/status", self.a)["deliveries"][0]["status"], "unknown")

    def test_overdue_slots_repeat_without_replaying_missed_slots(self):
        self.settings.update(deadline_offsets=[], overdue_every=15)
        self.call("PUT", "/api/me/prefs", self.a, {"tz": "UTC", "backlog_reminders": self.settings})
        self.task()
        sent = []
        for seconds in [16 * 60, 17 * 60, 31 * 60, 32 * 60]:
            self.run_tick(lambda *args: sent.append(args), seconds)
        self.assertEqual(len(sent), 2)

    def test_disabled_completed_and_archived_work_is_skipped_and_account_deletion_cascades(self):
        self.task(done=True)
        sent = []
        self.run_tick(lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        self.task(done=False)
        self.call("PUT", "/api/me/prefs", self.a, {"tz": "UTC", "backlog_reminders": {**self.settings, "enabled": False}})
        self.run_tick(lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        self.call("PUT", "/api/me/prefs", self.a, {"tz": "UTC", "backlog_reminders": self.settings})
        self.doc = self.call("PUT", "/api/backlogs/" + self.doc["id"], self.a, {**self.doc, "archived": True})
        self.run_tick(lambda *args: sent.append(args))
        self.assertEqual(sent, [])
        self.call("PUT", "/api/backlogs/" + self.doc["id"], self.a, {**self.doc, "archived": False})
        self.run_tick(lambda *args: sent.append(args))
        self.assertEqual(len(sent), 1)
        self.call("DELETE", "/api/me", self.a, status=204)
        with self.M.SessionLocal() as s:
            self.assertIsNone(s.scalar(self.select(self.M.BacklogReminderDelivery).where(self.M.BacklogReminderDelivery.user_id == self.ids[0])))
        self.assertEqual(self.call("GET", "/api/backlog-reminders/status", self.b)["deliveries"], [])

import datetime as dt
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app import logic as L  # noqa: E402

U = dt.timezone.utc


def at(y, mo, d, h, mi=0):
    return dt.datetime(y, mo, d, h, mi, tzinfo=U)


class AgendaTiming(unittest.TestCase):
    def test_tehran_fixed_offset(self):  # UTC+3:30, 07:00 local = 03:30 UTC
        self.assertEqual(L.is_due(at(2026, 10, 20, 3, 30), "Asia/Tehran"), (True, "2026-10-20"))
        self.assertFalse(L.is_due(at(2026, 10, 20, 3, 29), "Asia/Tehran")[0])
        self.assertFalse(L.is_due(at(2026, 10, 20, 6, 30), "Asia/Tehran")[0])  # 10:00 local: window closed

    def test_berlin_dst_start_and_end(self):
        self.assertTrue(L.is_due(at(2026, 3, 29, 5, 0), "Europe/Berlin")[0])    # CEST, DST began that night
        self.assertFalse(L.is_due(at(2026, 3, 29, 4, 59), "Europe/Berlin")[0])
        self.assertTrue(L.is_due(at(2026, 10, 25, 6, 0), "Europe/Berlin")[0])   # CET, DST ended that night
        self.assertFalse(L.is_due(at(2026, 10, 25, 5, 59), "Europe/Berlin")[0])

    def test_local_date_differs_from_utc_date(self):
        self.assertEqual(L.is_due(at(2026, 10, 19, 17, 0), "Pacific/Kiritimati"), (True, "2026-10-20"))
        self.assertEqual(L.is_due(at(2026, 10, 20, 14, 0), "Pacific/Auckland")[1], "2026-10-21")

    def test_bad_zone_falls_back_to_utc(self):
        self.assertEqual(L.is_due(at(2026, 10, 20, 7, 0), "Not/AZone"), (True, "2026-10-20"))


class AgendaContent(unittest.TestCase):
    data = {
        "tasks": [
            {"title": "Algorithm class", "due": "2026-10-20", "time": "10:00", "dur": 90, "status": "To do", "pri": "High"},
            {"title": "Pay bill", "due": "2026-10-20", "status": "To do"},
            {"title": "Done already", "due": "2026-10-20", "status": "Done"},
            {"title": "Archived", "due": "2026-10-20", "status": "To do", "arch": True},
            {"title": "Call landlord", "due": "2026-10-18", "status": "To do"},
        ],
        "routines": [{"title": "Exercise", "rec": "daily", "dur": 60, "created": "2026-01-01"},
                     {"title": "Weekly review", "rec": "weekly", "created": "2026-10-19"},  # Monday; 20th is Tuesday
                     {"title": "Paused", "rec": "daily", "active": False}],
        "goals": [{"title": "Run 20", "type": "activity", "target": 4, "routine": "r1", "start": "2026-10-01", "status": "Active"},
                  {"title": "Books", "type": "outcome", "target": 12, "current": 6, "status": "Active"},
                  {"title": "Old", "type": "outcome", "target": 1, "current": 1, "status": "Completed"}],
        "logs": [{"result": "Completed", "routine": "r1", "date": "2026-10-05"}, {"result": "Completed", "routine": "r1", "date": "2026-10-06"},
                 {"result": "Missed", "routine": "r1", "date": "2026-10-07"}],
    }

    def test_english(self):
        t = L.build_agenda(self.data, "2026-10-20", "en", "Asia/Tehran", "https://x.test")
        for want in ("Algorithm class", "10:00", "high priority", "Pay bill", "Call landlord", "Exercise", "Run 20 — 50%", "Books — 50%", "https://x.test"):
            self.assertIn(want, t)
        for no in ("Done already", "Archived", "Paused", "Weekly review", "Old"):
            self.assertNotIn(no, t)

    def test_persian_and_empty_day(self):
        self.assertIn("صبح بخیر", L.build_agenda(self.data, "2026-10-20", "fa", "Asia/Tehran"))
        self.assertIn("Nothing scheduled", L.build_agenda({}, "2026-10-20"))

    def test_length_cap(self):
        big = {"tasks": [{"title": "x" * 80, "due": "2026-10-20", "status": "To do"} for _ in range(500)]}
        self.assertLessEqual(len(L.build_agenda(big, "2026-10-20")), 3900)

    def test_recurrence(self):
        d = dt.date(2026, 10, 20)
        self.assertTrue(L.occurs({"rec": "monthly", "created": "2026-01-20"}, d))
        self.assertFalse(L.occurs({"rec": "monthly", "created": "2026-01-21"}, d))
        self.assertTrue(L.occurs({"rec": "weekly", "created": "2026-10-13"}, d))
        self.assertFalse(L.occurs({"rec": "daily", "active": False}, d))


class AiHelpers(unittest.TestCase):
    def test_ssrf_blocking(self):
        for bad in ("http://example.com/v1", "https://127.0.0.1/v1", "https://10.1.2.3/v1", "https://169.254.169.254/",
                    "https://192.168.0.5/v1", "https://[::1]/v1", "https://localhost/v1", "https://user:pw@8.8.8.8/v1",
                    "https://8.8.8.8:8443/v1", "ftp://8.8.8.8/", "", "https://100.64.0.1/v1"):
            with self.assertRaises(ValueError, msg=bad):
                L.safe_public_https_url(bad)

    def test_allows_public_and_allowlist(self):
        self.assertEqual(L.safe_public_https_url("https://8.8.8.8/v1/"), "https://8.8.8.8/v1")
        with self.assertRaises(ValueError):
            L.safe_public_https_url("https://8.8.8.8/v1", allowed_hosts=["api.arvancloudai.ir"])

    def test_auth_schemes(self):
        self.assertEqual(L.schemes_to_try("auto", "https://api.arvancloudai.ir/v1"), ["apikey", "Bearer"])
        self.assertEqual(L.schemes_to_try("auto", "https://api.openai.com/v1"), ["Bearer", "apikey"])
        self.assertEqual(L.schemes_to_try("Bearer", "https://api.arvancloudai.ir/v1"), ["Bearer"])
        self.assertEqual(L.auth_header("apikey", "apikey abc-123"), {"Authorization": "apikey abc-123"})
        self.assertEqual(L.auth_header("Bearer", "  sk-1 "), {"Authorization": "Bearer sk-1"})

    def test_strip_think(self):
        self.assertEqual(L.strip_think("<think>hmm\nlong</think>\n- Do X"), "- Do X")
        self.assertEqual(L.strip_think("<think>cut off"), "")
        self.assertEqual(L.strip_think("plain"), "plain")

    def test_stats_validation_and_prompt(self):
        self.assertTrue(L.validate_stats({"a": 1}))
        self.assertFalse(L.validate_stats({"a": "x" * 7000}))
        self.assertFalse(L.validate_stats([1]))
        m = L.build_ai_messages({"streak": 3}, "fa")
        self.assertIn("Persian", m[0]["content"])
        self.assertIn('"streak": 3', m[1]["content"])


if __name__ == "__main__":
    unittest.main()

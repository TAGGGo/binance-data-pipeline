"""Offline tests for the shared core (no network). Run: python3 -m unittest discover tests"""
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

os.environ["MDH_DATA_DIR"] = tempfile.mkdtemp()

from mdh.core import db  # noqa: E402
from mdh.core.http import HttpError, QuotaExceeded, RateLimitedClient  # noqa: E402


class FakeResp:
    def __init__(self, status, headers=None):
        self.status_code, self.headers, self.text = status, headers or {}, ""

    def json(self):
        return {}


class TestHttp(unittest.TestCase):
    def client(self, **kw):
        return RateLimitedClient(kw.pop("rpm", {}), kw.pop("quota", {}), Path(tempfile.mkdtemp()), **kw)

    def test_pacing_respects_rpm(self):
        c = self.client(rpm={"x.test": 600})          # 0.1 s gap
        with mock.patch.object(c.session, "request", return_value=FakeResp(200)):
            t0 = time.monotonic()
            for _ in range(4):
                c.get("https://x.test/a")
            self.assertGreaterEqual(time.monotonic() - t0, 0.29)

    def test_retry_on_429_then_success_and_budget_halved(self):
        c = self.client(rpm={"x.test": 6000})
        seq = [FakeResp(429, {"Retry-After": "0"}), FakeResp(200)]
        with mock.patch.object(c.session, "request", side_effect=seq), mock.patch("time.sleep"):
            self.assertEqual(c.get("https://x.test/a").status_code, 200)
        self.assertEqual(c.host_rpm["x.test"], 3000)

    def test_non_retryable_raises(self):
        c = self.client()
        with mock.patch.object(c.session, "request", return_value=FakeResp(403)):
            with self.assertRaises(HttpError):
                c.get("https://x.test/a")

    def test_monthly_quota_enforced_and_persisted(self):
        state = Path(tempfile.mkdtemp())
        c = RateLimitedClient({"q.test": 6000}, {"q.test": 2}, state)
        with mock.patch.object(c.session, "request", return_value=FakeResp(200)):
            c.get("https://q.test/1")
            c.get("https://q.test/2")
            with self.assertRaises(QuotaExceeded):
                c.get("https://q.test/3")
        c2 = RateLimitedClient({"q.test": 6000}, {"q.test": 2}, state)   # new run, same month
        self.assertEqual(c2.quota_used("q.test"), 2)


class TestUpsert(unittest.TestCase):
    def test_upsert_replaces_and_adds_columns(self):
        con = db.connect()
        db.upsert(con, "t", pd.DataFrame({"k": [1, 2], "v": [10.0, 20.0]}), ["k"])
        db.upsert(con, "t", pd.DataFrame({"k": [2, 3], "v": [21.0, 30.0], "extra": ["a", "b"]}), ["k"])
        rows = con.execute("SELECT k, v, extra FROM t ORDER BY k").fetchall()
        self.assertEqual(rows, [(1, 10.0, None), (2, 21.0, "a"), (3, 30.0, "b")])
        self.assertEqual(db.max_value(con, "t", "k"), 3)


if __name__ == "__main__":
    unittest.main()

"""Indicator math checked against plain-loop reference implementations (python3 -m unittest discover tests)."""
import unittest

import numpy as np
import pandas as pd

from mdh import indicators as ind


def ref_ema(x, n):
    a, out, e = 2 / (n + 1), [], None
    for v in x:
        e = v if e is None else a * v + (1 - a) * e
        out.append(e)
    return np.array(out)


def ref_rsi(x, n=14):
    ch = np.diff(x)
    out = [np.nan] * len(x)
    ag = sum(max(c, 0) for c in ch[:n]) / n
    al = sum(max(-c, 0) for c in ch[:n]) / n
    out[n] = 100 - 100 / (1 + ag / al)
    for i in range(n + 1, len(x)):
        c = ch[i - 1]
        ag = (ag * (n - 1) + max(c, 0)) / n
        al = (al * (n - 1) + max(-c, 0)) / n
        out[i] = 100 - 100 / (1 + ag / al)
    return np.array(out)


class TestIndicators(unittest.TestCase):
    def setUp(self):
        rng = np.random.default_rng(7)
        self.x = pd.Series(100 * np.exp(np.cumsum(rng.normal(0, 0.02, 400))))

    def test_sma(self):
        s = ind.sma(self.x, 20)
        self.assertTrue(s.iloc[:19].isna().all())
        np.testing.assert_allclose(s.iloc[19:].values, np.convolve(self.x, np.ones(20) / 20, "valid"), rtol=1e-12)

    def test_ema(self):
        e = ind.ema(self.x, 50)
        self.assertTrue(e.iloc[:49].isna().all())
        np.testing.assert_allclose(e.iloc[49:].values, ref_ema(self.x.values, 50)[49:], rtol=1e-12)

    def test_macd(self):
        m = ind.macd(self.x)
        line = ref_ema(self.x.values, 12) - ref_ema(self.x.values, 26)
        sig = ref_ema(line, 9)
        np.testing.assert_allclose(m["macd"].iloc[34:].values, line[34:], rtol=1e-10)
        np.testing.assert_allclose(m["signal"].iloc[34:].values, sig[34:], rtol=1e-10)
        np.testing.assert_allclose(m["hist"].iloc[34:].values, (line - sig)[34:], rtol=1e-10, atol=1e-12)
        self.assertTrue(m.iloc[:34].isna().all().all())

    def test_rsi(self):
        r = ind.rsi(self.x)
        np.testing.assert_allclose(r.values, ref_rsi(self.x.values), rtol=1e-10, equal_nan=True)
        self.assertTrue(((r.dropna() >= 0) & (r.dropna() <= 100)).all())

    def test_weekly_monthly_boundaries(self):
        idx = pd.date_range("2024-01-01", "2024-03-31", freq="D")   # 2024-01-01 is a Monday
        d = pd.DataFrame({"open": np.arange(len(idx)) + 1.0, "high": 1000.0, "low": 1.0,
                          "close": np.arange(len(idx)) + 2.0, "vol": 1.0}, index=idx)
        w = ind._agg(d, ind.TF_RULE["1w"])
        self.assertEqual(w.index[0], pd.Timestamp("2024-01-01"))
        self.assertTrue((w.index.dayofweek == 0).all())
        self.assertEqual(w["vol"].iloc[0], 7)
        self.assertEqual(w["open"].iloc[0], 1.0)
        self.assertEqual(w["close"].iloc[0], 8.0)
        mth = ind._agg(d, ind.TF_RULE["1M"])
        self.assertEqual(list(mth.index.day), [1, 1, 1])
        self.assertEqual(mth["vol"].tolist(), [31, 29, 31])



class TestVsBtcMa200(unittest.TestCase):
    def test_streak_and_tries(self):
        idx = pd.date_range("2025-01-01", periods=260, freq="D")
        btc = pd.Series(100.0, index=idx)
        coin = pd.Series(np.r_[np.full(200, 1.0), np.full(20, 0.9), np.full(3, 1.2), np.full(2, 0.8), np.full(35, 1.3)], index=idx)
        rel = ind.vs_btc(coin, btc)
        self.assertEqual(rel["streak200"].iloc[-1], 35)
        tries = ind.ma200_tries(rel, days=90)
        self.assertEqual([t["days"] for t in tries], [3, 35])
        self.assertTrue(tries[-1]["running"])
        self.assertFalse(tries[0]["running"])


if __name__ == "__main__":
    unittest.main()


"""Exchange-flow step filter, whale score and Fibonacci levels on small hand-checked inputs."""
import unittest

import numpy as np
import pandas as pd

from mdh import indicators as ind
from mdh.dashboard import onchain


class TestExchangeFlows(unittest.TestCase):
    def test_steps_removed_and_anchor(self):
        ix = pd.date_range("2026-01-01", periods=5)
        w = pd.DataFrame({"a": [100, 101, 99, 99, 98], "b": [10, 10, 30, 30, 29]}, index=ix, dtype=float)
        x = onchain.exchange_flows(w)
        # b's jump 10 -> 30 (+200%) is a wallet-list change: not a flow
        self.assertEqual(list(x["flow"]), [0, 1, -2, 0, -2])
        self.assertEqual(x["steps"].sum(), 1)
        self.assertAlmostEqual(x["adjusted"].iloc[-1], 127)          # anchored to today's reported total
        self.assertAlmostEqual(x["adjusted"].iloc[0], 127 - (1 - 2 + 0 - 2))

    def test_new_exchange_is_not_inflow(self):
        ix = pd.date_range("2026-01-01", periods=3)
        w = pd.DataFrame({"a": [100, 100, 100], "b": [np.nan, 50, 51]}, index=ix)
        self.assertEqual(list(onchain.exchange_flows(w)["flow"]), [0, 0, 1])


class TestWhaleScore(unittest.TestCase):
    def test_weighted_breadth(self):
        ix = pd.date_range("2026-01-01", periods=2)
        m = pd.DataFrame({"big_up": [100, 110], "small_down": [10, 5], "dormant": [50, 50], "new": [np.nan, 20]}, index=ix)
        s = onchain.whale_scores(m, 1, min_start=1).iloc[-1]
        self.assertAlmostEqual(s["score"], 100 / 110)                 # dormant ignored, weights = start balances
        self.assertAlmostEqual(s["net"], 10 - 5)
        self.assertAlmostEqual(s["new"], 20)


class TestFib(unittest.TestCase):
    def test_up_swing(self):
        ix = pd.date_range("2026-01-01", periods=40)
        close = np.r_[np.linspace(100, 60, 10), np.linspace(60, 200, 20), np.linspace(200, 150, 10)]
        d = pd.DataFrame({"close": close, "high": close, "low": close}, index=ix)
        f = ind.fib_levels(d)
        self.assertEqual(f["dir"], "up")
        self.assertEqual(f["high"][1], 200)
        self.assertEqual(f["low"][1], 60)
        lv = {x["r"]: x["p"] for x in f["levels"]}
        self.assertAlmostEqual(lv[0.5], 130)
        self.assertAlmostEqual(lv[0.618], 200 - 0.618 * 140)
        self.assertAlmostEqual(f["retrace"], 50 / 140)
        self.assertEqual(f["support"]["r"], 0.382)


if __name__ == "__main__":
    unittest.main()

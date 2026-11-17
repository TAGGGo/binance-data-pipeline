"""Offline unit tests for SoSoValue+CMC fallback, Bitwise App Router parser, and etf_issuers fallback."""
import json
import logging
import os
import tempfile
import unittest
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import duckdb
import pandas as pd

os.environ.setdefault("MDH_DATA_DIR", tempfile.mkdtemp())

from mdh.sources import bitwise, etf_issuers, sosovalue  # noqa: E402


class FakeHttp:
    def __init__(self, get_json_fn=None, post_fn=None, get_fn=None):
        self._get_json = get_json_fn
        self._post = post_fn
        self._get = get_fn

    def get_json(self, url, **kw):
        return self._get_json(url, **kw)

    def post(self, url, **kw):
        return self._post(url, **kw)

    def get(self, url, **kw):
        return self._get(url, **kw)


class FakePostResp:
    def __init__(self, data):
        self._data = data

    def json(self):
        return self._data


def _ctx(con, http):
    return SimpleNamespace(con=con, http=http, log=logging.getLogger("test.etf"))


class TestSosovalueCmcFallback(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_pending_filled_from_cmc_then_overwritten_by_complete_v2_and_no_nan_corruption(self):
        # Seed a prior snapshot row for 2026-11-13 IBIT to ensure pending doesn't clobber it with NaN
        self.con.execute("""
            CREATE TABLE etf_fund_snapshot (
                date DATE, asset VARCHAR, ticker VARCHAR, institute VARCHAR, source VARCHAR,
                net_assets_usd DOUBLE, daily_net_inflow_usd DOUBLE, cum_net_inflow_usd DOUBLE,
                value_traded_usd DOUBLE, premium DOUBLE, fee DOUBLE
            )
        """)
        self.con.execute("""
            INSERT INTO etf_fund_snapshot VALUES
            ('2026-11-13', 'BTC', 'IBIT', 'BlackRock', 'sosovalue', 5.4e10, -143.5e6, 6.4e10, 2.0e9, -0.001, 0.0025)
        """)

        cmc_payload = {
            "data": {
                "points": [{
                    "date": str(int(pd.Timestamp("2026-11-16", tz="UTC").timestamp() * 1000)),
                    "total": 999e6,  # must be ignored
                    "CHINAAMC": {"usd": 0.2e6},
                    "IBIT": {"usd": 76.5e6},
                    "ETHA": {"usd": 0.0},
                    "VSOL": {"usd": 0.9e6},
                }]
            }
        }

        v1_rows = {
            "BTC": [{"date": "2026-11-13", "total_net_inflow": -185.45e6, "total_value_traded": 2.5e9,
                     "total_net_assets": 86e9, "cum_net_inflow": 54.6e9}],
            "ETH": [{"date": "2026-11-13", "total_net_inflow": 2.36e6, "total_value_traded": 0.46e9,
                     "total_net_assets": 10.8e9, "cum_net_inflow": 11.5e9}],
            "SOL": [{"date": "2026-11-13", "total_net_inflow": -3.09e6, "total_value_traded": 0.04e9,
                     "total_net_assets": 1.1e9, "cum_net_inflow": 1.4e9}],
            "XRP": [{"date": "2026-11-16", "total_net_inflow": 0.0, "total_value_traded": 0.03e9,
                     "total_net_assets": 1.0e9, "cum_net_inflow": 1.7e9}],
        }

        ibit_complete = False

        def fake_get_json(url, **kw):
            if "summary-history" in url:
                return {"code": 0, "data": v1_rows[kw["params"]["symbol"]]}
            if "coinmarketcap" in url:
                return cmc_payload
            raise AssertionError(f"unexpected GET {url}")

        def fake_post(url, **kw):
            t = kw["json"]["type"]
            if t == "us-btc-spot":
                ibit_row = (
                    {"ticker": "IBIT", "institute": "BlackRock",
                     "dailyNetInflow": {"value": "76400000", "lastUpdateDate": "2026-11-16", "status": "1"},
                     "netAssets": {"value": "54e9", "lastUpdateDate": "2026-11-16"}}
                    if ibit_complete else
                    {"ticker": "IBIT", "institute": "BlackRock",
                     "dailyNetInflow": {"value": None, "lastUpdateDate": None, "status": "3"},
                     "netAssets": {"value": "54e9", "lastUpdateDate": "2026-11-13"}}
                )
                tot = "7886219.7" if ibit_complete else "-68513780.3"
                return FakePostResp({"data": {
                    "dailyNetInflow": {"value": tot, "lastUpdateDate": "2026-11-16", "status": "1"},
                    "totalNetAssets": {"value": "84e9"}, "cumNetInflow": {"value": "54.58e9"},
                    "dailyTotalValueTraded": {"value": "3e9"},
                    "list": [
                        {"ticker": "FBTC", "institute": "Fidelity",
                         "dailyNetInflow": {"value": "-68513780.3", "lastUpdateDate": "2026-11-16", "status": "1"},
                         "netAssets": {"value": "11e9", "lastUpdateDate": "2026-11-16"}},
                        ibit_row,
                    ],
                }})
            if t == "us-eth-spot":
                return FakePostResp({"data": {
                    "dailyNetInflow": {"value": "-8312467.225", "lastUpdateDate": "2026-11-16", "status": "1"},
                    "totalNetAssets": {"value": "10.5e9"}, "cumNetInflow": {"value": "11.51e9"},
                    "dailyTotalValueTraded": {"value": "0.5e9"},
                    "list": [
                        {"ticker": "FETH", "institute": "Fidelity",
                         "dailyNetInflow": {"value": "-8312467.225", "lastUpdateDate": "2026-11-16", "status": "1"},
                         "netAssets": {"value": "1.1e9", "lastUpdateDate": "2026-11-16"}},
                        {"ticker": "ETHA", "institute": "BlackRock",
                         "dailyNetInflow": {"value": None, "lastUpdateDate": None, "status": "3"},
                         "netAssets": {"value": "5.4e9", "lastUpdateDate": "2026-11-13"}},
                        {"ticker": "ETHB", "institute": "BlackRock",
                         "dailyNetInflow": {"value": None, "lastUpdateDate": None, "status": "3"},
                         "netAssets": {"value": "0.75e9", "lastUpdateDate": "2026-11-13"}},
                    ],
                }})
            return FakePostResp({"data": {
                "dailyNetInflow": {"value": "10784009.83", "lastUpdateDate": "2026-11-16", "status": "1"},
                "totalNetAssets": {"value": "1.0e9"}, "cumNetInflow": {"value": "1.52e9"},
                "dailyTotalValueTraded": {"value": "0.05e9"},
                "list": [
                    {"ticker": "BSOL", "institute": "Bitwise",
                     "dailyNetInflow": {"value": "10784009.83", "lastUpdateDate": "2026-11-16", "status": "1"},
                     "netAssets": {"value": "0.72e9", "lastUpdateDate": "2026-11-16"}},
                    {"ticker": "VSOL", "institute": "VanEck",
                     "dailyNetInflow": {"value": None, "lastUpdateDate": None, "status": "3"},
                     "netAssets": {"value": "0.015e9", "lastUpdateDate": "2026-11-13"}},
                ],
            }})

        ctx = _ctx(self.con, FakeHttp(get_json_fn=fake_get_json, post_fn=fake_post))
        with mock.patch.dict(os.environ, {"SOSOVALUE_API_KEY": "test-key"}):
            sosovalue.run(ctx)

        rows = dict(self.con.execute(
            "SELECT asset, net_inflow_usd FROM etf_flows_daily WHERE date='2026-11-16' ORDER BY asset"
        ).fetchall())
        self.assertAlmostEqual(rows["BTC"], 7986219.7, places=2)
        self.assertAlmostEqual(rows["ETH"], -8312467.225, places=2)
        self.assertAlmostEqual(rows["SOL"], 11684009.83, places=2)
        self.assertAlmostEqual(rows["XRP"], 0.0, places=2)

        # Ensure 2026-11-13 IBIT snapshot was NOT overwritten with NaN
        ibit_13 = self.con.execute(
            "SELECT daily_net_inflow_usd FROM etf_fund_snapshot WHERE date='2026-11-13' AND ticker='IBIT'"
        ).fetchone()[0]
        self.assertAlmostEqual(ibit_13, -143.5e6, places=1)
        # Ensure 2026-11-16 IBIT snapshot got CMC value, ETHB skipped
        snaps_16 = dict(self.con.execute(
            "SELECT ticker, daily_net_inflow_usd FROM etf_fund_snapshot WHERE date='2026-11-16'"
        ).fetchall())
        self.assertAlmostEqual(snaps_16["IBIT"], 76.5e6, places=1)
        self.assertNotIn("ETHB", snaps_16)

        # Now SoSoValue catches up on IBIT -> should overwrite CMC-assisted BTC 11/16 row
        ibit_complete = True
        with mock.patch.dict(os.environ, {"SOSOVALUE_API_KEY": "test-key"}):
            sosovalue.run(ctx)
        btc_exact = self.con.execute(
            "SELECT net_inflow_usd FROM etf_flows_daily WHERE date='2026-11-16' AND asset='BTC'"
        ).fetchone()[0]
        self.assertAlmostEqual(btc_exact, 7886219.7, places=2)


class TestBitwiseAppRouter(unittest.TestCase):
    def test_app_router_holdings_ahead_of_details(self):
        payload = json.dumps({
            "details": {
                "asOfDate": "2026-11-13",
                "marketPrice": {"value": 40.92},
                "netAssets": 131950938.5,
                "sharesOutstanding": 3220000,
            },
            "holdings": {
                "asOfDate": "2026-11-16",
                "basket": [{
                    "name": "HYPERLIQUID", "marketValue": 121222465.52,
                    "assetInTrust": 1768057.83795842, "assetPerShare": 0.5630757445727452,
                }],
            },
            "navMarketPrice": {},
        })
        escaped = json.dumps(payload)  # wrapped in quotes with escaped inner quotes
        html = f'<script>self.__next_f.push([1,{escaped}])</script>'
        snaps = bitwise.parse(html)
        self.assertEqual(len(snaps), 2)
        self.assertEqual(snaps[0]["date"], date(2026, 11, 13))
        self.assertEqual(snaps[0]["shares_outstanding"], 3220000)
        self.assertEqual(snaps[1]["date"], date(2026, 11, 16))
        self.assertEqual(snaps[1]["shares_outstanding"], 3140000)
        self.assertAlmostEqual(snaps[1]["aum_usd"], 121222465.52, places=2)

    def test_app_router_same_date(self):
        payload = json.dumps({
            "details": {
                "asOfDate": "2026-11-16",
                "marketPrice": {"value": 38.60},
                "netAssets": 121222465.52,
                "sharesOutstanding": 3140000,
            },
            "holdings": {
                "asOfDate": "2026-11-16",
                "basket": [{
                    "marketValue": 121222465.52,
                    "assetInTrust": 1768057.83795842, "assetPerShare": 0.5630757445727452,
                }],
            },
            "navMarketPrice": {},
        })
        html = f'<script>self.__next_f.push([1,{json.dumps(payload)}])</script>'
        snaps = bitwise.parse(html)
        self.assertEqual(len(snaps), 1)
        self.assertEqual(snaps[0]["date"], date(2026, 11, 16))
        self.assertEqual(snaps[0]["shares_outstanding"], 3140000)
        self.assertAlmostEqual(snaps[0]["market_price"], 38.60, places=2)


class TestEtfIssuersCmcFallback(unittest.TestCase):
    def setUp(self):
        self.con = duckdb.connect(":memory:")

    def tearDown(self):
        self.con.close()

    def test_hype_missing_thyp_and_hypg_filled_from_cmc(self):
        self.con.execute("""
            CREATE TABLE etf_fund_daily (
                date DATE, shares_outstanding BIGINT, nav_per_share DOUBLE,
                market_price DOUBLE, aum_usd DOUBLE, ticker VARCHAR, asset VARCHAR, source VARCHAR
            )
        """)
        rows = [
            # BHYP (lag=0): 11/12, 11/13, 11/16
            (date(2026, 11, 12), 3220000, 40.0, None, 128.8e6, "BHYP", "HYPE", "bitwise"),
            (date(2026, 11, 13), 3220000, 40.98, None, 131.95e6, "BHYP", "HYPE", "bitwise"),
            (date(2026, 11, 16), 3140000, 121222465.52 / 3140000, None, 121222465.52, "BHYP", "HYPE", "bitwise"),
            # THYP (lag=0): only up to 11/13
            (date(2026, 11, 12), 1080000, 42.0, None, 45.36e6, "THYP", "HYPE", "21shares"),
            (date(2026, 11, 13), 1080000, 42.13, None, 45.50e6, "THYP", "HYPE", "21shares"),
            # HYPG (lag=1): up to 11/16, so flow stops at 11/13
            (date(2026, 11, 12), 6470000, 25.33, None, 163.88e6, "HYPG", "HYPE", "grayscale"),
            (date(2026, 11, 13), 6470000, 25.73, None, 166.45e6, "HYPG", "HYPE", "grayscale"),
            (date(2026, 11, 16), 6470000, 24.16, None, 156.34e6, "HYPG", "HYPE", "grayscale"),
        ]
        self.con.executemany("INSERT INTO etf_fund_daily VALUES (?, ?, ?, ?, ?, ?, ?, ?)", rows)

        cmc_payload = {
            "data": {
                "points": [{
                    "date": str(int(pd.Timestamp("2026-11-16", tz="UTC").timestamp() * 1000)),
                    "THYP": {"usd": -4.8e6},
                    "HYPG": {"usd": 0.0},
                }]
            }
        }
        fake_issuers = {
            "HYPE": [
                {"ticker": "THYP", "issuer": "21shares", "listing": "2026-05-12"},
                {"ticker": "BHYP", "issuer": "bitwise", "listing": "2026-05-15"},
                {"ticker": "HYPG", "issuer": "grayscale", "listing": "2026-06-03", "flow_lag": 1},
            ]
        }
        ctx = _ctx(self.con, FakeHttp(get_json_fn=lambda u, **kw: cmc_payload))
        with mock.patch.dict(etf_issuers.settings.__dict__, {"ISSUER_ETFS": fake_issuers}):
            etf_issuers.run(ctx)

        res = dict(self.con.execute(
            "SELECT date, net_inflow_usd FROM etf_flows_daily WHERE asset='HYPE' ORDER BY date"
        ).fetchall())
        self.assertAlmostEqual(res[date(2026, 11, 13)], 0.0, places=2)
        self.assertAlmostEqual(res[date(2026, 11, 16)], -7888470.46, places=2)


if __name__ == "__main__":
    unittest.main()

"""
Question: Does the 2026 BTC & ETH market structure match 2023, or is @firehand_eth right that 2026 is playing out differently from 2023?
Data: DuckDB market.snapshot.duckdb (bn_kline_1d, onchain_daily, v_crypto_market_daily, v_macro_daily, v_funding_daily)
Date: 2026-11-11
"""
import duckdb
from pathlib import Path

REPO = Path(__file__).parents[2]
DB_PATH = REPO / "data" / "market.snapshot.duckdb"
OUT_PATH = REPO / "data" / "outputs" / "firehand_2023_vs_2026.txt"

def main():
    con = duckdb.connect(str(DB_PATH), read_only=True)
    lines = []
    def log(s=""):
        print(s)
        lines.append(s)

    log("=== 1. FIVE-STAGE STRUCTURAL COMPARISON (BTC & ETH) ===")
    # Query exact daily & weekly landmarks
    stages = [
        ("Cycle ATH",
         "symbol='BTC' AND ts BETWEEN '2021-11-01' AND '2021-11-15'",
         "symbol='BTC' AND ts BETWEEN '2025-10-01' AND '2025-10-15'",
         "symbol='ETH' AND ts BETWEEN '2021-11-01' AND '2021-11-15'",
         "symbol='ETH' AND ts BETWEEN '2025-08-01' AND '2025-10-15'"),
    ]

    for sym in ["BTC", "ETH"]:
        log(f"\n--- {sym} Landmarks: 2022-2023 vs 2025-2026 ---")
        # 2022-2023 landmarks
        ath_21 = con.execute("SELECT max(high), arg_max(ts::DATE, high) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2021-10-01' AND '2021-12-01'", [sym]).fetchone()
        leg1_low_22 = con.execute("SELECT min(low), arg_min(ts::DATE, low) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2022-06-01' AND '2022-07-01'", [sym]).fetchone()
        rally_hi_22 = con.execute("SELECT max(high), arg_max(ts::DATE, high), max(close) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2022-08-01' AND '2022-08-25'", [sym]).fetchone()
        wk_rally_22 = con.execute("""
            SELECT max(c), max(h) FROM (
                SELECT date_trunc('week', ts)::DATE wk, last(close ORDER BY ts) c, max(high) h
                FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2022-07-18' AND '2022-08-21' GROUP BY 1
            )
        """, [sym]).fetchone()
        pre_bd_22 = con.execute("""
            SELECT first(open ORDER BY ts), max(high), min(low), last(close ORDER BY ts)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2022-11-07'
        """, [sym]).fetchone()
        bot_22 = con.execute("SELECT min(low), arg_min(ts::DATE, low) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2022-11-01' AND '2022-12-31'", [sym]).fetchone()
        brk_23 = con.execute("""
            SELECT first(open ORDER BY ts), max(high), min(low), last(close ORDER BY ts)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2023-01-09'
        """, [sym]).fetchone()
        feb_hi_23 = con.execute("SELECT max(high), arg_max(ts::DATE, high) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2023-01-15' AND '2023-02-28'", [sym]).fetchone()
        mar_lo_23 = con.execute("SELECT min(low), arg_min(ts::DATE, low), min(close) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2023-03-01' AND '2023-03-12'", [sym]).fetchone()
        mar_wk_lo_23 = con.execute("""
            SELECT last(close ORDER BY ts), min(low)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2023-03-06'
        """, [sym]).fetchone()
        apr_hi_23 = con.execute("SELECT max(high), arg_max(ts::DATE, high) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2023-03-13' AND '2023-05-01'", [sym]).fetchone()
        jun_lo_23 = con.execute("SELECT min(low), arg_min(ts::DATE, low) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2023-05-01' AND '2023-06-20'", [sym]).fetchone()
        jul_hi_23 = con.execute("SELECT max(high), arg_max(ts::DATE, high) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2023-06-20' AND '2023-07-31'", [sym]).fetchone()

        log(f"  [2022-2023]")
        log(f"    Cycle High (2021-11):         {ath_21[0]:.1f} ({ath_21[1]})")
        log(f"    Leg-1 Crash Low (2022-06):    {leg1_low_22[0]:.1f} ({leg1_low_22[1]}), drawdown {(leg1_low_22[0]/ath_21[0]-1)*100:.1f}%")
        log(f"    Upper Red Line (2022-08):     Week Close High {wk_rally_22[0]:.1f}, Intraday High {wk_rally_22[1]:.1f} ({rally_hi_22[1]}), bounce +{(wk_rally_22[1]/leg1_low_22[0]-1)*100:.1f}%")
        log(f"    Lower Red Line (2022-11-07):  Breakdown Week Open {pre_bd_22[0]:.1f} (High {pre_bd_22[1]:.1f} -> Close {pre_bd_22[3]:.1f}, {(pre_bd_22[3]/pre_bd_22[0]-1)*100:+.1f}%)")
        log(f"    Cycle Bottom (2022-11):       {bot_22[0]:.1f} ({bot_22[1]}), drawdown from ATH {(bot_22[0]/ath_21[0]-1)*100:.1f}%, below Leg-1 low by {(bot_22[0]/leg1_low_22[0]-1)*100:+.1f}%")
        log(f"    Bottoming weeks before breakout: 8 weeks (2022-11-14 to 2023-01-02)")
        log(f"    Breakout Week (2023-01-09):   Open {brk_23[0]:.1f} -> Close {brk_23[3]:.1f} ({(brk_23[3]/brk_23[0]-1)*100:+.1f}%), High {brk_23[1]:.1f}")
        log(f"    1st Attempt at Upper Line:    {feb_hi_23[0]:.1f} ({feb_hi_23[1]})")
        log(f"    Retest of Lower Red Line:     2023-03-06 Week Close {mar_wk_lo_23[0]:.1f}, Intraday Wick {mar_wk_lo_23[1]:.1f} ({mar_lo_23[1]})")
        log(f"    2nd Surge above Upper Line:   {apr_hi_23[0]:.1f} ({apr_hi_23[1]}), +{(apr_hi_23[0]/wk_rally_22[0]-1)*100:.1f}% above Upper Red Line")
        log(f"    Jun-2023 Retest of Upper Line:{jun_lo_23[0]:.1f} ({jun_lo_23[1]}) -> Jul-2023 High {jul_hi_23[0]:.1f} ({jul_hi_23[1]})")

        # 2025-2026 landmarks
        ath_25 = con.execute("SELECT max(high), arg_max(ts::DATE, high) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2025-08-01' AND '2025-10-20'", [sym]).fetchone()
        leg1_low_26 = con.execute("SELECT min(low), arg_min(ts::DATE, low) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2026-01-25' AND '2026-02-28'", [sym]).fetchone()
        wk_rally_26 = con.execute("""
            SELECT max(c), max(h), arg_max(wk, h) FROM (
                SELECT date_trunc('week', ts)::DATE wk, last(close ORDER BY ts) c, max(high) h
                FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2026-04-01' AND '2026-05-15' GROUP BY 1
            )
        """, [sym]).fetchone()
        pre_bd_26 = con.execute("""
            SELECT first(open ORDER BY ts), max(high), min(low), last(close ORDER BY ts)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2026-06-01'
        """, [sym]).fetchone()
        bot_26 = con.execute("SELECT min(low), arg_min(ts::DATE, low) FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2026-06-01' AND '2026-08-15'", [sym]).fetchone()
        brk_26 = con.execute("""
            SELECT first(open ORDER BY ts), max(high), min(low), last(close ORDER BY ts)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2026-08-17'
        """, [sym]).fetchone()
        sep_hi_26 = con.execute("""
            SELECT max(c), max(h), arg_max(wk, c) FROM (
                SELECT date_trunc('week', ts)::DATE wk, last(close ORDER BY ts) c, max(high) h
                FROM bn_kline_1d WHERE market='spot' AND symbol=? AND ts BETWEEN '2026-08-17' AND '2026-10-05' GROUP BY 1
            )
        """, [sym]).fetchone()
        now_26 = con.execute("""
            SELECT ts::DATE, close, low FROM bn_kline_1d WHERE market='spot' AND symbol=? ORDER BY ts DESC LIMIT 1
        """, [sym]).fetchone()
        wk_now_26 = con.execute("""
            SELECT min(low), last(close ORDER BY ts)
            FROM bn_kline_1d WHERE market='spot' AND symbol=? AND date_trunc('week', ts)::DATE = '2026-11-09'
        """, [sym]).fetchone()

        log(f"  [2025-2026]")
        log(f"    Cycle High (2025):            {ath_25[0]:.1f} ({ath_25[1]})")
        log(f"    Leg-1 Crash Low (2026-02):    {leg1_low_26[0]:.1f} ({leg1_low_26[1]}), drawdown {(leg1_low_26[0]/ath_25[0]-1)*100:.1f}%")
        log(f"    Upper Red Line (2026-04/05):  Week Close High {wk_rally_26[0]:.1f}, Intraday High {wk_rally_26[1]:.1f} (wk {wk_rally_26[2]}), bounce +{(wk_rally_26[1]/leg1_low_26[0]-1)*100:.1f}%")
        log(f"    Lower Red Line (2026-06-01):  Breakdown Week Open {pre_bd_26[0]:.1f} (High {pre_bd_26[1]:.1f} -> Close {pre_bd_26[3]:.1f}, {(pre_bd_26[3]/pre_bd_26[0]-1)*100:+.1f}%)")
        log(f"    Cycle Bottom (2026-06):       {bot_26[0]:.1f} ({bot_26[1]}), drawdown from ATH {(bot_26[0]/ath_25[0]-1)*100:.1f}%, below Leg-1 low by {(bot_26[0]/leg1_low_26[0]-1)*100:+.1f}%")
        log(f"    Bottoming weeks before breakout: 10 weeks (2026-06-08 to 2026-08-10)")
        log(f"    Breakout Week (2026-08-17):   Open {brk_26[0]:.1f} -> Close {brk_26[3]:.1f} ({(brk_26[3]/brk_26[0]-1)*100:+.1f}%), High {brk_26[1]:.1f}")
        log(f"    Sep-2026 Peak above Upper Line: Week Close High {sep_hi_26[0]:.1f} (wk {sep_hi_26[2]}), Intraday High {sep_hi_26[1]:.1f}, +{(sep_hi_26[0]/wk_rally_26[0]-1)*100:.1f}% above Upper Red Line")
        log(f"    Current (2026-11-11):         Close {now_26[1]:.1f} (this week low {wk_now_26[0]:.1f}), 6 consecutive red weeks from Sep peak ({(now_26[1]/sep_hi_26[0]-1)*100:+.1f}%)")
        log(f"    Position vs Red Lines:        vs Upper Line ({wk_rally_26[0]:.1f}): {(now_26[1]/wk_rally_26[0]-1)*100:+.1f}% | vs Lower Line ({pre_bd_26[0]:.1f}): {(now_26[1]/pre_bd_26[0]-1)*100:+.1f}%")

    log("\n=== 2. ON-CHAIN & MARKET DOMINANCE COMPARISON ===")
    # Check what metrics exist in onchain_daily
    metrics = [r[0] for r in con.execute("SELECT DISTINCT metric FROM onchain_daily ORDER BY 1").fetchall()]
    log(f"onchain_daily metrics: {metrics}")
    for d_label, dt in [
        ("2022-11-21 (2022 Bottom)", "2022-11-21"),
        ("2023-01-16 (After Jan Breakout)", "2023-01-16"),
        ("2023-03-10 (Mar SVB Retest Low)", "2023-03-10"),
        ("2023-04-14 (Apr Breakout Peak)", "2023-04-14"),
        ("2023-06-15 (Jun Retest Low)", "2023-06-15"),
        ("2023-08-18 (Aug Breakdown)", "2023-08-18"),
        ("2026-06-29 (2026 Bottom)", "2026-06-29"),
        ("2026-08-24 (After Aug Breakout)", "2026-08-24"),
        ("2026-09-28 (Sep Peak)", "2026-09-28"),
        ("2026-11-10 (Current)", "2026-11-10"),
    ]:
        rows = con.execute("""
            SELECT metric, value FROM onchain_daily
            WHERE asset='BTC' AND date = ?
            ORDER BY metric
        """, [dt]).fetchall()
        if not rows:
            # get latest on or before dt
            rows = con.execute("""
                SELECT metric, arg_max(value, date)
                FROM onchain_daily
                WHERE asset='BTC' AND date <= ? AND date >= (?::DATE - INTERVAL '7 days')
                GROUP BY metric ORDER BY metric
            """, [dt, dt]).fetchall()
        m_dict = {k: round(v, 4) if v is not None else None for k, v in rows}
        log(f"  {d_label}: {m_dict}")

    log("\n=== 3. BTC.D, ETH.D, AND MACRO (SPX/NDX/DXY) COMPARISON ===")
    # Check tv_bars symbols
    tv_syms = [r[0] for r in con.execute("SELECT DISTINCT symbol FROM tv_bars ORDER BY 1").fetchall()]
    log(f"tv_bars symbols: {tv_syms}")
    for d_label, dt in [
        ("2022-11-07", "2022-11-07"),
        ("2023-01-09", "2023-01-09"),
        ("2023-03-10", "2023-03-10"),
        ("2023-04-14", "2023-04-14"),
        ("2023-08-18", "2023-08-18"),
        ("2026-06-01", "2026-06-01"),
        ("2026-08-17", "2026-08-17"),
        ("2026-09-28", "2026-09-28"),
        ("2026-11-10", "2026-11-10"),
    ]:
        rows = con.execute("""
            SELECT symbol, arg_max(close, ts)
            FROM tv_bars
            WHERE ts::DATE <= ?::DATE AND ts::DATE >= (?::DATE - INTERVAL '7 days')
            GROUP BY symbol ORDER BY symbol
        """, [dt, dt]).fetchall()
        log(f"  {d_label}: { {k: round(v, 2) for k, v in rows} }")

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    OUT_PATH.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nSaved to {OUT_PATH}")

if __name__ == "__main__":
    main()

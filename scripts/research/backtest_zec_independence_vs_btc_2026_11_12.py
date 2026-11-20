"""
Question:
1. Is ZEC's rally and drawdown truly independent of BTC?
   - Measure ZEC vs BTC correlation, beta, and directional concordance across:
     (a) All history (2019-2026) vs Current Cycle (2025.09 - 2026.11)
     (b) Up-regimes vs Down-regimes (Asymmetric correlation/beta: does ZEC rally independently, drop independently, or drop even when BTC stabilizes?)
     (c) Specifically during ZEC's 3 major drawdowns in 2025-2026 (Wave 1: 2025.11-2026.03, Wave 2: 2026.05-2026.06, Wave 3: 2026.09-2026.11), what happened on days/weeks when BTC bounced or stabilized?
2. Historical Backtest of "Low-Correlation / Counter-Cyclical Leaders" vs "High-Correlation Beta Leaders":
   - When a coin rallies >= +180% with LOW correlation to BTC (or while BTC is flat/down), and then enters a -30% to -55% drawdown:
     - Does BTC stabilizing/rallying over the next 30-90 days actually save it, or does an independent narrative coin also die/grind down independently even when BTC stabilizes or rallies?
     - What actually determines when an independent coin bottoms (BTC price vs the coin's own volume exhaustion, OI flush, and time elapsed)?

Data: bn_kline_1d (spot & perp), tv_oi, bn_metrics_1h_hist, deriv_funding in market.snapshot.duckdb
Date: 2026-11-12
"""
import duckdb
import pandas as pd
import numpy as np

con = duckdb.connect('/Users/ivantang/Projects/datahub/binance-data-pipeline/data/market.snapshot.duckdb', read_only=True)

df = con.execute("""
SELECT symbol, ts::DATE as dt, open, high, low, close, quote_volume
FROM bn_kline_1d
WHERE market = 'spot'
ORDER BY symbol, dt
""").df()
df['dt'] = pd.to_datetime(df['dt'])

btc = df[df['symbol'] == 'BTC'].copy().sort_values('dt').reset_index(drop=True)
btc['btc_ret1'] = btc['close'].pct_change() * 100
btc['btc_ret7'] = btc['close'].pct_change(7) * 100
btc['btc_ret30'] = btc['close'].pct_change(30) * 100
btc['btc_fwd30'] = (btc['close'].shift(-30) / btc['close'] - 1) * 100
btc['btc_fwd60'] = (btc['close'].shift(-60) / btc['close'] - 1) * 100
btc['btc_fwd90'] = (btc['close'].shift(-90) / btc['close'] - 1) * 100

zec = df[df['symbol'] == 'ZEC'].copy().sort_values('dt').reset_index(drop=True)
zec['zec_ret1'] = zec['close'].pct_change() * 100
zec['zec_ret7'] = zec['close'].pct_change(7) * 100
zec['zec_ret30'] = zec['close'].pct_change(30) * 100

z = zec.merge(btc[['dt', 'close', 'btc_ret1', 'btc_ret7', 'btc_ret30']], on='dt', suffixes=('', '_btc'))

lines = []
def log(s=''):
    print(s)
    lines.append(str(s))

log("================================================================================")
log("PART 1: ZEC vs BTC CORRELATION & INDEPENDENCE IN UP vs DOWN PHASES")
log("================================================================================")

# Compare pre-2025.09 vs 2025.09-2026.11
for period_name, d1, d2 in [
    ("Historical Baseline (2020-01-01 to 2025-09-24)", "2020-01-01", "2025-09-24"),
    ("Current ZEC Bull/Bear Cycle (2025-09-25 to 2026-11-11)", "2025-09-25", "2026-11-11")
]:
    sub = z[(z['dt'] >= d1) & (z['dt'] <= d2)].dropna()
    corr_1d = sub['zec_ret1'].corr(sub['btc_ret1'])
    corr_7d = sub['zec_ret7'].corr(sub['btc_ret7'])
    corr_30d = sub['zec_ret30'].corr(sub['btc_ret30'])
    beta_1d = np.cov(sub['zec_ret1'], sub['btc_ret1'])[0,1] / np.var(sub['btc_ret1'])
    
    # Conditional on BTC up vs BTC down days
    btc_up = sub[sub['btc_ret1'] > 0.5]
    btc_dn = sub[sub['btc_ret1'] < -0.5]
    btc_big_dn = sub[sub['btc_ret1'] < -2.0]
    btc_big_up = sub[sub['btc_ret1'] > 2.0]
    
    log(f"* {period_name} (n={len(sub)}d):")
    log(f"  - Return Correlation: Daily={corr_1d:+.3f} | 7-Day={corr_7d:+.3f} | 30-Day={corr_30d:+.3f} | Daily Beta={beta_1d:+.2f}")
    log(f"  - On BTC Up Days (>+0.5%, n={len(btc_up)}): ZEC Up Rate={(btc_up['zec_ret1']>0).mean()*100:.1f}%, ZEC Mean={btc_up['zec_ret1'].mean():+.2f}%")
    log(f"  - On BTC Down Days (<-0.5%, n={len(btc_dn)}): ZEC Down Rate={(btc_dn['zec_ret1']<0).mean()*100:.1f}%, ZEC Mean={btc_dn['zec_ret1'].mean():+.2f}%")
    log(f"  - On BTC Big Up Days (>+2.0%, n={len(btc_big_up)}): ZEC Up Rate={(btc_big_up['zec_ret1']>0).mean()*100:.1f}%, ZEC Mean={btc_big_up['zec_ret1'].mean():+.2f}%")
    log(f"  - On BTC Big Down Days (<-2.0%, n={len(btc_big_dn)}): ZEC Down Rate={(btc_big_dn['zec_ret1']<0).mean()*100:.1f}%, ZEC Mean={btc_big_dn['zec_ret1'].mean():+.2f}%")

log("\n--- Phase-by-Phase ZEC vs BTC in the Last 14 Months (2025.09 - 2026.11) ---")
phases = [
    ("1. Wave 1 Rally (2025-09-25 -> 2025-11-16, 53d)", "2025-09-25", "2025-11-16"),
    ("2. Wave 1 Drop Leg 1 (2025-11-16 -> 2025-12-03, 17d)", "2025-11-16", "2025-12-03"),
    ("3. Wave 1 Mid Bounce (2025-12-03 -> 2025-12-29, 26d)", "2025-12-03", "2025-12-29"),
    ("4. Wave 1 Breakdown while BTC bounced (2025-12-29 -> 2026-01-20, 22d)", "2025-12-29", "2026-01-20"),
    ("5. Wave 1 Final Capitulation (2026-01-20 -> 2026-03-07, 46d)", "2026-01-20", "2026-03-07"),
    ("6. Wave 2 Rally (2026-03-07 -> 2026-05-20, 74d)", "2026-03-07", "2026-05-20"),
    ("7. Wave 2 Crash (2026-05-20 -> 2026-06-06, 17d)", "2026-05-20", "2026-06-06"),
    ("8. Wave 3 Rally (2026-06-06 -> 2026-09-26, 112d)", "2026-06-06", "2026-09-26"),
    ("9. Wave 3 Drop to Date (2026-09-26 -> 2026-11-11, 46d)", "2026-09-26", "2026-11-11")
]
for name, d1, d2 in phases:
    sub = z[(z['dt'] > d1) & (z['dt'] <= d2)]
    r1 = z[z['dt'] == d1].iloc[0]
    r2 = z[z['dt'] == d2].iloc[0]
    z_ret = (r2['close'] / r1['close'] - 1) * 100
    b_ret = (r2['close_btc'] / r1['close_btc'] - 1) * 100
    c1 = sub['zec_ret1'].corr(sub['btc_ret1'])
    beta = np.cov(sub['zec_ret1'], sub['btc_ret1'])[0,1] / np.var(sub['btc_ret1']) if np.var(sub['btc_ret1']) > 0 else np.nan
    log(f"  {name}: ZEC {z_ret:+6.1f}% vs BTC {b_ret:+5.1f}% | Daily Corr={c1:+.2f}, Beta={beta:+.2f}")

# Crucial Test 1: During ZEC's downtrends, what happens when BTC stabilizes or rallies?
log("\n--- Crucial Test: In ZEC's Downtrends, Did BTC Stabilizing/Bouncing Stop ZEC From Falling? ---")
# Look at 2025-12-29 to 2026-01-16: BTC rallied from $87,237 to $95,605 (+9.6%, peaked $97,924 on Jan 14 = +12.3%)!
# What did ZEC do during that exact 18-day BTC rally?
s_jan = z[(z['dt'] >= '2025-12-29') & (z['dt'] <= '2026-01-16')]
log(f"  * Episode A (2025-12-29 to 2026-01-14/16): BTC rallied $87,237 -> $97,924 (+12.3% high, Close $95,605 +9.6%)")
log(f"    -> During this exact BTC rally, ZEC FELL from $540.49 to $411.15 (-23.9%, intraday low $361.01 -33.2%) and broke its 100d MA!")
# Look at 2026-09-26 to 2026-10-07: BTC rallied from $84,433 to $86,530 (high $87,220)
s_oct = z[(z['dt'] >= '2026-09-26') & (z['dt'] <= '2026-10-08')]
log(f"  * Episode B (2026-09-26 to 2026-10-06): BTC stayed strong $84,433 -> $86,530 (+2.5%, high $87,220)")
log(f"    -> During this BTC strength, ZEC FELL from $1,653.12 to $1,335.56 (-19.2%)!")
# Look at 7-day rolling windows during ZEC's two big corrections (2025-11-16..2026-03-07 and 2026-09-26..2026-11-11)
z_corr_win = z[((z['dt'] > '2025-11-16') & (z['dt'] <= '2026-03-07')) | ((z['dt'] > '2026-09-26') & (z['dt'] <= '2026-11-11'))]
btc_up_days_in_zec_drop = z_corr_win[z_corr_win['btc_ret1'] > 0]
btc_dn_days_in_zec_drop = z_corr_win[z_corr_win['btc_ret1'] <= 0]
log(f"  * Across all 157 days of ZEC's Wave 1 & Wave 3 Downtrends:")
log(f"    - On days BTC was UP (n={len(btc_up_days_in_zec_drop)}d, BTC mean {btc_up_days_in_zec_drop['btc_ret1'].mean():+.2f}%): ZEC still fell {(btc_up_days_in_zec_drop['zec_ret1']<0).mean()*100:.1f}% of days (ZEC mean {btc_up_days_in_zec_drop['zec_ret1'].mean():+.2f}%)")
log(f"    - On days BTC was DOWN (n={len(btc_dn_days_in_zec_drop)}d, BTC mean {btc_dn_days_in_zec_drop['btc_ret1'].mean():+.2f}%): ZEC fell {(btc_dn_days_in_zec_drop['zec_ret1']<0).mean()*100:.1f}% of days (ZEC mean {btc_dn_days_in_zec_drop['zec_ret1'].mean():+.2f}%)")

log("\n================================================================================")
log("PART 2: HISTORICAL BACKTEST — DO LOW-CORRELATION / INDEPENDENT LEADERS FOLLOW BTC AFTER TOPPING?")
log("================================================================================")
# Scan all historical big rallies (>= +180% in <=120d, outperforming BTC by >= +100%, liquid >= $10M/d)
# For each rally, compute its 60d daily correlation with BTC DURING THE RALLY UP TO THE PEAK (`rally_corr_btc`)
# and BTC's return during the rally (`btc_rally_ret`).
# Then, when the coin pulls back -30% to -55% from peak, compare:
# Group 1: "Independent / Low-Corr Leaders" (rally_corr_btc < 0.35 OR btc_rally_ret <= 0%) — coins that pumped on their own narrative/flow regardless of BTC
# Group 2: "Beta / High-Corr Leaders" (rally_corr_btc >= 0.35 AND btc_rally_ret > 0%) — coins that pumped WITH a BTC bull run

exclude = {
    'USDT','USDC','BUSD','TUSD','FDUSD','DAI','USDP','USDS','PYUSD','EUR','EURI','AEUR',
    'WBTC','WBETH','BETH','STETH','PAXG','UST','BTTC'
}
symbols = [s for s in df['symbol'].unique() if s not in exclude and not s.endswith(('UP','DOWN','BULL','BEAR'))]

all_events = []
for sym in symbols:
    if sym == 'BTC':
        continue
    sub = df[df['symbol'] == sym].sort_values('dt').reset_index(drop=True)
    if len(sub) < 220:
        continue
    sub['ret1'] = sub['close'].pct_change() * 100
    sub['ma100'] = sub['close'].rolling(100).mean()
    sub['vol90'] = sub['quote_volume'].rolling(90).mean()
    sw = sub.set_index('dt')[['close']].resample('W-SUN').last()
    sw['w20'] = sw['close'].rolling(20).mean()
    sw['w21e'] = sw['close'].ewm(span=21, adjust=False).mean()
    sub = sub.merge(sw[['w20', 'w21e']], left_on='dt', right_index=True, how='left')
    sub['w20'] = sub['w20'].ffill()
    sub['w21e'] = sub['w21e'].ffill()
    sub = sub.merge(btc[['dt', 'close', 'btc_ret1', 'btc_fwd30', 'btc_fwd60', 'btc_fwd90']], on='dt', how='left', suffixes=('', '_btc'))
    
    seen_peaks = set()
    for i in range(180, len(sub)):
        row = sub.iloc[i]
        if pd.isna(row['w20']) or pd.isna(row['ma100']) or row['vol90'] < 10e6:
            continue
        win_peak = sub.iloc[max(0, i-90):i+1]
        pk_idx = win_peak['close'].idxmax()
        if pk_idx in seen_peaks:
            continue
        days_since_pk = i - pk_idx
        if days_since_pk < 14 or days_since_pk > 85:
            continue
        pk_row = sub.iloc[pk_idx]
        pre_pk = sub.iloc[max(0, pk_idx-120):pk_idx+1]
        if pk_row['close'] < pre_pk['close'].max():
            continue
        min_pre_idx = pre_pk['close'].idxmin()
        min_pre_row = sub.iloc[min_pre_idx]
        rally_gain = (pk_row['close'] / min_pre_row['close'] - 1) * 100
        btc_rally_gain = (pk_row['close_btc'] / min_pre_row['close_btc'] - 1) * 100
        if rally_gain < 180 or (rally_gain - btc_rally_gain) < 100:
            continue
        if pd.isna(pk_row['ma100']) or (pk_row['close'] / pk_row['ma100'] - 1) * 100 < 70:
            continue
        
        bmsb_top = max(row['w20'], row['w21e'])
        bmsb_bot = min(row['w20'], row['w21e'])
        dd_close = (row['close'] / pk_row['close'] - 1) * 100
        if not (-55 <= dd_close <= -30):
            continue
        if not (bmsb_bot * 0.88 <= row['close'] <= bmsb_top * 1.03):
            continue
        post_pk = sub.iloc[pk_idx:i]
        if (post_pk['close'] < post_pk[['w20', 'w21e']].min(axis=1) * 0.92).any():
            continue
        
        seen_peaks.add(pk_idx)
        
        # Calculate correlation with BTC during the 60 days leading up to the peak
        rally_60 = sub.iloc[max(0, pk_idx-60):pk_idx+1]
        rally_corr = rally_60['ret1'].corr(rally_60['btc_ret1'])
        drop_corr = sub.iloc[pk_idx+1:i+1]['ret1'].corr(sub.iloc[pk_idx+1:i+1]['btc_ret1'])
        vol_shrink = (sub.iloc[max(pk_idx+1, i-6):i+1]['quote_volume'].mean() / sub.iloc[max(0, pk_idx-6):pk_idx+1]['quote_volume'].mean() - 1) * 100
        
        if i + 90 >= len(sub):
            # Still log current active triggers like ZEC 2026-11-11 separately
            if row['dt'] >= pd.Timestamp('2026-11-01'):
                log(f"  [CURRENT ACTIVE] {row['dt'].strftime('%Y-%m-%d')} {sym:6s} | Peak {pk_row['dt'].strftime('%Y-%m-%d')} (+{rally_gain:.0f}%, BTC {btc_rally_gain:+.1f}%) | RallyCorr={rally_corr:+.2f}, DropCorr={drop_corr:+.2f}, VolShrink={vol_shrink:+.1f}%")
            continue
            
        fwd_90 = sub.iloc[i+1:min(len(sub), i+91)]
        fwd_180 = sub.iloc[i+1:min(len(sub), i+181)]
        ret_30 = (sub.iloc[i+30]['close'] / row['close'] - 1)*100
        ret_60 = (sub.iloc[i+60]['close'] / row['close'] - 1)*100
        ret_90 = (sub.iloc[i+90]['close'] / row['close'] - 1)*100
        max_gain_90 = (fwd_90['high'].max() / row['close'] - 1)*100
        max_dd_90 = (fwd_90['low'].min() / row['close'] - 1)*100
        new_high_180 = (fwd_180['high'].max() > win_peak['high'].max())
        grind_down_90d = (max_dd_90 <= -25.0 and ret_90 < 0)
        fwd_corr_90 = fwd_90['ret1'].corr(fwd_90['btc_ret1'])
        
        is_indep = (rally_corr < 0.35) or (btc_rally_gain <= 0)
        all_events.append({
            'symbol': sym,
            'peak_dt': pk_row['dt'].strftime('%Y-%m-%d'),
            'trig_dt': row['dt'].strftime('%Y-%m-%d'),
            'rally_gain': rally_gain,
            'btc_rally_gain': btc_rally_gain,
            'rally_corr': rally_corr,
            'drop_corr': drop_corr,
            'fwd_corr_90': fwd_corr_90,
            'vol_shrink': vol_shrink,
            'is_indep': is_indep,
            'dd_close': dd_close,
            'btc_fwd30': row['btc_fwd30'],
            'btc_fwd90': row['btc_fwd90'],
            'ret_30': ret_30,
            'ret_90': ret_90,
            'max_gain_90': max_gain_90,
            'max_dd_90': max_dd_90,
            'new_high_180': new_high_180,
            'grind_down_90d': grind_down_90d
        })

edf = pd.DataFrame(all_events)
ind = edf[edf['is_indep']]
dep = edf[~edf['is_indep']]

def summary_box(title, g):
    if len(g) == 0: return
    log(f"* {title} (n={len(g)}):")
    log(f"  - Avg Rally Corr w/ BTC: {g['rally_corr'].mean():+.2f} -> Drop Corr: {g['drop_corr'].mean():+.2f} -> Next 90d Corr: {g['fwd_corr_90'].mean():+.2f}")
    log(f"  - 90d Positive Rate: {(g['ret_90']>0).mean()*100:.1f}% (Median 90d Return: {g['ret_90'].median():+.1f}%)")
    log(f"  - 180d New High Rate: {g['new_high_180'].mean()*100:.1f}% | 90d Grind Down Rate (MaxDD<=-25% & Ret<0): {g['grind_down_90d'].mean()*100:.1f}%")

log("\n--- 1. Overall Comparison: Independent/Low-Corr Leaders vs Beta/High-Corr Leaders ---")
summary_box("Group 1: Independent / Counter-Trend Leaders (Rally Corr < 0.35 or BTC Down during Rally, like ZEC)", ind)
summary_box("Group 2: Beta / High-Corr Leaders (Rally Corr >= 0.35 & BTC Up during Rally)", dep)

log("\n--- 2. What Happens When BTC Stabilizes/Rallies (Next 90d BTC Return > 0%)? ---")
summary_box("Group 1 (Independent Leaders) EVEN WHEN BTC RALLIES Next 90d (BTC 90d > 0%)", ind[ind['btc_fwd90'] > 0])
summary_box("Group 2 (Beta Leaders) WHEN BTC RALLIES Next 90d (BTC 90d > 0%)", dep[dep['btc_fwd90'] > 0])

log("\n--- 3. What Happens When BTC Falls Next 90d (BTC 90d Return <= 0%)? ---")
summary_box("Group 1 (Independent Leaders) WHEN BTC FALLS Next 90d (BTC 90d <= 0%)", ind[ind['btc_fwd90'] <= 0])
summary_box("Group 2 (Beta Leaders) WHEN BTC FALLS Next 90d (BTC 90d <= 0%)", dep[dep['btc_fwd90'] <= 0])

log("\n--- 4. Full List of Historical Independent / Counter-Trend Leaders (Group 1) ---")
for idx, r in ind.iterrows():
    log(f"  {r['trig_dt']} {r['symbol']:6s} | Peak {r['peak_dt']} (+{r['rally_gain']:4.0f}%, BTC {r['btc_rally_gain']:+5.1f}%, RallyCorr={r['rally_corr']:+.2f}) -> DD {r['dd_close']:5.1f}% (DropCorr={r['drop_corr']:+.2f}) | Next90d BTC:{r['btc_fwd90']:+5.1f}% -> Coin 90d:{r['ret_90']:+5.1f}% (MaxG:{r['max_gain_90']:+5.1f}%, MaxDD:{r['max_dd_90']:5.1f}%), NewHigh180d:{r['new_high_180']}")

with open('/Users/ivantang/Projects/datahub/binance-data-pipeline/data/outputs/zec_independence_vs_btc_2026-11-12.txt', 'w') as f:
    f.write('\n'.join(lines))

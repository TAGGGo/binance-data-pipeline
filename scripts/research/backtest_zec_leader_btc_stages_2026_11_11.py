"""
Backtest & Historical Analog Study:
1. Which historical coins had a 1-year trajectory most similar to ZEC (2025.10 - 2026.11)?
   - Specifically: LINK (2019-2020), BNB (2019), SOL (2021 & 2023-2024), INJ (2023-2024), FET (2023-2024).
2. Across all 2018-2026 historical events where a leading coin (+180% prior rally, >=+100% excess over BTC) pulled back 30%-55% to test its 20W SMA / 21W EMA (BMSB), what is the exact statistical breakdown of:
   - Path A: Bottoming & breaking out to a new high (or +50% major rally) within 90-180d
   - Path B: Breaking BMSB and grinding lower ("慢慢找底", >=-25% further drawdown & 90d return < 0)
   - How does this depend on:
     (1) BTC's ex-ante macro cycle stage (Topping/Early Bear vs Deep Bear vs Post-Bear Recovery like today)
     (2) BTC's position relative to its 20W SMA & 200d MA
     (3) Whether BTC holds or loses its own support over the next 30 days?

Data: bn_kline_1d (spot, all symbols, 2017-2026) in market.snapshot.duckdb
Date: 2026-11-11
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
btc['btc_ma50'] = btc['close'].rolling(50).mean()
btc['btc_ma100'] = btc['close'].rolling(100).mean()
btc['btc_ma200'] = btc['close'].rolling(200).mean()
btc['btc_ma200_slope20'] = (btc['btc_ma200'] / btc['btc_ma200'].shift(20) - 1) * 100
btc['btc_high90'] = btc['high'].rolling(90).max()
btc['btc_dd90'] = (btc['close'] / btc['btc_high90'] - 1) * 100
btc['btc_high365'] = btc['high'].rolling(365, min_periods=60).max()
btc['btc_dd365'] = (btc['close'] / btc['btc_high365'] - 1) * 100

# Ex-ante cycle ATH tracking (rolling 730d high, days since that high, and max drawdown experienced since that high up to day t)
ath_days = []
max_dd_after_ath = []
high_vals = btc['high'].values
low_vals = btc['low'].values
for i in range(len(btc)):
    start_w = max(0, i - 730)
    win_h = high_vals[start_w:i+1]
    pk_rel = int(np.argmax(win_h))
    pk_idx = start_w + pk_rel
    days_since = i - pk_idx
    min_low_since = np.min(low_vals[pk_idx:i+1])
    mdd = (min_low_since / high_vals[pk_idx] - 1.0) * 100.0
    ath_days.append(days_since)
    max_dd_after_ath.append(mdd)

btc['days_since_ath'] = ath_days
btc['max_dd_since_ath'] = max_dd_after_ath
btc['btc_high730'] = btc['high'].rolling(730, min_periods=60).max()
btc['btc_dd_ath'] = (btc['close'] / btc['btc_high730'] - 1) * 100

btc_w = btc.set_index('dt')[['close']].resample('W-SUN').last()
btc_w['btc_w20'] = btc_w['close'].rolling(20).mean()
btc_w['btc_w21e'] = btc_w['close'].ewm(span=21, adjust=False).mean()
btc = btc.merge(btc_w[['btc_w20', 'btc_w21e']], left_on='dt', right_index=True, how='left')
btc['btc_w20'] = btc['btc_w20'].ffill()
btc['btc_w21e'] = btc['btc_w21e'].ffill()
btc['btc_vs_w20'] = (btc['close'] / btc['btc_w20'] - 1) * 100
btc['btc_vs_ma200'] = (btc['close'] / btc['btc_ma200'] - 1) * 100
btc['btc_fwd30'] = (btc['close'].shift(-30) / btc['close'] - 1) * 100
btc['btc_fwd60'] = (btc['close'].shift(-60) / btc['close'] - 1) * 100
btc['btc_fwd90'] = (btc['close'].shift(-90) / btc['close'] - 1) * 100

exclude = {
    'USDT','USDC','BUSD','TUSD','FDUSD','DAI','USDP','USDS','PYUSD','EUR','EURI','AEUR',
    'WBTC','WBETH','BETH','STETH','PAXG','UST','BTTC'
}
symbols = [s for s in df['symbol'].unique() if s not in exclude and not s.endswith(('UP','DOWN','BULL','BEAR'))]

lines = []
def log(s=''):
    print(s)
    lines.append(str(s))

log("================================================================================")
log("SECTION 1: CLOSEST 1-YEAR STRUCTURAL ANALOGS TO ZEC (2025.10 - 2026.11)")
log("================================================================================")

def inspect_coin_episode(sym, d_start, d_peak1, d_trough1, d_peak2, d_test2, d_after):
    sub = df[df['symbol'] == sym].sort_values('dt').reset_index(drop=True)
    sub['ma100'] = sub['close'].rolling(100).mean()
    sub['ma200'] = sub['close'].rolling(200).mean()
    sw = sub.set_index('dt')[['close']].resample('W-SUN').last()
    sw['w20'] = sw['close'].rolling(20).mean()
    sw['w21e'] = sw['close'].ewm(span=21, adjust=False).mean()
    sub = sub.merge(sw[['w20', 'w21e']], left_on='dt', right_index=True, how='left')
    sub['w20'] = sub['w20'].ffill()
    sub['w21e'] = sub['w21e'].ffill()
    sub = sub.merge(btc[['dt', 'close', 'btc_ma200', 'btc_w20', 'btc_vs_w20', 'btc_vs_ma200', 'btc_dd_ath', 'days_since_ath', 'max_dd_since_ath']], on='dt', how='left', suffixes=('', '_btc'))
    
    def pt(dt_s):
        r = sub[sub['dt'] >= dt_s].iloc[0]
        return r
    r0, rp1, rt1, rp2, rtest, raft = pt(d_start), pt(d_peak1), pt(d_trough1), pt(d_peak2), pt(d_test2), pt(d_after)
    w_after = sub[(sub['dt'] > rtest['dt']) & (sub['dt'] <= raft['dt'])]
    log(f"--- {sym} ({d_start} to {d_after}) ---")
    log(f"  Wave 1: {r0['dt'].strftime('%Y-%m-%d')} ${r0['close']:.4g} -> {rp1['dt'].strftime('%Y-%m-%d')} ${rp1['close']:.4g} (+{(rp1['close']/r0['close']-1)*100:.0f}%) | Trough 1: {rt1['dt'].strftime('%Y-%m-%d')} ${rt1['close']:.4g} ({(rt1['close']/rp1['close']-1)*100:.1f}%)")
    log(f"  Wave 2/3 Peak: {rp2['dt'].strftime('%Y-%m-%d')} Close=${rp2['close']:.4g} (High=${rp2['high']:.4g}, +{(rp2['close']/rt1['close']-1)*100:.0f}% from Trough 1)")
    log(f"  BMSB Test Date: {rtest['dt'].strftime('%Y-%m-%d')} Close=${rtest['close']:.4g} (Low=${rtest['low']:.4g}, {(rtest['close']/rp2['close']-1)*100:.1f}% vs Peak Close) | vs 20W_SMA=${rtest['w20']:.4g} ({(rtest['close']/rtest['w20']-1)*100:+.1f}%), vs 100dMA=${rtest['ma100']:.4g} ({(rtest['close']/rtest['ma100']-1)*100:+.1f}%)")
    log(f"  BTC at BMSB Test: Close=${rtest['close_btc']:.0f} (DaysSinceATH={rtest['days_since_ath']}d, MaxBearDD={rtest['max_dd_since_ath']:.1f}%, CurDD={rtest['btc_dd_ath']:.1f}%) | BTC vs 20W_SMA: {rtest['btc_vs_w20']:+.1f}%, vs 200dMA: {rtest['btc_vs_ma200']:+.1f}%")
    if len(w_after) > 0:
        max_h = w_after['high'].max()
        max_h_dt = w_after.loc[w_after['high'].idxmax(), 'dt'].strftime('%Y-%m-%d')
        min_l = w_after['low'].min()
        min_l_dt = w_after.loc[w_after['low'].idxmin(), 'dt'].strftime('%Y-%m-%d')
        log(f"  Subsequent Outcome (to {raft['dt'].strftime('%Y-%m-%d')}): End Close=${raft['close']:.4g} ({(raft['close']/rtest['close']-1)*100:+.1f}%) | Max High=${max_h:.4g} on {max_h_dt} ({(max_h/rtest['close']-1)*100:+.1f}%) | Min Low=${min_l:.4g} on {min_l_dt} ({(min_l/rtest['close']-1)*100:+.1f}%)")
        log(f"  BTC Outcome (to {raft['dt'].strftime('%Y-%m-%d')}): ${rtest['close_btc']:.0f} -> ${raft['close_btc']:.0f} ({(raft['close_btc']/rtest['close_btc']-1)*100:+.1f}%)")

inspect_coin_episode('LINK', '2019-04-01', '2019-06-29', '2019-09-16', '2019-11-11', '2019-12-18', '2020-02-19')
inspect_coin_episode('LINK', '2019-09-16', '2020-03-04', '2020-03-16', '2020-08-16', '2020-09-23', '2021-01-15')
inspect_coin_episode('BNB', '2018-12-07', '2019-04-20', '2019-05-09', '2019-06-22', '2019-08-15', '2019-12-18')
inspect_coin_episode('SOL', '2021-01-01', '2021-05-18', '2021-07-20', '2021-09-08', '2021-09-21', '2021-11-06')
inspect_coin_episode('SOL', '2021-07-20', '2021-09-08', '2021-09-21', '2021-11-06', '2021-12-13', '2022-03-15')
inspect_coin_episode('SOL', '2022-12-29', '2023-04-17', '2023-06-10', '2023-07-14', '2023-09-11', '2023-12-25')
inspect_coin_episode('SOL', '2023-09-11', '2023-12-25', '2024-01-23', '2024-03-18', '2024-04-30', '2024-11-22')
inspect_coin_episode('INJ', '2023-01-01', '2023-04-17', '2023-06-10', '2023-07-14', '2023-08-17', '2023-12-24')
inspect_coin_episode('INJ', '2023-08-17', '2023-12-24', '2024-01-23', '2024-03-13', '2024-04-13', '2024-08-05')
inspect_coin_episode('FET', '2022-11-21', '2023-02-07', '2023-03-11', '2023-04-14', '2023-05-01', '2023-08-17')
inspect_coin_episode('ZEC', '2025-09-25', '2025-11-16', '2026-03-07', '2026-09-26', '2026-11-11', '2026-11-12')

# Section 2: Systematic backtest across all symbols
events = []
for sym in symbols:
    if sym == 'BTC':
        continue
    sub = df[df['symbol'] == sym].sort_values('dt').reset_index(drop=True)
    if len(sub) < 220:
        continue
    sub['ma50'] = sub['close'].rolling(50).mean()
    sub['ma100'] = sub['close'].rolling(100).mean()
    sub['ma200'] = sub['close'].rolling(200).mean()
    sub['vol90'] = sub['quote_volume'].rolling(90).mean()
    
    sw = sub.set_index('dt')[['close']].resample('W-SUN').last()
    sw['w20'] = sw['close'].rolling(20).mean()
    sw['w21e'] = sw['close'].ewm(span=21, adjust=False).mean()
    sub = sub.merge(sw[['w20', 'w21e']], left_on='dt', right_index=True, how='left')
    sub['w20'] = sub['w20'].ffill()
    sub['w21e'] = sub['w21e'].ffill()
    sub = sub.merge(btc[['dt', 'close', 'btc_ma200', 'btc_ma200_slope20', 'btc_w20', 'btc_w21e', 'btc_vs_w20', 'btc_vs_ma200', 'btc_dd90', 'btc_dd365', 'btc_dd_ath', 'days_since_ath', 'max_dd_since_ath', 'btc_fwd30', 'btc_fwd60', 'btc_fwd90']], on='dt', how='left', suffixes=('', '_btc'))
    
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
        if i + 90 >= len(sub):
            continue
        fwd_90 = sub.iloc[i+1:min(len(sub), i+91)]
        fwd_180 = sub.iloc[i+1:min(len(sub), i+181)]
        ret_30 = (sub.iloc[i+30]['close'] / row['close'] - 1)*100
        ret_60 = (sub.iloc[i+60]['close'] / row['close'] - 1)*100
        ret_90 = (sub.iloc[i+90]['close'] / row['close'] - 1)*100
        max_gain_90 = (fwd_90['high'].max() / row['close'] - 1)*100
        max_dd_90 = (fwd_90['low'].min() / row['close'] - 1)*100
        max_gain_180 = (fwd_180['high'].max() / row['close'] - 1)*100
        max_dd_180 = (fwd_180['low'].min() / row['close'] - 1)*100
        new_high_180 = (fwd_180['high'].max() > win_peak['high'].max())
        rebound_50_90d = (max_gain_90 >= 50.0)
        grind_down_90d = (max_dd_90 <= -25.0 and ret_90 < 0)
        
        # Ex-ante BTC Macro Stage classification:
        # Stage 1 (Post-Bear Recovery / Mid-Cycle Re-accumulation, like Today Nov 2026, or 2019.08, 2020.09, 2023.03-09):
        #   BTC already experienced >=45% bear drawdown from 2-year ATH, is >200 days past 2-year ATH, and is near/above 200d MA (btc_vs_ma200 >= -10%)
        # Stage 2 (Bull High / Cycle Top Distribution, like Late 2021, Mar-Jun 2024, Dec 2024-Feb 2025):
        #   BTC has NOT had a >=45% bear drawdown since its 2-year ATH (or is <180d from ATH) and is still near/above 200d MA (btc_vs_ma200 >= -10%)
        # Stage 3 (Bear Breakdown / Deep Bear below 200d MA, like May-Jun 2021 crash, Jan-Sep 2022, Jan-Jun 2026):
        #   BTC is >10% below its 200d MA (btc_vs_ma200 < -10%)
        if row['btc_vs_ma200'] < -10:
            stage = '3_Bear_Below_200dMA'
        elif row['max_dd_since_ath'] <= -45 and row['days_since_ath'] >= 200:
            stage = '1_PostBear_Recovery_Above200dMA (Like Today)'
        else:
            stage = '2_BullHigh_Distribution_Above200dMA'
            
        events.append({
            'symbol': sym,
            'peak_dt': pk_row['dt'].strftime('%Y-%m-%d'),
            'trig_dt': row['dt'].strftime('%Y-%m-%d'),
            'stage': stage,
            'rally_gain': rally_gain,
            'dd_close': dd_close,
            'btc_vs_w20': row['btc_vs_w20'],
            'btc_vs_ma200': row['btc_vs_ma200'],
            'btc_dd90': row['btc_dd90'],
            'btc_dd_ath': row['btc_dd_ath'],
            'days_since_ath': row['days_since_ath'],
            'max_dd_since_ath': row['max_dd_since_ath'],
            'btc_fwd30': row['btc_fwd30'],
            'btc_fwd90': row['btc_fwd90'],
            'ret_30': ret_30,
            'ret_60': ret_60,
            'ret_90': ret_90,
            'max_gain_90': max_gain_90,
            'max_dd_90': max_dd_90,
            'new_high_180': new_high_180,
            'rebound_50_90d': rebound_50_90d,
            'grind_down_90d': grind_down_90d
        })

edf = pd.DataFrame(events)
log("\n================================================================================")
log(f"SECTION 2: BACKTEST OF LEADER COINS PULLING BACK 30-55% TO BMSB (n={len(edf)} completed events)")
log("================================================================================")

def report_group(name, g):
    if len(g) == 0:
        return
    nh = g['new_high_180'].mean()*100
    rb50 = g['rebound_50_90d'].mean()*100
    gd = g['grind_down_90d'].mean()*100
    pos90 = (g['ret_90'] > 0).mean()*100
    log(f"* {name} (n={len(g)}):")
    log(f"  - 90d Positive Return Rate: {pos90:.1f}% (Median 90d Return: {g['ret_90'].median():+.1f}%, Mean: {g['ret_90'].mean():+.1f}%)")
    log(f"  - 90d Rebound >= +50% Rate: {rb50:.1f}% | 180d Break Above Prior Peak (New High) Rate: {nh:.1f}%")
    log(f"  - 90d Grind Down (MaxDD <= -25% & 90d < 0) Rate: {gd:.1f}% (Median 90d MaxGain: {g['max_gain_90'].median():+.1f}%, Median 90d MaxDD: {g['max_dd_90'].median():.1f}%)")

report_group("ALL HISTORICAL EVENTS (2018-2026)", edf)

log("\n--- Split by Ex-Ante BTC Macro Cycle Stage ---")
for st in sorted(edf['stage'].unique()):
    report_group(st, edf[edf['stage'] == st])

log("\n--- Every Historical Event in Stage 1 (Post-Bear Recovery Above/Near 200dMA, Like Today Nov 2026) ---")
s1 = edf[edf['stage'].str.startswith('1_')]
for idx, r in s1.iterrows():
    log(f"  {r['trig_dt']} {r['symbol']:6s} | Peak {r['peak_dt']} (+{r['rally_gain']:.0f}%) -> DD {r['dd_close']:.1f}% | BTC_vs_20W:{r['btc_vs_w20']:+.1f}%, BTC_vs_200d:{r['btc_vs_ma200']:+.1f}%, BTC_fwd30:{r['btc_fwd30']:+.1f}% | +90d:{r['ret_90']:+.1f}%, 90dMaxG:{r['max_gain_90']:+.1f}%, 90dMaxDD:{r['max_dd_90']:.1f}%, NewHigh180d:{r['new_high_180']}")

log("\n--- Split by What BTC Does Next 30 Days (Conditional Analysis) ---")
report_group("A. If BTC Holds / Rallies over Next 30d (BTC 30d return >= 0%)", edf[edf['btc_fwd30'] >= 0])
report_group("B. If BTC Mildly Dips over Next 30d (-10% <= BTC 30d return < 0%)", edf[edf['btc_fwd30'].between(-10, -0.001)])
report_group("C. If BTC Breaks Down over Next 30d (BTC 30d return < -10%)", edf[edf['btc_fwd30'] < -10])

with open('/Users/ivantang/Projects/datahub/binance-data-pipeline/data/outputs/zec_leader_btc_stages_2026-11-11.txt', 'w') as f:
    f.write('\n'.join(lines))

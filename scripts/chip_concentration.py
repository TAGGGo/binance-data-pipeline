"""Murphy's 筹码集中度 = % of BTC supply whose URPD cost is within +-PCT of price.
Reads data/onchain/bgeometrics/urpd/<date>.csv ($2k bins, from 2026-08-11) + btc-price.csv.
Bins partially inside the band count pro rata. Matches Murphy within ~0.2 pt
(10/12: 13.6 vs his 13.8; 10/3: 11.9 vs 12; Aug peak 15.1 vs 15.2).
Usage: python3 scripts/chip_concentration.py [--pct 5] [--last 20] [--price 83000]
"""
import argparse, glob, os, pandas as pd
ROOT = os.path.join(os.path.dirname(__file__), '..', 'data', 'onchain', 'bgeometrics')
ap = argparse.ArgumentParser(); ap.add_argument('--pct', type=float, default=5)
ap.add_argument('--last', type=int, default=20); ap.add_argument('--price', type=float, help='use this price for the latest snapshot (e.g. live)')
a = ap.parse_args()
px = pd.read_csv(os.path.join(ROOT, 'btc-price.csv')); px = dict(zip(px.iloc[:, 0], px.iloc[:, 1]))
files = sorted(glob.glob(os.path.join(ROOT, 'urpd', '*.csv')))
for i, f in enumerate(files[-a.last:]):
    u = pd.read_csv(f); d = u.theDate.iloc[0]
    p = a.price if (a.price and f == files[-1]) else px.get(d)
    if p is None: continue
    lo, hi = p * (1 - a.pct / 100), p * (1 + a.pct / 100)
    ov = (u.priceUpper.clip(upper=hi) - u.priceLower.clip(lower=lo)).clip(lower=0) / (u.priceUpper - u.priceLower)
    top = u[u.priceLower > 0].nlargest(1, 'btcSupply').iloc[0]
    print(f"{d}  price {p:>8,.0f}  conc {(ov * u.pctSupply).sum():5.1f}%  top bar {int(top.priceLower/1000)}-{int(top.priceUpper/1000)}k {top.btcSupply/1e6:.2f}M")

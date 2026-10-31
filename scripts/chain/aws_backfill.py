"""Hourly: keep data/chain/raw (see aws_extract.py) current and backfill history, newest first.
1. Re-pull today every run; yesterday until a copy exists that was written >= 02:00 UTC today (late blocks settled).
2. Backfill older days, newest first, down to MDH_CHAIN_FLOOR (default 2017-01-01; was 2024-10-01 before the external drive), skipping days already done,
   until MDH_CHAIN_BUDGET seconds (default 2100; was 1500 until the 2017 backfill, 10/29) are used. Stops if the disk has < MDH_CHAIN_MIN_FREE_GB (default 30) free.
Data on the external drive /Volumes/chain (1 TB) since 2026-10-29; full history est. ~150 GB.
"""
import os, sys, time, shutil, datetime as dt
sys.path.insert(0, os.path.dirname(__file__))
import aws_extract as ax

budget = float(os.environ.get('MDH_CHAIN_BUDGET', 2100))
floor = dt.date.fromisoformat(os.environ.get('MDH_CHAIN_FLOOR', '2017-01-01'))
min_free = float(os.environ.get('MDH_CHAIN_MIN_FREE_GB', 30)) * 1e9
t0 = time.time()
today = dt.datetime.utcnow().date()
done_new = 0
# today: every run. yesterday: only until we have a copy written >= 2h after it ended (late blocks settled), then never again.
settle = dt.datetime.combine(today, dt.time(2, 0), tzinfo=dt.timezone.utc).timestamp()
y = today - dt.timedelta(days=1)
yf = ax.ROOT / 'inputs' / f'date={y}.parquet'
if __name__ == '__main__':
    for d in ([today] + ([y] if not yf.exists() or yf.stat().st_mtime < settle else [])):
        try:
            ax.day(d.isoformat(), force=True)
        except Exception as e:
            print('chain: failed', d, repr(e)[:200])

# backfill in MDH_CHAIN_WORKERS parallel processes (default 3; was 1 until 10/31, Avalon: 放开一些)
def one(ds):
    import aws_extract as ax2
    t = time.time()
    try: ax2.day(ds)
    except Exception as e: raise RuntimeError(f'{ds} {e!r}'[:300])
    return ds, time.time() - t

if __name__ == '__main__':
    from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
    workers = int(os.environ.get('MDH_CHAIN_WORKERS', 3))
    d = today - dt.timedelta(days=2)
    def todo():
        global d
        while d >= floor:
            ds = d.isoformat(); d -= dt.timedelta(days=1)
            if not (ax.ROOT / 'inputs' / f'date={ds}.parquet').exists(): return ds
        return None
    with ProcessPoolExecutor(workers) as ex:
        running = set()
        while True:
            while len(running) < workers and time.time() - t0 < budget and shutil.disk_usage(ax.ROOT).free >= min_free:
                ds = todo()
                if ds is None: break
                running.add(ex.submit(one, ds))
            if not running: break
            fin, running = wait(running, return_when=FIRST_COMPLETED)
            for f in fin:
                try: f.result(); done_new += 1
                except Exception as e: print('chain: failed', repr(e)[:200])
    if shutil.disk_usage(ax.ROOT).free < min_free: print('chain: disk below limit, stopping')
if __name__ == '__main__':
    have = sorted(p.name[5:15] for p in (ax.ROOT / 'inputs').glob('date=*.parquet'))
    size = sum(p.stat().st_size for p in ax.ROOT.rglob('*.parquet'))
    print(f'chain: backfilled {done_new} days in {time.time()-t0:.0f}s; have {len(have)} days {have[0] if have else "-"}..{have[-1] if have else "-"}, {size/1e9:.1f} GB, free {shutil.disk_usage(ax.ROOT).free/1e9:.0f} GB')

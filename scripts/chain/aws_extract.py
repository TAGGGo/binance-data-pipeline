"""Raw on-chain table, pulled from AWS Open Data (s3://aws-public-blockchain/v1.0/btc/transactions/, anonymous, free).
One UTC day -> data/chain/raw/{tx,outputs,inputs}/date=D.parquet (zstd). Reads only the columns we need (no scripts/witness).
Compact keys: txid -> int64 (first 16 hex chars), address -> int64 (blake2b-8 of the address string; 0 = no address).
Match labels by hashing them the same way: addr_key("bc1q...").
Usage: python3 aws_extract.py 2026-10-27 [more days]   (skips days already done unless --force)
"""
import sys, time, pathlib, hashlib
import numpy as np
import pyarrow as pa, pyarrow.parquet as pq, pyarrow.compute as pc, pyarrow.fs as fs

BUCKET = 'aws-public-blockchain/v1.0/btc/transactions'
# Data lives on the external drive "chain" since 2026-10-29 (Mac disk too small). MDH_CHAIN_DIR overrides
# (the Cowork VM sees the drive at ~/mnt/chain/datahub/chain). Refuse to run if the drive is missing,
# so we never silently start a second copy on the Mac disk.
import os
CHAIN_DIR = pathlib.Path(os.environ.get('MDH_CHAIN_DIR', '/Volumes/chain/datahub/chain'))
if not CHAIN_DIR.is_dir():
    raise SystemExit(f'chain: data folder {CHAIN_DIR} not found (external drive "chain" not plugged in?)')
ROOT = CHAIN_DIR / 'raw'
COLS = ['txid', 'block_number', 'index', 'block_timestamp', 'is_coinbase', 'fee',
        'outputs.list.element.address', 'outputs.list.element.index', 'outputs.list.element.type', 'outputs.list.element.value',
        'inputs.list.element.address', 'inputs.list.element.value', 'inputs.list.element.type',
        'inputs.list.element.spent_transaction_hash', 'inputs.list.element.spent_output_index']
s3 = fs.S3FileSystem(anonymous=True, region='us-east-2', connect_timeout=10, request_timeout=30)  # timeouts: a stuck connection must not hang the hourly run
_HEX = np.full(256, 0, np.uint64)
for i, ch in enumerate(b'0123456789abcdef'):
    _HEX[ch] = i

def addr_key(a):
    return int.from_bytes(hashlib.blake2b(a.encode(), digest_size=8).digest(), 'little', signed=True) if a else 0

def tx_key(arr):
    """first 16 hex chars of a 64-char txid -> int64 (vectorised)."""
    arr = pc.cast(arr, pa.string()).combine_chunks() if isinstance(arr, pa.ChunkedArray) else arr
    s = np.asarray(pc.utf8_slice_codeunits(arr, 0, 16).to_pylist(), dtype='S16')
    b = np.frombuffer(s.tobytes(), np.uint8).reshape(-1, 16)
    v = np.zeros(len(b), np.uint64)
    for j in range(16):
        v = (v << np.uint64(4)) | _HEX[b[:, j]]
    return pa.array(v.view(np.int64))

def akeys(arr):
    return pa.array([addr_key(a) for a in arr.to_pylist()], pa.int64())

def sats(a):
    return pc.cast(pc.round(pc.multiply(pc.fill_null(a, 0.0), 1e8)), pa.int64())

def explode(t, side, keep):
    lst = t.column(side).combine_chunks()
    flat = pc.list_flatten(lst)
    base = t.select(keep).take(pc.list_parent_indices(lst))
    cols = {k: base.column(k) for k in keep}
    names = [flat.type.field(i).name for i in range(flat.type.num_fields)]
    # earliest files: P2PK outputs have no 'address' field at all -> addr 0 (= no address)
    cols['addr'] = akeys(pc.struct_field(flat, 'address')) if 'address' in names else pa.array(np.zeros(len(flat), np.int64))
    cols['type'] = pc.struct_field(flat, 'type').dictionary_encode()
    cols['sats'] = sats(pc.struct_field(flat, 'value'))
    if side == 'outputs':
        cols['n'] = pc.cast(pc.struct_field(flat, 'index'), pa.int32())
    else:
        cols['prev_tx'] = tx_key(pc.struct_field(flat, 'spent_transaction_hash'))
        cols['prev_n'] = pc.cast(pc.struct_field(flat, 'spent_output_index'), pa.int32())
    return pa.table(cols)

def day(d, force=False):
    if not force and (ROOT / 'inputs' / f'date={d}.parquet').exists():
        return
    t0 = time.time()
    files = [f.path for f in s3.get_file_info(fs.FileSelector(f'{BUCKET}/date={d}', allow_not_found=True)) if f.path.endswith('.parquet')]
    # Today's partition is one small file per block (<height>.snappy.parquet). If we already have this day and every
    # file is per-block, only read blocks we don't have yet and append (was re-reading ~150 files, ~5 min, every hour).
    prev = None
    names = [p.rsplit('/', 1)[1].split('.')[0] for p in files]
    have = ROOT / 'tx' / f'date={d}.parquet'
    if force and have.exists() and files and all(n.isdigit() for n in names):
        hb = pq.read_table(have, columns=['block'])['block']
        maxb = pc.max(hb).as_py() if len(hb) else -1
        new = [p for p, n in zip(files, names) if int(n) > maxb]
        if not new:
            print(d, 'no new blocks', flush=True); return
        prev = {k: pq.read_table(ROOT / k / f'date={d}.parquet') for k in ('tx', 'outputs', 'inputs')}
        files = new
    if not files:
        # 2009-01-04..08 have no blocks; some early dates have no AWS partition. Store empty tables so the day counts as done.
        if d < '2011-01-01':
            for name in ('tx', 'outputs', 'inputs'):
                tpl = next((ROOT / name).glob('date=2010-12-*.parquet'), None)
                if tpl is None: raise RuntimeError('no template file for empty day')
                dst = ROOT / name / f'date={d}.parquet'
                pq.write_table(pq.read_schema(tpl).empty_table(), dst, compression='zstd')
            print(d, 'no blocks in AWS -> empty day', flush=True); return
        raise RuntimeError(f'no AWS files for {d}')
    def read1(p):
        f = pq.ParquetFile(p, filesystem=s3)
        try:
            tb = f.read(columns=COLS)
        except (KeyError, ValueError, pa.ArrowInvalid):
            tb = f.read(columns=[c for c in COLS if c != 'fee'])
        if 'fee' not in tb.column_names:   # earliest files have no 'fee' column (fees were ~0)
            tb = tb.append_column('fee', pa.nulls(tb.num_rows, pa.float64()))
        return tb
    tabs = [read1(p) for p in files]
    tabs = [x.select(tabs[0].column_names) for x in tabs]
    t = pa.concat_tables(tabs, promote_options='permissive')
    t = pa.table({'tx': tx_key(t['txid'].combine_chunks()),
                  'block': pc.cast(t['block_number'], pa.int32()), 'pos': pc.cast(t['index'], pa.int32()),
                  'ts': pc.cast(t['block_timestamp'], pa.timestamp('s')), 'coinbase': t['is_coinbase'],
                  'fee_sats': sats(t['fee']), 'outputs': t['outputs'],
                  **({'inputs': t['inputs']} if 'inputs' in t.column_names else {})})   # coinbase-only days have no inputs column
    keep = ['tx', 'block', 'ts']
    out = {'tx': t.select(['tx', 'block', 'pos', 'ts', 'coinbase', 'fee_sats']),
           'outputs': explode(t, 'outputs', keep),
           'inputs': (explode(t.filter(pc.invert(t['coinbase'])), 'inputs', keep) if 'inputs' in t.column_names
                      else pq.read_schema(next((ROOT / 'inputs').glob('date=2010-12-*.parquet'))).empty_table())}
    if prev:
        out = {k: pa.concat_tables([prev[k], v.cast(prev[k].schema)]) for k, v in out.items()}
    sizes = {}
    for name, tab in out.items():
        dst = ROOT / name / f'date={d}.parquet'
        dst.parent.mkdir(parents=True, exist_ok=True)
        tmp = dst.with_name(dst.name + '.tmp')
        pq.write_table(tab, tmp, compression='zstd', compression_level=9)
        tmp.replace(dst)
        sizes[name] = f'{tab.num_rows} rows {dst.stat().st_size/1e6:.1f}MB'
    print(d, f'{time.time()-t0:.0f}s', len(files), 'files', sizes, flush=True)

if __name__ == '__main__':
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    for d in args:
        day(d, force='--force' in sys.argv)

"""Stop recomputing what the knob being tuned does not change.

Tuning the embargo, the lookback or the simulated window re-runs
`filter_backtest.py` from scratch every time, and most of that work is
identical between runs. Measured over one session of nine runs:

  panel load, windows load        identical every time
  MiniRocket transform            identical every time -- 55,737 windows
                                  into 4,200 features, 0.94 GB, the single
                                  largest cost in the script
  per-block ridge fits            identical whenever the block's TRAINING
                                  ROWS are the same. Running to 2018 and
                                  running to 2026 share ten such blocks.
  the simulation                  genuinely different, and cheap

So two caches, both keyed on content rather than on flags, which is what
lets unrelated runs share entries without anyone declaring that they
should. `--until 2018-12-31` and the full run produce identical training
masks for 2009-2018, hash the same, and hit the same cache.

WHAT IS NOT CACHED, deliberately: the fits themselves cannot be reused
across different embargoes, because the embargo IS the definition of
which rows a block trains on. A different embargo is a different fit.
See EVALUATION_SPEC.md for the one way around that -- precomputing ridge
Gram matrices in time slices -- and why it is not built here.

Nothing in this file changes a number. Delete `results/.fitcache` and
every run reproduces exactly what it produced before.
"""

import hashlib
from pathlib import Path

import numpy as np

try:
    from lppl_backtest import ROOT
except ImportError:
    ROOT = Path('.')

CACHE = ROOT / 'results' / '.fitcache'


def _feed(h, v) -> None:
    if isinstance(v, np.ndarray):
        a = np.packbits(v) if v.dtype == bool else np.ascontiguousarray(v)
        h.update(a.tobytes())
        h.update(f'{v.dtype}{v.shape}'.encode())
    else:
        h.update(repr(v).encode())
    h.update(b'|')


def key(*parts) -> str:
    """A content hash of everything the result depends on.

    Boolean masks are packed before hashing, so a 55,737-row training mask
    costs 7 kB to hash rather than 56 kB."""
    h = hashlib.sha1()
    for p in parts:
        _feed(h, p)
    return h.hexdigest()[:20]


def file_key(path) -> str:
    """Identity of an input file: its size and modification time. Cheap,
    and it changes whenever the file is rebuilt."""
    st = Path(path).stat()
    return key('file', Path(path).name, st.st_size, int(st.st_mtime))


def load(name: str, k: str):
    """The cached arrays as a dict, or None."""
    f = CACHE / f'{name}_{k}.npz'
    if not f.exists():
        return None
    with np.load(f, allow_pickle=False) as z:
        return {q: z[q] for q in z.files}


def save(name: str, k: str, **arrays) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f'{name}_{k}.tmp.npz'
    np.savez(tmp, **arrays)
    tmp.replace(CACHE / f'{name}_{k}.npz')


def big_load(name: str, k: str, mmap: bool = True):
    """A single large array, memory-mapped by default so selecting a
    block's rows costs only those rows."""
    f = CACHE / f'{name}_{k}.npy'
    if not f.exists():
        return None
    return np.load(f, mmap_mode='r' if mmap else None)


def big_save(name: str, k: str, arr: np.ndarray) -> None:
    CACHE.mkdir(parents=True, exist_ok=True)
    tmp = CACHE / f'{name}_{k}.tmp.npy'
    np.save(tmp, arr)
    tmp.replace(CACHE / f'{name}_{k}.npy')


def cached_big(name: str, k: str, build, mmap: bool = True):
    """Return the cached array, or build it, store it and return it."""
    got = big_load(name, k, mmap)
    if got is not None:
        print(f'  {name}: cache hit ({got.shape}, '
              f'{got.nbytes / 1e9:.2f} GB on disk)', flush=True)
        return got
    arr = build()
    big_save(name, k, arr)
    print(f'  {name}: built and cached ({arr.nbytes / 1e9:.2f} GB)',
          flush=True)
    return arr

"""Acceptance gate for MINERVINI_SPEC.md v2 — build order step 2.

The spec forbids trusting (or even running) the portfolio backtest until
the case studies pass. This script runs them and prints, for each real
name, the exact condition that eats each day of the window, so a failure
is a diagnosis rather than a shrug.

It also runs the `--chain` diagnostic: the same rules with the base
anchored at the START OF THE CONTRACTION CHAIN instead of at the 325-day
rim. That is not the frozen spec — it is the measurement behind the
amendment proposed in the FINDINGS entry, kept here so the claim is
reproducible. It changes no committed behaviour.

Run: python minervini_gate.py
     python minervini_gate.py --chain
"""

import sys

import numpy as np
import pandas as pd

import minervini as mv
from lppl_backtest import ROOT, load_config

CASES = [('SPHR', '2025-09-01', '2026-01-31', 100.0),
         ('SMCI', '2023-06-01', '2024-01-31', None)]


def prep(ticker: str, cfg: dict):
    raw = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'ohlcv'
                          / f'{ticker}.parquet')
    close = raw['close'].to_numpy()
    volume = raw['volume'].to_numpy()
    m = cfg['minervini']
    v = pd.Series(volume)
    v_long = v.rolling(m['dryup_long']).mean().to_numpy()
    dryup = v.rolling(m['dryup_window']).min().to_numpy() <= \
        m['dryup_max_ratio'] * v_long
    zz = mv.zigzag(close, m['zigzag_threshold'])
    depth = np.zeros(len(zz['idx']))
    hi_idx = np.full(len(zz['idx']), -1, dtype=np.int64)
    for s in range(1, len(zz['idx'])):
        if zz['kind'][s] == -1 and zz['kind'][s - 1] == 1:
            depth[s] = (zz['price'][s - 1] - zz['price'][s]) / zz['price'][s - 1]
            hi_idx[s] = zz['idx'][s - 1]
    return {'raw': raw, 'close': close, 'volume': volume, 'v_long': v_long,
            'dryup': dryup, 'zz': zz, 'depth': depth, 'hi_idx': hi_idx,
            'template': mv.trend_template(close, cfg)}


def chain_base(d: dict, i: int, cfg: dict):
    """DIAGNOSTIC ONLY. Base = the run of contractions that keeps deepening
    as you walk back; it starts at the oldest such pullback's high, not at
    the 325-day rim."""
    m = cfg['minervini']
    zz, depth, hi_idx, close = d['zz'], d['depth'], d['hi_idx'], d['close']
    last = int(np.searchsorted(zz['confirm'], i, side='right')) - 1
    if last < 1 or zz['kind'][last] != -1 or zz['kind'][last - 1] != 1:
        return None, 'no_confirmed_trough'
    pivot = float(zz['price'][last - 1])
    if close[i] >= pivot:
        return None, 'already_above_pivot'
    depths, highs, s = [], [], last
    while s >= 1 and hi_idx[s] >= 0:
        dd = depth[s]
        if depths and not depths[-1] < dd:
            break                                   # left edge of the base
        if dd > m['max_correction']:
            break                                   # a prior cycle, not a base
        depths.append(dd), highs.append(int(hi_idx[s]))
        s -= 2
    if len(depths) < m['min_contractions']:
        return None, f'only_{len(depths)}_contractions'
    if depths[0] > m['final_contraction_max']:
        return None, 'final_too_deep'
    age = i - highs[-1]
    if age < m['base_age_min']:
        return None, f'age_{age}'
    if age > m['base_age_max']:
        return None, 'age_too_old'
    b_val = float(close[highs[-1]:i + 1].max())
    if pivot < m['pivot_min_of_base'] * b_val:
        return None, 'pivot_below_rim'
    lb = m['base_lookback']
    if i >= lb - 1 and b_val < (1 - m['max_correction']) \
            * float(close[i - lb + 1:i + 1].max()):
        return None, 'inside_a_prior_collapse'
    return (pivot, age, len(depths)), 'SETUP'


def spec_base(d: dict, i: int, cfg: dict):
    """The frozen v2 rule, with the rejection reason."""
    m = cfg['minervini']
    zz, depth, hi_idx, close = d['zz'], d['depth'], d['hi_idx'], d['close']
    if i < m['base_lookback'] - 1:
        return None, 'short_history'
    anc = mv.anchor_base(close, i, cfg)
    if anc is None:
        return None, 'no_anchor'
    b_val, b_i = anc
    age = i - b_i
    if not (m['base_age_min'] <= age <= m['base_age_max']):
        return None, f'age_{age}' if age < m['base_age_min'] else 'age_too_old'
    last = int(np.searchsorted(zz['confirm'], i, side='right')) - 1
    if last < 1 or zz['kind'][last] != -1 or zz['kind'][last - 1] != 1:
        return None, 'no_confirmed_trough'
    pivot = float(zz['price'][last - 1])
    if pivot < m['pivot_min_of_base'] * b_val:
        return None, 'pivot_below_rim'
    if close[i] >= pivot:
        return None, 'already_above_pivot'
    depths, s = [], last
    while s >= 1 and hi_idx[s] >= b_i:
        depths.append(depth[s])
        s -= 2
    if len(depths) < m['min_contractions']:
        return None, f'only_{len(depths)}_contractions'
    if depths[0] > m['final_contraction_max']:
        return None, 'final_too_deep'
    if not all(a < b for a, b in zip(depths, depths[1:])):
        return None, 'not_decreasing'
    return (pivot, age, len(depths)), 'SETUP'


def gate(cfg: dict, use_chain: bool) -> bool:
    base_fn = chain_base if use_chain else spec_base
    label = 'CHAIN DIAGNOSTIC' if use_chain else 'FROZEN v2 SPEC'
    m = cfg['minervini']
    print(f'=== acceptance gate, {label} ===')
    passed = True
    for ticker, start, end, max_px in CASES:
        d = prep(ticker, cfg)
        raw = d['raw']
        w = np.asarray((raw.index >= start) & (raw.index <= end))
        reasons: dict[str, int] = {}
        setups = []
        for i in np.flatnonzero(w):
            i = int(i)
            if not d['template'][i]:
                reasons['no_template'] = reasons.get('no_template', 0) + 1
                continue
            if not d['dryup'][i]:
                reasons['no_dryup'] = reasons.get('no_dryup', 0) + 1
                continue
            res, why = base_fn(d, i, cfg)
            reasons[why] = reasons.get(why, 0) + 1
            if res is not None:
                setups.append((i, res[0]))

        triggers = []
        for i, pivot in setups:
            j = i + 1
            if j >= len(raw):
                continue
            stop = pivot * (1 + m['buy_stop_offset'])
            if raw['high'].to_numpy()[j] < stop:
                continue
            fill = max(raw['open'].to_numpy()[j], stop)
            if fill > pivot * (1 + m['max_chase']):
                continue
            vr = d['volume'][j] / d['v_long'][j]
            triggers.append((raw.index[j].date(), fill, vr))

        hits = [t for t in triggers if max_px is None or t[1] < max_px]
        ok = len(hits) > 0
        passed &= ok
        print(f'\n{ticker} {start}..{end}: {int(w.sum())} days, '
              f'{len(setups)} setups, {len(triggers)} triggers  '
              f'-> {"PASS" if ok else "FAIL"}')
        for k, v in sorted(reasons.items(), key=lambda kv: -kv[1]):
            print(f'    {k:26s} {v}')
        for dt, fill, vr in triggers:
            print(f'    TRIGGER {dt}  fill {fill:8.2f}  '
                  f'breakout volume {vr:.2f}x the 50d mean '
                  f'({"confirmed" if vr >= m["breakout_volume_mult"] else "UNCONFIRMED"})')
    print(f'\ngate: {"PASS" if passed else "FAIL"}')
    return passed


def main() -> None:
    cfg = load_config()
    ok = gate(cfg, use_chain='--chain' in sys.argv)
    if not ok:
        print('The spec forbids running the portfolio backtest on a failed '
              'gate. Not run.')


if __name__ == '__main__':
    main()

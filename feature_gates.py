"""Do the voided data columns predict anything? Two gates, in minutes.

RANKER_SPEC.md Amendment 6, step 1 -- the first and cheapest go/no-go.
Three families of data are already fetched and were all rejected once:
the Code 33 fundamental legs (EPS, sales, margins), industry-group
strength, and the earnings surprise. **Those rejections are void.** Every
one of them was measured as a HARD GATE in the retired veto architecture
-- pass all three legs or the day is not tradable -- which is a different
question from "does this column carry any ordering information". As
feature columns under a value target they are untested.

Two gates, cheapest first, and nothing downstream is paid for until both
are cleared:

    Gate 1, seconds   per-year Spearman of the column against the target.
                      Admitted if the SIGN agrees in >= 10 of 15 years.
                      A column whose relationship changes direction every
                      other year has nothing a walk-forward can carry
                      from one block to the next, whatever its pooled
                      correlation says.

    Gate 2, minutes   a ridge on the candidate columns ALONE, on the same
                      folds, the same grouped purged alpha and the same
                      null as every other fold line in this repo.
                      Admitted if it beats predicting the training mean
                      in >= 8 of 15 folds.

No book is simulated here. The fold line is the gate; the book is the
ceremony, and it comes later if anything survives.

The panel this reads is the STANDARD one -- prices, rs, the calendar --
and the candidate columns are rebuilt from the raw tables beside it
(`fundamentals_quarterly.parquet`, `earnings_eps.parquet`,
`industries.csv`, `earnings_surprise*.parquet`). Nothing here rebuilds
the 250 MB panel cache: `build_panel(code33=..., group=...)` would, under
a different cache name, for columns this file computes in seconds from
the `rs` matrix the cached panel already holds.

Usage
    python feature_gates.py
    python feature_gates.py --min-years 10 --min-folds 8
"""

import sys

import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from bets_common import (EMBARGO_DAYS, LOOKBACK_YEARS, load, value_target,
                         year_blocks)
from filter_backtest import LEDGER, WINDOWS, key_columns
from lppl_backtest import ROOT, load_config
from minervini import code33_legs, eps_gate, group_strength
from minervini_backtest import apply_v5, build_panel, load_surprise
from rankers import MultiRidge, purged_years

DATA = ROOT / 'data'


# ----------------------------------------------------------------------
# the candidate columns
# ----------------------------------------------------------------------

def last_surprise(report_dates, surprise_pct, calendar, max_age) -> np.ndarray:
    """The most recent report's surprise percentage, as a CONTINUOUS
    column rather than the beat/miss boolean the retired gate used.

    Same staleness rule as `minervini.beat_gate`: a report older than
    `max_age` days stops counting, and a day with no report behind it is
    NaN rather than zero -- a missing figure is not a zero surprise, and
    the (value, finite) pair downstream is what keeps the two apart.
    """
    out = np.full(len(calendar), np.nan)
    sp = np.asarray(surprise_pct, dtype=float)
    if not len(sp):
        return out
    days = calendar.to_numpy()
    known = np.searchsorted(report_dates, days, side='right')
    has = known > 0
    if not has.any():
        return out
    idx = known[has] - 1
    age = (days[has] - report_dates[idx]).astype('timedelta64[D]').astype(int)
    fresh = age <= max_age
    vals = sp[idx]
    pos = np.flatnonzero(has)[fresh]
    out[pos] = vals[fresh]
    return out


def candidates(panel, cfg) -> dict:
    """The voided columns, as (days x tickers) matrices on the panel's own
    calendar. Each is built exactly the way `build_panel` builds it when
    asked -- same functions, same config -- so a column admitted here is
    the column the model would get."""
    cal, tickers = panel['calendar'], list(panel['tickers'])
    n, k = panel['close'].shape
    out = {}

    tab = pd.read_csv(DATA / 'industries.csv')
    gmap = dict(zip(tab['ticker'], tab['industry']))
    gid = {g: i for i, g in enumerate(sorted(set(gmap.values())))}
    groups = np.array([gid.get(gmap.get(t, None), -1) for t in tickers])
    out['group_pct'] = group_strength(panel['rs'], groups, cfg)

    fq = pd.read_parquet(DATA / 'fundamentals_quarterly.parquet')
    fq = fq.sort_values(['ticker', 'filed'])
    legs = np.zeros((n, k), dtype=np.int8)
    for j, g in fq.groupby('ticker'):
        if j in tickers:
            legs[:, tickers.index(j)] = code33_legs(
                g['filed'].to_numpy(), g['revenue'].to_numpy(),
                g['net_income'].to_numpy(), cal, cfg)
    out['c33_sales_margin'] = legs.astype(float)

    eps = (pd.read_parquet(DATA / 'earnings_eps.parquet')
           .dropna(subset=['eps']).sort_values('date'))
    ok = np.zeros((n, k))
    for j, g in eps.groupby('ticker'):
        if j in tickers:
            ok[:, tickers.index(j)] = eps_gate(
                g['date'].to_numpy(), g['eps'].to_numpy(), cal, cfg)
    out['c33_eps'] = ok

    max_age = cfg['minervini_fundamentals']['max_report_age_days']
    sp = load_surprise(DATA)
    sur = np.full((n, k), np.nan)
    if sp is not None and len(sp):
        sp = sp.sort_values('date')
        for j, g in sp.groupby('ticker'):
            if j in tickers:
                sur[:, tickers.index(j)] = last_surprise(
                    g['date'].to_numpy(), g['surprise_pct'].to_numpy(),
                    cal, max_age)
    out['surprise'] = sur
    return out


def as_pairs(mat, ei, tj) -> np.ndarray:
    """One candidate column as the (value filled with 0, finite) pair the
    model sees, read on the day the ORDER is placed -- `entry_i - 1`, the
    same instant `key_columns` reads its three."""
    v = np.asarray(mat[np.maximum(ei - 1, 0), tj], dtype=np.float64)
    ok = np.isfinite(v)
    return np.stack([np.where(ok, v, 0.0), ok.astype(np.float64)], 1)


# ----------------------------------------------------------------------
# the gates
# ----------------------------------------------------------------------

def gate_one(name, col, r, yr, years, min_years) -> bool:
    """Sign stability, per year. Nothing is fitted."""
    signs, rows = [], []
    for Y in years:
        sel = (yr == Y) & np.isfinite(col)
        if sel.sum() < 30 or np.ptp(col[sel]) == 0:
            rows.append('  .  ')
            continue
        with np.errstate(invalid='ignore'):
            rho = spearmanr(col[sel], r[sel]).statistic
        if not np.isfinite(rho):
            rows.append('  .  ')
            continue
        signs.append(np.sign(rho))
        rows.append(f'{rho:+.2f}')
    if not signs:
        print(f'  {name:18s} no year has enough finite rows')
        return False
    agree = max(int((np.array(signs) > 0).sum()),
                int((np.array(signs) < 0).sum()))
    ok = agree >= min_years
    print(f'  {name:18s} {" ".join(rows)}   sign agrees {agree}/{len(signs)}'
          f'  {"PASS" if ok else "fail"}')
    return ok


def gate_two(name, F, r, date, blocks, min_folds) -> bool:
    """A ridge on these columns alone, against the honest null: predict
    the fold's own training mean. Same schedule, same grouped purged
    alpha criterion as every other fit in this repo."""
    beat, n_ok, tot = 0, 0, 0
    for Y, tr, ev in blocks:
        rk = MultiRidge(alpha='cv').fit(F[tr], r[tr], date[tr])
        if not rk.fitted_:
            continue
        p = rk.score(F[ev])
        null = float(np.mean((r[ev] - r[tr].mean()) ** 2))
        r2 = 1.0 - float(np.mean((p - r[ev]) ** 2)) / null
        beat += r2 > 0
        n_ok += 1
        tot += r2 * int(ev.sum())
    ok = beat >= min_folds
    print(f'  {name:18s} beats the constant in {beat}/{n_ok} folds  '
          f'{"PASS" if ok else "fail"}')
    return ok


def main() -> None:
    av = sys.argv

    def opt(flag, default, cast=str):
        return cast(av[av.index(flag) + 1]) if flag in av else default

    min_years = opt('--min-years', 10, int)
    min_folds = opt('--min-folds', 8, int)

    cfg = apply_v5(load_config())
    cfg['minervini_trading']['reentry_fast'] = True
    panel = build_panel(cfg, v5=True)

    d = load(str(WINDOWS))
    led = pd.read_csv(LEDGER, parse_dates=['entry_date'])
    w = pd.DataFrame({'ticker': [str(t) for t in d['ticker']],
                      'entry_date': pd.to_datetime(d['entry_date']),
                      'wrow': np.arange(len(d['y']))})
    m = (w.merge(led[['ticker', 'entry_date', 'exit_date', 'entry_i',
                      'ticker_j', 'y', 'half_frac', 'y_half']],
                 on=['ticker', 'entry_date'], how='inner')
         .drop_duplicates('wrow').reset_index(drop=True))
    ei = m['entry_i'].to_numpy(np.int64)
    tj = m['ticker_j'].to_numpy(np.int64)
    date = m['entry_date'].to_numpy().astype('datetime64[D]')
    exits = pd.to_datetime(m['exit_date']).to_numpy().astype('datetime64[D]')
    yr = m['entry_date'].dt.year.to_numpy()
    # THE TARGET IS THE VALUE ONE (Amendment 6), on the CURRENT ledger.
    # The cap has not been built yet and does not need to be: a column
    # that cannot order uncapped outcomes will not start ordering capped
    # ones, and this is the cheap half of the question.
    r = value_target(m['y'].to_numpy(np.float64),
                     m['half_frac'].to_numpy(np.float64),
                     m['y_half'].to_numpy(np.float64))

    blocks = year_blocks(date, exits, lookback_years=LOOKBACK_YEARS or None,
                         embargo_days=EMBARGO_DAYS)
    # BOTH GATES READ THE SAME YEARS. A fold whose training window cannot
    # supply two purged years never fits, so its year has no gate-2 vote
    # and must not have a gate-1 one either -- otherwise the sign bar is
    # counted out of a longer record than the fold bar and "10 of 15"
    # quietly becomes "10 of 18". The test is dates only, so it costs
    # nothing to ask before fitting.
    blocks = [b for b in blocks if purged_years(date[b[1]]) >= 2]
    years = [Y for Y, _, _ in blocks]
    print(f'\nFEATURE GATES  target=ln(y)  ledger={len(m):,} bets  '
          f'{len(blocks)} folds {years[0]}-{years[-1]}  '
          f'gate1 sign >= {min_years}/{len(years)}, '
          f'gate2 beats-constant >= {min_folds}/{len(years)}')

    cand = candidates(panel, cfg)
    print('\ncoverage (finite share of the ledger rows, read at entry-1):')
    cols = {}
    for name, mat in cand.items():
        F = as_pairs(mat, ei, tj)
        cols[name] = F
        print(f'  {name:18s} {F[:, 1].mean():6.1%}')

    print(f'\ngate 1 -- per-year Spearman against the target, '
          f'{years[0]}-{years[-1]}:')
    g1 = {n: gate_one(n, np.where(F[:, 1] > 0, F[:, 0], np.nan), r, yr,
                      years, min_years)
          for n, F in cols.items()}

    live = [n for n, ok in g1.items() if ok]
    if not live:
        print('\nnothing survives gate 1; no fit is paid for.')
        return
    print(f'\ngate 2 -- ridge on the candidate columns alone '
          f'({len(live)} survivor(s)):')
    g2 = {n: gate_two(n, cols[n], r, date, blocks, min_folds) for n in live}
    if len(live) > 1:
        gate_two('ALL SURVIVORS', np.concatenate([cols[n] for n in live], 1),
                 r, date, blocks, min_folds)

    won = [n for n in live if g2[n]]
    print(f'\nadmitted to the full model: '
          f'{", ".join(won) if won else "nothing"}')
    if not won:
        print('no book is simulated for a feature set that has not cleared '
              'gate 2 (Amendment 6).')


if __name__ == '__main__':
    main()

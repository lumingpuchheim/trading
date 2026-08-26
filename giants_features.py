"""Steady Giants phase 2: monthly qualification table per STEADY_GIANTS_SPEC.

For every (ticker, monthly decision date): liquidity, trailing 3y vol,
5y log-price regression slope/R2 (on total-return prices), dividend
record, trailing-12m reported EPS, P/E on RECONSTRUCTED NOMINAL prices
(adjusted prices carry a dividend-adjustment drift that would corrupt
the own-history ceiling), and expanding own-history P/E percentiles.

Output: data/giants_monthly.parquet.  Run: python giants_features.py
"""

import numpy as np
import pandas as pd

from lppl_backtest import ROOT, load_config

OUT = ROOT / 'data' / 'giants_monthly.parquet'
VOL_WIN = 756       # 3y
REG_WIN = 1260      # 5y
DIV_YEARS = 5       # unbroken payment record
CUT = 0.8           # >20% YoY drop = cut
MIN_PE_MONTHS = 20  # min own-history months before percentiles exist


def nominal_prices(adj: np.ndarray, dates, divs: pd.DataFrame | None) -> np.ndarray:
    """Invert yfinance's dividend back-adjustment: walk ex-dates from the
    most recent backwards, accumulating the adjustment factor. Nominal
    price = adjusted / factor; factor == 1 after the last ex-date."""
    nominal = adj.copy()
    if divs is None or not len(divs):
        return nominal
    pos = pd.Series(np.arange(len(dates)), index=dates)
    f = 1.0
    for r in divs.sort_values('date', ascending=False).itertuples():
        loc = pos.index.searchsorted(r.date)
        if loc <= 0 or loc >= len(dates):
            continue
        i = int(pos.iloc[loc])
        p_ex = adj[i] / f if np.isfinite(adj[i]) else np.nan
        if not (np.isfinite(p_ex) and p_ex > r.amount > 0):
            continue
        f *= 1.0 - r.amount / p_ex
        nominal[:i] = adj[:i] / f
    return nominal


def main() -> None:
    cfg = load_config()
    d = cfg['data']
    data_dir = ROOT / d['cache_dir']
    spy = pd.read_parquet(data_dir / 'ohlcv' / f"{d['benchmark']}.parquet")
    cal = spy.index
    n = len(cal)
    months = pd.Series(cal).dt.to_period('M')
    dec_i = pd.Series(np.arange(n)).groupby(months).first().to_numpy()
    dec_i = dec_i[dec_i >= REG_WIN]

    div = pd.read_parquet(data_dir / 'dividends.parquet')
    eps = pd.read_parquet(data_dir / 'earnings_eps.parquet') \
        .dropna(subset=['eps']).sort_values('date')
    div_by = {t: g for t, g in div.groupby('ticker')}
    eps_by = {t: g for t, g in eps.groupby('ticker')}

    t_idx = np.arange(REG_WIN, dtype=float)
    t_c = t_idx - t_idx.mean()
    t_ss = float(t_c @ t_c)

    rows = []
    files = sorted((data_dir / 'ohlcv').glob('*.parquet'))
    for k, path in enumerate(files, 1):
        t = path.stem
        if t == d['benchmark']:
            continue
        df = pd.read_parquet(path).reindex(cal)
        close = df['close'].to_numpy()
        dollar = (df['close'] * df['volume']).rolling(
            d['dollar_volume_window']).mean().to_numpy()
        ret = pd.Series(close).ffill().pct_change().to_numpy()
        nom = nominal_prices(close, cal, div_by.get(t))

        dv = div_by.get(t)
        ysum = dv.groupby(dv['date'].dt.year)['amount'].sum() \
            if dv is not None and len(dv) else pd.Series(dtype=float)
        eg = eps_by.get(t)
        eps_dates = eg['date'].to_numpy() if eg is not None \
            else np.array([], dtype='datetime64[ns]')
        eps_vals = eg['eps'].to_numpy() if eg is not None else np.array([])

        for i in dec_i:
            c = close[i]
            if not (np.isfinite(c) and c > d['min_price']
                    and np.isfinite(dollar[i])
                    and dollar[i] > d['min_dollar_volume']):
                continue
            w = close[i - REG_WIN + 1:i + 1]
            r3 = ret[i - VOL_WIN + 1:i + 1]
            if not (np.all(np.isfinite(w)) and np.all(np.isfinite(r3[1:]))):
                continue
            vol = float(np.std(r3[1:]))
            y = np.log(w)
            y_c = y - y.mean()
            slope = float((t_c @ y_c) / t_ss)
            fitted_ss = slope * slope * t_ss
            tot_ss = float(y_c @ y_c)
            r2 = fitted_ss / tot_ss if tot_ss > 0 else 0.0

            # dividend record over the last DIV_YEARS complete years
            yr = cal[i].year
            paid = all(ysum.get(y_, 0.0) > 0 for y_ in range(yr - DIV_YEARS, yr))
            cut = any(ysum.get(y_, 0.0) < CUT * ysum.get(y_ - 1, 0.0)
                      for y_ in (yr - 1, yr - 2)) if paid else False

            # trailing 12m reported EPS as of this date
            m = eps_dates <= cal[i]
            ttm = float(eps_vals[m][-4:].sum()) if m.sum() >= 4 else np.nan
            pe = nom[i] / ttm if np.isfinite(ttm) and ttm > 0 \
                and np.isfinite(nom[i]) else np.nan

            rows.append({'ticker': t, 'date': cal[i], 'i': int(i),
                         'vol': vol, 'slope': slope, 'r2': float(r2),
                         'div_paid': bool(paid), 'div_cut': bool(cut),
                         'has_eps': bool(m.sum() >= 4), 'pe': pe})
        if k % 200 == 0:
            print(f'  {k}/{len(files)} tickers, {len(rows)} rows', flush=True)

    tab = pd.DataFrame(rows)
    # cross-sectional volatility tercile per month
    tab['vol_terc'] = tab.groupby('date')['vol'] \
        .transform(lambda x: pd.qcut(x, 3, labels=False, duplicates='drop'))
    # expanding own-history P/E percentiles (monthly samples, prior months only)
    tab = tab.sort_values(['ticker', 'date']).reset_index(drop=True)
    for q, col in [(0.90, 'pe_p90'), (0.95, 'pe_p95'), (1.00, 'pe_max')]:
        tab[col] = tab.groupby('ticker')['pe'].transform(
            lambda s: s.expanding(min_periods=MIN_PE_MONTHS).quantile(q).shift(1))
    tab.to_parquet(OUT)
    q = tab[(tab['vol_terc'] == 0) & (tab['slope'] > 0) & tab['div_paid']
            & ~tab['div_cut'] & tab['has_eps']]
    print(f'{len(tab)} ticker-months -> {OUT}')
    print(f'qualifier-months at r2 thresholds: '
          + ', '.join(f'{th}: {int((q["r2"] >= th).sum())}'
                      for th in (0.6, 0.7, 0.8)))


if __name__ == '__main__':
    main()

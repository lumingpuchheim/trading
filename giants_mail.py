"""Steady Giants monthly mail: fetch fresh data, compute this month's
buy candidates under the frozen winning config (R2 >= 0.7, own-history
P/E p90 ceiling), and email the ranked list. One email per run, green
light or red, so silence always means breakage.

Standalone and stateless: fetches everything fresh into data/live/
(never touches the frozen research caches in data/ohlcv etc.), tracks
no portfolio, receives nothing back. Universe: the S&P 1500 snapshot
frozen at the 2026-08 research download (known limitation, stated in
the mail footer).

Fetch strategy (fresh every run, polite to yfinance): one batched 6y
download of adjusted close+volume for the whole universe decides the
price screens (liquidity, lowest-vol tercile, 5y regression); only the
~few hundred survivors get per-ticker dividends, reported EPS, and
full-history closes for the nominal-price P/E and its own-history p90.
An adjusted series shifts its whole history at each new dividend, so
each run's own fetch is used end to end - never mixed with frozen bars.

Send: Resend API, key from env RESEND_API_KEY. Missing key: the
composed mail is printed instead and the run exits 0 (an ordinary
state, not a crash). --dry-run composes and prints, never sends.
--limit N restricts the universe for a quick test run.

Run: python giants_mail.py [--dry-run] [--limit N]
Schedule (Windows Task Scheduler, monthly): see README of the task or
FINDINGS; the command is simply `python giants_mail.py` with this
directory as the working directory.
"""

import json
import os
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

from giants_features import (CUT, DIV_YEARS, MIN_PE_MONTHS, REG_WIN,
                             VOL_WIN, div_record, nominal_prices,
                             slope_r2, ttm_eps)
from lppl_backtest import ROOT, load_config

LIVE = ROOT / 'data' / 'live'
R2_TH = 0.7           # frozen winning config
SLOTS = 8
PRICE_YEARS = 6       # batch window: 5y regression + buffer
CHUNK = 150           # tickers per yfinance batch request
TO = 'luming.sjtu@gmail.com'
FROM = 'onboarding@resend.dev'
RESEND_URL = 'https://api.resend.com/emails'


def universe() -> list[str]:
    cfg = load_config()
    d = ROOT / cfg['data']['cache_dir'] / 'ohlcv'
    return sorted(p.stem for p in d.glob('*.parquet')
                  if p.stem != cfg['data']['benchmark'])


def batch_download(tickers: list[str], **kw) -> pd.DataFrame:
    """yf.download in polite chunks with one retry for tickers that come
    back empty (transient yfinance blips happen); returns the
    concatenated frame with MultiIndex columns (field, ticker)."""
    import yfinance as yf

    def _dl(tks: list[str]) -> pd.DataFrame:
        parts = []
        for k in range(0, len(tks), CHUNK):
            chunk = tks[k:k + CHUNK]
            df = yf.download(chunk, auto_adjust=True, progress=False,
                             group_by='column', threads=True, **kw)
            if df is not None and len(df):
                if not isinstance(df.columns, pd.MultiIndex):  # 1 ticker
                    df.columns = pd.MultiIndex.from_product(
                        [df.columns, chunk])
                parts.append(df)
            time.sleep(1.0)
        return pd.concat(parts, axis=1) if parts else pd.DataFrame()

    out = _dl(tickers)
    have = set(out['Close'].dropna(axis=1, how='all').columns) \
        if len(out) and 'Close' in out.columns.get_level_values(0) else set()
    missing = [t for t in tickers if t not in have]
    if missing and have:                       # partial failure: retry once
        time.sleep(5.0)
        retry = _dl(missing)
        if len(retry):
            out = out.combine_first(retry)
    return out


def market_light(spy: pd.Series) -> tuple[bool, dict]:
    """SPY > 200d SMA and 20d vol <= its trailing 756d 90th percentile
    - exactly build_market in giants_backtest."""
    sma = spy.rolling(200).mean()
    v20 = spy.pct_change().rolling(20).std()
    thr = v20.rolling(756).quantile(0.90)
    green = bool(spy.iloc[-1] > sma.iloc[-1]) \
        and not bool(v20.iloc[-1] > thr.iloc[-1])
    return green, {'spy': float(spy.iloc[-1]), 'sma200': float(sma.iloc[-1]),
                   'vol20': float(v20.iloc[-1]), 'vol_thr': float(thr.iloc[-1])}


def pe_history(nom: np.ndarray, dates: pd.DatetimeIndex,
               eps_dates: np.ndarray, eps_vals: np.ndarray) -> tuple[float, float]:
    """(current P/E, own-history p90 of monthly P/E samples before this
    month). Monthly first trading days, nominal price / TTM reported
    EPS, same construction as the research table."""
    months = pd.Series(dates).dt.to_period('M')
    firsts = pd.Series(np.arange(len(dates))).groupby(months).first().to_numpy()
    samples = []
    for i in firsts[:-1]:                      # prior months only
        ttm = ttm_eps(eps_dates, eps_vals, dates[i])
        if np.isfinite(ttm) and ttm > 0 and np.isfinite(nom[i]):
            samples.append(nom[i] / ttm)
    ttm_now = ttm_eps(eps_dates, eps_vals, dates[-1])
    pe_now = nom[-1] / ttm_now \
        if np.isfinite(ttm_now) and ttm_now > 0 and np.isfinite(nom[-1]) \
        else np.nan
    p90 = float(np.quantile(samples, 0.90)) \
        if len(samples) >= MIN_PE_MONTHS else np.nan
    return pe_now, p90


def div_streak(dv: pd.Series, last_year: int) -> int:
    """Consecutive complete calendar years with a payment, counting
    back from last_year."""
    ysum = dv.groupby(dv.index.year).sum() if len(dv) else pd.Series(dtype=float)
    n = 0
    y = last_year
    while ysum.get(y, 0.0) > 0:
        n += 1
        y -= 1
    return n


def trim_summary(text: str, target: int = 250, max_chars: int = 500) -> str:
    """First ~2-3 sentences: accumulate '. '-separated parts until the
    target length, so abbreviations like 'U.S.' never end the intro."""
    out = ''
    for p in str(text).split('. '):
        out = p if not out else f'{out}. {p}'
        if len(out) >= target:
            break
    if not out.endswith('.'):
        out += '.'
    return out[:max_chars] + ('…' if len(out) > max_chars else '')


def compose(run_month: str, green: bool, light: dict, cands: list[dict],
            n_liquid: int, n_qual: int) -> tuple[str, str]:
    state = 'GREEN' if green else 'RED'
    light_line = (f"Market light: {state} - SPY {light['spy']:.0f} vs "
                  f"200-day average {light['sma200']:.0f}; 20-day "
                  f"volatility {light['vol20']:.2%} vs calm threshold "
                  f"{light['vol_thr']:.2%}.")
    footer = ("<hr><p style='color:#666;font-size:12px'>Steady Giants "
              "monthly screen. Universe: S&P 1500 snapshot 2026-08 (a "
              "known limitation: newly listed or newly added companies "
              "are not seen). Qualification: lowest-volatility third of "
              "the liquid universe, 5-year straight-line price growth "
              "with R&#178; &#8805; 0.7, dividends every one of the last "
              "5 years with no cut over 20%, and a price/earnings ratio "
              "not above the stock's own 90th-percentile history. This "
              "is a screen, not advice; it knows nothing about what you "
              "already hold.</p>")
    if not green:
        subject = f'Steady Giants {run_month}: light RED, no buys'
        body = (f'<p>The market light is <b>RED</b> - no buy candidates '
                f'this month, whatever qualifies.</p><p>{light_line}</p>'
                f'<p>{n_qual} stocks qualify and would be ranked next '
                f'green month.</p>' + footer)
        return subject, body
    subject = (f'Steady Giants {run_month}: {len(cands)} candidate'
               f'{"s" if len(cands) != 1 else ""} (light GREEN)')
    if not cands:
        body = (f'<p>The market light is GREEN but no stock passes every '
                f'test this month ({n_qual} qualify on steadiness; none '
                f'is below its own price ceiling).</p><p>{light_line}</p>'
                + footer)
        return subject, body
    rows = ''.join(
        f"<tr><td>{k}</td><td><b>{c['ticker']}</b></td><td>{c['name']}</td>"
        f"<td>{c['r2']:.2f}</td><td>{c['trend']:+.1%}</td>"
        f"<td>{c['pe']:.1f}</td><td>{c['p90']:.1f}</td>"
        f"<td>{c['pe_vs']:+.0%}</td><td>{c['streak']}</td></tr>"
        for k, c in enumerate(cands, 1))
    table = ("<table border='1' cellpadding='4' cellspacing='0' "
             "style='border-collapse:collapse'>"
             "<tr><th>#</th><th>Ticker</th><th>Company</th>"
             "<th>Straightness (R&#178;)</th><th>Trend/yr</th>"
             "<th>P/E</th><th>Own ceiling</th><th>vs ceiling</th>"
             "<th>Dividend streak (y)</th></tr>" + rows + '</table>')
    intros = ''.join(
        f"<p><b>{c['ticker']} - {c['name']}</b><br>{c['summary']}</p>"
        for c in cands)
    body = (f'<p>{light_line}</p><p>{len(cands)} buy candidates, '
            f'straightest compounder first (out of {n_qual} qualifiers '
            f'in a liquid universe of {n_liquid}):</p>'
            + table + '<h3>Who they are</h3>' + intros + footer)
    return subject, body


def send(subject: str, body: str, dry_run: bool) -> None:
    key = os.environ.get('RESEND_API_KEY')
    if dry_run or not key:
        note = ('dry run' if dry_run
                else 'RESEND_API_KEY is not set - printing the mail '
                     'instead of sending it')
        print(f'--- {note} ---')
        print(f'Subject: {subject}')
        print(body)
        return
    import requests
    r = requests.post(
        RESEND_URL, timeout=30,
        headers={'Authorization': f'Bearer {key}'},
        json={'from': FROM, 'to': [TO], 'subject': subject, 'html': body})
    if r.status_code >= 300:
        print(f'send failed: HTTP {r.status_code} {r.text[:300]}')
        sys.exit(1)
    print(f'sent: {subject} -> {TO}')


def main() -> None:
    dry_run = '--dry-run' in sys.argv
    limit = int(sys.argv[sys.argv.index('--limit') + 1]) \
        if '--limit' in sys.argv else None
    LIVE.mkdir(parents=True, exist_ok=True)
    cfg = load_config()
    d = cfg['data']
    tks = universe()
    if limit:
        tks = tks[:limit]
    start = (pd.Timestamp.today()
             - pd.DateOffset(years=PRICE_YEARS)).strftime('%Y-%m-%d')

    px = batch_download(tks + [d['benchmark']], start=start)
    if not len(px):
        print('the price download came back empty - nothing to screen, '
              'no mail composed')
        sys.exit(1)
    close, volume = px['Close'], px['Volume']
    close.to_parquet(LIVE / 'close_6y.parquet')
    spy = close[d['benchmark']].dropna()
    cal = spy.index
    print(f'fetched {close.shape[1] - 1}/{len(tks)} tickers, '
          f'{len(cal)} days through {cal[-1].date()}')

    green, light = market_light(spy)
    print(f"light: {'GREEN' if green else 'RED'} "
          f"(SPY {light['spy']:.0f} vs sma200 {light['sma200']:.0f}, "
          f"vol20 {light['vol20']:.2%} vs thr {light['vol_thr']:.2%})")

    # phase 1: price screens on the whole universe
    stats = []
    for t in tks:
        if t not in close.columns:
            continue
        c = close[t].reindex(cal).to_numpy()
        v = volume[t].reindex(cal).to_numpy() if t in volume.columns else None
        if v is None or len(c) < REG_WIN or not np.isfinite(c[-1]):
            continue
        dollar = np.nanmean((c * v)[-d['dollar_volume_window']:])
        if not (c[-1] > d['min_price'] and np.isfinite(dollar)
                and dollar > d['min_dollar_volume']):
            continue
        w = c[-REG_WIN:]
        if not np.all(np.isfinite(w)):
            continue
        r3 = pd.Series(c[-VOL_WIN - 1:]).pct_change().to_numpy()[1:]
        if not np.all(np.isfinite(r3)):
            continue
        slope, r2 = slope_r2(np.log(w))
        stats.append({'ticker': t, 'vol': float(np.std(r3)),
                      'slope': slope, 'r2': r2})
    liq = pd.DataFrame(stats)
    if not len(liq):
        print('no ticker passes the liquidity screen - no candidates')
        liq = pd.DataFrame(columns=['ticker', 'vol', 'slope', 'r2'])
    liq['vol_terc'] = pd.qcut(liq['vol'], 3, labels=False,
                              duplicates='drop') if len(liq) >= 3 else 2
    surv = liq[(liq['vol_terc'] == 0) & (liq['slope'] > 0)
               & (liq['r2'] >= R2_TH)].set_index('ticker')
    print(f'price screens: {len(liq)} liquid, {len(surv)} in the calm '
          f'tercile with a straight 5y uptrend')

    # phase 2: dividends, EPS, full-history closes for survivors only
    import yfinance as yf
    full = batch_download(sorted(surv.index), period='max')
    fc = full['Close'] if len(full) else pd.DataFrame()
    rows, skipped = [], []
    last_year = date.today().year - 1
    for t in sorted(surv.index):
        try:
            tk = yf.Ticker(t)
            dv = tk.dividends
            e = tk.get_earnings_dates(limit=100)
        except Exception:
            continue
        time.sleep(0.2)
        if dv is None or not len(dv) or e is None or not len(e):
            continue
        ddf = pd.DataFrame(
            {'date': [pd.Timestamp(pd.Timestamp(x).date()) for x in dv.index],
             'amount': dv.to_numpy(dtype=float)})
        ysum = ddf.groupby(ddf['date'].dt.year)['amount'].sum()
        paid, cut = div_record(ysum, last_year + 1)
        if not paid or cut:
            continue
        rep = e['Reported EPS'].dropna().sort_index()
        if len(rep) < 4:
            continue
        eps_dates = np.array([np.datetime64(pd.Timestamp(x).date())
                              for x in rep.index], dtype='datetime64[ns]')
        eps_vals = rep.to_numpy(dtype=float)
        c_full = fc[t].dropna() if t in fc.columns else pd.Series(dtype=float)
        if len(c_full) < 5:
            skipped.append(t)
            continue
        nom = nominal_prices(c_full.to_numpy(), c_full.index, ddf)
        pe, p90 = pe_history(nom, c_full.index, eps_dates, eps_vals)
        if not np.isfinite(pe):
            continue
        rows.append({'ticker': t, 'r2': float(surv.loc[t, 'r2']),
                     'vol': float(surv.loc[t, 'vol']),
                     'trend': float(np.exp(surv.loc[t, 'slope'] * 252) - 1),
                     'pe': pe, 'p90': p90,
                     'pe_vs': pe / p90 - 1 if np.isfinite(p90) else np.nan,
                     'streak': div_streak(dv, last_year),
                     'buyable': not (np.isfinite(p90) and pe > p90)})
    if skipped:
        print(f'note: {len(skipped)} survivors had an empty history fetch '
              f'even after retry and were skipped: {skipped[:10]}')
    qual = pd.DataFrame(rows)
    n_qual = int(len(qual))
    cands = qual[qual['buyable']].sort_values(
        ['r2', 'vol'], ascending=[False, True]).head(SLOTS) \
        .to_dict('records') if n_qual else []
    print(f'qualifiers: {n_qual} (dividend record + EPS + P/E), '
          f'{len(cands)} buyable; top: '
          f'{[c["ticker"] for c in cands] or "none"}')

    # names + business intros, cached across runs
    cache_p = LIVE / 'descriptions.json'
    cache = json.loads(cache_p.read_text(encoding='utf-8')) \
        if cache_p.exists() else {}
    for c in cands:
        t = c['ticker']
        if t not in cache:
            try:
                info = yf.Ticker(t).info
                cache[t] = {
                    'name': info.get('longName') or info.get('shortName') or t,
                    'summary': trim_summary(
                        info.get('longBusinessSummary')
                        or 'No description available.')}
            except Exception:
                cache[t] = {'name': t,
                            'summary': 'No description available.'}
            time.sleep(0.2)
        c['name'] = cache[t]['name']
        c['summary'] = cache[t]['summary']
    cache_p.write_text(json.dumps(cache, indent=1, ensure_ascii=False),
                       encoding='utf-8')

    run_month = date.today().strftime('%Y-%m')
    if not green:
        cands = []
    subject, body = compose(run_month, green, light, cands,
                            n_liquid=len(liq), n_qual=n_qual)
    send(subject, body, dry_run)


if __name__ == '__main__':
    main()

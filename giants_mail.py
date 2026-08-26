"""Steady Giants monthly mail: sync an immutable local price store,
compute this month's buy candidates under the frozen winning config
(R2 >= 0.7, own-history P/E p90 ceiling), and email the ranked list.
One mail per run, green light or red, so silence always means breakage.

Data layer (user requirement: verifiable, immutable raw history):

- data/live/raw/{ticker}.parquet - unadjusted-by-dividends daily close
  and volume. Yahoo's auto_adjust=False basis, which is empirically
  SPLIT-adjusted to current shares but dividend-unadjusted (verified on
  NVDA around its 2024-06-10 10:1 split: Close reads 122, as-traded was
  1224) - i.e. exactly the "nominal" series the research P/E uses.
  First run seeds full history (the P/E own-history ceiling and the
  dividend streaks need decades, so seeding only 6y would silently
  change the screen); monthly runs append only days after the stored
  high-water mark. Every append verifies an overlap window: raw closes
  must match the fresh download. A near-constant overlap ratio is a new
  stock split (Yahoo rescales its whole series): it is confirmed
  against yfinance's split history, the stored series is rescaled by
  the announced ratio, and a loud audit line is printed. Any other
  mismatch is reported and the fresh values win for those days - never
  silently.
- data/live/dividends_ledger.csv - append-only: date, ticker, amount in
  AS-DECLARED terms (what the company's investor page shows), converted
  from Yahoo's current-share terms via the split ledger. Existing
  entries are never modified; a re-fetch that disagrees with a stored
  entry becomes a report line in console and mail footer.
- data/live/splits_ledger.csv - append-only: date, ticker, ratio.
- Adjustment is computed locally each run from raw + ledger with the
  exact inverse of giants_features.nominal_prices, so screen results
  stay consistent with the research. Nothing pre-cooked from Yahoo
  enters the screen.

Standalone and stateless otherwise: no portfolio tracking, frozen
research caches in data/ohlcv never touched. Universe: the S&P 1500
snapshot frozen at the 2026-08 research download (stated in the mail
footer). Reported EPS is fetched fresh per surviving ticker (it is a
point-in-time report stream, not an adjusted series).

Send: Resend API, key from env RESEND_API_KEY. Missing key: the
composed mail is printed instead and the run exits 0 (an ordinary
state, not a crash). --dry-run composes and prints, never sends.
--limit N restricts the universe for a quick test run.

Run: python giants_mail.py [--dry-run] [--limit N]
"""

import json
import os
import sys
import time
from datetime import date

import numpy as np
import pandas as pd

from giants_features import (MIN_PE_MONTHS, REG_WIN, VOL_WIN, div_record,
                             slope_r2, ttm_eps)
from lppl_backtest import ROOT, load_config

LIVE = ROOT / 'data' / 'live'
RAW = LIVE / 'raw'
DIV_LEDGER = LIVE / 'dividends_ledger.csv'
SPLIT_LEDGER = LIVE / 'splits_ledger.csv'
R2_TH = 0.7           # frozen winning config
SLOTS = 8
CHUNK = 150           # tickers per yfinance batch request
OVERLAP_DAYS = 21     # calendar days of stored tail re-fetched to verify
TO = 'luming.sjtu@gmail.com'
FROM = 'onboarding@resend.dev'
RESEND_URL = 'https://api.resend.com/emails'


def universe() -> list[str]:
    cfg = load_config()
    d = ROOT / cfg['data']['cache_dir'] / 'ohlcv'
    return sorted(p.stem for p in d.glob('*.parquet')
                  if p.stem != cfg['data']['benchmark'])


def naive_index(idx) -> pd.DatetimeIndex:
    idx = pd.DatetimeIndex(idx)
    if idx.tz is not None:
        idx = idx.tz_localize(None)
    return idx.normalize()


def batch_download(tickers: list[str], **kw) -> pd.DataFrame:
    """yf.download in polite chunks (auto_adjust=False, actions on) with
    one retry for tickers that come back empty."""
    import yfinance as yf

    def _dl(tks: list[str]) -> pd.DataFrame:
        parts = []
        for k in range(0, len(tks), CHUNK):
            chunk = tks[k:k + CHUNK]
            df = yf.download(chunk, auto_adjust=False, actions=True,
                             progress=False, group_by='column',
                             threads=True, **kw)
            if df is not None and len(df):
                if not isinstance(df.columns, pd.MultiIndex):  # 1 ticker
                    df.columns = pd.MultiIndex.from_product(
                        [df.columns, chunk])
                df.index = naive_index(df.index)
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


def load_ledger(path, cols: list[str]) -> pd.DataFrame:
    if path.exists():
        df = pd.read_csv(path, parse_dates=['date'])
        return df[cols]
    return pd.DataFrame(columns=cols)


def append_ledger(path, rows: list[dict], cols: list[str]) -> None:
    if not rows:
        return
    df = pd.DataFrame(rows)[cols]
    df['date'] = pd.to_datetime(df['date']).dt.strftime('%Y-%m-%d')
    header = not path.exists()
    df.to_csv(path, mode='a', header=header, index=False,
              lineterminator='\n')


def declared_amount(amount_current: float, div_date, splits: pd.DataFrame,
                    ticker: str) -> float:
    """Yahoo dividend amounts are in current-share terms; the company
    declared them in the share terms of their day. Undo the splits that
    happened after the dividend."""
    s = splits[(splits['ticker'] == ticker) & (splits['date'] > div_date)]
    return float(amount_current * s['ratio'].prod())


def current_amount(amount_declared: float, div_date, splits: pd.DataFrame,
                   ticker: str) -> float:
    s = splits[(splits['ticker'] == ticker) & (splits['date'] > div_date)]
    return float(amount_declared / s['ratio'].prod())


def compute_adjusted(raw: np.ndarray, dates: pd.DatetimeIndex,
                     divs: pd.DataFrame | None) -> np.ndarray:
    """Total-return series from raw closes + dividends (current-share
    terms) - the exact inverse of giants_features.nominal_prices, so the
    screen matches the research methodology."""
    adj = raw.astype(float).copy()
    if divs is None or not len(divs):
        return adj
    for r in divs.sort_values('date', ascending=False).itertuples():
        loc = int(dates.searchsorted(r.date))
        if loc <= 0 or loc >= len(dates):
            continue
        p_ex = raw[loc]
        if not (np.isfinite(p_ex) and p_ex > r.amount > 0):
            continue
        adj[:loc] *= 1.0 - r.amount / p_ex
    return adj


def extract_ticker(px: pd.DataFrame, t: str) -> pd.DataFrame | None:
    if not len(px) or t not in px['Close'].columns:
        return None
    df = pd.DataFrame({'close': px['Close'][t], 'volume': px['Volume'][t],
                       'dividend': px['Dividends'][t],
                       'split': px['Stock Splits'][t]})
    df = df[df['close'].notna()]
    return df if len(df) else None


def harvest_actions(t: str, df: pd.DataFrame, div_led: pd.DataFrame,
                    split_led: pd.DataFrame, notes: list[str]
                    ) -> tuple[list[dict], list[dict], int]:
    """New ledger rows from a fetched frame; overlapping entries are
    compared and reported, never rewritten."""
    new_splits = []
    known_splits = set(split_led[split_led['ticker'] == t]['date'])
    for dt, ratio in df['split'][df['split'] > 0].items():
        if dt not in known_splits:
            new_splits.append({'date': dt, 'ticker': t,
                               'ratio': float(ratio)})
    all_splits = split_led if not new_splits else (
        pd.DataFrame(new_splits) if not len(split_led)
        else pd.concat([split_led, pd.DataFrame(new_splits)]))
    mine = div_led[div_led['ticker'] == t]
    last = mine['date'].max() if len(mine) else pd.Timestamp.min
    new_divs, n_checked = [], 0
    for dt, amt in df['dividend'][df['dividend'] > 0].items():
        decl = declared_amount(float(amt), dt, all_splits, t)
        if dt > last:
            new_divs.append({'date': dt, 'ticker': t, 'amount': decl})
        else:
            n_checked += 1
            stored = mine[mine['date'] == dt]
            if not len(stored):
                notes.append(f'{t}: dividend {decl:.4f} on '
                             f'{dt.date()} is missing from the ledger '
                             f'(not appended - entries are immutable; '
                             f'add it by hand if the company page '
                             f'confirms it)')
            elif not np.isclose(stored['amount'].iloc[0], decl,
                                rtol=2e-2):
                notes.append(f'{t}: ledger says dividend '
                             f'{stored["amount"].iloc[0]:.4f} on '
                             f'{dt.date()}, a re-fetch says {decl:.4f} '
                             f'- ledger kept, please verify against '
                             f'the company page')
    return new_divs, new_splits, n_checked


def _extend(led: pd.DataFrame, rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return led
    add = pd.DataFrame(rows)
    return add if not len(led) else pd.concat([led, add],
                                              ignore_index=True)


def store_sync(tks: list[str], benchmark: str) -> dict:
    """Seed or append the raw store and the ledgers. Returns counts and
    audit notes for the console and the mail footer."""
    import yfinance as yf
    RAW.mkdir(parents=True, exist_ok=True)
    div_led = load_ledger(DIV_LEDGER, ['date', 'ticker', 'amount'])
    split_led = load_ledger(SPLIT_LEDGER, ['date', 'ticker', 'ratio'])
    notes: list[str] = []
    all_tks = tks + [benchmark]
    stored = {p.stem for p in RAW.glob('*.parquet')}

    if not stored:                                   # first run: deep seed
        print(f'seeding the raw store with full history for '
              f'{len(all_tks)} tickers - a one-time long fetch')
        n_div = n_split = 0
        for k in range(0, len(all_tks), CHUNK):
            chunk = all_tks[k:k + CHUNK]
            px = batch_download(chunk, period='max')
            for t in chunk:
                df = extract_ticker(px, t)
                if df is None:
                    notes.append(f'{t}: seed fetch came back empty')
                    continue
                df[['close', 'volume']].to_parquet(RAW / f'{t}.parquet')
                nd, ns, _ = harvest_actions(t, df, div_led, split_led,
                                            notes)
                append_ledger(DIV_LEDGER, nd, ['date', 'ticker', 'amount'])
                append_ledger(SPLIT_LEDGER, ns, ['date', 'ticker', 'ratio'])
                div_led = _extend(div_led, nd)
                split_led = _extend(split_led, ns)
                n_div += len(nd)
                n_split += len(ns)
            print(f'  seeded {min(k + CHUNK, len(all_tks))}/{len(all_tks)}')
        return {'mode': 'seed', 'n_appended_days': 0, 'n_new_div': n_div,
                'n_new_split': n_split, 'notes': notes}

    # monthly append: fetch a short window covering the stored tail
    bench = pd.read_parquet(RAW / f'{benchmark}.parquet')
    anchor = bench.index.max()
    start = (anchor - pd.Timedelta(days=OVERLAP_DAYS)).strftime('%Y-%m-%d')
    px = batch_download(all_tks, start=start)
    n_appended = n_div = n_split = n_rescaled = 0
    for t in all_tks:
        f = RAW / f'{t}.parquet'
        fresh = extract_ticker(px, t)
        if fresh is None:
            continue
        if not f.exists():                    # new ticker: deep seed one
            try:
                h = yf.Ticker(t).history(period='max', auto_adjust=False,
                                         actions=True)
            except Exception:
                notes.append(f'{t}: not in the store and the deep fetch '
                             f'failed - skipped this month')
                continue
            time.sleep(0.5)
            h.index = naive_index(h.index)
            df = pd.DataFrame({'close': h['Close'], 'volume': h['Volume'],
                               'dividend': h['Dividends'],
                               'split': h['Stock Splits']}).dropna(
                                   subset=['close'])
            df[['close', 'volume']].to_parquet(f)
            nd, ns, _ = harvest_actions(t, df, div_led, split_led, notes)
            append_ledger(DIV_LEDGER, nd, ['date', 'ticker', 'amount'])
            append_ledger(SPLIT_LEDGER, ns, ['date', 'ticker', 'ratio'])
            notes.append(f'{t}: new ticker, deep-seeded '
                         f'({len(df)} days, {len(nd)} dividends)')
            continue
        old = pd.read_parquet(f)
        all_shared = old.index.intersection(fresh.index)
        changed = False
        if len(all_shared):
            # the newest shared bar may be a live quote while the market
            # is open: refresh it silently, immutability starts one day
            # back
            prov = all_shared.max()
            pv = fresh.loc[prov, ['close', 'volume']].to_numpy(dtype=float)
            if not np.allclose(
                    old.loc[prov, ['close', 'volume']].to_numpy(dtype=float),
                    pv, rtol=1e-9, equal_nan=True):
                old.loc[prov, ['close', 'volume']] = pv
                changed = True
        shared = all_shared[all_shared < all_shared.max()] \
            if len(all_shared) else all_shared
        if len(shared):
            a = old.loc[shared, 'close'].to_numpy(dtype=float)
            b = fresh.loc[shared, 'close'].to_numpy(dtype=float)
            ok = np.isclose(a, b, rtol=1e-5)
            if not ok.all():
                ratio = a / b
                near_const = np.isfinite(ratio).all() and \
                    ratio.max() / ratio.min() < 1.001 \
                    and abs(ratio.mean() - 1) > 0.01
                matched = False
                if near_const:
                    try:
                        sp = yf.Ticker(t).splits
                        sp.index = naive_index(sp.index)
                        recent = sp[sp.index > anchor - pd.Timedelta(days=45)]
                        for sdt, sratio in recent.items():
                            if np.isclose(float(sratio), ratio.mean(),
                                          rtol=0.01):
                                old['close'] /= float(sratio)
                                old['volume'] *= float(sratio)
                                changed = True
                                matched = True
                                n_rescaled += 1
                                notes.append(
                                    f'AUDIT {t}: {sratio:.4g}-for-1 split '
                                    f'on {sdt.date()} - stored history '
                                    f'rescaled by the announced ratio')
                                break
                    except Exception:
                        pass
                if not matched:
                    bad = int((~ok).sum())
                    notes.append(
                        f'WARNING {t}: {bad}/{len(shared)} overlap days '
                        f'disagree with the fresh download (max rel '
                        f'{np.nanmax(np.abs(a / b - 1)):.2%}) and it is '
                        f'not a known split - fresh values used for '
                        f'those days')
                    old.loc[shared, ['close', 'volume']] = \
                        fresh.loc[shared, ['close', 'volume']].to_numpy()
                    changed = True
        add = fresh.loc[fresh.index > old.index.max()]
        if len(add):
            old = pd.concat([old, add[['close', 'volume']]])
            changed = True
            n_appended += len(add)
        if changed:
            old.to_parquet(f)
        nd, ns, _ = harvest_actions(t, fresh, div_led, split_led, notes)
        append_ledger(DIV_LEDGER, nd, ['date', 'ticker', 'amount'])
        append_ledger(SPLIT_LEDGER, ns, ['date', 'ticker', 'ratio'])
        div_led = _extend(div_led, nd)
        split_led = _extend(split_led, ns)
        n_div += len(nd)
        n_split += len(ns)
    return {'mode': 'append', 'n_appended_days': n_appended,
            'n_new_div': n_div, 'n_new_split': n_split,
            'n_rescaled': n_rescaled, 'notes': notes}


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
    month), nominal price over TTM reported EPS."""
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


def div_streak(ysum: pd.Series, last_year: int) -> int:
    n, y = 0, last_year
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
            n_liquid: int, n_qual: int, notes: list[str]) -> tuple[str, str]:
    state = 'GREEN' if green else 'RED'
    light_line = (f"Market light: {state} - SPY {light['spy']:.0f} vs "
                  f"200-day average {light['sma200']:.0f}; 20-day "
                  f"volatility {light['vol20']:.2%} vs calm threshold "
                  f"{light['vol_thr']:.2%}.")
    audit = ''
    if notes:
        lines = ''.join(f'<li>{n}</li>' for n in notes[:20])
        more = f'<li>… and {len(notes) - 20} more, see console</li>' \
            if len(notes) > 20 else ''
        audit = (f"<p style='color:#a60;font-size:12px'><b>Data audit "
                 f"notes:</b></p><ul style='color:#a60;font-size:12px'>"
                 f'{lines}{more}</ul>')
    footer = (audit +
              "<hr><p style='color:#666;font-size:12px'>Steady Giants "
              "monthly screen. Universe: S&P 1500 snapshot 2026-08 (a "
              "known limitation: newly listed or newly added companies "
              "are not seen). Qualification: lowest-volatility third of "
              "the liquid universe, 5-year straight-line price growth "
              "with R&#178; &#8805; 0.7, dividends every one of the last "
              "5 years with no cut over 20%, and a price/earnings ratio "
              "not above the stock's own 90th-percentile history. Prices "
              "come from a local immutable store with append-only "
              "dividend and split ledgers (data/live/). This is a "
              "screen, not advice; it knows nothing about what you "
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
    cfg = load_config()
    d = cfg['data']
    tks = universe()
    if limit:
        tks = tks[:limit]

    sync = store_sync(tks, d['benchmark'])
    print(f"store: {sync['mode']}, {sync['n_appended_days']} ticker-days "
          f"appended, {sync['n_new_div']} new dividends, "
          f"{sync['n_new_split']} new splits, "
          f"{len(sync['notes'])} audit notes")
    for n in sync['notes'][:15]:
        print(f'  {n}')

    div_led = load_ledger(DIV_LEDGER, ['date', 'ticker', 'amount'])
    split_led = load_ledger(SPLIT_LEDGER, ['date', 'ticker', 'ratio'])

    def current_divs(t: str) -> pd.DataFrame:
        mine = div_led[div_led['ticker'] == t]
        if not len(mine):
            return mine
        mine = mine.copy()
        mine['amount'] = [current_amount(a, dt, split_led, t)
                          for a, dt in zip(mine['amount'], mine['date'])]
        return mine

    spy_raw = pd.read_parquet(RAW / f"{d['benchmark']}.parquet")
    spy_adj = compute_adjusted(spy_raw['close'].to_numpy(), spy_raw.index,
                               current_divs(d['benchmark']))
    spy = pd.Series(spy_adj, index=spy_raw.index)
    print(f'store spans through {spy.index[-1].date()}')
    green, light = market_light(spy)
    print(f"light: {'GREEN' if green else 'RED'} "
          f"(SPY {light['spy']:.0f} vs sma200 {light['sma200']:.0f}, "
          f"vol20 {light['vol20']:.2%} vs thr {light['vol_thr']:.2%})")

    # phase 1: price screens from the local store, locally adjusted
    stats, raws = [], {}
    for t in tks:
        f = RAW / f'{t}.parquet'
        if not f.exists():
            continue
        df = pd.read_parquet(f)
        if len(df) < REG_WIN or df.index[-1] < spy.index[-1] - \
                pd.Timedelta(days=7):
            continue
        raws[t] = df
        c_raw = df['close'].to_numpy(dtype=float)
        adj = compute_adjusted(c_raw, df.index, current_divs(t))
        dollar = np.nanmean(
            (c_raw * df['volume'].to_numpy())[-d['dollar_volume_window']:])
        if not (c_raw[-1] > d['min_price'] and np.isfinite(dollar)
                and dollar > d['min_dollar_volume']):
            continue
        w = adj[-REG_WIN:]
        if not np.all(np.isfinite(w)):
            continue
        r3 = pd.Series(adj[-VOL_WIN - 1:]).pct_change().to_numpy()[1:]
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

    # phase 2: dividend record from the ledger, EPS fetched fresh
    import yfinance as yf
    rows = []
    last_year = date.today().year - 1
    for t in sorted(surv.index):
        divs = current_divs(t)
        if not len(divs):
            continue
        ysum = divs.groupby(divs['date'].dt.year)['amount'].sum()
        paid, cut = div_record(ysum, last_year + 1)
        if not paid or cut:
            continue
        try:
            e = yf.Ticker(t).get_earnings_dates(limit=100)
        except Exception:
            continue
        time.sleep(0.2)
        if e is None or not len(e):
            continue
        rep = e['Reported EPS'].dropna().sort_index()
        if len(rep) < 4:
            continue
        eps_dates = np.array([np.datetime64(pd.Timestamp(x).date())
                              for x in rep.index], dtype='datetime64[ns]')
        eps_vals = rep.to_numpy(dtype=float)
        df = raws[t]
        pe, p90 = pe_history(df['close'].to_numpy(dtype=float), df.index,
                             eps_dates, eps_vals)
        if not np.isfinite(pe):
            continue
        rows.append({'ticker': t, 'r2': float(surv.loc[t, 'r2']),
                     'vol': float(surv.loc[t, 'vol']),
                     'trend': float(np.exp(surv.loc[t, 'slope'] * 252) - 1),
                     'pe': pe, 'p90': p90,
                     'pe_vs': pe / p90 - 1 if np.isfinite(p90) else np.nan,
                     'streak': div_streak(ysum, last_year),
                     'buyable': not (np.isfinite(p90) and pe > p90)})
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
                            n_liquid=len(liq), n_qual=n_qual,
                            notes=sync['notes'])
    send(subject, body, dry_run)


if __name__ == '__main__':
    main()

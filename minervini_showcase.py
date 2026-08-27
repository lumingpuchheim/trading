"""Anatomy of individual Minervini entries — the audit you can eyeball.

One figure per trade, two panels:

  top    — 15 months of closes with SMA50/150/200, so the nine trend
           conditions are visually checkable (price above all three,
           50 > 150 > 200, all rising), plus the base shaded, each
           contraction labelled with its depth, the pivot line, the buy
           and the exit.
  bottom — volume with its 50-day mean, the dry-up days that qualified
           the base marked, and the breakout day's volume multiple.

Run: python minervini_showcase.py              # default three trades
     python minervini_showcase.py DOCN CMI     # pick your own tickers
     python minervini_showcase.py --period dev
"""

import sys

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import minervini as mv
from lppl_backtest import ROOT, load_config

PRE, POST = 320, 60
DEFAULT = {'test': ['DOCN', 'CMI', 'POWL'], 'dev': ['V', 'BJRI', 'NDSN']}


def base_and_contractions(close: np.ndarray, zz: dict, upto: int, cfg: dict):
    """Exactly what `_base_day` used on the setup day: the rim from
    `anchor_base`, then the contractions whose high sits at or after it,
    newest first."""
    anc = mv.anchor_base(close, upto, cfg)
    if anc is None:
        return upto, []
    _, b_i = anc
    last = int(np.searchsorted(zz['confirm'], upto, side='right')) - 1
    out, s = [], last
    while s >= 1 and zz['kind'][s] == -1 and zz['kind'][s - 1] == 1             and int(zz['idx'][s - 1]) >= b_i:
        hi_p, lo_p = float(zz['price'][s - 1]), float(zz['price'][s])
        out.append({'hi_i': int(zz['idx'][s - 1]), 'lo_i': int(zz['idx'][s]),
                    'hi': hi_p, 'lo': lo_p, 'd': (hi_p - lo_p) / hi_p})
        s -= 2
    return b_i, out


def draw(ticker: str, trade, cfg: dict, results) -> None:
    m = cfg['minervini']
    raw = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'ohlcv'
                          / f'{ticker}.parquet')
    close = raw['close'].to_numpy()
    volume = raw['volume'].to_numpy()
    pos = {d: i for i, d in enumerate(raw.index)}
    buy_i = pos[trade.entry_date]
    exit_i = pos.get(trade.exit_date, len(close) - 1)
    setup_i = buy_i - 1

    s = mv.signals({k: raw[k].to_numpy()
                    for k in ('open', 'high', 'close', 'volume')}, cfg)
    pivot = s['pivot'][setup_i]
    zz = s['zigzag']
    base_start, chain = base_and_contractions(close, zz, setup_i, cfg)

    a, b = max(0, buy_i - PRE), min(len(close) - 1, exit_i + POST)
    idx = np.arange(a, b + 1)
    dates = raw.index[idx]
    c = pd.Series(close)
    sma = {n: c.rolling(n).mean().to_numpy() for n in (50, 150, 200)}
    v50 = pd.Series(volume).rolling(m['dryup_long']).mean().to_numpy()

    fig, (ax, axv) = plt.subplots(
        2, 1, figsize=(15, 9), sharex=True,
        gridspec_kw={'height_ratios': [3, 1], 'hspace': 0.08})

    ax.axvspan(raw.index[base_start], raw.index[setup_i], color='0.92',
               zorder=0, label='the base')
    ax.plot(dates, close[idx], color='black', lw=1.2, label='close')
    for n, col in zip((50, 150, 200), ('tab:blue', 'tab:orange', 'tab:red')):
        ax.plot(dates, sma[n][idx], lw=1.0, color=col, alpha=0.85,
                label=f'SMA{n}')
    ax.axhline(pivot, color='darkorange', ls='--', lw=1.2,
               label=f'pivot {pivot:.2f}')
    ax.axhline(trade.entry_px * cfg['minervini_trading']['stop_loss'],
               color='crimson', ls=':', lw=1.0, label='8% stop')

    for k, ct in enumerate(chain):
        ax.annotate('', xy=(raw.index[ct['lo_i']], ct['lo']),
                    xytext=(raw.index[ct['hi_i']], ct['hi']),
                    arrowprops=dict(arrowstyle='->', color='purple', lw=1.4))
        ax.text(raw.index[ct['lo_i']], ct['lo'] * 0.985,
                f"-{ct['d']:.1%}", color='purple', fontsize=10,
                ha='center', va='top', fontweight='bold')

    ax.plot(raw.index[buy_i], trade.entry_px, marker='^', ms=13,
            color='green', zorder=5, label=f'BUY {trade.entry_px:.2f}')
    ax.plot(raw.index[exit_i], trade.exit_px, marker='v', ms=13,
            color='crimson', zorder=5,
            label=f'exit {trade.exit_px:.2f} ({trade.exit_reason})')

    ok = (close[setup_i] > sma[50][setup_i] > sma[150][setup_i]
          > sma[200][setup_i])
    rising = sma[200][setup_i] > sma[200][setup_i - m['sma_slow_rising_lookback']]
    ax.set_title(
        f'{ticker}  buy {raw.index[buy_i].date()} at {trade.entry_px:.2f}  '
        f'-> {trade.ret_net:+.1%} in {trade.days_held} days\n'
        f'trend template on the setup day: close > SMA50 > SMA150 > SMA200 '
        f'= {ok}, SMA200 rising = {rising}, '
        f'base {setup_i - base_start} days, {len(chain)} contractions')
    ax.legend(fontsize=8, loc='upper left', ncol=2)
    ax.grid(alpha=0.3)
    ax.set_ylabel('price')

    dry = (pd.Series(volume).rolling(m['dryup_window']).min().to_numpy()
           <= m['dryup_max_ratio'] * v50)
    axv.bar(dates, volume[idx], color='0.7', width=1.0)
    axv.plot(dates, v50[idx], color='tab:blue', lw=1.0, label='50d mean volume')
    axv.plot(dates, m['dryup_max_ratio'] * v50[idx], color='tab:green',
             lw=0.9, ls='--', label='75% of it (dry-up line)')
    base_days = np.arange(base_start, setup_i + 1)
    quiet = [i for i in base_days if volume[i] <= m['dryup_max_ratio'] * v50[i]]
    if quiet:
        axv.bar(raw.index[quiet], volume[quiet], color='tab:green', width=1.0,
                label=f'{len(quiet)} quiet days in the base')
    axv.bar([raw.index[buy_i]], [volume[buy_i]], color='crimson', width=1.4,
            label=f'breakout volume {volume[buy_i] / v50[buy_i]:.2f}x')
    axv.legend(fontsize=8, loc='upper left', ncol=2)
    axv.grid(alpha=0.3)
    axv.set_ylabel('volume')
    axv.set_xlim(dates[0], dates[-1])
    ax.set_xlim(dates[0], dates[-1])

    out = results / f'minervini_buy_{ticker}.png'
    fig.savefig(out, dpi=120, bbox_inches='tight')
    plt.close(fig)
    print(f'  {ticker}: base {setup_i - base_start}d, {len(chain)} contractions '
          f'({", ".join(f"-{x['d']:.1%}" for x in reversed(chain))}), '
          f'pivot {pivot:.2f}, dry-up days {len(quiet)}, '
          f'breakout volume {volume[buy_i] / v50[buy_i]:.2f}x -> {out.name}')


def main() -> None:
    cfg = load_config()
    results = ROOT / cfg['backtest']['results_dir']
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    period = 'dev' if '--period' in sys.argv and 'dev' in sys.argv else 'test'
    trades = pd.read_csv(results / f'minervini_v2_moc_{period}_trades.csv',
                         parse_dates=['entry_date', 'exit_date'])
    want = args or DEFAULT[period]
    print(f'{period} period:')
    for t in want:
        row = trades[trades['ticker'] == t]
        if not len(row):
            print(f'  {t}: no trade in {period}')
            continue
        draw(t, row.nlargest(1, 'ret_net').iloc[0], cfg, results)


if __name__ == '__main__':
    main()

"""Anatomy of the Minervini breakout failures (MINERVINI_SPEC.md audit).

Two pictures, both from the frozen signal panel:

1. `minervini_failure_cases.png` — the six worst test-period trades, each
   with its base, its pivot line, the trigger day, the fill, the exit and
   60 days of aftermath. The question each panel answers is whether the
   entry was wrong or the exit was: did the stock recover after we sold?

2. `minervini_event_study.png` — every trigger in the universe, not just
   the ones the portfolio had a slot for: median price path from 60 days
   before to 60 days after the breakout, dev vs test, against the median
   path of random template-passing days (the control's entries).

Run: python minervini_failures.py
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

import minervini as mv
from lppl_backtest import ROOT, load_config
from minervini_backtest import build_panel

N_CASES = 6
PRE, POST = 90, 60
WINDOW = 60          # event-study half-width


def case_panels(panel: dict, cfg: dict, results) -> None:
    cal = panel['calendar']
    pos = {d: i for i, d in enumerate(cal)}
    trades = pd.read_csv(results / 'minervini_test_trades.csv',
                         parse_dates=['entry_date', 'exit_date'])
    worst = trades.nsmallest(N_CASES, 'ret_net')

    fig, axes = plt.subplots(2, 3, figsize=(16, 9))
    for ax, r in zip(axes.ravel(), worst.itertuples()):
        j = panel['tickers'].index(r.ticker)
        entry_i, exit_i = pos[r.entry_date], pos[r.exit_date]
        trig_i = entry_i - 1
        a = max(0, trig_i - PRE)
        b = min(len(cal) - 1, exit_i + POST)
        idx = np.arange(a, b + 1)
        close = panel['close'][idx, j]
        piv = panel['pivot'][trig_i, j]

        ax.plot(cal[idx], close, color='black', lw=1.1, label='close')
        ax.plot(cal[idx], panel['sma50'][idx, j], color='steelblue', lw=0.9,
                label='SMA50')
        ax.axhline(piv, color='darkorange', ls='--', lw=1.0,
                   label=f'pivot {piv:.2f}')
        ax.axhline(r.entry_px * cfg['minervini_trading']['stop_loss'],
                   color='crimson', ls=':', lw=0.9, label='8% stop')
        age = int(mv.pivot(panel['close'][:, j], cfg)[1][trig_i - 1])
        ax.axvspan(cal[trig_i - 1 - age], cal[trig_i], color='0.9', zorder=0)
        ax.plot(cal[entry_i], r.entry_px, marker='^', ms=9, color='green',
                label='fill (next open)')
        ax.plot(cal[exit_i], r.exit_px, marker='v', ms=9, color='crimson',
                label=f'exit: {r.exit_reason}')

        after = panel['close'][min(b, exit_i + POST), j]
        ax.set_title(f'{r.ticker}  {r.ret_net:+.1%} in {r.days_held}d  '
                     f'({r.exit_reason}); {POST}d later '
                     f'{after / r.exit_px - 1:+.0%} vs exit', fontsize=10)
        ax.grid(alpha=0.3)
        ax.tick_params(labelsize=8)
    axes[0, 0].legend(fontsize=7, loc='upper left')
    fig.suptitle('Minervini test-period breakouts: the six worst trades '
                 '(grey = base, orange = pivot it cleared)')
    fig.tight_layout()
    fig.savefig(results / 'minervini_failure_cases.png', dpi=120)
    plt.close(fig)


def event_study(panel: dict, cfg: dict, results) -> None:
    bt = cfg['backtest']
    cal = panel['calendar']
    close = panel['close']
    n = len(cal)
    dev_end = int(cal.searchsorted(pd.Timestamp(bt['dev_end']), side='right'))
    rng = np.random.default_rng(bt['random_seed'])

    def paths(mask: np.ndarray, limit: int | None = None) -> np.ndarray:
        rows, cols = np.nonzero(mask)
        keep = (rows >= WINDOW) & (rows < n - WINDOW)
        rows, cols = rows[keep], cols[keep]
        if limit is not None and len(rows) > limit:
            pick = rng.choice(len(rows), limit, replace=False)
            rows, cols = rows[pick], cols[pick]
        out = np.full((len(rows), 2 * WINDOW + 1), np.nan)
        for k, (i, j) in enumerate(zip(rows, cols)):
            seg = close[i - WINDOW:i + WINDOW + 1, j]
            if np.isfinite(seg).all() and seg[WINDOW] > 0:
                out[k] = seg / seg[WINDOW]
        return out[np.isfinite(out).all(axis=1)]

    plt.figure(figsize=(12, 7))
    x = np.arange(-WINDOW, WINDOW + 1)
    styles = {'dev': ('tab:blue', slice(0, dev_end)),
              'test': ('tab:red', slice(dev_end, n))}
    stats = []
    for name, (color, sl) in styles.items():
        m = np.zeros_like(panel['trigger'])
        m[sl] = panel['trigger'][sl]
        p = paths(m)
        c = np.zeros_like(panel['template'])
        c[sl] = panel['template'][sl]
        pc = paths(c, limit=4000)
        plt.plot(x, np.median(p, axis=0), color=color, lw=2,
                 label=f'{name} triggers (n={len(p)})')
        plt.plot(x, np.median(pc, axis=0), color=color, lw=1, ls='--',
                 alpha=0.7, label=f'{name} random template days (n={len(pc)})')
        stats.append({'period': name, 'n_triggers': len(p),
                      'med_60d_after_trigger': float(np.median(p[:, -1]) - 1),
                      'med_60d_after_template': float(np.median(pc[:, -1]) - 1),
                      'share_below_entry_at_60d': float((p[:, -1] < 1).mean())})
    plt.axvline(0, color='black', lw=0.8)
    plt.axhline(1.0, color='black', lw=0.6, alpha=0.4)
    plt.xlabel('trading days from the breakout trigger')
    plt.ylabel('close / close on the trigger day (median)')
    plt.title('Every Minervini trigger in the universe: median path around '
              'the breakout')
    plt.legend()
    plt.grid(alpha=0.3)
    plt.tight_layout()
    plt.savefig(results / 'minervini_event_study.png', dpi=120)
    plt.close()
    df = pd.DataFrame(stats)
    df.to_csv(results / 'minervini_event_study.csv', index=False)
    print(df.to_string(index=False, float_format=lambda v: f'{v:.4f}'))


def main() -> None:
    cfg = load_config()
    results = ROOT / cfg['backtest']['results_dir']
    panel = build_panel(cfg)
    case_panels(panel, cfg, results)
    event_study(panel, cfg, results)
    print(f'charts -> {results}/minervini_failure_cases.png, '
          f'minervini_event_study.png')


if __name__ == '__main__':
    main()

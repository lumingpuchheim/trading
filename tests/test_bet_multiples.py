"""One bet, one multiple, one average — and the book and the ledger agree.

Three numbers used to be in circulation for "what a bet returns": an
arithmetic mean of the per-signal ledger (1.0122), an arithmetic mean of
the simulator's trade ROWS (1.0406) and a geometric mean of the same rows
(1.0302). None of them was the same quantity, and they were compared with
each other for weeks.

The rule these tests pin down: a bet is a POSITION. Whether it sold in one
piece or two is bookkeeping, its dividends are part of what the euro
became, and multiples are averaged by multiplying — never by adding.

`test_book_and_ledger_price_the_same_bet` is the important one. It runs
one hand-built price path through BOTH pricers — `simulate()` with the
portfolio around it and `price_bet()` with nothing around it — and
requires the same multiple out of each. If a future change moves one
convention (which day an ex-date pays, which fill a half-sale gets) the
two stop agreeing and this test says so.
"""

from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import yaml

from geostats import bet_multiples, geo_per_bet
from minervini_backtest import apply_v5, simulate
from minervini_bets import price_bet

ENTRY_I = 60          # 60 flat days first, so the SMA50 exists


# ------------------------------------------------------------- unit tests

def _rows(*specs) -> pd.DataFrame:
    """specs: (ticker, entry_date, ret_net, weight[, div_eur, bet_eur])."""
    cols = ['ticker', 'entry_date', 'ret_net', 'weight', 'div_eur', 'bet_eur']
    out = [dict(zip(cols, s + (0.0, 1.0)[: max(0, 6 - len(s))]))
           for s in specs]
    return pd.DataFrame(out)


def test_an_unsplit_position_is_one_bet_at_its_own_return():
    m = bet_multiples(_rows(('AAA', '2020-01-02', -0.08, 1.0)))
    assert m.tolist() == pytest.approx([0.92])


def test_a_split_winner_is_ONE_bet_not_two():
    # half banked at +20%, the rider exits at +150%: one bet, blended
    t = _rows(('AAA', '2020-01-02', 0.20, 0.5),
              ('AAA', '2020-01-02', 1.50, 0.5))
    m = bet_multiples(t)
    assert len(m) == 1
    assert m.iloc[0] == pytest.approx(0.5 * 1.20 + 0.5 * 2.50)


def test_the_split_is_what_used_to_double_count_winners():
    # one winner that split, one loser that did not. Averaging ROWS says
    # the book is up; averaging BETS says it is flat.
    t = _rows(('WIN', '2020-01-02', 0.20, 0.5),
              ('WIN', '2020-01-02', 1.00, 0.5),
              ('LOSS', '2020-01-02', -0.50, 1.0))
    rows = float((1.0 + t['ret_net']).mean())
    bets = geo_per_bet(t)
    assert rows == pytest.approx((1.20 + 2.00 + 0.50) / 3)     # 1.2333
    assert bets == pytest.approx(np.sqrt(1.60 * 0.50))         # 0.8944
    assert bets < 1.0 < rows


def test_same_ticker_different_entry_dates_are_different_bets():
    t = _rows(('AAA', '2020-01-02', 0.10, 1.0),
              ('AAA', '2021-06-01', -0.10, 1.0))
    assert len(bet_multiples(t)) == 2


def test_dividends_count_toward_what_the_euro_became():
    # 10,000 committed, 250 of dividends collected: +2.5% on top
    t = _rows(('AAA', '2020-01-02', 0.10, 1.0, 250.0, 10_000.0))
    assert bet_multiples(t).iloc[0] == pytest.approx(1.10 + 0.025)


def test_dividends_on_both_legs_of_a_split_add_up():
    t = _rows(('AAA', '2020-01-02', 0.20, 0.5, 100.0, 10_000.0),
              ('AAA', '2020-01-02', 0.60, 0.5, 150.0, 10_000.0))
    assert bet_multiples(t).iloc[0] == pytest.approx(
        0.5 * 1.20 + 0.5 * 1.60 + 250.0 / 10_000.0)


def test_runs_without_the_columns_fall_back_to_halves_and_no_dividends():
    t = pd.DataFrame({'ticker': ['AAA', 'AAA', 'BBB'],
                      'entry_date': ['2020-01-02', '2020-01-02', '2020-03-04'],
                      'ret_net': [0.20, 1.00, -0.30]})
    m = bet_multiples(t)
    assert len(m) == 2
    assert m.loc['AAA|2020-01-02'] == pytest.approx(0.5 * 1.20 + 0.5 * 2.00)


def test_geo_per_bet_is_the_geometric_mean_of_those_multiples():
    t = _rows(('AAA', '2020-01-02', 0.20, 1.0),
              ('BBB', '2020-01-02', -0.20, 1.0),
              ('CCC', '2020-01-02', 0.50, 1.0))
    assert geo_per_bet(t) == pytest.approx(
        float(np.exp(np.mean(np.log([1.2, 0.8, 1.5])))))


def test_no_trades_is_nan_not_a_crash():
    assert np.isnan(geo_per_bet(pd.DataFrame()))


# ------------------------------------------- the book against the ledger

def _cfg():
    """v5r with costs switched OFF, so the two pricers can be compared to
    the cent: the ledger charges no commission by design and whole-share
    rounding is exact on this path (100 shares, 50 sold)."""
    cfg = apply_v5(yaml.safe_load(
        open(Path(__file__).parent.parent / 'config.yaml')))
    cfg['minervini_trading']['reentry_fast'] = True
    cfg['minervini_trading']['cost_per_side'] = 0.0
    return cfg


def _panel(close, div):
    """One ticker, open == close, volume neutral, market always green,
    one armed entry at ENTRY_I. `div` is the cash paid per share per day."""
    close = np.asarray(close, float)
    n = len(close)
    cal = pd.bdate_range('2020-01-02', periods=n)
    col = close.reshape(n, 1)
    panel = {
        'calendar': cal, 'tickers': ['TEST'],
        'open': col.copy(), 'close': col.copy(),
        'sma50': pd.Series(close).rolling(50).mean().to_numpy().reshape(n, 1),
        'volx': np.ones((n, 1)),
        'fill_moc': col.copy(), 'fill_px': col.copy(),
        'trigger_moc': np.zeros((n, 1), bool),
        'trigger': np.zeros((n, 1), bool),
        'vol_ok': np.ones((n, 1), bool),
        'last_i': np.array([n - 1]), 'green': np.ones(n, bool),
        'spy_close': pd.Series(np.full(n, 100.0), index=cal),
        'rsl_hi': np.ones((n, 1), bool), 'weak': np.zeros((n, 1)),
        'rs': np.zeros((n, 1)),
        'rep_label': np.zeros((n, 1), int),
        'div': np.asarray(div, float).reshape(n, 1),
    }
    panel['trigger_moc'][ENTRY_I, 0] = True
    panel['trigger'][ENTRY_I, 0] = True
    empty = np.array([], dtype=int)
    pool = [np.array([0]) if i == ENTRY_I - 1 else empty for i in range(n)]
    return panel, pool


def _ledger_y(panel, cfg):
    """`price_bet` on the same signal, the ledger's own way."""
    tr = cfg['minervini_trading']
    p = {'stop_loss': tr['stop_loss'],
         'be_level': 1.0 + tr['breakeven_r'] * (1.0 - tr['stop_loss']),
         'strength_sell_at': tr['strength_sell_at'],
         'strength_sell_frac': tr['strength_sell_frac'],
         'protect_days': tr['protect_days'],
         'decisive_break_frac': tr['decisive_break_frac'],
         'decisive_volume': tr['decisive_volume'],
         'fix_egg': False}
    arr = {k: panel[k] for k in ('close', 'open', 'sma50', 'volx', 'last_i',
                                 'tickers', 'rep_label', 'rs', 'div',
                                 'calendar', 'green')}
    return price_bet(ENTRY_I, 0, arr, p)


# entry at 100; flat to day 14; +25% on day 15 arms both the breakeven and
# the strength half-sale; the half fills at 130 on day 16; day 17 closes
# back under the entry so the breakeven exit fills at 90 on day 18.
# Dividends of 1.00 land on day 5 (whole position), day 16 (the half-sale
# fills that morning, so the whole position still collects) and day 18
# (the exit fills that morning, so the surviving half still collects).
AFTER = [100.0] * 15 + [125.0, 130.0, 95.0, 90.0] + [90.0] * 5
PATH = np.concatenate([np.full(ENTRY_I, 100.0), AFTER])
DIV = np.zeros(len(PATH))
DIV[ENTRY_I + 5] = DIV[ENTRY_I + 16] = DIV[ENTRY_I + 18] = 1.0


def test_book_and_ledger_price_the_same_bet():
    cfg = _cfg()
    panel, pool = _panel(PATH, DIV)
    trades, _, _, _ = simulate(panel, cfg, (0, len(PATH) - 1),
                               pool_days=pool, moc=True)

    assert list(trades['exit_reason']) == ['strength', 'breakeven']
    mult = bet_multiples(trades)
    assert len(mult) == 1, 'a position that sold in two pieces is ONE bet'

    y = _ledger_y(panel, cfg)['y']
    # half out at 130, half out at 90, 2.50 of dividends per 100 committed
    assert y == pytest.approx(1.125)
    assert mult.iloc[0] == pytest.approx(y)


def test_dropping_the_dividends_would_understate_the_bet():
    """The same path with the dividend column removed prices lower — the
    gap is exactly the cash, and it is what the simulator used to lose."""
    cfg = _cfg()
    panel, pool = _panel(PATH, DIV)
    trades, _, _, _ = simulate(panel, cfg, (0, len(PATH) - 1),
                               pool_days=pool, moc=True)
    with_div = bet_multiples(trades).iloc[0]
    without = bet_multiples(trades.drop(columns=['div_eur'])).iloc[0]
    assert with_div - without == pytest.approx(0.025)


def test_the_row_average_is_not_the_bet_and_this_path_shows_it():
    """One position, two rows: +30% and -10%. Averaging rows calls the
    bet +10%; the bet actually returned +12.5% with its dividends and
    +10% without, and the +30% row was only ever half the money."""
    cfg = _cfg()
    panel, pool = _panel(PATH, DIV)
    trades, _, _, _ = simulate(panel, cfg, (0, len(PATH) - 1),
                               pool_days=pool, moc=True)
    assert len(trades) == 2
    assert list(trades['weight']) == pytest.approx([0.5, 0.5])
    assert float(trades['div_eur'].sum()) == pytest.approx(250.0)
    assert float(trades['bet_eur'].iloc[0]) == pytest.approx(10_000.0)

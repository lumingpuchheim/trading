"""Signal and email-report behaviour (no network)."""

import numpy as np
import pandas as pd
import pytest

from lppl_backtest import load_config
from sim.email_report import build_email
from sim.signals import LPPL_DIP2, STEADY_GIANTS, flag_state

CFG = load_config()


def _series(values: np.ndarray) -> pd.Series:
    idx = pd.bdate_range('2020-01-01', periods=len(values))
    return pd.Series(values, index=idx)


def test_flag_state_marks_a_textbook_bubble_and_ignores_a_random_walk():
    n = 700
    t = np.arange(n, dtype=float)
    dt = (n - 1 + 30.0) - t
    # same shape as tests/test_lppl.py's fixture: damping m|B|/(w|C|) > 1,
    # so the fit qualifies instead of being rejected as too oscillatory
    bubble = np.exp(5.0 - 0.05 * dt ** 0.5
                    + 0.002 * dt ** 0.5 * np.cos(8.0 * np.log(dt)))
    st = flag_state(_series(bubble), CFG)
    assert st['flagged2'] and st['votes'] >= 2
    assert st['tc_date'] is not None and st['tc_ahead'] > 0

    rng = np.random.default_rng(5)
    walk = 50 * np.exp(np.cumsum(rng.normal(0, 0.012, n)))
    assert not flag_state(_series(walk), CFG)['flagged2']


def test_flag_state_is_safe_on_short_history():
    st = flag_state(_series(np.linspace(10, 20, 50)), CFG)
    assert st['votes'] == 0 and not st['flagged2']


def test_email_labels_every_recommendation_with_its_source():
    recs = [{'symbol': 'AAA', 'source': LPPL_DIP2, 'price': 12.5,
             'buyable': True, 'reason': '', 'detail': 'votes 3/5'},
            {'symbol': 'KO', 'source': STEADY_GIANTS, 'price': 90.0,
             'buyable': False, 'reason': 'P/E 30.1 above its own history p90',
             'detail': '5y R2 0.95'}]
    warns = [{'symbol': 'SMCI', 'level': 'CERTIFIED (3+ of 5)', 'votes': 4,
              'tc_date': '2026-11-02', 'r2': 0.96, 'price': 40.0,
              'warn': True}]
    books = [{'name': 'main', 'equity': 20500.0, 'cash': 1000.0,
              'positions': 3}]
    light = {'green': True, 'trend': True, 'calm': True, 'spy': 766.0,
             'sma200': 706.0}
    subject, text, html = build_email('2026-08-30', light, recs, warns, books)

    assert '1 buyable' in subject and '1 bubble warning' in subject
    # both systems appear as their own section AND as a per-row label
    assert 'LPPL_DIP2 (bubble dip-buyer)' in html
    assert 'STEADY_GIANTS (compounders)' in html
    assert html.count('LPPL_DIP2') >= 2 and html.count('STEADY_GIANTS') >= 2
    assert 'BLOCKED — P/E 30.1 above its own history p90' in html
    assert '[LPPL_DIP2] AAA' in text and '[STEADY_GIANTS] KO' in text
    assert 'SMCI' in html and 'CERTIFIED' in html


def test_email_red_light_is_explained():
    light = {'green': False, 'trend': False, 'calm': True, 'spy': 600.0,
             'sma200': 700.0}
    _, _, html = build_email('2026-08-30', light, [], [], [])
    assert 'RED — no new entries (trend down)' in html

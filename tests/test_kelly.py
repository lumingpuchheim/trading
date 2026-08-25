import pytest

from learn import kelly_fraction


def test_known_values():
    # p/L - (1-p)/W: 0.6/0.08 - 0.4/0.10 = 7.5 - 4.0 = 3.5
    assert kelly_fraction(0.6, 0.10, 0.08) == pytest.approx(3.5)


def test_breakeven_bet_is_zero():
    # p=0.5, W=L=0.1: 5 - 5 = 0
    assert kelly_fraction(0.5, 0.1, 0.1) == pytest.approx(0.0)


def test_negative_edge_clips_to_zero():
    # p=0.3, W=0.08, L=0.08: 3.75 - 8.75 < 0 -> clipped
    assert kelly_fraction(0.3, 0.08, 0.08) == 0.0


def test_degenerate_inputs_are_zero():
    assert kelly_fraction(0.6, 0.0, 0.08) == 0.0   # no winning payoff
    assert kelly_fraction(0.6, 0.10, 0.0) == 0.0   # no observed loss size


def test_higher_win_probability_never_lowers_the_fraction():
    lo = kelly_fraction(0.4, 0.12, 0.08)
    hi = kelly_fraction(0.6, 0.12, 0.08)
    assert hi >= lo

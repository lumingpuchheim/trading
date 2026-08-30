import numpy as np
import pytest

from geostats import account_growth_per_bet, geo_mean_per_euro


def test_unweighted_equals_plain_geometric_mean():
    mult = [1.10, 0.90, 1.20]
    expected = float(np.exp(np.mean(np.log(mult))))
    assert geo_mean_per_euro(mult) == pytest.approx(expected)


def test_equal_weights_match_unweighted():
    mult = [1.10, 0.90, 1.20]
    assert geo_mean_per_euro(mult, [0.07, 0.07, 0.07]) == pytest.approx(
        geo_mean_per_euro(mult))


def test_known_weighted_value():
    # exp((0.05*ln 1.2 + 0.07*ln 0.9) / 0.12)
    expected = float(np.exp(
        (0.05 * np.log(1.2) + 0.07 * np.log(0.9)) / 0.12))
    assert geo_mean_per_euro([1.2, 0.9], [0.05, 0.07]) == pytest.approx(
        expected)


def test_split_invariance():
    # one 10% bet at multiple m == two 5% bets at multiple m
    whole = geo_mean_per_euro([1.15, 0.92], [0.10, 0.07])
    split = geo_mean_per_euro([1.15, 1.15, 0.92], [0.05, 0.05, 0.07])
    assert split == pytest.approx(whole)


def test_bigger_bet_pulls_the_mean_toward_its_outcome():
    small_loss = geo_mean_per_euro([1.2, 0.9], [0.07, 0.05])
    big_loss = geo_mean_per_euro([1.2, 0.9], [0.05, 0.07])
    assert big_loss < small_loss


def test_nonpositive_and_nan_pairs_are_dropped():
    with_junk = geo_mean_per_euro(
        [1.1, 0.9, float('nan'), 1.5], [0.05, 0.07, 0.05, 0.0])
    assert with_junk == pytest.approx(
        geo_mean_per_euro([1.1, 0.9], [0.05, 0.07]))


def test_all_invalid_is_nan():
    assert np.isnan(geo_mean_per_euro([float('nan')], [0.05]))
    assert np.isnan(geo_mean_per_euro([1.1], [0.0]))


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        geo_mean_per_euro([1.1, 0.9], [0.05])
    with pytest.raises(ValueError):
        account_growth_per_bet([1.1, 0.9], [0.05])


def test_account_growth_known_value():
    # factors: 1 + 0.05*0.2 = 1.01, 1 + 0.07*(-0.1) = 0.993
    expected = float(np.exp(np.mean(np.log([1.01, 0.993]))))
    assert account_growth_per_bet([1.2, 0.9], [0.05, 0.07]) == pytest.approx(
        expected)


def test_account_growth_every_bet_counts_once():
    # a total loss on a 5% bet costs the account exactly 5%
    assert account_growth_per_bet([0.0], [0.05]) == pytest.approx(0.95)


def test_account_wipeout_returns_zero():
    # frac 1.0 on a total loss: growth factor 0, account is gone
    assert account_growth_per_bet([1.3, 0.0], [0.05, 1.0]) == 0.0

import numpy as np

from screener import find_base, load_config

CFG = load_config()


def test_valid_base_after_runup():
    # 220-day run-up to 100, then 30 flat days at 99
    closes = np.concatenate([np.linspace(50, 100, 220), np.full(30, 99.0)])
    # first close >= 0.98 * 100 = 98: linspace value 50 + 50*i/219 >= 98 -> i = 211
    result = find_base(closes, CFG)
    assert result is not None
    base_top, length = result
    assert base_top == 100.0
    assert length == 250 - 211  # 39 days from base start through today


def test_base_too_short_is_rejected():
    # run-up crossed 98 only 15 days before today
    closes = np.concatenate([np.linspace(50, 100, 245), np.full(5, 99.0)])
    assert find_base(closes, CFG) is None


def test_base_too_long_is_rejected():
    closes = np.full(250, 100.0)  # within 2% of the high for all 250 days
    assert find_base(closes, CFG) is None


def test_base_with_deep_dip_is_rejected():
    closes = np.concatenate([np.linspace(50, 100, 220), np.full(30, 99.0)])
    closes[230] = 65.0  # dip below 0.70 * H inside the base
    assert find_base(closes, CFG) is None


def test_short_window_is_rejected():
    assert find_base(np.full(100, 100.0), CFG) is None

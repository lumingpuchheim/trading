"""The ported marco-hui-95 VCP: does it see what their code sees, and
does it ever see the future?

The causality test is the important one. Their `argrelextrema(order=10)`
needs the ten bars after a bar to call it an extreme, so the port defers
every extreme to index + 10; truncating the series at day i must leave
day i's verdict unchanged.
"""

import numpy as np
import pytest

import vcp_marco as vm


def synth(depths_pct, leg=14, up=0.30, tail=14, base_px=100.0):
    """A rally, then one pullback per entry in `depths_pct`, each
    recovering to the prior high, then `tail` days easing gently off it.

    Swings are `leg` bars apart so argrelextrema(order=10) can resolve
    them, and the tail is long enough to confirm the last swing high
    while staying under it. Volume declines throughout, so their
    5d-under-30d dry-up holds on every day and the tests isolate price.

    Every assertion below reads the LAST day: a base with five
    contractions passes through a valid three-contraction state on its
    way, so `.any()` would answer a different question than the one
    each test asks."""
    px = [base_px * (1 + up * t / 60) for t in range(60)]
    for d in depths_pct:
        top = px[-1]
        trough = top * (1 - d / 100.0)
        px += list(np.linspace(top, trough, leg + 1))[1:]
        px += list(np.linspace(trough, top * 0.995, leg + 1))[1:]
    px += list(px[-1] * (1 - 0.0008 * np.arange(1, tail + 1)))
    c = np.asarray(px, dtype=float)
    vol = np.linspace(2_000_000.0, 400_000.0, len(c))
    return {'high': c * 1.002, 'low': c * 0.998, 'volume': vol}


def test_shrinking_contractions_fire():
    """25% then 12% then 6%: three contractions, newest under 15%,
    deepest under 50% — their pattern."""
    assert vm.marco_flags(synth([25, 12, 6]))[-1]


def test_widening_contractions_do_not_fire():
    """The same swings in the opposite order: walking back from the
    newest, the run breaks immediately, so the count is 1."""
    assert not vm.marco_flags(synth([6, 12, 25]))[-1]


def test_one_contraction_does_not_fire():
    assert not vm.marco_flags(synth([12]))[-1]


def test_final_contraction_deeper_than_15pct_does_not_fire():
    """Their `min_c <= 15` cap on the newest contraction."""
    assert not vm.marco_flags(synth([40, 22]))[-1]


def test_deepest_contraction_over_50pct_does_not_fire():
    """Their `max_c > 50` rejection on the oldest counted contraction."""
    assert not vm.marco_flags(synth([60, 8]))[-1]


def test_more_than_four_contractions_do_not_fire():
    """Their upper bound: 2 <= count <= 4, and five deepening pullbacks
    walking back is five."""
    assert not vm.marco_flags(synth([40, 28, 18, 11, 6]))[-1]


def test_volume_must_be_drying():
    """Same price path, volume rising into the base instead of falling."""
    bars = synth([25, 12, 6])
    bars['volume'] = np.linspace(200_000, 2_000_000, len(bars['volume']))
    assert not vm.marco_flags(bars)[-1]


def test_already_broken_out_does_not_fire():
    """Their flag_consolidation: today's high must sit below the most
    recent swing high. A base that ends by running away has no VCP."""
    bars = synth([25, 12, 6])
    for k in ('high', 'low'):
        bars[k] = np.concatenate([bars[k], np.full(12, bars[k][-1] * 1.4)])
    bars['volume'] = np.concatenate([bars['volume'], np.full(12, 300_000.0)])
    assert not vm.marco_flags(bars)[-1]


@pytest.mark.parametrize('seed', [0, 1, 2, 3, 4])
def test_no_lookahead(seed):
    """Day i's verdict may not change when the future is removed."""
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(0.0004, 0.02, 600)))
    bars = {'high': c * 1.01, 'low': c * 0.99,
            'volume': rng.lognormal(13, 0.4, 600)}
    full = vm.marco_flags(bars)
    for i in range(200, 600, 37):
        cut = {k: v[:i + 1] for k, v in bars.items()}
        assert vm.marco_flags(cut)[i] == full[i], f'day {i} used the future'


def test_nan_history_is_not_a_pattern():
    """Days before a ticker lists are NaN in the panel; they must not
    produce extrema or flags."""
    bars = synth([25, 12, 6])
    for k in ('high', 'low', 'volume'):
        bars[k] = np.concatenate([np.full(50, np.nan), bars[k]])
    f = vm.marco_flags(bars)
    assert not f[:60].any()
    assert f[-1]

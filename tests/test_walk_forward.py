"""No special year: the fit boundary rolls, and it never looks forward.

EVALUATION_SPEC.md rule 1. There is no development period and no test
period; a model may only see data from before the block it scores, and
that is expressed as a rolling schedule over the whole record rather than
as a date written into the source.

Two purge rules exist and both are tested here, because they stop
different leaks:

  exit date   a training bet is kept only if it CLOSED before the block
              opened. Exact, per row, no constant to tune -- but it
              leaves the training data adjacent to the block, and the
              market moves slowly, so adjacent is not independent.
  embargo     a training bet is kept only if it was ENTERED N days
              before the block. Blunt, needs a number, but it puts real
              distance between the two.

What the tests pin either way: the schedule never trains on a block or on
anything after it, every year with enough history behind it is scored
including the last, and nothing depends on a hardcoded calendar year.
"""

import numpy as np
import pandas as pd
import pytest

from bets_common import (AUX_Q, LEGACY_EMBARGO, folds, label_from,
                         warmup_rows, year_blocks)

DATES = np.array(
    pd.date_range('2007-01-03', '2026-08-27', freq='3D'), dtype='datetime64[D]')
RNG = np.random.default_rng(0)
Y = 1.0 + RNG.normal(0.01, 0.15, len(DATES))
# holds of 1 to 400 days, the spread the real ledger has
EXITS = DATES + RNG.integers(1, 400, len(DATES)).astype('timedelta64[D]')

# min_train is lowered so the SHAPE of the schedule is what these tests
# exercise, not the size cutoff -- that has its own test below. With the
# production 2,000 this synthetic record would yield only two blocks.
BLANKET = dict(exits=None, embargo_days=LEGACY_EMBARGO, min_train=100)
EXACT = dict(exits=EXITS, embargo_days=None, min_train=100)


# ------------------------------------------------------ the year schedule

@pytest.mark.parametrize('mode', ['blanket', 'exact'])
def test_every_year_with_history_is_scored_including_the_last(mode):
    kw = BLANKET if mode == 'blanket' else EXACT
    years = [y for y, _, _ in year_blocks(DATES, lookback_years=None, **kw)]
    assert years == sorted(years)
    assert years[-1] == 2026, 'the final year must be scored, not reserved'
    assert years == list(range(years[0], years[-1] + 1)), 'a year is missing'


@pytest.mark.parametrize('mode', ['blanket', 'exact'])
def test_training_never_reaches_into_its_own_block_or_past_it(mode):
    kw = BLANKET if mode == 'blanket' else EXACT
    for year, train, block in year_blocks(DATES, lookback_years=None, **kw):
        assert not (train & block).any(), f'{year}: trained on its own block'
        opens = np.datetime64(f'{year}-01-01')
        assert DATES[train].max() < opens, f'{year}: trained on the future'


def test_the_blanket_embargo_puts_the_distance_it_claims_to():
    for year, train, _ in year_blocks(DATES, lookback_years=None, **BLANKET):
        opens = np.datetime64(f'{year}-01-01')
        gap = (opens - DATES[train].max()).astype(int)
        assert gap > LEGACY_EMBARGO - 3, f'{year}: only {gap} days of purge'


def test_exit_purging_admits_bets_the_embargo_would_have_dropped():
    """The trade-off, made explicit: purging on the exit date keeps data
    right up against the block, which is why it needs no constant and
    also why it leaves the two correlated."""
    blanket = {y: t for y, t, _ in
               year_blocks(DATES, lookback_years=None, **BLANKET)}
    exact = {y: t for y, t, _ in
             year_blocks(DATES, lookback_years=None, **EXACT)}
    common = set(blanket) & set(exact)
    assert common
    for y in common:
        assert exact[y].sum() >= blanket[y].sum()


@pytest.mark.parametrize('mode', ['blanket', 'exact'])
def test_no_training_bet_is_still_open_when_its_block_starts(mode):
    """Under exact purging this is exact. Under the blanket rule it holds
    because 400 days is the longest hold in the ledger."""
    kw = BLANKET if mode == 'blanket' else EXACT
    for year, train, _ in year_blocks(DATES, lookback_years=None, **kw):
        opens = np.datetime64(f'{year}-01-01')
        assert EXITS[train].max() < opens, f'{year}: a training bet overlaps'


def test_a_block_with_too_little_history_is_not_scored():
    short = DATES[DATES < np.datetime64('2009-01-01')]
    years = [y for y, _, _ in
             year_blocks(short, None, min_train=10_000,
                         lookback_years=None, embargo_days=LEGACY_EMBARGO)]
    assert years == []


def test_no_year_is_privileged():
    """The schedule must not change if the record is shifted in time: a
    date written into the source would show up as a different shape."""
    a = [(y, int(t.sum()), int(bm.sum())) for y, t, bm in
         year_blocks(DATES, lookback_years=None, **BLANKET)]
    shifted = DATES + np.timedelta64(365 * 3, 'D')
    b = [(y, int(t.sum()), int(bm.sum())) for y, t, bm in
         year_blocks(shifted, None, lookback_years=None,
                     embargo_days=LEGACY_EMBARGO, min_train=100)]
    # Same number of blocks, same years apart, same sizes. The sizes are
    # compared with a tolerance of one row: 365*3 days is not three
    # calendar years -- the leap day shifts the 3-day sampling grid by a
    # day inside each block. A hardcoded year would not show up as +/-1,
    # it would change the shape.
    assert len(a) == len(b)
    assert [x[0] for x in b] == [x[0] + 3 for x in a]
    for (_, ta, ba), (_, tb, bb) in zip(a, b):
        assert abs(ta - tb) <= 1 and abs(ba - bb) <= 1


def test_the_lookback_holds_every_block_to_the_same_evidence():
    """Expanding lets the last block train on nine times what the first
    did, which confounds 'the edge decayed' with 'the sample grew'."""
    exp = [int(t.sum()) for _, t, _ in
           year_blocks(DATES, lookback_years=None, **BLANKET)]
    win = [int(t.sum()) for _, t, _ in
           year_blocks(DATES, lookback_years=3, **BLANKET)]
    assert max(exp) / min(exp) > 5
    assert max(win[2:]) / min(win[2:]) < 2


# ------------------------------------------------------------- the label

def test_the_label_is_cut_on_training_rows_alone():
    train = DATES < np.datetime64('2015-01-01')
    lab = label_from(Y, train)
    thr = float(np.quantile(Y[train], AUX_Q))
    assert (lab == (Y >= thr)).all()
    assert lab[train].mean() == pytest.approx(1 - AUX_Q, abs=0.02)


def test_the_label_moves_when_the_training_window_moves():
    early = label_from(Y, DATES < np.datetime64('2012-01-01'))
    late = label_from(Y, DATES < np.datetime64('2022-01-01'))
    assert not (early == late).all(), 'a fixed threshold has crept back in'


# --------------------------------------------------- the expanding folds

def test_folds_run_to_the_end_of_the_record():
    fs = folds(DATES, 4, EXITS)
    assert fs, 'no folds produced'
    assert DATES[fs[-1][1]].max() == DATES.max(), 'a tail is being reserved'


def test_folds_never_train_on_their_validation_block():
    for train, val, _, _ in folds(DATES, 4, EXITS):
        assert not (train & val).any()
        assert DATES[train].max() < DATES[val].min()
        assert EXITS[train].max() <= DATES[val].min()


def test_folds_accept_the_blanket_rule_too():
    fs = folds(DATES, 4, None, embargo_days=LEGACY_EMBARGO)
    assert fs
    for train, val, _, _ in fs:
        assert DATES[train].max() < DATES[val].min()


# ------------------------------------------------------- the bias warm-up

def test_warmup_rows_come_from_the_start_of_the_record():
    rows = warmup_rows(DATES, 100, np.random.default_rng(0))
    assert len(rows) == 100
    assert DATES[rows].max() <= np.sort(DATES)[300]

# Evaluation spec — how this system is measured

Two rules, agreed 2026-08-29. Both exist because the same quantity was
being computed several different ways and the results were compared with
each other.

---

## Rule 1 — one record, no special year

**There is no development period and no test period.** Every measurement
runs over the whole record, from `backtest.start` (2007-01-01) to today,
as one continuous path.

The old split at 2018-12-31 / 2019-01-01 did three unrelated jobs under
one name, and only the third was doing any work:

1. It cut reports in half in scripts where **nothing is fitted**. The
   screener's constants are frozen in `MINERVINI_SPEC.md`, so the two
   halves were the same rules on different years — one result printed
   twice, then compared with itself.
2. It froze two constants inside the filter scripts: the "jackpot"
   label threshold and the feature-standardisation sample were computed
   once from 2007-2018 and reused through 2026. Not lookahead, but a
   2026 fold measured against a yardstick cut in 2018.
3. It was a genuine train/grade boundary in the three scripts that fit a
   model.

Job 1 is deleted. Job 2 becomes per-fold. Job 3 becomes walk-forward.

### Anything fitted is fitted walk-forward, over the whole record

A model may only ever see data from before the block it is scoring.
That is expressed as a rolling schedule, never as a calendar constant:

    for each block (one calendar year):
        train on every bet whose entry is more than EMBARGO days
                 before the block starts
        score the block
        roll

`EMBARGO = 400` calendar days, longer than the ledger's longest hold, so
no training bet's outcome can resolve inside the block it is scored on.
Every year of the record is scored this way, including the earliest ones
that have enough history to fit on.

Anything derived from data — the label threshold, the standardisation
mean and standard deviation, the decision cut — is derived **from that
fold's own training window**, never from a fixed slice and never from the
block being scored.

This is what "train and test from beginning to end" means: the boundary
still exists at every point in time, but it moves, and no calendar year
is privileged.

### What changes, by file

**Nothing is fitted — the split is deleted outright:**

| file | was | becomes |
|---|---|---|
| `minervini_backtest.py` | two `simulate()` runs, two of every output file | one run, one equity curve, one controls histogram, `minervini_{tag}_trades.csv` |
| `minervini_stats.py` | two tables, hardcoded 12.0 / 7.65 years | one table, span measured from the trades |
| `minervini_bets.py` | writes a `period` column | column removed |
| `slot_sweep.py`, `minervini_size_scan.py`, `minervini_sell_scan.py` | two rows per config | one |
| `rs_keys_ev.py` | `--period dev\|test` | flag removed |
| `filter_backtest.py` | `--period` selects the simulation window | flag removed; the window is the whole path |
| `minervini_failures.py`, `minervini_showcase.py` | colour and defaults by period | removed |

**A fit exists — the fixed date becomes a rolling one:**

| file | was | becomes |
|---|---|---|
| `filter_agreement.py` | scores dev years only (`(yr == Y) & (date <= DEV_END)`) | every year |
| `filter_backtest.py`, `equity_vs_spy.py`, `minervini_rocket.py` | label threshold and seed rows from `date <= DEV_END` | per fold, from that fold's training window |
| `minervini_rocket.py --test`, `rocket_ev.py`, `ev_model.py` | one fit before 2018, graded after | walk-forward over the whole record |

**Constants:**

- `bets_common.DEV_END` is deleted. Nothing in the Minervini path may
  name a year.
- `config.yaml`'s `backtest.dev_end` and `backtest.test_start` stay, but
  only the pre-Minervini system (`backtest.py`, `lppl_*.py`, `learn.py`)
  reads them, and a comment says so. The same applies to
  `learning.penalty_folds`, which is a third fold schedule belonging to
  that older system.

### Acceptance

- `grep -rn DEV_END` returns nothing outside `lppl_*` / `backtest.py`.
- No Minervini output filename contains `dev` or `test`.
- `python minervini_backtest.py --v5 --e3 --moc` prints one block and
  writes one trades file covering 2007 to today.
- Every filter arm reports a score for every year of the record, and each
  year's score comes from a fit that ended at least 400 days earlier.

---

## Rule 2 — one bet, one multiple, one average

Already implemented, recorded here so the two rules live together.

A bet is a **position**, not a row. The simulator writes one row per
sale, and a position can sell in pieces, so averaging rows counts a split
winner twice and a loser once.

    multiple = SUM over the position's rows of  weight x (1 + ret_net)
               + dividends collected / what the position cost

Bets are averaged **geometrically**, one vote each, split or not.

Both halves live in `geostats.py` (`bet_multiples`, `geo_per_bet`) and
nothing computes a per-bet figure any other way. The arithmetic means
were deleted, not deprecated.

`simulate()` credits dividends to cash on the same window the ledger
uses: an entry fills at the close so it misses that day's ex-date, an
exit fills at the open so it still collects.
`tests/test_bet_multiples.py` runs one price path through both pricers
and requires the same multiple out of each.

---

## Rule 3 — every arm runs the same code over the same universe

The baseline arm is `AllPass`, the filter that approves everything — not
a separate code path that skips the filter layer. Today
`filter_backtest.py` passes `gate=None` for `k == 0` and labels it
"AllPass (v5r)" while `filters.AllPass` is never instantiated; that is
two definitions of "take everything", one of them dead.

A filter can only reject a signal it has a score for. 591 signals (1.06%
of the tradable universe) are orderable by `simulate()` but absent from
the ledger, because the ledger keys the market light on the signal day
while the simulator places the order the day before. Those signals are
currently admitted unconditionally by every arm, so "AllPass" and
"MiniRocket k=0.50" are not the same experiment minus one thing.

Fix: the off-by-one is corrected so the ledger covers exactly the
signals the simulator can order, and the `k == 0` arm runs through
`filters.AllPass` like every other arm.

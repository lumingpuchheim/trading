# Handover — filter layer on v5r (session ended 2026-08-29)

Read `DECISIONS.md` first for verdicts, `FINDINGS.md` for evidence,
`LIMITATIONS.md` for what the data cannot support. This file is only what
the next session needs that is not yet written down elsewhere.

---

## 1. THE CONTRADICTION IS CLOSED — read this before any per-bet number

It was never three measurements of one thing. It was one quantity
measured three different ways, and the three ways were compared with each
other. Resolved 2026-08-29; the rules now live in `EVALUATION_SPEC.md`.

What each number actually was:

| was reported as | value | what it really was |
|---|---|---|
| mean y over ALL dev candidates | 1.0122 | ARITHMETIC mean of per-bet multiples |
| mean y of the ranking's top 10 | 1.0120 | same, on a subset |
| avg realised trade | 1.0406 | ARITHMETIC mean over trade ROWS |
| geo/trade | 1.0302 | GEOMETRIC mean over the same ROWS |

A row is not a bet: the +20% strength rule sells half and writes its own
row, so a split WINNER wrote two rows and a loser wrote one. The
simulator also never credited dividends, which the ledger did. Three
differences at once — the unit, the average, and the dividends — none of
them selection skill.

### Where it stands now, on ONE record

2007-01-03 .. 2026-08-27, no development / test split anywhere:

| | bets | geometric mean per bet |
|---|---|---|
| the candidate pool | 55,737 | **1.0052** |
| the bets the book actually took, unpriced | 1,252 | **1.0097** |
| the same, after the 0.4% round trip | 1,252 | **1.0057** |

Book minus ledger, per bet: median **-0.0039**, mean -0.0041. The
round-trip commission is 0.0040, and **all 1,252 positions match** — the
two pricers now agree bet for bet on the only difference that was ever
modelled between them.

So the ranking is worth about **+0.45% per bet before fees** (1.0097
against a pool of 1.0052), and roughly the commission eats it. That is
the whole of the selection effect; the 2-3 points that could not be
explained were an artefact of three incompatible measurements.

Portfolio, same run: total **+291.6%**, ann **+7.20%**, maxDD -30.2%,
1,477 trade rows / 1,252 bets, 73.4% invested, beating 95% of the random
template-passing controls.

---

## 2. Established, with evidence

**The slot constraint binds almost always** (`slot_pressure.py`). 4,944
trading days, 10 slots: full on 75.2% of days, full while signals arrive on
70.5%, a free slot with signals on 3.1%. Median signals on a green day 13,
**median free slots 0**. This is why a filter can matter at all.
(Measured before 2026-08-29; the shape holds, the decimals predate the
dividend credit and the one-record change.)

**Filters change WHICH trades, not HOW MANY.** They block ~16,500 of
~30,000 signals and the book still does ~830 trades: it takes only ~3% of
signals to keep ten slots busy.

**Neither model picks jackpots.** Precision on the >5% label is the base
rate in every arm ever measured (x0.95 to x1.05), across four objectives
written to attack exactly that: cost-sensitive BCE, balanced BCE,
symmetric log-value regression, F-beta paying only for a correct claim.
The loss was never the binding constraint.

**Expected value is not predictable from (MiniRocket score, rsl_hi, weak,
rs)** (`ev_model.py`). Test R2 = **-0.0038** (worse than a constant),
spearman +0.035, and the decile table is INVERTED: best-predicted decile
returns 1.0037, worst-predicted 1.0122. Permutation importance is negative
for `rocket` and `rs` -- shuffling them improves the model.

**`weak` is effectively undefined.** 6,258 finite cells in a panel of
8,143,265 (0.08%); **8 of 55,737 ledger rows**. It needs a base AND a SPY
down-day inside it, and the pullback entry (99.7% of the pool) almost never
has one. So the second key in `simulate()`'s sort never breaks a tie -- the
ranking is really `rsl_hi`, then `rs`, then alphabetical.

**The two filters disagree** (`filter_agreement.py`): spearman **-0.116**,
Jaccard 0.281. The rank agreement stands. **The cell returns below it do
NOT: they were arithmetic means, and the file now reports geometric.**
Re-run it before quoting them. As recorded they were rocket-only 1.0155,
shapelet-only 1.0055, "both approve" 1.0110 and "both reject" 1.0153 --
and the last of those was flagged as unexplained, which section 1 has
since shown is what an arithmetic mean of a skewed multiple looks like.

---

## 3. Ruled out — do not redo

- **CNN** — deleted. Every width (938 to 7,586 params) sat inside its own
  label-shuffle control.
- **Volume in either filter** — both variants lost. Shapelet +126.3% ->
  +73.7% with volume; MiniRocket +104.0% -> +68.4% with the price x volume
  interaction. Capacity is fixed, so volume was swapped in, not added; and
  the screener already spends volume information (`dryup_max_ratio` 0.75,
  `breakout_volume_mult` 1.5).
- **Strict thresholds** (k=0.80, 0.90) — starve the book: invested falls
  71.7% -> 53.4% -> 40.4% and returns drop below AllPass.
- **Rewriting the loss to chase jackpots** — four attempts, section 2.

---

## 4. Where the numbers stand

Continuous 2009-2026, one path, no fees or tax (`equity_vs_spy.py`).
**The filter rows predate the dividend credit and the one-record change
of 2026-08-29 and have not been re-measured**; re-run before quoting
them. The v5r row below has: with dividends it is ann +8.97% over
2009-2026, against SPY's +14.81% total return, so the verdict is
unchanged. The rest of the table is as recorded:

| | ann | maxDD | trades |
|---|---|---|---|
| SPY total return | **+14.81%** | -33.7% | 1 |
| MiniRocket k=0.50 | +11.16% | **-28.3%** | 1,222 |
| Shapelet g=0 k=0.50 | +8.75% | -40.2% | 1,414 |
| v5r, no filter | +8.61% | -29.7% | 1,379 |

**Everything loses to holding the index**, before fees. MiniRocket is the
only filter that survives the full path; the shapelet's dev-best +126.3%
does not transfer.

Dev and test have disagreed on EVERY structural change tested: the
dividend correction, slot count, both filters, and the volume variants.
Treat any dev-only result as unproven.

---

## 5. Code map

    EVALUATION_SPEC.md    the two rules everything else obeys: one record
                          with no special year, and one per-bet average
    bets_common.year_blocks / folds / label_from / warmup_rows
                          THE walk-forward schedule. Anything fitted takes
                          its train/score boundary from here, so no file
                          names a year.
    geostats.py           THE per-bet average. `bet_multiples` folds a
                          trades table into one multiple per position
                          (splits blended, dividends in); `geo_per_bet`
                          takes the geometric mean of those. Nothing
                          computes a per-bet figure any other way.
    bets_common.py        data, folds, metrics, labels. Shared by all.
    minervini_bets.py     per-signal ledger; --windows writes model inputs
    filters.py            Filter interface: AllPass / Shapelet / Rocket /
                          Ensemble. Threshold frozen at FIT time.
    minervini_rocket.py   84 fixed kernels + ridge; --mv channel subsets
    minervini_shapelet.py 249 params, plottable; 3 losses
    filter_backtest.py    walk-forward by year through the real simulator
    slot_pressure.py      the constraint measurement
    slot_sweep.py         10x10% vs 20x5%
    filter_agreement.py   do the filters disagree, and does it pay
    rs_keys_ev.py         do the RS ranking keys predict anything
    ev_model.py           predict EV from rocket + the three keys
    equity_vs_spy.py      one continuous path against SPY
    rocket_ev.py          per-bet EV with bootstrap CIs

The filter plugs into `simulate()` through ONE argument: `gate`, a
(days x tickers) boolean. Nothing else about the portfolio changes.

---

## 6. Traps that cost this session real time

1. **`simulate(pool_days=...)` is not optional for v5.** Omit it and it
   silently falls back to `panel['setup']`, reducing v5r to the pivot-only
   system: a fraction of the trades. Pass `pool_by_day(panel['watch'])`.
   **Always check the AllPass arm reproduces +291.6% over 2007-2026
   before reading any filter row.**
2. **Label every number with its population.** Two are in play: all
   55,737 signals in the ledger, and the 1,252 bets that won a slot.
   Confusing them wasted several exchanges. There is no third population
   any more -- the dev / test split is gone (`EVALUATION_SPEC.md`).
3. **Geometric, never arithmetic**, for anything per-trade or per-bet.
   Enforced in code since 2026-08-29: every such figure goes through
   `geostats.py` and the arithmetic ones were deleted, not deprecated.
4. **Prices are NOT dividend adjusted** since 2026-08-29. Dividends are
   explicit cash. Do not re-introduce `auto_adjust=True`; `download_data.py`
   will refuse to mix conventions. `simulate()` credits them to cash at
   the top of each day and stamps `div_eur` on every trade row; the
   ledger does the same in `price_bet`. The two conventions are pinned
   against each other by `tests/test_bet_multiples.py`.
5. **Do not pipe long runs into `tail`** -- it buffers everything until the
   process exits and you fly blind. Redirect to a log file.
6. **`weak` is 99.99% NaN.** Any model using it is using a constant.

---

## 6b. The dev / test split is gone

Removed 2026-08-29 on the user's instruction: "we train and test from
begin to end, no special year". The date was doing three unrelated jobs
under one name (`EVALUATION_SPEC.md` rule 1) and only the third was work:

1. It cut reports in half where **nothing is fitted** -- deleted. One
   run, one curve, one `minervini_{tag}_trades.csv`.
2. It froze the jackpot label and the standardisation sample at 2018 and
   reused them through 2026 -- now cut per fold, from that fold's own
   training window.
3. It was a real train/grade boundary in `minervini_rocket.py --test`,
   `rocket_ev.py` and `ev_model.py` -- now walk-forward over the whole
   record, scores pooled out of fold.

`bets_common.DEV_END` is deleted. `config.yaml`'s `dev_end` /
`test_start` survive but are read only by the pre-Minervini system
(`backtest.py`, `lppl_*.py`, `learn.py`) and are marked as such.

`tests/test_walk_forward.py` pins the schedule: the embargo really
purges, no training row comes from inside or after its own block, every
year including the last one is scored, and the shape does not change if
the record is shifted in time (which is how a hardcoded year would show
itself).

**The baseline is now a filter.** `filter_backtest.py`'s `k == 0` arm runs
through `filters.AllPass` instead of passing `gate=None`, so the baseline
and the filtered arms cannot be different code. And the ledger's market
light was read a day late: it keyed on green at the signal, while
`simulate()` places the order the evening before. That left 591 signals
the book could buy and no filter could score, admitted unconditionally by
every arm. Fixed -- the ledger is 55,737 bets now, exactly the orderable
set, and all 1,252 of the book's positions match a ledger row.

---

## 7. Repo state

Two commits on `master`:

    8f00ce7  Add a trade filter layer in front of v5r
    ba8227c  Stop taking dividend-adjusted prices from the vendor

**Left uncommitted deliberately:** `geostats.py`, `vcp_marco.py`,
`tests/test_geostats.py`, `tests/test_vcp_marco.py` and the
`minervini_stats.py` change. These date from 2026-08-28 17:06-18:14 and
belong to different work; committing them under this session's message
would misattribute them.

Also unstaged: ~28 regenerated files under `results/` that changed only
because the price convention changed.

The bet ledger and its windows are now gitignored as derived caches.
Rebuild with `python minervini_bets.py --windows 252` (needs
`data/minervini_panel_v5.npz`, itself rebuilt by
`python minervini_backtest.py --v5 --e3 --moc`).

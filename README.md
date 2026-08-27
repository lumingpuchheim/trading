# seller-decay screener

Tests one idea: when a stock's daily range and volume both shrink at a steady
exponential rate inside a tight base, sellers are running out; buying the
breakout earns money. Pipeline: mechanical measurement -> small ridge
regression that learns whether the measurement pays -> quarter-Kelly sizing ->
fixed entry/exit rules -> out-of-sample backtest.

## How to run

```
pip install yfinance pandas numpy pyarrow matplotlib pyyaml requests lxml pytest
python download_data.py    # once; caches parquet under data/
python screener.py         # sections 2-4 -> data/signals.parquet
python learn.py            # section 5  -> data/trade_table.parquet + frozen artifacts
python backtest.py         # sections 6-7 -> tables + charts in results/
python -m pytest tests/    # unit + lookahead tests
```

All tunables are in `config.yaml`. The `trading:` block, the r2 sanity filter,
and the quarter-Kelly multiplier are fixed by design — never tune them.
Everything in section 5 is fitted on 2007-2018 only and frozen; delete
`data/trade_table.parquet` to force a trade-table rebuild after re-screening.
Read `LIMITATIONS.md` before believing any number in `results/`.

## LPPL bubble-dip strategy (second idea)

Buy dips inside a Sornette-style bubble: a stock is "in a bubble" when a
fixed-grid LPPL fit (Filimonov-Sornette linearisation, deterministic — no
optimizer, no random starts) qualifies on >= 3 of 5 window lengths, for 2
consecutive weekly evaluations. Entry: close >= 4% below the 20-day high
while the flag is on and the median critical time tc is still ahead; buy
next open. Exit: 8% stop, or today past tc. A cheap pre-screen
(accelerating +20% run-up — a necessary condition for any qualifying fit)
cuts compute ~10x; lppl_detect.py probes rejected days to verify it drops
(almost) nothing. Ablations with identical rules: bubble_nodip (no dip
wait), dip_only (pre-screen + dip, no LPPL fit).

```
python lppl_detect.py      # detector -> data/lppl_flags.parquet (~30 min, 7 cores)
python lppl_backtest.py    # -> results/lppl_* tables and charts
```

## Minervini Stage-2 breakout (third idea — v1 rejected, v2 gated)

Buys strength instead of dips: a nine-condition trend template (relative
strength = top 30% of the liquid universe that day) plus a base of
progressively shallower pullbacks with volume drying up, bought with a
resting buy stop just over the top of the last pullback — never chasing
more than 5% past it. Exits: 8% stop or a close below the SMA50.

**v1** froze the constants before the first run and was rejected by the
audit: dev +7.5% (t 0.63, 62nd percentile of 200 random-template
controls), test -23.7% (t -3.0, below all 200). A source audit afterwards
showed four of its five mechanics deviated from the published method, so
`MINERVINI_SPEC.md` was rewritten as **v2** (book-faithful pivot, 3-week
minimum base, quiet DAYS instead of a quiet 10-day mean, shallower-dips
test, buy-stop fill with a chase guard).

v2's signal core is implemented, but its acceptance gate FAILS — SPHR and
SMCI both produce zero triggers — so **no v2 backtest has been run**, per
the spec's own build order. The gate script prints the exact condition
that eats every day of each case window.

```
python minervini_gate.py           # acceptance gate + rejection funnel
python minervini_gate.py --chain   # diagnostic: base anchored at the
                                   # contraction chain instead of the rim
python minervini_backtest.py       # v1 audit + controls (historical)
python minervini_failures.py       # v1 event study + six worst trades
```

`MINERVINI_COVERAGE.md` is the complete inventory of which parts of the
method are implemented, which cannot be with this data, and which simply
are not. See FINDINGS for the verdicts.

## Interpretation choices (where the spec left room)

- The range/volume normalisation baseline is the mean over the 120 days
  immediately prior to each day t (rolling, excluding day t itself).
- If a base is shorter than the 40-day fit window, the fit uses the whole base.
- A signal stays armed for its breakout until close falls below 0.70 x base top
  or 90 trading days (the maximum base length) pass without a fresh signal.
- A base's measurements are those of the last signal before its breakout.
- The trade table (5a) simulates every base independently: no position limits,
  no cooldown, overlapping trades of one ticker allowed.
- Exits trigger on the close and fill at the next day's open, symmetric with
  entries. "60 days held" therefore fills on day 61's open.
- Position size fractions apply to portfolio value at the previous close (the
  value known when the entry was scheduled).
- All 5c bucket edges (r2, predicted, edge) are development quantiles, frozen;
  the test-period 5c tables reuse the dev edges so rows are comparable.
- Each backtest period starts flat and liquidates at its last close, so the
  development and test periods are independent.

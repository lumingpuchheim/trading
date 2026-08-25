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

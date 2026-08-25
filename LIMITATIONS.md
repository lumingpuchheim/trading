# Limitations

Read this before trusting any number in `results/`.

## Survivorship bias (the big one)

The universe is the **current** S&P 1500 constituents (Wikipedia; the Russell
1000 list is no longer published there and the iShares holdings CSV is behind a
bot wall). Every stock that was delisted, acquired at a loss, or fell out of
the index before today is missing. A breakout system on 2007-2019 data that
only contains 2026's survivors is structurally optimistic: the stocks that
broke out and then died are simply not in the sample. Absolute returns here are
an upper bound. The model-vs-template-vs-random comparison is more meaningful
than any absolute number, since all three strategies share the same biased
universe — but the bias need not cancel exactly.

## Other known issues

- **Adjusted prices.** yfinance `auto_adjust=True` back-adjusts for splits and
  dividends. The $5 price filter and the base geometry are therefore applied to
  adjusted, not actual historical, prices. Dividends are implicitly reinvested.
- **Point-in-time universe only for liquidity.** The price/dollar-volume filter
  is applied per-day (no lookahead), but index membership itself is as of today.
- **Stops fill on the next open, measured on the close.** A gap through the
  stop fills at the gapped open; real losses on the worst trades can exceed 8%.
  There is no intraday data here, so this is unavoidable but honest.
- **Flat costs.** 0.2% per side, no market impact, no borrow, no slippage
  model. For liquid large caps at small size this is roughly fair; for the
  smallest S&P 600 names it is optimistic.
- **Yahoo data quality.** Occasional bad prints, missing days, and silently
  restated history. Tickers with fewer than 260 usable rows are dropped and
  logged (`data/download_log.txt`, screener output), not silently ignored.
- **Delistings mid-trade.** If a held ticker's data ends, the position is
  closed at its last available close and counted; with a survivor universe
  this is rare and mild — in reality delistings are exactly where the losses live.
- **One development pass.** Config values were set once from the spec. Any
  further tuning on the development period must be declared; the test period
  (2019-today) must never be used for tuning. The ridge weights, penalty,
  shrink function, Kelly table, and all bucket edges are fitted on 2007-2018
  and frozen before the test period is touched.
- **Trades straddling the split.** A base entered in late 2018 may exit in
  early 2019; its realised return is used for fitting. This is how deployment
  would work (weights frozen at the boundary), but it means a handful of
  development targets contain early-2019 price action.
- **The trade table ignores portfolio frictions.** Learning targets come from
  every base traded independently (no position limits, no cooldown), so the
  model learns per-base economics, not portfolio-constrained ones.

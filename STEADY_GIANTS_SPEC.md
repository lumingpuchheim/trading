# Steady Giants — specification (pre-registered before any code)

Declared 2026-08-26, before implementation. Every knob is fixed here; the
dev period may tune only what this document explicitly marks TUNABLE.
Protocol as always: dev 2007–2018 selects, test 2019–today audits once.

## Objective

Automate the identification and patient holding of steady compounders
("boring business, steady winning, boring price development") — the class
of stock the LPPL system is blind to by construction (KO: evaluated 26
times in 20 years while returning +146%). Absolute quality is the goal;
SPY is reported as context, never targeted.

## Capital model

Cash is never idle: uninvested capital earns the 3-month T-bill yield
(FRED DGS3MO, daily accrual). The portfolio is either in giants or in
T-bills. No leverage, no shorts.

## Universe & data

- Cached S&P 1500 OHLCV (unchanged; do not re-download).
- **Survivorship caveat, prominent:** today's members that look steady
  for 20 years are steady partly because they survived. All results are
  an upper bound; point-in-time membership remains the #1 paid-data
  upgrade. The random-qualifier control below is the partial antidote.
- New free data (one-time fetches, cached as parquet):
  1. Dividend history per ticker (yfinance, decades deep).
  2. Reported quarterly EPS per ticker (yfinance earnings history,
     ~25y deep — verified on the 429-ticker earnings fetch). Trailing
     12-month EPS = sum of last 4 reported quarters, known only after
     each report date (point-in-time safe).
  3. FRED DGS3MO (T-bill yield).

## Total-return accounting (user requirement: dividends count)

- Cached prices are downloaded with yfinance auto_adjust=True: they are
  TOTAL-RETURN series with dividends baked in. Equity curves computed
  from them therefore already include reinvested dividends; dividend
  cash is never added separately (that would double count).
- **P/E must NOT use adjusted prices**: dividend adjustment shrinks past
  prices with a drift that fades toward today, which would mechanically
  push measured P/E upward over time and spuriously trigger the
  own-history ceiling for exactly the best payers. Nominal prices are
  reconstructed from the adjusted series and the fetched dividend
  history (invert the back-adjustment factor ex-date by ex-date);
  all P/E ratios use nominal price over reported trailing EPS.
- Cash earns the 3M T-bill yield with daily accrual, as above.

## Qualification: what is a steady giant?

Computed monthly, trailing windows only. A stock QUALIFIES when all hold:

1. **Liquidity** — existing filter (close > $5, dollar volume > $5M).
2. **Boring price** — trailing 3y daily volatility in the LOWEST tercile
   of the liquid universe that month.
3. **Steady winning** — trailing 5y log-price regression: positive slope
   AND R² ≥ 0.7 (a straight-line compounder, not a flat-liner or a
   roller-coaster). TUNABLE on dev: R² threshold in {0.6, 0.7, 0.8}.
4. **Dividend discipline** — dividends paid in each of the trailing 5
   calendar years, no cut > 20% year-over-year in the trailing 2 years.
5. **Data present** — at least 4 reported EPS quarters (P/E computable).

## Buy rule

Buy a qualifying giant when:
- a slot is free, AND
- the market light is green (SPY > 200d SMA and 20d vol ≤ trailing 756d
  90th pct — the buy-timing result carried over), AND
- its trailing P/E is NOT above its own historic 90th percentile (do not
  buy what the sell rule would immediately flag).

Ranking when slots are contested: highest 5y regression R² first (the
straightest compounder), ties by lower volatility.

## Sell rules (user-specified core + two hygiene rules)

1. **Valuation ceiling** — trailing P/E above the 95th percentile of its
   own full history-to-date (minimum 5y of P/E history before this can
   fire). TUNABLE on dev: percentile in {90, 95, 100=all-time high}.
2. **Sornette says tc** — the LPPL detector certifies the stock (2-of-5
   votes with standard persistence): the boring giant has gone bubbly;
   sell into the mania. (Supported by measurement: 3–5-vote days show
   −4.5% median 60d forward excess in the test era.)
3. **Dividend cut** (> 20% YoY) — steadiness broken, disqualify and sell.
4. **Delisting** — forced exit at last price.

No stop-loss. No profit target. No index-relative exits. Expected
holding period: years.

## Sizing — "aggressive like Buffett"

8 slots, equal weight 12.5% at entry, no rebalancing between signals
(winners are allowed to grow into concentration — aggressive means
letting a compounder become 30% of the book, as Buffett does). Costs
0.2% per side as always.

## Cadence

Monthly decision date (first trading day). Expected turnover: a few
trades per year. Everything else is holding and collecting.

## Verdict criteria (pre-registered)

Success is absolute, not SPY-relative:
1. Dev CAGR meaningfully above T-bills with maxDD < the universe's, AND
2. the system beats ≥ 75% of 200 random-qualifier portfolios (same
   slots, same cadence, random picks among qualifiers — the skill-vs-
   survivorship control), in BOTH periods.
SPY and T-bill curves are reported for context. Statistical honesty:
monthly decisions over 12y ≈ dozens of overlapping bets; expect wide
uncertainty and say so.

## Case studies (descriptive, after the verdict)

KO, PG, JNJ, COST walk-throughs: when did the system first buy, when did
it sell, what did the P/E ceiling and the tc-sell each contribute. The
KO-2019+ question ("would we have caught the rise Berkshire sat out")
answered explicitly.

## Build order

1. `giants_data.py` — dividend + EPS + T-bill fetches, cached parquets.
2. `giants_features.py` — monthly qualification table (vectorized,
   cached).
3. `giants_backtest.py` — portfolio simulator + random-qualifier
   controls + charts.
4. Dev run → freeze tunables → single test audit → FINDINGS entry.
5. Case studies.

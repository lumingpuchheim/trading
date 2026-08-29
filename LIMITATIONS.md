# Limitations

Read this before trusting any number in `results/`.

## The rule we actually trade is not his rule (added 2026-08-28)

This belongs above survivorship, because survivorship distorts a
measurement of the method while this one means we are not measuring the
method.

**Minervini's entry is a two-stage pipeline.** The trend template is a
*screen*: nine arithmetic conditions that say a stock is in a Stage 2
advance, true for weeks at a time, saying nothing about when to buy. The
VCP is the *setup* inside that state: successive shallower contractions
with volume drying into the last one, producing a pivot -- a specific
price on a specific day. Stage 2 is a precondition; the VCP is what
turns a watchlist name into a trade.

**We run the first stage and skip the second.** Measured over the
standing configuration's 1,230 positions:

| entry | dev | test |
|---|---|---|
| **pullback to the SMA20** | **689** | **524** |
| pivot breakout (the VCP path) | 4 | 2 |
| cheat | 2 | 2 |
| power play | 3 | 4 |

The rule that takes 99% of the trades is:

```
trend template (all nine)
  + a 60-day-high close within the last 10 days
  + today's low touches the SMA20 (low <= 1.005 x SMA20)
  + the close holds it (close >= SMA20)
```

No base is required, no contraction chain, no pivot, no volume dry-up,
and until section 14 no volume condition of any kind. It fires on
**65,898 of 906,079 template stock-days -- one day in fourteen.**

### Why this is a replacement and not a simplification

1. **The selectivity is gone.** A completed VCP setup occurs on 0.5% of
   template days. Our trigger occurs on 7.3% of them -- fifteen times as
   often. A pattern that fires one day in fourteen is not filtering the
   screen, it is timing it.
2. **The discriminating information is gone.** What a VCP asserts is that
   supply has been absorbed -- shrinking pullbacks, drying volume. Our
   trigger asserts that price recently made a high and has come back to a
   moving average. Those are different claims about the world.
3. **The controls say so.** The benchmark is random buys of
   template-passing stocks, and it returns a median +54% (dev) and +58%
   (test). Our strategy is "template-passing stocks that touch their
   20-day line". Beating that benchmark at the 97th percentile is real,
   but the distance between strategy and control is timing, exits and
   ranking -- not pattern selection.
4. **We know it is a replacement because making it faithful broke it.**
   Section 14 added the four conditions the source states for a pullback
   (volume drying, depth cap, hold-and-bounce, no gapped high). Returns
   fell from +148%/+147% to +71%/+33% and the test period dropped to the
   23rd control percentile. The configuration that works is the one that
   is not his.

### How it happened

The v2 acceptance gate -- two hand-picked stocks that formed textbook
bases, SPHR in October 2025 and SMCI in January 2024 -- produced **zero**
triggers under the frozen constants. The gate existed precisely to catch
a broken VCP implementation. It fired. The backtest was run anyway on
explicit instruction, and from v5 onward the repertoire routed around the
failed layer instead of repairing it. Four defects in the base machinery
are recorded in the spec and none has been fixed; the `--chain`
diagnostic already shows SPHR passing its case with 2 triggers at $84.67
under a corrected anchor.

### What this system should honestly be called

**A trend-template momentum system with an SMA20 pullback trigger,
tennis-ball exits and a strength ranking.** It makes money in both
periods and beats its controls, and none of that is in doubt. What is in
doubt is the attribution: the two things carrying Minervini's name --
the VCP and the pivot breakout -- contribute six trades out of 1,230.
Every result in this repo should be read as a result about that system,
not about SEPA.

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

### The wide universe (`--wide`, added 2026-08-28)

`download_data.py --wide` adds every other US-listed common stock above
$100M and $5 (Nasdaq screener: NASDAQ + NYSE + AMEX) to the S&P 1500 --
the Russell 2000 / Nasdaq Composite / NYSE Composite ground the index
list leaves out. Three things about it that any result must be read
through:

1. **Survivorship is WORSE, not better.** It is a snapshot of what is
   listed today. Small caps that failed are absent, and small caps fail
   far more often than index members, so the added names are a more
   heavily filtered set of survivors than the ones already there.
2. **It is not a point-in-time index.** A name that IPO'd in 2021 simply
   has no data before 2021 (correct), but a name that was listed in 2009
   and delisted in 2014 is missing entirely (not correct).
3. **It adds fewer opportunities than names.** Measured on the first 637
   downloaded: median 348 days passing the $5M dollar-volume gate,
   against 3,963 for a 400-name S&P 1500 sample, and 14% never liquid at
   all. The liquidity filter, not the name count, sets how much the
   universe actually grows.

The wide names carry their own `earnings_surprise_wide.parquet`, because
the v3 earnings blackout treats a name with no known report date as
clear -- without it the new names would escape a filter the S&P 1500
names face, and "more names" would be confounded with "weaker filter".

## Other known issues

- **Split-adjusted prices (dividend adjustment REMOVED 2026-08-29).** Prices
  now come from `auto_adjust=False, actions=True`, so nothing is rescaled by a
  reinvestment assumption and `dividends` / `splits` are stored beside the
  prices. Dividends are added as explicit cash wherever profit is computed
  (`minervini_bets.py`; `lppl_backtest.close_out`, which also CHARGES them to
  shorts; `giants_features.total_return_prices`, which now derives the
  total-return series instead of inverting Yahoo's). What remains: Yahoo's OHLC
  is still SPLIT adjusted and cannot be had raw from this source, so a 2014
  close still moves when a 2020 split happens. That is the lesser problem — a
  split ratio is a discrete public fact, it is in the `splits` column, and the
  adjustment is exactly invertible. The old convention was rejected because
  Yahoo recomputes the dividend back-adjustment at download time: a 2015 close
  depended on payments made in 2016-2026, the file changed on every re-fetch,
  and nothing computed from it could be reproduced or audited. Measured
  impact: dividends were 13.5% of dev and 16.6% of test per-bet EV, arriving
  invisibly; KO's first stored close moved from 10.87 to 20.77; v5r's dev
  result moved from +148.4% (97th control percentile) to +55.0% (65th).
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

## What the Minervini model does NOT implement

**The complete, systematic inventory is `MINERVINI_COVERAGE.md`** — built
by enumerating the code and the method separately and cross-checking.
This section is the summary; the coverage file is the list.

His method (SEPA) has five pillars: trend, fundamentals, catalyst, entry
points, exit points. We built one and a half of them.

**Not built at all**

- **Fundamentals.** Accelerating earnings, sales and margins — his second
  pillar and the thing that separates a leader from a chart that merely
  looks like one. Our screen is 100% price and volume. `earnings_eps`
  and `earnings_dates` are cached (Steady Giants uses them), so EPS
  acceleration is buildable; revenue and margins are not cached.
- **Catalyst.** New product, earnings surprise, contract, new management.
  Nothing in the repo represents it.
- **Industry-group leadership.** He buys the leader of a leading group.
  Our relative strength is a single universe-wide percentile with no
  sector or peer-group ranking.

**Cannot be built with this data**

- **Intraday volume pace.** He judges price and volume together while the
  breakout is happening. Daily bars give one volume number, after the
  close. Every entry convention we tried is a workaround for that single
  missing input, and each workaround lost money in its own way.
- **The universe is wrong for him twice over.** Current S&P 1500
  constituents: survivorship-flattered, and it also *excludes his actual
  hunting ground* — emerging small and mid caps, recent IPOs, names that
  join the index years after the move he would have traded, or never.
- **Float, turnover, institutional sponsorship.** Not cached.

**Cannot be mechanised honestly**

- **Contraction quality.** He reads shape: tight closes, symmetry, where
  within each pullback the volume dries up, whether the final shakeout
  undercuts a prior low. Our zigzag reduces all of that to a list of
  depth percentages.
- **Entry variety.** Pivot breakout is one of his entries. Undercut &
  rally, the low cheat, pullbacks to the 10/20 EMA, power plays — none
  are implemented.
- **Position sizing and exposure.** Progressive exposure, pyramiding into
  strength, cutting size after losses, scaling total exposure with market
  health. We use flat 10% slots and a binary market light.
- **Selling into strength.** He takes planned partial profits when a
  stock gets extended. Our spec says winners run, with no profit target.
- **Stop placement.** His stop sits under the final contraction's low and
  the trade is skipped unless reward:risk is roughly 2:1 or better. Ours
  is a flat 8% below the fill wherever the base low happens to be.
- **Selectivity.** He passes on most qualifying setups. We take every one
  alphabetically until the slots fill.

**The tell.** Our rendering finds 238 volume-confirmed pivot breakouts in
21 years across 1,496 names — roughly eleven a year, in a universe of
large caps, for a method whose practitioner finds candidates weekly. The
narrowness is ours, not his.

## Trade filters: what they can and cannot see (added 2026-08-29)

A filter (`filters.py`) ranks the signals the screener already produced
and decides which one a freed slot is spent on. This is about the FILTER
models only — the screener's own volume conditions (`dryup_max_ratio`
0.75, `breakout_volume_mult` 1.5) are a separate matter and are applied
before any filter sees a candidate.

| model | volume | what's missing |
|---|---|---|
| Shapelet | absent | volume entirely |
| MiniRocket | present, per-channel | the price x volume interaction |

**Shapelet.** `ShapeletFilter(channels=(0,))` reads `logpx` alone. Volume
is not an input at all. The choice was interpretability — 249 parameters
and eight curves that can be plotted beside a chart — and the price-only
shapelets that were drawn (two flat dead units, three single-day spike
detectors, one dip-and-recover) were found without volume. `--channels 0,2`
adds it and doubles the parameters to 489. Untried.

**MiniRocket.** All five channels are used, `log_volx` among them, but
`transform()` convolves ONE CHANNEL AT A TIME and concatenates afterwards,
so every one of the 4,200 features describes a single channel. No feature
can express "price contracted WHILE volume dried up" — the ridge can
weight a price feature and a volume feature, but that is a sum of two
separate observations, not a co-occurrence.

That conjunction is the VCP claim, so this is not a cosmetic gap. The
published MiniRocket-Multivariate handles it by assigning each kernel a
random subset of channels and summing their convolutions BEFORE pooling,
so one feature fires only when several channels move together. What is
implemented here is the simpler univariate-applied-per-channel variant.

**BOTH GAPS WERE CLOSED AND BOTH MADE IT WORSE (measured 2026-08-29).**
Dev, walk-forward, k=0.50, identical protocol in every arm:

| filter | volume | dev total | maxDD |
|---|---|---|---|
| AllPass (v5r) | -- | +55.0% | -28.0% |
| Shapelet, price only | absent | **+126.3%** | -25.3% |
| MiniRocket, per-channel | present, no interaction | **+104.0%** | -35.8% |
| Shapelet, price + volume (`--channels 0,2`) | added | +73.7% | -25.9% |
| MiniRocket-MV (`--mv`) | price x volume interaction | +68.4% | -26.8% |

Shapelet -53pp when volume was added; MiniRocket -36pp when the
interaction was added. Two different models, two different mechanisms,
same sign.

Why, most likely. **Capacity is fixed, so volume was not added -- it was
swapped in.** The shapelet still has 8 curves, each of which must now
match price AND volume shape at once with the same budget; MiniRocket-MV
still has 4,200 features, with channel groups replacing channels. What got
displaced was carrying the result. And the screener has already spent the
volume information: every candidate passed `dryup_max_ratio` 0.75 and
`breakout_volume_mult` 1.5, so volume structure inside that set is nearly
exhausted while price geometry within it still varies freely.

The mechanism described above is still accurate -- per-channel features
genuinely cannot express a co-occurrence. It simply turns out not to be
worth expressing here. Caveat: dev only, one run per configuration, 3
seeds for the shapelet arms.

## LPPL strategy specifics

- **The fit is stabilised, not validated.** The deterministic grid and the
  3-of-5 window vote make the detector reproducible; they do not make LPPL
  a proven crash model. The replication record of the literature is mixed.
- **tc is a grid median, not a forecast with error bars.** The exit "past
  median tc" inherits the fit's end-of-window bias (tc estimates cluster
  just beyond the data edge).
- **The pre-screen clips ~1% of would-be verdicts.** Probing 2,318 rejected
  refit days found 0.99% would have qualified; the detector therefore misses
  a small tail of bubbles that were not accelerating in the simple sense.
- **Same survivorship-biased universe** as everything else here; bubble-y
  stocks that later died and left the index are missing entirely, which
  flatters any bubble-buying strategy's absolute numbers.

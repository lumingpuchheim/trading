# Minervini Stage-2 Breakout — specification v2 (pre-registered)

Declared 2026-08-27, before any v2 implementation. Supersedes v1 (same
day, rejected — see FINDINGS "Minervini Stage-2 breakout"). v1's trend
template was faithful to the source; its base/breakout mechanics were
not, and the source-audit showed those four deviations are exactly what
killed the acceptance cases and the test period. v2 replaces the
mechanics with the book's own definitions and changes nothing else.

**Epistemic status, stated plainly:** v2 is written after seeing v1
fail. Its constants come from Minervini's published rules and from the
case-study *diagnostics* (SPHR's pause lengths, SMCI's quiet-day
counts) — never from any backtest P&L. The protected object is the
portfolio audit: it has not been run under these rules, nothing in
this file was chosen by looking at returns, and the no-rescue-scan
protocol applies unchanged.

## What v1 got wrong (the deltas — full audit in FINDINGS)

| # | v1 (rejected) | source method | v2 |
|---|---|---|---|
| 1 | pivot = 60-day high (ex last 5) | pivot = high of the final contraction, near the base rim | last confirmed swing high, within 10% of the base high |
| 2 | base age 20–90 (90 unreachable) | bases run 3–65 weeks | age 15–325 trading days |
| 3 | dry-up = 10d mean <= 75% of 50d mean (impossible while volume trends up) | lowest-volume *days* appear in the final contraction | any of the last 5 days <= 75% of the 50d mean |
| 4 | strict std ordering over fixed 10/20/40d blocks | successive pullbacks, each shallower | zigzag pullback depths strictly decreasing |
| 5 | fill next open after a closing breakout (median +2.9–3.8% over pivot; 24–36% of fills > +5%) | buy stop pennies above the pivot; never chase > ~5% | intraday buy stop at pivot x 1.001, skip if the fill would exceed pivot x 1.05 |

## 1. Trend Template — unchanged from v1

All nine must hold on the setup day (daily closes):

1. close > SMA50            2. close > SMA150         3. close > SMA200
4. SMA50 > SMA150           5. SMA150 > SMA200
6. SMA200 higher than 21 trading days ago
7. close >= 1.30 x 52-week low
8. close >= 0.75 x 52-week high
9. RS rank: trailing 126d return in the TOP 30% of the liquid universe
   that day (universe-level membership filter, never a slot priority).

## 2. Base, contractions, pivot (all constants frozen here)

**Base anchor.** B = highest close of the trailing 325 trading days;
day(B) = the last day it printed. If the deepest close since day(B) is
more than 35% below B, the old peak is a prior cycle, not this base's
rim: re-anchor B to the highest close since that trough (repeat until
the correction is <= 35% or no anchor remains). Base age = days since
day(B); require **15 <= age <= 325**. Base low >= 0.65 x B.

**Contractions.** Zigzag on closes with a 3% reversal threshold,
from day(B) to today. Pullback depths d1, d2, … dk (swing high to
confirmed trough). Require **k >= 2**, **strictly decreasing**
(d1 > d2 > … > dk), and the final contraction **dk <= 10%**. The final
trough must be confirmed (price has recovered >= 3% off it) — a V-day
straight off the low is not a completed contraction.

**Pivot.** P = the last confirmed swing high. Require **P >= 0.90 x B**
— a coil collapsing far below the rim is a wedge, not a base; the
breakout must clear the top tenth of the base.

**Volume dry-up.** At least one of the last 5 days has volume
**<= 0.75 x the 50-day mean volume**. (Days, not a 10-day mean: a name
under accumulation has rising volume, so a mean-ratio can never fire —
the v1 mistake that made SMCI "never dry up" despite 27 such days.)

**Setup day** (the watchlist state, "waiting for breakout above P"):
template + liquidity + base + contractions + pivot + dry-up hold, and
close < P. Market light must be green (SPY trend + calm, the gate we
already trust).

## 3. Trigger and fill (buy stop, not next-open chase)

On any day after a setup day, while the setup held on the previous
close:

- **Buy stop at P x 1.001.** If today's high >= that level, the fill is
  `max(open, P x 1.001)` — the stop is either hit intraday (fill at the
  stop) or gapped over (fill at the open).
- **Chase guard:** if the fill price would exceed **P x 1.05**, skip the
  entry entirely. He either gets the pivot or waits for the next setup.
- **Volume confirmation at that day's close:** volume >= 1.5 x the
  50-day mean. If it fails, the breakout is unconfirmed: **sell at the
  next open** (exit reason `failed_breakout`). Only the price touching
  the stop is used intraday; the volume verdict waits for the close, so
  there is no lookahead in the fill.

## 4. Exits — unchanged from v1, one configuration, no scanning

- Fixed stop: close <= 0.92 x entry -> sell next open.
- Trend death: close < SMA50 -> sell next open.
- `failed_breakout` as defined above.
- No profit target, no time cap; winners run. Note the stop is now
  anchored ~8% under a pivot-proximal entry — below the final
  contraction's lows, where the source method puts it — instead of 8%
  under a fill that had already chased.

## 5. Backtest protocol — unchanged

Zero tunables; dev 2007–2018 and test 2019–today both reported; the bar
is positive and non-collapsed in BOTH, judged against the control
distribution. Portfolio mechanics identical to lppl_dip2: 10 slots, 10%
equal weight, 0.2%/side, 20d cooldown. Controls: 200 random portfolios
buying random template-passing stocks on random days, entry-rate
matched, same slots and exits. No rescue scans; the result goes to
FINDINGS either way. Survivorship warning doubled, as in v1.

## 6. Acceptance gate (must pass BEFORE the backtest is trusted)

1. A synthetic escalator with a tightening, drying base triggers
   exactly once, on its constructed breakout day.
2. Random walks essentially never trigger (< 0.1% of stock-days).
3. **SPHR**: at least one trigger 2025-09-01 .. 2026-01-31 below $100.
   (Its October 2025 pause lasted 33 trading days — a real base under
   the 15-day rule; v1's 4-week minimum plus the 60d-high pivot is what
   missed it.)
4. **SMCI: window amended, and here is why.** v1 demanded a trigger in
   H1 2023. The source-audit shows that window was wrong, not just the
   scanner: SMCI in H1 2023 was gap-and-go — it never rested 15 days
   below a peak, and its two upside moves were overnight earnings gaps
   of ~+28%, which the don't-chase rule refuses *by design*. A method
   that buys pivots cannot buy a stock that only moves by gapping over
   them; it catches the NEXT base. Amended case: at least one trigger
   **2023-06-01 .. 2024-01-31** (the post-gap flag or the January 2024
   base before the 300% run). This amendment is declared here, before
   any v2 code exists, and the window will not move again.

If any case fails, stop and report; the backtest is not to be trusted
and does not get run-and-published as if it were.

## 7. Simulator integration (only after the user has seen the verdict)

Unchanged from v1: weekly scan, third email/GUI section `1c. MINERVINI
(stage-2 breakouts)`, BUYABLE / BLOCKED semantics, 10% auto-sizing, and
the "setting up" list — names in the setup state shown as BLOCKED
"waiting for breakout above P x 1.001" with the exact trigger price.
The incremental updater must store volume alongside close.

## 8. Fundamentals gate — SEPA pillar 2 (pre-registered 2026-08-27, before any run)

Added because pillar 2 was never specified and never built. Declared in
full here, before the code exists and before any number is seen; one
run, both periods, no thresholds moved afterwards.

**What the source asks for.** "Code 33": three consecutive quarters of
year-on-year acceleration in EPS, sales AND profit margins, with
quarterly EPS growth of at least 20-25% YoY and sales growth above 15%.

**What this cache can supply.** Quarterly EPS by report date
(`earnings_eps.parquet`, 1,494 tickers, 948 with history from 2007 or
earlier, median 95 quarters). **Sales and margins are not cached.** So
this gate is the EPS leg of Code 33 — one of its three metrics — and
must be read as a partial implementation, not as Code 33.

**The gate**, evaluated on the setup day using only reports dated on or
before that day (`q[-1]` = latest such report):

- **F0 depth.** At least 8 quarterly reports with a non-null EPS.
- **F1 growth.** TTM = sum(q[-4:]), prior TTM = sum(q[-8:-4]). Require
  both > 0 and **TTM / prior TTM - 1 >= 0.25** (the top of the source's
  stated 20-25% minimum, taken as the single frozen number).
- **F2 acceleration, three quarters.** With
  g1 = q[-1]/q[-5] - 1, g2 = q[-2]/q[-6] - 1, g3 = q[-3]/q[-7] - 1
  (each requiring its year-ago quarter > 0), require **g1 >= g2 >= g3** —
  the EPS leg of Code 33's three consecutive accelerating quarters.
- **F3 freshness.** The latest report is within **120 calendar days** of
  the decision day; otherwise the fundamentals are stale and the name is
  excluded.

A name failing any of F0-F3 is not a setup, exactly as if the trend
template had failed. Nothing else changes: same base, same pivot, same
dry-up, same exits, same slots, same costs.

**Comparison protocol.** One run of the market-on-close convention —
the only entry daily bars can express without an invented rule — with
the gate on, both periods, against 200 entry-rate-matched controls drawn
from the template-AND-fundamentals pool. The published no-fundamentals
MOC numbers (dev -12.4%, test -7.9%) are the baseline. Whatever comes
out is reported; there is no second threshold to try.

**Expected cost, declared in advance:** the gate can only reduce the 238
MOC entries, so the comparison will be low-powered and a sign flip in
one period would not be evidence of much. Trade counts are reported
alongside every number.

## 8b. Fundamentals gate v2 — the faithfulness fixes (pre-registered 2026-08-27)

Section 8 is superseded. The source verification above confirmed three
deviations that were mine, not the data's, and they are corrected here
before any v2 number exists. One run, both periods, nothing moved after.

**Growth measure, defined once so turnarounds work.** For the k-th most
recent report, compare it with the same quarter a year earlier:

    g_k = (q[-k] - q[-k-4]) / |q[-k-4]|,  defined when q[-k-4] != 0

The absolute value in the denominator is the whole point: a company that
went from -$1.00 to +$0.50 scores +150% rather than an undefined or
negative number, so a swing from loss to profit is measured instead of
being silently dropped. Section 8 required every year-ago quarter to be
positive and therefore discarded exactly those inflections.

**F0 depth.** At least 8 quarterly reports with a non-null EPS (g4 needs
q[-8]).

**F1 growth — the MOST RECENT QUARTER, not TTM.** Require `q[-1] > 0`
(a shrinking loss is not growth) and **g1 >= 0.25**. The source asks for
20-25% year-on-year in the most recent one to three quarters; the most
recent quarter is the strictest reading of "most recent" and is the one
frozen here. Section 8 applied the threshold to trailing-twelve-month
EPS, which smooths away the inflection the method exists to buy.

**F2 acceleration — three quarters, strictly rising.** Require
**g1 > g2 > g3 > g4**: three consecutive quarters in which the growth
rate accelerated, which is what "three quarters of acceleration" says.
Section 8 used `>=` over two comparisons, admitting flat growth.

**F3 freshness.** Latest report within 120 calendar days. Unchanged.

### Legs that stay unbuilt, with the measurement that rules them out

- **Sales and profit margins.** Code 33 needs both, accelerating for
  three quarters, which requires 8 quarters of history. The provider
  returns **5 quarters for AAPL and 6 for POWL** (measured 2026-08-27).
  Not enough for the backtest and not enough for a live scan either. Two
  of Code 33's three legs remain missing, and with them the
  quality-of-earnings check that catches EPS lifted by buybacks or
  cost-cutting. **This gate is therefore still not Code 33.**
- **Analyst estimate revisions.** The provider exposes only a current
  snapshot, no history at all. Not testable, now or ever, on this source.
- **Earnings beats — CORRECTION, and section 8c below.** I first wrote
  that this data only reaches ~2014 and could therefore never be a
  both-period gate. That was wrong: it came from a probe with a low row
  limit. Fetched properly, the provider returns a median of **96
  quarters per ticker back to 1998**, covering 572 tickers in 2007 and
  780 by 2018 — both periods. The beat leg is therefore buildable and is
  pre-registered in section 8c.

**Comparison protocol.** Identical to section 8: one run of the
market-on-close convention with the gate on, both periods, 200
entry-rate-matched controls from the template-AND-fundamentals pool,
against the published no-fundamentals baseline (dev -12.4%, test -7.9%)
and the section-8 gate (5 trades per period). Reported whatever it says.

## 8c. Earnings-beat leg (pre-registered 2026-08-27, before any run)

The "catalyst" pillar's cheapest observable: did the company beat the
consensus estimate on its latest report? `data/earnings_surprise.parquet`
(ticker, report date, EPS estimate, reported EPS, surprise %) — fetched
for the whole universe, median 96 quarters per ticker, 1998-2026, 98.8%
of rows carrying a surprise figure. Known on the report date, so using it
from that date forward is causal.

**F4 beat.** The most recent report on or before the decision day has
**surprise_pct > 0**. Names with no surprise figure for that report fail
F4 rather than passing by default.

Two runs, declared together, so neither is a scan:

1. **F4 alone** on top of the technical setup (no EPS gate) — is the
   catalyst worth anything by itself?
2. **F4 + the section 8b EPS gate** — the two fundamentals legs together.

Both over both periods, market-on-close convention, 200 entry-rate
matched controls from the matching pool, plus the high-powered split of
all 4,585 buy-stop fills by F4. Reported whatever they say.

## 9. v3 — the three sourced violations, fixed (pre-registered 2026-08-27)

The fidelity audit (MINERVINI_COVERAGE.md) found three places where the
code violates the sourced method, none of them data-limited. The user
approved fixing exactly these three. Constants frozen here, one run
(market-on-close convention, both periods, 200 entry-rate-matched
controls), nothing moved afterwards.

**Epistemic status:** the blackout window and the decisiveness threshold
were chosen after the audit measured those splits descriptively on the
same history (trade returns by earnings proximity; SMA-exit depth and
volume). No portfolio P&L under these rules has been seen. That is a
weaker pre-registration than v2's and is stated as such.

1. **Higher lows** (`require_higher_lows: true`): every contraction
   trough in the base must sit strictly ABOVE the previous trough —
   ascending bottoms, the sourced "demand absorbing supply". A base
   whose final low undercuts a prior low is distribution and is not a
   setup. (Was: only the depths had to shrink; 39% of v2's trades sat
   on undercutting bases.)
2. **Earnings blackout** (`earnings_blackout_days: 21`): a name is not a
   setup while its next known report is within 21 calendar days — no
   time to build the cushion the source demands. A name with no known
   upcoming report is clear (absence of a calendar is not evidence of
   an imminent report). (Was: 23% of entries within 3 weeks of a
   report, at triple the average loss.)
3. **Decisive trend exit + breakeven-or-better**
   (`decisive_break_frac: 0.01`, `decisive_volume: true`,
   `breakeven_r: 2`): the 50-day exit fires only on a DECISIVE break —
   close more than 1% below the SMA50, or any close below it on
   above-average volume (> the 50d mean). And once a position has shown
   a profit of 2R (R = the 8% stop distance, so +16%), a later close at
   or below the entry price sells at the next open: a 2R winner must
   not become a loss. (Was: any close below the SMA50 from day one;
   57% of exits fired within 1% of the average.)

Everything else — template, base, pivot, dry-up, volume confirmation,
chase guard, 8% stop, slots, costs, cooldown, controls — unchanged from
v2. The 8% stop still caps every loss; rule 3 only removes the
hair-trigger, it does not widen the risk.

---

## BUILD STATUS (updated 2026-08-27, after implementation)

This section is a record, written after the fact. Everything above it is
the pre-registered specification and has not been edited since the gate
ran; everything stated here is what the code actually does.

### Implemented and verified

| spec section | code | tests |
|---|---|---|
| 1. Trend template, all nine conditions | `minervini.trend_template`, `rs_ok_matrix` | 7 unit tests + visual audit (`minervini_showcase.py`) |
| 2. Confirmed swing structure | `minervini.zigzag` | 3 unit tests (alternation, confirmation never precedes the swing, sub-threshold noise ignored) |
| 2. Base rim + prior-cycle re-anchor | `minervini.anchor_base` | 1 unit test |
| 2. Age, contractions, pivot, dry-up | `minervini._base_day`, `vcp`-side of `signals` | 5 unit tests |
| 3. Buy stop, chase guard, gap fill | `minervini.signals` -> `trigger`, `fill_px` | 4 unit tests |
| 3. Volume confirmation | `signals` -> `vol_ok` | 1 unit test |
| 4. Exits (8% stop, SMA50, failed_breakout, delisted) | `minervini_backtest.simulate` | exercised by the audit |
| 5. Portfolio + 200 entry-rate-matched controls | `minervini_backtest` | — |
| 6. Acceptance gate | `minervini_gate.py` | 2 strict xfails + 2 diagnosis tests |
| no-lookahead | truncation test over every output | 1 unit test |

24 tests, all green (2 as strict xfails: the acceptance cases).

### Deviations from this spec that the code carries

1. **A third fill convention was added** after the audit, at the user's
   instruction: market-on-close (`--moc`). Price above the pivot and 1.5x
   volume are both knowable at the close, so it buys there and needs no
   eject. Sections 3 and 4 above describe the buy-stop convention only;
   both are implemented and both are reported.
2. Nothing else. No constant in sections 1-4 was changed after any run.

### Defects IN THIS SPECIFICATION, found by implementing it

Recorded, not fixed — fixing them is a new pre-registration.

1. **The base anchor is wrong (section 2).** Anchoring the base at the
   325-day rim and measuring age *and* the contraction chain from it
   means a marginal new high inside a base resets the age to zero and
   deletes the earlier contractions. SMCI's real January-2024 structure
   (-12.9% then -9.4%, a valid two-contraction base) is scored
   `only_1_contractions`; SPHR dies as `age_1..age_14` on 43 of 105 days.
   Measured fix (`minervini_gate.py --chain`, diagnostic only): anchor at
   the START of the contraction chain — walk back while pullbacks keep
   deepening. SPHR then passes its acceptance case with 2 triggers at
   $84.67.
2. **The `failed_breakout` eject (section 3) is not Minervini.** He does
   not buy an unconfirmed breakout and sell it the next morning; he does
   not buy it at all. This invented rule produced 90-92% of all trades
   and most of the loss. It exists only because a buy stop fills before
   the day's volume is knowable.
3. **The confirmed-trough requirement (section 2) is a zigzag artifact.**
   Requiring the final low to have recovered 3% before a setup exists
   means a base whose recovery gaps straight through the pivot never
   produces a setup day at all — which is precisely what happened to
   SMCI in January 2024.
4. **`base_age_max` (325) can never bind** while `base_lookback` is also
   325 and the rim must precede today. Harmless, but it is not a real
   constraint.
5. **A factual error in section 6, case 3.** It states SPHR's October
   2025 pause "lasted 33 trading days". That number was carried over
   from a v1 diagnostic whose pivot excluded the last 5 days, so it kept
   counting while the stock was already printing new highs. Measured
   properly, SPHR's longest stretch without a new all-time-high close in
   the whole acceptance window is **16 trading days** (max drawdown from
   the running high, 15.3%). The 15-day minimum is therefore right at
   the edge for SPHR rather than comfortably satisfied — the case was
   built on a mismeasurement. Section 6 is left as written because it is
   the pre-registered record; this is the correction.


### Faithfulness audit of the fundamentals gate (section 8)

Asked directly whether section 8 follows the source: **no, it is a
partial and in one place a distorted rendering.** The deviations, worst
first:

1. **One metric of three.** Code 33 is EPS *and* sales *and* net margin,
   each accelerating three quarters. Only EPS is cached. The other two
   legs exist precisely to prove the earnings growth is real business
   growth rather than cost-cutting, buybacks or one-offs — so what was
   built passes exactly the low-quality earnings growth the missing legs
   were designed to reject.
2. **The 25% threshold was applied to the wrong quantity.** The source
   asks for 20-25% growth in the most recent one to three *quarters*,
   year on year. Section 8 applies it to trailing-twelve-month EPS
   against the prior TTM. TTM smooths: it passes a company whose latest
   quarter is decelerating hard behind three strong ones, and fails one
   that has just inflected — which is the exact moment the method wants
   to buy. This is not a data limitation; it is a substitution made
   while writing the spec.
3. **"Acceleration" was implemented as non-decreasing** (g1 >= g2 >= g3).
   Acceleration means increasing. The `>=` admits flat growth.
4. **Loss-to-profit inflections are excluded by construction.** Each
   year-ago quarter is required to be positive so the growth ratio is
   defined, which throws out the turnaround from a loss — a setup the
   source explicitly likes.
5. **No earnings surprise, no estimate revisions, no annual or 3-year
   growth.** He uses beats against expectations and upward forward
   revisions; no estimates are cached.
6. **Applied as a hard binary gate on the setup day.** He uses
   fundamentals to build a watchlist and to size conviction, not as an
   on/off switch evaluated at the instant of the breakout.
7. **The EPS series is as-reported and may be restated.** yfinance
   supplies reported EPS by report date (`giants_data.py`); restatements
   would mean using a number that was not known at the time. Small, but
   it is a lookahead risk and it is not controlled.

Points 2, 3 and 4 are mine and were avoidable with the data in hand.
Points 1, 5 and 7 are data limitations. Point 6 is a modelling choice.
Any future version should fix 2-4 before another number is generated.


### Source verification of the claims above (checked 2026-08-27)

The user asked for proof that my characterisation of the method is what
the source actually says, not my paraphrase. Each load-bearing claim was
checked against public sources. Caveat first: the books are the primary
source and are not readable here, so all of this is secondary — screener
documentation, book summaries, and one direct book quotation.

**Confirmed — these drove the conclusions and they hold:**

| claim | status |
|---|---|
| Code 33 = three quarters of acceleration in earnings, sales AND profit margins | **confirmed**, direct book quote: "Look for what I call a Code 33 Situation, three quarters of acceleration in earnings, sales and profit margins" |
| the 20-25% growth test is QUARTERLY year-on-year, not TTM | **confirmed** — "year-over-year quarterly comparisons", 20%+ minimum, 40-50%+ exceptional |
| "acceleration" means increasing, not flat | **confirmed** — "sequential increases in the growth rate (e.g. 10%, then 30%, then 50%)" |
| sales growth >15%, margins expanding, as separate legs | confirmed |
| never chase more than ~5% above the pivot | **confirmed** — "the entry should be the pivot, not several percent above it"; within ~5% |
| bases run 3-65 weeks | confirmed |
| pivot = high of the final contraction | confirmed |
| dry-up = the lowest-volume DAYS sit in the final contraction | confirmed |
| in cash ~50% of an average year; win rate under 50% | confirmed |

**Corrections to statements I made to the user earlier:**

1. **Breakout volume.** I said the source asks for "roughly 1.3-1.4x
   (30-40% above average)". A direct screener source states **40-50%
   above average**, i.e. 1.4-1.5x; other secondary sources say 30-40%.
   Our frozen 1.5x is therefore at the strict end of a range that varies
   by source, not above it — it is faithful. (The SPHR conclusion is
   unaffected: its breakouts printed 0.50x and 0.75x, *below* average, so
   no threshold anywhere in that range admits them.)
2. **Stop placement.** I implied our fixed 8%-from-entry stop was
   unfaithful. Sources describe him using **both** — structurally "just
   below the low of the last contraction" **and** a 5-8% rule from
   entry, with an average realised loss of 4-5% and a 10% maximum. Our
   8% number is inside his stated range. What we actually lack is the
   structural leg and the reward:risk floor before entry — a
   simplification, not a contradiction. The earlier framing overstated it.
3. **Loss-to-profit turnarounds.** I asserted he "explicitly likes"
   them. **I could not find a source for that.** Retracted as
   unverified. Excluding them therefore remains a limitation of the
   implementation, but it is not a documented deviation from him.

Everything in the fundamentals faithfulness audit above stands after this
check, with deviation 4 (turnarounds) downgraded from "deviation" to
"unverified".

### Results (full detail in FINDINGS.md)

Acceptance gate: **FAIL** — SPHR 0 triggers, SMCI 0 triggers. Per section
6 the backtest should not have been run; it was run anyway on the user's
explicit instruction, in preference to hand-amending the rules.

| entry convention | dev | test | trades | vs 200 controls |
|---|---|---|---|---|
| v1 next-open chase (superseded) | +7.5% (t 0.63) | -23.7% (t -3.0) | 104 / 76 | 63% / 0% |
| v2 buy stop + eject (this spec) | -42.8% (t -5.46) | -31.3% (t -1.76) | 1122 / 1200 | 0% / 0% |
| v2 market-on-close | -12.4% (t -1.58) | -7.9% (t -0.56) | 113 / 83 | 3.5% / 16% |
| v2 MOC + fundamentals (section 8, sloppy) | -1.7% (5 trades) | -1.7% (5 trades) | 5 / 5 | no power |
| v2 MOC + fundamentals (section 8b, faithful) | -2.9% (9 trades) | -3.5% (9 trades) | 9 / 9 | no power |
| v2 MOC + earnings beat (section 8c) | -9.3% (t -1.28) | -3.5% (t -0.22) | 91 / 67 | 8.5% / 23% |
| v2 MOC + beat + EPS gate | -3.2% (8 trades) | -3.5% (9 trades) | 8 / 9 | no power |

Universe funnel: 906,079 template stock-days -> 11,171 setup days ->
4,676 buy-stop fills -> 402 volume-confirmed -> 238 market-on-close
entries, over 21 years and 1,496 names.

### NOT built (and why)

**Superseded by `MINERVINI_COVERAGE.md`**, the single complete inventory.
What follows is the earlier partial summary, kept for provenance.

The signal layer is complete against this spec. The *method* is not: SEPA
has five pillars — trend, fundamentals, catalyst, entry points, exit
points — and this spec only ever addressed trend and entry, plus a
simplified exit. Full inventory in `LIMITATIONS.md`; the headline gaps:

- **Catalyst: PARTLY BUILT (section 8c).** `minervini.beat_gate` +
  `--beat`, fed by `fetch_surprise.py` (1,495 names, median 96 quarters
  back to 1998). The only fundamentals element that improves both
  periods while leaving the strategy runnable (-12.4%/-7.9% ->
  -9.3%/-3.5%, 91/67 trades), but still negative and still under its
  controls; the underlying effect is +0.35% per 60 days, t 0.52. The
  rest of the catalyst pillar — products, contracts, management — is not
  represented at all.
- **Fundamentals: BUILT (sections 8 and 8b), EPS leg only.** `minervini.eps_gate`
  + `--fund`. Section 8b corrected three deviations that were mine
  (quarterly not TTM, strictly rising, turnarounds measurable). Sales and
  margins remain unobtainable — the provider returns 5-6 quarters against
  the 8 Code 33 needs — so this is still one leg of three. As a filter it
  leaves 9 trades per period. Measured on 4,585 fills, the faithful gate
  scores +1.35% vs +2.08% (t -0.67, negative in both periods); the
  sloppy section-8 version's apparent +2.55% (t 2.03) did not survive
  being implemented correctly. See FINDINGS.
- **Never specified here, never built:** industry-group leadership.
  Analyst estimate revisions are impossible on this source (current
  snapshot only, no history).
- **Cannot be built on this data:** intraday volume pace (the input both
  failed fill conventions were working around), a point-in-time
  emerging-growth universe (ours is *current* S&P 1500 — survivorship-
  flattered and it excludes his actual hunting ground), float and
  institutional sponsorship.
- **Not mechanised:** contraction quality as a shape rather than a list
  of depths; his other entries (undercut & rally, low cheat, pullback to
  the 10/20 EMA, power play); position sizing, pyramiding and progressive
  exposure; selling into strength; a stop under the final contraction's
  low with a reward:risk floor; and selectivity — he passes on most
  qualifying setups, we take every one alphabetically until the slots
  fill.
- **Section 7 (simulator integration): not built.** It was gated on a
  passing verdict and there is none.

## Build order — status

1. **DONE** — `minervini.py` rewritten to this spec, 24 tests in
   `tests/test_minervini.py`.
2. **DONE — FAILED** — `minervini_gate.py`; reported before any backtest.
3. **RUN ANYWAY, on user instruction** — `minervini_backtest.py`
   (buy-stop) and `--moc`; controls, both periods, FINDINGS entries for
   both. The gate failure is recorded alongside every number.
4. **NOT BUILT** — simulator integration was gated on a passing verdict.

Supporting scripts written along the way, all committed:
`minervini_gate.py` (acceptance gate + rejection funnel + `--chain`
diagnostic), `minervini_failures.py` (v1 event study, six worst trades,
`--v2` volume-confirmation study), `minervini_showcase.py` (per-trade
anatomy: trend, base, contractions, dry-up, buy, exit).

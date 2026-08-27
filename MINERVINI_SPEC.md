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

## Build order

1. Rewrite `minervini.py` signal functions to this spec + unit tests
   (escalator, random walk, zigzag reference checks, no-lookahead
   truncation test, SPHR + SMCI acceptance cases).
2. Run the acceptance gate. Report it to the user pass or fail.
3. Only on a green gate: `minervini_backtest.py` under the new signals
   (buy-stop fills, chase guard, failed-breakout eject), controls,
   both periods, FINDINGS entry win or lose.
4. Simulator integration after the verdict is on the table.

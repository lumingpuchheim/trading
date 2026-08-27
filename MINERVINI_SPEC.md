# Minervini Stage-2 Breakout — specification (pre-registered)

Declared 2026-08-27, before any implementation. Third recommender
(source label `MINERVINI`), filling the gap between the systems we have:
the young, steady, strong climber that is not old enough for
STEADY_GIANTS (no 5y record, no dividends) and not parabolic enough yet
for LPPL_DIP2 (certification needs terminal acceleration — SPHR was
flagged at $144 after the template below already held at $65).

Buys STRENGTH at a breakout — the opposite entry direction to every
system tested in this repo so far.

## The two-part signal (all constants frozen here; a `minervini:` block
in config.yaml carries them)

### 1. Trend Template — is the stock in a Stage-2 uptrend?

All nine must hold on the trigger day (daily closes):

1. close > SMA50            2. close > SMA150         3. close > SMA200
4. SMA50 > SMA150           5. SMA150 > SMA200
6. SMA200 higher than 21 trading days ago
7. close >= 1.30 x 52-week low
8. close >= 0.75 x 52-week high
9. RS rank: trailing 126d return in the TOP 30% of the liquid universe
   that day (mechanical stand-in for Minervini's RS >= 70).

Note the distinction from the rejected `_rs` experiment: there, RS
ranked candidates INSIDE an already-selected pool for slot priority
(failed OOS); here it is a universe-level membership filter — the
canonical use. Declared, not re-litigated.

### 2. Mechanical VCP + pivot breakout — when to buy

- **Pivot** = highest close of the trailing 60 trading days, excluding
  the last 5 (the base must have a left side).
- **Base age**: that pivot high was set 20–90 trading days ago.
- **Volatility contraction**: std of daily returns over the last 10d
  < std over the prior 20d < std over the prior 40d (strictly).
- **Tight final range**: (max−min)/max of the last 10 closes <= 8%.
- **Volume dry-up**: 10d mean volume <= 75% of 50d mean volume.
- **Trigger**: today's close > pivot AND today's volume >= 1.5 x 50d
  mean volume. Buy at the NEXT open (house fill convention).
- Market light must be green (SPY trend + calm — Minervini himself
  gates on market health; we use the gate we already trust).

### Exits (pre-registered, exactly one configuration, no scanning)

- Fixed stop: close <= 0.92 x entry -> sell next open (his 7–8% rule;
  our standard).
- Trend death: close < SMA50 -> sell next open (Stage 2 over).
- No profit target, no time cap; winners run. The exit-law lesson
  (responsive exits die) was proven on a DIP-buyer whose entries sat
  near the SMA; breakout entries start far above it. If this exit is
  wrong, the backtest will say so — it will not be rescued by an exit
  scan afterwards.

## Backtest protocol (zero tunables — an audit, not a fit)

Every constant above is frozen before the first run. Dev 2007–2018 and
test 2019–today both reported; there is nothing to select, so the bar
is: adoption interest only if positive and non-collapsed in BOTH
periods. No rescue scans if it fails; the result stands and goes to
FINDINGS either way. Portfolio mechanics identical to lppl_dip2 for
comparability: 10 slots, 10% equal weight, 0.2%/side, 20d cooldown.

**Controls (the actual science):** 200 random portfolios that buy
random template-passing stocks on random days (same slots, same exits).
This separates "VCP breakout timing adds value" from "owning Stage-2
stocks in a survivor universe looks good". The comparison to beat is
the control distribution, not SPY.

**Survivorship warning, doubled:** buy-high-sell-higher is the single
most flattered strategy class on a survivor-only universe. All results
are upper bounds; the random-template control is the partial antidote;
the honest judge is the simulator's forward ledger.

## Acceptance case study (must pass before the backtest is trusted)

SPHR: the scanner, run on history, must produce at least one breakout
trigger between 2025-09-01 and 2026-01-31 at a price below $100 (the
escalator phase). SMCI must trigger in H1 2023. A random-walk synthetic
must essentially never trigger. These are unit tests, not tuning aids.

## Simulator integration (after the user has seen the backtest verdict)

- Weekly scan -> third email/GUI section `1c. MINERVINI (stage-2
  breakouts)` with the source label on every row, same BUYABLE /
  BLOCKED semantics and 10% auto-sizing.
- **The "setting up" list is the point**: names where the template +
  contraction + dry-up hold but the pivot is NOT yet broken are shown
  as BLOCKED — "waiting for breakout above $X" — so an SPHR-type
  escalator is visible while it consolidates, with its exact trigger
  price, instead of appearing months later at the summit.
- Live data note: the incremental updater must start storing volume
  alongside close (the dry-up and trigger need it); the frozen research
  cache already has it.

## Build order

1. `minervini.py` — template / VCP / trigger functions + unit tests
   (synthetic escalator passes, random walk fails, SPHR + SMCI case
   studies).
2. `minervini_backtest.py` — portfolio audit + random-template
   controls, both periods, charts, FINDINGS entry win or lose.
3. Simulator integration (scan, email section, GUI, tests) after the
   verdict is on the table.

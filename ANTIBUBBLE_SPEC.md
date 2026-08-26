# Anti-bubble detection & profit paths — specification (pre-registered)

Declared 2026-08-26 before any implementation. Companion to the bubble
detector (`lppl.py` / `lppl_detect.py`); frozen constants carry over.

## The model

Sornette's anti-bubble is the mirror regime: after a peak at critical
time tc in the PAST, price decays as a log-periodic power law of the
time SINCE tc:

  ln p(t) = A + B·τ^m + C·τ^m·cos(w·ln τ + φ),   τ = t − tc > 0,  B < 0

— structured, decelerating decline with stretching oscillations
(herding on the way down), vs. the bubble's accelerating ascent into a
FUTURE tc. Mechanically a small mirror of the existing fit: the grid
tensor uses τ = t_index + tc_behind (tc before the window start) instead
of dt = (window end + tc_ahead) − t. Same fixed grids for m, w; tc_behind
spans [5, 0.5·window] days before the window start; same qualification
(B < 0, R² ≥ 0.8, damping ≥ 1); same 5 windows, 2-of-5 loose gate,
persistence 2, refit every 5 days.

**Frozen by declaration:** every constant reuses the bubble-side value.
No re-tuning of grids or thresholds on the anti side — the bubble side
already spent its selection budget; the anti side inherits, take it or
leave it.

**Pre-screen (mirror, deliberately loose):** drawdown ≥ 25% from the
252-day high AND close < 200-day SMA. Its job is compute, not selection;
validated by random probes exactly like the bubble pre-screen.

**Output:** `data/lppl_anti_flags.parquet` — same row schema as the
bubble flags (votes, tc_behind, params), separate file, ~one detector
run (~20–50 min).

## Profit paths, in test order (cheapest and most promising first)

### V1 — Anti-bubble breadth as the missing 2021 gauge (regime use)

The model's blindest failure mode is the hidden growth bear: speculative
stocks falling all year under a rising index (2021). Every price-based
regime gate died because "cohort falling" is also what a buyable dip
looks like. Anti-bubble certification is a DIFFERENT observable:
structured log-periodic decay, not mere decline. Claim to falsify,
stated before any code:

- daily count of certified anti-bubbles (liquid universe), normalized;
  hostile when count above a trailing 3y 80th percentile
- MUST mark: 2021 H2 ≥ 50% of days hostile, 2022 ≥ 50%, Mar–May 2020
  hostile
- MUST NOT mark: 2009–2013 ≤ 20% of days, 2023–2025 ≤ 25%
- then the standard trade-level kill test: average return of
  lppl_dip2 trades the gauge would block vs allow, BOTH periods. If
  blocked ≥ allowed (the flagged-cohort failure), V1 dies regardless of
  the regime table.

### V2 — Stock-level no-buy veto (discrimination use)

The bedrock problem: at decision time a dip and a collapse are the same
observable. Anti-bubble certification claims to tell them apart AT THE
STOCK LEVEL: a 4%-dip candidate that is simultaneously a certified
anti-bubble is a collapse in progress, not seller exhaustion. Test:
lppl_dip2 (and later the giants' buy rule) refuses candidates currently
anti-certified. Kill test: vetoed trades' average return vs kept, both
periods; adoption bar as always (dev not degraded, test not collapsed).

### V3 — Short the decay (mirror strategy; low prior, tested last)

Short certified anti-bubble stocks on bounces (close ≥ 4% above the
20-day low), adverse stop 8%, cover at the fitted curve's next
oscillation trough or a 60-trading-day cap (anti-bubbles have no
forward tc; their end is notoriously ill-defined — the clock is a cap,
not a forecast). Declared expectations and handicaps:

- the plain bubble short already failed decisively (dev t −4.4);
- borrow cost/availability is unmodeled (results are optimistic);
- BUT survivorship cuts the other way here: our universe holds only
  companies that survived into today's index, so every backtested
  decline "recovers" by construction — the backtest is PESSIMISTIC for
  shorts. A positive result would therefore mean more than usual; a
  negative one less. Both periods reported; V3 runs only if V1 or V2
  shows the certification carries information at all.

## Protocol

Dev 2007–2018 selects, test 2019+ audits once, per house rules. No new
tunables exist by construction (all constants inherited). FINDINGS
entry win or lose. Implementation order: mirror grid in `lppl.py`,
`lppl_anti_detect.py`, V1 gauge script, V2 veto backtest, V3 last.

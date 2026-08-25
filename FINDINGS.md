# Findings — seller-decay and LPPL bubble strategies

Status as of 2026-08-25. Universe: current S&P 1500 constituents (survivorship-
biased — see `LIMITATIONS.md`; all absolute numbers are optimistic). Data:
yfinance daily OHLCV 2005–today. Development period 2007–2018, test period
2019–today. Costs 0.2% per side. All t-stats assume independent trades and
therefore overstate significance; trades are correlated (up to 10 concurrent
positions, shared market shocks).

## 1. Seller-decay (mechanical VCP) — dead

Exponential-decay fits of normalised range/volume inside tight bases
(`screener.py`), ridge-learned expected returns and quarter-Kelly sizing
(`learn.py`, 15,836 dev base-trades).

- The ridge gave **lambda a negative weight** (lambda_vol −0.0024, one-input
  lambda −0.001): faster seller-decay predicted *worse* returns. All weights
  < 0.3% per standard deviation.
- Test-period edge quintiles: top vs bottom +0.03% per trade — nothing.
- Dev equity: model_kelly +20%; test: model_kelly +47% but *worse than both
  no-model ablations* (template +65%, random +61%).

## 2. Does fit quality predict profit? — no

61,017 unfiltered base-trades: among decaying bases, Spearman(r2, return) =
**−0.025 dev / −0.003 test**. The only pocket consistent across both periods
was the top r2 decile of *expanding* (anti-model) bases: ~+1.2% per trade in
both. Where the sample had power, the seller-exhaustion effect was absent.

## 3. LPPL (Sornette) bubble detection

Deterministic fixed-grid Filimonov–Sornette fit, 5 windows (125–500d),
qualification B<0, R² ≥ 0.8, damping ≥ 1; 3-of-5 vote, 2-evaluation
persistence (`lppl.py`, `lppl_detect.py`). Detector: ~19 ms per evaluation,
full universe ~20 min on 7 cores.

**As identification, it works; as a buy signal, it is structurally late.**
Certification requires super-exponential curvature, which only exists in the
terminal phase. STX 2025–26 did a 9x (89→782) under weekly evaluation with
0 of 5 votes before the flag finally fired at $782, six weeks before the top.

**The gate cannot see the bubble end.** Fit quality is scored on a 125–500
day window; a 17% crash moved STX's R² from 0.9746 to 0.9737 and pushed the
estimated tc *further out* (17→46→81 days) as the fitter re-explained the
decline. There is no condition tying today's price to the fitted curve; the
flag survives the crash it was supposed to warn about, with weeks of lag.
Consequence (both failure charts, CHWY 2021 and STX 2026): the strategy's
"first 4% dip inside a certified bubble" is selected for being the crash's
first day. Stops fill on the next open, so 8% nominal risk realises at
−15…−20% on gaps.

### Variants tried (equal-weight 10%, 10 slots, 8% stop + past-tc exit)

| variant | dev total (t) | test total (t) | verdict |
|---|---|---|---|
| lppl_dip (3-of-5, buy dips) | +47% (1.6) | −26% (−1.1) | noise, negative OOS |
| bubble_nodip (no dip wait) | +34% (1.3) | −24% (−1.2) | dip wait ~irrelevant |
| tc widened to 1.0 windows | +18% (1.0) | −37% (−2.0) | hurt dev → reverted |
| one-profit-per-bubble rule | identical | identical | never fires; cooldown + tc exits already prevent same-episode re-entry |
| lppl_short (mirror) | −67% (**−4.4**) | −3% (0.1) | decisively bad: intra-bubble wobbles stop shorts out repeatedly |
| lppl_bottom2 (buy fitted-curve bottom) | 2 trades | 0 trades | self-refuting: damping ≥ 1 forces monotone fitted curves — the model predicts no buyable bottoms; real 4% dips are 2–4x its oscillation amplitude |
| **lppl_dip2 (2-of-5 gate)** | **+150% (2.9)** | **+91% (1.9)** | only variant positive in both periods |
| dip_only (pre-screen + dip, no LPPL) | −23% (0.1) | +60% (1.4) | momentum alone beats certification OOS |

### The one encouraging result

`lppl_dip2` — the loose 2-of-5 gate — beats both its neighbours (strict gate
and no gate) in both periods, and its dev t-stat is the only one above 2.
The 2-vote state precedes certification by weeks to months (STX: February vs
May 2026), consistent with the general lesson: **the value is in the
formation/acceleration phase; certification marks the end.** Performance in
this family improves monotonically as the gate is relaxed toward earlier
entry.

Caveats, in order of severity: ~6 variants were tried, so t=2.9 as the best
of six is worth p ≈ 1–2% before the correlated-trades and survivorship
corrections; the test period was examined repeatedly during design and is
not clean; the pre-screen clips ~1% of would-be candidates.

**Frozen candidate:** `lppl_dip2` exactly as of commit `34f4347` (config
included). No further tuning. Judge it only on data arriving after
2026-08-25 — the only sample nothing here has peeked at.

### Exit variants (tested 2026-08-25, removed)

Same entries, three exits. The tc clock beat both alternatives in both
periods — dev +154% / +66% (trail) / +10% (SMA); test +86% / +60% / −3%:

- **Trailing 8% stop**: shaken out by the same intra-bubble oscillations
  the entry buys (448 of 450 dev exits were trail-stops; avg winner shrank
  +22% → +13%; churn + cooldown made it miss resumptions). A dip-buying
  entry and a wobble-selling exit cancel each other. Only merit: test
  maxDD −33% vs −47%.
- **50-day SMA cross**: a dip-seller attached to a dip-buyer — positions
  bought 4–8% below the 20-day high sit near the SMA by construction, so
  it churned 769 dev trades at ±3–4% each; t-stat 0.7 dev, 0.3 test.
- **Takeaway**: the tc clock's value is not accuracy but *deafness to
  price wobbles* — time-based patience plus price-based ejection (the
  fixed stop) beats every price-based profit exit tried.

### tc tuning (tested 2026-08-25, rejected)

Protocol: scan exit shift tc+k, k = −15..+20 trading days, on dev only;
select by highest dev t-stat; run test once for the selected k.

- Dev surface is **flat**: total returns +108%..+161%, t-stats 2.40–3.00
  with no structure. Shifting the clock three weeks either way barely
  matters — consistent with tc carrying timing information only at the
  months scale, not weeks.
- Selected k = −10 (dev t 3.00 vs baseline 2.90) scored **worse** on its
  single test run: +68% (t 1.75) vs the baseline's +86% (t 1.87).
  Textbook regression to the mean: picking the max of eight near-identical
  noisy values bought selection bias, and the test period returned it.
- Baseline (k = 0) stands. The tc exit works because it is a clock, not
  because the clock is precise.

### Crash-entry guards (tested 2026-08-25, REJECTED by protocol)

Motivated by the March-2020 failure mode (buying market-wide collapses as
"dips"): guard 1 = entry only while log-price >= own fitted curve − 3
fitted-sigma; guard 3 = no entries while SPY is itself >= 4% below its
20-day high. Both mechanical, no external data.

- They work as designed: COVID-window entries 22 → 6 (avg −8.2% → −1.8%),
  2022 entries 27 → 10; test +106% (t 2.13, maxDD −32%) vs baseline +86%
  (t 1.87, maxDD −47%).
- But dev: +67% (t 1.99) vs +154% (t 2.90) — only 24 fewer trades, yet
  half the return gone. The vetoed dev entries were disproportionately
  winners: in 2009–2012 the market was chronically below its 20-day high
  and stocks far below their fitted curves, and buying exactly those
  systemic dips was the most profitable behavior in the sample.
- **Decision: rejected.** The protocol says dev selects, test audits;
  dev rejects the guards decisively. The attractive test number is the
  predicted artifact of designing rules while staring at 2020's failures.
  Deeper reading: crash losses are not a bug a rule can remove — they are
  the price of the same behavior that produced the 2009–2012 gains. The
  guarded variant stays in the code as a registered alternative for
  post-2026 data to judge.

### Dip ceiling and breadth veto (tested 2026-08-25, both rejected)

- **Dip ceiling** (entry only for dips 4–10% below the 20-day high; deeper
  = off-model): loses in BOTH periods — dev +76% (t 2.10) vs baseline
  +154% (t 2.90); test +29% (t 1.19) vs +86% (t 1.87). Deep dips carry
  both the crash disasters and a disproportionate share of the big
  winners; cutting them removes more profit than pain. Unambiguous no.
- **Breadth veto** (>5 simultaneous new candidates = systemic day, take
  none): the mirror image of the guards — slightly better dev (+174%,
  t 3.14, the best dev configuration seen) but worse test (+43%, t 1.46).
  The dev edge is within noise and the test period does not confirm it;
  not adopted.
- Combined (+both): middling dev, poor test. Rejected.

### Flag-death exit (tested 2026-08-25, rejected — worst exit tried)

Sell when the detector stops affirming the bubble (votes < 2) instead of
waiting for the stale tc date. Decisively bad: the raw vote count flickers
(the multi-window instability), so positions are dumped within days — 1,019
dev trades (vs 295), 907 of them flag-exits, avg winner collapses +22% →
+5%, dev −33% (t −1.6), test +27% (t 1.1). Confirms the pattern from the
trailing-stop and SMA tests: every *responsive* exit — price-based or
fit-based — is destroyed by noise it responds to; the tc clock survives
because it is deaf. Exit design space now fully explored: calendar shifts
(flat), price triggers (bad), fit triggers (worse). The baseline exit is
final.

**Running tally: every modification attempted on lppl_dip2 — vote gates
(1/3-of-5), short mirror, curve-timed entry, Kelly sizing, trailing/SMA
exits, tc shifts, crash guards, dip ceiling, breadth veto — has failed to
beat the baseline on both periods jointly.** The baseline is a local
optimum in every direction probed. The skeptical reading (favoured by the
sample sizes): it sits at the peak of the selection process that created
it, and its true edge remains unproven until post-2026-08-25 data rules.

## 4. Statistical reality (applies to everything above)

Per-trade σ ≈ 16–22%. Detecting a true 1% per-trade edge at t=2 needs
~1,400+ independent trades; these strategies produce 20–60 correlated trades
per year. Portfolio equity curves at this scale are storytelling. The
analyses with real power (15k–61k per-base observations) consistently found
nothing. Every conclusion drawn from a few hundred trades — including the
encouraging one — should be held loosely.

## Provenance

- `d37eefe` seller-decay screener + learning + Kelly backtest
- `56aaed9` LPPL detector + bubble-dip backtest + ablations
- `4840f3f` tc widening (rejected) + once-rule (inert)
- `34f4347` short mirror, 2-of-5 gate, curve-timed bottom; tc reverted

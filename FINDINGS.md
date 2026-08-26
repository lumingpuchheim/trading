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

### Relative-strength ranking (tested 2026-08-25, rejected by audit)

Slot candidates ranked by trailing 126-day return instead of (votes, r2).
The sharpest dev/test split of the project: dev +222% (t 3.33, best ever
recorded) — test **−13%** (t 0.40, vs baseline +86%). Ranking by RS inside
an already-momentum-selected candidate pool concentrates the portfolio in
the most extended names, which is exactly what the 2020–22 momentum
unwinds punished. The external momentum prior does not transfer to
"momentum among momentum". Also the clearest demonstration yet that a
dev-best configuration can be an out-of-sample disaster.

### Cohort-ratio regime gate (tested 2026-08-25, theory falsified)

Claimed before testing: a top-decile-momentum cohort/SPY ratio with
positive 126-day slope would be OFF in 2008, the 2020 crash, 2021 and
2022. Measured: **ON 98% of 2008** and **ON 74% of 2022** — the claim was
wrong in half the regimes. Cause: the monthly-rebalanced top decile
rotates INTO whatever leads — defensives in 2008, energy in 2022 — so the
"leaders vs index" ratio rises during exactly the bears it was supposed
to flag. A performance-defined cohort tracks the momentum *style*, not
the speculative-growth *habitat*; the two coincide only in growth-led
regimes. (2020 crash: correctly OFF; 2021: only partially caught, 45–72%
ON.) Backtest: gate mildly hurts returns in both periods (dev t 2.45 vs
2.90; test 1.64 vs 1.87) while improving drawdowns (dev −26% vs −31%,
test −30% vs −47%, mostly the 2020 window). Not adopted. Mechanical
regime gating has now failed in four different formulations (market-dip
guard, breadth veto, U-shaped habitat gauge, cohort ratio).

### Democratic ensemble gate (tested 2026-08-25, rejected — and why voting can't fix it)

Four prior regime signals vote (market dip, habitat-dead, mania, cohort
slope); entries blocked at >= 2 of 4 hostile. Pre-declared, unscanned.
Verification vs known regimes: 2008 blocked only 11%, 2021 blocked 2%,
2022 blocked 32% — only the 2020 crash (79%) is caught. Backtest: worse
in both periods (dev t 2.46 vs 2.90, test 1.58 vs 1.87). Root cause:
voter errors are regime-specific and complementary in the wrong way —
each bad regime is visible to a DIFFERENT single indicator (2008 to the
market-dip voter, 2021 briefly to the mania voter), so a majority
requirement erases exactly the minority knowledge, while an any-vote OR
gate resurrects every voter's false positives and kills the recoveries.
With correlated regime-specific errors, no aggregation threshold works.
Fifth and final mechanical regime formulation; the avenue is closed with
this data.

### Flagged-cohort ratio (tested 2026-08-26 — the decisive regime kill)

Cohort = stocks flagged within the trailing 126 days (definitionally
speculative, cannot rotate into defensives); gate hostile when the
cohort/SPY ratio falls over 126 days. Stage-1 regime table: the best ever
— 100% of the 2020 crash, **91% of 2021 H2 (first detector to catch the
hidden top in seven attempts)**, 57% of 2022, instant reopening at the
2009 bottom, 14% FP in 2009–13. And the trade-level audit still kills it:
the trades it would block averaged **+5.8% (dev) / +4.9% (test)** versus
+2.4% / +3.2% for allowed ones — the gate blocks the BEST trades in both
periods. Cause: 'flagged cohort falling' is the gate's hostile signature
AND the strategy's food — dips in bubble stocks. At cohort level exactly
as at stock level, a dip and a collapse are the same observable at
decision time. Even a regime classifier that sees every bad regime
transfers negative value, because hostile states and profitable states
share their signature. Mechanical regime gating is closed — not for lack
of detectors, but because the discrimination the gate needs is the same
one the whole strategy is built on failing to make.

### Soft-vote sizing ensemble (2026-08-26 — first both-period-consistent overlay)

Size = 10% x (4 − hostile votes)/4 over {S1 200SMA, S3 vol, V3 mania, FC
flagged-cohort}. Returns slightly lower (dev +135% vs +154%, test +75% vs
+86%), maxDD much lower in both periods (−25% vs −31%; −36% vs −47%),
per-trade t higher in both (2.96/1.99 vs 2.90/1.87). Sizing degrades
gracefully where blocking failed catastrophically. Registered alongside
the frozen baseline; judged on post-2026 data.

### Hostile-time winners vs losers — indistinguishable (the bedrock result)

For all trades entered under >= 1 hostile voter (179 dev, 139 test):
static features (vote count, dip depth, stock votes/r2/RS/tc) are
identical between winners and losers in both periods. Every feature that
shows a gradient FLIPS SIGN between periods: hostility age (old-alarm
trades +4.9% dev, −2.6% test), FC slope depth, and — tested at the user's
suggestion — the simultaneous-SPY comparison rel_dip (stock's 20d
drawdown minus SPY's concurrent one): high-idiosyncratic dips are the
WORST tercile in dev (+1.2%/+1.9%) and the BEST in test (+8.6%/+8.5%).
Same for 5-day relative return. Conclusion: the label that separates a
buyable hostile-time dip from a fatal one is the regime type itself,
which no price-derived feature encodes stably. This justifies soft
sizing (optimal when affected trades are indistinguishable) and closes
the discrimination question at stock, cohort, and feature level.

### Monetary-policy conditioning (2026-08-26 — external candidate #1, falsified)

Hypothesis (pre-registered): the cross-period sign-flips are conditional
on Fed policy direction (label = sign of the most recent target change,
FRED, no parameters). Both predictions failed. Age gradient under easing:
~0 in dev, strongly NEGATIVE in test (old-hostility −1.9% vs young
+12.2%) — opposite of prediction; under hiking: ~0 in dev, POSITIVE in
test — also opposite. rel_dip still flips between periods WITHIN the
easing state (dev −3.5pp, test +7.1pp). Even topline performance by
policy reverses (dev: easing better; test: hiking better). The
best-matched external variable does not explain the instability; the
flips are period-specific beyond the policy label. (Caveats: n=47–191
per cell; crude last-move label.)

### Sector-based growth cohort (2026-08-26 — external candidate #3, the purest flip yet)

Fixed-membership cohort by GICS classification (IT + Comm Services +
Cons Discretionary + Health Care, 593 tickers, no rotation), equal-weight
vs SPY, 126d slope. It IS the 2021 detector: 84% of 2021 H2 hostile with
only 12% false positives in 2009-13. And the trade audit is the exact
mirror of every predecessor: in dev the gate finally WORKS (blocked
trades -0.3% vs allowed +4.7%) — the first favorable dev audit of any
gate — and in test it blocks the BEST trades (+6.0% blocked vs +2.0%
allowed), because equal-weight growth lagged the Mag7-driven cap-weighted
SPY through 2023-25, marking the AI boom hostile (63%). A gate that
passes dev and fails test: under our own protocol it would have been
adopted and would have failed live. The strongest single exhibit of
non-stationarity in the project.

### The 2020 blunders, decomposed (2026-08-26)

Baseline 2020: pre-crash late-bubble buys (5 trades, sum −0.53) — no
signal was hostile yet, ex-ante indistinguishable; crash-window buys
Feb 20–Mar 23 (15 trades, sum −1.45 ≈ −14.5% of equity at 10% sizing) —
THE blunder; gap-amplified stops (worst fill −24.6% vs 8% nominal);
rebound absence (only 7 entries Mar 24–Aug 31, +7.1% avg — flags need
months of new run-up to re-form, so the model missed most of the
strongest rally in a decade by construction).

Mitigations measured: soft-vote sizing cut the crash-window equity hit
roughly in half (−14.5% → −7.6%; 8 of 15 entries sized at 0.25x) at a
cost of ~10% of total profit per period — the only 2020 fix consistent
in both periods. Curve-guard alone (_g1, newly isolated): barely catches
the crash (sum −1.12 vs −1.45) and costs heavily in both periods (dev
+110% vs +154%, test +36% vs +86%) — in the earlier guards round the
market-dip gate, not the curve check, was doing the catching. Rejected.
Verdict: about half of 2020's crash damage is avoidable by sizing;
the rest (onset buys, gap slippage, rebound lag) is the strategy's
nature, not an implementation error.

**Running tally: every modification attempted on lppl_dip2 — vote gates
(1/3-of-5), short mirror, curve-timed entry, Kelly sizing, trailing/SMA
exits, tc shifts, crash guards, dip ceiling, breadth veto — has failed to
beat the baseline on both periods jointly.** The baseline is a local
optimum in every direction probed. The skeptical reading (favoured by the
sample sizes): it sits at the peak of the selection process that created
it, and its true edge remains unproven until post-2026-08-25 data rules.

### When the model works — the habitat (per-year analysis, corrected)

Per-year trade stats (results/lppl_winrate_by_year.csv) group cleanly:
profitable years are 2009–2013, 2016, 2023–2025 — always the first years
after a bear-market bottom, when speculative leadership re-forms. Losing
years come in three shapes with one cause: fast crash (2020: 14 entries
in two weeks, gap-amplified −11% losers), slow bear (2022: NOT a crash —
worst 20-day index move only −12%; losses arrived as a drip, 22 of 27
trades stopped), and a hidden bear in the growth names (2021: index +29%
yet the model lost — the speculative stocks themselves fell all year under
a rising index). The failure condition is therefore NOT the index: it is
falling prices across the speculative growth stocks themselves (nothing to
do with macro price levels), for which the index is a proxy that fails
exactly in 2021-type years. Index-level overlays (mechanical guards or
human macro judgment) watch the wrong object; cohort breadth — e.g. the
model's own daily flag/pre-screen count — is the relevant weather gauge,
noted here as an observation, not an adopted rule.

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

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

### Crash-halt gates (2026-08-26): down+volume vs vol-spike — first dev-neutral gate

User rule A (SPY day <= -2% on >= 1.5x volume; halt 10 days): catches the
2020 crash window perfectly (15/15 entries blocked — day-one speed is
real) but its blocked pile in dev averages +6.5%: high-volume panic dips
in 2011/2009-13 were the model's best food (the capitulation ambiguity).
Dev +103% vs +154%, test +35% vs +86%. Rejected.

Rule B (20d vol > trailing 756d 90th pct; entries halted while true):
good-year tax only 3-4%; blocked trades are worthless in BOTH periods
(dev -0.3%, test +0.9% avg). Backtest: dev IDENTICAL to baseline
(+154%, t 2.93 vs 2.90, maxDD -29 vs -31) and test BETTER (+102% vs
+86%, t 2.06, maxDD -42 vs -47). The first hard gate that is dev-neutral
and test-positive; it catches only 8/15 of the 2020 crash window (lag)
but what it blocks is junk in both decades. Registered as
candidate alongside softvote; usual selection-tax caveat (~20th variant).

### Liquidate-on-halt (2026-08-26, rejected — dominated)

Extending rule B to also force-sell all positions while the vol halt is
hostile: dev +123% vs +154% (entry-halt only), test +47% vs +102%.
89/317 dev and 60/259 test trades ended as forced 'halt' sales. In 2020
the forced exits landed at post-alarm panic prices, barely better than
letting the stops fire (year avg -2.5% vs -2.7%); in 2011-type spikes it
dumped positions that recovered. Its only gain (test maxDD -34% vs -42%)
is dominated by softvote_c3 (-35% maxDD at +79% return vs +47%). Stops
already provide fast emergency exits; forced liquidation only converts
recoverable positions into realised losses at spike lows.

### Sector P&L decomposition (2026-08-26 — knowledge, not a rule)

Dev winners: Consumer Discretionary (+2.78 summed return, 49 trades),
Staples, Communication (+16% avg on 10 trades); dev losers: Energy
(10% win rate), IT (38 trades, ZERO net). Test: **Information Technology
is +8.59 of the period's ~+9.5 total — 90% of all test-period trade
profit** (AI-era bubbles, 2023/2025 cells +1.9/+5.1); Financials, Health
Care, Consumer Discretionary negative. Sector rankings flip completely
across the split (dev's best sector negative in test; dev's zero sector
is test's everything) — the non-stationarity is fractal down to sectors.
Reading: a bubble-hunting model is definitionally a concentrated bet on
whichever sector hosts the era's bubble; sector diversification in this
book is illusory, and a per-sector cap would have destroyed the test
period. Use as monitoring knowledge (know what single bet the book
currently is), not as a filter.

### Greedy exit: acceleration-conditional tc extension (2026-08-26, rejected)

Post-exit audit first: stocks drift +3.0%/+4.8% in the 60 days after tc
exits (positive both periods) — but that is market-rate beta, not bubble
alpha, and recycled slots out-earn it (+0.10%/day vs +0.05%/day). The
conditional extension (hold past tc while the stock's 20d return exceeds
its own trailing 756d 90th percentile): extensions barely fire — at tc
most bubbles are in a stall, not a blow-off (SMCI's Dec-2023 tc had flat
momentum; the rule sells on schedule and still misses the January run) —
and the variant is mildly worse in BOTH periods (dev +129% t 2.62 vs
+154% t 2.90; test +78% t 1.81 vs +86% t 1.87), with avg winners DOWN.
The greed question is closed: the money after tc is ordinary drift, the
blow-offs are not identifiable at tc, and the capital is better recycled.

### Second-leg re-entry (2026-08-26, no-op — and why SMCI stays uncatchable)

User theory confirmed mechanically: after a tc exit the old bubble
pollutes the long windows, so second legs re-certify at only 1-of-5
(SMCI Dec 2023 - Apr 2024: fresh fits, R2 0.96, new tc — always 1 vote).
Baseline re-entries after tc exits are accordingly rare (5/140 dev,
4/85 test; the test four averaged +29%). The targeted fix — accept
1-of-5 for re-entry within 130 days of a tc exit — changes nothing:
12 unique test entries at −2.3% avg; totals within ±2pp of baseline.
SMCI itself was STILL not re-entered: its 1-vote evaluations were
sporadic (pre-screen gaps) and the second leg went vertical without a
4% dip inside a live flag window until the Feb-16 −20% break — i.e. the
next buyable dip was the collapse. A dip-buyer cannot board a rocket
that does not dip until it explodes. Not adopted; mechanism understood.

### Detector watchlist exemption (2026-08-26, REJECTED — dev collapses)

Motivated by the verified SMCI gap above: after the 2023-12-04 tc exit the
pre-screen went dark in the consolidation, leaving a five-week evaluation
hole exactly over the Jan 5–18 2024 dips at $29–32. Change tested: a ticker
with any votes>=1 evaluation in the trailing 126 trading days is evaluated
on every refit day regardless of the pre-screen (`lppl.watchlist_days`).

- **Mechanism verified, motivating trade still missed.** The prescreen-
  passed evaluations are row-identical (all 156,190 — the change is purely
  additive), SMCI's hole is closed (continuous 5-day evaluations, b1 covers
  the Jan 5–18 dips with fresh tc). But every second-leg evaluation still
  scores 1-of-5 (the old bubble pollutes the long windows — same mechanism
  as the leg2 test), so b2 never fires and the b2-gated strategies STILL
  cannot buy SMCI's January dips.
- **The "small watchlist" premise was wrong.** Votes>=1 exempt evaluations
  re-extend the watchlist, making it self-sustaining: +262,309 exempt
  evaluations (62.7% of all; detector runtime doubled to ~49 min), 90,790
  with votes>=1 and 37,069 with votes>=2. Universe votes>=2 evaluations
  went 16,733 → 53,802 (3.2x): post-run-up consolidations keep fitting the
  run-up still inside their windows. The pre-screen probe's "0.00% of
  rejected days qualify" holds for random rejected days, not for the
  selected watchlist ones. The exemption does not fill gaps — it triples
  the b2 flag set.
- **Backtest: dev collapses.** Baseline dev +59% (t 1.86, maxDD −46%) vs
  +154% (t 2.90, −31%); test +118% (t 2.05) vs +86% (t 1.87). volhalt_B
  and softvote_c3 are worse in BOTH periods (volhalt dev t 2.24 / test
  t 1.33 vs 2.93 / 2.06; softvote dev t 2.41 / test t 1.78 vs 2.99 / 2.10).
  Dev trades 295 → 196, avg invested 0.77 → 0.88: persistent b2 windows
  keep rolling tc forward, holds lengthen, slots stop recycling — the
  exemption quietly turned the dip-buyer into a longer-hold strategy.
- **Rejected by protocol** (dev selects; two of three variants also lose
  the test period). Cache reverted to the pre-screen-only flags (restore
  verified row-identical; the watchlist run is preserved in
  `data/lppl_flags_watchlist.parquet`, charts in
  `results/lppl_watchlist_{dev,test}.png`). `watchlist_days` set to 0.
  The baseline's better test number is the crash-guard mirage again: a
  rule shaped by staring at a test-period failure (SMCI) flatters test
  and is repriced by dev.

### Refit-while-held exit (2026-08-26, rejected — the greed test, deconfounded)

Why did SMCI sell on a boring, flat day? Because the 2023-12-04 exit
executed the 2023-08-10 evaluation's tc (+79 trading days) with zero
updates — the pre-screen was dark through the whole consolidation, so the
detector never re-examined a stock it was holding. A fresh fit dated the
sell day (from the preserved watchlist stream) said the opposite: votes 2,
R² 0.950, tc ≈ 167 trading days out — "hold".

Test (user request), isolating the exit half of the rejected watchlist
change: entries stay on the pre-screen-only flags; held positions are
refit every 5 days (live cost ≤ 10 tickers/day) and fresh votes>=2
evaluations roll the tc clock forward (same persistence semantics as the
entry flags). `simulate(tc_roll_key=...)` + `lppl_refit_exit.py`.

- On the motivating trade it is spectacular: SMCI held Apr 2023 → Jul
  2024, +633% instead of +172%.
- On the book it fails in both periods: dev +70% (t 2.07, maxDD −43%) vs
  +154% (t 2.90, −31%); test +16% (t 0.71, maxDD −59%) vs +86% (t 1.87,
  −47%). Win rate 34% → 20% (test) while the avg winner doubles (+31% →
  +59%): the exit distribution becomes a lottery — one +633% SMCI against
  a book of extended losers.
- Mechanism, the STX pathology now measured at scale: tc exits collapse
  (dev 140 → 63, test 85 → 28) because fits made during declines
  re-explain the decline and push tc out, so once refits feed the clock
  it almost never fires; extended positions round-trip to the entry-price
  stop or decay while blocking slots (trades 244 → 173).
- Third confirmation of the exit law: an exit that listens to the fit is
  worse than the deaf clock in BOTH directions — flag-death (sell when
  the fit goes quiet) and refit-exit (hold while the fit keeps talking)
  both lose to executing a stale forecast on schedule. The tc estimate
  carries months-scale information at entry time and none at exit time.
  The regret about SMCI's exit is survivorship of one lucky path.

### Expected-win audit, Route 1 (2026-08-26 — no usable payoff model; a leak demo)

User question: the strategy measures odds (is this a bubble?) but never
payoff (how much is left?). Pre-registered audit (`lppl_payoff.py`): 1,798
candidate-day pseudo-trades (1,027 dev / 771 test — every liquid b2∧dip∧tc
day, non-overlapping per ticker, real trade mechanics, no slot
competition), 15 decision-time features, dev-fixed quintile edges,
survival = |dev spread| ≥ 1pp with same-signed test spread; ridge on
survivors. Pre-registered directions: young flag_age +, long tc_runway +,
low persist_depth +.

- Stage 1: 7 of 15 features nominally survive — but ~half of a 15-feature
  field passing a sign-agreement test is close to the noise expectation,
  and most quintile profiles are non-monotone (spreads made at the
  endpoints). Prereg scorecard: flag_age and persist_depth pass on
  spreads with rank correlations ≈ 0 (weak); tc_runway fails with the
  OPPOSITE sign in both periods — short-dated fits pay better (dev rho
  −0.11, test −0.13, the only replicated structure in the table), and its
  cousin p_n agrees (small windows better; corr 0.47). Direction of every
  surviving gradient: less certified / younger / shorter / smaller →
  higher payoff — consistent with the formation-phase lesson and mildly
  indicting the (votes desc, r2 desc) slot ranking, whose payoff
  gradients are both negative in dev.
- **The leak demonstration (keep this one).** The prereg survival rule
  checks the test spread's sign — one bit of test information leaking
  into stage-2 feature selection. Contaminated ridge: test rho +0.094,
  top-decile +4.1% — looks like the first stable payoff model. Selecting
  on dev alone (adds p_sigma, one feature): test rho collapses to
  **+0.026**, top-decile +2.3% vs the +1.4% sample average, quintiles
  non-monotone. One bit of leakage inflated the out-of-sample rank
  correlation almost fourfold. The clean number is the +0.026.
- Verdict: Route 1 concludes NEGATIVE — no expected-win model worth
  sizing on (composite edge ~1pp per trade against 16–22% per-trade
  noise). Stage 3 (sizing overlay) not run, per the pre-declared bar.
  The short-runway/small-window payoff tilt is recorded as knowledge and
  as the single candidate for a future pre-registered test on post-2026
  data; testing it now, after seeing this table, would be the RS trap.

**Running tally: every modification attempted on lppl_dip2 — vote gates
(1/3-of-5), short mirror, curve-timed entry, Kelly sizing, trailing/SMA
exits, tc shifts, crash guards, dip ceiling, breadth veto, detector
watchlist exemption, refit-while-held exit — has failed to beat the
baseline on both periods jointly.** The baseline is a local
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

### The anatomy of being right (2026-08-26 descriptive study, no changes)

`lppl_study.py` — six-section diagnosis on cached data. Findings, in
order of how firmly the data supports them:

1. **The model is a tail harvester.** Median trade is NEGATIVE in both
   periods (−6.8% dev, −8.3% test); win rates 42%/34%. The top 10% of
   trades carry 60%/77% of gross wins; in test the top FIVE trades carry
   a third. Stops average −10.1%/−11.0% (8% nominal plus gap slippage;
   7/9 fills below −15%), median loser gone in 20–25 days; tc exits
   average +18.7%/+30.0% over ~4.5-month holds. "Right" means catching a
   handful of monsters per period; everything else pays the entry fee.
2. **The home regime is consistent across both periods** (the cleanest
   conditional pattern in the project): entries while SPY > 200d SMA
   with 20d vol below its 756d 90th pct average +4.5%/+4.8% per trade
   (~2/3 of all trades, ~all of the profit); every other market state
   averages ≈ 0. Matches volhalt_B/S1 from the gating side.
3. **The flag is an acceleration marker, not a top marker.** Median
   episode is a 10-trading-day flicker (2 evaluations). The actual price
   peak comes a median 87/75 trading days after episode start, and
   85%/82% of peaks occur AFTER the flag has already lapsed.
4. **tc is an unbiased but coarse clock**: median (tc estimate − actual
   peak) = +7/+13 trading days, IQR roughly ±50. The exit clock works
   because its median is honest and its patience window matches its
   ±2.5-month noise — quantifying the earlier "months-scale" claim.
5. **Certification carries thin cross-sectional content** (controlled
   test, ~56k/52k control days): vs 0-vote accelerating-run-up controls,
   flagged days show ~zero median 60/120d excess-vs-SPY return in dev,
   and monotonically WORSE outcomes with votes in test (3–5 votes:
   −4.5% median 60d excess, 120d crash rate 17.9% vs 14.3%). The LPPL
   curve does not predict returns; the strategy's edge lives in trade
   construction (dip entry, asymmetric exits, tc patience) inside the
   momentum habitat. Caveat: dip_only (no gate — but also no tc clock)
   collapsed in dev, so the gate's candidate selection + clock supply is
   load-bearing there, just not separable from the exit it enables.
6. **Breadth level is NOT the weather gauge.** High-flag years split
   evenly (2013, 2024 good; 2014, 2017, 2018, 2021 bad) and low-flag
   years contain both the best (2009, 2023) and the worst (2008, 2020,
   2022) years. The earlier cohort-breadth observation fails in its
   simplest (level) form; trade count is decoupled from breadth by the
   slot cap. Episode-level crash rates (180d post-peak drawdown ≤ −30%:
   29% dev, 45% test) are description only — no matched episode control.

Non-result: entry order within episodes cannot be studied — 294/295 dev
trades are first entries (cooldown + tc exits preclude re-entry, as the
once-rule already showed).

External data worth acquiring, in priority order: (1) point-in-time
index membership — survivorship bias touches every number here; (2)
earnings-announcement dates — are the −15%+ stop fills earnings nights?;
(3) short interest / borrow fees — crowding as episode-crash
discriminator; (4) options IV/skew — speculative-cohort health, the 2021
blind spot; (5) real-rate levels (weak prior after the policy-label
failure).

### Earnings adjacency of stops (2026-08-26 — external data test #1)

`lppl_earnings.py`; yfinance earnings dates for all 429 traded tickers
(34,656 reports, dev coverage 96%, test 100%; dates imperfect — levels
carry uncertainty, ratios are the evidence). Control = tc exits, whose
timing is clock-driven: their earnings-adjacency (dev 5.2%, ~= the 4.8%
theoretical base rate; test 2.4%) estimates the random-window rate.

- **Ordinary stops concentrate around earnings at ~2.5x the base rate in
  both periods** (dev 13.0% vs 5.2%; test 6.1% vs 2.4%). In dev,
  earnings-adjacent stops also lose 3pp more (−12.6% vs −9.6%); in test
  there is no slippage difference (−11.1% vs −11.0%). Total drag is
  modest: roughly 0.6 summed return units across 12 dev years.
- **The catastrophic fills are NOT an earnings story.** Dev: 3 of 5
  stops ≤ −15% were earnings nights (WAT −27%, ARCB −25%, OLED −17%).
  Test: 0 of 9 — the list is the 2020 crash window (TSLA, WING, NEE,
  MRNA), idiosyncratic news (PTGX −64%, biotech), and 2021/24/25 breaks.
  The worst losses come from the market and from non-earnings news, not
  the calendar — consistent with the 2020 decomposition.
- Winners sit through ~1.5–1.9 reports on average, losers ~0.5–0.7 —
  mechanical (4.5-month winning holds span ~1.5 quarters), but it means
  the winner tail REQUIRES holding through earnings; an
  avoid-earnings rule would amputate the tail that pays for everything.
  Recorded as knowledge; no rule proposed or tested.

### Green-light entry gate (2026-08-26 — registered with maximal post-hoc caveat)

User-requested: the anatomy study's home regime as a hard entry gate
(SPY > 200d SMA AND 20d vol <= its 756d 90th pct), entry-only, decomposed
against its halves (`lppl_greenlight.py`).

- dev: green +162% (t 3.18, maxDD −26%, win rate 45.4%) vs baseline
  +154% (t 2.90, −31%) — the best dev configuration recorded that also
  survives its test audit (the breadth veto's t 3.14 did not).
  trend_only is nearly identical (+159%, t 3.12).
- test: green +82% (t 1.83, maxDD −38%) vs baseline +86% (t 1.87, −47%)
  — mildly lower return, meaningfully lower drawdown, no collapse. In
  test, green degenerates to trend_only exactly (identical 218 trades);
  the calm leg adds nothing once trend is in. calm_only remains the best
  test performer (+102%, t 2.06): trend's marginal blocks in test are
  the spring-2020 rebound entries (SPY below its 200d SMA into June
  2020) — the smoother ride is bought with exactly those trades.
- Verdict: passes the adoption letter (dev improves, test intact), but
  the gate was constructed the same day from the both-period descriptive
  table — the most post-hoc rule tested here. Registered as a third
  candidate alongside volhalt_B (better test, neutral dev) and
  softvote_c3 (drawdown control); the frozen baseline stands and
  post-2026 data judges.

### Pre-earnings ejector seat (2026-08-26, rejected — the scan was noise)

User idea: a report lands on the coming gap night and the position is
down more than X% → sell at the pre-report close instead of risking the
gap (stops don't hold across gaps). X scanned on dev over {0,2,4,6}%,
'sell-always' as reference; `simulate(earn_exit=...)`, same-close fill.

- Dev surface is jagged, not monotone: x0 +179% (t 3.11) but x2 +117%,
  x4 +132%, x6 +127% — all BELOW baseline (+154%) except the single x0
  cell. A real mechanism would shade gradually with depth; one popping
  cell is noise. 'Always' confirms the tail amputation prediction:
  +61% (t 2.00) with 241 forced exits.
- Test audit of the dev-best (x0): +44% (t 1.37) vs baseline +86%
  (t 1.87) — regression to the mean, exactly like the tc-shift scan.
  The 29 ejections averaged only −3.1% (mild saves), but win rate fell
  34.4% → 30.5% and the winner tail thinned: ejected red positions
  include the ones whose report was the up-gap that made them winners.
  In the AI-era test period, earnings beats were the fuel.
- Deeper lesson, consistent with the exit law: in a lottery-ticket
  payoff structure, ANY rule that surrenders a ticket before a binary
  event trades an ~3% saving against a fat right tail. The 8% stop
  (post-gap) remains the only earnings defense that survives both
  periods. Earnings knowledge stays useful only as entry-timing
  discipline (don't open fresh positions into a report), which the
  backtest's 1-day fill lag makes untestable here.

### Win-probability model (2026-08-26 — the first OOS-valid model, and why it still doesn't pay)

User question: expected-win failed; can the features at least learn
P(win)? Ridge-logistic on the 1,798 pseudo-trades (`lppl_winprob.py`),
lambda by walk-forward fold AUC, one test evaluation. Pre-registered
trap: E[ret|features] is known flat OOS, so if P(win) is learnable, win
SIZE must anti-compensate.

- **P(win) is learnable — the first stable OOS result in the project.**
  Test AUC 0.586 (dev in-sample 0.613); win rate rises monotonically
  across dev-edge quintiles OOS: 28.5% → 29.0% → 38.4% → 40.5% → 47.6%.
  Direction (log-odds per sd): calmer stock (vol20 −0.26), dip shared
  with the market rather than idiosyncratic (rel_dip −0.23), deeper dip
  (+0.21), small-window short-dated fits (p_n −0.14, tc_runway −0.13).
  Mechanically sensible: these predict "survives to the tc clock
  without hitting −8%".
- **And the trap sprang exactly as registered: win size anti-compensates
  monotonically in BOTH periods.** Mean winner size by the same
  quintiles: dev 31.4% → 12.0%, test 30.6% → 9.7%. Mean RETURN per
  quintile: no gradient in either period (dev +1.6..+4.4 unordered;
  test +1.0/−0.1/+1.9/+4.5/+0.4). With the stop fixing the loss side,
  E[ret] ≈ p·W − (1−p)·10%, and the data says p·W ≈ const: probability
  and payoff are inversely priced inside the candidate pool. The model
  predicts a trade's STYLE (steady grinder vs lottery ticket), not its
  value.
- Consequence: any p-based sizing/filter tilts toward grinders and cuts
  the tail that carries the book — the ejector-seat lesson in model
  form. Not used for trading. Also explains the test era's lower win
  rate: its trades skew into the low-p buckets (267 vs 82 in the
  extremes) — the AI-era book was structurally more lottery-like.
- Caveats: single pre-registered test evaluation; correlated trades
  overstate nominal significance; pseudo-trades are slot-free.

## Steady Giants (2026-08-26 — separate system, pre-registered in STEADY_GIANTS_SPEC.md)

Buffett-style steady compounders: monthly qualification (liquid, lowest
trailing-3y-vol tercile, 5y log-price regression with positive slope and
R² above threshold, 5y unbroken dividends with no >20% cut, EPS present),
buys only on the market green light and only when trailing P/E is not
above the stock's own historic p90, sells only on the own-history P/E
ceiling / LPPL 2-of-5 certification / dividend cut / delisting. 8 slots,
winners never trimmed, idle cash earns 3M T-bills. P/E uses reconstructed
nominal prices; equity curves use the total-return series (dividends
already in, never double-counted).

**Survivorship caveat, load-bearing as always: the universe is today's
S&P 1500 members, so a hold-forever system is being graded with the
answer key — every number below is an upper bound.** The 200
random-qualifier controls (same rules, random picks among qualifiers)
are the partial antidote: they share the same biased universe, so
beating them measures selection skill, not survivorship.

Qualifier sanity: 201,766 ticker-months; qualifier-months 34,059 /
29,080 / 21,873 at R² ≥ 0.6 / 0.7 / 0.8 — roughly 110–225 per month
from 2012 on, a plausible band. First possible decision is 2010-02 (the
5y regression window only fills then; data starts 2005), so the "dev"
curve sits in T-bills through 2007–09 — **the dev maxDD below is
flattered by having slept through 2008.**

Dev 3×3 grid (selection by MAR, declared before running): R² ≥ 0.7 with
sell at own-history **p90** won (MAR 0.96); pe_max (never sell on
valuation) was the worst column (MAR 0.75–0.76) — taking profits at the
own-history ceiling beat holding forever, even in the dev bull market.

| | total | CAGR | maxDD | MAR | buys | avg hold | vs controls |
|---|---|---|---|---|---|---|---|
| dev 2007–18 | +275.5% | +11.7% | −12.2% | 0.96 | 66 | 0.94y | beats 80.0% (median +242%) |
| test 2019–26 | +125.8% | +11.3% | −24.2% | 0.47 | 53 | 0.67y | beats 88.5% (median +93%) |

Context: T-bills +10.0% dev / +23.6% test; SPY +126% dev (maxDD −55%) /
+242% test (maxDD −34%). SPY is context, never the target: the system
beat it in dev, lost to it in test.

**Verdict: PASS on the pre-registered criteria** — CAGR far above
T-bills with maxDD well under the universe's in both periods, and above
the 75%-of-controls bar in both (80.0% and 88.5%). Held loosely per the
statistical-reality section: the dev margin over the control bar is 5
points of a 200-draw distribution, and both periods lean on the same
survivorship-biased universe. What the controls do establish: ranking
by straightest-line R² picks better steady giants than picking steady
giants at random.

Sell-rule contributions: pe_ceiling did the volume (45 dev / 37 test
sells, mean +21% / +13% per trade), the LPPL certification sell was
rare but the most profitable exit (10 dev / 8 test, mean **+26.5% /
+18.0%**) — selling a boring giant into its mania worked; div_cut fired
3 times (dev, mean +3%). No delistings. One honest deviation from the
spec's temperament: expected holding was "years", realised average is
0.7–0.9y, because the p90 ceiling churns — the system is a valuation
cycler in steady names, not a 20-year holder.

### The KO answer (and PG/JNJ/COST)

**The system never bought KO — or PG, JNJ, COST — in either period**,
while holding exactly their genre (WEC, XEL, MCD, GIS, CHD, RSG, CTAS,
AJG, ICE...). KO qualified in 165 of 199 decision months, and three
blockers stacked:

| | qualified | own-P/E too high | red light | slots full | outranked | best rank |
|---|---|---|---|---|---|---|
| KO dev | 73 | 39 | 6 | 28 | 0 | 8 (2013-08) |
| KO test | 92 | 33 | 11 | 43 | 5 | 29 (2026-02) |
| PG dev/test | 56 / 71 | 34 / 28 | 4 / 11 | 18 / 30 | 1 / 2 | 40 / 26 |
| JNJ dev/test | 66 / 62 | 34 / 8 | 9 / 16 | 22 / 33 | 1 / 5 | 32 / 13 |
| COST dev/test | 21 / 24 | 7 / 23 | 2 / 0 | 12 / 1 | 0 / 0 | 5 / 4 |

In the whole test period there were exactly 5 months with a green
light, a free slot, and KO eligible — and KO's R² (0.76–0.84) ranked
37th–92nd against a slot cutoff of 0.93–0.97. On 2019-03-01, the first
test decision with all 8 slots free, KO ranked 92 of 145 eligibles. So
the answer to "would we have caught the KO rise Berkshire sat out" is
**no**: through the 2019+ rise KO's P/E sat above its own p90 in a third
of months (the do-not-buy-what-you'd-flag filter), and whenever it was
buyable, dozens of straighter compounders outranked it for 8 slots. KO
is a steady giant; it is never among the *straightest* 8. COST is the
sharpest version: nearly always eligible-quality (best rank 4–5), but
P/E-blocked in 23 of its 24 test qualifying months.

Charts: `results/giants_dev.png`, `results/giants_test.png`; trades in
`results/giants_{dev,test}_trades.csv`, summary `results/giants_summary.csv`.

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

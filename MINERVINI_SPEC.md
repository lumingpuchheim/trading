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

## 10. The four missing pillars, specified (v4 candidates — NOT implemented)

Written 2026-08-27 after the paired exit measurement. Plain language
first, mechanics second. Nothing here is built; every constant is a
proposal awaiting approval, and one caveat governs all of it: **both
backtest periods have been seen. Any v4 number computed on this history
is post-hoc by construction. The honest judge for these changes is the
simulator's forward paper ledger, not another backtest.**

Ranked by measured importance:

### 10.1 Exit philosophy — hold through the shakeout (biggest measured gap)

**Plain language.** Today the system sells whenever the price wobbles
below an average line. The measurement says that is the single biggest
destroyer: the same entries, simply left alone for 60 days, win 60-76%
of the time; through our exits they win 19-28%. Minervini does the
opposite of us: he decides in advance how much he is willing to lose
(the stop), and between the stop and his profit targets he deliberately
sits through noise — including the scary dip right after buying.

**His own words for the mechanism we lack — "Tennis Ball Action"**
(raised by the user; verified from his posts, so this is primary
source): *"Stocks under strong institutional accumulation almost always
find support during the first few pullbacks... Tennis ball action will
generally occur after two to five days or even one to two weeks of
pullback, followed by the stock bouncing back up again, taking out the
most recent highs... Does it come bouncing back like a tennis ball or
splat like an egg? The best stocks rebound the fastest. Once I buy a
stock, if it displays tennis ball action, I will probably hold it
longer."* In plain terms: a fresh buy that dips for up to two weeks and
then jumps back to new highs has just PROVEN there are big buyers
underneath — that dip is a reason to hold, not to sell. A stock that
sags and cannot recover is the one to get rid of. Our current exits
sell the tennis balls at the bottom of their first bounce. He also
reads post-entry health from: more up days than down days, more strong
closes than weak ones, bigger volume on up days than down days
("squat" and "reversal recovery" are his names for a stalled breakout
that undercuts briefly and recovers within 1-4 days — healthy).

**Proposed mechanics (constants frozen on approval):**
- Keep the 8% stop. It is his number and it caps every disaster.
- DELETE the SMA50 exit for the first 15 trading days after entry.
  During that window only the stop can sell. This is the tennis-ball
  tolerance: dips of days-to-two-weeks are expected, not punished.
- Tennis-ball test at day 15: if the stock has taken out its
  post-entry high at any point after a pullback, it is a tennis ball —
  switch to trend mode (decisive SMA50 break as in v3, breakeven-at-2R
  as in v3). If it sits below the entry price and has never recovered
  its post-entry high, it is an egg — sell at the next open.
- Sell into strength: sell HALF at +20% (his stated winner range is
  20-30%), let the rest run under the trend rule.

### 10.2 Selection — rank the candidates, stop taking them alphabetically

**Plain language.** He passes on most qualifying setups; we take every
one in alphabetical order until the slots fill, which is a coin-flip
disguised as a rule. The controls beating us say our entry choice adds
negative value against random. What he selects FOR is strength in the
precise sense the user read about: the stocks that fall least when the
market falls, that bounce first when it turns, whose
performance-vs-index line hits new highs BEFORE the price does
("anticipating leadership" — institutions accumulating quietly), with
RS ratings 90+ at the start of big moves, in leading industry groups.

**Proposed mechanics, all computable from cached data except the last:**
- rank candidates by RS percentile (we gate at top 30%; rank inside it,
  prefer the 90th+ percentile),
- holds-up-when-weak score: the stock's average return on the SPY
  down-days inside its own base (higher = leader),
- RS-line-new-high flag: stock/SPY ratio at a 52-week high on the setup
  day while price is still below the pivot,
- industry-group strength needs a sector table we do not cache —
  buildable, small data acquisition.
- Slots fill from the top of this ranking. (Declared: an RS ranking
  failed OOS once in the LPPL system as slot priority; this is a
  different system and the claim will be judged on the forward ledger,
  not asserted.)

### 10.3 Universe — fish where he fishes

**Plain language.** Our list is today's S&P 1500: big, established
survivors. His hunting ground is young small/mid caps and recent IPOs —
names that join an index years AFTER the move he trades, or never. No
rule change fixes this; it needs point-in-time constituent and IPO
data, which costs money. Until then every result here carries this
asterisk.

### 10.4 Sizing and progressive exposure — LAST, and only after the sign flips

**Plain language.** Betting bigger on a losing average trade just loses
faster, so this pillar is meaningless until 10.1-10.3 produce a
positive average on the forward ledger. Once (if) they do, his
structure: risk 1.25-2.5% of the account per trade, concentrate in
20-25% positions when trades are working, pyramid into winners, cut
size in half after a losing streak — exposure follows results, in both
directions.

## 11. v5 — the full entry repertoire (pre-registered 2026-08-27)

User instruction: the repertoire was never specified; specify and build
all of it. Constants frozen here before any run. Post-hoc caveat as in
sections 9-10: both periods are burned; numbers are behaviour
description. All entries share the v4 context: trend template,
liquidity, market light, earnings blackout, MOC fill, tennis-ball
exits, strength ranking, shared slots and cooldown.

1. **Pivot breakout** — built (v3/v4). Unchanged.
2. **Cheat (early entry inside the base).** Yesterday was a setup day
   (valid base, price under the pivot P). The pause ceiling C = the
   highest close of the prior 10 days, required strictly below P, with
   the last 5 closes in a <= 5% range (the pause). Trigger: today's
   close crosses C on >= 1.5x the 50-day volume, close <= 1.05 x C.
   The low-cheat/cheat/handle distinction by height in the base is NOT
   separately mechanised — one `cheat` label, declared.
3. **Pullback to the 20-day line.** The stock printed a 60-day-high
   close within the last 10 days; today's low touches the SMA20
   (low <= 1.005 x SMA20) and the close holds it (close >= SMA20).
   Quiet volume acceptable — pullbacks are quiet by nature. Label
   `pullback`.
4. **Power play.** Some day p in the last 10-40 days closed at >= 2.0x
   its close 40 trading days earlier (the doubling). Since then: a flag
   of 10-30 days whose lowest close stays >= 0.80 x the flag high H.
   Trigger: today's close crosses H on >= 1.5x volume. No base, no
   contraction, no dry-up required — velocity is the signal. Label
   `power_play`.

## 12. v6 — money engine and market engine — REVERTED (decision 2026-08-27)

**Decision, on user instruction, recorded permanently:** risk-based
sizing, pyramiding and progressive exposure are REVERTED and are not
part of any standing configuration. Reasoning: these levers are
Minervini's *judgement* expressed as position size — he presses because
he understands WHY the tape is paying, and pulls back because he senses
why it is not. The system has no such judgement. A rulebook imitation of
the behaviour without its cause is pretending to have judgement we do
not have, and the measurement agrees: v6 degraded both periods
(+107/+147 -> +44/+53), deepened drawdowns (-25/-23 -> -41/-31), and
halved size exactly at bottoms. The market dimmer falls with them, same
reasoning. Flat 10% equal-weight slots and the binary light stay — a
size rule that CLAIMS nothing is the only honest one for a judgement-
free system. `--v6` remains runnable solely to reproduce the recorded
negative result. Do not re-propose these without new judgement-bearing
inputs, not new curves.

(Original pre-registration below, kept for provenance.)

Specifies everything still missing that is not data-limited. 12.1 and
12.2 are BUILT (--v6, on top of v5); 12.3 and 12.4 are specified only.
Post-hoc caveat unchanged and compounding: each layer written after the
history was seen. The forward ledger is the only judge.

### 12.1 Money engine (BUILT)

- **Risk-based sizing**: each trade risks `risk_per_trade` = 1.25% of
  equity; with the 8% stop that is a 15.6% position, capped at 20%.
- **Pyramiding**: when a position first reaches 2R (+16%), add 50% of
  the original share count at the next open, once, cash permitting.
- **Progressive exposure**: if the sum of the last 5 closed trade
  returns is negative, all new positions are cut to half size until the
  tape pays again.
- Staged selling and breakeven stay as built in v4/v3.

### 12.2 Market engine (BUILT)

The binary light becomes a four-point dimmer, one point each for:
SPY > 200d SMA; SPY > 50d SMA; 20d vol <= its 756d 90th percentile;
SPY 20d return > 0. Entries need score >= 2; every new position is
scaled by score/4 (50%, 75%, 100%).

### 12.5 Exit refinements — ablation (pre-registered 2026-08-27, POST-HOC)

Four sourced exit mechanisms, each toggleable ALONE so the contribution
of every method is visible, plus all-on (v7). Frozen: E1 climax — close
>= 1.25x entry AND a single day of >= +5% -> sell ALL next open. E2
volume-weighted weakness — the SMA50 exit fires only on above-average
volume (quiet drift below the line is tolerated; replaces the 1%-depth
alternative). E3 re-entry — cooldown 5 days after egg/sma/breakeven/
climax exits (20 stays after stops). E4 aging stop — from day 60 the
stop rises to the entry price. Runs: v5, +E1, +E2, +E3, +E4, v7(all),
both periods. Post-hoc; forward ledger judges.

### 12.5 decision (2026-08-27): KEEP E3 only — standing configuration "v5r"

Ablation verdicts, recorded permanently:
- **E1 climax sell-all: REJECTED.** Hurts both periods (-21/-52 pts).
  The distribution's whole edge lives in the +50-100% right tail; a rule
  that sells everything at +25% amputates precisely that. Do not
  re-propose profit caps of any form on this system.
- **E2 volume-weighted weakness: REJECTED.** Dev +71 pts, test -28 pts —
  a regime bet, not an improvement; fails the both-periods bar.
- **E4 aging stop: REMOVED, dead code.** The 2R-breakeven rule always
  fires first; in 1,467 trades it never triggered once.
- **E3 fast re-entry: KEPT.** Dev +41 pts, test neutral, 97th control
  percentile in both. It repairs a measured leak (63% of day-15 "eggs"
  recover) without touching anything else.

**Bet size: FLAT 10% slots, final (user decision 2026-08-27).** The
scan (5/10/15/20/25/33%) showed 20% worse than 10% in BOTH periods and a
non-monotonic, variance-driven pattern overall; no size optimum exists in
this data. 10% at an 8% stop is ~0.8% account risk per trade — below
Minervini's own 1.25% floor, the correct posture for a system without
his entry precision or judgement.

**Standing configuration `v5r` = --v5 --e3** (repertoire, tennis-ball
exits, strength ranking, flat 10% slots, binary light, 5-day re-entry
after non-stop exits).

**The bets we take (v5r — corrected twice, this is the final form).**
Two earlier versions of this table were wrong. The first averaged trade
ROWS of unequal size. The second split them into "full-size" and
"half-size" buckets and described the full-size bucket as losing — which
reversed the causality, because **the buckets are defined by the outcome,
not by the size**:

- every bet is entered at 10%;
- if it later reaches +20%, the strength rule sells half, so that
  position emits TWO 5% rows (the banked half, and the rider's eventual
  exit);
- if it never reaches +20%, it exits whole as ONE 10% row.

So "full-size rows" is simply the set of trades that never got to +20%,
which excludes every large winner by construction, and "half-size rows"
is the set that did. Both obey the SAME exits (stop, egg, decisive SMA,
breakeven); the only extra rule on the split ones is the +20% half-sale
that created them. Highest full-size row: +15.5% — as expected, since
crossing +20% is precisely what moves a position out of that bucket.

The honest unit is one POSITION (all its rows, share-weighted):

| per 10% position | dev | test |
|---|---|---|
| positions | 703 (59/yr) | 531 (69/yr) |
| **mean** | **+1.26%** | **+2.03%** |
| **geometric mean** | **+0.57%** | **+1.08%** |
| median | -1.77% | -2.18% |
| P(win) | 35% | 36% |
| reached +20% (split) | 121 (17%), mean +23.79% | 107 (20%), mean +26.17% |
| never did | 582, mean -3.43% | 424, mean -4.06% |

Read it as: **two bets in three lose, the median bet loses ~2%, and the
whole system rests on the 17-20% of positions that reach +20%.** Account
arithmetic closes with these inputs: sum(weight x return) = +7.4%/yr dev
and +14.1%/yr test against realized CAGR 6.3% and 12.5%. Every
qualifying bet must still be taken — which bet becomes the 1-in-5 is not
identifiable in advance. Known failure mode (2026): when leadership
narrows to mega-caps that never base, the repertoire deploys fully into
rotation names and loses while the index rises (-23.9% vs +12.8% YTD).

**Standing configuration `v5r` = --v5 --e3** (repertoire, tennis-ball
exits, strength ranking, flat 10% slots, binary light, 5-day re-entry
after non-stop exits).

**The bets we take (v5r — CORRECTED 2026-08-27, see below):** the
earlier figures here averaged trade ROWS of unequal size and were wrong.
The strength rule sells HALF a position at +20%, so every such row is a
5% bet that exists only because the trade won; averaging it beside
full 10% rows inflates the mean. Dollar-weighted and de-duplicated:

| | dev | test |
|---|---|---|
| distinct positions | 703 (59/yr) | 531 (69/yr) |
| dollar-weighted mean per row | **+1.26%** | **+2.03%** |
| full-size (10%) rows | 582, mean **-3.43%** | 424, mean **-4.06%** |
| half-size (5%) rows, winners by construction | 242, mean +23.79% | 214, mean +26.17% |
| geometric mean per bet (one euro cycled through) | +3.52% | +4.56% |
| P(win) / median row | 45% / -0.70% | 46% / -0.52% |

Read it as: **a full-size bet loses ~3-4% on average**; the system earns
only through the halves banked at +20% and the riders left after them.
Account arithmetic closes with these inputs: sum(weight x return) =
+7.4%/yr dev and +14.1%/yr test against realized CAGR 6.3% and 12.5%,
the residual being compounding and sequencing. Every qualifying bet must
still be taken — the winners are not identifiable in advance. Known
failure mode (2026): when leadership narrows to mega-caps that never
base, the repertoire deploys fully into rotation names and loses while
the index rises (-23.9% vs +12.8% YTD).

**Standing configuration `v5r` = --v5 --e3** (repertoire, tennis-ball
exits, strength ranking, flat 10% slots, binary light, 5-day re-entry
after non-stop exits).

### 12.3 Craft layer (specified, NOT built)

Good-close count (close in the upper half of the day's range) and
up-day/down-day volume ratio as additional ranking features; re-entry
after a shakeout: cooldown drops 20 -> 5 days for names exited via
`egg` or `sma` (never for stops); squat/reversal-recovery handling
beyond the tennis-ball window.

### 12.4 Judgment (irreducible, recorded as such)

His discretionary veto — story, group, tape feel — is approximated by
the ranking and cannot be built. Whatever gap remains after 12.1-12.3
is attributed there, and no further mechanical layer should claim it.

## 13. Momentum-conditioned selling (specified 2026-08-27; BUILT as `--v9` 2026-08-28, see BUILD STATUS)

The user's observation, confirmed against sources: our +20% half-sale is
UNCONDITIONAL, but the source method conditions profit-taking on how the
stock got there. Three sourced facts:

1. He takes partials into strength around 2-3R and trails the rest via a
   moving average or swing-low structure — roughly our rule.
2. **During a "power move" he does NOT take profits** — the O'Neil-school
   rule he trades alongside: a stock up 20%+ within ~3 weeks of breakout
   is held ~8 weeks, because only overwhelming institutional demand
   moves a stock that fast, and those are the 100%+ candidates.
3. The exception inside the exception: **climax topping signs** end the
   hold-through — the largest up-day of the whole run, an exhaustion
   gap, the largest up-day followed at once by the largest down-day,
   after an extended advance (his MSTR example: +100% in two weeks off
   a 4th-stage base).

**The difference, specified against our current rule:**

| situation | v5r today | source method |
|---|---|---|
| reaches +20% SLOWLY (> 15 trading days) | sell half | sell half — same |
| reaches +20% FAST (<= 15 trading days) | sell half | **HOLD WHOLE ~8 weeks** — this is the jackpot cohort; selling half here amputates exactly the 1-in-5 tail the system lives on |
| parabolic while extended | nothing — rides until the trend break | **sell INTO the climax**: partial on the largest up-day of the run once well extended |

Proposed frozen mechanisation (awaiting approval):
- **Velocity exemption**: close >= 1.20 x entry within 15 trading days of
  entry -> NO partial; hold the full position for 40 trading days from
  entry (stop / breakeven / climax still active), then normal rules.
- **Climax partial**: while >= +30% above entry, on a day whose gain is
  both >= +5% and the largest single-day gain since entry -> sell HALF
  at that close. (Distinct from rejected E1, which sold EVERYTHING at a
  mere +25% on any +5% day and amputated the tails; this one requires
  the extension, requires the day to be the run's largest, sells only
  half, and only applies to positions still held whole.)
- Slow winners keep the existing +20% half-sale unchanged.

Rationale from our own measurements: the top 5% of positions carry >half
of all profit, and the fast-to-+20% cohort is where they concentrate;
the current unconditional partial halves precisely those positions.
Post-hoc caveat at maximum strength: this targets the tail visible in
seen data; a backtest of it is decoration. Not built until approved.

## 14. Pullback entry — the four omissions — REVERTED (decision 2026-08-28)

**Decision, on user instruction, recorded permanently:** P1-P4 are
REVERTED and are not part of any standing configuration. They cost 77
points in dev and 113 in test and dropped the system to the 23rd control
percentile in the test period; of v5r's 30 best positions the filtered
version takes 4 and 5. The loss side improved exactly as designed and the
winner side fell further, which is fatal in a system whose edge is a
17-20% right tail.

`--v10` remains runnable solely to reproduce the recorded negative
result, exactly as `--v6` does. The constants are NOT loosened toward
what the history prefers -- this section pre-registered that a worse
result is not a licence to re-tune, and it is not being re-tuned; it is
being switched off whole.

**What survives the revert, and it is the uncomfortable part:** the audit
that produced this section still stands. v5r's pullback entry is not
Minervini's pullback entry -- 58.8% of its positions break at least one
condition the source states, and the APP trade was the rule working as
written, not an exception. Reverting P1-P4 restores the returns; it does
not make the entry faithful. Both facts are now recorded and neither
cancels the other. Questions 3 and 6 of the open-source list (is the
entry a retest of a prior BREAKOUT? what confirms the bounce?) were my
invention, not the source's, and they did most of the filtering here --
so this negative result is partly a test of my guesses rather than of
his method.

(Original pre-registration below, kept for provenance.)

## 14. Pullback entry — the four omissions, closed (pre-registered 2026-08-28)

Written after auditing one trade (APP, 2025-02-24, the worst position of
the test period) against the source. That audit found the pullback entry
of section 11.3 conformant to its own text and unfaithful to the method
it claims to implement: it keeps the geometry of Minervini's pullback
and discards every qualifier that makes it his. The measurement that
follows is what forces this section — **58.8% of all 1,230 positions
break at least one of the source's stated pullback conditions, and 20.4%
break two.** That is not a tail case, and the earlier claim that it was
is withdrawn.

**Epistemic status — the weakest in this file, stated plainly.** Both
backtest periods have been seen many times. Worse, the depth condition
below was measured on this history before being written down: entries
more than 10% below the 60-day high are over-represented among the worst
5% of positions in dev (x3.4) and test (x4.4). A constant chosen after
seeing that is not pre-registered in any meaningful sense. Two things
limit the damage and neither repairs it: every constant below is taken
from the source's own stated value rather than from the measured
optimum, and no threshold will be moved after the run. The forward paper
ledger is the only honest judge, exactly as sections 10-13 say.

### What the source requires (verified 2026-08-28)

Directly quoted from a fetched page: *"a corrective pullback drifts
sideways on drying volume, while an impulsive decline falls hard on
rising volume"*, and *"heavy volume during the handle signals
distribution — the setup is compromised."* Volume behaviour, not
geometry, is what separates a rest from a reversal.

Secondary and NOT verified on a fetched page, flagged as such: that the
pullback should not exceed roughly 8-10% from the high, that the entry
is a retest of a level the stock broke through (holding it and bouncing
on fading volume), and that an earnings gap is not itself a clean entry.
These three drove constants below and their status is recorded here
rather than asserted.

### The four conditions, added to 11.3

Each one alone rejects the APP trade. All four are required; they apply
to the `pullback` label only — the cheat and power play carry their own
volume tests.

1. **P1 volume dry-up (`pb_vol_max: 1.00`).** Both the mean volume from
   the day after the qualifying 60-day-high close through today, and
   today's own volume, must be **at or below the 50-day mean**. Section
   11.3 said "Quiet volume acceptable — pullbacks are quiet by nature",
   which replaced a test with an assumption; the assumption is false in
   46.9% of the entries it licensed. The threshold is the 50-day mean
   itself — the one non-arbitrary level available — not a tuned number.
   (APP: 1.46x across the slide, 1.54x on the entry day.)

2. **P2 depth cap (`pb_max_depth: 0.08`).** The close must be **at or
   above 0.92 x the qualifying 60-day-high close**. The source range is
   8-10%; 8% is the strict end, taken for the same reason section 8b
   took the strict end of the 20-25% growth range. It is deliberately
   NOT the 10% that was measured, so the measurement does not set the
   constant. (APP: -19.5%.)

3. **P3 hold AND bounce.** The source's phrase is holding the level *and
   bouncing*. Section 11.3 built only the holding: one close above the
   SMA20. Require additionally **close > the previous close** (the
   pullback has stopped) **and close >= the midpoint of today's range**
   (a good close, the definition already in section 12.3). (APP: closed
   down from 415.31 to 410.45, and below its own midpoint of 410.75.)

   **Declared unbuilt:** the other half of P3 — that the level being
   retested is a prior *breakout* — is not mechanised. Encoding it needs
   the base machinery, which fires 6 times in 21 years (see the entry-mix
   record in FINDINGS), so requiring it would delete the entry rather
   than repair it. This section therefore closes three and a half of the
   four omissions, and says so.

4. **P4 no gapped high (`pb_gap_max: 0.05`, `pb_gap_window: 5`).** The
   qualifying 60-day-high close must **not** sit within 5 sessions after
   a day that opened more than 5% above the previous close. A price that
   teleported and then fell back never made the advance the entry is
   supposed to be resting from. The 5% is the spec's existing chase
   limit reused, not a new number. (APP: its 60-day high of 510.13 on
   2025-02-14 came one session after a +31% earnings gap open.)

### Protocol

One run, `--v10` = the standing configuration v5r plus P1-P4, both
periods, market-on-close, 200 entry-rate-matched controls, against the
v5r baseline (dev +148.4%, test +146.8%, 97th control percentile in
both). Trade counts reported beside every number, because these filters
can only remove entries and the comparison will be lower-powered than
v5r's. Whatever it says is recorded; nothing is adopted on the strength
of a backtest over history that has been seen.

**Declared in advance:** if v10 is worse, that is evidence about this
data and not a licence to loosen the constants back toward what the
history prefers. If it is better, it is still post-hoc and still waits
for the forward ledger.

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


### Section 14 (pullback qualifiers) — BUILT 2026-08-28, and it is worse

`--v10` = v5r + P1-P4. Constants in `minervini_v10:`; six unit tests, one
per qualifier plus the APP regression; the v5r baseline reproduces to the
digit with the flag off. Result: dev +148.4% -> +71.1%, test +146.8% ->
+33.4%, and the 23rd control percentile in test. The loss side improved
as designed (stops 140 -> 104 in dev, smaller average loser in both) and
the winner side fell further: of v5r's 30 best positions v10 takes 4 and
5. Full numbers and the two readings in FINDINGS. Per this section's own
pre-registration the constants are NOT loosened, and v5r stays standing.

### Section 13 (momentum-conditioned selling) — BUILT 2026-08-28

`--v9` = the standing config v5r + section 13, exactly as frozen there:
velocity exemption (fast +20% -> hold whole 40 days, stop / breakeven /
climax still live), climax partial (still-whole and >+30% -> sell half
at the close of the run's largest up-day, if that day gained >= 5%), slow
winners unchanged. Constants in the `minervini_v9:` block of config.yaml;
six unit tests drive the portfolio simulator over hand-built price paths
(`tests/test_minervini.py`, section "v9"). Re-running `--v5 --e3 --moc`
reproduces the recorded v5r numbers to the digit, so the new code is
inert when the rule is off.

Two implementation notes, neither of them a constant change:

1. **The climax partial sells AT the close**, not at the next open like
   every other exit. That is what section 13 says, and it is causal: both
   its conditions (today's gain, and whether it is the largest since
   entry) are known at that close, the same argument the market-on-close
   entry convention rests on.
2. **"Then normal rules" is read as resumption, not waiver** — after day
   40 a still-whole position above +20% takes the ordinary half-sale.
   Section 13 does not say which reading it wants; this is the one built,
   and it matches the O'Neil 8-week rule the section cites.

Result: worse in both periods, better in 2026, and only 18/24 positions
per period are treated differently at all — no power. Full numbers and
the paired cohort measurement in FINDINGS. v5r remains standing.

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

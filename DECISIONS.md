# Minervini system — the decision register

One line per mechanism ever built, with its verdict. Written 2026-08-28
because the record had grown across FINDINGS, the spec, LIMITATIONS and
chat until nobody could say what was in and what was out.

**Standing configuration: `v5r` = `--v5 --e3 --moc`.** Dev **+55.0%** (65th
control percentile), test **+150.9%** (100th), restated 2026-08-29 on
unadjusted prices. This line previously read +148.4% / +146.8% / 97th in
both; that was measured on dividend-adjusted prices and does not belong to
this dataset. See LIMITATIONS.md, "Split-adjusted prices".

**A FILTER LAYER sat in front of it** (`filters.py`,
`filter_backtest.py`) — a veto plus a hard-coded strength sort. **That
architecture was audited and retired on 2026-08-31** (see "The filter
architecture is wrong", below); its verdicts table still stands as a
record but no longer binds anything. Its numbers, for the record, one
continuous 2009-2026 path, no fees or tax: v5r +8.61%/yr, +MiniRocket
k=0.50 +11.16%/yr, +Shapelet g=0 +8.75%/yr, **SPY total return
+14.81%/yr**.

**Read this first:** the standing configuration takes 1,213 of its 1,230
positions on the section-11.3 SMA20 pullback and 6 on the pivot
breakout. The VCP is not used. See LIMITATIONS.md, "The rule we actually
trade is not his rule".

---

## IN — the standing configuration

| mechanism | spec | why it is in |
|---|---|---|
| Trend template, nine conditions | §1 | membership filter; the part that transfers to code cleanly |
| Market light (SPY > 200d SMA and calm) | §2 | the whole drawdown control: 68% of 2008 days flat, 50% of 2022 |
| Market-on-close fill | §3 dev. 1 | the only convention daily bars can express without an invented rule |
| 8% stop from entry | §4 | his number, inside his stated 5-8% range; caps every loss |
| Higher lows in the base | §9 | sourced: ascending bottoms = demand absorbing supply |
| Earnings blackout, 21 days | §9 | entries within 3 weeks of a report lost at triple the average |
| Decisive SMA50 exit (>1% or on volume) | §9 | removed a hair-trigger: 57% of exits fired within 1% of the line |
| Breakeven at 2R | §9 | a +16% winner may not become a loss |
| Tennis-ball window: 15 days, then the egg test | §10.1 | biggest measured gap; the old exits sold the dips at the bottom |
| Strength ranking (RS-line, weak-day, RS) | §10.2 | replaced alphabetical slot-filling |
| +20% half-sale | §10.1 | sell into strength; 17-20% of positions reach it |
| Entry repertoire (cheat / pullback / power play) | §11 | the pullback supplies 99% of all trades |
| E3 fast re-entry, 5 days after non-stop exits | §12.5 | dev +41 pts, test neutral, 97th control percentile in both |
| Flat 10% slots | §12.5 | see the size scan below; no optimum exists in this data |

## OUT — rejected, reverted or measured and not adopted

| mechanism | verdict | the number that decided it |
|---|---|---|
| v1 pivot mechanics (60d-high pivot, fixed-block ordering) | **rejected** | superseded by v2 after a source audit found four deviations |
| v2 buy stop + `failed_breakout` eject | **rejected** | dev -42.8%, test -31.3%, 0th control percentile in both; the eject was an invented rule producing 90% of trades |
| Fundamentals gate §8 (TTM EPS) | **rejected** | 5 trades per period; the threshold was applied to the wrong quantity |
| Fundamentals gate §8b (quarterly EPS, faithful) | **rejected** | 9 trades per period, negative in both; +1.35% vs +2.08% on 4,585 fills |
| Earnings-beat leg §8c | **not adopted** | improved both periods (-12.4/-7.9 -> -9.3/-3.5) but still negative and under its controls |
| v6 money engine (risk sizing, pyramiding, progressive exposure) | **REVERTED** | +107/+147 -> +44/+53, drawdowns -25/-23 -> -41/-31; halved size at bottoms |
| v6 market dimmer (4-point score) | **REVERTED** | same run, same reasoning: judgement we do not have |
| E1 climax sell-all at +25% | **rejected** | -21/-52 pts; amputates the +50-100% right tail the edge lives in |
| E2 volume-weighted weakness | **rejected** | dev +71, test -28 — a regime bet, fails the both-periods bar |
| E4 aging stop from day 60 | **removed, dead code** | never triggered once in 1,467 trades |
| SPY parking of idle cash | **not adopted** | largest lever found (dev +148 -> +224%) but doubles dev drawdown to -54%: it imports the beta the market light exists to avoid |
| Craft ranking (good closes, up/down volume) | **not isolated** | bundled with parking in one run; no separate verdict exists |
| Split-ratio scan 0-100% (how much to realise at +20%) | **50% stands** | whole curve spans 0.27/0.40 pts per bet against a 16-22% sigma; 50% tops both periods but nothing here is outside noise; selling MORE is monotonically worse (E1 again) |
| Bet-size scan 5-33% | **rejected, 10% final** | non-monotonic; 20% worse than 10% in BOTH periods; the 33% test row is a concentration lottery |
| v8 adaptive sizing (bet more when signals are scarce) | **rejected** | dev +62%, test +56% against +107/+147; sizes by arrival intensity, which is not conviction |
| v9 momentum-conditioned selling (velocity exemption + climax partial) | **not adopted** | dev -11, test -7 pts; only 18/24 positions treated differently — no power |
| v10 pullback qualifiers (dry-up, depth cap, bounce, no gapped high) | **REVERTED** | dev +71%, test +33%, 23rd control percentile; the faithful version is the worse one |
| Wide US universe (+1,737 names) | **rejected** | dev +67%, test +37%; new names stopped out twice as often, geometric mean per position goes negative |
| Code 33 hard gate (EPS + sales + margins) | **not adopted** | 14 and 45 trades, 2-5% invested; 0.86% of template days pass all three legs |
| Code 33 conviction ranking | **rejected** | dev -21, test +27 — the E2 pattern, same bar |
| Industry-group strength, hard gate (top 30% of groups) | **rejected** | dev +133%, test +53%; at the control median in test |
| v11 pyramid 5/3/2 (pilot 5%, adds 3% and 2%) | **rejected** | euro/bet 1.0082/1.0108 -> 0.9978/0.9997; the pyramided sixth of positions loses 4.6/4.7 pts because the ladder caps at the flat 10%, so adds only raise the cost basis without adding capital |
| Industry-group strength, conviction ranking | **rejected** | dev +112%, test **-0.8%**, 3rd control percentile — 97 of 100 random portfolios beat it |
| Capital-counted slots (a split position occupies its remaining ~half, freed halves finance extra positions) | **rejected 2026-08-31** | measured under the ranker era on operator request: strength +291.5% -> +259.8%, rocket +234.6% -> +159.1%; more bets (+44/+58), more time invested (+3 pts), lower total — same sign in both arms and both measurement windows, so not path noise. Per-bet quality of the whole book falls (+0.57% -> +0.44%): the extra capacity recomposes the book, not just appends to it. The `capital_slots` knob stays in `simulate()`, off; with it off the +291.5% control reproduces bit for bit |

## Never built, and why

| | reason |
|---|---|
| Sales and margin legs before 2026-08-28 | recorded as "not obtainable" — that was a limit of yfinance; EDGAR has 71,327 quarters. **Corrected, then built.** |
| Analyst estimate revisions | the provider exposes a current snapshot with no history. Not testable on this source, ever |
| Point-in-time universe and IPO dates | costs money; ours is today's S&P 1500 |
| Float, turnover, institutional sponsorship | not cached |
| Intraday volume pace | daily bars have one volume number, after the close. Both failed fill conventions were workarounds for this |
| Structural stop under the last contraction, and a reward:risk floor | specified nowhere, never built; only the 8%-from-entry half exists |
| Contraction quality as a shape | reduced to a list of depth percentages; the shape is the signal and we do not have it |
| His discretionary veto | irreducible. Whatever gap remains is attributed here |

*(Industry-group strength moved out of this table on 2026-08-28: it was
built, tested both ways, and rejected. See the OUT table above.)*

## Filter layer — verdicts (added 2026-08-29; **VOIDED 2026-08-31**)

**Every verdict in this table was measured through the architecture
retired below: binary top-20% label → quantile veto → hard-coded
strength re-rank.** They are records of that chain, not conclusions
about the transforms, losses or features inside it. A transform that
lost as a veto (volume, MV, the CNN's widths), a loss that lost when
binarised (F-beta, the rate target), and a capability ruled out across
four classification objectives (jackpot picking) have all only ever
been tried in a pipeline whose decision the loss never saw. None of
these rows may be cited to rule anything in or out under the ranker;
re-measure there first. The numbers stay as the record of what the
retired architecture did.

| filter | verdict *(void — retired architecture)* |
|---|---|
| **MiniRocket k=0.50** (84 fixed kernels, PPV, balanced ridge) | **IN.** Dev +104.0%; and the only arm that survives the continuous 2009-2026 path: +8.61% -> +11.16%/yr with drawdown IMPROVING, -29.7% -> -28.3% |
| **Shapelet g=0 k=0.50** (8 curves x 30 days, 249 params) | **IN on dev, DOES NOT TRANSFER.** Dev +126.3%, best of the session; continuous path +8.75%/yr against v5r's +8.61% — nothing — with drawdown worsening to -40.2% |
| Volume added to the shapelet (`--channels 0,2`) | OUT. +126.3% -> +73.7% |
| Price x volume interaction in MiniRocket (`--mv`) | OUT. +104.0% -> +68.4% |
| Stricter thresholds (k=0.80, k=0.90) | OUT. Starve the book: invested falls 71.7% -> 53.4% -> 40.4% and returns fall below AllPass |
| **Dilated CNN** (`minervini_cnn.py`) | **OUT, DELETED 2026-08-29: too many parameters, hard to train.** 2,514-3,010 params against an effective sample size of ~3,000-4,000 (windows overlap 251/252 days, labels overlap, ~12 bets share each day's market factor). Every width tried -- 938, 2,514, 4,730, 7,586 -- landed inside its own label-shuffle control: mean lift -0.0013 to +0.0013 against the shuffle's -0.0048, AUC 0.484-0.541 with no ordering by width. A 249-parameter shapelet and a ZERO-learned-parameter MiniRocket both beat it. The shared helpers it happened to contain (`load`, `folds`, `report`, `line`, `jackpot_loss`, the constants) were never CNN-specific and moved to `bets_common.py`; nothing else was lost |
| F-beta loss, reward only a correct >5% call (`--loss f1`) | **OUT, reverted 2026-08-29.** Dev +89.7% against the BCE shapelet's +126.3%. Kept runnable as a recorded negative, like `--v6` and `--v10`. Its one win: best drawdown of any arm, -23.8% |
| Jackpot picking, any arm | **Not a capability these models have.** FOUR objectives aimed at it, all landing at or below the base rate: cost-weighted BCE x1.02, balanced BCE x0.96, symmetric log-value AUC 0.480, F-beta rewarding only true positives **x0.95**. The loss was never the binding constraint — the information is not in a year of price history in a form these models can reach. The filters earn their return by declining bad trades, not by finding good ones |

## The filter architecture is wrong — audit and replacement (2026-08-31, proposed)

**Verdict: the veto-plus-strength-sort construction is replaced, not
tuned.** No further loss functions, thresholds or ensembles are to be
tried inside it.

### What the code does today, end to end

A signal reaches the book through four stages, and the trained one is
not the one that decides:

1. The screener (v5r) proposes candidates — ~13 on a green day, against
   a median of **zero** free slots.
2. A model scores each candidate's window, and a quantile threshold
   frozen at fit time turns the score into a yes/no veto
   (`filters.py`, `decide`). After that the score is thrown away — it
   never reaches the simulator.
3. `simulate()` sorts the survivors by hard-coded keys — `rsl_hi`, then
   `weak`, then `rs`, then ticker (`minervini_backtest.py:934`) — and
   the top of that sort gets the slot. Since `weak` is 99.99% NaN, the
   real picker is `rsl_hi` → `rs` → alphabet.
4. The loss that trained stage 2 was a classification against a binary
   top-20% label (`filter_backtest.py`, `score_walk_forward`:
   `aux = y >= thr`). A rate-target variant was written and it changed
   only *which* quantity was binarised — the model still saw "top fifth
   or not", never the rate.

### Where the objective leaks

Three lossy conversions sit between the goal (growth per slot-day) and
the decision, each discarding what the previous stage produced:

- the continuous outcome is binarised into a top-quantile label before
  training — the loss cannot prefer +40% over +6%, or a 20-day gain
  over the same gain in 60 days;
- the trained score is collapsed to a boolean veto at a frozen
  quantile — the ranking information the fit did learn is discarded at
  decision time;
- the survivors are re-ranked by fixed keys the loss has never seen —
  the pick the money actually rides on is made by a component that was
  never trained and cannot learn.

Nothing in this chain optimises the goal, so its relation to the goal
is accidental: the system can sit below AllPass indefinitely, and would
sit above it with the veto inverted. Which way it lands is not
informative and is deliberately not investigated (operator instruction,
2026-08-31). The finding is the architecture, not any loss inside it.

### What this audit voids

A conclusion is only as good as the pipeline that measured it, and this
pipeline could not translate a better model into a better book. So:

- **Every row of the filter-layer verdicts table above is void as a
  verdict** — MiniRocket IN, the shapelet's non-transfer, volume OUT in
  both forms, strict thresholds OUT, the CNN, F-beta, and "jackpot
  picking is not a capability these models have". All were measured as
  vetoes in front of a picker the loss never saw. The numbers remain
  correct records of the retired chain and nothing more.
- **The threshold rows are doubly void**: "k=0.80 starves the book" is
  a statement about the veto mechanism itself, which no longer exists.
- **CLAUDE.md's stated goal** ("an ensembled investment strategy out of
  weak filters", filters that "earn by declining bad trades") describes
  the retired architecture and needs rewording once the ranker stands.
- The screener-level IN/OUT tables (v5r mechanisms) are unaffected —
  they were measured with the strength sort as part of v5r itself, and
  v5r as-is remains the baseline. "Strength ranking §10.2" stays IN for
  the baseline, and under the ranker it is subsumed: the keys become
  features, and StrengthScore becomes the control arm.

### The replacement: one ranker, one target, no downstream picker

One trained model — call it the **ranker** — produces one number per
orderable signal: the predicted growth rate of a euro spent on it. The
slot decision reads that number directly:

    take = top free-slots of the day's usable pool, by predicted rate,
           ticker as the determinism tie

No veto, no threshold, no `--keeps`, no strength keys in the sort. The
slot capacity is the only selectivity. The `key()` function and the
boolean `gate` both retire; `simulate()` takes a (days × tickers) score
matrix instead.

**Features: everything, including the old picker.** The ranker's input
is the window transform (MiniRocket features as today) **plus** the
panel columns the hard-coded sort used to spend — `rsl_hi`, `rs`,
`weak`, `group_pct`, `code33` — plus anything else already in the
panel. This is what makes the architecture safe: a linear ranker with
positive weight on `rsl_hi` and `rs` and zero elsewhere *is* today's
v5r ordering. The hypothesis space contains the current system, so
persistent underperformance of the baseline stops being an available
failure mode of the design and becomes an ordinary fitting failure,
visible as such.

**Loss: least squares on the rate itself.** The fit is a ridge
*regression* of the realised rate on the features (closed form,
`RidgeCV`, same walk-forward schedule and embargo as today). Squared
error estimates the expected rate; ranking by expected
rate is the greedy-optimal slot assignment when every slot-day not
spent on A is available to B. The loss is the goal — nothing is
binarised, thresholded or re-sorted after it.

### The target, carefully

Definitions: `y` = euros returned per euro committed (dividends in,
`geostats.bet_multiples` convention), `t` = **trading** days held —
calendar days have a minimum of zero and would divide by zero — floored
at `t_floor` days. The floor exists because 1.8% of bets close within
three days and carry ~14% of the total `|rate|` mass, overwhelmingly
fast stop-outs whose rates run to -0.11/day against a best of +0.012.

**The quantity optimised is `ln(y)/t`, and a bet is one vote.** Every
bet is the same size — a flat tenth of equity — so bet size is a
constant and never weights anything.

- **Per signal (the training target).** An unsplit bet:

      r = ln(y) / t

  A split bet is two capital streams of the one bet: the banked half
  earned `ln(y_half)` over its own `t_half` days, the rest earned
  `ln(y_rest)` over the full `t`. Sum both wins, each stream at its own
  rate, each with its capital share `f` (0.5 under the +20% half-sale):

      r = f·ln(y_half)/t_half + (1-f)·ln(y_rest)/t
      y_rest = (y - f·y_half) / (1-f)

  Multiples decompose *arithmetically* by capital share (never logs);
  the streams' rates then combine by those same shares. Ending the
  first stream's clock at the half-sale is the point: banked capital is
  free capital, and the rate target credits it.
- **Per portfolio (the evaluation).** The geometric mean of daily
  multiples, one vote per bet:

      G_day = exp( (1/n) · sum(r_i) )        n = bets taken

  reported beside `geo_per_bet` (the per-bet multiple, `geostats.py`),
  which is unchanged.

*(Operator decision 2026-08-31: equal weight per bet. A euro-day
weight — `f·t` in both the leg blend and the portfolio average — was
proposed and rejected: it re-weights long bets upward, undoing the
per-day normalisation the target exists for. Do not re-propose.)*

*(Later the same day, operator-approved: `ln(y)/t` is superseded AS
THE TRAINED OBJECTIVE by the rent target `ln(y) − c·t` —
`RANKER_SPEC.md` Amendment 4. The measured ground: the ratio prefers
a small fast win over a jackpot and is dominated by stop-outs, and
the book trained on it held longer, bet less and lost the total. One
vote per bet stands unchanged in the rent loss; `G_day` keeps this
section's definition as a reported column.)*

**The natural zero.** The predicted rate is on cash's own scale: cash
earns 0.0/day. A slot may therefore stay empty when the best
candidate's predicted rate is negative — read off the predicted
quantity itself, not a tuned threshold. Off by default; the market
light already does the regime version of this.

**Specified 2026-08-31 in `RANKER_SPEC.md`**: four arms (StrengthScore
— the do-nothing control that must reproduce today's AllPass book
exactly — MiniRocket, MultiRocket, Hydra), per-fold loss/Spearman/AUC
lines, and the acceptance list. Ensembles agreed as the next step.

### What changes, by file

| | change |
|---|---|
| `filters.py` | `decide`/`threshold`/`keep` retire; the interface is `fit(features, r)` + `score`. `AllPass` retires with the veto. The baseline becomes **StrengthScore** — the old sort key encoded as a score — so the control arm runs the same code path as every fitted arm (Rule 3 preserved) and must reproduce **+291.5%** before any fitted row is read |
| `minervini_backtest.py` | `simulate(scores=...)`; `take` = top slots by score. `key()` survives only for the legacy no-score path |
| `filter_backtest.py` | `ln(y)/t` is the only target, with the leg blend above; the ledger must carry `half_frac`, `y_half`, `half_days_held` and `days_held` per signal (`minervini_bets.py`); `RidgeCV` regression replaces `RidgeClassifierCV`; `aux`, jackpot stats and `--keeps` retire |
| caches | feature caches are keyed on the transform and survive untouched; block and model caches refit — a regression is a different estimator, so this is a real refit of the ridge stages, not a key-field accident |
| `EVALUATION_SPEC.md` | Rule 3's baseline definition becomes StrengthScore; every run prints its target and estimator (`target=ln(y)/t estimator=ridge-reg`) beside the embargo and window it already reports; `G_day` joins the reported figures |

**State of the tree, 2026-08-31 (later the same day): BUILT.** This
section was a design; it is now code -- `rankers.py`, a rewritten
`filter_backtest.py`, `simulate(scores=...)`, `bets_common.rate_target`
and the `y_half` / `half_days_held` ledger columns. Two arms, not four:
`strength` and `rocket`, on the operator's instruction. The revert to
`56ead0c` had taken `minervini_hydra.py` and `minervini_multirocket.py`
with it, so those two arms have no transform to sit behind and
`filter_backtest.py` refuses them by name rather than guessing; their
cached features are orphans in `results/.fitcache` until the modules are
written again. See "The ranker, measured" below, and `RANKER_SPEC.md`'s
"As built" section for the six decisions taken at the keyboard -- one of
which, the surviving watchlist cap, is an amendment to the spec rather
than a detail.

Headline evaluation is unchanged: one continuous path through
`simulate()`, per-bet figures through `geostats.py`, the control arm
reproduced before any fitted row is read.

## The ranker, measured — MiniRocket against do-nothing (2026-08-31)

> **SUPERSEDED the same day by "The alpha was the whole story" below.** Everything here was measured with alpha chosen by leave-one-bet-out, which RANKER_SPEC Amendment 1 then showed cannot see past a bet's same-day twins. The book numbers in this section are the record of that estimator, not of MiniRocket as a ranker. The reasoning about the architecture, the target and the pool still stands.

**Verdict: the architecture stands, the arm does not.** One record,
2007-01-03 .. 2026-08-27, one continuous path, embargo 400d, expanding
window, `target=ln(y)/t` floored at 3 trading days,
`estimator=ridge-loocv`, 4,206 features (4,200 MiniRocket plus the three
old keys as value/finite pairs). Both arms share one schedule, built
once and handed to each in turn.

| arm | total | ann | maxDD | rows | bets | geo/bet | G_day | invested |
|---|---|---|---|---|---|---|---|---|
| **strength** (the do-nothing control) | **+291.5%** | +7.2% | -30.2% | 1,477 | 1,252 | **+0.57%** | -0.3561% | 73.4% |
| **rocket** (MiniRocket + ridge) | +136.3% | +4.5% | **-21.6%** | 1,368 | 1,196 | +0.35% | **-0.2655%** | 74.4% |
| the whole candidate pool | — | — | — | — | 55,737 | +0.52% | -0.2380% | — |

**The control reproduced exactly** — the same trades row for row and
+291.5%, checked in-process before any fitted number was printed. The
encoding has no freedom, so that check is a real one, and it passed.

**The arm loses the book and wins the drawdown.** Less than half the
total return, a third off the maximum drawdown, 56 fewer bets at
essentially the same time invested. Read the two per-bet columns
together: the ranker is BETTER on the quantity it was trained on
(`G_day` -0.2655% against the control's -0.3561%) and WORSE on the
quantity the book compounds (`geo/bet` +0.35% against +0.57%).

**But it did NOT do what the loss asked, and the loss is where to look
first.** Against the honest causal null -- predict the fold's own
training mean -- the out-of-fold mse is worse than that constant in
**all 18 folds**, by 2x to 8x, row-weighted R2 **-2.33** over 53,489
scored bets, while in sample it explains +0.57. The fit's level is
wrong: it overshoots, and `alpha=100` chosen by leave-one-out on the
training rows is nowhere near the shrinkage the out-of-fold loss wants.
What survives is the rank alone (Spearman +0.064), and the slot decision
uses nothing but rank, which is the only reason the book functions
rather than collapses. So this row is a bad regression that is a
slightly-better-than-nothing ranker -- NOT evidence about the target,
which has not yet been given a fit that minimises it out of fold. The
next measurement is a shrinkage sweep judged on out-of-fold loss instead
of on training LOO. *(Specified 2026-08-31 as `RANKER_SPEC.md`
Amendment 1 — with one correction: the sweep's judge is grouped,
purged cross-validation INSIDE the training window, never the outer
out-of-fold loss, which would leak the scored block into model
selection.)*

**Both books sit below the pool on `G_day`** (-0.36% and -0.27% against
-0.24%), which is less strange than it looks. A per-day rate with one
vote per bet is dominated by the bets that close fastest, and those are
stop-outs; and the book is not a random draw from the pool, since it can
only buy when a slot is free. The pool row is a level to read against,
not a counterfactual portfolio.

**The signal is weak and it is not zero.** Eighteen folds, out-of-fold
Spearman mean **+0.064**, median +0.070, positive in 13 of 18, range
-0.15 to +0.24. Out-of-fold AUC averages **0.500** on the diagnostic
top-20% cut: the rate target ranks slightly and does not classify at
all. Training Spearman falls from +0.87 to +0.52 as the window expands
from 2,174 to 49,334 rows while out-of-fold Spearman does not move,
which is what 4,206 features on a few thousand rows looks like — the fit
is 8.5x better in sample than out of it (mse 2.1e-05 against 1.8e-04).
Alpha lands on 100 in 17 of 18 folds and 316 once, which is where the
retired classifier landed too.

What this does NOT say: no row of the voided verdicts table is
reinstated or refuted by it. This is one transform, one estimator and
one target measured under the new architecture, and it is the first such
row.


## The alpha was the whole story — Amendment 1, measured (2026-08-31)

**Verdict: the criterion was the bug, not the transform.** Same record,
same schedule, same features, same target; the only change is that alpha
is chosen by grouped, purged cross-validation instead of
leave-one-bet-out (RANKER_SPEC.md Amendment 1, `estimator=ridge-ycv`).

| arm | total | ann | maxDD | rows | bets | geo/bet | G_day | invested |
|---|---|---|---|---|---|---|---|---|
| **strength** (control) | **+291.5%** | +7.2% | -30.2% | 1,477 | 1,252 | +0.57% | -0.3561% | 73.4% |
| **rocket, ridge-ycv** | +234.6% | +6.3% | **-23.0%** | 1,303 | 1,143 | **+0.76%** | **-0.2222%** | 74.4% |
| rocket, ridge-loo *(superseded)* | +136.3% | +4.5% | -21.6% | 1,368 | 1,196 | +0.35% | -0.2655% | 74.4% |
| the whole candidate pool | — | — | — | — | 55,737 | +0.52% | -0.2380% | — |

**The loss now beats the constant**: row-weighted out-of-fold R2
**+0.038**, better than predicting the fold's own training mean in **13
of 15** fitted folds, against **0 of 18** under leave-one-out. The last
fold is the best of the run (+0.08, out-of-fold Spearman +0.18). Three
folds -- 2009, 2010, 2011 -- cannot supply two purged years and fit
nothing, exactly as the amendment predicted; 48,600 of 55,737 signals
are scored and the rest keep the control ordering.

**The alpha moved four orders of magnitude**: 100 under leave-one-out,
**1e+06** for 2012-2014 and **3.16e+06** from 2015 on. It did NOT pin at
the grid's 1e+08 ceiling, so the criterion found an interior optimum
rather than asking for the mean. Training Spearman collapsed from +0.87
to about +0.12 while out-of-fold Spearman rose to +0.12-+0.18: the fit
stopped memorising and kept the rank. Standardised features make the
comparable quantity alpha/n -- 0.003 under leave-one-out against about
88 now, which is the same statement in the only units where it means
anything.

**What the arm is worth, stated carefully.** It beats the control and
the pool on every per-bet measure -- +0.76% per bet against +0.57% and
+0.52%, and G_day -0.2222% against -0.3561% and the pool's -0.2380%,
the first arm ever to beat the pool on the quantity it is trained on --
with a drawdown a quarter smaller, on 109 fewer bets at the same time
invested. **And it still loses the book: +234.6% against +291.5%.**
That is not a contradiction. Ten bets are open at once, so the equity
curve depends on when capital was committed and not only on what each
bet returned; one vote per bet and one continuous path answer different
questions and they disagree here. Which of the two is the objective is
a decision, and it is not taken in this row.

What this does NOT say: no row of the voided verdicts table is
reinstated or refuted. It does say that "MiniRocket as a ranker is worse
than doing nothing", recorded in the section above, was a statement
about leave-one-bet-out.


## Amendment 3 and the rent target, measured (2026-08-31)

Three of Amendment 3's four runs, then Amendment 4. All on the fitted
years 2012-01-03 .. 2026-08-27 unless stated, 10 slots unless stated,
every arm on one schedule. **3.4 is still outstanding.**

### 3.1 The blend — the two orderings compose

Per-day rank-average of the control ordering and the fitted scores, no
new fits. Under the RATIO target:

| arm | total | maxDD | bets | geo/bet | G_day |
|---|---|---|---|---|---|
| strength (w=0) | +292.4% | -29.3% | 988 | +0.82% | -0.3152% |
| blend0.25 | +195.0% | -30.0% | 987 | +0.65% | -0.2561% |
| blend0.50 | +265.3% | -23.3% | 896 | +1.10% | -0.1741% |
| **blend0.75** | +272.1% | **-17.6%** | 879 | **+1.23%** | **-0.1081%** |
| rocket (w=1) | +251.6% | -18.5% | 876 | +1.18% | -0.1283% |

blend0.75 is OUTSIDE both members on per-bet geo, `G_day` and maxDD --
not between them, which was the other branch. But the sweep is not
monotone (0.25 is worse than both its neighbours), which is a noise
signature, and 3.3 explains why.

### 3.2 Slot count — the ranker's edge is a ten-slot phenomenon

Gross exposure held at 100%; compare WITHIN a config only.

| slots | strength geo/bet | rocket geo/bet | strength total | rocket total |
|---|---|---|---|---|
| 10 x 10% | +0.82% | **+1.18%** | +292.4% | +251.6% |
| 15 x 6.67% | **+1.08%** | +0.88% | +381.6% | +190.5% |
| 20 x 5% | **+0.99%** | +0.72% | +338.9% | +161.9% |

**The per-bet edge inverts.** Asked for ten names a day the ranker
delivers; asked for twenty it must go deeper into its own ordering and
its deeper picks are worse than the control's. It is good at the top of
its ranking and nowhere else. Idle capital is not the explanation --
the fitted arms are MORE invested in every config.

### 3.3 The noise yardstick — total return is unreadable here

200 books with the scores shuffled within each day (pool and score
distribution preserved), same window and config:

| | min | p5 | median | p95 | max |
|---|---|---|---|---|---|
| total | +48.4% | +81.9% | +204.5% | +357.6% | +525.3% |
| maxDD | -35.2% | -32.8% | -25.1% | -20.5% | -18.5% |
| geo/bet | +0.04% | +0.20% | +0.73% | +1.21% | +1.56% |

**The 90% band on total return is 276 points wide.** strength (+292.4%)
sits at the 86th percentile, rocket (+251.6%) at the 74th, blend0.75
(+272.1%) at the 80th. Every total-return comparison in this register
between these arms -- in BOTH directions -- is inside noise and decides
nothing. The incumbent ordering is itself not distinguishable from
shuffling the same day's candidates.

What survives the yardstick: **maxDD**, where rocket lands on the best
of 200 shuffled books (-18.5%) and blend0.75 goes past it (-17.6%); and
**geo/bet**, marginally, at the 94th and 95.5th percentiles. The
control is ordinary on both (median maxDD, 65th percentile geo/bet).

### Amendment 4 — the rent target: correct shape, unlearnable half

`r = ln(y) - c*t`, no floor, two ridge heads (log-profit, slot-days)
and `c` derived per fold by Dinkelbach iteration at the book's own
2.25% selectivity. Same window, same schedule:

| arm | total | ann | maxDD | bets | geo/bet | G_day | G_rent |
|---|---|---|---|---|---|---|---|
| strength | +292.4% | +9.8% | -29.3% | 988 | +0.82% | -0.3152% | -1.0204% |
| rocket (rent) | +302.8% | +10.0% | -34.1% | 1,028 | +0.70% | -0.3727% | -1.0474% |
| **blend0.75** | **+428.2%** | **+12.1%** | -33.6% | 1,023 | +0.95% | -0.3559% | **-0.8178%** |

**The profit head cannot be estimated.** Its alpha pinned at the grid
ceiling 1e+08 in **all 15 folds**; the days head stopped at 1e+07 with
`daysR2` +0.01 to +0.12. Out-of-fold R2 against the constant is
**-0.000, better in 5 of 15 folds**, against the ratio target's +0.038
in 13 of 15.

**So the ranker collapsed into a holding-time predictor.** With
`ln(y)` shrunk away the score is dominated by `-c * predicted_days`,
i.e. it ranks by shortest predicted hold -- the opposite of the
amendment's intent. Three signs agree: it takes MORE bets (1,028
against 988, where the ratio arm took 876); holding time reversed to
about 28.4 days/bet against the control's 29.6, where the ratio arm
held 32.65; and the mandatory sensitivity band rises monotonically with
rent (0.5c +241.5%, 1c +320.1%, 2c +413.5% on the whole record). A
172-point swing across `c/2..2c` is the fragility flag firing.

The amendment's REASONING stands -- the ratio target is anti-right-tail
and the rent shape is the right one. What the data adds is that with
these features the profit half is not estimable at all, so the
correct-shaped target degenerates to its second term. **A better target
cannot rescue a feature set that does not predict profit.**

**The one book outside the noise band.** blend0.75 of the rent arm with
the control reaches +428.2% with the best per-bet figure of any arm
measured (+0.95%) on essentially the control's bet count. Against
3.3's band (p95 +357.6%, max +525.3%) that is roughly the 97th-98th
percentile -- the first total-return figure in this sequence that is
not obviously noise. Not settled: the band was measured against the
ratio-era scores and re-permuting against these would make it exact.

### Where this leaves the transforms

Amendment 3.1 said build them; 3.2 and 3.3 say the evidence for that was
a ten-slot, inside-the-noise reading; Amendment 4 says the missing
ingredient is not the target. MultiRocket and Hydra remain frozen, and
the open question is whether new geometry over the same windows can
predict profit when 4,200 kernels and two targets could not.


## Amendment 5 — one fit, and the target is still not learnable (2026-08-31)

The two-head construction of Amendment 4 was a specification error: the
grouped CV judged log-profit alone (noise, shrunk to the ceiling) and
slot-days alone (some signal, kept), and the difference of two
individually-regularised halves ranked its own target NEGATIVELY out of
fold. Replaced by ONE ridge fitted directly on `r = ln(y) - c*t`, `c`
derived exactly as before. The rule that generalises, now beside "one
bet, one multiple, one average": **a decision comes from the single best
estimate of the decision quantity, never from separately tuned estimates
of its parts.** It is enforced in code -- `MultiRidge.score` raises on a
multi-target fit.

Fitted years 2012-2026, 10 slots:

| arm | total | ann | maxDD | bets | geo/bet | G_rent |
|---|---|---|---|---|---|---|
| strength | +292.4% | +9.8% | -29.3% | 988 | +0.82% | -1.0204% |
| rocket (one fit on r) | +128.1% | +5.8% | -31.6% | 1,029 | +0.31% | -1.4233% |
| blend0.75 | +253.9% | +9.0% | -27.9% | 1,018 | +0.63% | -1.1412% |

**The composition error was real and fixing it was not enough.**
Training Spearman went from negative to positive -- the model now ranks
its own target in sample -- but out-of-fold R2 against the constant is
-0.002, better in 6 of 15 folds, with alpha at the 1e+08 ceiling in 13
of 15.

**One correction to the amendment's own branch 2.** "The fit shrinks to
a constant, so the book keeps the control ordering" is not what happens:
ridge shrinkage SCALES the coefficient vector and rank is scale
invariant, so a maximally shrunk model still emits a complete ordering
-- driven by whatever direction survives, which is noise. The book does
not revert to the control, it reverts to an arbitrary reordering, and
pays for it on every column.

**What it cost, read against the 3.3 yardstick.** Totals decide nothing
(+128.1% and +292.4% are both inside the 276-point band). On the two
readable columns the rent formulation is WORSE than the ratio one it
replaced: maxDD -31.6% against the ratio arm's -18.5%, which was the
best of 200 shuffled books; geo/bet +0.31% against +1.18%, which was the
94th percentile. **The only measurement in this whole sequence that
stood outside noise was the ratio arm's drawdown, and it does not
survive the rent target.**

## Amendment 6, steps 1-3 — cap the hold, train the value (2026-09-01)

### Step 1: the voided data, gated

Two gates over the same 15 fittable years -- per-year sign stability,
then a ridge on the columns alone against the training-mean null:

| candidate | coverage | gate 1 (sign) | gate 2 (folds) |
|---|---|---|---|
| **group_pct** | 87.4% | **12/15 PASS** | **8/15 PASS** |
| c33_eps | 100% | 10/15 PASS | 6/15 fail |
| c33_sales_margin | 100% | 9/15 fail | — |
| surprise | 96.7% | 9/15 fail | — |
| both survivors together | | | 7/15 fail |

**`group_pct` survives, and its stable sign is NEGATIVE**: a stock in a
stronger industry group returns a LOWER log multiple, in 12 of 15 years.
The retired §16 hard gate required the top 30% of groups -- it was
selecting the wrong side of a real relationship, which is precisely why
voiding gate-era rejections was right. As a gate it could only lose; as
a feature with a free sign it is usable. It clears gate 2 at exactly the
bar, and adding the EPS column makes it worse (7/15), so nothing is
admitted with it.

### Step 2: what the cap costs the incumbent, before any fit

`max_hold` force-sells H trading days after entry; off by default and
the uncapped control still reproduces +291.5% row for row. At H=42, on
the whole record under the strength ordering:

| | uncapped | H=42 |
|---|---|---|
| total | **+291.5%** | **+214.8%** |
| maxDD | -30.2% | -28.7% |
| bets | 1,252 | **1,535** (+22.6%) |
| geo/bet | +0.57% | +0.30% |

The ledger says where the 77 points went: **12,248 signals now exit at
the cap, and they were averaging 1.1448** (median 1.1283) -- still
running at day 42. What is left under `sma` collapses from 1.0587 to
1.0003: the good trend exits WERE the ones still alive at the cap.

**The sharpest pair of numbers in this step.** The POOL's per-bet value
barely moves (+0.52% -> +0.50%): the cap does not hurt the average
candidate. The BOOK's halves (+0.57% -> +0.30%). So the incumbent
ordering goes from marginally better than its own pool to clearly worse
than it, once it may not hold winners past two months. **The strength
ordering's entire per-bet edge lives in the right tail the cap
amputates.**

### Step 3: the value target on capped labels

One ridge per fold on `ln(y)` -- with the hold capped, per-bet and
per-slot-time are the same ranking, so there is nothing left to compose.

| target | R2 oof | folds beating the constant |
|---|---|---|
| ratio `ln(y)/t` | **+0.038** | **13/15** |
| rent, two heads | -0.000 | 5/15 |
| rent, one fit | -0.002 | 6/15 |
| **value, capped H=42** | -0.002 | **8/15** |

The cap lifts the fold count to a bare majority -- best since the ratio
target -- but the loss is still marginally worse than the constant and
alpha pins at the ceiling in every fold.

Books, fitted years, H=42:

| arm | total | ann | maxDD | bets | geo/bet | G_day |
|---|---|---|---|---|---|---|
| strength (capped control) | **+204.9%** | +7.9% | -26.8% | 1,223 | +0.46% | -0.2727% |
| rocket | +182.1% | +7.4% | **-22.4%** | 1,226 | +0.44% | **-0.2316%** |
| blend0.75 | +86.2% | +4.3% | -32.2% | 1,227 | +0.14% | -0.2657% |
| the pool | — | — | — | 55,737 | +0.50% | -0.2171% |

- **The model recovers none of the cap's cost** and matches the control
  per bet.
- **Both orderings are now below their own pool** (+0.46%, +0.44%
  against +0.50%). Under the cap neither the incumbent keys nor the
  model beats a random draw from the same candidates.
- **The blend collapses** (+86.2%, +0.14%/bet, worst drawdown of the
  three): whatever made blend0.75 work uncapped does not survive the
  cap.

The total-return gaps are NOT called: the capped book needs its own
200-way permutation band and the 276-point one belongs to the uncapped
book (Amendment 6 step 5, not yet run). Steps 4-6 are outstanding.


## PROPOSED — not built

| idea | what it would need |
|---|---|
| **Combine MiniRocket and Shapelet** | **Superseded 2026-08-31: written in veto terms (`AND`/`vote`, thresholds to loosen) for the retired architecture.** Under the ranker the same idea is one line — both transforms' features in the same regression — and needs no combiner, no vote and no threshold. The historical text is kept below for the record. — Both raise the per-bet result on dev, from different representations — fixed kernels over five channels versus eight learned price curves. If their scores rank bets differently there is something to gain; if they agree, nothing. **Measure the rank correlation of the two scores on the same candidates FIRST** — that one number decides whether any combiner is worth building. Forms, in rising cost: `AND` (both must approve), rank-average or weighted sum of scores, or a trained second-stage model taking both scores plus context. `filters.py` already has an `Ensemble` class with `all` / `any` / `vote` and rank-average scoring, written and never run. Two constraints that shape the choice: (1) with slots full 70.5% of days, `AND` is strictly more selective and would need each member's threshold LOOSENED to keep the book invested — the k=0.90 row above is what over-selection costs; (2) the shapelet does not survive the continuous path, so any combiner that leans on it inherits that fragility. A weighted form that can down-weight a member is safer than `AND`, and a trained combiner needs its own walk-forward or it just overfits the pair |

## Standing rules about the process itself

- **Both periods or nothing.** A mechanism that helps one period and hurts the other is a regime bet (E2, Code 33 ranking).
- **A worse result is not a licence to re-tune.** §14's constants were switched off whole rather than loosened toward what the history prefers.
- **No profit caps.** E1's rejection is permanent: the edge is the right tail.
- **Do not re-propose** risk-based sizing, pyramiding, progressive exposure or the market dimmer without new judgement-bearing inputs — not new curves.
- **Both periods have been seen.** Everything from v3 onward is post-hoc by construction. The forward paper ledger is the only honest judge.

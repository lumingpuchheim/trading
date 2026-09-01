# trading — working rules

## The goal

**Find an ensembled investment strategy out of weak rankers.**

No single arm here beats holding the index, and none is expected to. The
unit is a **ranker**: candidate in, one predicted growth rate out, and
the slot decision reads that number directly (`RANKER_SPEC.md`). It used
to be a filter, and this paragraph used to say the arms "earn what they
earn by declining bad trades, not by finding good ones", at the base
rate, across four model families. That described the veto architecture
retired on 2026-08-31, and those verdicts were voided with it: the
pipeline that measured them could not turn a better model into a better
book, so it could not rule anything out either. Nothing about the
transforms is currently ruled in or out. Re-measure under the ranker
first.

The target is still a **combination** of arms that are individually weak
and disagree with each other — MiniRocket and the shapelet had rank
correlation -0.116, which is the raw material. Under the ranker a
combination needs no combiner, no vote and no threshold: it is both
transforms' features in the same regression, or a ranker over rankers.

Judge work against that goal: a new transform is interesting because it
adds a *different* mistake, not because its solo row is the highest —
and every arm is read against `StrengthScore`, the do-nothing control
that reproduces today's book exactly or the run stops.

## Principle: fail fast, iterate fast — starting from scratch is not allowed

Every expensive step is cached, and the cache is what makes iteration
possible. Breaking it is the most costly mistake available in this repo,
and it is silent.

**Before changing anything, ask what it invalidates.**

| step | cost | cached as | keyed on |
|---|---|---|---|
| feature transform | 4-20 min, 0.9-3.8 GB | `results/.fitcache/feats_*.npy` | the transform key: filter, dilations, its own knobs, input shape |
| per-block ridge fit | minutes to hours | `results/.fitcache/block_*.npz` | transform key + alpha + solver + the training/eval masks |
| a whole model | seconds to load | `results/.fitcache/model_*.npz` | training end date + lookback + alpha + solver |
| the simulation | seconds | not cached, and does not need to be | — |

Rules that follow from the table:

1. **Never add a field to a cache key without checking what it costs.**
   A key is a content hash; one extra field re-runs every entry. If the
   field is already implied by another (the transform key fixes the
   feature count, hence the alpha route), adding it buys nothing and
   throws away hours.
2. **A killed run resumes.** Fits are written block by block, so an
   interrupted walk-forward restarts at the first unfinished block. Never
   delete `results/.fitcache` to "be safe".
3. **An ensemble fits nothing.** It reads its members' cached per-block
   scores and combines them (`filter_backtest.combine`). A member must
   therefore pick the same solver inside an ensemble that it picked
   standalone, or the keys diverge and everything refits.
4. **Test the widest arm before changing a default.** `alpha='cv'` was
   made the default having been checked only on 4,200-feature arms;
   `RidgeClassifierCV` has no `solver` argument and cannot run at 16,800.
   That default would have failed an hour into a run.
5. **Fail fast on the short window first.** `--until 2012-12-31` runs the
   whole path in a few minutes. Use it to prove a change works before
   spending the full record on it.

## Every result carries its configuration

The fit configuration lives in `bets_common` (`EMBARGO_DAYS`,
`LOOKBACK_YEARS`, `AUX_Q`, `MIN_TRAIN`, `T_FLOOR`) and every run prints
it before any number: `filter_backtest.py` opens with the `RANKER` line
carrying the embargo, the window, the target, the floor, the estimator,
the arms and the feature count.

(This section used to cite an `EVALUATION_SPEC.md` Rule 4 and constants
`ALPHA` / `USE_MODEL_STORE` / `SOLVER`. Neither survived the revert to
`56ework`; the spec has three rules and `bets_common` has no such
constants. Corrected 2026-08-31 rather than left pointing at nothing.)

A number quoted without its configuration is not a result. Two scripts
once reported the same nominal arm as +291% and +530% because they
disagreed about two unprinted defaults.

## Held fixed across arms, unless the run says otherwise

`embargo=400d`, `window=5y rolling` (`--lookback 5` — the measured
best schedule, worth more than any target rewrite; expanding dilutes
late folds and is kept only for comparisons), `target=ln(y)` (the
value target; the ratio target `ln(y)/t` and its floor are retired,
RANKER_SPEC Amendments 4-5), dilations `1,2,4,8,16`. Arms differ by
the transform and nothing else. The `strength` control must reproduce
today's book row for row and **+291.5%** over 2007-01-03 .. 2026-08-27
before a fitted row from that run is read — `filter_backtest.py`
checks it in-process and exits if it does not.

## How to test performance

The canonical run:

    python filter_backtest.py --target value --arms strength,rocket --lookback 5

Read the output in this order, and stop at the first failure:

1. **The control line.** It must say `IDENTICAL, +291.5%`. If it does
   not, the run is broken and NOTHING below it may be read.
2. **The fold lines** (train / out-of-fold, one per year). The gate
   for any fitted arm is fold-level: does it beat the constant
   (predict the training mean) out of fold, and in how many of 15
   folds? An arm that cannot beat a constant has no business in a
   book; simulate no book for it.
3. **The book table**, judged on ONE column: **geo/bet** — per-bet
   geometric mean — against the baselines below (operator decision,
   2026-09-01: growth objective; drawdown is reported, never judged;
   a smoother ride buys nothing because bet-size conversion is
   measured shut).
4. **Total return decides nothing between arms.** The 90% band of
   200 within-day score shuffles is **276 points wide** on total
   return, and the incumbent itself sits at the 86th percentile of
   random reorderings. Every total in this file's history, both
   directions, sat inside that band. Differences in `total` are
   path noise unless they clear a freshly measured permutation band.

### How expected value is calculated

One bet = one POSITION (not one trade row — a split winner writes two
rows). Its multiple `y` = euros returned per euro committed:

    y = sum over the position's rows of  weight x (1 + ret_net)
        + dividends collected / cost          (geostats.bet_multiples)

**Expected value per bet = the geometric mean, one vote per bet:**

    EV = exp( mean( ln y ) ) - 1              (geostats.geo_per_bet)

Never an arithmetic mean, never an average over rows. For a TRAINING
target the same quantity per signal: `ln(y)`, and a split bet counts
each leg at 0.5 — `0.5*ln(y_half) + 0.5*ln(y_rest)` (operator rule).
Per slot-day the book prints `per_day = exp(sum ln y / sum t)`, a
ratio of sums.

### The baselines every test must print and compare against

Whole record, 2007-01-03 .. 2026-08-27, fees on, 10 x 10% slots:

| baseline | total | ann/yr | maxDD | bets | geo/bet | per_day |
|---|---|---|---|---|---|---|
| `strength` (do-nothing control) | +291.5% | +7.2% | -30.2% | 1,252 | +0.57% | +0.0337% |
| `rocket` value, 5y window — best fitted arm | +294.5% | +7.3% | -27.8% | 1,238 | **+0.67%** | +0.0373% |
| the whole candidate pool | — | — | — | 55,737 | +0.52% | +0.0177% |
| SPY total return, 2009-2026 one path | — | **+14.81%** | -33.7% | 1 | — | — |

Fitted years only (2012-01-03 .. 2026-08-27), for arms that keep the
control ordering before 2012: `strength` +292.4%, +9.8%/yr, geo/bet
+0.82%.

The number a new arm must beat is the rocket row's **geo/bet +0.67%**
(whole record). Beating `strength`'s total is NOT a meaningful claim
(see the noise band above); beating SPY has never been achieved by
any configuration and is the honest external bar. The forward paper
ledger is the only judge of totals; one historical path cannot
resolve them.

## Tunable

`--arms`, the transform's own size knob (`--mr-biases`,
`--groups`/`--kernels`), `--alpha` and `--floor`. `--keeps` is gone with
the veto. Nothing else is a knob; if something else needs changing, it is
a decision, and it belongs in `DECISIONS.md`.

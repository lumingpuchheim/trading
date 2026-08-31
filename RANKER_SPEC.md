# Ranker spec — four arms, one loss, no downstream picks

Specifies the replacement architecture decided in `DECISIONS.md` ("The
filter architecture is wrong", 2026-08-31). This file is the
implementation contract; the decision register holds the why.

## The shape

A **ranker** is a pure scoring function: candidate in, one number out —
the predicted growth rate of a euro spent on that candidate, in
ln-per-trading-day. Rankers never trade. The one component that trades
is `simulate()`, which fills each day's free slots from the top of the
scores it was handed:

    take = top free-slots of the day's usable pool,
           by score descending, ticker ascending as the only tie

No veto, no threshold, no `--keeps`, no strength keys in the sort, and
no trading code inside any ranker — the portfolio mechanics exist once,
above all of them.

    class Ranker:
        def fit(self, F, r): ...      # features, realised rates
        def score(self, F): ...       # -> one float per row

`filter_backtest.py` owns the walk-forward loop: it fits each fold,
assembles the (days x tickers) score matrix over the ledger's
orderable signals, and calls `simulate(scores=...)`. Rankers see
matrices, never the calendar.

## The four arms

    REGISTRY = {'strength': StrengthScore,      # the do-nothing arm
                'rocket':      MiniRocket  + ridge regression,
                'multirocket': MultiRocket + ridge regression,
                'hydra':       Hydra       + ridge regression}

Ensembles are the agreed next step, not this one. The interface already
admits them (a ranker over rankers), so nothing here forecloses it.

### StrengthScore — the do-nothing arm

Fits nothing. It encodes exactly the ordering the book uses today —
`rsl_hi` desc, then `weak` desc (NaN last), then `rs` desc (NaN last),
then ticker — so that "run the new architecture and change nothing"
reproduces today's AllPass book.

Encoding, so the lexicographic sort survives as one float: per day,
rank `weak` and `rs` over that day's signals (equal values share a
rank, so ties still fall through to the next key), then compose

    score = (rsl_hi * B + rank_weak) * B + rank_rs      B = max
                                                        signals/day + 1

Integer-valued float64, exact far beyond any B here.

**This arm is the control, and it must reproduce today's AllPass run
EXACTLY: the same trades file row for row, and +291.5% over
2007-01-03 .. 2026-08-27.** Not approximately — the encoding above has
no freedom, so any difference is a bug in the score path. No fitted
arm's row may be read from a run whose StrengthScore arm has not
reproduced. This subsumes HANDOVER trap 1.

### The fitted arms

The transforms are exactly today's — same kernels, same dilations, same
cached features (`results/.fitcache/feats_*`; the transform keys do not
change, so nothing re-runs). Behind each transform:

- **Features**: the transform's output, plus the three keys the old
  picker spent — `rsl_hi`, `weak`, `rs` — each NaN-heavy column carried
  as (value filled with 0, finite-indicator) pairs. This is what makes
  the design safe: a fit that puts weight on the keys and nothing else
  IS today's ordering, so the baseline is inside the hypothesis space.
- **Fit**: `RidgeCV` regression, closed form, on the target below.
  Standardisation from the fold's own training rows, as always.
- **Schedule**: the walk-forward of `bets_common.year_blocks` —
  expanding window, 400-day embargo, no calendar constant. Unchanged.

## The target

Defined in `DECISIONS.md` ("The target, carefully") and restated here
as the contract: `r = ln(y)/t`, one vote per bet, `t` in trading days
floored at `t_floor`. A split bet sums both wins, each stream at its
own rate, each at its capital share `f` (0.5 under the +20% half-sale):

    r = f·ln(y_half)/t_half + (1-f)·ln(y_rest)/t
    y_rest = (y - f·y_half) / (1-f)

The ledger must carry `y`, `days_held`, `half_frac`, `y_half`,
`half_days_held` per signal (`minervini_bets.py`). Equal weight per
bet everywhere; the euro-day weighting was rejected, do not re-propose.

## What every run shows

These fits are closed form — there are no epochs, so there is no loss
curve. The training record is **one live line per fold**, printed as
the fold completes, train and out-of-fold side by side:

    2013  train 11,204   mse 4.31e-4 / 4.62e-4   spear +0.08 / +0.05   auc 0.61 / 0.55

- **mse** — the loss itself, on the rate.
- **spear** — Spearman rank correlation of predicted vs realised rate:
  the quantity the slot decision actually uses.
- **auc** — score vs the binary label "r in the top 20% of this fold's
  TRAINING rates"; the same training-window cut grades the fold's own
  block. Diagnostic only; nothing trains on it.

Before any number, the run prints its configuration on one line, in
the same words from every script:

    RANKER  embargo=400d  window=expanding  target=ln(y)/t  floor=3d
            estimator=ridge-cv  arm=rocket  features=4,203

and each arm ends with its book: total return, ann, maxDD, trades and
bets, and

    G_day = exp( mean of r over the bets taken )

reported beside `geo_per_bet` and next to the same two numbers for the
whole candidate pool. Everything goes to the console; no report files.

## What retires

- `Filter.decide`, `threshold`, `keep`, `--keeps`, and the quantile
  veto. Selectivity is slot capacity and nothing else.
- The binary `aux` label as a training target, and the jackpot
  precision/recall machinery.
- `simulate()`'s `key()` sort for scored runs (it survives only for the
  legacy no-score path), and the boolean `gate`.

## Caches

Feature caches survive untouched — the transform keys are unchanged.
Ridge fits are new entries (a regression is a different estimator);
that refit is the price of the architecture and is paid once.

## Acceptance

1. StrengthScore reproduces today's AllPass trades file row for row and
   +291.5% over 2007-01-03 .. 2026-08-27, on every run, before any
   fitted row is read.
2. Every year of the record is scored, and no fold's model trained
   within 400 days of its block (the `test_walk_forward.py` pattern).
3. Every arm prints the config line, one metrics line per fold with the
   six numbers, and the closing book with `G_day`.
4. A split bet's target reproduces the leg blend above bit for bit on a
   hand-built example (unit test).
5. `grep -n keeps filter_backtest.py` finds nothing but history.

---

## As built — 2026-08-31

The architecture above stands. Six things were decided at the keyboard
that the spec did not settle, and one of them is a real amendment.

**Two arms, not four.** `strength` and `rocket` only, on the operator's
instruction ("implement MiniRocket only and compare with the baseline
do_nothing"). `minervini_multirocket.py` and `minervini_hydra.py` did
not survive the revert to `56ework`, so those two arms have no
transform to sit behind; their cached features are still in
`results/.fitcache` and are orphans until the modules are rewritten.
`filter_backtest.py` names them and refuses rather than guessing.

**THE STRENGTH KEYS SURVIVE IN ONE PLACE, AND IT IS NOT THE SLOT
DECISION.** `simulate()` step 4 arms a watchlist of at most 100 names
per day and lets `max_positions` bind at the fill; the pool is larger
than 100 on **1,993 of 4,944 days**, so something has to choose which
100 get armed. The ranker's score cannot: it belongs to tomorrow's
close, and this is tonight. Dropping the cap instead is not free —
measured, the control book moves **+291.5% -> +304.4%**, so the cap is
part of what "today's book" means and cannot be removed inside a change
that has to reproduce it. So the keys order the WATCHLIST, and the slot
decision (step 3b) never sees them. Removing that last use is its own
change, with its own control.

**The slot decision moved to the fill, not the order.** `simulate()`
sorts the day's *fillable* candidates by score at the close they fill
at, which is the only place a score built from a window ending that day
is causal. The control encodes keys read the day the order was placed —
one day earlier, which is where `simulate()` reads them today — so it
is causal by a day's margin and reproduces the incumbent permutation
exactly.

**Alpha by exact leave-one-out, not GCV.** *(Itself superseded hours
later by Amendment 1 below: exact leave-one-out fixed the degeneracy
described here and was still the wrong criterion, because the unit it
leaves out is one bet and a bet's twins stay behind. The paragraph is
kept because the GCV failure it records is real and would otherwise be
rediscovered.)* GCV replaces the hat
diagonal by its mean, and that fails precisely here: three of the first
four folds have fewer rows than the 4,206 features (2,174 in 2009), the
fit interpolates, `df -> n`, and the criterion becomes 0/0. It chose
alpha=0.001 for 2012 and left an out-of-fold mse of **0.78** against a
training mse of 1.9e-05. The exact denominator does not degenerate;
with it the same folds choose 100 and 316, which is what the retired
classifier chose in 134 of 136 fits. `rankers.loo_ridge` computes it in
one eigendecomposition per fold plus one chunked pass over the rows, so
peak memory stays at the p x p Gram (142 MB) rather than sklearn's
n x p SVD (~4 GB). Printed as `estimator=ridge-loocv`.

**Years with no model keep the control ordering, in every arm.** The
first fittable block is 2009; 2007 and 2008 have too little history
behind them. Those years therefore run on `StrengthScore` in the fitted
arms too, so the arms differ exactly where the model exists and nowhere
else. The alternative — leaving them unscored — would have made them
alphabetical, which is neither arm.

**`gate` and `filters.py` are not deleted.** `equity_vs_spy.py`,
`filter_agreement.py` and `rocket_ev.py` still reproduce numbers from
the retired chain and would break. Both carry a header saying what they
are. Nothing new may use them, and `filter_backtest.py` refuses to take
`scores` and `gate` in the same call.

### One property of the target worth knowing before reading `G_day`

`G_day` is negative for the book AND for the whole candidate pool
(-0.33%/day against -0.24%/day on the short window), while the same
bets are +0.52% per bet geometrically. Both are correct. One vote per
bet on a PER-DAY rate is dominated by the bets that close fastest, and
those are stop-outs: a -8% stop that fills on day three is -0.028/day,
where a +40% winner held nine months is +0.0015/day. The floor caps how
far that goes but does not reverse it. So `G_day` is a ranking-quality
number, not a statement about what a euro did — `geo/bet` is that — and
the two are printed side by side, each beside the pool, for exactly
that reason.

---

## Amendment 1 — alpha by grouped, purged cross-validation (2026-08-31)

Supersedes "Alpha by exact leave-one-out" above. The mechanics of that
paragraph were right and its criterion was wrong: the measured run
(DECISIONS.md, "The ranker, measured") chose alpha=100 by leave-one-out
while the out-of-fold loss said a constant beat the fit in all 18
folds. Leave-ONE-bet-out cannot see that, because the bets are not
independent: ~12 share each trading day's market move, and their
252-day windows overlap almost completely. Hiding one bet leaves its
twins — same day, same stock weeks apart, same market month — in the
training set, so the criterion recognises the held-out bet rather than
predicting it, and it under-regularises with full confidence.

The fix hides a bet together with its twins. Not the outer block: the
outer out-of-fold loss may never judge alpha, or the block leaks into
model selection and the walk-forward stops being one.

### The criterion

Inside each outer fold's training window, group the training bets by
the **calendar year of entry**. For each candidate alpha and each
usable held-out year Y:

    inner-train = training bets whose entry is more than EMBARGO
                  (400) calendar days from BOTH boundaries of Y
    fit ridge(alpha) on inner-train, mse per bet on Y's bets

Pool the held-out bets across years — one vote per bet, the same
convention as everywhere else — and take the alpha with the smallest
pooled mse. The purge is symmetric on purpose: the left side keeps Y's
outcomes out of what the inner fit trains on (the embargo exceeds the
longest hold), the right side keeps out the bets whose feature windows
look back into Y and whose holding periods overlap Y's unresolved
tails. One constant, already defined, both directions.

**Usability.** A held-out year counts only if its inner-train keeps at
least `INNER_MIN = 1,000` rows after the purge. A fold needs at least
two usable held-out years to choose alpha honestly; below that it
fits nothing and keeps the control ordering, exactly as 2007-2008 do
today. Consequence, stated rather than hidden: the purge is brutal on
the earliest windows, so the first fitted year will move later than
2009. Where it lands is measured by the run, not assumed here.

**The grid grows upward**: `logspace(-3, 8, 23)`, so the criterion is
able to say "shrink almost everything" if that is the truth. The old
top of 1e5 presumed an answer.

### Computation

Gram matrices add over rows, which is the whole trick. One pass builds
the full Gram `G_total = X'X` and `b_total = X'y` (float32 sgemm
accumulated into float64, as now). Per held-out year, one pass over
the removed rows (Y plus its purge margins) builds `G_out`, and

    G_inner = G_total - G_out,   b_inner = b_total - b_out

One eigendecomposition of `G_inner` then serves the entire alpha grid
for that year. Peak memory stays at a few p x p float64 matrices
(~142 MB each at p = 4,206), never an n x p copy. Cost: one
eigh(4,206) per usable held-out year per outer fold — minutes per
fold, roughly 150-250 decompositions over the full record. Prove it on
the short window first; note `--until 2012-12-31` may contain ZERO
fitted folds under the usability rule, so the fail-fast window for
this change is `--until 2014-12-31`.

### Interface and bookkeeping

- `Ranker.fit(F, r)` gains the entry dates: `fit(F, r, when)`. The
  grouping is the fit's own business; the driver already has the
  dates. `StrengthScore` ignores the argument.
- Cache entries are keyed under a NEW name (`ridge-ycv`) with the grid
  in the key, so nothing collides with the measured `ridge-loo` run,
  which stays on disk as the record behind the DECISIONS row.
- The banner prints `estimator=ridge-ycv`; the fold line appends the
  count of usable held-out years, e.g. `alpha 3162 (9y)`. A fold that
  keeps the control ordering says so on its line.

Nothing else moves: target, floor, features, outer schedule, fold-line
metrics and the control invariant are all as specified above.

### Acceptance

1. A test asserts no inner-train row's entry is within 400 calendar
   days of its held-out year, for every (fold, year) pair actually
   used.
2. Gram subtraction equals the directly computed inner Gram on a small
   case, to float tolerance (unit test).
3. The twin test, pinning the mechanism this amendment exists for:
   synthetic data where same-day rows share their noise. Leave-one-out
   must choose a smaller alpha than the grouped criterion on the same
   matrix, and the grouped choice must have the smaller error on a
   held-out continuation. If this test cannot tell them apart, the
   implementation missed the point.
4. The control reproduces, unchanged (Acceptance 1 above); the
   StrengthScore book is untouched by this amendment.
5. The measured `ridge-loo` fold metrics remain loadable from the
   cache after the change (the record is not overwritten).

---

## Amendment 2 — the keys arms: attribute before building more (2026-08-31)

The measured rocket book (+234.6%, geo/bet +0.76% against the
control's +0.57%) differs from do-nothing in TWO ways at once: it
learns weights instead of sorting, and it adds 4,200 kernel features
on top of the keys. Nothing measured so far separates those. This
amendment separates them — and prices the old sort's lexicographic
structure — in one run that costs minutes, because none of it needs a
transform.

### Two new arms

    'keys'    RidgeRanker on the 6 key columns alone:
              (rsl_hi, weak, rs) as value/finite pairs, nothing else
    'keys+'   the same 6 plus two interactions:
              rsl_hi x rs_value, rsl_hi x rs_finite  (8 columns)

`keys+` exists because the old order is lexicographic and a linear
blend cannot be that in general. With a BINARY first key it can —
`C*rsl_hi + rs` with C beyond rs's range reproduces the effective
order exactly — and the interactions let `rs` carry a different slope
inside each `rsl_hi` tier, the realistic bent version of the old
structure. What no linear form can reach is the middle priority of
`weak`, which decided 8 of 55,737 signals; that residual is accepted,
not modelled.

Everything else is the rocket arm's, verbatim: same target, same
grouped purged CV (Amendment 1), same schedule, same usability rule.
**The tiny feature count must NOT be allowed an earlier first fitted
year**: all fitted arms share the same fitted years, so the four books
differ by their features and nowhere else. Comparability over
coverage.

### The run

    python filter_backtest.py --arms strength,keys,keys+,rocket

One command, four books. The rocket folds are cached; a keys fold's
eigendecomposition is 6x6; the run costs the panel build plus four
simulations. The block-cache key must carry the arm's feature identity
(`keys6` / `keys8` / `rocket`) — `src` alone no longer names the
feature matrix.

### Reading the four books — fixed before the run, so it cannot drift

| observation | conclusion, and the next step it buys |
|---|---|
| keys ≈ strength | reweighing the old information changes nothing; rocket−strength is the kernels' — rewriting MultiRocket/Hydra is justified |
| keys < strength, keys+ recovers most of it | the tier structure was load-bearing; the interaction columns go into every fitted arm from now on |
| keys ≈ rocket | the kernels are decoration at this target; the transform question CLOSES for this data, and the next lever is new information in the ledger, not new geometry over the same windows |
| keys > strength | the fixed priorities were mis-weighted all along; whatever is built next starts from keys, and rocket must beat IT, not strength |

### Acceptance

1. The control reproduces, as always, before any fitted row is read.
2. The banner names each arm with its feature count (`features=6`,
   `features=8`).
3. All fitted arms report the same fitted years; a run where they
   differ is invalid and no book from it may be quoted.
4. Fold lines and book rows print in the shared format, so the four
   arms read side by side in one console.

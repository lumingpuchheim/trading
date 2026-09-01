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

---

## Amendment 3 — four measurements before any new transform (2026-08-31)

MultiRocket and Hydra are NOT to be built until these four have run
and been read. Each is a run, not a model; together they either
justify the two rewrites or kill them cheaply. Standing facts they
build on: rocket picks better bets and fewer of them (+0.76%/bet,
876 vs 988 on the fitted years, book total below the control's); the
bet-count gap is hold duration, not declining (no threshold exists,
invested time is HIGHER); capital-counted slots were measured and
rejected (DECISIONS.md, OUT table).

### 3.1 The blend — the decisive run

A rank-average ensemble of the two orderings that exist:

    per day, over the day's pool:
      p_strength = percentile rank of the strength ordering
      p_rocket   = percentile rank of the rocket scores
      score      = w * p_rocket + (1-w) * p_strength,  w in {0.25, 0.5, 0.75}

Per-day percentiles, not raw values — the two scales are
incommensurable (one is an integer encoding, one is a rate). Both
inputs are readable at the fill close (strength's keys a day earlier),
so the blend inherits causality. No new fits: rocket's cached
out-of-fold scores and the strength matrix are both already on disk.

Reading, fixed now: any w beating the strength book on the agreed
columns → the quality-plus-throughput mechanism works, and each
DECORRELATED additional arm has a measurable slot to improve — build
the transforms. Every w landing between its members → blending
dilutes; more same-window transforms would dilute too; the rewrites
are cancelled and the next lever is information, not geometry.

### 3.2 Slot count under the ranker

`slot_sweep`'s recorded row (20 x 5%: same total, twice the trades,
maxDD -30.2% -> -21.7%, under the strength ordering) has never been
run with a fitted arm. Rocket's failure mode is exactly too few
draws. Run strength and rocket at 15 x 6.67% and 20 x 5% (config
values `max_positions` / `equal_weight_fraction`; keep 100% gross —
the bet-size scan rejected changing gross exposure). Reading: does
rocket's per-bet edge survive smaller bets and convert to total at
higher throughput? Note `geo/bet` mechanically drops for every arm at
5% bets; compare arms within a config, never across configs.

### 3.3 The noise yardstick, then the verdict bar

We watched a 40-point total-return difference flip sign with the
measurement path. Before any new arm is judged: shuffle the rocket
scores within each day (pool preserved, N >= 100 permuted books,
seconds each) and read the spread of totals. Differences inside that
spread are unreadable and may not decide anything. Then the verdict
bar for MultiRocket / Hydra is PRE-REGISTERED here: they are judged on
per-bet geo, `G_day`, maxDD, loss-vs-constant fold count, and rank
correlation against rocket's scores (decorrelation is the point of
building them); total return counts only outside the permutation
spread. A transform whose scores correlate > ~0.8 with rocket's adds
nothing an ensemble can use, whatever its book says.

### 3.4 The natural zero, along for the ride

`--min-score 0` is implemented and has never run: a slot stays in
cash when the best candidate's predicted rate is negative. One flag
on the 3.2 runs. Reading: if the model's "don't buy" has value it
shows as invested down, drawdown down, total held; if not, that is
worth one recorded line and the flag stays off.

### Order and cost

3.3's permutations and 3.1's blends share one session with 3.2's
configs; 3.4 rides on 3.2. Everything reuses cached folds — no fit is
re-run anywhere. If 3.1 and 3.2 both fail, no third transform fixes
throughput either, and the register should say where the question
moved: new information into the ledger, or sizing by score.

---

## Amendment 4 — the rent target: profit minus slot rent (2026-08-31)

Supersedes the ratio target `ln(y)/t` AS THE TRAINED OBJECTIVE. The
ratio stays reportable (`G_day` keeps its definition), but nothing
trains on it any more.

### Why the ratio target was the wrong shape

A slot's long-run growth is TOTAL log-profit over TOTAL days across
the bets that occupy it — a ratio of sums. The trained target was the
average of each bet's OWN ratio, and the two disagree exactly where
the money is:

    +10% in 20 days    ratio +0.0048/day   <- ratio target's favourite
    +40% in 180 days   ratio +0.0019/day   <- the jackpot, ranked BELOW it
    -8% stop in 3 days ratio -0.028/day    <- dominates the loss; costs
                                              the account 0.8% and frees
                                              the slot in days

The ratio target is anti-right-tail and stop-out-obsessed. The
measured book agreed: better bets, longer holds, fewer draws, lower
total (DECISIONS.md, "The ranker, measured"; the blend runs). The 3.1
blend proved the two orderings compose on quality, and 3.2 attacks
throughput — this amendment fixes what the model is FOR.

### The target

    r  =  ln(y)  −  c · t

`y` = euros returned per euro committed (dividends in, as before);
`t` = trading days held, **no floor — `t_floor` is void**, nothing
divides by days any more. A split bet decomposes as before: multiples
arithmetically by capital share, and each stream owes rent for its own
days:

    r = f·(ln(y_half) − c·t_half) + (1−f)·(ln(y_rest) − c·t)

A bet pays its profit and owes rent for every day it blocks the slot.
Ranking by expected `r` is the greedy-optimal slot decision for the
ratio-of-sums objective (the standard linearisation of a fractional
objective). Long holds are charged linearly — the push the old target
never had — and the jackpot outranks the fast small win again.

### The two heads, and why the rent sweep is free

Ridge is linear in its target and the target is linear in `c`, so fit
TWO ridges per fold — one on `ln(y)`, one on `t` — and the rent model
for EVERY `c` is their difference:

    score(c) = predicted_profit − c · predicted_days

One fit pair per fold serves the whole `c` grid, and the heads are
diagnostics in their own right: `predicted_days` shows directly
whether the model steers toward long holds. Alpha is chosen per head
by the Amendment 1 criterion, unchanged (the eigendecomposition is
target-independent, so this costs one extra back-substitution, not a
second decomposition).

### Finding `c`: derived, not tuned

Per fold, from that fold's own training window:

    c0 = mean(ln y) / mean(t)          over the training bets
    iterate: rank training bets by score(c_k); take the top slice at
             the book's own selectivity (the taken/orderable fraction
             of the training window); c_{k+1} = that slice's
             sum(ln y) / sum(t); stop when the change is < 5% or after
             3 rounds.

This is the standard fractional-programming iteration; it converges
monotonically and never leaves the training window. `c` is therefore
a per-fold derived quantity, printed on the fold line — NOT a knob.

**Sensitivity line, mandatory:** each arm's book is also run at `c/2`
and `2c` (free — same two heads) and the three totals print together.
A book that swings hard across that band is fragile and the run says
so. Theory expects the ranking to move slowly with `c`; verify, don't
assume.

**Held in reserve, only if the sensitivity line shows `c` matters:**
choose `c` jointly with alpha on the Amendment 1 grouped purged years,
maximising the held-out top-slice `sum(ln y)/sum(t)` — the true
objective, inside the training window. `c` may NEVER be chosen by the
outer book's total: that is tuning on the one path we have.

### What this voids

- `t_floor` and its rationale (fast stop-outs no longer explode a
  denominator). The `--floor` flag dies.
- The ratio target as anything trained. `G_day` remains a REPORTED
  column with its existing definition, beside a new one: `G_rent`,
  the mean of `r` over the bets taken, same shape as `G_day`.
- Nothing else: exits, schedule, grouped CV, control, evaluation
  through `geostats.py` all stand. MultiRocket/Hydra stay frozen
  until the rent-trained rocket is measured (Amendment 3 order).

### Acceptance

1. The control reproduces, as always.
2. Equivalence test: at fixed `c`, the two-head difference reproduces
   a direct single ridge on `ln(y) − c·t` to float tolerance — the
   linearity claim, pinned.
3. A hand-built split-bet example reproduces the leg decomposition
   above bit for bit.
4. The fold line prints the derived `c`, its iteration count, and
   both heads' metrics; the book prints the `c/2, c, 2c` sensitivity
   line.
5. `grep -n floor filter_backtest.py` finds nothing load-bearing.
6. The `ridge-ycv` fold caches of the ratio era remain loadable (the
   record is not overwritten); rent-era caches key under a new name
   that includes both heads and `c`'s derivation.

---

## Amendment 5 — train one thing (2026-08-31)

Amendment 4's target stands word for word. Its TWO-HEAD CONSTRUCTION
is retired as the decision path — a specification error, owned here.

### What the measured run proved about the construction

The two heads were regularised independently: the grouped CV judged
the profit head alone (found noise, shrank it to a constant, alpha at
the 1e8 ceiling in all 15 folds) and the days head alone (found
signal, kept it). But profit and duration are correlated in the data
— long holds are the survivors and winners, short holds include the
stop-outs — so the subtraction `constant − c·predicted_days` charged
duration its rent while ignoring duration's profit association, and
the composed score ranked its own target NEGATIVELY out of fold
(spearman ~ −0.1 in most folds). Two individually-optimal parts,
composed into worse than nothing.

The rule that generalises, alongside "one bet, one multiple, one
average": **a decision comes from the single best estimate of the
decision quantity, never from separately tuned estimates of its
parts.**

### The construction that replaces it

Per fold, ONE ridge, fitted directly on the rent number:

    row:     all features (transform output + the key columns, as now)
    answer:  r = ln(y) − c·t          (split legs as Amendment 4)

- `c` is DERIVED exactly as Amendment 4 built it — the Dinkelbach
  iteration at the book's own selectivity — printed, never a knob.
  (`c` may not be chosen by CV on r's own error: each `c` defines a
  different target scale, so those errors are not comparable. The
  held-out top-slice ratio selection stays in reserve, as before.)
- Alpha is chosen by the Amendment 1 grouped purged criterion, judged
  on the WHOLE rent number's held-out error — never per part.
- The score is that one model's prediction. Nothing subtracts two
  fits anywhere in the decision path. The two heads may still be
  FITTED AND PRINTED as diagnostics — they are how the flat profit
  half was discovered — but no score may be composed from them.
- The `c/2, c, 2c` sensitivity band stays mandatory: three single
  fits per fold, each with its own CV'd alpha. Cheap; honest.

Everything else stands: features, exits, schedule, control,
evaluation columns (`geo/bet`, `G_day`, `G_rent`), the Amendment 3
noise yardstick.

### The reading, fixed before the run

Totals are inside the measured 276-point permutation band and decide
nothing. The arm is judged on maxDD, geo/bet and `G_rent`, plus the
fold lines' loss-vs-constant count on r.

1. **The fit beats the constant in most folds and the ordering holds
   its sign** → the first right-shaped, right-composed model of these
   features; measure it on the readable columns and only then revisit
   Amendment 3's frozen transforms.
2. **The fit shrinks to a constant** → flat scores, the book keeps
   the control ordering, and that is the CORRECT behaviour, not a
   failure: with no estimable difference between candidates, the
   right amount of reordering is none. Record the closing verdict:
   these window features do not improve the slot decision for
   growth; the program's proven yield is the ratio-era
   crash-avoidance signal and its drawdown cut; the next euro goes
   to new information in the ledger, not new geometry over the same
   windows.

### Acceptance

1. The control reproduces, as always, before any fitted row is read.
2. The existing equivalence test is repurposed as the guard it always
   was: a single ridge on `ln(y) − c·t` at one alpha equals the
   two-head difference at that SAME alpha — and the decision path is
   the single ridge, verifiable by reading `RentRanker.score`.
3. No score anywhere is a difference of separately-regularised fits.
4. Fold lines print alpha, `c`, its iteration count, and train /
   out-of-fold error against the constant, on r.
5. The Amendment 4 two-head caches remain loadable as the record;
   single-fit caches key under their own name.

---

## Amendment 6 — the fixed-horizon experiment (2026-08-31)

Operator decision: force-sell every position `H` trading days after
entry, and train on EXPECTED VALUE — the plain per-bet log multiple,
no rent, no ratio, no floor. With the holding time capped, "profit
per bet" and "profit per slot-time" stop being different rankings, so
the training-vs-trading mismatch that consumed Amendments 4 and 5 is
removed by the trading rule instead of by loss engineering.

Background that makes this worth running: the value-shaped model was
the one that picked demonstrably better bets than strength (geo/bet
+1.18% vs +0.82% on the fitted years) and lost only by trading too
few. The cap attacks "too few" directly.

### The one change to the book

A new exit, `max_hold`: at the close of the `H`-th trading day after
entry, any remaining position is marked for exit and fills at the next
open, like the other slow exits. **Everything else in the ladder is
unchanged** — stop, tennis window, egg, breakeven, SMA50, the +20%
half-sale all stay — so the experiment changes exactly one thing and
its verdict attributes. Config knob `max_hold_days`; absent or 0 = off,
and with it off the +291.5% control reproduces bit for bit.

`H = 42` is the primary (about two months: above the commission
floor, below the obvious tail-amputation zone, roughly twice the
current median hold), with `21` and `63` as the band — one number is
a guess, three are a measurement.

### The target

Per signal, under the amended ladder:

    r = ln(y)                                    unsplit
    r = 0.5·ln(y_half) + 0.5·ln(y_rest)          split (operator rule:
                                                 each leg counts 0.5)

One model per fold on that number — the Amendment 5 rule stands, no
composition of parts — same features, same grouped-CV alpha, same
schedule. Evaluation per bet stays `geostats` (geometric mean, one
vote per bet). Labels are rebuilt per `H` (the exits changed, so `y`,
`y_half`, `days_held` change); the WINDOWS do not (features are
entry-day history), so every feature cache survives and only labels
and fits are re-made.

### New features: the voided data, admitted through two gates

Candidates, all already fetched and in the panel: the Code 33
fundamental legs (EPS, sales, margins), `group_pct` (industry-group
strength), earnings surprise. Their old rejections are void — they
were hard gates in the retired architecture; as feature columns under
this target they are untested. Each enters as a (value, finite) pair
with per-year coverage printed.

    Gate 1 — sign stability (seconds): per-year Spearman of feature
      vs target; admitted if the sign agrees in >= 10 of 15 years.
    Gate 2 — tiny model (minutes): ridge on the candidate columns
      alone; admitted if it beats the constant in >= 8 of 15 folds.

Only survivors join the full model. No book is simulated for a
feature set that has not cleared Gate 2 — the fold line is the gate,
the book is the ceremony.

### Run order — fastest to slowest, each step a go/no-go

1. **Feature gates on the CURRENT ledger** (no rebuild needed;
   seconds to minutes). Read: which candidates survive at all.
2. **The cap's own price, before any model**: rebuild the ledger at
   `H=42` and run the STRENGTH ordering under the capped ladder.
   This one cheap book measures what the time cap costs the incumbent
   — the right-tail truncation, priced directly. If the capped
   control collapses against the uncapped +291.5%, that headwind is
   known before a single fit is paid for.
3. **Fold lines at `H=42`**: the value target on existing features
   (fits refit — labels changed; transforms cached). The gate:
   beats-the-constant in most folds. The value target has never been
   fitted on capped labels; the cap compresses the right tail into
   the estimable range, so this is a genuinely new measurement, not
   a rerun.
4. **The band**: repeat 2-3 at `H=21` and `H=63`.
5. **Books and the yardstick**: the capped book gets its OWN 200-way
   within-day permutation band (the 276-point band belongs to the
   uncapped book and may not be borrowed). Judged on per-bet geo,
   maxDD, and that band. Fees on by default; one `--no-fees` run to
   read the commission cost of the faster recycling.
6. **Gate-2 survivors join the full model**; repeat 3 and 5 once.

### Acceptance

1. Knob off → today's book bit for bit, +291.5% control, as always.
2. Banner carries `max_hold=42d` (or 21/63) and the target name.
3. Ledger and label caches key on `H`; windows and feature caches are
   untouched and shared across all `H`.
4. The split convention (0.5 per leg) is pinned by a hand-built test.
5. Every step of the run order prints its go/no-go number before the
   next step spends anything.

---

## Amendment 7 — group_pct joins the model, uncapped (2026-09-01)

The one survivor of Amendment 6's gates gets its real test. The
measured ground: among candidates that all passed the strength
screen, stocks from HOTTER industry groups made WORSE bets — sign
negative in 12 of 15 years, the first non-price feature ever to pass
both gates here (tiny model 8 of 15 folds over the constant). The
retired §16 gate demanded the top 30% of groups: it held a real
signal by the wrong end. Its OUT verdict stands — for the gate. The
column is new evidence.

### Two deliberate departures from Amendment 6 step 6

1. **Uncapped.** Step 2 priced the day-42 forced sell at 77 points on
   the incumbent before any model: the cap is a measured no-go, and
   the live feature is not tested inside a refuted trading rule.
   Today's exits, today's ledger, no `max_hold`.
2. **Value target.** `r = ln(y)`, split legs at 0.5 each, as the
   operator fixed it — the same target the gates screened against.
   One model, Amendment 5's rule, grouped-CV alpha, same schedule.

### Nobody flips anything by hand

`group_pct` enters as an ordinary feature pair (value, finite). The
regression learns its weight per fold from that fold's own window; if
the screen is right the weight comes out NEGATIVE, and if the
relationship fades in later folds the weight walks back on its own —
which is exactly what the hard gate could never do. **Each fold line
prints the learned `group_pct` coefficient (standardised), so the
flip stays visible and human-readable.** If the printed signs
disagree wildly with the screen's 12-of-15, something is wrong with
the plumbing, not the market — stop and say so.

### Two arms, small before large

    'keys+group'    the six key columns + the group_pct pair
                    (8 features, minutes). One new column cannot
                    drown here; its effect is readable directly.
    'rocket+group'  the full transform + keys + the group_pct pair
                    (4,208 features). The production question.

Run `strength`, `keys+group`, `rocket+group` in one session; the
existing `rocket` fold caches serve as the no-group comparison
without a re-run.

### Gate, then book — pre-registered

- **The gate (fold lines):** `keys+group` must beat the constant in
  more folds than the keys alone ever did, and the learned group
  sign must be stably negative. If the gate fails, no book is
  simulated, the result is one recorded line, and the thread closes.
- **The book (only after the gate):** judged on per-bet geo, maxDD
  and G_day against `strength` and the cached `rocket`, inside the
  uncapped 276-point permutation band — total return decides nothing
  within it. The pre-registered honest outcomes: a small per-bet
  improvement with a printable one-sentence explanation ("strong
  stock, cool industry") — or no movement, one recorded line, and
  the register notes that the last gated feature is spent.

### Acceptance

1. Control reproduces +291.5% before any fitted row is read.
2. Banner: `target=ln(y)  features=8` / `features=4,208`, no
   `max_hold`.
3. Fold lines print the learned standardised `group_pct` coefficient
   beside the metrics.
4. The group column enters as a (value, finite) pair; its per-year
   coverage is printed once before the first fit.
5. Caches key on the feature identity (`keys6+grp`, `rocket+grp`);
   nothing existing is invalidated, and the no-group `rocket` folds
   are read from cache, not refitted.

---

## Amendment 8 — the within-day target: rank the race, not the weather (2026-09-01)

The slot decision is a WITHIN-day ranking, but the label has never
been within-day: `ln(y)` = (what the market did during the hold) +
(how this stock did against its same-day peers). The first component
is the larger by far — the fold lines' out-of-fold mse tracks each
year's market variance, 5-8x between calm and crash years — the picks
cancel it exactly (a per-day constant moves no rank), and it is the
most regime-flipping part of the data. So the fit has been spending
its capacity predicting weather it never uses, and dragging the
within-day ordering around with stale market coefficients. This
amendment makes the label match the decision.

### The target

Per signal: the value target as fixed by the operator (split legs at
0.5 each), MINUS the mean of that value over all ledger signals
entered the SAME day:

    v_i = ln(y_i)            (legs blended 0.5/0.5 as before)
    r_i = v_i − mean(v_j : entry_date_j == entry_date_i)

- A day with a single signal gets label exactly 0 — no within-day
  contrast exists, and the row stays (it contributes nothing and
  distorts nothing).
- No new lookahead: the day-mean uses peer OUTCOMES, which resolve
  within the hold; the 400-day embargo already keeps every training
  outcome resolved before the scored block opens, demeaned or not.
- `--min-score` is REFUSED with this target: the score is relative to
  an unknown day level, so "predicted rate below cash" is undefined.
  The natural zero belongs to absolute targets only.

### What changes and what does not

One line in label construction. Features, arms, schedule, grouped-CV
alpha (now judged on the demeaned number — automatically aligned),
the slot decision, and every book column are untouched. Fold metrics
gain the decision-relevant diagnostic: **within-day Spearman** —
rank correlation of score vs realised label inside each day (days
with >= 5 signals), averaged over the block's days — printed beside
the pooled Spearman, which stays for continuity.

### The run matrix — the rolling-window test rides along

The regime-drift question (expanding averages +sign years with -sign
years into zero) is one existing flag, so it shares the session:

    arms: strength (control), rocket
    target: within-day value        windows: expanding, --lookback 3,
                                             --lookback 5

Six fold-line columns, no books yet. Feature caches shared; block
caches key on target id and the training masks (the lookback changes
the masks, so keys separate themselves).

### Gate, then book — pre-registered, with a closing clause

**Gate, per config:** beats the constant (predict 0) in >= 10 of 15
folds AND mean within-day Spearman positive in >= 10 of 15. Books
are simulated only for configs that clear it, and judged on per-bet
geo, maxDD and G_day inside their own permutation band.

**The closing clause, agreed in advance:** this is the last
cross-sectional construction on these features. The label now matches
the decision exactly; the window question is answered in the same
session. If NO config clears the gate, the profit-ranking question
on price windows is CLOSED — the register records it as measured to
completion, the standing assets are the pool's edge, the market
light, and the crash-avoidance drawdown cut, and further growth work
requires new inputs, not new constructions. No amendment 9 on these
features.

### Acceptance

1. Control reproduces +291.5% before any fitted row is read.
2. A test pins the demeaning: within each entry day of the training
   window, the mean label is 0 to float tolerance; a single-signal
   day's label is exactly 0.
3. `--min-score` with this target refuses with a message naming this
   amendment.
4. Fold lines print pooled AND within-day Spearman; the banner names
   the target (`target=ln(y)-daymean`) and the window.
5. Existing caches untouched; the six configs' fold caches key
   separately and completely.

# trading — working rules

## The goal

**Find an ensembled investment strategy out of weak filters.**

No single filter here beats holding the index, and none is expected to.
Every arm measured so far picks jackpots at the base rate (x0.95 to
x1.08 across four model families) and earns what it earns by declining
bad trades, not by finding good ones. So the target is not a better
filter. It is a **combination** of filters that are individually weak and
disagree with each other — MiniRocket and the shapelet already have rank
correlation -0.116, which is the raw material.

Judge work against that goal: a new transform is interesting because it
adds a *different* mistake to the ensemble, not because its solo row is
the highest.

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

See `EVALUATION_SPEC.md` Rule 4. Short version: the fit configuration
lives in `bets_common` (`ALPHA`, `USE_MODEL_STORE`, `SOLVER`,
`EMBARGO_DAYS`, `LOOKBACK_YEARS`, `AUX_Q`, `MIN_TRAIN`), no script may
re-default it, every run prints `FIT CONFIG` before any number, and every
transactions file carries that line as its first row.

A number quoted without its configuration is not a result. Two scripts
once reported the same nominal arm as +291% and +530% because they
disagreed about two unprinted defaults.

## Held fixed across arms, unless the run says otherwise

`embargo=400d`, `window=expanding`, `label=top 20%`, dilations
`1,2,4,8,16`. Arms differ by the transform and nothing else. The AllPass
control must reproduce **+291.5%** over 2007-01-03 .. 2026-08-27 on any
run before a filtered row from that run is read.

## Tunable

`--keeps` (decision threshold), the transform's own size knob
(`--mr-biases`, `--groups`/`--kernels`), and `--alpha`. Nothing else is a
knob; if something else needs changing, it is a decision, and it belongs
in `DECISIONS.md`.

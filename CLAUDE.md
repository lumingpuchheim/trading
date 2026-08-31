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

`embargo=400d`, `window=expanding`, `target=ln(y)/t` with a 3-day
floor, dilations `1,2,4,8,16`. Arms differ by the transform and nothing
else. The `strength` control must reproduce today's book row for row and
**+291.5%** over 2007-01-03 .. 2026-08-27 before a fitted row from that
run is read — `filter_backtest.py` checks it in-process and exits if it
does not. (`label=top 20%` was the retired architecture's training
target; it survives only as the diagnostic AUC cut.)

## Tunable

`--arms`, the transform's own size knob (`--mr-biases`,
`--groups`/`--kernels`), `--alpha` and `--floor`. `--keeps` is gone with
the veto. Nothing else is a knob; if something else needs changing, it is
a decision, and it belongs in `DECISIONS.md`.

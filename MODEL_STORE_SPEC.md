# Model store — tune the embargo without refitting

## The problem

Every filter run rebuilds everything from source. Over nine runs on
2026-08-29/30 that meant nine MiniRocket transforms (55,737 windows into
4,200 features, 0.94 GB each time), and a full set of per-block fits for
every embargo value tried — 18 fits per value, 139k to 145k training rows
in total, and 407k rows for the expanding window.

Almost none of that had to happen twice.

## Why the current design refits

The embargo moves the **end of training**, not the start of prediction.
For the 2021 block, embargo 400 stops training around 2019-11; embargo
100 stops it around 2020-09. The year being scored is 2021 either way.

So today's code indexes a fit by *the block it scores*. Change the
embargo and every block's training window moves, so all eighteen fits are
invalidated at once.

## The fix: index a model by when its training ENDED

A fitted model does not know or care which block it will score. It is
determined by exactly three things:

    the training window   (train_end, lookback)
    the inputs            (which windows file, which channels)
    the learner           (kind, and its hyperparameters)

Store models under that identity, on a grid of `train_end` dates, and the
embargo stops being a training parameter at all. It becomes arithmetic
done at scoring time:

    required_end = block_open - embargo
    model        = the stored model whose train_end is the latest
                   grid date at or before required_end
    score        = model applied to the block's rows

Scoring is a dot product against 4,200 coefficients. Sweeping ten embargo
values costs ten scorings and zero fits.

## What is stored

One record per (train_end, lookback, learner, inputs):

| field | what it is |
|---|---|
| `coef`, `intercept` | the fitted ridge, 4,200 + 1 numbers |
| `mu`, `sd` | the standardisation, from this window's rows only |
| `label_thr` | the jackpot cut, the AUX_Q quantile of this window's `y` |
| `train_scores` | the model's scores on its own training rows, so a `keep` threshold can be frozen without refitting |
| `train_end`, `lookback`, `n_train` | the window it came from |
| `src`, `alpha`, `channels`, `aux_q` | the identity, for invalidation |

In float32 that is about 50 kB, dominated by `coef`, `mu` and `sd`.
`train_scores` adds ~40 kB at 10,000 training rows.

**Disk budget, stated because the machine has 9.2 GB free:**

| grid | models | size |
|---|---|---|
| quarterly, one lookback | ~70 | ~6 MB |
| monthly, one lookback | ~210 | ~19 MB |
| monthly, three lookbacks | ~630 | ~57 MB |

Under 0.7% of free space at the largest. The transform cache is 0.94 GB
and is a separate decision.

## The grid, and the one approximation

`train_end` runs on **month ends**. An arbitrary embargo is served by
snapping DOWN to the nearest grid date, never up — the model must never
have seen data closer to the block than asked for.

That means the realised embargo is between the requested value and about
31 days more. Ask for 400 days and you may get 415. The sweep on
2026-08-30 moved by a hundred points between 200 and 400 days, so this is
not a rounding error to hide: **every run prints the realised embargo per
block, and the minimum, median and maximum across blocks.** No result is
ever labelled "embargo 400" when it was 415.

A finer grid costs linearly: weekly would be ~900 models and ~80 MB.

## What is still not free

**Changing the lookback** moves the training window's start, so it is
part of a model's identity, not a scoring-time choice. Sweeping lookback
means fitting a new family. The grid is two-dimensional and the store
handles it, but the fits are real.

**The shapelet.** The same indexing works — the stored object is 249
parameters and a threshold — but a shapelet fit is 3 seeds x 40 epochs of
gradient descent, so building a monthly family is roughly ten times the
cost of building the ridge family. Build it lazily: fit a grid point the
first time some run asks for it, and keep it.

**The simulation.** Every arm still runs `simulate()`, because the gate
differs. That was never the expensive part.

**The first build.** A monthly grid is ~210 fits against today's 18 per
run. The store pays for itself after about twelve embargo values, sooner
if the lookback stays fixed.

## Correctness rules

1. **Snap down, never up.** A model whose training ended after
   `block_open - embargo` leaks and must never be served. The lookup is
   `max(g for g in grid if g <= required_end)`; if none exists, the block
   is not scored.
2. **Never cross the block.** `train_end < block_open` always, whatever
   the embargo. An embargo of 0 still gets a model trained only on data
   before the block.
3. **Identity is content, not flags.** The key includes the windows
   file's size and mtime, the channel list, `AUX_Q`, the learner and its
   hyperparameters. Rebuild the ledger and every model is invalidated,
   because `src` changed.
4. **The store may only skip work, never change a number.** Deleting it
   must reproduce identical results, and that is a test, not a hope.
5. **The label travels with the model.** `label_thr` is the AUX_Q
   quantile of the training window's own outcomes, so it can never be
   recomputed later from a different set of rows.

## Acceptance

- Building the grid once, then running the 100/200/300/400 sweep, does
  **zero** fits and reproduces the numbers already recorded for those
  four values.
- `--embargo 400` prints the realised embargo per block, and every one is
  in [400, 431].
- A run with the store deleted, and the same run with it present, give
  identical total return, geo/bet and blocked counts.
- No stored model has `train_end >= block_open` for any block it serves.
- Rebuilding `results/minervini_bets_v5r_windows.npz` invalidates every
  entry, verified by a changed key rather than by inspection.

"""Exponential-decay fit for the seller-decay model.

Fits p(t) = p0 * exp(-lam * t) to a series of positive values by ordinary
least squares on the log values: log(p_t) = log(p0) - lam * t.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class DecayFit:
    lam: float       # decay rate lambda; positive means the series is shrinking
    p0: float        # fitted value at t = 0
    p_today: float   # fitted value on the last day of the window, t = n
    r2: float        # R-squared of the linear fit on log values
    n_used: int      # number of points actually used in the fit


def fit_decay(values, min_valid_points: int) -> DecayFit | None:
    """Fit p(t) = p0 * exp(-lam * t) to `values` (day t = 1 .. n).

    Non-positive and non-finite values cannot be logged and are dropped;
    the remaining points keep their original t so the time axis is not
    distorted. Returns None if fewer than `min_valid_points` remain.
    """
    values = np.asarray(values, dtype=float)
    n = len(values)
    t = np.arange(1, n + 1, dtype=float)

    ok = np.isfinite(values) & (values > 0)
    if ok.sum() < min_valid_points:
        return None

    t_ok = t[ok]
    log_p = np.log(values[ok])

    # least squares for log_p = a + b * t ; lambda = -b
    slope, intercept = np.polyfit(t_ok, log_p, 1)
    lam = -slope
    p0 = float(np.exp(intercept))
    p_today = float(np.exp(intercept + slope * n))

    fitted = intercept + slope * t_ok
    ss_res = float(np.sum((log_p - fitted) ** 2))
    ss_tot = float(np.sum((log_p - log_p.mean()) ** 2))
    r2 = 1.0 - ss_res / ss_tot if ss_tot > 0 else 0.0

    return DecayFit(lam=float(lam), p0=p0, p_today=p_today, r2=r2, n_used=int(ok.sum()))

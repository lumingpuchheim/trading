"""LPPL (Sornette log-periodic power law) bubble fit on a fixed grid.

Model on log price, t = 0..n-1 inside one window, tc measured in days
after the window's last day:

  ln p(t) = A + B*(tc-t)^m + C1*(tc-t)^m*cos(w*ln(tc-t)) + C2*(...)*sin(...)

For fixed (tc, m, w) the model is linear in A, B, C1, C2
(Filimonov & Sornette 2013), so we scan a FIXED deterministic grid over
(tc, m, w) and solve the linear part exactly at every grid point. The best
grid point (lowest SSE) is THE fit; qualification is checked on it. Same
data in, same answer out — no optimizer, no random starts.

The design matrices depend only on the window length and the grid, never on
the data, so they are precomputed once per window length (WindowGrid) and a
single fit costs one (G,n,4)x(n) product plus batched 4x4 algebra.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class LpplFit:
    tc: float      # critical time, in trading days after the window end
    m: float
    w: float
    a: float
    b: float
    c1: float
    c2: float
    c: float       # oscillation amplitude sqrt(C1^2 + C2^2)
    r2: float
    damping: float  # m*|B| / (w*|C|): >=1 means trend dominates oscillation
    qualified: bool


class WindowGrid:
    """Precomputed grid tensors for one window length."""

    def __init__(self, n: int, cfg: dict):
        g = cfg['lppl']
        tc_grid = np.linspace(g['tc_min_ahead'], g['tc_max_ahead_frac'] * n,
                              g['tc_points'])
        m_grid = np.linspace(g['m_min'], g['m_max'], g['m_points'])
        w_grid = np.linspace(g['w_min'], g['w_max'], g['w_points'])
        tc, m, w = np.meshgrid(tc_grid, m_grid, w_grid, indexing='ij')
        self.n = n
        self.tc, self.m, self.w = tc.ravel(), m.ravel(), w.ravel()

        t = np.arange(n, dtype=float)
        dt = (n - 1 + self.tc[:, None]) - t[None, :]     # (G, n), all > 0
        f = dt ** self.m[:, None]
        lg = np.log(dt)
        self.X = np.stack([np.ones_like(f), f,
                           f * np.cos(self.w[:, None] * lg),
                           f * np.sin(self.w[:, None] * lg)], axis=2)  # (G,n,4)
        XtX = np.einsum('gni,gnj->gij', self.X, self.X)
        XtX += 1e-9 * np.eye(4)
        self.XtX = XtX
        self.P = np.linalg.inv(XtX)                      # (G, 4, 4)
        self.min_r2 = g['min_r2']
        self.min_damping = g['min_damping']


def fit_window(y: np.ndarray, grid: WindowGrid) -> LpplFit:
    """Fit one window of log prices (len == grid.n) on the fixed grid."""
    assert len(y) == grid.n
    Xty = np.einsum('gni,n->gi', grid.X, y)              # (G, 4)
    beta = np.einsum('gij,gj->gi', grid.P, Xty)          # (G, 4)
    # sse = y'y - 2 b'X'y + b'X'X b, all from precomputed pieces
    yty = float(y @ y)
    sse = yty - 2 * np.einsum('gi,gi->g', beta, Xty) \
        + np.einsum('gi,gij,gj->g', beta, grid.XtX, beta)
    g = int(np.argmin(sse))

    a, b, c1, c2 = beta[g]
    c = float(np.hypot(c1, c2))
    sstot = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - sse[g] / sstot if sstot > 0 else 0.0
    damping = grid.m[g] * abs(b) / (grid.w[g] * c) if c > 0 else np.inf
    qualified = bool(b < 0 and r2 >= grid.min_r2 and damping >= grid.min_damping)
    return LpplFit(tc=float(grid.tc[g]), m=float(grid.m[g]), w=float(grid.w[g]),
                   a=float(a), b=float(b), c1=float(c1), c2=float(c2), c=c,
                   r2=float(r2), damping=float(damping), qualified=qualified)


def prescreen(closes: np.ndarray, i: int, cfg: dict) -> bool:
    """Cheap necessary condition for a bubble fit at day i: a strong AND
    accelerating run-up. A stock failing this cannot produce a qualified fit
    (which needs super-exponential ascent), so skipping it drops no signal.
    Deliberately loose — its job is cutting compute, not picking winners."""
    g = cfg['lppl']
    lb = g['prescreen_lookback']
    if i + 1 < lb + 1:
        return False
    c_now, c_mid, c_then = closes[i], closes[i - lb // 2], closes[i - lb]
    if not (np.isfinite(c_now) and np.isfinite(c_mid) and np.isfinite(c_then)
            and c_then > 0 and c_mid > 0):
        return False
    if c_now / c_then - 1 < g['prescreen_min_runup']:
        return False
    return np.log(c_now / c_mid) > np.log(c_mid / c_then)


def evaluate_day(log_close: np.ndarray, i: int, grids: list[WindowGrid],
                 cfg: dict) -> dict:
    """Full multi-window evaluation at day i (uses data up to and including i).
    Returns the vote count, median tc of qualifying windows (in trading days
    after day i), the mean r2 of qualifying windows, and the full parameters
    of the qualifying window whose tc is closest to the median (for curve-
    based entry timing)."""
    votes, quals = 0, []
    for grid in grids:
        if i + 1 < grid.n:
            continue  # not enough history for this window: counts as no vote
        y = log_close[i + 1 - grid.n:i + 1]
        if not np.all(np.isfinite(y)):
            continue
        fit = fit_window(y, grid)
        if fit.qualified:
            votes += 1
            quals.append((fit, grid.n))
    out = {'votes': votes, 'tc_ahead': np.nan, 'mean_r2': np.nan,
           'bubble': votes >= cfg['lppl']['min_votes'],
           'p_n': 0, 'p_tc': np.nan, 'p_m': np.nan, 'p_w': np.nan,
           'p_a': np.nan, 'p_b': np.nan, 'p_c1': np.nan, 'p_c2': np.nan}
    if quals:
        tcs = [f.tc for f, _ in quals]
        med = float(np.median(tcs))
        out['tc_ahead'] = med
        out['mean_r2'] = float(np.mean([f.r2 for f, _ in quals]))
        fit, n = quals[int(np.argmin([abs(t - med) for t in tcs]))]
        out.update(p_n=n, p_tc=fit.tc, p_m=fit.m, p_w=fit.w,
                   p_a=fit.a, p_b=fit.b, p_c1=fit.c1, p_c2=fit.c2)
    return out


def next_curve_minimum(p: dict, after: int) -> int | None:
    """First strict local minimum of the fitted LPPL curve, in trading days
    after the evaluation day, at a lag > `after` and before tc. Returns the
    lag in days, or None if the curve has no future dip (monotone rise)."""
    k_max = int(np.floor(p['p_tc'])) - 1
    if k_max < after + 2:
        return None
    k = np.arange(0, k_max + 1, dtype=float)
    dt = p['p_tc'] - k
    lg = np.log(dt)
    f = dt ** p['p_m']
    curve = p['p_a'] + p['p_b'] * f \
        + f * (p['p_c1'] * np.cos(p['p_w'] * lg) + p['p_c2'] * np.sin(p['p_w'] * lg))
    for j in range(max(after + 1, 1), k_max):
        if curve[j] < curve[j - 1] and curve[j] <= curve[j + 1]:
            return j
    return None

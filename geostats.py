"""Geometric averages for bets of unequal size.

Two questions, two formulas. Sizes like 5% vs 7% of equity are handled
differently depending on which question is asked:

1. Per euro staked (the headline stat): the weight IS the bet size.
   A 7% bet is 7 unit-slices of equity passing through the same multiple,
   a 5% bet is 5, so each bet's log-multiple is weighted by its fraction
   of equity:

       G = exp( sum(f_i * ln m_i) / sum(f_i) )

   Splitting one 10% bet into two 5% bets with the same outcome leaves G
   unchanged -- the invariance that makes f_i the unique correct weight.

2. Per bet for the whole account: size is not a weight at all, it lives
   inside the growth factor. Each bet multiplies the account by
   1 + f_i*(m_i - 1), and every bet is exactly one compounding event:

       G_acct = ( prod(1 + f_i*(m_i - 1)) )^(1/n)

Inputs everywhere: mult = euros returned per euro committed (1 + net
return, e.g. 1.12 for +12%), frac = bet size as a fraction of equity
(0.05, 0.07). Weighting by euros staked instead of frac is deliberately
not offered as the headline: it lets bets made after the account has
compounded dominate, smuggling the equity-curve path into a per-bet stat.
"""

import numpy as np


def geo_mean_per_euro(mult, frac=None) -> float:
    """Geometric mean of euros returned per euro committed.

    frac weights each bet by the fraction of equity staked; omit it (or
    pass equal values) for the one-vote-per-bet mean. Pairs where frac
    is missing or non-positive are dropped. NaN if nothing remains.
    """
    m = np.asarray(mult, dtype=float)
    if frac is None:
        f = np.ones_like(m)
    else:
        f = np.asarray(frac, dtype=float)
        if f.shape != m.shape:
            raise ValueError('mult and frac must have the same length')
    ok = np.isfinite(m) & (m > 0) & np.isfinite(f) & (f > 0)
    if not ok.any():
        return float('nan')
    return float(np.exp(np.sum(f[ok] * np.log(m[ok])) / np.sum(f[ok])))


def account_growth_per_bet(mult, frac) -> float:
    """Unweighted geometric mean of the account's per-bet growth factor.

    Each bet scales the account by 1 + frac*(mult - 1); the unstaked
    remainder sits still. Every bet counts once regardless of size.
    NaN if no valid bets; 0.0 if any bet wiped the account (factor <= 0).
    """
    m = np.asarray(mult, dtype=float)
    f = np.asarray(frac, dtype=float)
    if f.shape != m.shape:
        raise ValueError('mult and frac must have the same length')
    ok = np.isfinite(m) & np.isfinite(f) & (f > 0)
    if not ok.any():
        return float('nan')
    g = 1.0 + f[ok] * (m[ok] - 1.0)
    if (g <= 0).any():
        return 0.0
    return float(np.exp(np.mean(np.log(g))))


def bet_multiples(trades) -> 'pd.Series':
    """One multiple per BET, from a simulator trades table.

    `simulate()` writes one ROW per sale, and a position can sell in
    pieces: the +20% strength rule banks part and lets the rest run, v9's
    climax partial splits the same way. So a row is not a bet. A split
    winner writes two rows and a loser writes one, which means averaging
    rows counts winners twice and reports a book that does not exist.

    The bet is the POSITION -- one euro committed at entry, however many
    sales it takes to close -- and what it returned is

        m = SUM over its rows of  weight x (1 + ret_net)
            + SUM of div_eur / bet_eur

    `weight` is the share of the original position each row disposes of,
    so the pieces sum to 1 and the split ratio never has to be assumed.
    `div_eur` is the dividend cash those shares collected while held:
    prices stopped being dividend adjusted on 2026-08-29, so without that
    term every dividend payer's bet is understated. Both columns are
    optional -- runs written before they existed fall back to halves and
    to no dividends.

    Returns a Series of multiples indexed by 'ticker|entry_date'.
    """
    import pandas as pd

    if trades is None or not len(trades):
        return pd.Series(dtype=float)
    t = pd.DataFrame(trades).copy()
    t['_pos'] = t['ticker'].astype(str) + '|' + t['entry_date'].astype(str)
    if 'weight' not in t.columns:      # runs written before 2026-08-28
        t['weight'] = np.where(t['_pos'].duplicated(keep=False), 0.5, 1.0)
    price = t.groupby('_pos').apply(
        lambda d: float((d['weight'] * (1.0 + d['ret_net'])).sum()),
        include_groups=False)
    if 'div_eur' not in t.columns or 'bet_eur' not in t.columns:
        return price
    cash = t.groupby('_pos').apply(
        lambda d: float(np.nansum(d['div_eur']) / d['bet_eur'].iloc[0])
        if np.isfinite(d['bet_eur'].iloc[0]) and d['bet_eur'].iloc[0] > 0
        else 0.0,
        include_groups=False)
    return price + cash.reindex(price.index).fillna(0.0)


def geo_per_bet(trades) -> float:
    """THE per-bet number: the geometric mean of `bet_multiples`.

    One vote per bet regardless of how many rows closed it, dividends
    inside the multiple, and the average that compounds. Every per-bet
    figure reported anywhere in the Minervini path comes from here, so
    two of them can never mean different things again.
    """
    return geo_mean_per_euro(bet_multiples(trades))

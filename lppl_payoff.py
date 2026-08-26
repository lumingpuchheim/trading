"""Route 1 expected-win audit: does any decision-time feature predict the
PAYOFF of an lppl_dip2 entry (not just the odds)?

Pre-registered protocol (declared 2026-08-26 before results were seen):

Sample — every candidate day (liquid & b2 & dip & tc>today, baseline flag
cache), non-overlapping per ticker with the standard 20d cooldown, entries
from backtest.start. Pseudo-trades use the real mechanics (next-open fills,
0.2%/side, 8% stop -> next open, tc clock rolled forward by fresh
evaluations, delisting). This removes the 10-slot competition, multiplying
the sample. Period by entry date.

Features at decision time: votes, mean_r2, tc_runway, p_m, p_w, p_n,
p_sigma, osc_amp, damping, flag_age, persist_depth, dip_depth, runup126,
vol20, rel_dip.

Pre-registered directions (mechanism-backed): flag_age young -> higher
payoff; tc_runway long -> higher; persist_depth low -> higher. Everything
else exploratory.

Stage 1 survival: quintile buckets with DEV-fixed edges; survive iff
|dev top-bottom spread| >= 1pp AND test spread has the same sign.
Stage 2: closed-form ridge on survivors only (standardized on dev, target
capped at learning.return_cap, penalty via learning.penalty_folds scored
on top-10% avg return); frozen model scored once on test.

Run: python lppl_payoff.py   (table cached in data/payoff_trades.parquet;
delete it to rebuild)
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from scipy.stats import spearmanr

from lppl_backtest import ROOT, load_config, load_panel

TABLE = ROOT / 'data' / 'payoff_trades.parquet'
FEATURES = ['votes', 'mean_r2', 'tc_runway', 'p_m', 'p_w', 'p_n', 'p_sigma',
            'osc_amp', 'damping', 'flag_age', 'persist_depth', 'dip_depth',
            'runup126', 'vol20', 'rel_dip']
PREREG = {'flag_age': -1, 'tc_runway': +1, 'persist_depth': -1}
MIN_DEV_SPREAD = 0.01


def build_table(cfg: dict) -> pd.DataFrame:
    panel = load_panel(cfg)
    cal = panel['calendar']
    cal_pos = {d: i for i, d in enumerate(cal)}
    n = len(cal)
    g = cfg['lppl']
    tr = cfg['lppl_trading']
    cost, stop, cd = tr['cost_per_side'], tr['stop_loss'], tr['reentry_cooldown']
    start_i = int(np.searchsorted(cal, pd.Timestamp(cfg['backtest']['start'])))

    # consecutive votes>=2 evaluation depth, valid refit_every days per eval
    flags = pd.read_parquet(ROOT / cfg['data']['cache_dir'] / 'lppl_flags.parquet')
    persist_by_t = {}
    for t, gg in flags.groupby('ticker'):
        arr = np.zeros(n, np.int32)
        depth = 0
        for r in gg.sort_values('date').itertuples():
            j = cal_pos.get(r.date)
            if j is None:
                continue
            depth = depth + 1 if r.votes >= g['min_votes_loose'] else 0
            arr[j:min(n, j + g['refit_every'])] = depth
        persist_by_t[t] = arr

    spy = panel['spy_close']
    spy_dip = (1 - spy / spy.rolling(g['dip_high_window']).max()).to_numpy()

    rows = []
    for t, a in panel['arrays'].items():
        b2, tc2, dip, liq = a['b2'], a['tc2'], a['dip'], a['liquid']
        close, open_ = a['close_f'], a['open']
        cs = pd.Series(close)
        hi20 = cs.rolling(g['dip_high_window']).max().to_numpy()
        vol20 = cs.pct_change().rolling(20).std().to_numpy()
        age = np.full(n, -1, np.int64)
        ep_start, last_b2 = -1, -10 ** 9
        for i in range(n):
            if b2[i]:
                if i - last_b2 > tr['episode_gap']:
                    ep_start = i
                last_b2 = i
                age[i] = i - ep_start
        pdep = persist_by_t.get(t, np.zeros(n, np.int32))

        i = start_i
        while i < n - 1:
            if not (b2[i] and dip[i] and liq[i] and tc2[i] > i
                    and np.isfinite(open_[i + 1]) and a['ev_ptr'][i] >= 0):
                i += 1
                continue
            p = a['evals'][a['ev_ptr'][i]]
            entry_px = open_[i + 1]
            pos_tc, reason = int(tc2[i]), None
            j = i + 1
            while j < n:
                if tc2[j] >= 0:
                    pos_tc = int(tc2[j])
                if j >= a['last_i'] and a['last_i'] < n - 1:
                    reason, fill = 'delisted', j
                    break
                if close[j] <= stop * entry_px:
                    reason = 'stop'
                elif j >= pos_tc:
                    reason = 'tc'
                if reason:
                    fill = min(j + 1, n - 1)
                    break
                j += 1
            else:
                reason, fill = 'open_end', n - 1
            exit_px = open_[fill] if reason in ('stop', 'tc') \
                and np.isfinite(open_[fill]) else close[fill]
            osc = float(np.hypot(p['p_c1'], p['p_c2']))
            damping = p['p_m'] * abs(p['p_b']) / (p['p_w'] * osc) \
                if np.isfinite(osc) and osc > 0 else np.nan
            rows.append({
                'ticker': t, 'decision_date': cal[i], 'entry_date': cal[i + 1],
                'exit_date': cal[fill], 'days_held': fill - (i + 1),
                'ret_net': exit_px * (1 - cost) / (entry_px * (1 + cost)) - 1,
                'reason': reason,
                'votes': int(a['votes'][i]), 'mean_r2': float(a['r2'][i]),
                'tc_runway': int(tc2[i] - i),
                'p_m': float(p['p_m']), 'p_w': float(p['p_w']),
                'p_n': int(p['p_n']), 'p_sigma': float(p['p_sigma']),
                'osc_amp': osc, 'damping': float(damping),
                'flag_age': int(age[i]), 'persist_depth': int(pdep[i]),
                'dip_depth': float(1 - close[i] / hi20[i]),
                'runup126': float(a['rs'][i]) if np.isfinite(a['rs'][i]) else np.nan,
                'vol20': float(vol20[i]),
                'rel_dip': float((1 - close[i] / hi20[i]) - spy_dip[i]),
            })
            i = fill + cd
    return pd.DataFrame(rows)


def bucket_means(df: pd.DataFrame, feat: str, edges: np.ndarray) -> pd.Series:
    b = pd.cut(df[feat], edges, labels=False, include_lowest=True)
    return df.groupby(b)['ret_net'].mean()


def stage1(dev: pd.DataFrame, test: pd.DataFrame) -> pd.DataFrame:
    out = []
    for f in FEATURES:
        d, te = dev[np.isfinite(dev[f])], test[np.isfinite(test[f])]
        edges = np.unique(d[f].quantile([0, .2, .4, .6, .8, 1]).to_numpy())
        if len(edges) < 3:
            continue
        edges[0], edges[-1] = -np.inf, np.inf
        md, mt = bucket_means(d, f, edges), bucket_means(te, f, edges)
        sp_d = md.iloc[-1] - md.iloc[0]
        sp_t = mt.iloc[-1] - mt.iloc[0] if len(mt) > 1 else np.nan
        rho_d = spearmanr(d[f], d['ret_net']).statistic
        rho_t = spearmanr(te[f], te['ret_net']).statistic
        out.append({
            'feature': f, 'n_buckets': len(edges) - 1,
            'prereg': PREREG.get(f, 0),
            'dev_q_means': ' '.join(f'{x:+.3f}' for x in md),
            'dev_spread': sp_d, 'dev_rho': rho_d,
            'test_q_means': ' '.join(f'{x:+.3f}' for x in mt),
            'test_spread': sp_t, 'test_rho': rho_t,
            'survives': bool(abs(sp_d) >= MIN_DEV_SPREAD
                             and np.isfinite(sp_t) and sp_d * sp_t > 0),
        })
    return pd.DataFrame(out)


def stage2(dev: pd.DataFrame, test: pd.DataFrame, feats: list[str],
           cfg: dict) -> None:
    L = cfg['learning']
    cap = L['return_cap']
    dv = dev.dropna(subset=feats)
    tv = test.dropna(subset=feats)
    mu, sd = dv[feats].mean(), dv[feats].std().replace(0, 1)
    Xd = ((dv[feats] - mu) / sd).to_numpy()
    Xd = np.column_stack([np.ones(len(Xd)), Xd])
    yd = dv['ret_net'].clip(upper=cap).to_numpy()

    def fit(X, y, lam):
        pen = lam * np.eye(X.shape[1])
        pen[0, 0] = 0.0
        return np.linalg.solve(X.T @ X + pen, X.T @ y)

    scores = {}
    for lam in L['ridge_penalties']:
        ss = []
        for fold in L['penalty_folds']:
            trn = dv['entry_date'] <= fold['fit_end']
            sco = (dv['entry_date'] >= fold['score_start']) \
                & (dv['entry_date'] <= fold['score_end'])
            if trn.sum() < 50 or sco.sum() < 50:
                continue
            b = fit(Xd[trn.to_numpy()], yd[trn.to_numpy()], lam)
            pred = Xd[sco.to_numpy()] @ b
            k = max(1, int(len(pred) * L['top_fraction']))
            ss.append(yd[sco.to_numpy()][np.argsort(pred)[-k:]].mean())
        scores[lam] = float(np.mean(ss)) if ss else np.nan
    lam = max(scores, key=lambda k: -np.inf if np.isnan(scores[k]) else scores[k])
    beta = fit(Xd, yd, lam)
    print(f'\nstage 2 ridge on {feats}: fold scores {scores}, chosen lambda {lam}')
    print('weights (per 1 sd):',
          {f: round(float(b), 4) for f, b in zip(feats, beta[1:])})

    Xt = ((tv[feats] - mu) / sd).to_numpy()
    Xt = np.column_stack([np.ones(len(Xt)), Xt])
    for name, X, frame in [('dev', Xd, dv), ('test', Xt, tv)]:
        pred = X @ beta
        q = pd.qcut(pred, 5, labels=False, duplicates='drop')
        means = frame.groupby(q)['ret_net'].mean()
        k = max(1, int(len(pred) * L['top_fraction']))
        top = frame['ret_net'].to_numpy()[np.argsort(pred)[-k:]].mean()
        rho = spearmanr(pred, frame['ret_net']).statistic
        print(f'{name}: pred-quintile means '
              f'{" ".join(f"{x:+.3f}" for x in means)} | '
              f'top-decile avg {top:+.4f} | rho {rho:+.3f} | n {len(frame)}')


def main() -> None:
    cfg = load_config()
    bt = cfg['backtest']
    results = ROOT / bt['results_dir']
    if TABLE.exists():
        df = pd.read_parquet(TABLE)
        print(f'cached table: {len(df)} pseudo-trades')
    else:
        df = build_table(cfg)
        df.to_parquet(TABLE)
        print(f'built table: {len(df)} pseudo-trades -> {TABLE}')

    dev = df[df['entry_date'] <= bt['dev_end']]
    test = df[df['entry_date'] >= bt['test_start']]
    print(f'dev {len(dev)} (avg ret {dev["ret_net"].mean():+.4f}), '
          f'test {len(test)} (avg ret {test["ret_net"].mean():+.4f})')
    print('exit reasons dev:', dev['reason'].value_counts().to_dict())

    s1 = stage1(dev, test)
    s1.to_csv(results / 'lppl_payoff_stage1.csv', index=False)
    cols = ['feature', 'prereg', 'dev_q_means', 'dev_spread', 'dev_rho',
            'test_q_means', 'test_spread', 'test_rho', 'survives']
    print('\n=== stage 1: payoff gradients (dev-fixed quintile edges) ===')
    print(s1[cols].to_string(index=False, float_format=lambda x: f'{x:+.4f}'))

    fig, axes = plt.subplots(3, 5, figsize=(20, 10))
    for ax, f in zip(axes.ravel(), FEATURES):
        d = dev[np.isfinite(dev[f])]
        edges = np.unique(d[f].quantile([0, .2, .4, .6, .8, 1]).to_numpy())
        if len(edges) < 3:
            continue
        edges[0], edges[-1] = -np.inf, np.inf
        md = bucket_means(d, f, edges)
        mt = bucket_means(test[np.isfinite(test[f])], f, edges)
        ax.plot(md.index, md.values, 'o-', label='dev')
        ax.plot(mt.index, mt.values, 's--', label='test')
        ax.axhline(0, color='gray', lw=0.5)
        ax.set_title(f)
    axes[0, 0].legend()
    fig.suptitle('mean pseudo-trade return by feature quintile (dev edges)')
    fig.tight_layout()
    fig.savefig(results / 'lppl_payoff_stage1.png', dpi=120)

    surv = s1.loc[s1['survives'], 'feature'].tolist()
    print(f'\nsurvivors (prereg criterion, test-sign-checked): {surv or "NONE"}')
    if surv:
        stage2(dev, test, surv, cfg)
    else:
        print('stage 2 skipped — no feature survived; Route 1 concludes negative.')

    # decontaminated variant: the prereg survival rule looks at the test sign,
    # which leaks test information into stage-2 feature selection. Select on
    # dev alone here so the test evaluation below is a clean single audit.
    surv_dev = s1.loc[s1['dev_spread'].abs() >= MIN_DEV_SPREAD,
                      'feature'].tolist()
    print(f'\ndev-only selection (|dev spread| >= {MIN_DEV_SPREAD:.0%}): {surv_dev}')
    if surv_dev and surv_dev != surv:
        stage2(dev, test, surv_dev, cfg)
    if surv_dev:
        print('\ndev correlation matrix of selected features:')
        print(dev[surv_dev].corr().round(2).to_string())


if __name__ == '__main__':
    main()

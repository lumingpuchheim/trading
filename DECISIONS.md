# Minervini system — the decision register

One line per mechanism ever built, with its verdict. Written 2026-08-28
because the record had grown across FINDINGS, the spec, LIMITATIONS and
chat until nobody could say what was in and what was out.

**Standing configuration: `v5r` = `--v5 --e3 --moc`.** Dev **+55.0%** (65th
control percentile), test **+150.9%** (100th), restated 2026-08-29 on
unadjusted prices. This line previously read +148.4% / +146.8% / 97th in
both; that was measured on dividend-adjusted prices and does not belong to
this dataset. See LIMITATIONS.md, "Split-adjusted prices".

**A FILTER LAYER now sits in front of it** (`filters.py`,
`filter_backtest.py`), ranking the signals the screener already produced
and deciding which one a freed slot is spent on. Separate layer, its own
verdicts below. On one continuous 2009-2026 path, no fees or tax:
v5r +8.61%/yr, +MiniRocket k=0.50 **+11.16%/yr**, +Shapelet g=0 +8.75%/yr,
**SPY total return +14.81%/yr**.

**Read this first:** the standing configuration takes 1,213 of its 1,230
positions on the section-11.3 SMA20 pullback and 6 on the pivot
breakout. The VCP is not used. See LIMITATIONS.md, "The rule we actually
trade is not his rule".

---

## IN — the standing configuration

| mechanism | spec | why it is in |
|---|---|---|
| Trend template, nine conditions | §1 | membership filter; the part that transfers to code cleanly |
| Market light (SPY > 200d SMA and calm) | §2 | the whole drawdown control: 68% of 2008 days flat, 50% of 2022 |
| Market-on-close fill | §3 dev. 1 | the only convention daily bars can express without an invented rule |
| 8% stop from entry | §4 | his number, inside his stated 5-8% range; caps every loss |
| Higher lows in the base | §9 | sourced: ascending bottoms = demand absorbing supply |
| Earnings blackout, 21 days | §9 | entries within 3 weeks of a report lost at triple the average |
| Decisive SMA50 exit (>1% or on volume) | §9 | removed a hair-trigger: 57% of exits fired within 1% of the line |
| Breakeven at 2R | §9 | a +16% winner may not become a loss |
| Tennis-ball window: 15 days, then the egg test | §10.1 | biggest measured gap; the old exits sold the dips at the bottom |
| Strength ranking (RS-line, weak-day, RS) | §10.2 | replaced alphabetical slot-filling |
| +20% half-sale | §10.1 | sell into strength; 17-20% of positions reach it |
| Entry repertoire (cheat / pullback / power play) | §11 | the pullback supplies 99% of all trades |
| E3 fast re-entry, 5 days after non-stop exits | §12.5 | dev +41 pts, test neutral, 97th control percentile in both |
| Flat 10% slots | §12.5 | see the size scan below; no optimum exists in this data |

## OUT — rejected, reverted or measured and not adopted

| mechanism | verdict | the number that decided it |
|---|---|---|
| v1 pivot mechanics (60d-high pivot, fixed-block ordering) | **rejected** | superseded by v2 after a source audit found four deviations |
| v2 buy stop + `failed_breakout` eject | **rejected** | dev -42.8%, test -31.3%, 0th control percentile in both; the eject was an invented rule producing 90% of trades |
| Fundamentals gate §8 (TTM EPS) | **rejected** | 5 trades per period; the threshold was applied to the wrong quantity |
| Fundamentals gate §8b (quarterly EPS, faithful) | **rejected** | 9 trades per period, negative in both; +1.35% vs +2.08% on 4,585 fills |
| Earnings-beat leg §8c | **not adopted** | improved both periods (-12.4/-7.9 -> -9.3/-3.5) but still negative and under its controls |
| v6 money engine (risk sizing, pyramiding, progressive exposure) | **REVERTED** | +107/+147 -> +44/+53, drawdowns -25/-23 -> -41/-31; halved size at bottoms |
| v6 market dimmer (4-point score) | **REVERTED** | same run, same reasoning: judgement we do not have |
| E1 climax sell-all at +25% | **rejected** | -21/-52 pts; amputates the +50-100% right tail the edge lives in |
| E2 volume-weighted weakness | **rejected** | dev +71, test -28 — a regime bet, fails the both-periods bar |
| E4 aging stop from day 60 | **removed, dead code** | never triggered once in 1,467 trades |
| SPY parking of idle cash | **not adopted** | largest lever found (dev +148 -> +224%) but doubles dev drawdown to -54%: it imports the beta the market light exists to avoid |
| Craft ranking (good closes, up/down volume) | **not isolated** | bundled with parking in one run; no separate verdict exists |
| Split-ratio scan 0-100% (how much to realise at +20%) | **50% stands** | whole curve spans 0.27/0.40 pts per bet against a 16-22% sigma; 50% tops both periods but nothing here is outside noise; selling MORE is monotonically worse (E1 again) |
| Bet-size scan 5-33% | **rejected, 10% final** | non-monotonic; 20% worse than 10% in BOTH periods; the 33% test row is a concentration lottery |
| v8 adaptive sizing (bet more when signals are scarce) | **rejected** | dev +62%, test +56% against +107/+147; sizes by arrival intensity, which is not conviction |
| v9 momentum-conditioned selling (velocity exemption + climax partial) | **not adopted** | dev -11, test -7 pts; only 18/24 positions treated differently — no power |
| v10 pullback qualifiers (dry-up, depth cap, bounce, no gapped high) | **REVERTED** | dev +71%, test +33%, 23rd control percentile; the faithful version is the worse one |
| Wide US universe (+1,737 names) | **rejected** | dev +67%, test +37%; new names stopped out twice as often, geometric mean per position goes negative |
| Code 33 hard gate (EPS + sales + margins) | **not adopted** | 14 and 45 trades, 2-5% invested; 0.86% of template days pass all three legs |
| Code 33 conviction ranking | **rejected** | dev -21, test +27 — the E2 pattern, same bar |
| Industry-group strength, hard gate (top 30% of groups) | **rejected** | dev +133%, test +53%; at the control median in test |
| v11 pyramid 5/3/2 (pilot 5%, adds 3% and 2%) | **rejected** | euro/bet 1.0082/1.0108 -> 0.9978/0.9997; the pyramided sixth of positions loses 4.6/4.7 pts because the ladder caps at the flat 10%, so adds only raise the cost basis without adding capital |
| Industry-group strength, conviction ranking | **rejected** | dev +112%, test **-0.8%**, 3rd control percentile — 97 of 100 random portfolios beat it |

## Never built, and why

| | reason |
|---|---|
| Sales and margin legs before 2026-08-28 | recorded as "not obtainable" — that was a limit of yfinance; EDGAR has 71,327 quarters. **Corrected, then built.** |
| Analyst estimate revisions | the provider exposes a current snapshot with no history. Not testable on this source, ever |
| Point-in-time universe and IPO dates | costs money; ours is today's S&P 1500 |
| Float, turnover, institutional sponsorship | not cached |
| Intraday volume pace | daily bars have one volume number, after the close. Both failed fill conventions were workarounds for this |
| Structural stop under the last contraction, and a reward:risk floor | specified nowhere, never built; only the 8%-from-entry half exists |
| Contraction quality as a shape | reduced to a list of depth percentages; the shape is the signal and we do not have it |
| His discretionary veto | irreducible. Whatever gap remains is attributed here |

*(Industry-group strength moved out of this table on 2026-08-28: it was
built, tested both ways, and rejected. See the OUT table above.)*

## Filter layer — verdicts (added 2026-08-29)

| filter | verdict |
|---|---|
| **MiniRocket k=0.50** (84 fixed kernels, PPV, balanced ridge) | **IN.** Dev +104.0%; and the only arm that survives the continuous 2009-2026 path: +8.61% -> +11.16%/yr with drawdown IMPROVING, -29.7% -> -28.3% |
| **Shapelet g=0 k=0.50** (8 curves x 30 days, 249 params) | **IN on dev, DOES NOT TRANSFER.** Dev +126.3%, best of the session; continuous path +8.75%/yr against v5r's +8.61% — nothing — with drawdown worsening to -40.2% |
| Volume added to the shapelet (`--channels 0,2`) | OUT. +126.3% -> +73.7% |
| Price x volume interaction in MiniRocket (`--mv`) | OUT. +104.0% -> +68.4% |
| Stricter thresholds (k=0.80, k=0.90) | OUT. Starve the book: invested falls 71.7% -> 53.4% -> 40.4% and returns fall below AllPass |
| **Dilated CNN** (`minervini_cnn.py`) | **OUT, DELETED 2026-08-29: too many parameters, hard to train.** 2,514-3,010 params against an effective sample size of ~3,000-4,000 (windows overlap 251/252 days, labels overlap, ~12 bets share each day's market factor). Every width tried -- 938, 2,514, 4,730, 7,586 -- landed inside its own label-shuffle control: mean lift -0.0013 to +0.0013 against the shuffle's -0.0048, AUC 0.484-0.541 with no ordering by width. A 249-parameter shapelet and a ZERO-learned-parameter MiniRocket both beat it. The shared helpers it happened to contain (`load`, `folds`, `report`, `line`, `jackpot_loss`, the constants) were never CNN-specific and moved to `bets_common.py`; nothing else was lost |
| F-beta loss, reward only a correct >5% call (`--loss f1`) | **OUT, reverted 2026-08-29.** Dev +89.7% against the BCE shapelet's +126.3%. Kept runnable as a recorded negative, like `--v6` and `--v10`. Its one win: best drawdown of any arm, -23.8% |
| Jackpot picking, any arm | **Not a capability these models have.** FOUR objectives aimed at it, all landing at or below the base rate: cost-weighted BCE x1.02, balanced BCE x0.96, symmetric log-value AUC 0.480, F-beta rewarding only true positives **x0.95**. The loss was never the binding constraint — the information is not in a year of price history in a form these models can reach. The filters earn their return by declining bad trades, not by finding good ones |

## PROPOSED — not built

| idea | what it would need |
|---|---|
| **Combine MiniRocket and Shapelet** | Both raise the per-bet result on dev, from different representations — fixed kernels over five channels versus eight learned price curves. If their scores rank bets differently there is something to gain; if they agree, nothing. **Measure the rank correlation of the two scores on the same candidates FIRST** — that one number decides whether any combiner is worth building. Forms, in rising cost: `AND` (both must approve), rank-average or weighted sum of scores, or a trained second-stage model taking both scores plus context. `filters.py` already has an `Ensemble` class with `all` / `any` / `vote` and rank-average scoring, written and never run. Two constraints that shape the choice: (1) with slots full 70.5% of days, `AND` is strictly more selective and would need each member's threshold LOOSENED to keep the book invested — the k=0.90 row above is what over-selection costs; (2) the shapelet does not survive the continuous path, so any combiner that leans on it inherits that fragility. A weighted form that can down-weight a member is safer than `AND`, and a trained combiner needs its own walk-forward or it just overfits the pair |

## Standing rules about the process itself

- **Both periods or nothing.** A mechanism that helps one period and hurts the other is a regime bet (E2, Code 33 ranking).
- **A worse result is not a licence to re-tune.** §14's constants were switched off whole rather than loosened toward what the history prefers.
- **No profit caps.** E1's rejection is permanent: the edge is the right tail.
- **Do not re-propose** risk-based sizing, pyramiding, progressive exposure or the market dimmer without new judgement-bearing inputs — not new curves.
- **Both periods have been seen.** Everything from v3 onward is post-hoc by construction. The forward paper ledger is the only honest judge.

# Minervini coverage — the complete list

One authoritative inventory of what this repo implements of Minervini's
method and what it does not. Written 2026-08-27 after the user pointed
out, correctly, that the limitations had been dribbled out piecemeal
across FINDINGS, LIMITATIONS, the spec and chat, in different groupings,
growing every time someone pushed.

**Method used to build this list, so it is not another partial one.**
Two enumerations, cross-checked against each other:

- *from the code*: every function in `minervini.py`, every knob in the
  three `minervini*` config blocks, every exit reason in
  `minervini_backtest.py`, every CLI flag;
- *from the method*: the five SEPA pillars (trend, fundamentals,
  catalyst, entry, exit), plus universe, position sizing, exposure
  management and trade management, each verified against sources
  (see the source-verification section of MINERVINI_SPEC.md).

Status key: **YES** built and tested · **PART** partly built ·
**NO-DATA** cannot be built from this data · **NO** simply not built.

---

## Pillar 1 — Trend

| element | status | where / why |
|---|---|---|
| close above SMA50 / 150 / 200 | YES | `trend_template` conditions 1-3 |
| SMA50 > SMA150 > SMA200 | YES | conditions 4-5 |
| SMA200 rising vs 21 days ago | YES | condition 6 |
| close >= 1.30 x 52-week low | YES | condition 7 |
| close >= 0.75 x 52-week high | YES | condition 8 |
| relative strength rank | PART | `rs_ok_matrix`: top 30% of the liquid universe by 126-day return. A price-momentum percentile, market-wide. Not IBD's RS line, not sector-relative. |
| market health gate | PART | SPY above its 200d SMA and 20d vol below its 756d 90th percentile. His own market timing is discretionary and far richer (distribution days, breadth, follow-through days). |

## Pillar 2 — Fundamentals

| element | status | where / why |
|---|---|---|
| quarterly EPS growth >= 20-25% YoY | YES | `eps_gate` F1, most recent quarter, +25% |
| EPS acceleration, 3 consecutive quarters | YES | `eps_gate` F2, g1 > g2 > g3 > g4, strictly rising |
| loss-to-profit turnarounds measurable | YES | `yoy_growth` scales by the absolute year-ago value |
| report freshness | YES | `eps_gate` F3, 120 days |
| **sales growth > 15% YoY** | **NO-DATA** | provider returns 5 quarters (AAPL) / 6 (POWL); Code 33 needs 8 |
| **profit-margin expansion** | **NO-DATA** | same |
| **Code 33 as a whole** | **NO** | it is EPS *and* sales *and* margins. We have one leg of three, and the two missing ones are exactly the quality-of-earnings check that rejects EPS lifted by buybacks or cost-cutting |
| annual / multi-year EPS growth | NO | never specified, never built |
| earnings estimates for the coming quarters | NO-DATA | current snapshot only |

## Pillar 3 — Catalyst

| element | status | where / why |
|---|---|---|
| earnings beat vs consensus | YES | `beat_gate`, `fetch_surprise.py`, 1,495 names, median 96 quarters back to 1998 |
| analyst estimate revisions | NO-DATA | provider exposes a current snapshot with no history |
| new product / contract / drug approval / new management | NO | not representable in any cached series |
| industry-group leadership ("the leader of a leading group") | **NO** | no sector or peer-group ranking exists anywhere in the repo |
| institutional sponsorship / accumulation | NO-DATA | not cached |
| float, share turnover | NO-DATA | not cached |

## Pillar 4 — Entry

| element | status | where / why |
|---|---|---|
| base of 2-6 progressively shallower contractions | YES | `zigzag` + `_base_day`, >= 2 required, strictly decreasing |
| base length 3-65 weeks | YES | 15-325 trading days |
| pivot = high of the final contraction | YES | last confirmed swing high, >= 0.90 x base rim |
| volume dry-up in the final contraction | YES | a quiet day (<= 75% of the 50d mean) in the last 5 |
| breakout on expanded volume | YES | >= 1.5x the 50d mean |
| buy stop just above the pivot | YES | pivot x 1.001, `--moc` variant buys at the close instead |
| never chase more than ~5% past the pivot | YES | `max_chase` |
| **intraday volume pace while the breakout happens** | **NO-DATA** | daily bars give one volume number, after the close. This is the missing input both earlier fill conventions were inventing workarounds for |
| **contraction quality as a shape** | **NO** | tightness of closes, symmetry, where inside each pullback volume dries up, whether the last shakeout undercuts a prior low — all reduced to a list of depth percentages |
| **other entry types** | **NO** | undercut & rally, low cheat, pullback to the 10/20 EMA, power play / high tight flag — only the pivot breakout is built |
| adding to a position on follow-through | NO | never built |

## Pillar 5 — Exit

| element | status | where / why |
|---|---|---|
| fixed stop 7-8% below entry | YES | 0.92 x entry |
| trend-death exit | PART | close below SMA50. Fires on ordinary noise when the entry sits just above a tight base: **176 of 196 trades exit this way, only 20 on the 8% stop** |
| **stop under the final contraction's low** | **NO** | he uses this structurally alongside the percentage rule |
| **reward:risk floor before entering** | **NO** | he refuses trades whose stop distance is not justified by the potential gain |
| **selling into strength / planned partial profits** | **NO** | our spec says winners run, no target |
| back-stop / breakeven stop raising | NO | not built |
| time-based exit | n/a | he does not use one; neither do we |

## Portfolio, universe and money management

| element | status | where / why |
|---|---|---|
| position sizing | **NO** | flat 10% x 10 slots. He concentrates, sizes by conviction, and pyramids |
| progressive exposure (scale in after wins, cut after losses) | **NO** | not built |
| total-exposure management (0% to fully invested) | **NO** | binary market light + fixed slots. He is in cash ~50% of an average year by choice |
| selectivity | **NO** | we take every qualifying name alphabetically until slots fill; he passes on most setups |
| **universe** | **NO-DATA** | current S&P 1500 constituents: survivorship-flattered, *and* it excludes his hunting ground — emerging small/mid caps and recent IPOs that join the index years after the move, or never |
| costs | YES | 0.2% per side |
| re-entry cooldown | YES | 20 days (ours, not his) |

## Known defects in our own specification

Recorded in MINERVINI_SPEC.md, unfixed, because fixing them is a new
pre-registration:

1. base anchored at the 325-day rim, which resets the age and truncates
   the contraction chain when a marginal new high prints mid-base;
2. the `failed_breakout` eject — buy first, check volume at the close,
   sell next morning — is not his rule at all and produced 90-92% of
   the trades in the buy-stop run;
3. the confirmed-trough requirement blocks any base whose recovery gaps
   through the pivot;
4. `base_age_max` (325) can never bind while `base_lookback` is 325;
5. acceptance case 3 was written on a mismeasurement (SPHR's "33-day
   pause" was a v1 artifact; the true figure is 16 days).

## Honest summary

Of the five pillars: **trend is essentially complete**, **entry is
complete except for the one input that matters at the moment of the
trade**, **fundamentals is one leg of three**, **catalyst is one narrow
proxy**, and **exit is a simplification that demonstrably misfires**.
Everything about money management — sizing, exposure, selectivity — is
absent, and those are the parts that turn a sub-50% win rate into a
championship.

None of the results in FINDINGS should be read as a test of Minervini's
method. They test this list's YES column.

---

## Fidelity audit against sourced principles (2026-08-27)

Principles pulled from public sources (TraderLion, Deepvue, ChartMill,
tradingsim, finermarketpoints — secondary; the books remain unread
here), then every one measured against the 196 market-on-close trades
(`results/minervini_v2_moc_audit.csv`). Audit only; no code changed.

### Signal rules the code VIOLATES

| sourced principle | what the code does | measured damage |
|---|---|---|
| "The pattern forms **higher lows** — each contraction's bottom should be higher than the previous ... a stock that keeps undercutting prior lows is not showing strong demand; breaking below prior consolidation lows demonstrates distribution" | only checks that contraction DEPTHS shrink; never compares the LEVELS of the lows | **39% of all trades (76/196) were taken on a base whose final low undercut the prior low** — distribution-shaped bases. They averaged -1.36% vs -0.81% for higher-low bases. STRA is the exhibit |
| "Buying a stock **just ahead of earnings** ... you won't have time to build a profit cushion before the release" | no earnings awareness at entry | **23% of entries had a report within 3 weeks; those trades averaged -1.97% vs -0.63%** — three times the loss rate. 11% were within one week |
| sell on a "**decisive** break below the 50-day, especially **on volume**"; use the 50d as a trailing stop **after breakeven**, not from day one | ANY close below the SMA50, from the day of entry, sells at the next open | 176 of 196 exits are this rule; **57% fired with the close within 1% of the average**, 39% on below-average volume, 28% both — shakeout sales his wording explicitly excludes |
| "a position that shows 2R profit should never turn into a loss" (breakeven-or-better) | no breakeven rule exists | winners like POWL/CMI gave back to the hair-trigger SMA instead |

### Signal rules the code SATISFIES (measured, not assumed)

| sourced principle | measured |
|---|---|
| "strongest VCPs form after a **+30-100% prior advance**" | median advance from the 52-week low into the base rim: **+76%; zero trades under +30%** (template condition 7 does this work) |
| pivot = final contraction's high, near the base rim | median pivot **2.4% below the rim**; only 13% more than 5% below. (A final swing high slightly under the rim is normal cup-and-handle geometry; the sourced requirement is rising LOWS, not rising highs) |
| 2-6 contractions, each smaller; final 3-10% | enforced (>= 2, strictly decreasing, <= 10%; the 3% zigzag floor supplies the lower bound) |
| volume dry-up in the final, tightest contraction | quiet day <= 75% of the 50d mean within the last 5 days |
| breakout volume 40-50% above average | 1.5x enforced |
| never chase more than ~5% past the pivot | enforced |
| stop 7-8% max from entry | 8% enforced |
| base length 3-65 weeks, correction <= 35% | enforced |
| trend template, all nine conditions | enforced (verified visually and in tests) |

### Bottom line of this audit

The trend template, the pivot, the tightening, both volume rules, the
chase guard and the stop size are faithful. The code violates the source
in three places, all measurable in the trades: it accepts
**distribution-shaped bases** (no higher-lows test, 39% of entries), it
**buys into imminent earnings** (23% of entries, triple the loss), and it
**sells leaders on indecisive 50-day grazes** (57% of its own exits).
None of these are data limitations — daily bars contain the lows, the
report calendar is cached, and volume on the exit day is known. They are
spec omissions, fixable only by a new pre-registration.

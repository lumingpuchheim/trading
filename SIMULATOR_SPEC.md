# Paper-Trading Simulator — specification (v1, 2026-08-26)

A realistic simulated brokerage on this PC, fed by the repo's signal
systems, accounted in EUR, matching Comdirect's fees and German taxes.
No real money; realism comes from future-only fills, real FX, and real
cost/tax accounting.

## 1. The weekly email

Sent once a week (default: Sunday 18:00, data through Friday's close) to
the registered address. Contents:

1. **Recommended stocks to buy**, from both systems:
   - `lppl_dip2` entry candidates (2-of-5 flag ∧ 4% dip ∧ tc ahead),
     with votes, R², tc date, and the market-light state;
   - Steady-Giants qualifiers (low vol, straight 5y compounding,
     unbroken dividends), with R², vol tercile, P/E vs own history.
   Each recommendation is either **BUYABLE** or **BLOCKED** with the
   reason shown (market light red; P/E above own p90; already flagged
   as a bubble). Blocked names cannot be bought in the simulator.

   **Every recommendation carries an explicit SOURCE label —
   `LPPL_DIP2` or `STEADY_GIANTS`** — as its own column, with the two
   systems listed in separate sections of the email. The label is
   stored on the order, the lot and every transaction row, so a
   position's origin stays visible for the life of the trade and each
   system's realized P&L can be reported separately.
2. **Bubble warnings** (lppl_mark logic, evaluated weekly, no
   pre-screen) for: every currently held stock, gold, and the S&P 500
   index. Vote count, stretch start date, median tc. Plus Steady-Giants
   sell triggers on held names: P/E ceiling breached, dividend cut,
   LPPL certification.

Email registration happens in the GUI (single user; stored locally;
"send test email" button; SMTP settings in `sim/config_sim.yaml`).

## 2. Instruments and currency

Base currency: **EUR**. Three tradable classes:

- **Recommended stocks** (USD names): fills convert via the real
  EUR/USD rate of the fill day (yfinance `EURUSD=X`, raw). This models
  buying US names on a German venue in EUR and makes currency risk
  visible in the equity curve.
- **S&P 500 index**: simulated as an S&P 500 UCITS ETF (SXR8.DE,
  EUR-quoted) — what a Comdirect customer actually buys, and what makes
  the 30% equity-fund tax exemption (Teilfreistellung) apply.
- **Gold**: simulated as **Xetra-Gold (4GLD.DE, EUR-quoted)**. This
  specific form (physically backed, delivery claim) is what German tax
  law treats like bullion: **tax-free if held ≥ 1 year**. A US fund
  like GLD would NOT be tax-free — the instrument choice is the tax
  feature.

Idle EUR cash earns **0%** (Comdirect settlement account) —
deliberately different from the research assumption of T-bills; the
simulator models the account you actually have.

## 3. Orders — future-only, no hindsight

- Orders can be placed any time via the GUI; they are timestamped and
  **fill at the NEXT trading day's official open** after placement (a
  price that does not exist yet at order time). No order may reference
  a past price; the fill job runs after the open and writes the fill
  from freshly fetched raw quotes.
- v1: market orders only. Phase 4 adds limit orders (Comdirect-style,
  good-till-date).
- Buy enforcement: stocks only from the current week's BUYABLE
  recommendation list; SXR8 and 4GLD always buyable; sells any time.
- Fractional shares: no (whole shares, like the real account).

## 4. Costs — Comdirect schedule (config constants, verify against the
current Preis-Leistungs-Verzeichnis at go-live)

Per executed order:
- commission: **4.90 € + 0.25% of order volume, min 9.90 €, max 59.90 €**
- venue fee: **2.50 €** flat (German venue assumption, configurable)

## 5. Taxes — German rules, withheld at sale like Comdirect does

Config constants with defaults; all applied automatically per realized
sale (FIFO lot matching, fees reduce the gain):

- **Stocks**: Abgeltungsteuer 25% + Solidaritätszuschlag 5.5% of it =
  **26.375%** on realized gains and dividends. Church tax off by
  default (configurable).
- **Loss pots (Verlustverrechnungstöpfe)**: stock losses offset ONLY
  stock gains (Aktien-Topf); ETF/other results use the general pot;
  both carry forward across years.
- **Sparer-Pauschbetrag**: first **1,000 €** of taxable investment
  income per calendar year is tax-free (Freistellungsauftrag assumed
  filed).
- **S&P 500 UCITS ETF**: equity-fund partial exemption
  (Teilfreistellung) 30% → effective **18.4625%** on gains and
  distributions. (Vorabpauschale on accumulating ETFs: phase-4
  refinement, small.)
- **US stock dividends**: 15% US withholding, credited against the
  German tax (net extra German tax 11.375%).
- **Gold (Xetra-Gold)**: holding ≥ 1 year → **tax-free**. Under 1 year:
  private sale (§23 EStG), taxed at the personal rate (default 42%,
  configurable) if total private-sale gains exceed the 1,000 €
  Freigrenze; the GUI warns before an early gold sale.

**Dividends are paid into the book's cash.** On each payment date every
held position is credited qty × dividend per share, converted at that
day's FX rate, with US withholding and German tax applied as above;
the payment appears in the transaction log as its own DIVIDEND row
(gross, withheld, German tax, net) and is *not* reinvested
automatically — the cash sits in the book until an order spends it.
This makes the Steady-Giants dividend stream visible as the cash it
actually is.

Tax is deducted from cash at each sale (like the real account); §23
private-sale tax on early gold sales is accrued and settled at year end
(the Freigrenze needs the full-year total; the real payment happens
with the annual return). A year-end tax report per book is generated.

## 6. Books — "different combinations"

A **book** is one named portfolio with its own starting capital
(default 20,000 €), rule set, and full accounting. Examples: "dip2
only", "giants only", "giants + park cash in SXR8", "everything".
Books are created in the GUI; the weekly email covers all books; orders
are placed per book. This is how combinations are tested — side by
side, same weeks, same prices.

## 7. Outputs

- **Transactions**: every order, fill, fee, tax debit and dividend as a
  table in the GUI and as CSV export (`sim/exports/`), append-only.
- **Graphs** per book and overlaid across books: equity curve (EUR),
  vs SXR8 benchmark, drawdown; PNG export.
- Weekly snapshot of each book appended to a history table (equity,
  cash, positions count) — the data behind the graphs.

## 8. Data separation — the frozen cache stays frozen

The research cache (`data/`) is never touched by the simulator. A new
`data_live/` store holds:
- incremental daily OHLCV for the universe (adjusted series feeding the
  detectors — same convention the backtests were built on),
- **raw** closes for fills (auditable against public quotes),
- `EURUSD=X`, `SXR8.DE`, `4GLD.DE` (raw),
- detector evaluations appended weekly (only new refit days — minutes,
  not a full rerun).
Signals think in the research convention; money moves on auditable raw
prices. State (orders, lots, cash, tax pots, email address) lives in
SQLite: `sim/sim.db`.

## 9. Interface

Basic local web GUI (Flask, http://localhost:8642, no styling beyond
tables and buttons): Recommendations (with BUY buttons only on
buyable rows) · Positions & warnings · Order entry/history ·
Transactions · Graphs · Settings (email, SMTP, books, tax constants).

## 10. Scheduling (Windows Task Scheduler)

- Daily (trading days, 22:30): fetch quotes, fill pending orders,
  record dividends, snapshot books.
- Weekly (Sunday 18:00): incremental detector update, build
  recommendations + warnings, send the email.

## 11. Build order

1. **Broker core** (headless): data_live fetch, order/fill engine,
   fees, FIFO lots, tax engine, SQLite, transaction log. Unit tests
   with worked fee/tax examples (each rule one test with hand-computed
   numbers).
2. **Signals + email**: incremental detector, recommendation builder,
   warning builder, SMTP sender.
3. **GUI**: pages above, book management, graphs, email registration.
4. **Refinements**: limit orders, Vorabpauschale, richer comparisons.

## 12. Honest limitations (stated once, in the GUI footer too)

Fills at the official open ignore spread and slippage (small for liquid
names, real nonetheless); recommendations inherit every research caveat
(survivorship-biased universe, unproven edge — see FINDINGS); the
simulator proves *process*, not *edge*. Its real product is a clean,
auditable forward ledger — the post-2026 out-of-sample record the
research has been waiting for.

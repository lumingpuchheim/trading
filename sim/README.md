# Paper-trading simulator — how to run it

Spec: `../SIMULATOR_SPEC.md`. State lives in `sim/sim.db` (git-ignored),
market data in `data_live/`, email previews in `sim/exports/`.

## Daily use

```
python -m sim.gui        # http://localhost:8642
```

Pages: **Recommendations** (Buy buttons only on BUYABLE rows; gold and the
S&P 500 ETF always available) · **Positions and warnings** · **Orders**
(cancel while pending) · **Transactions** (+ CSV) · **Graphs** (equity per
book) · **Settings** (email, books, run jobs, cost/tax constants).

Orders you place execute at the **next** trading day's opening price —
never at a price that already existed when you clicked.

## Jobs

```
python -m sim.jobs daily     # fill orders, pay dividends, snapshot books
python -m sim.jobs weekly    # rebuild recommendations + warnings, send email
python -m sim.jobs preview   # same as weekly but never sends
```

`weekly` refreshes the whole universe (several minutes). Both jobs are also
buttons on the Settings page.

## Email

Already configured for Gmail (`smtp.gmail.com:587`, sending enabled) with
your address registered in the database. **One step is left, and only you
can do it:** create a Google *app password* (Google account -> Security ->
2-step verification -> App passwords), then store it once:

```
setx SIM_SMTP_PASSWORD "your-16-char-app-password"
```

Open a NEW terminal afterwards and check it works:

```
python -m sim.jobs testmail
```

Until that variable exists the job says so and skips sending; nothing else
breaks. Every weekly run writes `sim/exports/email_<date>.html` regardless,
so the report is never lost.

## Windows Task Scheduler - already installed

Two tasks are registered and run the wrapper scripts in this folder:

| Task | When | Runs |
| --- | --- | --- |
| `SimDaily` | Mon-Fri 22:30 | `sim\run_daily.cmd` -> fills, dividends, snapshots |
| `SimWeekly` | Sunday 18:00 | `sim\run_weekly.cmd` -> recommendations, warnings, email |

Both append to `sim/jobs.log`. Useful commands:

```
schtasks /Query /TN SimDaily /FO LIST     # next run time and status
schtasks /Run   /TN SimWeekly             # run it now
schtasks /Change /TN SimWeekly /ST 20:00  # change the time
schtasks /Delete /TN SimDaily /F          # remove
```

`sim\run_gui.cmd` opens the browser and starts the GUI - double-click it or
pin a shortcut to it.

## What is simulated faithfully, and what is not

Faithful: Comdirect's fee schedule, German taxes (26.375%, separate stock
and general loss pots, 1,000 EUR yearly allowance, 30% equity-ETF
Teilfreistellung, US withholding credited, Xetra-Gold tax-free after a
year with a Freigrenze below that), EUR base with real daily FX, whole
shares, FIFO lots, dividends paid as cash.

Not modelled: bid/ask spread and slippage (fills use the official open),
limit orders (v1 is market-only), Vorabpauschale on accumulating ETFs,
and any edge in the signals themselves — see `../FINDINGS.md`.

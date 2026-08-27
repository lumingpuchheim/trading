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

Register the address in Settings. Put SMTP host/port/user in
`sim/config_sim.yaml`, set `enabled: true`, and export the password in the
environment variable named by `smtp_password_env` (default
`SIM_SMTP_PASSWORD`). Every weekly run writes
`sim/exports/email_<date>.html` whether or not sending is on.

## Windows Task Scheduler

Run from the repo root, adjusting the python path if needed:

```
schtasks /Create /TN "SimDaily" /TR "cmd /c cd /d C:\Users\user\workspace\trading && python -m sim.jobs daily" /SC WEEKLY /D MON,TUE,WED,THU,FRI /ST 22:30
schtasks /Create /TN "SimWeekly" /TR "cmd /c cd /d C:\Users\user\workspace\trading && python -m sim.jobs weekly" /SC WEEKLY /D SUN /ST 18:00
```

## What is simulated faithfully, and what is not

Faithful: Comdirect's fee schedule, German taxes (26.375%, separate stock
and general loss pots, 1,000 EUR yearly allowance, 30% equity-ETF
Teilfreistellung, US withholding credited, Xetra-Gold tax-free after a
year with a Freigrenze below that), EUR base with real daily FX, whole
shares, FIFO lots, dividends paid as cash.

Not modelled: bid/ask spread and slippage (fills use the official open),
limit orders (v1 is market-only), Vorabpauschale on accumulating ETFs,
and any edge in the signals themselves — see `../FINDINGS.md`.

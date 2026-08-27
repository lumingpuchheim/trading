"""Scheduled jobs (SIMULATOR_SPEC section 10).

  python -m sim.jobs daily     fetch quotes, fill orders, pay dividends,
                               snapshot every book
  python -m sim.jobs weekly    update prices, rebuild recommendations and
                               warnings, send the email
  python -m sim.jobs preview   build this week's email and write it to
                               sim/exports/ without sending
  python -m sim.jobs testmail  send a one-line test message to the
                               registered address (checks SMTP setup)

Daily runs after the close; orders placed earlier fill at that day's
open, so a fill can never use a price that existed when the order was
written.
"""

import sys
from datetime import date
from pathlib import Path

import pandas as pd

from lppl_backtest import load_config
from sim import market
from sim.broker import (cash, classify, credit_dividend, currency, equity,
                        fill_pending, positions, snapshot)
from sim.costs import load_sim_config
from sim.db import connect, get_setting
from sim.email_report import build_email, send_email
from sim.signals import market_light, scan_giants, scan_lppl, warnings_for

EXPORTS = Path(__file__).parent / 'exports'


def traded_symbols(conn, sim_cfg: dict) -> list[str]:
    """Everything the simulator needs live quotes for."""
    syms = set()
    for r in conn.execute('SELECT DISTINCT symbol FROM lots WHERE qty_open > 0'):
        syms.add(r['symbol'])
    for r in conn.execute("SELECT DISTINCT symbol FROM orders "
                          "WHERE status = 'PENDING'"):
        syms.add(r['symbol'])
    for r in conn.execute('SELECT DISTINCT symbol FROM recommendations '
                          'WHERE buyable = 1'):
        syms.add(r['symbol'])
    inst = sim_cfg['instruments']
    syms.update({inst['gold']['symbol'], inst['sp500']['symbol']})
    return sorted(syms)


def prices_eur(symbols: list[str], fx: float, sim_cfg: dict) -> dict:
    out = {}
    for s in symbols:
        px = market.last_close(s)
        if px is None:
            continue
        out[s] = px if currency(s, sim_cfg) == 'EUR' else px / fx
    return out


def buyable_set(conn) -> set[str]:
    row = conn.execute('SELECT MAX(week) AS w FROM recommendations').fetchone()
    if not row or not row['w']:
        return set()
    return {r['symbol'] for r in conn.execute(
        'SELECT symbol FROM recommendations WHERE week = ? AND buyable = 1',
        (row['w'],))}


def books(conn) -> list:
    return conn.execute('SELECT * FROM books ORDER BY id').fetchall()


def run_daily(conn, today: str | None = None) -> dict:
    sim_cfg = load_sim_config()
    today = today or date.today().isoformat()
    syms = traded_symbols(conn, sim_cfg)
    market.update_raw(syms + [market.FX_SYMBOL])
    market.update_dividends([s for s in syms
                             if classify(s, sim_cfg) == 'stock'])
    fx = market.fx_eurusd(today)
    opens = market.opens_on(syms, today)
    whitelist = buyable_set(conn)

    fills = fill_pending(conn, today, opens, fx, sim_cfg, buyable=whitelist)
    px = prices_eur(syms, fx, sim_cfg)
    paid = 0
    for b in books(conn):
        for sym in positions(conn, b['id']):
            per_share = market.dividends_on(sym, today)
            if per_share > 0 and credit_dividend(conn, b['id'], sym,
                                                 per_share, today, fx, sim_cfg):
                paid += 1
        snapshot(conn, b['id'], today, px)
    return {'date': today, 'fx': fx, 'fills': fills, 'dividends_paid': paid,
            'books': len(books(conn))}


def run_weekly(conn, today: str | None = None, send: bool = True,
               refresh_universe: bool = True) -> dict:
    cfg, sim_cfg = load_config(), load_sim_config()
    today = today or date.today().isoformat()
    if refresh_universe:
        print('updating universe prices ...', flush=True)
        market.update_adjusted(market.universe())

    light = market_light(cfg)
    print(f'market light: {"GREEN" if light["green"] else "RED"}', flush=True)
    recs = scan_lppl(cfg, light) + scan_giants(cfg, light)
    conn.execute('DELETE FROM recommendations WHERE week = ?', (today,))
    for r in recs:
        conn.execute('INSERT INTO recommendations(week, symbol, source, '
                     'buyable, reason, detail) VALUES(?,?,?,?,?,?)',
                     (today, r['symbol'], r['source'], int(r['buyable']),
                      r['reason'], r['detail']))
    conn.commit()

    inst = sim_cfg['instruments']
    held = sorted({s for b in books(conn) for s in positions(conn, b['id'])})
    watch = held + [inst['gold']['symbol'], cfg['data']['benchmark']]
    market.update_raw(sorted(set(watch)) + [market.FX_SYMBOL])
    warns = warnings_for(list(dict.fromkeys(watch)), cfg)

    fx = market.fx_eurusd(today)
    px = prices_eur(traded_symbols(conn, sim_cfg), fx, sim_cfg)
    book_rows = [{'name': b['name'], 'equity': equity(conn, b['id'], px),
                  'cash': cash(conn, b['id']),
                  'positions': len(positions(conn, b['id']))}
                 for b in books(conn)]

    subject, text, html = build_email(today, light, recs, warns, book_rows)
    EXPORTS.mkdir(exist_ok=True)
    (EXPORTS / f'email_{today}.html').write_text(html, encoding='utf-8')
    sent = False
    if send:
        try:
            sent = send_email(subject, text, html,
                              get_setting(conn, 'email', ''), sim_cfg)
        except Exception as exc:                      # never lose the report
            print(f'email send failed: {exc}')
    return {'date': today, 'recommendations': len(recs),
            'buyable': sum(1 for r in recs if r['buyable']),
            'warnings': sum(1 for w in warns if w['warn']),
            'sent': sent, 'preview': str(EXPORTS / f'email_{today}.html')}


def send_test(conn) -> dict:
    """Prove the SMTP setup works before relying on the Sunday job."""
    sim_cfg = load_sim_config()
    to = get_setting(conn, 'email', '')
    if not to:
        return {'sent': False, 'why': 'no address registered (Settings page)'}
    body = ('This is a test message from your trading simulator. '
            'If you can read it, the Sunday report will arrive too.')
    try:
        sent = send_email('Trading simulator — test message', body,
                          f'<p>{body}</p>', to, sim_cfg)
    except Exception as exc:
        return {'sent': False, 'to': to, 'error': str(exc)}
    return {'sent': sent, 'to': to}


def main() -> None:
    what = sys.argv[1] if len(sys.argv) > 1 else 'daily'
    conn = connect()
    if what == 'daily':
        print(run_daily(conn))
    elif what == 'weekly':
        print(run_weekly(conn))
    elif what == 'preview':
        print(run_weekly(conn, send=False,
                         refresh_universe='--refresh' in sys.argv))
    elif what == 'testmail':
        print(send_test(conn))
    else:
        sys.exit('usage: python -m sim.jobs [daily|weekly|preview|testmail]')
    conn.close()


if __name__ == '__main__':
    main()

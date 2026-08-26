"""Order/fill engine, FIFO lots, dividends, snapshots (SIMULATOR_SPEC 3-7).

Money rules: whole shares only; every fill pays the Comdirect fee; USD
names convert at the fill day's real FX rate; buy fees join the cost
basis (Anschaffungsnebenkosten) and sell fees reduce the gain, as German
practice requires; realized tax is withheld from cash at the sale.

Orders can only fill on a date AFTER they were placed — the engine
refuses same-day fills, which is what keeps the simulation honest.
"""

import sqlite3
from datetime import date as _date

from sim.costs import order_fee
from sim.tax import (ETF_EQUITY, GOLD, STOCK, TaxState, settle_private_sales,
                     tax_on_dividend, tax_on_sale)


def classify(symbol: str, cfg: dict) -> str:
    inst = cfg['instruments']
    if symbol == inst['gold']['symbol']:
        return GOLD
    if symbol == inst['sp500']['symbol']:
        return ETF_EQUITY
    return STOCK


def currency(symbol: str, cfg: dict) -> str:
    inst = cfg['instruments']
    for key in ('gold', 'sp500'):
        if symbol == inst[key]['symbol']:
            return inst[key]['currency']
    return inst['default_stock_currency']


def to_eur(amount: float, ccy: str, fx_eurusd: float) -> float:
    """USD -> EUR at EURUSD (dollars per euro); EUR passes through."""
    if ccy == 'EUR':
        return amount
    if not fx_eurusd or fx_eurusd <= 0:
        raise ValueError('EURUSD rate required for non-EUR fills')
    return amount / fx_eurusd


# ---------------------------------------------------------------- books

def create_book(conn: sqlite3.Connection, name: str, start_capital_eur: float,
                rules: str = '', created_at: str = '') -> int:
    cur = conn.execute(
        'INSERT INTO books(name, start_capital_eur, rules, created_at) '
        'VALUES(?,?,?,?)',
        (name, start_capital_eur, rules, created_at or _date.today().isoformat()))
    book_id = int(cur.lastrowid)
    conn.execute('INSERT INTO tax_pots(book_id) VALUES(?)', (book_id,))
    _tx(conn, book_id, created_at or _date.today().isoformat(), 'DEPOSIT',
        cash_delta_eur=start_capital_eur, note='initial capital')
    conn.commit()
    return book_id


def cash(conn: sqlite3.Connection, book_id: int) -> float:
    row = conn.execute('SELECT COALESCE(SUM(cash_delta_eur), 0) AS c '
                       'FROM transactions WHERE book_id = ?',
                       (book_id,)).fetchone()
    return round(float(row['c']), 2)


def positions(conn: sqlite3.Connection, book_id: int) -> dict[str, float]:
    rows = conn.execute(
        'SELECT symbol, SUM(qty_open) AS q FROM lots WHERE book_id = ? '
        'AND qty_open > 0 GROUP BY symbol', (book_id,)).fetchall()
    return {r['symbol']: float(r['q']) for r in rows}


def equity(conn: sqlite3.Connection, book_id: int,
           prices_eur: dict[str, float]) -> float:
    held = sum(q * prices_eur.get(s, 0.0)
               for s, q in positions(conn, book_id).items())
    return round(cash(conn, book_id) + held, 2)


def _tx(conn: sqlite3.Connection, book_id: int, date: str, type_: str,
        **kw) -> None:
    cols = {'symbol': '', 'source': '', 'qty': 0.0, 'price_eur': 0.0,
            'gross_eur': 0.0, 'fee_eur': 0.0, 'tax_eur': 0.0,
            'withheld_eur': 0.0, 'realized_gain_eur': 0.0,
            'cash_delta_eur': 0.0, 'fx': 1.0, 'note': ''}
    cols.update(kw)
    conn.execute(
        'INSERT INTO transactions(book_id, date, type, symbol, source, qty, '
        'price_eur, gross_eur, fee_eur, tax_eur, withheld_eur, '
        'realized_gain_eur, cash_delta_eur, fx, note) '
        'VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
        (book_id, date, type_, cols['symbol'], cols['source'], cols['qty'],
         cols['price_eur'], cols['gross_eur'], cols['fee_eur'], cols['tax_eur'],
         cols['withheld_eur'], cols['realized_gain_eur'],
         round(cols['cash_delta_eur'], 2), cols['fx'], cols['note']))


# ------------------------------------------------------------ tax state

def load_tax(conn: sqlite3.Connection, book_id: int) -> TaxState:
    r = conn.execute('SELECT * FROM tax_pots WHERE book_id = ?',
                     (book_id,)).fetchone()
    if r is None:
        conn.execute('INSERT INTO tax_pots(book_id) VALUES(?)', (book_id,))
        return TaxState()
    return TaxState(pot_stocks=r['pot_stocks'], pot_general=r['pot_general'],
                    year=r['year'], allowance_used=r['allowance_used'],
                    private_gains=r['private_gains'],
                    private_settled_tax=r['private_settled_tax'])


def save_tax(conn: sqlite3.Connection, book_id: int, st: TaxState) -> None:
    conn.execute(
        'UPDATE tax_pots SET pot_stocks=?, pot_general=?, year=?, '
        'allowance_used=?, private_gains=?, private_settled_tax=? '
        'WHERE book_id=?',
        (st.pot_stocks, st.pot_general, st.year, st.allowance_used,
         st.private_gains, st.private_settled_tax, book_id))


# --------------------------------------------------------------- orders

def place_order(conn: sqlite3.Connection, book_id: int, symbol: str, side: str,
                qty: int, placed_at: str, source: str = 'MANUAL',
                note: str = '') -> int:
    if qty <= 0:
        raise ValueError('quantity must be positive')
    if side not in ('BUY', 'SELL'):
        raise ValueError('side must be BUY or SELL')
    cur = conn.execute(
        'INSERT INTO orders(book_id, symbol, side, qty, source, placed_at, '
        'status, note) VALUES(?,?,?,?,?,?,?,?)',
        (book_id, symbol, side, qty, source, placed_at, 'PENDING', note))
    conn.commit()
    return int(cur.lastrowid)


def pending_orders(conn: sqlite3.Connection,
                   book_id: int | None = None) -> list[sqlite3.Row]:
    q = "SELECT * FROM orders WHERE status = 'PENDING'"
    args: tuple = ()
    if book_id is not None:
        q += ' AND book_id = ?'
        args = (book_id,)
    return conn.execute(q + ' ORDER BY id', args).fetchall()


def fill_pending(conn: sqlite3.Connection, trade_date: str,
                 opens: dict[str, float], fx_eurusd: float, cfg: dict,
                 buyable: set[str] | None = None) -> list[dict]:
    """Fill every pending order placed BEFORE `trade_date` at that day's
    open. `buyable` (when given) is the current recommendation whitelist;
    EUR instruments (gold, index ETF) are always allowed."""
    out = []
    always = {cfg['instruments']['gold']['symbol'],
              cfg['instruments']['sp500']['symbol']}
    for o in pending_orders(conn):
        if o['placed_at'][:10] >= trade_date:
            continue                       # placed today or later: not yet
        px = opens.get(o['symbol'])
        if px is None:
            continue                       # no quote: stays pending
        if o['side'] == 'BUY' and buyable is not None \
                and o['symbol'] not in buyable and o['symbol'] not in always:
            _reject(conn, o, trade_date, 'not on the buyable list')
            out.append({'order_id': o['id'], 'status': 'REJECTED'})
            continue
        try:
            res = (_fill_buy if o['side'] == 'BUY' else _fill_sell)(
                conn, o, trade_date, px, fx_eurusd, cfg)
        except ValueError as e:
            _reject(conn, o, trade_date, str(e))
            out.append({'order_id': o['id'], 'status': 'REJECTED',
                        'reason': str(e)})
            continue
        conn.execute("UPDATE orders SET status='FILLED', fill_date=? "
                     'WHERE id=?', (trade_date, o['id']))
        out.append({'order_id': o['id'], 'status': 'FILLED', **res})
    conn.commit()
    return out


def _reject(conn: sqlite3.Connection, o: sqlite3.Row, trade_date: str,
            reason: str) -> None:
    conn.execute("UPDATE orders SET status='REJECTED', fill_date=?, "
                 'note=? WHERE id=?', (trade_date, reason, o['id']))


def _fill_buy(conn: sqlite3.Connection, o: sqlite3.Row, trade_date: str,
              open_px: float, fx: float, cfg: dict) -> dict:
    sym = o['symbol']
    ccy = currency(sym, cfg)
    px_eur = to_eur(open_px, ccy, fx)
    gross = px_eur * o['qty']
    fee = order_fee(gross, cfg)
    if gross + fee > cash(conn, o['book_id']) + 1e-9:
        raise ValueError('insufficient cash')
    conn.execute(
        'INSERT INTO lots(book_id, symbol, asset_class, source, opened_date, '
        'qty_open, cost_eur_per_share, fee_eur_open) VALUES(?,?,?,?,?,?,?,?)',
        (o['book_id'], sym, classify(sym, cfg), o['source'], trade_date,
         float(o['qty']), px_eur, fee))
    _tx(conn, o['book_id'], trade_date, 'BUY', symbol=sym, source=o['source'],
        qty=float(o['qty']), price_eur=px_eur, gross_eur=gross, fee_eur=fee,
        cash_delta_eur=-(gross + fee), fx=fx if ccy != 'EUR' else 1.0)
    return {'symbol': sym, 'qty': o['qty'], 'price_eur': round(px_eur, 4),
            'fee_eur': fee}


def _fill_sell(conn: sqlite3.Connection, o: sqlite3.Row, trade_date: str,
               open_px: float, fx: float, cfg: dict) -> dict:
    sym, book_id = o['symbol'], o['book_id']
    ccy = currency(sym, cfg)
    px_eur = to_eur(open_px, ccy, fx)
    lots = conn.execute(
        'SELECT * FROM lots WHERE book_id=? AND symbol=? AND qty_open > 0 '
        'ORDER BY opened_date, id', (book_id, sym)).fetchall()
    have = sum(l['qty_open'] for l in lots)
    if have < o['qty']:
        raise ValueError('not enough shares held')

    gross = px_eur * o['qty']
    fee = order_fee(gross, cfg)
    st = load_tax(conn, book_id)
    asset_class = classify(sym, cfg)
    year = int(trade_date[:4])
    remaining, gain_total, tax_total = float(o['qty']), 0.0, 0.0

    for lot in lots:
        if remaining <= 0:
            break
        take = min(remaining, lot['qty_open'])
        share = take / lot['qty_open']
        fee_in = lot['fee_eur_open'] * share            # buy fee, pro rata
        fee_out = fee * (take / o['qty'])               # sell fee, pro rata
        gain = take * (px_eur - lot['cost_eur_per_share']) - fee_in - fee_out
        held = (_date.fromisoformat(trade_date)
                - _date.fromisoformat(lot['opened_date'])).days
        tax_total += tax_on_sale(st, asset_class, gain, held, year, cfg)
        gain_total += gain
        conn.execute('UPDATE lots SET qty_open=?, fee_eur_open=? WHERE id=?',
                     (lot['qty_open'] - take, lot['fee_eur_open'] - fee_in,
                      lot['id']))
        remaining -= take

    save_tax(conn, book_id, st)
    tax_total = round(tax_total, 2)
    _tx(conn, book_id, trade_date, 'SELL', symbol=sym, source=o['source'],
        qty=float(o['qty']), price_eur=px_eur, gross_eur=gross, fee_eur=fee,
        tax_eur=tax_total, realized_gain_eur=round(gain_total, 2),
        cash_delta_eur=gross - fee - tax_total,
        fx=fx if ccy != 'EUR' else 1.0)
    return {'symbol': sym, 'qty': o['qty'], 'price_eur': round(px_eur, 4),
            'fee_eur': fee, 'tax_eur': tax_total,
            'realized_gain_eur': round(gain_total, 2)}


# ------------------------------------------------------------ dividends

def credit_dividend(conn: sqlite3.Connection, book_id: int, symbol: str,
                    per_share_ccy: float, pay_date: str, fx_eurusd: float,
                    cfg: dict) -> dict | None:
    """Pay a dividend into the book's cash (SIMULATOR_SPEC section 5)."""
    qty = positions(conn, book_id).get(symbol, 0.0)
    if qty <= 0 or per_share_ccy <= 0:
        return None
    ccy = currency(symbol, cfg)
    gross = to_eur(per_share_ccy * qty, ccy, fx_eurusd)
    st = load_tax(conn, book_id)
    res = tax_on_dividend(st, gross, int(pay_date[:4]), cfg,
                          us_source=(ccy != 'EUR'),
                          asset_class=classify(symbol, cfg))
    save_tax(conn, book_id, st)
    src = conn.execute('SELECT source FROM lots WHERE book_id=? AND symbol=? '
                       'AND qty_open > 0 ORDER BY id LIMIT 1',
                       (book_id, symbol)).fetchone()
    _tx(conn, book_id, pay_date, 'DIVIDEND', symbol=symbol,
        source=src['source'] if src else '', qty=qty,
        price_eur=per_share_ccy, gross_eur=res['gross_eur'],
        tax_eur=res['german_tax_eur'], withheld_eur=res['withheld_eur'],
        cash_delta_eur=res['net_eur'], fx=fx_eurusd if ccy != 'EUR' else 1.0,
        note='dividend')
    conn.commit()
    return res


# ------------------------------------------------- year end and snapshots

def settle_year(conn: sqlite3.Connection, book_id: int, year: int,
                cfg: dict) -> float:
    """Debit §23 tax on early gold sales once the year is complete."""
    st = load_tax(conn, book_id)
    due = settle_private_sales(st, year, cfg)
    save_tax(conn, book_id, st)
    if due > 0:
        _tx(conn, book_id, f'{year}-12-31', 'TAX', cash_delta_eur=-due,
            tax_eur=due, note='§23 private sale tax (gold held < 1 year)')
    conn.commit()
    return due


def snapshot(conn: sqlite3.Connection, book_id: int, date: str,
             prices_eur: dict[str, float]) -> None:
    conn.execute(
        'INSERT INTO snapshots(book_id, date, equity_eur, cash_eur, '
        'n_positions) VALUES(?,?,?,?,?) ON CONFLICT(book_id, date) DO UPDATE '
        'SET equity_eur=excluded.equity_eur, cash_eur=excluded.cash_eur, '
        'n_positions=excluded.n_positions',
        (book_id, date, equity(conn, book_id, prices_eur),
         cash(conn, book_id), len(positions(conn, book_id))))
    conn.commit()

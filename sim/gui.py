"""Local web GUI for the simulator (SIMULATOR_SPEC section 9).

    python -m sim.gui      ->  http://localhost:8642

Deliberately plain: tables, forms, one chart. Buy buttons appear only on
rows this week's email marked BUYABLE; gold and the S&P 500 ETF are
always available. Every page works on the selected book (?book=<id>).
"""

import base64
import io
from datetime import datetime

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
from flask import Flask, Response, redirect, render_template_string, request

from sim import market
from sim.broker import (cash, create_book, equity, place_order, positions)
from sim.costs import load_sim_config, order_fee
from sim.db import connect, get_setting, set_setting
from sim.jobs import prices_eur, run_daily, run_weekly, traded_symbols

app = Flask(__name__)
SIM = load_sim_config()

LAYOUT = """<!doctype html><html><head><meta charset="utf-8">
<title>Trading simulator</title><style>
body{font-family:system-ui,Arial,sans-serif;font-size:14px;margin:18px;color:#222}
nav a{margin-right:14px;text-decoration:none;color:#0645ad}
nav{border-bottom:1px solid #ccc;padding-bottom:8px;margin-bottom:14px}
table{border-collapse:collapse;width:100%;margin:8px 0 18px 0}
th,td{border:1px solid #ccc;padding:4px 7px;text-align:left}
th{background:#f2f2f2}
.blocked{color:#999}.warn{background:#ffe9e9}.ok{color:#0a7a0a;font-weight:600}
.src{font-family:monospace;font-size:12px}
.money{text-align:right;font-variant-numeric:tabular-nums}
button{padding:2px 8px}input[type=number]{width:70px}
form.inline{display:inline}
footer{color:#777;font-size:12px;margin-top:26px;border-top:1px solid #eee;padding-top:8px}
.msg{background:#eef7ee;border:1px solid #cbe5cb;padding:6px 9px;margin-bottom:10px}
</style></head><body>
<nav><b>Simulator</b>
<a href="/?book={{bid}}">Recommendations</a>
<a href="/positions?book={{bid}}">Positions and warnings</a>
<a href="/orders?book={{bid}}">Orders</a>
<a href="/transactions?book={{bid}}">Transactions</a>
<a href="/graphs?book={{bid}}">Graphs</a>
<a href="/settings?book={{bid}}">Settings</a>
</nav>
{% if books %}<form method="get" class="inline" action="{{path}}">
Book: <select name="book" onchange="this.form.submit()">
{% for b in books %}<option value="{{b['id']}}" {% if b['id']==bid %}selected{% endif %}>{{b['name']}}</option>{% endfor %}
</select></form>
&nbsp; cash <b class="money">{{'%.2f'|format(bcash)}} EUR</b>
&nbsp; equity <b class="money">{{'%.2f'|format(beq)}} EUR</b>{% endif %}
{% if msg %}<p class="msg">{{msg}}</p>{% endif %}
{{ body|safe }}
<footer>Paper trading only. Orders fill at the NEXT trading day's open.
Fees and German taxes follow Comdirect's schedule (see sim/config_sim.yaml).
Signals inherit the research caveats in FINDINGS.md - this proves
process, not profit.</footer></body></html>"""


def db():
    return connect()


def books_list(conn):
    return conn.execute('SELECT * FROM books ORDER BY id').fetchall()


def current_book(conn):
    bs = books_list(conn)
    if not bs:
        return None
    want = request.args.get('book', type=int)
    for b in bs:
        if b['id'] == want:
            return b
    return bs[0]


def eur_prices(conn):
    try:
        fx = market.fx_eurusd()
    except Exception:
        fx = 1.0
    return prices_eur(traded_symbols(conn, SIM), fx, SIM), fx


def page(conn, body, msg=''):
    b = current_book(conn)
    px, _ = eur_prices(conn) if b else ({}, 1.0)
    return render_template_string(
        LAYOUT, body=body, books=books_list(conn), bid=b['id'] if b else 0,
        bcash=cash(conn, b['id']) if b else 0.0,
        beq=equity(conn, b['id'], px) if b else 0.0,
        msg=msg or request.args.get('msg', ''), path=request.path)


@app.route('/')
def index():
    conn = db()
    b = current_book(conn)
    if b is None:
        return page(conn, '<p>No book yet - create one in '
                          '<a href="/settings">Settings</a>.</p>')
    week = conn.execute('SELECT MAX(week) AS w FROM recommendations').fetchone()['w']
    rows = conn.execute('SELECT * FROM recommendations WHERE week = ? '
                        'ORDER BY source, buyable DESC, symbol',
                        (week,)).fetchall() if week else []
    px, fx = eur_prices(conn)
    out = ['<h2>Recommendations - week of %s</h2>' % (week or '(none yet)')]
    if not rows:
        out.append('<p>No recommendations stored. Build them on the '
                   '<a href="/settings">Settings</a> page.</p>')

    def buy_form(symbol, source, default_qty=10):
        return ('<form method="post" action="/buy" class="inline">'
                '<input type="hidden" name="book" value="%d">'
                '<input type="hidden" name="symbol" value="%s">'
                '<input type="hidden" name="source" value="%s">'
                '<input type="number" name="qty" min="1" value="%d">'
                '<button type="submit">Buy</button></form>'
                % (b['id'], symbol, source, default_qty))

    for src in ('LPPL_DIP2', 'STEADY_GIANTS'):
        sub = [r for r in rows if r['source'] == src]
        if not sub:
            continue
        title = 'bubble dip-buyer' if src == 'LPPL_DIP2' else 'compounders'
        out.append('<h3>%s <span class="src">(%s)</span></h3>'
                   '<table><tr><th>Symbol</th><th>Source</th><th>Status</th>'
                   '<th>Detail</th><th>Buy</th></tr>' % (src, title))
        for r in sub:
            if r['buyable']:
                status = '<span class="ok">BUYABLE</span>'
                action = buy_form(r['symbol'], r['source'])
                cls = ''
            else:
                status = 'BLOCKED - %s' % r['reason']
                action = '<i>not buyable</i>'
                cls = ' class="blocked"'
            out.append('<tr%s><td><b>%s</b></td><td class="src">%s</td>'
                       '<td>%s</td><td>%s</td><td>%s</td></tr>'
                       % (cls, r['symbol'], r['source'], status, r['detail'],
                          action))
        out.append('</table>')

    inst = SIM['instruments']
    out.append('<h3>Always available</h3><table><tr><th>Symbol</th>'
               '<th>What</th><th>Price</th><th>Buy</th></tr>')
    for key, what in (('gold', 'Xetra-Gold (tax-free after 1 year)'),
                      ('sp500', 'S and P 500 UCITS ETF')):
        sym = inst[key]['symbol']
        p = px.get(sym)
        out.append('<tr><td><b>%s</b></td><td>%s</td>'
                   '<td class="money">%s</td><td>%s</td></tr>'
                   % (sym, what, ('%.2f EUR' % p) if p else '-',
                      buy_form(sym, 'MANUAL', 1)))
    out.append('</table>')
    return page(conn, ''.join(out))


@app.route('/buy', methods=['POST'])
def buy():
    conn = db()
    bid = int(request.form['book'])
    sym = request.form['symbol']
    qty = int(request.form['qty'])
    src = request.form.get('source', 'MANUAL')
    place_order(conn, bid, sym, 'BUY', qty,
                datetime.now().isoformat(timespec='minutes'), src)
    return redirect('/orders?book=%d&msg=BUY+%d+%s+queued+for+the+next+open'
                    % (bid, qty, sym))


@app.route('/sell', methods=['POST'])
def sell():
    conn = db()
    bid = int(request.form['book'])
    sym = request.form['symbol']
    qty = int(request.form['qty'])
    place_order(conn, bid, sym, 'SELL', qty,
                datetime.now().isoformat(timespec='minutes'))
    return redirect('/orders?book=%d&msg=SELL+%d+%s+queued+for+the+next+open'
                    % (bid, qty, sym))


@app.route('/positions')
def positions_page():
    conn = db()
    b = current_book(conn)
    if b is None:
        return page(conn, '<p>No book yet.</p>')
    px, fx = eur_prices(conn)
    pos = positions(conn, b['id'])
    out = ['<h2>Positions</h2><table><tr><th>Symbol</th><th>Source</th>'
           '<th>Qty</th><th>Price</th><th>Value (EUR)</th><th>Sell</th></tr>']
    for sym, qty in sorted(pos.items()):
        src = conn.execute('SELECT source FROM lots WHERE book_id=? AND '
                           'symbol=? AND qty_open>0 ORDER BY id LIMIT 1',
                           (b['id'], sym)).fetchone()
        p = px.get(sym, 0.0)
        form = ('<form method="post" action="/sell" class="inline">'
                '<input type="hidden" name="book" value="%d">'
                '<input type="hidden" name="symbol" value="%s">'
                '<input type="number" name="qty" min="1" value="%d">'
                '<button type="submit">Sell</button></form>'
                % (b['id'], sym, int(qty)))
        out.append('<tr><td><b>%s</b></td><td class="src">%s</td>'
                   '<td class="money">%g</td><td class="money">%.2f</td>'
                   '<td class="money">%s</td><td>%s</td></tr>'
                   % (sym, src['source'] if src else '', qty, p,
                      '{:,.2f}'.format(qty * p), form))
    if not pos:
        out.append('<tr><td colspan="6"><i>no positions</i></td></tr>')
    out.append('</table>')

    out.append('<h2>Bubble warnings</h2>')
    try:
        from lppl_backtest import load_config
        from sim.signals import warnings_for
        cfg = load_config()
        watch = sorted(set(list(pos) + [SIM['instruments']['gold']['symbol'],
                                        cfg['data']['benchmark']]))
        out.append('<table><tr><th>Symbol</th><th>Bubble state</th>'
                   '<th>Votes</th><th>Est. critical time</th></tr>')
        for w in warnings_for(watch, cfg):
            cls = ' class="warn"' if w['warn'] else ''
            out.append('<tr%s><td><b>%s</b></td><td>%s</td><td>%d/5</td>'
                       '<td>%s</td></tr>'
                       % (cls, w['symbol'], w['level'], w['votes'],
                          w['tc_date'] or '-'))
        out.append('</table>')
    except Exception as exc:
        out.append('<p><i>warnings unavailable: %s</i></p>' % exc)
    return page(conn, ''.join(out))


@app.route('/orders')
def orders_page():
    conn = db()
    b = current_book(conn)
    if b is None:
        return page(conn, '<p>No book yet.</p>')
    rows = conn.execute('SELECT * FROM orders WHERE book_id=? ORDER BY id DESC '
                        'LIMIT 100', (b['id'],)).fetchall()
    out = ['<h2>Orders</h2><p>Orders always execute at the <b>next</b> '
           'trading day opening price.</p>',
           '<table><tr><th>#</th><th>Placed</th><th>Symbol</th><th>Source</th>'
           '<th>Side</th><th>Qty</th><th>Status</th><th>Filled</th>'
           '<th>Note</th><th></th></tr>']
    for r in rows:
        act = ''
        if r['status'] == 'PENDING':
            act = ('<form method="post" action="/cancel" class="inline">'
                   '<input type="hidden" name="book" value="%d">'
                   '<input type="hidden" name="id" value="%d">'
                   '<button type="submit">Cancel</button></form>'
                   % (b['id'], r['id']))
        out.append('<tr><td>%d</td><td>%s</td><td><b>%s</b></td>'
                   '<td class="src">%s</td><td>%s</td><td class="money">%d</td>'
                   '<td>%s</td><td>%s</td><td>%s</td><td>%s</td></tr>'
                   % (r['id'], r['placed_at'], r['symbol'], r['source'],
                      r['side'], r['qty'], r['status'], r['fill_date'] or '-',
                      r['note'] or '', act))
    out.append('</table>')
    return page(conn, ''.join(out))


@app.route('/cancel', methods=['POST'])
def cancel():
    conn = db()
    bid = int(request.form['book'])
    conn.execute("UPDATE orders SET status='CANCELLED' WHERE id=? AND "
                 "status='PENDING'", (int(request.form['id']),))
    conn.commit()
    return redirect('/orders?book=%d&msg=order+cancelled' % bid)


@app.route('/transactions')
def transactions_page():
    conn = db()
    b = current_book(conn)
    if b is None:
        return page(conn, '<p>No book yet.</p>')
    rows = conn.execute('SELECT * FROM transactions WHERE book_id=? '
                        'ORDER BY id DESC', (b['id'],)).fetchall()
    out = ['<h2>Transactions</h2>'
           '<p><a href="/transactions.csv?book=%d">Download CSV</a></p>'
           '<table><tr><th>Date</th><th>Type</th><th>Symbol</th>'
           '<th>Source</th><th>Qty</th><th>Price</th><th>Fee</th>'
           '<th>Withheld</th><th>Tax</th><th>Gain</th><th>Cash (EUR)</th>'
           '<th>Note</th></tr>' % b['id']]
    for r in rows:
        out.append('<tr><td>%s</td><td>%s</td><td>%s</td>'
                   '<td class="src">%s</td><td class="money">%g</td>'
                   '<td class="money">%.4f</td><td class="money">%.2f</td>'
                   '<td class="money">%.2f</td><td class="money">%.2f</td>'
                   '<td class="money">%.2f</td><td class="money">%.2f</td>'
                   '<td>%s</td></tr>'
                   % (r['date'], r['type'], r['symbol'], r['source'],
                      r['qty'], r['price_eur'], r['fee_eur'],
                      r['withheld_eur'], r['tax_eur'],
                      r['realized_gain_eur'], r['cash_delta_eur'],
                      r['note']))
    if not rows:
        out.append('<tr><td colspan="12"><i>nothing yet</i></td></tr>')
    out.append('</table>')
    return page(conn, ''.join(out))


@app.route('/transactions.csv')
def transactions_csv():
    conn = db()
    b = current_book(conn)
    rows = conn.execute('SELECT * FROM transactions WHERE book_id=? '
                        'ORDER BY id', (b['id'],)).fetchall()
    cols = list(rows[0].keys()) if rows else ['id']
    lines = [','.join(cols)]
    for r in rows:
        lines.append(','.join('"%s"' % r[c] for c in cols))
    return Response('\n'.join(lines), mimetype='text/csv',
                    headers={'Content-Disposition':
                             'attachment; filename=transactions_%s.csv'
                             % b['name']})


@app.route('/graphs')
def graphs():
    conn = db()
    bs = books_list(conn)
    if not bs:
        return page(conn, '<p>No book yet.</p>')
    fig, ax = plt.subplots(figsize=(10, 5))
    drawn = 0
    for b in bs:
        snaps = conn.execute('SELECT date, equity_eur FROM snapshots '
                             'WHERE book_id=? ORDER BY date',
                             (b['id'],)).fetchall()
        if len(snaps) < 2:
            continue
        ax.plot([s['date'] for s in snaps], [s['equity_eur'] for s in snaps],
                marker='.', label=b['name'])
        drawn += 1
    body = '<h2>Equity per book (EUR)</h2>'
    if not drawn:
        body += ('<p><i>Not enough history yet - snapshots are written by '
                 'the daily job. Run it a few times from Settings.</i></p>')
    else:
        ax.set_ylabel('EUR')
        ax.grid(alpha=0.3)
        ax.legend()
        fig.autofmt_xdate()
        fig.tight_layout()
        buf = io.BytesIO()
        fig.savefig(buf, format='png', dpi=110)
        body += ('<img src="data:image/png;base64,%s" style="max-width:100%%">'
                 % base64.b64encode(buf.getvalue()).decode())
    plt.close(fig)
    return page(conn, body)


@app.route('/settings')
def settings_page():
    conn = db()
    b = current_book(conn)
    bid = b['id'] if b else 0
    t = SIM['tax']
    rate = 100 * t['abgeltungsteuer'] * (1 + t['soli'] + t['church_tax'])
    book_rows = ''.join(
        '<tr><td>%s</td><td class="money">%s</td><td>%s</td><td>%s</td></tr>'
        % (x['name'], '{:,.2f}'.format(x['start_capital_eur']), x['rules'],
           x['created_at']) for x in books_list(conn))
    body = """<h2>Email</h2>
<form method="post" action="/settings/email">
<input type="hidden" name="book" value="%d">
<input type="email" name="email" size="34" value="%s" placeholder="you@example.com">
<button type="submit">Register</button></form>
<p class="src">SMTP host and user live in sim/config_sim.yaml; the password is
read from the environment variable %s. Sending is currently %s.</p>

<h2>Books</h2>
<form method="post" action="/settings/book">
name <input name="name" required>
start capital EUR <input type="number" name="capital" value="%d" step="100">
rules <input name="rules" placeholder="dip2 only / giants only / both">
<button type="submit">Create book</button></form>
<table><tr><th>Book</th><th>Start capital</th><th>Rules</th><th>Created</th></tr>
%s</table>

<h2>Run jobs now</h2>
<form method="post" action="/run" class="inline">
<input type="hidden" name="book" value="%d">
<input type="hidden" name="job" value="daily">
<button type="submit">Daily: fill orders, pay dividends, snapshot</button></form>
<form method="post" action="/run" class="inline">
<input type="hidden" name="book" value="%d">
<input type="hidden" name="job" value="weekly">
<button type="submit">Weekly: rebuild recommendations and email preview</button></form>
<p class="src">The weekly job refreshes the whole universe and takes several
minutes; the browser waits until it finishes.</p>

<h2>Cost and tax constants in force</h2>
<table>
<tr><td>Order fee on a 5,000 EUR order</td><td class="money">%.2f EUR</td></tr>
<tr><td>Abgeltungsteuer incl. Soli</td><td class="money">%.3f%%</td></tr>
<tr><td>Sparer-Pauschbetrag per year</td><td class="money">%s EUR</td></tr>
<tr><td>Equity-ETF Teilfreistellung</td><td class="money">%.0f%%</td></tr>
<tr><td>US dividend withholding</td><td class="money">%.0f%%</td></tr>
<tr><td>Gold tax-free after</td><td class="money">%d days</td></tr>
<tr><td>Personal rate on early gold sales</td><td class="money">%.0f%%</td></tr>
</table>""" % (bid, get_setting(conn, 'email', ''),
               SIM['email']['smtp_password_env'],
               'ON' if SIM['email']['enabled'] else 'OFF',
               int(SIM['books']['default_start_capital_eur']), book_rows,
               bid, bid, order_fee(5000, SIM), rate,
               '{:,.0f}'.format(t['allowance_eur']),
               100 * t['etf_teilfreistellung'], 100 * t['us_withholding'],
               t['private_sale_holding_days'], 100 * t['personal_rate'])
    return page(conn, body)


@app.route('/settings/email', methods=['POST'])
def save_email():
    conn = db()
    set_setting(conn, 'email', request.form['email'].strip())
    return redirect('/settings?book=%s&msg=email+saved'
                    % request.form.get('book', 0))


@app.route('/settings/book', methods=['POST'])
def new_book():
    conn = db()
    bid = create_book(conn, request.form['name'].strip(),
                      float(request.form['capital']),
                      request.form.get('rules', ''))
    return redirect('/settings?book=%d&msg=book+created' % bid)


@app.route('/run', methods=['POST'])
def run_job():
    conn = db()
    job = request.form['job']
    try:
        res = run_daily(conn) if job == 'daily' else run_weekly(conn, send=False)
        msg = '%s job finished: %s' % (job, res)
    except Exception as exc:
        msg = '%s job failed: %s' % (job, exc)
    return page(conn, '<p><a href="/">back to recommendations</a></p>', msg=msg)


def main() -> None:
    print('Simulator GUI on http://localhost:8642  (Ctrl+C to stop)')
    app.run(host='127.0.0.1', port=8642, debug=False)


if __name__ == '__main__':
    main()

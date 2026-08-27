"""GUI routes: buy buttons only where the week's list says BUYABLE."""

import pytest

from sim import gui
from sim.broker import create_book, place_order
from sim.db import connect


@pytest.fixture
def client(monkeypatch):
    conn = connect(':memory:')
    b = create_book(conn, 'main', 20_000.0, created_at='2026-08-26')
    conn.executemany(
        'INSERT INTO recommendations(week, symbol, name, source, buyable, '
        'reason, detail, price, currency) VALUES(?,?,?,?,?,?,?,?,?)',
        [('2026-08-26', 'DRH', 'DiamondRock Hospitality', 'LPPL_DIP2', 1, '',
          'votes 3/5', 117.0, 'USD'),
         ('2026-08-26', 'VLO', 'Valero Energy', 'LPPL_DIP2', 0, 'no 4% dip',
          'votes 3/5', 344.5, 'USD'),
         ('2026-08-26', 'MCK', 'McKesson Corporation', 'STEADY_GIANTS', 1, '',
          '5y R2 0.96', 873.5, 'USD'),
         ('2026-08-26', 'SKT', 'Tanger Inc.', 'STEADY_GIANTS', 0,
          'P/E 40.1 above its own history p70', '5y R2 0.91', 39.0, 'USD')])
    conn.commit()
    monkeypatch.setattr(gui, 'db', lambda: conn)
    monkeypatch.setattr(gui, 'eur_prices', lambda c: ({'DRH': 11.0}, 1.17))
    monkeypatch.setattr('sim.signals.warnings_for', lambda syms, cfg: [
        {'symbol': s, 'level': 'clear', 'votes': 0, 'tc_date': None,
         'r2': float('nan'), 'price': 1.0, 'warn': False} for s in syms])
    gui.app.config['TESTING'] = True
    with gui.app.test_client() as c:
        yield c, conn, b


def test_every_page_renders(client):
    c, conn, b = client
    for path in ('/', '/positions', '/orders', '/transactions', '/graphs',
                 '/settings'):
        r = c.get(f'{path}?book={b}')
        assert r.status_code == 200, path
        assert b'Simulator' in r.data


def test_buy_button_only_on_buyable_rows(client):
    c, conn, b = client
    html = c.get(f'/?book={b}').data.decode()
    # both sources appear as their own section, labelled
    assert 'LPPL_DIP2' in html and 'STEADY_GIANTS' in html
    assert 'bubble dip-buyer' in html and 'compounders' in html
    # blocked names show the reason and no buy form
    assert 'BLOCKED - no 4% dip' in html
    assert 'BLOCKED - P/E 40.1 above its own history p70' in html
    for blocked in ('VLO', 'SKT'):
        row = html.split(f'<b>{blocked}</b>')[1].split('</tr>')[0]
        assert 'not buyable' in row and 'action="/buy"' not in row
    for ok in ('DRH', 'MCK'):
        row = html.split(f'<b>{ok}</b>')[1].split('</tr>')[0]
        assert 'action="/buy"' in row
    # gold and the index ETF are always offered
    assert '4GLD.DE' in html and 'SXR8.DE' in html


def test_buy_post_queues_an_order_with_its_source(client):
    c, conn, b = client
    r = c.post('/buy', data={'book': b, 'symbol': 'DRH', 'qty': '25',
                             'source': 'LPPL_DIP2'})
    assert r.status_code == 302 and '/orders' in r.headers['Location']
    o = conn.execute('SELECT * FROM orders').fetchone()
    assert (o['symbol'], o['side'], o['qty'], o['source'], o['status']) \
        == ('DRH', 'BUY', 25, 'LPPL_DIP2', 'PENDING')


def test_pending_order_can_be_cancelled(client):
    c, conn, b = client
    oid = place_order(conn, b, 'DRH', 'BUY', 5, '2026-08-26T18:00')
    c.post('/cancel', data={'book': b, 'id': oid})
    assert conn.execute('SELECT status FROM orders WHERE id=?',
                        (oid,)).fetchone()['status'] == 'CANCELLED'


def test_transactions_csv_downloads(client):
    c, conn, b = client
    r = c.get(f'/transactions.csv?book={b}')
    assert r.status_code == 200
    assert 'attachment' in r.headers['Content-Disposition']
    assert r.data.decode().splitlines()[0].startswith('id,book_id,date,type')


def test_company_names_and_share_prices_are_shown(client):
    c, conn, b = client
    html = c.get(f'/?book={b}').data.decode()
    assert 'DiamondRock Hospitality' in html
    assert 'McKesson Corporation' in html
    assert '117.00 USD' in html and '873.50 USD' in html


def test_suggested_quantity_is_ten_percent_of_the_book(client):
    c, conn, b = client
    html = c.get(f'/?book={b}').data.decode()
    # 20,000 EUR book, 10% = 2,000 EUR; DRH at 117 USD / 1.17 = 100 EUR,
    # so 19 shares (20 would not leave room for the 12.40 EUR fee)
    row = html.split('<b>DRH</b>')[1].split('</tr>')[0]
    assert '19 sh = 1,912 EUR' in row
    assert 'name="qty" min="1" value="19"' in row
    # a much pricier name gets proportionally fewer shares
    row = html.split('<b>MCK</b>')[1].split('</tr>')[0]
    assert 'name="qty" min="1" value="2"' in row   # 873.50 USD = 746.58 EUR

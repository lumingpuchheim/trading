"""Simulator core: worked examples for every fee and tax rule."""

import pytest

from sim.broker import (cash, create_book, credit_dividend, fill_pending,
                        place_order, positions, settle_year)
from sim.costs import affordable_shares, load_sim_config, order_fee
from sim.db import connect
from sim.tax import (ETF_EQUITY, GOLD, STOCK, TaxState, settle_private_sales,
                     tax_on_dividend, tax_on_sale)

CFG = load_sim_config()
GOLD_SYM = CFG['instruments']['gold']['symbol']
ETF_SYM = CFG['instruments']['sp500']['symbol']


@pytest.fixture
def conn():
    c = connect(':memory:')
    yield c
    c.close()


# ------------------------------------------------------------------ fees

def test_order_fee_matches_comdirect_schedule():
    # 1,000 EUR: 4.90 + 2.50 = 7.40 -> below the 9.90 minimum
    assert order_fee(1_000, CFG) == pytest.approx(9.90 + 2.50)
    # 10,000 EUR: 4.90 + 25.00 = 29.90, inside the band
    assert order_fee(10_000, CFG) == pytest.approx(29.90 + 2.50)
    # 30,000 EUR: 4.90 + 75.00 = 79.90 -> capped at 59.90
    assert order_fee(30_000, CFG) == pytest.approx(59.90 + 2.50)


def test_affordable_shares_leaves_room_for_the_fee():
    qty = affordable_shares(1_000.0, 100.0, CFG)
    assert qty == 9  # 10 shares would cost 1000 + fee > 1000
    assert 9 * 100.0 + order_fee(900.0, CFG) <= 1_000.0


# ------------------------------------------------------------- stock tax

def test_stock_gain_uses_allowance_then_26375_percent():
    st = TaxState()
    tax = tax_on_sale(st, STOCK, 2_000.0, 30, 2026, CFG)
    assert tax == pytest.approx(1_000.0 * 0.26375, abs=0.01)  # 263.75


def test_stock_losses_offset_only_later_stock_gains():
    st = TaxState()
    assert tax_on_sale(st, STOCK, -500.0, 30, 2026, CFG) == 0.0
    assert st.pot_stocks == pytest.approx(500.0)
    # 2000 gain - 500 pot - 1000 allowance = 500 taxable
    tax = tax_on_sale(st, STOCK, 2_000.0, 30, 2026, CFG)
    assert tax == pytest.approx(500.0 * 0.26375, abs=0.01)
    assert st.pot_stocks == pytest.approx(0.0)


def test_allowance_is_yearly_and_pots_carry_forward():
    st = TaxState()
    tax_on_sale(st, STOCK, 2_000.0, 30, 2026, CFG)      # uses the 1,000
    assert st.allowance_used == pytest.approx(1_000.0)
    tax_on_sale(st, ETF_EQUITY, -1_000.0, 30, 2026, CFG)  # 700 into general
    tax = tax_on_sale(st, STOCK, 1_000.0, 30, 2027, CFG)  # new year
    # fresh allowance covers what the 700 general pot leaves
    assert tax == pytest.approx(0.0)
    assert st.allowance_used == pytest.approx(300.0)


def test_equity_etf_gets_the_30_percent_exemption():
    st = TaxState()
    tax = tax_on_sale(st, ETF_EQUITY, 2_000.0, 30, 2026, CFG)
    # 2000 x 0.70 = 1400, minus 1000 allowance = 400 taxable
    assert tax == pytest.approx(400.0 * 0.26375, abs=0.01)  # 105.50


# -------------------------------------------------------------- gold tax

def test_gold_is_tax_free_after_one_year():
    st = TaxState()
    assert tax_on_sale(st, GOLD, 5_000.0, 400, 2026, CFG) == 0.0
    assert st.private_gains == 0.0
    assert settle_private_sales(st, 2026, CFG) == 0.0


def test_early_gold_sale_is_a_private_sale_with_a_freigrenze():
    st = TaxState()
    assert tax_on_sale(st, GOLD, 5_000.0, 200, 2026, CFG) == 0.0  # accrued
    assert settle_private_sales(st, 2026, CFG) == pytest.approx(5_000 * 0.42)

    small = TaxState()
    tax_on_sale(small, GOLD, 800.0, 200, 2026, CFG)
    assert settle_private_sales(small, 2026, CFG) == 0.0  # under 1,000


# ------------------------------------------------------------- dividends

def test_us_dividend_credits_withholding_against_german_tax():
    st = TaxState(year=2026, allowance_used=1_000.0)   # allowance spent
    r = tax_on_dividend(st, 100.0, 2026, CFG)
    assert r['withheld_eur'] == pytest.approx(15.0)
    assert r['german_tax_eur'] == pytest.approx(26.375 - 15.0, abs=0.01)
    assert r['net_eur'] == pytest.approx(73.62, abs=0.02)


def test_dividend_inside_the_allowance_pays_no_german_tax():
    st = TaxState()
    r = tax_on_dividend(st, 100.0, 2026, CFG)
    assert r['german_tax_eur'] == 0.0
    assert r['net_eur'] == pytest.approx(85.0)  # US withholding still applies


def test_stock_loss_pot_cannot_offset_dividends():
    st = TaxState(pot_stocks=1_000.0, year=2026, allowance_used=1_000.0)
    r = tax_on_dividend(st, 100.0, 2026, CFG)
    assert r['german_tax_eur'] > 0
    assert st.pot_stocks == pytest.approx(1_000.0)  # untouched


# ---------------------------------------------------------------- broker

def test_buy_then_sell_books_fees_fx_and_cash(conn):
    b = create_book(conn, 'test', 20_000.0, created_at='2026-01-02')
    place_order(conn, b, 'AAA', 'BUY', 10, '2026-01-02T18:00', 'LPPL_DIP2')
    fill_pending(conn, '2026-01-05', {'AAA': 100.0}, 1.25, CFG)
    # 100 USD / 1.25 = 80 EUR; 800 gross + 12.40 fee
    assert positions(conn, b) == {'AAA': 10.0}
    assert cash(conn, b) == pytest.approx(20_000 - 812.40, abs=0.01)

    place_order(conn, b, 'AAA', 'SELL', 10, '2026-02-02T18:00')
    fill_pending(conn, '2026-02-03', {'AAA': 110.0}, 1.25, CFG)
    # 88 EUR x 10 = 880 gross, fee 12.40; gain 80 - both fees = 55.20,
    # covered by the allowance -> no tax withheld
    assert positions(conn, b) == {}
    assert cash(conn, b) == pytest.approx(20_000 - 812.40 + 867.60, abs=0.01)
    row = conn.execute("SELECT * FROM transactions WHERE type='SELL'").fetchone()
    assert row['realized_gain_eur'] == pytest.approx(55.20, abs=0.01)
    assert row['tax_eur'] == 0.0


def test_orders_never_fill_on_the_day_they_were_placed(conn):
    b = create_book(conn, 'future', 10_000.0, created_at='2026-01-02')
    place_order(conn, b, 'AAA', 'BUY', 5, '2026-01-05T09:00')
    assert fill_pending(conn, '2026-01-05', {'AAA': 10.0}, 1.1, CFG) == []
    assert positions(conn, b) == {}
    fill_pending(conn, '2026-01-06', {'AAA': 10.0}, 1.1, CFG)
    assert positions(conn, b) == {'AAA': 5.0}


def test_unaffordable_and_unrecommended_buys_are_rejected(conn):
    b = create_book(conn, 'guard', 1_000.0, created_at='2026-01-02')
    place_order(conn, b, 'AAA', 'BUY', 100, '2026-01-02T18:00')
    res = fill_pending(conn, '2026-01-05', {'AAA': 100.0}, 1.0, CFG)
    assert res[0]['status'] == 'REJECTED' and 'cash' in res[0]['reason']

    place_order(conn, b, 'BBB', 'BUY', 1, '2026-01-05T18:00')
    res = fill_pending(conn, '2026-01-06', {'BBB': 10.0}, 1.0, CFG,
                       buyable={'CCC'})
    assert res[0]['status'] == 'REJECTED'
    # gold and the index ETF bypass the recommendation whitelist
    place_order(conn, b, GOLD_SYM, 'BUY', 1, '2026-01-06T18:00')
    res = fill_pending(conn, '2026-01-07', {GOLD_SYM: 50.0}, 1.0, CFG,
                       buyable=set())
    assert res[0]['status'] == 'FILLED'


def test_fifo_lots_and_gold_holding_period(conn):
    b = create_book(conn, 'fifo', 50_000.0, created_at='2024-01-02')
    place_order(conn, b, GOLD_SYM, 'BUY', 100, '2024-01-02T18:00')
    fill_pending(conn, '2024-01-03', {GOLD_SYM: 50.0}, 1.0, CFG)
    place_order(conn, b, GOLD_SYM, 'BUY', 100, '2026-01-02T18:00')
    fill_pending(conn, '2026-01-05', {GOLD_SYM: 90.0}, 1.0, CFG)

    # selling 100 takes the OLD lot first: held > 1 year -> tax free
    place_order(conn, b, GOLD_SYM, 'SELL', 100, '2026-03-02T18:00')
    fill_pending(conn, '2026-03-03', {GOLD_SYM: 100.0}, 1.0, CFG)
    sell = conn.execute("SELECT * FROM transactions WHERE type='SELL'").fetchone()
    assert sell['realized_gain_eur'] > 4_000        # 50 -> 100 on 100 units
    assert sell['tax_eur'] == 0.0                   # held two years
    assert settle_year(conn, b, 2026, CFG) == 0.0

    # the young lot is a §23 private sale: accrued, then settled at year end
    place_order(conn, b, GOLD_SYM, 'SELL', 100, '2026-03-04T18:00')
    fill_pending(conn, '2026-03-05', {GOLD_SYM: 120.0}, 1.0, CFG)
    due = settle_year(conn, b, 2026, CFG)
    assert due == pytest.approx(0.42 * 2_900, abs=30)  # ~30 EUR/unit gain


def test_dividend_is_paid_into_cash_with_source_label(conn):
    b = create_book(conn, 'div', 20_000.0, created_at='2026-01-02')
    place_order(conn, b, 'KO', 'BUY', 10, '2026-01-02T18:00', 'STEADY_GIANTS')
    fill_pending(conn, '2026-01-05', {'KO': 100.0}, 1.25, CFG)
    before = cash(conn, b)
    res = credit_dividend(conn, b, 'KO', 2.0, '2026-03-02', 1.25, CFG)
    # 10 x 2 USD = 20 USD -> 16 EUR gross, 15% withheld, allowance covers
    assert res['gross_eur'] == pytest.approx(16.0)
    assert res['withheld_eur'] == pytest.approx(2.40)
    assert res['net_eur'] == pytest.approx(13.60)
    assert cash(conn, b) == pytest.approx(before + 13.60, abs=0.01)
    row = conn.execute("SELECT * FROM transactions WHERE type='DIVIDEND'") \
        .fetchone()
    assert row['source'] == 'STEADY_GIANTS'
    lot = conn.execute('SELECT source FROM lots').fetchone()
    assert lot['source'] == 'STEADY_GIANTS'

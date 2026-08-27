"""SQLite state for the simulator (SIMULATOR_SPEC section 8).

Append-only transaction log plus the state derived from it (open FIFO
lots, tax pots, snapshots). Every order, lot and transaction carries the
SOURCE label of the system that recommended it (LPPL_DIP2 /
STEADY_GIANTS / MANUAL), so a position's origin is traceable for life.
"""

import sqlite3
from pathlib import Path

ROOT = Path(__file__).parent.parent
DB_PATH = Path(__file__).parent / 'sim.db'

SCHEMA = """
CREATE TABLE IF NOT EXISTS books (
    id INTEGER PRIMARY KEY,
    name TEXT UNIQUE NOT NULL,
    start_capital_eur REAL NOT NULL,
    rules TEXT DEFAULT '',
    created_at TEXT NOT NULL);

CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY, value TEXT);

CREATE TABLE IF NOT EXISTS orders (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,                -- BUY | SELL
    qty INTEGER NOT NULL,
    source TEXT NOT NULL DEFAULT 'MANUAL',
    placed_at TEXT NOT NULL,           -- ISO timestamp
    status TEXT NOT NULL DEFAULT 'PENDING',   -- PENDING|FILLED|CANCELLED|REJECTED
    fill_date TEXT, note TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS transactions (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    type TEXT NOT NULL,                -- DEPOSIT|BUY|SELL|DIVIDEND|TAX
    symbol TEXT DEFAULT '',
    source TEXT DEFAULT '',
    qty REAL DEFAULT 0,
    price_eur REAL DEFAULT 0,
    gross_eur REAL DEFAULT 0,
    fee_eur REAL DEFAULT 0,
    tax_eur REAL DEFAULT 0,
    withheld_eur REAL DEFAULT 0,
    realized_gain_eur REAL DEFAULT 0,
    cash_delta_eur REAL NOT NULL,
    fx REAL DEFAULT 1.0,
    note TEXT DEFAULT '');

CREATE TABLE IF NOT EXISTS lots (
    id INTEGER PRIMARY KEY,
    book_id INTEGER NOT NULL,
    symbol TEXT NOT NULL,
    asset_class TEXT NOT NULL,
    source TEXT NOT NULL DEFAULT 'MANUAL',
    opened_date TEXT NOT NULL,
    qty_open REAL NOT NULL,
    cost_eur_per_share REAL NOT NULL,
    fee_eur_open REAL NOT NULL);       -- buy fee still attached to qty_open

CREATE TABLE IF NOT EXISTS tax_pots (
    book_id INTEGER PRIMARY KEY,
    pot_stocks REAL DEFAULT 0,
    pot_general REAL DEFAULT 0,
    year INTEGER DEFAULT 0,
    allowance_used REAL DEFAULT 0,
    private_gains REAL DEFAULT 0,
    private_settled_tax REAL DEFAULT 0);

CREATE TABLE IF NOT EXISTS snapshots (
    book_id INTEGER NOT NULL,
    date TEXT NOT NULL,
    equity_eur REAL NOT NULL,
    cash_eur REAL NOT NULL,
    n_positions INTEGER NOT NULL,
    PRIMARY KEY (book_id, date));

CREATE TABLE IF NOT EXISTS recommendations (
    id INTEGER PRIMARY KEY,
    week TEXT NOT NULL,                -- ISO date of the email
    symbol TEXT NOT NULL,
    name TEXT DEFAULT '',              -- company name
    source TEXT NOT NULL,              -- LPPL_DIP2 | STEADY_GIANTS
    buyable INTEGER NOT NULL,
    reason TEXT DEFAULT '',
    detail TEXT DEFAULT '',
    price REAL DEFAULT 0,              -- RAW last close, own currency
    currency TEXT DEFAULT 'USD');

CREATE INDEX IF NOT EXISTS ix_tx_book ON transactions(book_id, date);
CREATE INDEX IF NOT EXISTS ix_lots_book ON lots(book_id, symbol);
CREATE INDEX IF NOT EXISTS ix_rec_week ON recommendations(week, symbol);
"""


MIGRATIONS = {'recommendations': [('name', "TEXT DEFAULT ''"),
                                  ('price', 'REAL DEFAULT 0'),
                                  ('currency', "TEXT DEFAULT 'USD'")]}


def connect(path: Path | str | None = None) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path or DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    for table, cols in MIGRATIONS.items():      # add columns a older db lacks
        have = {r['name'] for r in conn.execute(f'PRAGMA table_info({table})')}
        for col, decl in cols:
            if col not in have:
                conn.execute(f'ALTER TABLE {table} ADD COLUMN {col} {decl}')
    conn.commit()
    return conn


def get_setting(conn: sqlite3.Connection, key: str, default: str = '') -> str:
    row = conn.execute('SELECT value FROM settings WHERE key = ?',
                       (key,)).fetchone()
    return row['value'] if row else default


def set_setting(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute('INSERT INTO settings(key, value) VALUES(?, ?) '
                 'ON CONFLICT(key) DO UPDATE SET value = excluded.value',
                 (key, value))
    conn.commit()

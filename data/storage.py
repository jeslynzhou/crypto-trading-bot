import sqlite3
import os
from datetime import datetime
from typing import Optional


DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "trading_bot.db")


def get_connection(db_path: str = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db(db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS candles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            interval TEXT NOT NULL,
            open_time INTEGER NOT NULL,
            open REAL NOT NULL,
            high REAL NOT NULL,
            low REAL NOT NULL,
            close REAL NOT NULL,
            volume REAL NOT NULL,
            close_time INTEGER NOT NULL,
            quote_volume REAL NOT NULL,
            num_trades INTEGER NOT NULL,
            UNIQUE(symbol, interval, open_time)
        );

        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL DEFAULT 'default',
            timestamp TEXT NOT NULL,
            symbol TEXT NOT NULL,
            side TEXT NOT NULL,
            price REAL NOT NULL,
            quantity REAL NOT NULL,
            strategy_name TEXT NOT NULL,
            reason TEXT,
            order_id TEXT,
            status TEXT DEFAULT 'FILLED',
            pnl REAL DEFAULT 0.0,
            fee REAL DEFAULT 0.0,
            leverage INTEGER DEFAULT 1
        );

        CREATE INDEX IF NOT EXISTS idx_candles_symbol_interval
            ON candles(symbol, interval, open_time);
        CREATE INDEX IF NOT EXISTS idx_trades_timestamp
            ON trades(timestamp);
        CREATE INDEX IF NOT EXISTS idx_trades_strategy
            ON trades(strategy_name);
        CREATE INDEX IF NOT EXISTS idx_trades_symbol
            ON trades(symbol);

        CREATE TABLE IF NOT EXISTS user_strategies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id TEXT NOT NULL,
            name TEXT NOT NULL,
            code TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            updated_at TEXT NOT NULL DEFAULT (datetime('now')),
            UNIQUE(user_id, name)
        );
    """)
    _migrate_trades_table(conn)
    conn.execute("CREATE INDEX IF NOT EXISTS idx_trades_user ON trades(user_id)")
    conn.commit()
    conn.close()


def _migrate_trades_table(conn: sqlite3.Connection):
    cursor = conn.execute("PRAGMA table_info(trades)")
    columns = {row[1] for row in cursor.fetchall()}
    if "fee" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN fee REAL DEFAULT 0.0")
    if "leverage" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN leverage INTEGER DEFAULT 1")
    if "user_id" not in columns:
        conn.execute("ALTER TABLE trades ADD COLUMN user_id TEXT NOT NULL DEFAULT 'default'")


def insert_candle(candle: dict, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("""
        INSERT OR REPLACE INTO candles
        (symbol, interval, open_time, open, high, low, close, volume, close_time, quote_volume, num_trades)
        VALUES (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume, :close_time, :quote_volume, :num_trades)
    """, candle)
    conn.commit()
    conn.close()


def insert_candles(candles: list[dict], db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.executemany("""
        INSERT OR REPLACE INTO candles
        (symbol, interval, open_time, open, high, low, close, volume, close_time, quote_volume, num_trades)
        VALUES (:symbol, :interval, :open_time, :open, :high, :low, :close, :volume, :close_time, :quote_volume, :num_trades)
    """, candles)
    conn.commit()
    conn.close()


def get_candles(symbol: str, interval: str, limit: int = 500,
                start_time: Optional[int] = None, end_time: Optional[int] = None,
                db_path: str = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    query = "SELECT * FROM candles WHERE symbol = ? AND interval = ?"
    params: list = [symbol, interval]

    if start_time is not None:
        query += " AND open_time >= ?"
        params.append(start_time)
    if end_time is not None:
        query += " AND open_time <= ?"
        params.append(end_time)

    query = f"SELECT * FROM ({query} ORDER BY open_time DESC LIMIT ?) ORDER BY open_time ASC"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def insert_trade(trade: dict, db_path: str = DB_PATH):
    trade.setdefault("fee", 0.0)
    trade.setdefault("leverage", 1)
    trade.setdefault("user_id", "default")
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO trades
        (user_id, timestamp, symbol, side, price, quantity, strategy_name, reason, order_id, status, pnl, fee, leverage)
        VALUES (:user_id, :timestamp, :symbol, :side, :price, :quantity, :strategy_name, :reason, :order_id, :status, :pnl, :fee, :leverage)
    """, trade)
    conn.commit()
    conn.close()


def get_trades(strategy_name: Optional[str] = None, symbol: Optional[str] = None,
               limit: int = 100, user_id: Optional[str] = None,
               db_path: str = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    query = "SELECT * FROM trades WHERE 1=1"
    params: list = []

    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    if strategy_name:
        query += " AND strategy_name = ?"
        params.append(strategy_name)
    if symbol:
        query += " AND symbol = ?"
        params.append(symbol)

    query += " ORDER BY timestamp DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(query, params).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_strategy_pnl(user_id: Optional[str] = None,
                     db_path: str = DB_PATH) -> dict[str, dict]:
    conn = get_connection(db_path)
    query = """
        SELECT strategy_name,
               COUNT(*) as num_trades,
               COALESCE(SUM(pnl), 0) as total_pnl,
               COALESCE(SUM(fee), 0) as total_fees,
               COALESCE(SUM(pnl) - SUM(fee), 0) as net_pnl
        FROM trades
    """
    params: list = []
    if user_id:
        query += " WHERE user_id = ?"
        params.append(user_id)
    query += " GROUP BY strategy_name"
    rows = conn.execute(query, params).fetchall()
    conn.close()
    return {row["strategy_name"]: dict(row) for row in rows}


def clear_trades(user_id: Optional[str] = None, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    if user_id:
        conn.execute("DELETE FROM trades WHERE user_id = ?", (user_id,))
    else:
        conn.execute("DELETE FROM trades")
    conn.commit()
    conn.close()


def get_portfolio_value(initial_capital: float = 1000.0, user_id: Optional[str] = None,
                        db_path: str = DB_PATH) -> float:
    conn = get_connection(db_path)
    if user_id:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl) - SUM(fee), 0) as net_pnl FROM trades WHERE user_id = ?",
            (user_id,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT COALESCE(SUM(pnl) - SUM(fee), 0) as net_pnl FROM trades"
        ).fetchone()
    conn.close()
    return initial_capital + row["net_pnl"]


def get_daily_pnl(date: Optional[str] = None, user_id: Optional[str] = None,
                  db_path: str = DB_PATH) -> float:
    if date is None:
        date = datetime.utcnow().strftime("%Y-%m-%d")
    conn = get_connection(db_path)
    query = "SELECT COALESCE(SUM(pnl) - SUM(fee), 0) as daily_pnl FROM trades WHERE timestamp LIKE ?"
    params: list = [f"{date}%"]
    if user_id:
        query += " AND user_id = ?"
        params.append(user_id)
    row = conn.execute(query, params).fetchone()
    conn.close()
    return row["daily_pnl"]


# ── User Strategies ──

def save_user_strategy(user_id: str, name: str, code: str, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("""
        INSERT INTO user_strategies (user_id, name, code, updated_at)
        VALUES (?, ?, ?, datetime('now'))
        ON CONFLICT(user_id, name) DO UPDATE SET code=excluded.code, updated_at=datetime('now')
    """, (user_id, name, code))
    conn.commit()
    conn.close()


def get_user_strategies(user_id: str, db_path: str = DB_PATH) -> list[dict]:
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT name, code FROM user_strategies WHERE user_id = ? ORDER BY name",
        (user_id,)
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]


def get_user_strategy(user_id: str, name: str, db_path: str = DB_PATH) -> Optional[str]:
    conn = get_connection(db_path)
    row = conn.execute(
        "SELECT code FROM user_strategies WHERE user_id = ? AND name = ?",
        (user_id, name)
    ).fetchone()
    conn.close()
    return row["code"] if row else None


def delete_user_strategy(user_id: str, name: str, db_path: str = DB_PATH):
    conn = get_connection(db_path)
    conn.execute("DELETE FROM user_strategies WHERE user_id = ? AND name = ?", (user_id, name))
    conn.commit()
    conn.close()

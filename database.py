# database.py - Robust SQLite & PostgreSQL storage for trading data, active trades, and audit logs

import os
import csv
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

DB_PATH = Path("trading_bot.db")
MARKET_TIMEZONE = ZoneInfo("Asia/Kolkata")
DATABASE_URL = os.getenv("DATABASE_URL")


def now_ist() -> datetime:
    return datetime.now(MARKET_TIMEZONE)


def is_postgres() -> bool:
    return bool(DATABASE_URL and ("postgres://" in DATABASE_URL or "postgresql://" in DATABASE_URL))


def get_db_connection():
    if is_postgres():
        try:
            import psycopg2
            import psycopg2.extras
            pg_url = DATABASE_URL.replace("postgres://", "postgresql://", 1)
            if "render.com" in pg_url and "sslmode" not in pg_url:
                separator = "&" if "?" in pg_url else "?"
                pg_url += f"{separator}sslmode=require"
            conn = psycopg2.connect(pg_url, connect_timeout=10)
            return conn
        except Exception as e:
            print(f"[DB Warning] Could not connect to PostgreSQL ({e}), falling back to SQLite.")

    conn = sqlite3.connect(str(DB_PATH), timeout=10.0)
    conn.row_factory = sqlite3.Row
    return conn


def _adapt_query(query: str, is_pg: bool) -> str:
    if is_pg:
        query = query.replace("INTEGER PRIMARY KEY AUTOINCREMENT", "SERIAL PRIMARY KEY")
        query = query.replace("?", "%s")
    return query


def init_database() -> None:
    """Initialize database tables if they do not exist."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()

        # 1. Trades table (Live and Paper)
        cursor.execute(_adapt_query("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id TEXT UNIQUE,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            event_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            underlying TEXT NOT NULL,
            option_symbol TEXT NOT NULL,
            display_symbol TEXT,
            option_type TEXT NOT NULL,
            trade_type TEXT NOT NULL,
            mode TEXT NOT NULL,
            status TEXT NOT NULL,
            entry_price REAL NOT NULL,
            exit_price REAL,
            quantity INTEGER NOT NULL,
            lot_size INTEGER NOT NULL,
            lots INTEGER NOT NULL,
            target_1 REAL,
            target_2 REAL,
            target_3 REAL,
            stop_loss REAL,
            pnl REAL DEFAULT 0.0,
            gross_pnl REAL DEFAULT 0.0,
            charges REAL DEFAULT 0.0,
            exit_reason TEXT,
            entry_reason TEXT,
            strategy_score REAL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            closed_at TIMESTAMP
        )
        """, is_pg))

        # 2. Blocked & Decisions table
        cursor.execute(_adapt_query("""
        CREATE TABLE IF NOT EXISTS signal_decisions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            time TEXT NOT NULL,
            event_time TEXT NOT NULL,
            symbol TEXT NOT NULL,
            signal TEXT NOT NULL,
            final_decision TEXT NOT NULL,
            setup_score REAL,
            adx REAL,
            rsi REAL,
            close_price REAL,
            blocked_reason TEXT,
            details_json TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """, is_pg))

        # 3. System Events & Logs table
        cursor.execute(_adapt_query("""
        CREATE TABLE IF NOT EXISTS system_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event_time TEXT NOT NULL,
            level TEXT NOT NULL,
            component TEXT NOT NULL,
            message TEXT NOT NULL,
            extra_data TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        """, is_pg))

        # 4. Daily Metrics Summary table
        cursor.execute(_adapt_query("""
        CREATE TABLE IF NOT EXISTS daily_metrics (
            date TEXT PRIMARY KEY,
            mode TEXT NOT NULL,
            total_trades INTEGER DEFAULT 0,
            wins INTEGER DEFAULT 0,
            losses INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0.0,
            total_pnl REAL DEFAULT 0.0,
            max_drawdown REAL DEFAULT 0.0,
            updated_at TEXT NOT NULL
        )
        """, is_pg))

        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_date ON trades(date)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)")
        cursor.execute("CREATE INDEX IF NOT EXISTS idx_decisions_date ON signal_decisions(date)")

        conn.commit()
        cursor.execute("SELECT COUNT(*) FROM trades")
        trade_count = cursor.fetchone()[0]
        cursor.close()
        if trade_count == 0:
            db_import_csv_trades()
    except Exception as e:
        print(f"[DB Init Error] {e}")
    finally:
        conn.close()


def db_save_trade(trade: dict) -> None:
    """Insert or update a trade record in SQLite / PostgreSQL."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        trade_id = trade.get("trade_id") or f"{trade.get('option_symbol')}_{trade.get('opened_at') or now_ist().isoformat()}"
        cursor = conn.cursor()
        query = _adapt_query("""
        INSERT INTO trades (
            trade_id, date, time, event_time, symbol, underlying, option_symbol, display_symbol,
            option_type, trade_type, mode, status, entry_price, exit_price, quantity, lot_size,
            lots, target_1, target_2, target_3, stop_loss, pnl, gross_pnl, charges,
            exit_reason, entry_reason, strategy_score, closed_at
        ) VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
        )
        ON CONFLICT(trade_id) DO UPDATE SET
            status = excluded.status,
            exit_price = excluded.exit_price,
            pnl = excluded.pnl,
            gross_pnl = excluded.gross_pnl,
            charges = excluded.charges,
            exit_reason = excluded.exit_reason,
            closed_at = excluded.closed_at
        """, is_pg)

        params = (
            trade_id,
            trade.get("date") or now_ist().strftime("%Y-%m-%d"),
            trade.get("time") or now_ist().strftime("%H:%M:%S"),
            trade.get("event_time") or now_ist().isoformat(),
            trade.get("underlying") or "NIFTY",
            trade.get("underlying") or "NIFTY",
            trade.get("option_symbol", ""),
            trade.get("display_symbol", ""),
            trade.get("option_type", "CE"),
            trade.get("trade_type", "BUY"),
            trade.get("mode", "PAPER"),
            trade.get("status", "OPEN"),
            float(trade.get("entry_price") or 0),
            float(trade.get("exit_price") or 0) if trade.get("exit_price") else None,
            int(float(trade.get("quantity") or 0)),
            int(float(trade.get("lot_size") or 1)),
            int(float(trade.get("lots") or 1)),
            float(trade.get("target_1") or 0) if trade.get("target_1") else None,
            float(trade.get("target_2") or 0) if trade.get("target_2") else None,
            float(trade.get("target_3") or trade.get("target") or 0) if trade.get("target_3") or trade.get("target") else None,
            float(trade.get("stop_loss") or 0),
            float(trade.get("pnl") or 0),
            float(trade.get("gross_pnl") or 0),
            float(trade.get("paper_total_charges") or 0),
            trade.get("exit_reason") or trade.get("reason"),
            trade.get("entry_reason"),
            float(trade.get("strategy_score") or trade.get("SETUP_SCORE") or 0),
            trade.get("closed_at"),
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        db_log_event("ERROR", "db_save_trade", str(e))
    finally:
        conn.close()


def db_log_signal_decision(symbol: str, signal: dict, final_decision: str, reason: str, extra: dict | None = None) -> None:
    """Log every signal decision (executed or blocked) to SQLite / PostgreSQL."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("""
        INSERT INTO signal_decisions (
            date, time, event_time, symbol, signal, final_decision,
            setup_score, adx, rsi, close_price, blocked_reason, details_json
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, is_pg)
        params = (
            now_ist().strftime("%Y-%m-%d"),
            now_ist().strftime("%H:%M:%S"),
            now_ist().isoformat(),
            symbol,
            signal.get("SIGNAL", "HOLD"),
            final_decision,
            float(signal.get("SETUP_SCORE") or 0),
            float(signal.get("ADX_14") or 0),
            float(signal.get("RSI_14") or 0),
            float(signal.get("close") or 0),
            reason,
            json.dumps(extra or {}, default=str),
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Error] db_log_signal_decision: {e}")
    finally:
        conn.close()


def db_log_event(level: str, component: str, message: str, extra: dict | None = None) -> None:
    """Log system operational events and warnings to SQLite / PostgreSQL."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("""
        INSERT INTO system_logs (event_time, level, component, message, extra_data)
        VALUES (?, ?, ?, ?, ?)
        """, is_pg)
        params = (
            now_ist().isoformat(),
            level.upper(),
            component,
            message,
            json.dumps(extra or {}, default=str) if extra else None,
        )
        cursor.execute(query, params)
        conn.commit()
        cursor.close()
    except Exception as e:
        print(f"[DB Error] db_log_event: {e}")
    finally:
        conn.close()


def db_get_recent_trades(limit: int = 50) -> list[dict]:
    """Fetch recent trades for dashboard or analysis."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("SELECT * FROM trades ORDER BY id DESC LIMIT ?", is_pg)
        cursor.execute(query, (limit,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()




def db_get_pnl_summary(mode: str = "PAPER") -> float:
    """Return total realized P&L from closed trades in database."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("SELECT COALESCE(SUM(pnl), 0.0) FROM trades WHERE status = 'CLOSED' AND mode = ?", is_pg)
        cursor.execute(query, (mode,))
        val = cursor.fetchone()[0]
        cursor.close()
        return round(float(val or 0.0), 2)
    except Exception:
        return 0.0
    finally:
        conn.close()


def db_get_distinct_dates() -> list[str]:
    """Return list of distinct trading dates from database."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT date FROM trades UNION SELECT DISTINCT date FROM signal_decisions ORDER BY 1 DESC")
        dates = [str(row[0]) for row in cursor.fetchall() if row[0]]
        cursor.close()
        return dates
    except Exception:
        return []
    finally:
        conn.close()


def db_get_trades_for_date(day: str, mode: str | None = None) -> list[dict]:
    """Return all trades for a given date from database."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        if mode:
            query = _adapt_query("SELECT * FROM trades WHERE date = ? AND mode = ? ORDER BY id ASC", is_pg)
            cursor.execute(query, (day, mode))
        else:
            query = _adapt_query("SELECT * FROM trades WHERE date = ? ORDER BY id ASC", is_pg)
            cursor.execute(query, (day,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def db_get_decisions_for_date(day: str) -> list[dict]:
    """Return all signal decisions for a given date from database."""
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    try:
        cursor = conn.cursor()
        query = _adapt_query("SELECT * FROM signal_decisions WHERE date = ? ORDER BY id ASC", is_pg)
        cursor.execute(query, (day,))
        if is_pg:
            columns = [desc[0] for desc in cursor.description]
            rows = [dict(zip(columns, row)) for row in cursor.fetchall()]
        else:
            rows = [dict(row) for row in cursor.fetchall()]
        cursor.close()
        return rows
    except Exception:
        return []
    finally:
        conn.close()


def db_import_csv_trades(csv_path: str = "logs/trade_history.csv") -> int:
    """Import historical trades from CSV into database if not already imported."""
    p = Path(csv_path)
    if not p.exists():
        return 0

    count = 0
    try:
        with p.open(encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                try:
                    db_save_trade(row)
                    count += 1
                except Exception:
                    pass
    except Exception:
        pass
    return count


# Auto-initialize database schema on import
init_database()

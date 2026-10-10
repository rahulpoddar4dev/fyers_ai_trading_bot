# view_db.py - Inspect database tables and view trades & audit data easily

import argparse
import os
import sqlite3
from database import get_db_connection, is_postgres, DB_PATH


def print_table(title: str, headers: list[str], rows: list[list]):
    print(f"\n{'='*20} {title} {'='*20}")
    if not rows:
        print("  (No data found)")
        return

    # Calculate column widths
    col_widths = [len(h) for h in headers]
    for row in rows:
        for idx, val in enumerate(row):
            col_widths[idx] = max(col_widths[idx], len(str(val) if val is not None else "-"))

    # Format header
    header_str = " | ".join(h.ljust(col_widths[i]) for i, h in enumerate(headers))
    sep_str = "-+-".join("-" * col_widths[i] for i in range(len(headers)))
    print(header_str)
    print(sep_str)

    # Format rows
    for row in rows:
        row_str = " | ".join(str(val if val is not None else "-").ljust(col_widths[i]) for i, val in enumerate(row))
        print(row_str)


def view_database(target_table: str = "all", limit: int = 15):
    conn = get_db_connection()
    is_pg = hasattr(conn, "closed") and not isinstance(conn, sqlite3.Connection)
    db_type = "PostgreSQL (Cloud / Render)" if is_pg else f"SQLite ({DB_PATH.name})"
    print(f"\n[Database Connected]: {db_type}")

    cursor = conn.cursor()

    try:
        # 1. Summary of row counts
        tables = ["trades", "signal_decisions", "system_logs", "daily_metrics"]
        summary_rows = []
        for tbl in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {tbl}")
                count = cursor.fetchone()[0]
                summary_rows.append([tbl, count])
            except Exception:
                summary_rows.append([tbl, "N/A"])

        print_table("DATABASE TABLES SUMMARY", ["Table Name", "Total Records"], summary_rows)

        # 2. View Trades
        if target_table in {"all", "trades"}:
            cursor.execute(f"""
                SELECT id, date, time, symbol, option_symbol, option_type, trade_type, mode, status, entry_price, exit_price, pnl, exit_reason
                FROM trades ORDER BY id DESC LIMIT {limit}
            """)
            trade_rows = cursor.fetchall()
            headers = ["ID", "Date", "Time", "Index", "Option Contract", "Type", "Side", "Mode", "Status", "Entry", "Exit", "P&L (Rs)", "Exit Reason"]
            rows = [[r[i] for i in range(len(headers))] for r in trade_rows]
            print_table(f"LATEST TRADES (Last {limit})", headers, rows)

        # 3. View Signal Decisions
        if target_table in {"all", "decisions"}:
            cursor.execute(f"""
                SELECT id, time, symbol, signal, final_decision, setup_score, adx, rsi, blocked_reason
                FROM signal_decisions ORDER BY id DESC LIMIT {limit}
            """)
            dec_rows = cursor.fetchall()
            headers = ["ID", "Time", "Symbol", "Signal", "Decision", "Score", "ADX", "RSI", "Reason / Block"]
            rows = [[r[i] for i in range(len(headers))] for r in dec_rows]
            print_table(f"LATEST DECISIONS & AUDIT LOGS (Last {limit})", headers, rows)

        # 4. View System Logs
        if target_table in {"all", "logs"}:
            cursor.execute(f"""
                SELECT id, event_time, level, component, message
                FROM system_logs ORDER BY id DESC LIMIT {limit}
            """)
            log_rows = cursor.fetchall()
            headers = ["ID", "Event Time", "Level", "Component", "Message"]
            rows = [[r[i] for i in range(len(headers))] for r in log_rows]
            print_table(f"LATEST SYSTEM EVENTS (Last {limit})", headers, rows)

    except Exception as e:
        print(f"[Error querying database]: {e}")
    finally:
        cursor.close()
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="View trading bot database tables and records")
    parser.add_argument("--table", choices=["all", "trades", "decisions", "logs"], default="all", help="Target table to view")
    parser.add_argument("--limit", type=int, default=15, help="Number of records to display")
    args = parser.parse_args()

    view_database(target_table=args.table, limit=args.limit)

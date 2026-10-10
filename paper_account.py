# paper_account.py - Rolling paper account balance from closed trade P&L

import csv
import json
from pathlib import Path

import config
from log_utils import csv_datetime


ACCOUNT_FILE = Path("paper_account.json")
HISTORY_FILE = Path("logs") / "trade_history.csv"


def starting_balance() -> float:
    return float(config.PAPER_BALANCE_OVERRIDE or 0)


def realized_pnl() -> float:
    try:
        from database import db_get_pnl_summary
        mode = getattr(config, "TRADING_MODE", "PAPER")
        db_pnl = db_get_pnl_summary("PAPER" if mode in {"", "PAPER"} else mode)
        if db_pnl != 0.0:
            return db_pnl
    except Exception:
        pass

    if not HISTORY_FILE.exists():
        return 0.0

    total = 0.0
    with HISTORY_FILE.open() as csv_file:
        for row in csv.DictReader(csv_file):
            if row.get("event") == "EXIT":
                total += float(row.get("pnl") or 0)

    return round(total, 2)


def build_paper_account() -> dict:
    initial = starting_balance()
    pnl = realized_pnl()
    return {
        "updated_at": csv_datetime(),
        "starting_balance": round(initial, 2),
        "realized_pnl": pnl,
        "available_balance": round(initial + pnl, 2),
    }


def refresh_paper_account() -> dict:
    account = build_paper_account()
    ACCOUNT_FILE.write_text(json.dumps(account, indent=2))
    return account


def get_paper_balance() -> float:
    return float(refresh_paper_account()["available_balance"])

# paper_fill_engine.py - Realistic paper fills using bid/ask, slippage, and charges

import config
from order_manager import round_to_tick


def _to_float(value, default=0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _paper_charges(price: float, quantity: int) -> float:
    turnover = max(price, 0) * max(quantity, 0)
    percentage_charge = turnover * (float(config.PAPER_CHARGES_PERCENT) / 100)
    return round(float(config.PAPER_BROKERAGE_PER_ORDER) + percentage_charge, 2)


def apply_paper_entry_fill(option_trade: dict) -> tuple[dict, dict]:
    trade = dict(option_trade)
    if not config.REALISTIC_PAPER_FILL_ENABLED:
        return trade, {"status": "FILLED", "reason": "Realistic paper fill disabled."}

    entry_price = _to_float(trade.get("entry_price"))
    best_ask = _to_float(trade.get("best_ask"))
    spread_percent = _to_float(trade.get("bid_ask_spread_percent"))
    quantity = int(_to_float(trade.get("quantity")))
    reference_price = best_ask or entry_price

    if reference_price <= 0 or quantity <= 0:
        trade["paper_fill_status"] = "MISSED"
        trade["paper_missed_fill_reason"] = "No valid paper entry price or quantity."
        return trade, {"status": "MISSED", "reason": trade["paper_missed_fill_reason"]}

    if spread_percent and spread_percent > float(config.PAPER_MAX_FILL_SPREAD_PERCENT):
        trade["paper_fill_status"] = "MISSED"
        trade["paper_missed_fill_reason"] = (
            f"Spread {spread_percent}% is wider than paper fill limit "
            f"{config.PAPER_MAX_FILL_SPREAD_PERCENT}%."
        )
        return trade, {"status": "MISSED", "reason": trade["paper_missed_fill_reason"]}

    slippage = reference_price * (float(config.PAPER_ENTRY_SLIPPAGE_PERCENT) / 100)
    fill_price = round_to_tick(reference_price + slippage)
    limit_tolerance = entry_price * (1 + float(config.PAPER_LIMIT_FILL_TOLERANCE_PERCENT) / 100)
    if entry_price and fill_price > limit_tolerance:
        trade["paper_fill_status"] = "MISSED"
        trade["paper_missed_fill_reason"] = (
            f"Paper ask/slippage fill Rs {fill_price} exceeded allowed entry tolerance Rs {round(limit_tolerance, 2)}."
        )
        return trade, {"status": "MISSED", "reason": trade["paper_missed_fill_reason"]}

    planned_entry = entry_price
    trade["planned_entry_price"] = planned_entry
    trade["entry_price"] = round(float(fill_price), 2)
    trade["average_price"] = trade["entry_price"]
    trade["paper_fill_status"] = "FILLED"
    trade["paper_entry_reference_price"] = round(reference_price, 2)
    trade["paper_entry_slippage"] = round(trade["entry_price"] - reference_price, 2)
    trade["paper_entry_charges"] = _paper_charges(trade["entry_price"], quantity)

    stop_loss = _to_float(trade.get("stop_loss"))
    if planned_entry > 0 and stop_loss > 0 and planned_entry != trade["entry_price"]:
        stop_gap = max(planned_entry - stop_loss, 0)
        target_1_gap = max(_to_float(trade.get("target_1")) - planned_entry, 0)
        target_2_gap = max(_to_float(trade.get("target_2")) - planned_entry, 0)
        target_3_gap = max(_to_float(trade.get("target_3") or trade.get("target")) - planned_entry, 0)
        trade["stop_loss"] = round(max(trade["entry_price"] - stop_gap, 0), 2)
        if target_1_gap:
            trade["target_1"] = round(trade["entry_price"] + target_1_gap, 2)
        if target_2_gap:
            trade["target_2"] = round(trade["entry_price"] + target_2_gap, 2)
        if target_3_gap:
            trade["target_3"] = round(trade["entry_price"] + target_3_gap, 2)
            trade["target"] = trade["target_3"]

    trade["estimated_required_amount"] = round(trade["entry_price"] * quantity, 2)
    trade["estimated_risk_amount"] = round(max(trade["entry_price"] - _to_float(trade.get("stop_loss")), 0) * quantity, 2)
    return trade, {"status": "FILLED", "reason": "Paper entry filled with realistic bid/ask/slippage."}


def apply_paper_exit_fill(trade: dict, exit_price: float) -> tuple[float, dict]:
    if not config.REALISTIC_PAPER_FILL_ENABLED or trade.get("mode") != "PAPER":
        return round(float(exit_price), 2), {}

    reference_price = _to_float(trade.get("best_bid")) or _to_float(exit_price)
    slippage = reference_price * (float(config.PAPER_EXIT_SLIPPAGE_PERCENT) / 100)
    filled_price = max(round_to_tick(reference_price - slippage), 0)
    quantity = int(_to_float(trade.get("remaining_quantity") or trade.get("quantity")))
    return round(float(filled_price), 2), {
        "paper_exit_reference_price": round(reference_price, 2),
        "paper_exit_slippage": round(reference_price - filled_price, 2),
        "paper_exit_charges": _paper_charges(float(filled_price), quantity),
    }

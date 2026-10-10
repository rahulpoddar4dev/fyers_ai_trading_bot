# market_depth.py - Option bid/ask depth helpers for execution quality

import config
from order_manager import round_to_tick


def extract_market_depth(option_quote: dict, quantity: int = 0) -> dict:
    depth = option_quote.get("depth") or {}
    buy_depth = depth.get("buy") or []
    sell_depth = depth.get("sell") or []
    best_bid = _level_price(buy_depth)
    best_ask = _level_price(sell_depth)
    bid_qty = _level_quantity(buy_depth)
    ask_qty = _level_quantity(sell_depth)
    ltp = float(option_quote.get("last_price") or 0)
    spread = round(best_ask - best_bid, 2) if best_bid and best_ask else 0
    reference_price = best_ask or ltp or best_bid
    spread_percent = round((spread / reference_price) * 100, 2) if reference_price else 0
    required_top_qty = int(quantity * config.MIN_TOP_DEPTH_QUANTITY_MULTIPLIER) if quantity else 0

    return {
        "best_bid": best_bid,
        "best_ask": best_ask,
        "bid_qty": bid_qty,
        "ask_qty": ask_qty,
        "spread": spread,
        "spread_percent": spread_percent,
        "top_depth_required_qty": required_top_qty,
        "top_depth_sufficient": ask_qty >= required_top_qty if required_top_qty else True,
    }


def apply_market_depth_to_trade(option_trade: dict, option_quote: dict) -> dict:
    if not config.MARKET_DEPTH_ENABLED:
        return option_trade

    trade = dict(option_trade)
    quantity = int(float(trade.get("quantity") or 0))
    depth = extract_market_depth(option_quote, quantity)
    trade.update({
        "best_bid": depth["best_bid"],
        "best_ask": depth["best_ask"],
        "bid_qty": depth["bid_qty"],
        "ask_qty": depth["ask_qty"],
        "bid_ask_spread": depth["spread"],
        "bid_ask_spread_percent": depth["spread_percent"],
        "top_depth_required_qty": depth["top_depth_required_qty"],
        "top_depth_sufficient": depth["top_depth_sufficient"],
    })

    block_reasons = []
    if depth["spread_percent"] and depth["spread_percent"] > config.MAX_BID_ASK_SPREAD_PERCENT:
        block_reasons.append(
            f"Spread {depth['spread_percent']}% is above max {config.MAX_BID_ASK_SPREAD_PERCENT}%."
        )
    if not depth["top_depth_sufficient"]:
        block_reasons.append(
            f"Best ask qty {depth['ask_qty']} is below required top-depth qty {depth['top_depth_required_qty']}."
        )

    if block_reasons:
        trade["market_depth_block_reason"] = " ".join(block_reasons)

    return trade


def depth_based_buy_limit_price(option_trade: dict) -> float | None:
    best_ask = float(option_trade.get("best_ask") or 0)
    if not config.USE_DEPTH_FOR_LIMIT_PRICE or not best_ask:
        return None

    buffer_percent = config.ENTRY_LIMIT_BUFFER_PERCENT / 100
    return round_to_tick(best_ask * (1 + buffer_percent))


def _level_price(levels: list[dict]) -> float:
    if not levels:
        return 0.0
    return float(levels[0].get("price") or 0)


def _level_quantity(levels: list[dict]) -> int:
    if not levels:
        return 0
    return int(float(levels[0].get("quantity") or 0))

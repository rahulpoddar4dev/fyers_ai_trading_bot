# order_manager.py - Place F&O orders on Zerodha

import config

def round_to_tick(price: float, tick_size: float | None = None) -> float:
    tick_size = tick_size or config.ORDER_PRICE_TICK_SIZE
    if not tick_size:
        return round(float(price), 2)
    return round(round(float(price) / tick_size) * tick_size, 2)


def place_order(kite, symbol, action, quantity, exchange=None, order_type: str = "MARKET", price: float | None = None):
    """
    Place a simple F&O order.
    action = "BUY" or "SELL"
    order_type = "MARKET" or "LIMIT"
    """

    if config.PAPER_TRADE or config.TRADING_MODE == "PAPER":
        print("Order blocked: PAPER_TRADE/PAPER mode is enabled.")
        return None

    try:
        normalized_order_type = (order_type or "MARKET").upper()
        order_kwargs = {
            "tradingsymbol": symbol,
            "exchange": exchange or kite.EXCHANGE_NFO,
            "transaction_type": action,
            "quantity": quantity,
            "order_type": kite.ORDER_TYPE_LIMIT if normalized_order_type == "LIMIT" else kite.ORDER_TYPE_MARKET,
            "product": kite.PRODUCT_MIS,
            "variety": kite.VARIETY_REGULAR,
        }

        if normalized_order_type == "LIMIT":
            if price is None:
                raise ValueError("LIMIT order needs a price.")
            order_kwargs["price"] = round_to_tick(price)

        order_id = kite.place_order(**order_kwargs)

        print(f"{normalized_order_type} order placed! Order ID: {order_id}")
        return order_id

    except Exception as e:
        print(f"Order failed: {e}")
        return None


def get_order_by_id(kite, order_id):
    if not order_id:
        return None

    try:
        for order in kite.orders():
            if str(order.get("order_id")) == str(order_id):
                return order
    except Exception as error:
        print(f"Could not fetch order status: {error}")

    return None


def get_positions(kite):
    """Check current open positions"""
    positions = kite.positions()
    return positions['net']


def get_balance(kite):
    """Check available margin/balance"""
    margins = kite.margins()
    available = margins['equity']['available']['live_balance']
    print(f"Available Balance: Rs {available}")
    return available

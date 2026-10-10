# market_data.py - Fetch index quotes and historical candle data

from datetime import datetime, timedelta

import config
from index_config import get_index_config
from market_time import market_now_naive


_TOKEN_CACHE = {}


def fetch_index_market_data(fyers, symbol: str) -> dict:
    index_config = get_index_config(symbol)
    quote_key = index_config["quote_key"]
    quote = fyers.quote([quote_key])
    quote_data = quote[quote_key]
    previous_close = quote_data["ohlc"]["close"]
    last_price = quote_data["last_price"]

    return {
        "symbol": index_config["symbol"],
        "quote_key": quote_key,
        "last_price": last_price,
        "high": quote_data["ohlc"]["high"],
        "low": quote_data["ohlc"]["low"],
        "open": quote_data["ohlc"].get("open"),
        "previous_close": previous_close,
        "volume": quote_data.get("volume", 0),
        "change_percent": round(((last_price - previous_close) / previous_close) * 100, 2),
    }


def _find_index_token(fyers, symbol: str) -> int:
    index_config = get_index_config(symbol)
    if index_config.get("instrument_token"):
        return int(index_config["instrument_token"])

    cache_key = index_config["symbol"]
    if cache_key in _TOKEN_CACHE:
        return _TOKEN_CACHE[cache_key]

    instruments = fyers.instruments(index_config["spot_exchange"])
    for instrument in instruments:
        if instrument.get("tradingsymbol") == index_config["spot_tradingsymbol"]:
            token = int(instrument["instrument_token"])
            _TOKEN_CACHE[cache_key] = token
            return token

    raise RuntimeError(f"Could not find instrument token for {symbol}.")


def fetch_index_candles(fyers, symbol: str, interval: str | None = None, lookback_days: int | None = None) -> list[dict]:
    to_date = market_now_naive()
    from_date = to_date - timedelta(days=lookback_days or config.CANDLE_LOOKBACK_DAYS)

    return fyers.historical_data(
        instrument_token=_find_index_token(fyers, symbol),
        from_date=from_date,
        to_date=to_date,
        interval=interval or config.CANDLE_INTERVAL,
    )


def fetch_nifty_market_data(fyers) -> dict:
    return fetch_index_market_data(fyers, "NIFTY")


def fetch_nifty_candles(fyers) -> list[dict]:
    return fetch_index_candles(fyers, "NIFTY")

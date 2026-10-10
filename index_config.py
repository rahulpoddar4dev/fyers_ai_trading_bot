# index_config.py - Index settings for analysis and option selection

import config


INDEX_CONFIGS = {
    "NIFTY": {
        "symbol": "NIFTY",
        "name": "NIFTY",
        "quote_key": "NSE:NIFTY 50",
        "spot_exchange": "NSE",
        "spot_tradingsymbol": "NIFTY 50",
        "instrument_token": config.NIFTY_INSTRUMENT_TOKEN,
        "option_exchange": "NFO",
        "option_name": "NIFTY",
        "strike_step": 50,
    },
    "SENSEX": {
        "symbol": "SENSEX",
        "name": "SENSEX",
        "quote_key": "BSE:SENSEX",
        "spot_exchange": "BSE",
        "spot_tradingsymbol": "SENSEX",
        "instrument_token": None,
        "option_exchange": "BFO",
        "option_name": "SENSEX",
        "strike_step": 100,
    },
    "INDIA_VIX": {
        "symbol": "INDIA_VIX",
        "name": "India VIX",
        "quote_key": "NSE:INDIA VIX",
        "spot_exchange": "NSE",
        "spot_tradingsymbol": "INDIA VIX",
        "instrument_token": None,
        "option_exchange": None,
        "option_name": None,
        "strike_step": None,
    },
}


def get_index_config(symbol: str) -> dict:
    key = symbol.upper()
    if key not in INDEX_CONFIGS:
        raise ValueError(f"Unsupported index: {symbol}")
    return INDEX_CONFIGS[key]

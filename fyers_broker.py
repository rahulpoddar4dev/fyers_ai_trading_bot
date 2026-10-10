# fyers_broker.py - Fyers API v3 broker client for market data, options, and order execution

from __future__ import annotations

import csv
import hashlib
import io
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode
import requests

import config

IST = timezone(timedelta(hours=5, minutes=30))

# Standard instrument tokens used internally across the bot
NIFTY_TOKEN = 256265
SENSEX_TOKEN = 265
INDIA_VIX_TOKEN = 264969

# Map standard index keys to Fyers v3 symbols
INDEX_TO_FYERS_SYMBOL_MAP = {
    "NSE:NIFTY 50": "NSE:NIFTY50-INDEX",
    "BSE:SENSEX": "BSE:SENSEX-INDEX",
    "NSE:INDIA VIX": "NSE:INDIAVIX-INDEX",
    "NSE:NIFTY BANK": "NSE:NIFTYBANK-INDEX",
    "NSE:NIFTY FIN SERVICE": "NSE:FINNIFTY-INDEX",
}

TOKEN_TO_FYERS_SYMBOL = {
    NIFTY_TOKEN: "NSE:NIFTY50-INDEX",
    SENSEX_TOKEN: "BSE:SENSEX-INDEX",
    INDIA_VIX_TOKEN: "NSE:INDIAVIX-INDEX",
}

INTERVAL_TO_FYERS_RESOLUTION = {
    "minute": "1",
    "1minute": "1",
    "3minute": "3",
    "5minute": "5",
    "10minute": "10",
    "15minute": "15",
    "30minute": "30",
    "60minute": "60",
    "day": "D",
}

FYERS_SYMBOL_MASTER_URLS = {
    "NFO": "https://public.fyers.in/sym_details/NSE_FO.csv",
    "BFO": "https://public.fyers.in/sym_details/BSE_FO.csv",
}


class FyersTokenException(Exception):
    """Raised when the Fyers access token or auth_code is invalid or expired."""


class FyersBrokerAdapter:
    """
    Fyers API v3 client that routes all market data, option chain,
    historical candles, profile, margins, and order execution through Fyers.
    """

    EXCHANGE_NFO = "NFO"
    EXCHANGE_BFO = "BFO"
    EXCHANGE_NSE = "NSE"
    EXCHANGE_BSE = "BSE"
    ORDER_TYPE_LIMIT = "LIMIT"
    ORDER_TYPE_MARKET = "MARKET"
    PRODUCT_MIS = "MIS"
    VARIETY_REGULAR = "regular"

    API_BASE = "https://api-t1.fyers.in/api/v3"
    DATA_BASE = "https://api-t1.fyers.in/data"

    def __init__(
        self,
        client_id: str | None = None,
        secret_key: str | None = None,
        redirect_uri: str | None = None,
        timeout: int = 15,
    ):
        self.client_id = (client_id or getattr(config, "FYERS_CLIENT_ID", "") or "").strip()
        self.secret_key = (secret_key or getattr(config, "FYERS_SECRET_KEY", "") or "").strip()
        self.redirect_uri = (
            redirect_uri
            or getattr(config, "FYERS_REDIRECT_URI", "https://trade.fyers.in/api-login/redirect-uri/index.html")
            or "https://trade.fyers.in/api-login/redirect-uri/index.html"
        ).strip()
        self.timeout = timeout
        self.access_token: str | None = None
        self._instrument_cache: dict[str, tuple[date, list[dict]]] = {}
        self._fyers_symbol_by_tradingsymbol: dict[str, str] = {}

    def _headers(self) -> dict[str, str]:
        if not self.client_id or not self.access_token:
            raise FyersTokenException("Fyers access_token is not set. Please login first.")
        auth_val = (
            self.access_token
            if ":" in self.access_token
            else f"{self.client_id}:{self.access_token}"
        )
        return {
            "Authorization": auth_val,
            "Content-Type": "application/json",
        }

    def set_access_token(self, access_token: str) -> None:
        token = (access_token or "").strip()
        if ":" in token and token.startswith(f"{self.client_id}:"):
            token = token.split(":", 1)[1]
        self.access_token = token

    def login_url(self) -> str:
        redirect_uri = self.redirect_uri or "https://trade.fyers.in/api-login/redirect-uri/index.html"
        params = {
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "state": "trading_ai_bot",
        }
        return f"{self.API_BASE}/generate-authcode?{urlencode(params)}"

    def generate_session(self, auth_code_value: str, api_secret: str | None = None) -> dict:
        secret = (api_secret or self.secret_key or "").strip()
        auth_code = (auth_code_value or "").strip()
        if not self.client_id or not secret:
            raise FyersTokenException("FYERS_CLIENT_ID or FYERS_SECRET_KEY is missing in .env / config.py.")
        if not auth_code:
            raise FyersTokenException("Empty Fyers auth_code provided.")

        app_id_hash = hashlib.sha256(f"{self.client_id}:{secret}".encode("utf-8")).hexdigest()
        payload = {
            "grant_type": "authorization_code",
            "appIdHash": app_id_hash,
            "code": auth_code,
        }
        resp = requests.post(
            f"{self.API_BASE}/validate-authcode",
            json=payload,
            timeout=self.timeout,
        )
        try:
            data = resp.json()
        except Exception as exc:
            raise FyersTokenException(f"Invalid response from Fyers validate-authcode: {resp.text}") from exc

        if data.get("s") != "ok" or not data.get("access_token"):
            msg = data.get("message") or data.get("errmsg") or str(data)
            raise FyersTokenException(f"Fyers session generation failed: {msg}")

        self.set_access_token(data["access_token"])
        return {"access_token": self.access_token, "raw": data}

    def profile(self) -> dict:
        resp = requests.get(
            f"{self.API_BASE}/profile",
            headers=self._headers(),
            timeout=self.timeout,
        )
        try:
            body = resp.json()
        except Exception as exc:
            raise FyersTokenException(f"Invalid profile response from Fyers: {resp.text}") from exc

        if resp.status_code in (401, 403) or body.get("s") == "error" or body.get("code") in (-8, -15, -16, -17, 401):
            raise FyersTokenException(body.get("message") or "Fyers token expired or invalid.")

        data = body.get("data") or {}
        return {
            "user_name": data.get("name") or data.get("display_name") or data.get("fy_id") or "Fyers User",
            "user_id": data.get("fy_id") or "",
            "email": data.get("email_id") or "",
            "broker": "FYERS",
        }

    def margins(self) -> dict:
        resp = requests.get(
            f"{self.API_BASE}/funds",
            headers=self._headers(),
            timeout=self.timeout,
        )
        body = resp.json() if resp.content else {}
        fund_limits = body.get("fund_limit") or []
        available = 0.0
        for item in fund_limits:
            if item.get("id") == 10 or str(item.get("title", "")).lower() == "available balance":
                available = float(item.get("equityAmount") or 0.0)
                break
        else:
            for item in fund_limits:
                if item.get("id") == 1:
                    available = float(item.get("equityAmount") or 0.0)
                    break

        return {
            "equity": {
                "available": {
                    "live_balance": round(available, 2),
                }
            }
        }

    def _to_fyers_symbol(self, symbol_key: str) -> str:
        if symbol_key in INDEX_TO_FYERS_SYMBOL_MAP:
            return INDEX_TO_FYERS_SYMBOL_MAP[symbol_key]

        if ":" not in symbol_key:
            return self._fyers_symbol_by_tradingsymbol.get(symbol_key, f"NSE:{symbol_key}")

        exchange, tradingsymbol = symbol_key.split(":", 1)
        exchange = exchange.upper()

        if tradingsymbol in self._fyers_symbol_by_tradingsymbol:
            return self._fyers_symbol_by_tradingsymbol[tradingsymbol]

        if exchange == "NFO":
            return f"NSE:{tradingsymbol}"
        if exchange == "BFO":
            return f"BSE:{tradingsymbol}"
        if exchange in {"NSE", "BSE"}:
            if not tradingsymbol.endswith(("-EQ", "-INDEX")):
                return f"{exchange}:{tradingsymbol}-EQ"
            return f"{exchange}:{tradingsymbol}"

        return symbol_key

    def quote(self, keys: list[str] | tuple[str, ...]) -> dict:
        if isinstance(keys, str):
            keys = [keys]
        keys = list(keys)
        if not keys:
            return {}

        fyers_to_orig: dict[str, list[str]] = {}
        option_keys: list[tuple[str, str]] = []

        for k in keys:
            f_sym = self._to_fyers_symbol(k)
            fyers_to_orig.setdefault(f_sym, []).append(k)
            if k.startswith(("NFO:", "BFO:")):
                option_keys.append((k, f_sym))

        result: dict[str, dict] = {}
        all_fyers_syms = list(fyers_to_orig.keys())

        for idx in range(0, len(all_fyers_syms), 50):
            batch = all_fyers_syms[idx : idx + 50]
            resp = requests.get(
                f"{self.DATA_BASE}/quotes",
                params={"symbols": ",".join(batch)},
                headers=self._headers(),
                timeout=self.timeout,
            )
            body = resp.json() if resp.content else {}
            for item in body.get("d") or []:
                f_sym = item.get("n")
                v = item.get("v") or {}
                if not f_sym or v.get("s") == "error":
                    continue
                ltp = float(v.get("lp") or 0.0)
                bid = float(v.get("bid") or 0.0)
                ask = float(v.get("ask") or 0.0)
                mapped_quote = {
                    "last_price": ltp,
                    "volume": int(float(v.get("volume") or 0)),
                    "oi": int(float(v.get("oi") or 0)),
                    "ohlc": {
                        "open": float(v.get("open_price") or ltp),
                        "high": float(v.get("high_price") or ltp),
                        "low": float(v.get("low_price") or ltp),
                        "close": float(v.get("prev_close_price") or ltp),
                    },
                    "depth": {
                        "buy": [{"price": bid, "quantity": 1000}] if bid > 0 else [],
                        "sell": [{"price": ask, "quantity": 1000}] if ask > 0 else [],
                    },
                }
                for orig_k in fyers_to_orig.get(f_sym, []):
                    result[orig_k] = dict(mapped_quote)

        for orig_k, f_sym in option_keys:
            try:
                d_resp = requests.get(
                    f"{self.DATA_BASE}/depth",
                    params={"symbol": f_sym, "ohlcv_flag": "1"},
                    headers=self._headers(),
                    timeout=self.timeout,
                )
                d_body = d_resp.json() if d_resp.content else {}
                d_data = (d_body.get("d") or {}).get(f_sym)
                if not d_data:
                    continue
                ltp = float(d_data.get("ltp") or (result.get(orig_k) or {}).get("last_price") or 0.0)
                bids = [
                    {"price": float(b.get("price") or 0.0), "quantity": int(float(b.get("volume") or 0))}
                    for b in (d_data.get("bids") or [])
                    if float(b.get("price") or 0.0) > 0
                ]
                asks = [
                    {"price": float(a.get("price") or 0.0), "quantity": int(float(a.get("volume") or 0))}
                    for a in (d_data.get("ask") or [])
                    if float(a.get("price") or 0.0) > 0
                ]
                result[orig_k] = {
                    "last_price": ltp,
                    "volume": int(float(d_data.get("v") or 0)),
                    "oi": int(float(d_data.get("oi") or 0)),
                    "ohlc": {
                        "open": float(d_data.get("o") or ltp),
                        "high": float(d_data.get("h") or ltp),
                        "low": float(d_data.get("l") or ltp),
                        "close": float(d_data.get("c") or ltp),
                    },
                    "depth": {
                        "buy": bids,
                        "sell": asks,
                    },
                }
            except Exception:
                pass

        return result

    def instruments(self, exchange: str) -> list[dict]:
        exch = (exchange or "NFO").upper()
        if exch in {"NSE", "BSE"}:
            return [
                {
                    "instrument_token": NIFTY_TOKEN,
                    "tradingsymbol": "NIFTY 50",
                    "name": "NIFTY 50",
                    "exchange": "NSE",
                },
                {
                    "instrument_token": SENSEX_TOKEN,
                    "tradingsymbol": "SENSEX",
                    "name": "SENSEX",
                    "exchange": "BSE",
                },
                {
                    "instrument_token": INDIA_VIX_TOKEN,
                    "tradingsymbol": "INDIA VIX",
                    "name": "INDIA VIX",
                    "exchange": "NSE",
                },
            ]

        today = datetime.now(IST).date()
        cached = self._instrument_cache.get(exch)
        if cached and cached[0] == today:
            return cached[1]

        url = FYERS_SYMBOL_MASTER_URLS.get(exch)
        if not url:
            return []

        resp = requests.get(url, timeout=max(self.timeout, 25))
        resp.raise_for_status()

        rows: list[dict] = []
        reader = csv.reader(io.StringIO(resp.text))
        for parts in reader:
            if len(parts) < 16:
                continue
            opt_type = parts[15].strip().upper()
            if opt_type not in {"CE", "PE"}:
                continue
            underlying = parts[13].strip().upper()
            if underlying not in {"NIFTY", "SENSEX", "BANKNIFTY", "FINNIFTY"}:
                continue

            try:
                fy_token = int(parts[0].strip())
                lot_size = int(float(parts[3].strip() or 1))
                expiry_epoch = int(float(parts[8].strip() or 0))
                expiry_dt = datetime.fromtimestamp(expiry_epoch, tz=IST).date()
                full_fyers_sym = parts[9].strip()
                strike = float(parts[14].strip() or 0.0)
            except Exception:
                continue

            tradingsymbol = full_fyers_sym.split(":", 1)[-1]
            TOKEN_TO_FYERS_SYMBOL[fy_token] = full_fyers_sym
            self._fyers_symbol_by_tradingsymbol[tradingsymbol] = full_fyers_sym

            rows.append(
                {
                    "instrument_token": fy_token,
                    "tradingsymbol": tradingsymbol,
                    "name": underlying,
                    "expiry": expiry_dt,
                    "strike": strike,
                    "lot_size": lot_size,
                    "instrument_type": opt_type,
                    "exchange": exch,
                }
            )

        self._instrument_cache[exch] = (today, rows)
        return rows

    def historical_data(
        self,
        instrument_token: int,
        from_date: datetime | date | str,
        to_date: datetime | date | str,
        interval: str = "5minute",
        **kwargs,
    ) -> list[dict]:
        token = int(instrument_token)
        fyers_sym = TOKEN_TO_FYERS_SYMBOL.get(token)
        if not fyers_sym:
            self.instruments("NFO")
            fyers_sym = TOKEN_TO_FYERS_SYMBOL.get(token)
        if not fyers_sym:
            self.instruments("BFO")
            fyers_sym = TOKEN_TO_FYERS_SYMBOL.get(token)
        if not fyers_sym:
            raise ValueError(f"Unknown instrument_token {instrument_token} for Fyers historical_data.")

        resolution = INTERVAL_TO_FYERS_RESOLUTION.get(interval, "5")

        def _fmt_date(val) -> str:
            if isinstance(val, datetime):
                return val.strftime("%Y-%m-%d")
            if isinstance(val, date):
                return val.strftime("%Y-%m-%d")
            return str(val)[:10]

        params = {
            "symbol": fyers_sym,
            "resolution": resolution,
            "date_format": "1",
            "range_from": _fmt_date(from_date),
            "range_to": _fmt_date(to_date),
            "cont_flag": "1",
        }
        resp = requests.get(
            f"{self.DATA_BASE}/history",
            params=params,
            headers=self._headers(),
            timeout=self.timeout,
        )
        body = resp.json() if resp.content else {}
        candles_raw = body.get("candles") or []

        candles: list[dict] = []
        for c in candles_raw:
            if len(c) < 6:
                continue
            dt = datetime.fromtimestamp(int(c[0]), tz=IST).replace(tzinfo=None)
            candles.append(
                {
                    "date": dt,
                    "open": float(c[1]),
                    "high": float(c[2]),
                    "low": float(c[3]),
                    "close": float(c[4]),
                    "volume": int(float(c[5])),
                }
            )
        return candles

    def place_order(
        self,
        tradingsymbol: str,
        exchange: str = "NFO",
        transaction_type: str = "BUY",
        quantity: int = 1,
        order_type: str = "MARKET",
        product: str = "MIS",
        variety: str = "regular",
        price: float | None = None,
        **kwargs,
    ) -> str:
        fyers_sym = self._to_fyers_symbol(f"{exchange}:{tradingsymbol}")
        is_limit = str(order_type).upper() == "LIMIT"
        is_buy = str(transaction_type).upper() == "BUY"

        payload = {
            "symbol": fyers_sym,
            "qty": int(quantity),
            "type": 1 if is_limit else 2,
            "side": 1 if is_buy else -1,
            "productType": "INTRADAY" if str(product).upper() == "MIS" else "MARGIN",
            "limitPrice": float(price or 0.0) if is_limit else 0.0,
            "stopPrice": 0.0,
            "validity": "DAY",
            "disclosedQty": 0,
            "offlineOrder": False,
        }
        resp = requests.post(
            f"{self.API_BASE}/orders/sync",
            json=payload,
            headers=self._headers(),
            timeout=self.timeout,
        )
        body = resp.json() if resp.content else {}
        if body.get("s") != "ok" or not body.get("id"):
            raise RuntimeError(body.get("message") or f"Fyers order failed: {body}")
        return str(body["id"])

    def orders(self) -> list[dict]:
        resp = requests.get(
            f"{self.API_BASE}/orders",
            headers=self._headers(),
            timeout=self.timeout,
        )
        body = resp.json() if resp.content else {}
        order_list = body.get("orderBook") or []

        status_map = {
            1: "CANCELLED",
            2: "COMPLETE",
            4: "OPEN",
            5: "REJECTED",
            6: "OPEN",
        }
        mapped: list[dict] = []
        for item in order_list:
            fyers_status = int(item.get("status") or 0)
            mapped.append(
                {
                    "order_id": str(item.get("id") or ""),
                    "status": status_map.get(fyers_status, "OPEN"),
                    "status_message": item.get("message") or "",
                    "filled_quantity": int(float(item.get("filledQty") or 0)),
                    "pending_quantity": int(float(item.get("remainingQuantity") or 0)),
                    "average_price": float(item.get("tradedPrice") or item.get("limitPrice") or 0.0),
                    "tradingsymbol": str(item.get("symbol") or "").split(":", 1)[-1],
                }
            )
        return mapped

    def positions(self) -> dict:
        resp = requests.get(
            f"{self.API_BASE}/positions",
            headers=self._headers(),
            timeout=self.timeout,
        )
        body = resp.json() if resp.content else {}
        return {"net": body.get("netPositions") or []}

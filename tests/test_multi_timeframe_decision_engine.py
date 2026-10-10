# tests/test_multi_timeframe_decision_engine.py - Unit tests for 15m HTF, Structural SL, and Runner Trailing Engine

import pytest
import config
from datetime import datetime
from strategy import build_strategy_signal, _analyze_htf_trend
from option_selector import calculate_option_stop_loss_percent, select_index_option
from position_sizer import apply_position_sizing
from trade_state import update_target_stage_stop, monitor_open_trade


def _make_dummy_candle(date_str, close, high=None, low=None, open_val=None, volume=1000):
    high = high if high is not None else close + 5
    low = low if low is not None else close - 5
    open_val = open_val if open_val is not None else close
    return {
        "date": date_str,
        "open": open_val,
        "high": high,
        "low": low,
        "close": close,
        "volume": volume,
    }


def _make_trending_candles(count=35, start_price=22500, direction="DOWN"):
    candles = []
    price = start_price
    for i in range(count):
        step = -15 if direction == "DOWN" else 15
        price += step
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, price, high=price + 8, low=price - 8, open_val=price - step))
    return candles


def test_htf_trend_analysis_detects_bearish_and_bullish():
    # 1. Bearish 15m candles (falling price)
    bearish_htf = _make_trending_candles(count=20, start_price=22500, direction="DOWN")
    bearish_analysis = _analyze_htf_trend(bearish_htf)
    assert bearish_analysis["bias"] == "BEARISH"
    assert bearish_analysis["swing_high"] is not None

    # 2. Bullish 15m candles (rising price)
    bullish_htf = _make_trending_candles(count=20, start_price=22000, direction="UP")
    bullish_analysis = _analyze_htf_trend(bullish_htf)
    assert bullish_analysis["bias"] == "BULLISH"
    assert bullish_analysis["swing_low"] is not None


def _make_bullish_setup_5m(count=35):
    candles = []
    for i in range(25):
        dt = f"2026-10-08T09:{i:02d}:00"
        p = 22000 + (2 if i % 2 == 0 else -2)
        candles.append(_make_dummy_candle(dt, p, p + 15, p - 15, p))
    p = 22000
    for i in range(25, count):
        p += 5
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, p, p + 16, p - 12, p - 5))
    return candles


def _make_bearish_setup_5m(count=35):
    candles = []
    for i in range(25):
        dt = f"2026-10-08T09:{i:02d}:00"
        p = 22500 + (2 if i % 2 == 0 else -2)
        candles.append(_make_dummy_candle(dt, p, p + 15, p - 15, p))
    p = 22500
    for i in range(25, count):
        p -= 5
        dt = f"2026-10-08T09:{i:02d}:00"
        candles.append(_make_dummy_candle(dt, p, p + 12, p - 16, p + 5))
    return candles


def test_counter_trend_ce_blocked_when_htf_is_bearish():
    # Setup: 5-minute candles form a valid bullish setup
    # But 15m HTF candles are deeply bearish
    bearish_htf = _make_trending_candles(count=25, start_price=22500, direction="DOWN")
    candles_5m = _make_bullish_setup_5m(count=35)
    market_data = {"symbol": "NIFTY", "last_price": 22050, "change_percent": -1.2}

    signal, _ = build_strategy_signal(
        market_data,
        candles_5m,
        htf_candles=bearish_htf,
    )
    # Even though 5m setup is bullish, HTF is strictly BEARISH -> BUY CE MUST BE BLOCKED!
    assert signal["SIGNAL"] == "HOLD"
    assert "Counter-trend BUY blocked" in signal["REASON"]
    assert "15-Minute HTF is strictly BEARISH" in signal["REASON"]


def test_counter_trend_pe_blocked_when_htf_is_bullish():
    # Setup: 15m HTF candles are deeply bullish
    bullish_htf = _make_trending_candles(count=25, start_price=22000, direction="UP")
    candles_5m = _make_bearish_setup_5m(count=35)
    market_data = {"symbol": "NIFTY", "last_price": 22450, "change_percent": 1.5}

    signal, _ = build_strategy_signal(
        market_data,
        candles_5m,
        htf_candles=bullish_htf,
    )
    # Even though 5m setup is bearish, HTF is strictly BULLISH -> SELL PE MUST BE BLOCKED!
    assert signal["SIGNAL"] == "HOLD"
    assert "Counter-trend SELL blocked" in signal["REASON"]
    assert "15-Minute HTF is strictly BULLISH" in signal["REASON"]


def test_calculate_option_stop_loss_percent_tiers():
    # Cheap option (< 100): minimum 18% floor to avoid noise wicks
    sl_cheap, reason_cheap = calculate_option_stop_loss_percent(entry_price=75.0, spot_price=22400)
    assert sl_cheap >= 18.0

    # Medium option (100 - 220): minimum 15% floor
    sl_med, reason_med = calculate_option_stop_loss_percent(entry_price=130.0, spot_price=22400)
    assert sl_med >= 15.0

    # Expensive option (> 220): 12.0% floor
    sl_exp, reason_exp = calculate_option_stop_loss_percent(entry_price=300.0, spot_price=22400)
    assert sl_exp == 12.0

    # Structural SL with index stop loss:
    strategy_signal = {"INDEX_STOP_LOSS": 22445} # 45 pts away on Nifty
    # 45 pts * 0.52 delta = 23.4 option pts / 130 entry = 18.0%
    sl_struct, reason_struct = calculate_option_stop_loss_percent(
        entry_price=130.0,
        spot_price=22400,
        strategy_signal=strategy_signal,
    )
    assert sl_struct == 18.0
    assert "Multi-timeframe structural SL" in reason_struct


def test_runner_peak_trailing_protects_profit_without_choke():
    # Entry at 100, Target 1 at 114 (+14%)
    trade = {
        "entry_price": 100.0,
        "stop_loss": 85.0,
        "target_1": 114.0,
        "target_2": 125.0,
        "target_stage": 0,
        "highest_price_seen": 100.0,
    }
    # Current price moves to 115 (T1 hit)
    updated, reason = update_target_stage_stop(trade, 115.0)
    assert updated["target_stage"] == 1
    # Peak trailing activates: 115 * (1 - 0.12) = 101.20, minimum floor is 101.50
    assert updated["stop_loss"] >= 101.50 # Zero loss lock

    # Option runs to 150 (+50%)
    updated_run, _ = update_target_stage_stop(updated, 150.0)
    # Peak trailing at 12%: 150 * 0.88 = 132.0 (locks +32% profit!)
    assert updated_run["stop_loss"] == 132.0
    assert updated_run["highest_price_seen"] == 150.0

    # Option runs to 250 (+150%)
    updated_mega, _ = update_target_stage_stop(updated_run, 250.0)
    # Peak trailing at 12%: 250 * 0.88 = 220.0 (locks +120% profit!)
    assert updated_mega["stop_loss"] == 220.0


def test_position_sizing_adapts_to_wider_sl_without_increasing_rupee_risk():
    balance = 100000.0
    # Case A: Narrow 10% SL on Rs 120 option (SL = 108, risk per lot = 12 * 25 = Rs 300)
    trade_narrow = {
        "entry_price": 120.0,
        "stop_loss": 108.0,
        "lot_size": 25,
        "market_regime": "NORMAL",
    }
    sized_narrow = apply_position_sizing(trade_narrow, balance)

    # Case B: Structural 16% SL on Rs 120 option (SL = 100.80, risk per lot = 19.20 * 25 = Rs 480)
    trade_wide = {
        "entry_price": 120.0,
        "stop_loss": 100.80,
        "lot_size": 25,
        "market_regime": "NORMAL",
    }
    sized_wide = apply_position_sizing(trade_wide, balance)

    # Lots automatically decrease so rupee risk NEVER exceeds risk hard limit
    assert sized_wide["lots"] <= sized_narrow["lots"]
    assert sized_wide["estimated_risk_amount"] <= sized_wide["risk_hard_limit"]

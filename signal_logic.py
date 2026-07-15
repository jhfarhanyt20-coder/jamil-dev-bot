"""
signal_logic.py
================
1-Minute Binary Trading Signal Logic (Multi-Indicator Confirmation - Enhanced with 5M HTF SNR Reversal)
"""

import pandas as pd
import numpy as np
from datetime import datetime


def get_next_candle_window(period_seconds: int, now: float = None):
    import time as _time
    if now is None:
        now = _time.time()
    next_boundary = (int(now // period_seconds) + 1) * period_seconds
    entry_dt = datetime.fromtimestamp(next_boundary)
    exit_dt = datetime.fromtimestamp(next_boundary + period_seconds)
    seconds_until_entry = round(next_boundary - now, 1)
    return entry_dt, exit_dt, seconds_until_entry


# ============================================================
# Safe value helper
# ============================================================

def get_value_safe(series, index=-1, default=0.0):
    try:
        val = series.iloc[index]
        if pd.isna(val):
            return default
        return float(val)
    except Exception:
        return default


# ============================================================
# Indicator calculations
# ============================================================

def calculate_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()

    rename_map = {}
    for col in df.columns:
        lc = str(col).lower()
        if lc in ("open", "o"):
            rename_map[col] = "Open"
        elif lc in ("close", "c", "price"):
            rename_map[col] = "Close"
        elif lc in ("high", "h", "max"):
            rename_map[col] = "High"
        elif lc in ("low", "l", "min"):
            rename_map[col] = "Low"
        elif lc in ("volume", "v"):
            rename_map[col] = "Volume"
    df = df.rename(columns=rename_map)

    if "High" not in df.columns:
        df["High"] = df[["Open", "Close"]].max(axis=1)
    if "Low" not in df.columns:
        df["Low"] = df[["Open", "Close"]].min(axis=1)
    if "Volume" not in df.columns:
        df["Volume"] = 0

    close = df["Close"]
    high = df["High"]
    low = df["Low"]

    # ---------------- EMA ----------------
    df["EMA5"] = close.ewm(span=5, adjust=False).mean()
    df["EMA13"] = close.ewm(span=13, adjust=False).mean()
    df["EMA50"] = close.ewm(span=50, adjust=False).mean()

    # ---------------- RSI ----------------
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 7, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / 7, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    df["RSI"] = 100 - (100 / (1 + rs))
    df["RSI"] = df["RSI"].fillna(50)

    # ---------------- MACD ---------------------------
    ema12 = close.ewm(span=12, adjust=False).mean()
    ema26 = close.ewm(span=26, adjust=False).mean()
    df["MACD"] = ema12 - ema26
    df["MACD_Signal"] = df["MACD"].ewm(span=9, adjust=False).mean()
    df["MACD_Hist"] = df["MACD"] - df["MACD_Signal"]

    # ---------------- Bollinger Bands ---------------------
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    df["BB_Mid"] = sma20
    df["BB_Upper"] = sma20 + 2 * std20
    df["BB_Lower"] = sma20 - 2 * std20

    # ---------------- Stochastic -------------------------
    low5 = low.rolling(5).min()
    high5 = high.rolling(5).max()
    k = 100 * (close - low5) / (high5 - low5).replace(0, np.nan)
    df["Stoch_K"] = k.fillna(50)
    df["Stoch_D"] = df["Stoch_K"].rolling(3).mean().fillna(50)

    # ---------------- CCI ----------------------------
    tp = (high + low + close) / 3
    sma_tp = tp.rolling(14).mean()
    mad = tp.rolling(14).apply(lambda x: np.abs(x - x.mean()).mean(), raw=True)
    df["CCI"] = (tp - sma_tp) / (0.015 * mad)
    df["CCI"] = df["CCI"].fillna(0)

    # ---------------- ATR -------------------
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    df["ATR"] = tr.rolling(14).mean()

    # ---------------- ADX -----------------------
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    atr14_wilder = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    plus_di = 100 * pd.Series(plus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14_wilder
    minus_di = 100 * pd.Series(minus_dm, index=df.index).ewm(alpha=1 / 14, adjust=False).mean() / atr14_wilder
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    df["ADX"] = dx.ewm(alpha=1 / 14, adjust=False).mean().fillna(0)

    # ---------------- Swing high/low ------------------------
    df["Swing_High"] = high.rolling(20).max()
    df["Swing_Low"] = low.rolling(20).min()

    # ---------------- Classic Pivot Points --------------
    piv_window = 20
    win_high = high.rolling(piv_window).max()
    win_low = low.rolling(piv_window).min()
    win_close = close.shift(1)
    df["Pivot_P"] = (win_high + win_low + win_close) / 3
    df["Pivot_R1"] = (2 * df["Pivot_P"]) - win_low
    df["Pivot_S1"] = (2 * df["Pivot_P"]) - win_high

    return df


def calculate_htf_trend(df: pd.DataFrame) -> str:
    if df is None or len(df) < 20:
        return "neutral"
    df = calculate_indicators(df)
    ema13 = get_value_safe(df["EMA13"])
    ema50 = get_value_safe(df["EMA50"])
    close = get_value_safe(df["Close"])
    if close > ema13 > ema50:
        return "bull"
    if close < ema13 < ema50:
        return "bear"
    return "neutral"


def check_pivot_entry_trigger(df: pd.DataFrame, confirm_candles: int = 3):
    if df is None or len(df) < confirm_candles + 1 or "Pivot_P" not in df.columns:
        return None, "", 0
    pivot = get_value_safe(df["Pivot_P"])
    if pivot == 0:
        return None, "", 0
    recent_closes = df["Close"].tail(confirm_candles)
    if (recent_closes < pivot).all():
        return "PUT", f"Price held below pivot ({pivot:.5f}) for last {confirm_candles} candles", 15
    if (recent_closes > pivot).all():
        return "CALL", f"Price held above pivot ({pivot:.5f}) for last {confirm_candles} candles", 15
    return None, f"Price chopping around pivot ({pivot:.5f})", 0


def detect_rsi_divergence(df: pd.DataFrame, lookback: int = 14):
    if df is None or len(df) < lookback + 5 or "RSI" not in df.columns:
        return None, "", 0
    window = df.tail(lookback).reset_index(drop=True)
    highs = window["High"]
    lows = window["Low"]
    rsi = window["RSI"]

    high_idx_sorted = highs.sort_values(ascending=False).index.tolist()
    swing_high_idxs = []
    for idx in high_idx_sorted:
        if all(abs(idx - existing) >= 3 for existing in swing_high_idxs):
            swing_high_idxs.append(idx)
        if len(swing_high_idxs) == 2: break

    low_idx_sorted = lows.sort_values(ascending=True).index.tolist()
    swing_low_idxs = []
    for idx in low_idx_sorted:
        if all(abs(idx - existing) >= 3 for existing in swing_low_idxs):
            swing_low_idxs.append(idx)
        if len(swing_low_idxs) == 2: break

    if len(swing_high_idxs) == 2:
        i1, i2 = sorted(swing_high_idxs)
        if highs[i2] > highs[i1] and rsi[i2] < rsi[i1]:
            return "PUT", "Bearish RSI divergence (price HH, RSI LH)", 15
    if len(swing_low_idxs) == 2:
        i1, i2 = sorted(swing_low_idxs)
        if lows[i2] < lows[i1] and rsi[i2] > rsi[i1]:
            return "CALL", "Bullish RSI divergence (price LL, RSI HL)", 15
    return None, "", 0


def _detect_candle_patterns(df):
    if len(df) < 2:
        return None, "", 0
    o1, c1, h1, l1 = df["Open"].iloc[-2], df["Close"].iloc[-2], df["High"].iloc[-2], df["Low"].iloc[-2]
    o2, c2, h2, l2 = df["Open"].iloc[-1], df["Close"].iloc[-1], df["High"].iloc[-1], df["Low"].iloc[-1]
    body1, body2 = abs(c1 - o1), abs(c2 - o2)
    range2 = h2 - l2 if (h2 - l2) != 0 else 1e-9
    range1 = h1 - l1 if (h1 - l1) != 0 else 1e-9

    if c1 < o1 and c2 > o2 and c2 >= o1 and o2 <= c1:
        return "CALL", "Bullish engulfing (strong reversal)", 10
    if c1 > o1 and c2 < o2 and c2 <= o1 and o2 >= c1:
        return "PUT", "Bearish engulfing (strong reversal)", 10
    lower_wick2, upper_wick2 = min(o2, c2) - l2, h2 - max(o2, c2)
    if lower_wick2 > body2 * 2 and lower_wick2 / range2 > 0.6 and c2 > o2:
        return "CALL", "Bullish hammer/pin bar (strong reversal)", 10
    if upper_wick2 > body2 * 2 and upper_wick2 / range2 > 0.6 and c2 < o2:
        return "PUT", "Bearish shooting star/pin bar (strong reversal)", 10
    return None, "", 0


# ============================================================
# Main signal generator - COMPOUND SCORING (WITH 5M SNR)
# ============================================================

def get_signal_simple(df: pd.DataFrame, htf_trend: str = "neutral", min_confidence: float = 70.0, df_5m: pd.DataFrame = None):
    reasons = []
    call_score = 0.0
    put_score = 0.0
    max_score = 0.0
    confirmations = []

    if df is None or len(df) < 30:
        return None, 0, ["❌ Not enough candle data (need at least 30)"]

    # Get local 1M indicator values
    ema5 = get_value_safe(df["EMA5"])
    ema13 = get_value_safe(df["EMA13"])
    ema50 = get_value_safe(df["EMA50"])
    rsi = get_value_safe(df["RSI"], default=50)
    rsi_prev = get_value_safe(df["RSI"], index=-2, default=50)
    macd_hist = get_value_safe(df["MACD_Hist"])
    macd_hist_prev = get_value_safe(df["MACD_Hist"], index=-2)
    close = get_value_safe(df["Close"])
    high = get_value_safe(df["High"])
    low = get_value_safe(df["Low"])
    bb_upper = get_value_safe(df["BB_Upper"])
    bb_lower = get_value_safe(df["BB_Lower"])
    stoch_k = get_value_safe(df["Stoch_K"], default=50)
    stoch_d = get_value_safe(df["Stoch_D"], default=50)
    stoch_k_prev = get_value_safe(df["Stoch_K"], index=-2, default=50)
    stoch_d_prev = get_value_safe(df["Stoch_D"], index=-2, default=50)
    cci = get_value_safe(df["CCI"], default=0)
    cci_prev = get_value_safe(df["CCI"], index=-2, default=0)
    atr = get_value_safe(df["ATR"])
    atr_avg = df["ATR"].tail(30).mean() if "ATR" in df.columns else 0
    adx = get_value_safe(df["ADX"], default=0)
    swing_high = get_value_safe(df["Swing_High"], default=close)
    swing_low = get_value_safe(df["Swing_Low"], default=close)

    # ---- 5-MINUTE HTF SNR CALCULATION & TOUCH DETECTION ----
    touch_5m_support = False
    touch_5m_resistance = False
    htf_resistance = 0.0
    htf_support = 0.0

    if df_5m is not None and len(df_5m) >= 20:
        df_5m_norm = df_5m.copy()
        rename_5m = {c: c.capitalize() for c in df_5m_norm.columns if c.lower() in ["high", "low", "open", "close"]}
        df_5m_norm = df_5m_norm.rename(columns=rename_5m)
        
        # Calculate 5M SNR Levels using a rolling window of recent 20 candles
        htf_resistance = get_value_safe(df_5m_norm["High"].rolling(20).max())
        htf_support = get_value_safe(df_5m_norm["Low"].rolling(20).min())
        
        if htf_support > 0 and htf_resistance > 0:
            # Flexible buffer area based on current 1M volatility (40% ATR)
            buffer_5m = atr * 0.4 if atr > 0 else 0.0
            
            # Detect if current 1M candle touched or entered the 5M SNR zone
            if low <= htf_support + buffer_5m:
                touch_5m_support = True
            if high >= htf_resistance - buffer_5m:
                touch_5m_resistance = True

    # ---- REVERSAL STRATEGY DETECTION ----
    reversal_call = False
    reversal_put = False
    reversal_weight = 0

    # 1. Indicator Overbought/Oversold conditions
    if rsi >= 70 and cci >= 100 and stoch_k >= 80:
        reversal_put = True
        reversal_weight = 15
        reasons.append("🔴 EXTREME OVERBOUGHT: RSI≥70, CCI≥100, Stoch≥80 (reversal down likely)")
    elif rsi <= 30 and cci <= -100 and stoch_k <= 20:
        reversal_call = True
        reversal_weight = 15
        reasons.append("🟢 EXTREME OVERSOLD: RSI≤30, CCI≤-100, Stoch≤20 (reversal up likely)")

    # 2. 5M HTF SNR Direct Touch Trigger (Forces Reversal Mode)
    if touch_5m_resistance:
        reversal_put = True
        reversal_weight = max(reversal_weight, 25)
        reasons.append(f"🔥 HTF SNR TOUCH: Hit 5M Resistance ({htf_resistance:.5f}) -> 1M Reversal PUT Triggered")
    elif touch_5m_support:
        reversal_call = True
        reversal_weight = max(reversal_weight, 25)
        reasons.append(f"🔥 HTF SNR TOUCH: Hit 5M Support ({htf_support:.5f}) -> 1M Reversal CALL Triggered")

    # ---- COMPOUND SCORING (each indicator adds to score) ----

    # 1) TREND: EMA alignment (weight 20)
    w = 20
    max_score += w
    if ema5 > ema13 > ema50:
        call_score += w
        reasons.append("✅ Trend: EMA5 > EMA13 > EMA50 (bullish)")
        confirmations.append("trend_bull")
    elif ema5 < ema13 < ema50:
        put_score += w
        reasons.append("✅ Trend: EMA5 < EMA13 < EMA50 (bearish)")
        confirmations.append("trend_bear")

    # 2) RSI momentum (weight 15)
    w = 15
    max_score += w
    if 50 < rsi < 70 and rsi > rsi_prev:
        call_score += w
        confirmations.append("rsi_bull")
    elif 30 < rsi < 50 and rsi < rsi_prev:
        put_score += w
        confirmations.append("rsi_bear")
    elif rsi >= 70 and reversal_put:
        put_score += w * 0.7
        confirmations.append("rsi_reversal_put")
    elif rsi <= 30 and reversal_call:
        call_score += w * 0.7
        confirmations.append("rsi_reversal_call")

    # 3) MACD histogram (weight 18)
    w = 18
    max_score += w
    if macd_hist > 0 and macd_hist > macd_hist_prev:
        call_score += w
        confirmations.append("macd_bull")
    elif macd_hist < 0 and macd_hist < macd_hist_prev:
        put_score += w
        confirmations.append("macd_bear")

    # 4) Bollinger Bands (weight 15)
    w = 15
    max_score += w
    if close <= bb_lower:
        call_score += w
        reasons.append("✅ Price at lower Bollinger Band (oversold bounce)")
        confirmations.append("bb_bull")
    elif close >= bb_upper:
        put_score += w
        reasons.append("✅ Price at upper Bollinger Band (overbought pullback)")
        confirmations.append("bb_bear")

    # 5) Stochastic crossover (weight 15)
    w = 15
    max_score += w
    if stoch_k > stoch_d and stoch_k_prev <= stoch_d_prev and stoch_k < 80:
        call_score += w
        confirmations.append("stoch_bull")
    elif stoch_k < stoch_d and stoch_k_prev >= stoch_d_prev and stoch_k > 20:
        put_score += w
        confirmations.append("stoch_bear")

    # 6) CCI (weight 12)
    w = 12
    max_score += w
    if cci > 0 and cci > cci_prev:
        call_score += w * 0.5
    elif cci < 0 and cci < cci_prev:
        put_score += w * 0.5

    # 7) Candlestick Patterns (weight 15)
    w = 15
    max_score += w
    pattern_dir, pattern_desc, pattern_weight = _detect_candle_patterns(df)
    if pattern_dir == "CALL":
        call_score += w
        reasons.append(f"✅ Candle: {pattern_desc}")
        confirmations.append("candle_bull")
    elif pattern_dir == "PUT":
        put_score += w
        reasons.append(f"✅ Candle: {pattern_desc}")
        confirmations.append("candle_bear")

    # 8) Reversal Bonus (extra weight for extreme conditions)
    if reversal_call:
        w = 15
        max_score += w
        call_score += w
        confirmations.append("reversal_call")
    elif reversal_put:
        w = 15
        max_score += w
        put_score += w
        confirmations.append("reversal_put")

    # 8b) 5M HTF SNR Touch Confluence Weight (Massive weight 35 to dominate scoring)
    if touch_5m_support or touch_5m_resistance:
        w_htf_snr = 35
        max_score += w_htf_snr
        if touch_5m_support:
            call_score += w_htf_snr
            confirmations.append("htf_snr_call")
        else:
            put_score += w_htf_snr
            confirmations.append("htf_snr_put")

    # ---- VOLATILITY FILTER ----
    low_volatility = atr_avg > 0 and atr < (atr_avg * 0.4)

    # 9) ADX Trend Strength (weight 12)
    w = 12
    max_score += w
    strong_trend = adx >= 25
    if strong_trend and call_score >= put_score:
        call_score += w
    elif strong_trend and put_score > call_score:
        put_score += w

    # 10) Higher-timeframe trend confirmation (weight 15)
    w = 15
    if htf_trend in ("bull", "bear"):
        max_score += w
        if htf_trend == "bull" and call_score >= put_score:
            call_score += w
        elif htf_trend == "bear" and put_score > call_score:
            put_score += w

    # 11) Pivot-level entry trigger (weight 15)
    w = 15
    max_score += w
    pivot_dir, pivot_desc, pivot_weight = check_pivot_entry_trigger(df)
    if pivot_dir == "PUT" and put_score >= call_score:
        put_score += w
    elif pivot_dir == "CALL" and call_score >= put_score:
        call_score += w

    # 12) RSI Divergence (weight 15)
    w = 15
    max_score += w
    div_dir, div_desc, div_weight = detect_rsi_divergence(df)
    if div_dir == "PUT" and put_score >= call_score:
        put_score += w
        confirmations.append("divergence_put")
    elif div_dir == "CALL" and call_score >= put_score:
        call_score += w
        confirmations.append("divergence_call")

    # ---- SUPPORT/RESISTANCE FILTER ----
    sr_buffer = atr * 0.5 if atr > 0 else 0
    near_resistance = sr_buffer > 0 and (swing_high - close) <= sr_buffer
    near_support = sr_buffer > 0 and (close - swing_low) <= sr_buffer
    
    # Note: Only penalize trend follow setups heading directly into local walls, 
    # Do not penalize if it's a confirmed HTF SNR Reversal trade.
    if near_resistance and call_score > put_score and not touch_5m_support:
        call_score *= 0.6
    if near_support and put_score > call_score and not touch_5m_resistance:
        put_score *= 0.6

    # ---- COMPOUND CONFIDENCE CALCULATION ----
    call_confidence = round((call_score / max_score) * 100, 1) if max_score else 0
    put_confidence = round((put_score / max_score) * 100, 1) if max_score else 0

    confirmation_count = len(confirmations)
    if confirmation_count >= 6: boost = 35
    elif confirmation_count >= 5: boost = 25
    elif confirmation_count >= 4: boost = 18
    elif confirmation_count >= 3: boost = 10
    elif confirmation_count >= 2: boost = 5
    else: boost = 0

    if call_confidence > put_confidence:
        call_confidence = min(98, call_confidence + boost)
    elif put_confidence > call_confidence:
        put_confidence = min(98, put_confidence + boost)

    MIN_CONFIDENCE = min_confidence
    if low_volatility: MIN_CONFIDENCE = max(MIN_CONFIDENCE, min_confidence + 5)
    if not strong_trend: MIN_CONFIDENCE += 5

    # If it's a solid 5M HTF SNR setup, let it execute smoothly
    reversal_floor = max(min_confidence - 5, 65) 
    if reversal_call and touch_5m_support and call_confidence >= reversal_floor:
        return "CALL", call_confidence, reasons
    elif reversal_put and touch_5m_resistance and put_confidence >= reversal_floor:
        return "PUT", put_confidence, reasons

    # Normal check
    if call_confidence >= MIN_CONFIDENCE and call_confidence > put_confidence:
        return "CALL", call_confidence, reasons
    elif put_confidence >= MIN_CONFIDENCE and put_confidence > call_confidence:
        return "PUT", put_confidence, reasons
    else:
        reasons.append(f"ℹ️ Scores: CALL {call_confidence}% / PUT {put_confidence}%")
        return None, max(call_confidence, put_confidence), reasons
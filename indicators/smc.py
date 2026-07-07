import numpy as np
import pandas as pd
import config
import logging
import math
import pandas_ta as ta
from config import SWING_MIN_AMPLITUDE_PCT, DIVERGENCE_MAX_AGE_CANDLES

def sniper_get_htf_bias(df):
    """1 Günlük EMA20 vs EMA50 ile trend yönü belirle.
    Returns: 1 (Bullish/AL), -1 (Bearish/SAT), 0 (Nötr)
    """
    if len(df) < 50:
        return 0
    df = df.copy()
    if f'EMA_{config.IND_EMA_MID}' not in df.columns:
        df.ta.ema(length=config.IND_EMA_MID, append=True)
    if f'EMA_{config.IND_EMA_SLOW}' not in df.columns:
        df.ta.ema(length=config.IND_EMA_SLOW, append=True)

    ema20 = df[f'EMA_{config.IND_EMA_MID}'].iloc[-1]
    ema50 = df[f'EMA_{config.IND_EMA_SLOW}'].iloc[-1]
    close = df['close'].iloc[-1]

    if pd.isna(ema20) or pd.isna(ema50):
        return 0
    if ema20 > ema50 and close > ema20:
        return 1
    elif ema20 < ema50 and close < ema20:
        return -1
    return 0

def sniper_find_swing_points(df, point_type="low", neighbors=3, min_amplitude_pct=None):
    """Swing high veya swing low noktaları tespit et.
    NumPy vektörel hesaplama ile O(N) performans.
    RED-03: min_amplitude_pct filtresi — gürültü swing'leri eler.
    Returns: [(index, price), ...] listesi
    """
    if min_amplitude_pct is None:
        min_amplitude_pct = SWING_MIN_AMPLITUDE_PCT
    col = 'low' if point_type == "low" else 'high'
    values = df[col].values
    n = len(values)
    swings = []

    if n < 2 * neighbors + 1:
        return swings

    for i in range(neighbors, n - neighbors):
        val = values[i]
        if point_type == "low":
            is_swing = all(val <= values[i - j] for j in range(1, neighbors + 1))
            is_swing = is_swing and all(val < values[i + j] for j in range(1, neighbors + 1))
        else:
            is_swing = all(val >= values[i - j] for j in range(1, neighbors + 1))
            is_swing = is_swing and all(val > values[i + j] for j in range(1, neighbors + 1))
        if is_swing:
            window = values[max(0, i - neighbors):i + neighbors + 1]
            local_range = window.max() - window.min()
            if val > 0:
                amplitude_pct = (local_range / val) * 100
                if amplitude_pct >= min_amplitude_pct:
                    swings.append((i, val))
            else:
                swings.append((i, val))
    return swings

def sniper_detect_sweep(df, swing_points, point_type="low", lookback=10):
    """Son lookback mum içinde eski bir swing noktasının ihlal edilip geri çekilmesini tespit et.
    LONG (low): Fitil swing low altına sardı ama gövde yukarıda kapandı.
    SHORT (high): Fitil swing high üstüne sardı ama gövde aşağıda kapandı.
    """
    if not swing_points:
        return False, None

    check_start = max(0, len(df) - lookback)
    tolerance = getattr(config, 'MSB_TOLERANCE_PCT', 0.001)

    for i in range(check_start, len(df)):
        row = df.iloc[i]
        for sw_idx, sw_price in swing_points:
            if sw_idx >= i:
                continue

            if point_type == "low":
                target_price = sw_price * (1 + tolerance)
                if row['low'] < target_price and row['close'] > sw_price:
                    return True, sw_price
            else:
                target_price = sw_price * (1 - tolerance)
                if row['high'] > target_price and row['close'] < sw_price:
                    return True, sw_price

    return False, None

def sniper_detect_msb(df, swing_points, point_type="high"):
    """Market Structure Break tespiti.
    LONG (high): Son kapanış en son swing high'ın üzerinde mi?
    SHORT (low): Son kapanış en son swing low'un altında mı?
    """
    if not swing_points:
        return False, None, None

    last_sw_idx, last_sw_price = swing_points[-1]
    current_close = df['close'].iloc[-1]
    current_high = df['high'].iloc[-1]
    current_low = df['low'].iloc[-1]
    
    tolerance = getattr(config, 'MSB_TOLERANCE_PCT', 0.001)

    if point_type == "high":
        target_price = last_sw_price * (1 - tolerance)
        if current_close > target_price or current_high > last_sw_price:
            return True, last_sw_price, last_sw_idx
    elif point_type == "low":
        target_price = last_sw_price * (1 + tolerance)
        if current_close < target_price or current_low < last_sw_price:
            return True, last_sw_price, last_sw_idx

    return False, None, None

def sniper_calculate_ote(sweep_price, msb_price):
    """Fibonacci 0.618 - 0.786 OTE (Optimal Trade Entry) bölgesini hesapla."""
    fib_range = abs(msb_price - sweep_price)
    if math.isclose(fib_range, 0.0, abs_tol=1e-10):
        return 0, 0

    tolerance = getattr(config, 'OTE_TOLERANCE_PCT', 0.0)

    if sweep_price < msb_price:
        ote_top = msb_price - (fib_range * (config.FIB_618 - tolerance))
        ote_bottom = msb_price - (fib_range * (config.FIB_786 + tolerance))
    else:
        ote_bottom = msb_price + (fib_range * (config.FIB_618 - tolerance))
        ote_top = msb_price + (fib_range * (config.FIB_786 + tolerance))
    return ote_top, ote_bottom

def sniper_detect_fvg(df, ote_top, ote_bottom, lookback=15, direction="bullish"):
    """OTE bölgesi içinde doldurulmamış FVG (Fair Value Gap) tespit et."""
    search_start = max(1, len(df) - lookback)

    for i in range(search_start, len(df) - 1):
        if i < 1:
            continue

        if direction == "bullish":
            candle1_high = df['high'].iloc[i - 1]
            candle3_low = df['low'].iloc[i + 1]
            if candle3_low > candle1_high:
                gap_bottom = candle1_high
                gap_top = candle3_low
                if gap_bottom <= ote_top and gap_top >= ote_bottom:
                    filled = any(df['low'].iloc[j] <= gap_top for j in range(i + 2, len(df)))
                    if not filled:
                        return True, gap_bottom, gap_top
        else:
            candle1_low = df['low'].iloc[i - 1]
            candle3_high = df['high'].iloc[i + 1]
            if candle3_high < candle1_low:
                gap_top = candle1_low
                gap_bottom = candle3_high
                if gap_bottom <= ote_top and gap_top >= ote_bottom:
                    filled = any(df['high'].iloc[j] >= gap_bottom for j in range(i + 2, len(df)))
                    if not filled:
                        return True, gap_bottom, gap_top

    return False, None, None

def detect_sfp(df_4h, neighbors=3):
    """Swing Failure Pattern (Zirve Tuzağı) tespit eder.
    Returns: (sfp_found, swing_high_price, sfp_candle) veya (False, None, None)
    """
    if df_4h is None or len(df_4h) < 20:
        return False, None, None

    highs = df_4h['high'].values
    n = len(highs)
    swing_highs = []

    for i in range(neighbors, n - neighbors - 1):
        is_swing = True
        for j in range(1, neighbors + 1):
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_swing = False
                break
        if is_swing:
            swing_highs.append((i, highs[i]))

    if not swing_highs:
        return False, None, None

    last_swing_idx, last_swing_high = swing_highs[-1]
    last_candle = df_4h.iloc[-1]

    if last_candle['high'] > last_swing_high:
        body_close_ok = not config.SFP_BODY_CLOSE_INSIDE_REQUIRED or (last_candle['close'] < last_swing_high)
        if body_close_ok and last_candle['close'] < last_candle['open']:
            body = abs(last_candle['close'] - last_candle['open'])
            upper_wick = last_candle['high'] - max(last_candle['close'], last_candle['open'])
            if body > 0 and upper_wick > (config.CANDLE_HAMMER_LOWER_SHADOW_MULT * body):
                if config.SFP_VOLUME_CONFIRMATION_MULT > 0:
                    vol_sma = df_4h['volume'].rolling(20).mean().iloc[-1]
                    if not pd.isna(vol_sma) and last_candle['volume'] < vol_sma * config.SFP_VOLUME_CONFIRMATION_MULT:
                        return False, None, None

                if getattr(config, 'SFP_MFE_TIME_FILTER_REQUIRED', False):
                    candle_range = last_candle['high'] - last_candle['low']
                    if candle_range > 0:
                        close_percentile = (last_candle['close'] - last_candle['low']) / candle_range
                        if close_percentile > 0.40:
                            return False, None, None

                return True, last_swing_high, last_candle

    return False, None, None

def detect_premium_rejection(df_4h, df_1d):
    """Pahalı Bölge Reddi (SMC Premium Short) tespit eder.
    Fibonacci 0.618-0.786 bölgesinde bearish red arar.
    Returns: (found, fib_618, fib_786, entry_candle) veya (False, None, None, None)
    """
    if df_4h is None or len(df_4h) < 30 or df_1d is None or len(df_1d) < 20:
        return False, None, None, None

    df_1d = df_1d.copy()
    df_4h = df_4h.copy()

    if f'EMA_{config.IND_EMA_MID}' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_MID, append=True)
    if f'EMA_{config.IND_EMA_SLOW}' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_SLOW, append=True)
    last_1d = df_1d.iloc[-1]
    ema20_1d = last_1d.get(f'EMA_{config.IND_EMA_MID}')
    ema50_1d = last_1d.get(f'EMA_{config.IND_EMA_SLOW}')

    if ema20_1d is None or ema50_1d is None or pd.isna(ema20_1d) or pd.isna(ema50_1d):
        return False, None, None, None
    if ema20_1d >= ema50_1d:
        return False, None, None, None

    recent = df_4h.tail(90)
    swing_high_val = float(recent['high'].max())
    swing_high_idx = recent['high'].idxmax()
    after_high = recent.loc[swing_high_idx:]
    if len(after_high) < 5:
        return False, None, None, None

    high_position = list(recent.index).index(swing_high_idx)
    if high_position < 10:
        return False, None, None, None

    swing_low_val = float(after_high['low'].min())

    leg_range = swing_high_val - swing_low_val
    if leg_range <= 0:
        return False, None, None, None

    fib_width_pct = (leg_range / swing_high_val) * 100
    if fib_width_pct > 40:
        return False, None, None, None

    fib_618 = swing_low_val + config.FIB_618 * leg_range
    fib_786 = swing_low_val + config.FIB_786 * leg_range

    last_4h = df_4h.iloc[-1]
    current_close = float(last_4h['close'])

    if fib_618 <= current_close <= fib_786:
        is_red_candle = last_4h['close'] < last_4h['open']
        is_bearish_engulfing = (
            is_red_candle and
            len(df_4h) >= 2 and
            last_4h['open'] > df_4h.iloc[-2]['close'] and
            last_4h['close'] < df_4h.iloc[-2]['open']
        )

        df_4h.ta.ema(length=config.IND_EMA_MID, append=True)
        ema20_4h = last_4h.get(f'EMA_{config.IND_EMA_MID}')
        ema_rejection = False
        if ema20_4h is not None and not pd.isna(ema20_4h):
            ema_rejection = (last_4h['high'] >= ema20_4h and last_4h['close'] < ema20_4h
                             and is_red_candle)

        if is_bearish_engulfing or ema_rejection or is_red_candle:
            return True, fib_618, fib_786, last_4h

    return False, None, None, None

def detect_bearish_divergence(df_4h, neighbors=3):
    """Negatif Uyumsuzluk (Yorgunluk Tepesi) tespit eder.
    Fiyat Higher High + RSI Lower High + Hacim düşük + EMA20 kırılımı.
    Returns: (found, swing_high_1, swing_high_2, rsi_1, rsi_2) veya (False, ...)
    """
    if df_4h is None or len(df_4h) < 30:
        return False, None, None, None, None

    df_4h = df_4h.copy()
    df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_4h.ta.ema(length=config.IND_EMA_MID, append=True)

    rsi_col = 'RSI_14'
    if rsi_col not in df_4h.columns:
        return False, None, None, None, None

    highs = df_4h['high'].values
    n = len(highs)
    swing_points = []

    for i in range(neighbors, n - 1):
        is_swing = True
        for j in range(1, neighbors + 1):
            if i - j < 0 or i + j >= n:
                is_swing = False
                break
            if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                is_swing = False
                break
        if is_swing:
            swing_points.append(i)

    if len(swing_points) < 2:
        return False, None, None, None, None

    idx1, idx2 = swing_points[-2], swing_points[-1]
    
    if n - idx2 > 15:
        return False, None, None, None, None
        
    price_1 = float(df_4h.iloc[idx1]['high'])
    price_2 = float(df_4h.iloc[idx2]['high'])
    rsi_1 = float(df_4h.iloc[idx1][rsi_col])
    rsi_2 = float(df_4h.iloc[idx2][rsi_col])

    if price_2 > price_1 and rsi_2 < rsi_1 and (rsi_1 - rsi_2) >= 3:
        if rsi_1 < config.SHORT_RSI_OVERBOUGHT_LIMIT:
            return False, None, None, None, None

        if (len(df_4h) - 1 - idx2) > DIVERGENCE_MAX_AGE_CANDLES:
            return False, None, None, None, None

        if config.DIVERGENCE_MACD_CONFIRMATION_REQUIRED:
            df_4h.ta.macd(append=True)
            macdh_cols = [c for c in df_4h.columns if 'MACDh' in c]
            if macdh_cols:
                macd_1 = float(df_4h.iloc[idx1][macdh_cols[0]])
                macd_2 = float(df_4h.iloc[idx2][macdh_cols[0]])
                if price_2 > price_1 and macd_2 >= macd_1:
                    return False, None, None, None, None

        vol_1 = float(df_4h.iloc[idx1]['volume'])
        vol_2 = float(df_4h.iloc[idx2]['volume'])
        vol_divergence = vol_2 < vol_1

        last = df_4h.iloc[-1]
        ema20 = last.get(f'EMA_{config.IND_EMA_MID}')
        if ema20 is not None and not pd.isna(ema20):
            if float(last['close']) < float(ema20) and vol_divergence:
                return True, price_1, price_2, rsi_1, rsi_2

    return False, None, None, None, None

def detect_bullish_divergence(df_4h, neighbors=3):
    """Pozitif Uyumsuzluk (Dip Avcılığı Onayı) tespit eder.
    Fiyat Lower Low + RSI Higher Low + Hacim düşük + EMA20 kırılımı.
    Returns: (found, swing_low_1, swing_low_2, rsi_1, rsi_2) veya (False, ...)
    """
    if df_4h is None or len(df_4h) < 30:
        return False, None, None, None, None

    df_4h = df_4h.copy()
    df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_4h.ta.ema(length=config.IND_EMA_MID, append=True)

    rsi_col = 'RSI_14'
    if rsi_col not in df_4h.columns:
        return False, None, None, None, None

    lows = df_4h['low'].values
    n = len(lows)
    swing_points = []

    for i in range(neighbors, n - 1):
        is_swing = True
        for j in range(1, neighbors + 1):
            if i - j < 0 or i + j >= n:
                is_swing = False
                break
            if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                is_swing = False
                break
        if is_swing:
            swing_points.append(i)

    if len(swing_points) < 2:
        return False, None, None, None, None

    idx1, idx2 = swing_points[-2], swing_points[-1]
    
    if n - idx2 > 15:
        return False, None, None, None, None
        
    price_1 = float(df_4h.iloc[idx1]['low'])
    price_2 = float(df_4h.iloc[idx2]['low'])
    rsi_1 = float(df_4h.iloc[idx1][rsi_col])
    rsi_2 = float(df_4h.iloc[idx2][rsi_col])

    if price_2 < price_1 and rsi_2 > rsi_1 and (rsi_2 - rsi_1) >= 3:
        if (len(df_4h) - 1 - idx2) > DIVERGENCE_MAX_AGE_CANDLES:
            return False, None, None, None, None

        if config.DIVERGENCE_MACD_CONFIRMATION_REQUIRED:
            df_4h.ta.macd(append=True)
            macdh_cols = [c for c in df_4h.columns if 'MACDh' in c]
            if macdh_cols:
                macd_1 = float(df_4h.iloc[idx1][macdh_cols[0]])
                macd_2 = float(df_4h.iloc[idx2][macdh_cols[0]])
                if price_2 < price_1 and macd_2 <= macd_1:
                    return False, None, None, None, None

        vol_1 = float(df_4h.iloc[idx1]['volume'])
        vol_2 = float(df_4h.iloc[idx2]['volume'])
        vol_divergence = vol_2 < vol_1

        last = df_4h.iloc[-1]
        ema20 = last.get(f'EMA_{config.IND_EMA_MID}')
        if ema20 is not None and not pd.isna(ema20):
            if float(last['close']) > float(ema20) and vol_divergence:
                return True, price_1, price_2, rsi_1, rsi_2

    return False, None, None, None, None


def detect_adx_divergence(df, is_long=True, neighbors=3):
    """
    ADX Divergence tespit eder (Trend yorgunluğu).
    Fiyat lower low yaparken ADX lower high yapıyorsa (Düşüş momentumu azalıyor -> Bullish Divergence)
    Fiyat higher high yaparken ADX lower high yapıyorsa (Yükseliş momentumu azalıyor -> Bearish Divergence)
    """
    if df is None or len(df) < 30:
        return False
        
    df_temp = df.copy()
    # Check if ADX exists, else compute
    if 'ADX_14' not in df_temp.columns:
        df_temp.ta.adx(length=14, append=True)
    
    adx_col = 'ADX_14'
    if adx_col not in df_temp.columns:
        return False

    lows = df_temp['low'].values
    highs = df_temp['high'].values
    n = len(df_temp)
    swing_points = []

    for i in range(neighbors, n - 1):
        is_swing = True
        for j in range(1, neighbors + 1):
            if i - j < 0 or i + j >= n:
                is_swing = False
                break
            if is_long:
                if lows[i] >= lows[i - j] or lows[i] >= lows[i + j]:
                    is_swing = False
                    break
            else:
                if highs[i] <= highs[i - j] or highs[i] <= highs[i + j]:
                    is_swing = False
                    break
        if is_swing:
            swing_points.append(i)

    if len(swing_points) < 2:
        return False

    idx1, idx2 = swing_points[-2], swing_points[-1]
    
    if n - idx2 > 15:
        return False
        
    if is_long:
        price_1 = float(df_temp.iloc[idx1]['low'])
        price_2 = float(df_temp.iloc[idx2]['low'])
    else:
        price_1 = float(df_temp.iloc[idx1]['high'])
        price_2 = float(df_temp.iloc[idx2]['high'])
        
    adx_1 = float(df_temp.iloc[idx1][adx_col])
    adx_2 = float(df_temp.iloc[idx2][adx_col])

    if is_long:
        # Fiyat lower low, ADX lower high
        if price_2 < price_1 and adx_2 < adx_1 and (adx_1 - adx_2) >= 1.0:
            return True
    else:
        # Fiyat higher high, ADX lower high
        if price_2 > price_1 and adx_2 < adx_1 and (adx_1 - adx_2) >= 1.0:
            return True

    return False


def detect_bos_choch_zones(df, pivot_lookbacks=[1, 2, 3, 5, 11, 15, 20], max_boxes=50, require_inducement=False):
    """
    SMC tabanlı Supply (Arz) ve Demand (Talep) bölgelerini tespit eder.
    Pine Script (BOS/CHOCH Demand & Supply) eşdeğeri.
    """
    if len(df) < max(pivot_lookbacks) * 2 + 1:
        return [], []
        
    highs = df['high'].values
    lows = df['low'].values
    opens = df['open'].values
    closes = df['close'].values
    n = len(df)
    
    num_lookbacks = len(pivot_lookbacks)
    
    lastHighs = [None] * num_lookbacks
    prevHighs = [None] * num_lookbacks
    lastHigh_highs = [None] * num_lookbacks
    lastHigh_opens = [None] * num_lookbacks
    lastHigh_closes = [None] * num_lookbacks
    lastHighBars = [None] * num_lookbacks
    
    lastLows = [None] * num_lookbacks
    prevLows = [None] * num_lookbacks
    lastLows_low = [None] * num_lookbacks
    lastLows_opens = [None] * num_lookbacks
    lastLows_closes = [None] * num_lookbacks
    lastLowBars = [None] * num_lookbacks
    
    bullBoxes = []
    bearBoxes = []
    
    for i in range(n):
        for li, lookback in enumerate(pivot_lookbacks):
            if i < 2 * lookback:
                continue
                
            pivot_idx = i - lookback
            
            # Check if pivot_idx is a pivot high
            is_pivot_high = True
            for k in range(1, lookback + 1):
                if highs[pivot_idx] <= highs[pivot_idx - k] or highs[pivot_idx] <= highs[pivot_idx + k]:
                    is_pivot_high = False
                    break
            
            if is_pivot_high:
                prevHighs[li] = lastHighs[li]
                lastHighs[li] = highs[pivot_idx]
                lastHighBars[li] = pivot_idx
                lastHigh_highs[li] = highs[pivot_idx]
                lastHigh_opens[li] = opens[pivot_idx]
                lastHigh_closes[li] = closes[pivot_idx]
                
            # Check if pivot_idx is a pivot low
            is_pivot_low = True
            for k in range(1, lookback + 1):
                if lows[pivot_idx] >= lows[pivot_idx - k] or lows[pivot_idx] >= lows[pivot_idx + k]:
                    is_pivot_low = False
                    break
            
            if is_pivot_low:
                prevLows[li] = lastLows[li]
                lastLows[li] = lows[pivot_idx]
                lastLowBars[li] = pivot_idx
                lastLows_low[li] = lows[pivot_idx]
                lastLows_opens[li] = opens[pivot_idx]
                lastLows_closes[li] = closes[pivot_idx]
                
            lastHigh = lastHighs[li]
            prevHigh = prevHighs[li]
            lastLow = lastLows[li]
            prevLow = prevLows[li]
            
            # Break Conditions
            isBullishBreak = False
            if lastHigh is not None and lastLow is not None and i > 0:
                isBullishBreak = closes[i] > lastHigh and highs[i-1] < lastHigh
                
            isBearishBreak = False
            if lastLow is not None and lastHigh is not None and i > 0:
                isBearishBreak = closes[i] < lastLow and lows[i-1] > lastLow
                
            # Inducement Logic
            inducementTaken_Bullish = False
            if prevLow is not None and lastLow is not None:
                inducementTaken_Bullish = lastLow < prevLow
                
            inducementTaken_Bearish = False
            if prevHigh is not None and lastHigh is not None:
                inducementTaken_Bearish = lastHigh > prevHigh
                
            # Drawing Logic Bullish
            if isBullishBreak:
                isCHOCH = prevHigh is not None and lastHigh < prevHigh
                isBOS = prevHigh is None or lastHigh > prevHigh
                
                if (isCHOCH or isBOS) and (not require_inducement or inducementTaken_Bullish):
                    boxTop = max(lastLows_opens[li], lastLows_closes[li])
                    boxBottom = lastLows_low[li]
                    bullBoxes.append({
                        'left': lastLowBars[li],
                        'top': float(boxTop),
                        'bottom': float(boxBottom),
                        'mitigated': False,
                        'lookback': lookback,
                        'type': 'CHOCH Demand' if isCHOCH else 'BOS Demand'
                    })
                    
            # Drawing Logic Bearish
            if isBearishBreak:
                isCHOCH = prevLow is not None and lastLow > prevLow
                isBOS = prevLow is None or lastLow < prevLow
                
                if (isCHOCH or isBOS) and (not require_inducement or inducementTaken_Bearish):
                    boxTop = lastHigh_highs[li]
                    boxBottom = min(lastHigh_opens[li], lastHigh_closes[li])
                    bearBoxes.append({
                        'left': lastHighBars[li],
                        'top': float(boxTop),
                        'bottom': float(boxBottom),
                        'mitigated': False,
                        'lookback': lookback,
                        'type': 'CHOCH Supply' if isCHOCH else 'BOS Supply'
                    })
                    
        # Manage Boxes (Mitigation & Break)
        # Bearish Boxes (Supply)
        active_bear = []
        for box in bearBoxes:
            if highs[i] > box['top']:
                continue
            if not box['mitigated'] and highs[i] >= box['bottom']:
                box['mitigated'] = True
            active_bear.append(box)
        bearBoxes = active_bear[-max_boxes:]
        
        # Bullish Boxes (Demand)
        active_bull = []
        for box in bullBoxes:
            if lows[i] < box['bottom']:
                continue
            if not box['mitigated'] and lows[i] <= box['top']:
                box['mitigated'] = True
            active_bull.append(box)
        bullBoxes = active_bull[-max_boxes:]

    return bullBoxes, bearBoxes


def detect_supply_zones(df, pivot_lookbacks=[1, 2, 3, 5, 11, 15, 20]):
    """
    Returns active, unmitigated Supply Zones based on BOS/CHOCH logic.
    Geriye dönük uyumluluk için `crypto.py` tarafından çağrılır.
    """
    bull, bear = detect_bos_choch_zones(df, pivot_lookbacks)
    # Sadece mitigated (ihlal) olmamış taze kutuları al
    return [b for b in bear if not b['mitigated']]

def detect_demand_zones(df, pivot_lookbacks=[1, 2, 3, 5, 11, 15, 20]):
    """
    Returns active, unmitigated Demand Zones based on BOS/CHOCH logic.
    """
    bull, bear = detect_bos_choch_zones(df, pivot_lookbacks)
    return [b for b in bull if not b['mitigated']]


def is_price_in_supply_zone(price, supply_zones, tolerance_pct=0.02):
    """
    Anlık fiyatın aktif arz bölgelerine (Supply Zones) belirtilen % tolerans 
    kadar yakın olup olmadığını kontrol eder. (Varsayılan %2 altı/üstü)
    """
    if not supply_zones:
        return False
        
    for zone in supply_zones:
        lower_bound = zone['bottom'] * (1 - tolerance_pct)
        upper_bound = zone['top'] * (1 + tolerance_pct)
        if lower_bound <= price <= upper_bound:
            return True
            
    return False

def is_price_in_demand_zone(price, demand_zones, tolerance_pct=0.02):
    """
    Anlık fiyatın aktif talep bölgelerine (Demand Zones) belirtilen % tolerans 
    kadar yakın olup olmadığını kontrol eder. (Varsayılan %2 altı/üstü)
    """
    if not demand_zones:
        return False
        
    for zone in demand_zones:
        lower_bound = zone['bottom'] * (1 - tolerance_pct)
        upper_bound = zone['top'] * (1 + tolerance_pct)
        if lower_bound <= price <= upper_bound:
            return True
            
    return False

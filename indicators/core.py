import numpy as np
import pandas as pd
import config
import pandas_ta as ta
import logging
import math

try:
    import talib
    TA_LIB_AVAILABLE = True
    logging.info("TA-Lib tespit edildi, hizlandirilmis motor kullanilacak.")
except ImportError:
    TA_LIB_AVAILABLE = False
    logging.info("TA-Lib bulunamadi, Pandas-TA fallback modunda çalisiliyor.")

def inject_smart_indicators(df):
    """
    Kritik indikatörleri TA-Lib oncelikli, Pandas-TA fallback'li olarak df uzerine ekler.
    Tüm stratejilerin basinda cagirilir.
    """
    df_copy = df.copy()
    
    # EMA
    if TA_LIB_AVAILABLE:
        df_copy[f'EMA_{config.IND_EMA_FAST}'] = talib.EMA(df_copy['close'].values, timeperiod=config.IND_EMA_FAST)
        df_copy[f'EMA_{config.IND_EMA_MID}'] = talib.EMA(df_copy['close'].values, timeperiod=config.IND_EMA_MID)
        df_copy[f'EMA_{config.IND_EMA_SLOW}'] = talib.EMA(df_copy['close'].values, timeperiod=config.IND_EMA_SLOW)
        df_copy[f'EMA_{config.IND_EMA_21}'] = talib.EMA(df_copy['close'].values, timeperiod=config.IND_EMA_21)
        df_copy[f'EMA_{config.IND_EMA_55}'] = talib.EMA(df_copy['close'].values, timeperiod=config.IND_EMA_55)
        df_copy[f'EMA_20'] = talib.EMA(df_copy['close'].values, timeperiod=20)
    else:
        df_copy.ta.ema(length=config.IND_EMA_FAST, append=True)
        df_copy.ta.ema(length=config.IND_EMA_MID, append=True)
        df_copy.ta.ema(length=config.IND_EMA_SLOW, append=True)
        df_copy.ta.ema(length=config.IND_EMA_21, append=True)
        df_copy.ta.ema(length=config.IND_EMA_55, append=True)
        df_copy.ta.ema(length=20, append=True)
        
    # SMA
    if TA_LIB_AVAILABLE:
        df_copy[f'SMA_{config.IND_SMA_SLOW}'] = talib.SMA(df_copy['close'].values, timeperiod=config.IND_SMA_SLOW)
        df_copy[f'SMA_100'] = talib.SMA(df_copy['close'].values, timeperiod=100)
        df_copy[f'SMA_{config.IND_SMA_TREND}'] = talib.SMA(df_copy['close'].values, timeperiod=config.IND_SMA_TREND)
        df_copy[f'SMA_200'] = talib.SMA(df_copy['close'].values, timeperiod=200)
    else:
        df_copy.ta.sma(length=config.IND_SMA_SLOW, append=True)
        df_copy.ta.sma(length=100, append=True)
        df_copy.ta.sma(length=config.IND_SMA_TREND, append=True)
        df_copy.ta.sma(length=200, append=True)
        
    # RSI
    if TA_LIB_AVAILABLE:
        df_copy[f'RSI_{config.IND_RSI_LENGTH}'] = talib.RSI(df_copy['close'].values, timeperiod=config.IND_RSI_LENGTH)
    else:
        df_copy.ta.rsi(length=config.IND_RSI_LENGTH, append=True)

    # ADX
    if TA_LIB_AVAILABLE:
        df_copy[f'ADX_{config.IND_ADX_LENGTH}'] = talib.ADX(df_copy['high'].values, df_copy['low'].values, df_copy['close'].values, timeperiod=config.IND_ADX_LENGTH)
    else:
        df_copy.ta.adx(length=config.IND_ADX_LENGTH, append=True)
        
    # ATR
    if TA_LIB_AVAILABLE:
        df_copy[f'ATRr_{config.IND_ATR_LENGTH}'] = talib.ATR(df_copy['high'].values, df_copy['low'].values, df_copy['close'].values, timeperiod=config.IND_ATR_LENGTH)
    else:
        df_copy.ta.atr(length=config.IND_ATR_LENGTH, append=True)
        
    # MACD
    if TA_LIB_AVAILABLE:
        macd, macdsignal, macdhist = talib.MACD(df_copy['close'].values, fastperiod=12, slowperiod=26, signalperiod=9)
        df_copy['MACD_12_26_9'] = macd
        df_copy['MACDh_12_26_9'] = macdhist
        df_copy['MACDs_12_26_9'] = macdsignal
    else:
        df_copy.ta.macd(fast=12, slow=26, signal=9, append=True)
        
    # BBANDS
    if TA_LIB_AVAILABLE:
        upper, middle, lower = talib.BBANDS(df_copy['close'].values, timeperiod=config.IND_BBANDS_LENGTH, nbdevup=config.IND_BBANDS_STD, nbdevdn=config.IND_BBANDS_STD, matype=0)
        df_copy[f'BBL_{config.IND_BBANDS_LENGTH}_{config.IND_BBANDS_STD}'] = lower
        df_copy[f'BBM_{config.IND_BBANDS_LENGTH}_{config.IND_BBANDS_STD}'] = middle
        df_copy[f'BBU_{config.IND_BBANDS_LENGTH}_{config.IND_BBANDS_STD}'] = upper
    else:
        df_copy.ta.bbands(length=config.IND_BBANDS_LENGTH, std=config.IND_BBANDS_STD, append=True)
        
    # Keltner Channels
    df_copy.ta.kc(length=config.IND_BBANDS_LENGTH, scalar=config.KC_SCALAR, append=True) # TA-Lib has no native KC, use pandas-ta
    
    # Williams %R with 13 EMA (TRI)
    if TA_LIB_AVAILABLE:
        df_copy['WILLR_21'] = talib.WILLR(df_copy['high'].values, df_copy['low'].values, df_copy['close'].values, timeperiod=21)
        df_copy['WILLR_21_EMA_13'] = talib.EMA(df_copy['WILLR_21'].values, timeperiod=13)
    else:
        df_copy.ta.willr(length=21, append=True)
        # pandas-ta outputs 'WILLR_21'
        df_copy.ta.ema(close='WILLR_21', length=13, append=True)
        # It creates EMA_13 so we should rename it to be consistent, but wait, pandas-ta ema uses close by default.
        # df.ta.ema(close=df_copy['WILLR_21'], length=13) might be better. Let's do it manually.
        df_copy['WILLR_21_EMA_13'] = df_copy['WILLR_21'].ewm(span=13, adjust=False).mean()
    
    # Choppiness Index (CHOP) - Pandas-TA automatically names it CHOP_14_1_100
    df_copy.ta.chop(length=14, append=True)
    
    # Volume SMA 20 (for VSA No Demand/No Supply)
    if TA_LIB_AVAILABLE:
        df_copy['VOL_SMA_20'] = talib.SMA(df_copy['volume'].values, timeperiod=20)
    else:
        df_copy['VOL_SMA_20'] = df_copy['volume'].rolling(window=20).mean()
        
    return df_copy

def calculate_anchored_vwap_series(df, anchor_type="weekly"):
    """Haftalık veya Aylık açılışa göre Anchored VWAP Serisini tamamen vektörel hesaplar."""
    if len(df) < 10:
        return pd.Series(np.nan, index=df.index)
    
    df_copy = df.copy()
    if not isinstance(df_copy.index, pd.DatetimeIndex):
        df_copy.index = pd.to_datetime(df_copy.index)
        
    tp = (df_copy['high'] + df_copy['low'] + df_copy['close']) / 3
    tp_vol = tp * df_copy['volume']
    
    if anchor_type == "weekly":
        grouper = [df_copy.index.isocalendar().year, df_copy.index.isocalendar().week]
    elif anchor_type == "monthly":
        grouper = [df_copy.index.year, df_copy.index.month]
    else:
        grouper = [df_copy.index.isocalendar().year, df_copy.index.isocalendar().week]
        
    cum_tp_vol = tp_vol.groupby(grouper).cumsum()
    cum_vol = df_copy['volume'].groupby(grouper).cumsum()
    
    vwap_series = cum_tp_vol / cum_vol
    return vwap_series

def calculate_anchored_vwap(df, anchor_type="weekly"):
    """Geriye donuk uyumluluk icin sadece son VWAP degerini dondurur."""
    vwap_series = calculate_anchored_vwap_series(df, anchor_type)
    if vwap_series.empty or pd.isna(vwap_series.iloc[-1]):
        return None
    return float(vwap_series.iloc[-1])

def detect_vwap_bounce(df, vwap_val):
    """
    Son mum VWAP'a değip Pin Bar bıraktı mı ve VWAP üzerinde tutundu mu?
    VWAP_SLOPE_CONFIRMATION: VWAP eğiminin pozitif olmasını şart koşar.
    VWAP_BOUNCE_CANDLE_CONFIRM: VWAP üzerinde kapanan ardışık onay mum sayısı.
    Returns: (bounce_ok, wick_low)
    """
    if vwap_val is None or len(df) < max(5, config.VWAP_SLOPE_LOOKBACK, config.VWAP_BOUNCE_CANDLE_CONFIRM):
        return False, None

    last = df.iloc[-1]
    
    # 1. VWAP Eğim Kontrolü
    if config.VWAP_SLOPE_CONFIRMATION:
        vwap_series = calculate_anchored_vwap_series(df, anchor_type="weekly")
        if len(vwap_series) >= config.VWAP_SLOPE_LOOKBACK:
            vwap_past = vwap_series.iloc[-config.VWAP_SLOPE_LOOKBACK:]
            if vwap_past.iloc[0] >= vwap_past.iloc[-1]:
                return False, None

    # 2. VWAP Değme (Touch) ve İğne Kontrolü (Front-running toleransı eklendi)
    touched_vwap = False
    vwap_upper_band = vwap_val * (1 + getattr(config, 'VWAP_TOLERANCE_PCT', 0.003))
    for i in range(1, config.VWAP_BOUNCE_CANDLE_CONFIRM + 2):
        if i <= len(df):
            c = df.iloc[-i]
            if c['low'] <= vwap_upper_band:
                touched_vwap = True
                break
    if not touched_vwap:
        return False, None

    # 3. Çoklu Mum Kapanış Onayı
    for i in range(1, config.VWAP_BOUNCE_CANDLE_CONFIRM + 1):
        c = df.iloc[-i]
        if c['close'] <= vwap_val:
            return False, None

    body = abs(last['close'] - last['open'])
    if math.isclose(body, 0.0, abs_tol=1e-10):
        body = last['close'] * 0.0001
    lower_wick = min(last['close'], last['open']) - last['low']
    shadow_mult = getattr(config, 'VWAP_BOUNCE_LOWER_SHADOW_MULT', 0.5)
    if lower_wick < body * shadow_mult:
        return False, None

    return True, float(last['low'])

def calculate_relative_strength(df_stock, df_index):
    """Hissenin endekse göre göreceli gücünü hesaplar.
    Returns: (rs_strong, rs_trend_up, index_stressed, index_recovering)
    """
    if df_stock is None or df_index is None:
        return False, False, False, False
    if len(df_stock) < 55 or len(df_index) < 55:
        return False, False, False, False

    common_idx = df_stock.index.intersection(df_index.index)
    if len(common_idx) < 55:
        return False, False, False, False

    stock_c = df_stock.loc[common_idx]['close']
    index_c = df_index.loc[common_idx]['close']

    rs_line = stock_c / index_c
    rs_sma = rs_line.rolling(config.IND_RS_SMA_LENGTH).mean()

    rs_strong = bool(not pd.isna(rs_sma.iloc[-1]) and rs_line.iloc[-1] > rs_sma.iloc[-1])

    rs_trend_up = False
    if len(rs_line) >= config.IND_RS_MOMENTUM_LONG_START:
        rs_trend_up = bool(rs_line.iloc[-config.IND_RS_MOMENTUM_SHORT:].mean() > rs_line.iloc[-config.IND_RS_MOMENTUM_LONG_START:-config.IND_RS_MOMENTUM_LONG_END].mean())

    index_stressed = False
    if len(index_c) >= 6:
        idx_chg = (index_c.iloc[-1] - index_c.iloc[-6]) / index_c.iloc[-6] * 100
        index_stressed = bool(idx_chg < -2.0)

    index_recovering = False
    if len(df_index) >= 10:
        df_idx = df_index.copy()
        if 'EMA_8' not in df_idx.columns:
            df_idx.ta.ema(length=config.IND_EMA_FAST, append=True)
        ema_col = 'EMA_8' if 'EMA_8' in df_idx.columns else None
        if ema_col and not pd.isna(df_idx[ema_col].iloc[-1]):
            index_recovering = bool(df_idx['close'].iloc[-1] > df_idx[ema_col].iloc[-1])

    return rs_strong, rs_trend_up, index_stressed, index_recovering

def get_trend_sma(last_1d: dict) -> float:
    """
    Sma fallback hiyerarşisine göre trend sma değerini döndürür.
    Öncelik: SMA_200 -> SMA_100 -> SMA_50
    Eğer hiçbiri yoksa None döner.
    """
    if last_1d is None:
        return None
        
    sma_200 = last_1d.get("SMA_200")
    if sma_200 is not None and not pd.isna(sma_200):
        return float(sma_200)
        
    sma_100 = last_1d.get("SMA_100")
    if sma_100 is not None and not pd.isna(sma_100):
        return float(sma_100)
        
    sma_50 = last_1d.get("SMA_50")
    if sma_50 is not None and not pd.isna(sma_50):
        return float(sma_50)
        
    return None

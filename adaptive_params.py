"""
adaptive_params.py — Dinamik Adaptif Parametre Motoru (V3.2 Kaos Çözümü #1)
Statik parametreleri piyasa volatilitesine göre otomatik adapte eder.
Walk-Forward prensibi: Parametreler geçmiş veriye göre kalibre edilir.
"""
import json
import logging
import os
import threading
import numpy as np
import pandas as pd
from datetime import datetime, timezone

ADAPTIVE_STATE_FILE = "adaptive_params_state.json"
_adaptive_lock = threading.Lock()

# ════════════════════════════════════════
# Temel Volatilite Hesaplayıcıları
# ════════════════════════════════════════

def calculate_volatility_percentile(df, window_short=30, window_long=180):
    """
    Mevcut volatilitenin tarihsel dağılımdaki yüzdelik dilimini hesaplar.
    0.0 = Tarihsel olarak en düşük volatilite
    1.0 = Tarihsel olarak en yüksek volatilite
    """
    if df is None or len(df) < window_long:
        return 0.5  # Yetersiz veri → nötr varsay
    
    try:
        close = df['close'] if 'close' in df.columns else df['Close']
        returns = close.pct_change().dropna()
        
        if len(returns) < window_long:
            return 0.5
        
        current_vol = returns.tail(window_short).std()
        historical_vol = returns.tail(window_long).std()
        all_rolling = returns.rolling(window=window_short).std().dropna()
        
        if len(all_rolling) == 0 or historical_vol == 0:
            return 0.5
        
        percentile = (all_rolling < current_vol).sum() / len(all_rolling)
        return float(np.clip(percentile, 0.0, 1.0))
    except Exception as e:
        logging.warning(f"[adaptive_params] Volatilite hesaplama hatası: {e}")
        return 0.5


def get_adaptive_rsi_threshold(market: str, vol_percentile: float, direction: str = "oversold") -> float:
    """
    Volatiliteye göre RSI eşiğini dinamik hesaplar.
    
    Düşük volatilite (vol_pct < 0.3): RSI eşikleri gevşer (daha kolay sinyal)
    Yüksek volatilite (vol_pct > 0.7): RSI eşikleri sıkılaşır (daha seçici)
    
    BIST Oversold: 30-45 arası (baz: 40)
    Crypto Oversold: 20-35 arası (baz: 28)
    FOMO Overbought: 80-92 arası (baz: 85)
    """
    vol_pct = float(np.clip(vol_percentile, 0.0, 1.0))
    
    if direction == "oversold":
        if market == "BIST":
            # Düşük vol → 45 (gevşek), Yüksek vol → 30 (sıkı dip arama)
            base, low, high = 40, 30, 45
        else:  # CRYPTO
            # Düşük vol → 35 (gevşek), Yüksek vol → 20 (gerçek dip)
            base, low, high = 28, 20, 35
        # Yüksek volatilite → düşük eşik (daha derin dip gerekir)
        return round(high - (high - low) * vol_pct)
    
    elif direction == "overbought":
        # FOMO: Düşük vol → 80 (erken çık), Yüksek vol → 92 (trend güçlüyse sabret)
        base, low, high = 85, 80, 92
        return round(low + (high - low) * vol_pct)
    
    return base


def get_adaptive_atr_multiplier(market: str, df=None, base_mult=None) -> float:
    """
    ATR çarpanını mevcut vs tarihsel volatilite oranına göre ayarlar.
    
    Formül: adaptive_mult = base_mult × (son_30g_ATR_ort / son_180g_ATR_ort)
    Sınırlar: base × 0.6 ile base × 1.8 arası (aşırı değerler engellenir)
    """
    from config import ATR_MULTIPLIER_BIST, ATR_MULTIPLIER_CRYPTO
    
    if base_mult is None:
        base_mult = ATR_MULTIPLIER_BIST if market == "BIST" else ATR_MULTIPLIER_CRYPTO
    
    if df is None or len(df) < 180:
        return base_mult
    
    try:
        close = df['close'] if 'close' in df.columns else df['Close']
        high = df['high'] if 'high' in df.columns else df['High']
        low = df['low'] if 'low' in df.columns else df['Low']
        
        # True Range hesapla
        tr1 = high - low
        tr2 = abs(high - close.shift(1))
        tr3 = abs(low - close.shift(1))
        tr = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
        
        atr_short = tr.tail(30).mean()
        atr_long = tr.tail(180).mean()
        
        if atr_long == 0 or np.isnan(atr_long) or np.isnan(atr_short):
            return base_mult
        
        ratio = atr_short / atr_long
        adaptive = base_mult * ratio
        
        # Güvenlik sınırları: base × 0.6 ~ base × 1.8
        min_mult = base_mult * 0.6
        max_mult = base_mult * 1.8
        return float(np.clip(adaptive, min_mult, max_mult))
    except Exception as e:
        logging.warning(f"[adaptive_params] ATR çarpan hesaplama hatası: {e}")
        return base_mult


def get_adaptive_bb_width(df=None, base_width=0.15) -> float:
    """
    Bollinger Band genişliğini varlığa özel tarihsel dağılıma göre ayarlar.
    Eğer varlık normalde geniş BB'ye sahipse, eşik de genişler.
    """
    if df is None or len(df) < 60:
        return base_width
    
    try:
        close = df['close'] if 'close' in df.columns else df['Close']
        
        # Son 60 mum için BB genişliklerini hesapla
        sma = close.rolling(20).mean()
        std = close.rolling(20).std()
        upper = sma + 2 * std
        lower = sma - 2 * std
        bb_width = ((upper - lower) / sma).dropna()
        
        if len(bb_width) < 20:
            return base_width
        
        # Eşik = tarihsel BB genişliğinin 25. yüzdelik dilimi
        threshold = float(bb_width.quantile(0.25))
        # Güvenlik: base × 0.5 ile base × 3.0 arası
        return float(np.clip(threshold, base_width * 0.5, base_width * 3.0))
    except Exception as e:
        logging.warning(f"[adaptive_params] BB genişlik hesaplama hatası: {e}")
        return base_width


def get_adaptive_adx_threshold(vol_percentile: float) -> dict:
    """
    ADX eşiklerini volatiliteye göre ayarlar.
    
    Returns:
        {"trend": int, "too_late": int, "strong": int}
    """
    vol_pct = float(np.clip(vol_percentile, 0.0, 1.0))
    
    # Yüksek vol → ADX eşiği düşer (güçlü trend zaten var)
    # Düşük vol → ADX eşiği yükselir (sahte trend riski)
    trend = round(28 - 8 * vol_pct)      # 20-28 arası
    too_late = round(50 - 10 * vol_pct)   # 40-50 arası
    strong = round(35 - 10 * vol_pct)     # 25-35 arası
    
    return {
        "trend": max(trend, 18),
        "too_late": max(too_late, 38),
        "strong": max(strong, 22)
    }


def get_adaptive_volume_mult(vol_percentile: float, base_mult=1.5) -> float:
    """
    Hacim çarpanını volatiliteye göre ayarlar.
    Düşük vol dönemde → 2.0x (gerçek breakout lazım)
    Yüksek vol dönemde → 1.2x (zaten herkes işlem yapıyor)
    """
    vol_pct = float(np.clip(vol_percentile, 0.0, 1.0))
    # Ters orantı: yüksek vol → düşük eşik
    adaptive = 2.0 - 0.8 * vol_pct
    return float(np.clip(adaptive, 1.0, 2.5))


# ════════════════════════════════════════
# Adaptif Parametre Paketi
# ════════════════════════════════════════

def get_adaptive_params(market: str, df_daily=None) -> dict:
    """
    Tek bir çağrıyla tüm adaptif parametreleri döner.
    Strateji dosyasından kolayca kullanılır:
    
        params = get_adaptive_params("BIST", df_daily)
        rsi_threshold = params["rsi_oversold"]
    """
    vol_pct = calculate_volatility_percentile(df_daily) if df_daily is not None else 0.5
    adx = get_adaptive_adx_threshold(vol_pct)
    
    params = {
        "vol_percentile": vol_pct,
        "rsi_oversold": get_adaptive_rsi_threshold(market, vol_pct, "oversold"),
        "rsi_overbought": get_adaptive_rsi_threshold(market, vol_pct, "overbought"),
        "atr_multiplier": get_adaptive_atr_multiplier(market, df_daily),
        "bb_width": get_adaptive_bb_width(df_daily),
        "adx_trend": adx["trend"],
        "adx_too_late": adx["too_late"],
        "adx_strong": adx["strong"],
        "volume_mult": get_adaptive_volume_mult(vol_pct),
    }
    
    # Durum kaydet (debug ve izleme için)
    _save_adaptive_state(market, params)
    return params


def _save_adaptive_state(market: str, params: dict):
    """Adaptif parametre durumunu dosyaya kaydet (izleme/debug)."""
    with _adaptive_lock:
        state = {}
        if os.path.exists(ADAPTIVE_STATE_FILE):
            try:
                with open(ADAPTIVE_STATE_FILE, 'r', encoding='utf-8') as f:
                    state = json.load(f)
            except Exception:
                state = {}
        
        state[market] = {
            **params,
            "updated_at": datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
        }
        
        try:
            with open(ADAPTIVE_STATE_FILE, 'w', encoding='utf-8') as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
        except Exception as e:
            logging.warning(f"[adaptive_params] State kaydedilemedi: {e}")


def get_last_adaptive_state() -> dict:
    """Son adaptif parametre durumunu oku (heartbeat/debug için)."""
    if os.path.exists(ADAPTIVE_STATE_FILE):
        try:
            with open(ADAPTIVE_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            import logging
            logging.error(f"Beklenmeyen hata: {e}")
    return {}

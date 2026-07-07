"""
strategies/crypto/__init__.py — Kripto Strateji Paketi Giriş Noktası
Tüm Kripto stratejilerini LONG ve SHORT olarak modüler yapıda çalıştırır.
"""
import pandas as pd
import importlib
import pkgutil

import config
from indicators import get_trend_sma

# Shared helpers and validators
from strategies.crypto.shared import (
    _ensure_crypto_indicators,
    _filter_crypto_signals,
    _is_crypto_signal_valid,
)

from strategies.crypto.base import StrategyRegistry, StrategyContext

# Dynamically discover and register strategies
def _load_strategies():
    from . import long, short
    for _, module_name, _ in pkgutil.iter_modules(long.__path__):
        importlib.import_module(f"strategies.crypto.long.{module_name}")
    for _, module_name, _ in pkgutil.iter_modules(short.__path__):
        importlib.import_module(f"strategies.crypto.short.{module_name}")

_load_strategies()

# Expose private function for backtest compatibility
_is_crypto_signal_valid = _is_crypto_signal_valid

def _collect_metrics(symbol, current_price, last_1d, last_4h, metrics_collector):
    """SRP: Metrik toplama işlemini ayırır."""
    if metrics_collector is not None:
        metrics_collector[symbol] = {
            "Symbol": symbol, "Market": "KRIPTO", "Price": current_price,
            "1D RSI": round(last_1d.get("RSI_14", 0), 2) if pd.notna(last_1d.get("RSI_14")) else None,
            "4H ADX": round(last_4h.get("ADX_14", 0), 2) if pd.notna(last_4h.get("ADX_14")) else None,
            "1H RSI": None,
            "1D SMA 50": round(last_1d.get("EMA_50", 0), 2) if pd.notna(last_1d.get("EMA_50")) else None,
            "1D Trend SMA": round(get_trend_sma(last_1d), 2) if pd.notna(get_trend_sma(last_1d)) else None,
            "Trend": "Bullish" if last_1d.get("EMA_20", 0) > last_1d.get("EMA_50", float('inf')) else "Bearish",
            "1H Volume": last_4h.get("volume")
        }

def _build_context(symbol, df_1d, df_4h, current_price, last_1d, last_4h, btc_ok, btc_sniper_bias, df_1h_sniper):
    """SRP: Context nesnesi yaratma işlemini ayırır."""
    adx_1d = last_1d.get('ADX_14', 0)
    is_choppy = adx_1d < 25 if not pd.isna(adx_1d) else False

    adx_val = last_4h.get('ADX_14', 0)
    if pd.isna(adx_val): 
        adx_val = 0
        
    dynamic_atr_mult = 2.0 if adx_val > 25 else 1.2

    return StrategyContext(
        symbol=symbol, 
        last_1d=last_1d, 
        last_4h=last_4h,
        current_price=current_price, 
        df_1d=df_1d, 
        df_4h=df_4h,
        btc_ok=btc_ok, 
        btc_sniper_bias=btc_sniper_bias,
        dynamic_atr_mult=dynamic_atr_mult,
        is_choppy=is_choppy, 
        adx_1d=adx_1d,
        market="KRIPTO",
        df_1h_sniper=df_1h_sniper,
        sl_distance_penalty=0.0
    )


# KRİPTO STRATEJİ MOTORU
def analyze_strategies_crypto(symbol, df_1d, df_4h, btc_ok=False, btc_sniper_bias=0, metrics_collector=None, df_1h_sniper=None):
    signals = []

    if len(df_1d) < 50 or len(df_4h) < 20:
        return signals

    df_1d = df_1d.copy()
    df_4h = df_4h.copy()

    # Calculate indicators (O(1) operation per symbol)
    _ensure_crypto_indicators(df_1d, df_4h)

    last_1d = df_1d.iloc[-1]
    last_4h = df_4h.iloc[-1]
    current_price = float(last_4h['close'])

    _collect_metrics(symbol, current_price, last_1d, last_4h, metrics_collector)
    
    ctx = _build_context(
        symbol, df_1d, df_4h, current_price, last_1d, last_4h, 
        btc_ok, btc_sniper_bias, df_1h_sniper
    )

    # OCP compliant execution
    for strat in StrategyRegistry.get_long_strategies():
        signals.extend(strat.check(ctx))

    from data_sources import check_btc_not_pumping
    if check_btc_not_pumping():
        for strat in StrategyRegistry.get_short_strategies():
            signals.extend(strat.check(ctx))

    # Filter signals
    return _filter_crypto_signals(signals, symbol, current_price, last_4h, ctx)

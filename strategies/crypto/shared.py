"""
strategies/crypto/shared.py — Ortak Kripto Yardımcı Fonksiyonları ve İndikatör Hazırlığı
"""
import math
import pandas as pd
import pandas_ta as ta
import config

from config import (
    ATR_MULTIPLIER_CRYPTO, ATR_CAP_CRYPTO,
)
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores, build_dip_scores, build_breakout_scores,
    build_short_scores, build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH, SNIPER_CRYPTO_WEIGHTS,
    check_hard_blocks,
)
from indicators import (
    sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    detect_bullish_divergence, detect_bearish_divergence,
    detect_vwap_bounce, detect_obv_accumulation,
    detect_squeeze, calculate_cmf, sniper_calculate_ote_body,
    sniper_calculate_ote, calculate_anchored_vwap, get_trend_sma,
)
from data_sources import (
    get_crypto_1h_data, get_crypto_15m_data, get_funding_rate, fetch_crypto_oi_crash,
    get_btc_dominance_trend, check_btc_not_pumping, check_token_unlocks,
    get_btc_rsi_and_change,
)
from ..helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _is_meaningful_volume,
    _get_consecutive_sl, _has_absolute_hourly_volume, _get_darth_maul_ratio,
    _is_funding_safe_for_short,
)
from .ml_filter import evaluate_ml_fakeout


def apply_5x_sl_cap(sl: float, current_price: float, ctx: dict = None) -> float:
    """
    5x kaldıraç için SL mesafesini %2 ile sınırlar ve
    eğer mesafe %2'yi aşıyorsa dinamik/orantılı soft ceza uygular.
    """
    original_dist_pct = abs(current_price - sl) / current_price
    
    if original_dist_pct > 0.02:
        if ctx is not None:
            # Kripto oynaklığı yüksek olduğu için mesafe sınırı cezalandırılmaz, sadece SL cap'lenir.
            ctx["sl_distance_penalty"] = 0.0
            
        max_dist = current_price * 0.02
        if sl < current_price:
            return current_price - max_dist
        else:
            return current_price + max_dist
    else:
        if ctx is not None:
            ctx["sl_distance_penalty"] = 0.0
        return sl


def _ensure_crypto_1d_indicators(df_1d):
    if 'RSI_14' not in df_1d.columns:
        df_1d.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    if 'EMA_20' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_MID, append=True)
    if 'EMA_50' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_SLOW, append=True)
    if 'ADX_14' not in df_1d.columns:
        df_1d.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    if not any(c in df_1d.columns for c in ['BBP_20_2.0', 'BBU_20_2.0']):
        df_1d.ta.bbands(length=config.IND_BBANDS_LENGTH, std=config.IND_BBANDS_STD, append=True)
    if 'SMA_200' not in df_1d.columns and len(df_1d) >= 200:
        df_1d.ta.sma(length=200, append=True)


def _ensure_crypto_4h_indicators(df_4h):
    if 'RSI_14' not in df_4h.columns:
        df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    if 'EMA_20' not in df_4h.columns:
        df_4h.ta.ema(length=config.IND_EMA_MID, append=True)
    if 'EMA_50' not in df_4h.columns:
        df_4h.ta.ema(length=config.IND_EMA_SLOW, append=True)
    if 'ADX_14' not in df_4h.columns:
        df_4h.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    if 'ATRr_14' not in df_4h.columns and 'ATR_14' not in df_4h.columns:
        df_4h.ta.atr(length=config.IND_ATR_LENGTH, append=True)
    if 'CMF_20' not in df_4h.columns:
        df_4h.ta.cmf(length=20, append=True)
    if 'SMA_200' not in df_4h.columns:
        df_4h.ta.sma(length=200, append=True)
    if 'EMA_200' not in df_4h.columns:
        df_4h.ta.ema(length=200, append=True)
    if 'vol_sma_20' not in df_4h.columns:
        df_4h['vol_sma_20'] = ta.sma(df_4h['volume'], length=config.IND_VOL_SMA_LENGTH)
        
    if 'chop' not in df_4h.columns:
        import numpy as np
        atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
        if atr_col in df_4h.columns:
            atr_sum = df_4h[atr_col].rolling(14).sum()
            high_max = df_4h['high'].rolling(14).max()
            low_min = df_4h['low'].rolling(14).min()
            df_4h['chop'] = 100 * np.log10(atr_sum / (high_max - low_min).replace(0, 1e-9)) / np.log10(14)
        else:
            df_4h['chop'] = 50.0
        df_4h['chop'] = df_4h['chop'].fillna(50.0)


def _ensure_crypto_indicators(df_1d, df_4h):
    _ensure_crypto_1d_indicators(df_1d)
    _ensure_crypto_4h_indicators(df_4h)


def _is_crypto_signal_valid(sig, rel_vol_4h, ema_diff_pct, cmf_4h):
    score = sig.get('conviction_score', 0)
    direction = "LONG" if sig.get('signal') == "AL" else "SHORT"
    if score < 50:
        return False
    if rel_vol_4h < 0.7:
        return False
    if ema_diff_pct > 8.0:
        return False
    if direction == 'LONG' and cmf_4h < -0.10:
        return False
    if direction == 'SHORT' and cmf_4h > 0.10:
        return False
    return True


def _filter_crypto_signals(signals, symbol, current_price, last_4h, ctx):
    vol_sma = last_4h.get('vol_sma_20', 0)
    vol = last_4h.get('volume', 0)
    rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
    
    ema_20_val = last_4h.get('EMA_20', 0)
    ema_50_val = last_4h.get('EMA_50', 0)
    ema_diff_pct = 0.0
    if pd.notna(ema_20_val) and pd.notna(ema_50_val) and pd.notna(current_price) and current_price > 0:
        ema_diff_pct = (abs(ema_20_val - ema_50_val) / current_price) * 100
        
    cmf_4h = last_4h.get('CMF_20', 0)
    if pd.isna(cmf_4h):
        cmf_4h = 0.0

    filtered_signals = []
    for sig in signals:
        if not _is_crypto_signal_valid(sig, rel_vol_4h, ema_diff_pct, cmf_4h):
            continue
            
        ml_features = sig.get("ml_features")
        if ml_features:
            prob = evaluate_ml_fakeout(ml_features)
            sig["ml_fakeout_prob"] = prob
            if prob >= 0.70:
                print(f"[ML FILTER] {symbol} {sig.get('signal')} sinyali engellendi! (Fakeout Riski: %{prob*100:.1f})")
                continue
            else:
                sig["reason"] += f"\n🤖 ML Fakeout Riski: %{prob*100:.1f} (GÜVENLİ)"
                
        filtered_signals.append(sig)

    def _build_confluence(sig_list, direction_name, direction_signal):
        if len(sig_list) >= 3:
            confluence_details = {f"Signal_{i+1}": s["strategy"] for i, s in enumerate(sig_list)}
            base_sig = sig_list[0]
            # Basit default SL/TP hesaplaması
            sl_val = current_price * 0.95 if direction_signal == "AL" else current_price * 1.05
            tp_val = current_price * 1.05 if direction_signal == "AL" else current_price * 0.95
            
            filtered_signals.append({
                "raw_indicators": base_sig.get("raw_indicators", {}),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": f"SÜPER SİNYAL: CONFLUENCE ({direction_name})", "signal": direction_signal,
                "entry_price": current_price, 
                "sl": base_sig.get("sl", sl_val), 
                "tp": base_sig.get("tp", tp_val),
                "conviction_score": 95, "conviction_grade": CONVICTION_STRONG,
                "conviction_details": {"Confluence_Count": len(sig_list), **confluence_details}, 
                "position_size_pct": 5.0,
                "reason": f"Süper Sinyal: Aynı anda {len(sig_list)} farklı strateji {direction_signal} verdi!"
            })

    al_signals = [s for s in filtered_signals if s.get("signal") == "AL"]
    sat_signals = [s for s in filtered_signals if s.get("signal") == "SAT"]

    _build_confluence(al_signals, "LONG", "AL")
    _build_confluence(sat_signals, "SHORT", "SAT")

    return filtered_signals

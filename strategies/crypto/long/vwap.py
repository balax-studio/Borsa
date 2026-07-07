"""
strategies/crypto/long/vwap.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import detect_vwap_bounce, calculate_anchored_vwap
from data_sources import get_btc_rsi_and_change
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class VwapStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 6: VWAP KURUMSAL MIKNATISI")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        btc_ok = ctx["btc_ok"]

        vol_sma = last_4h.get('vol_sma_20', 0)
        vol = last_4h.get('volume', 0)
        rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
        if rel_vol_4h <= 1.2126:
            return signals

        btc_rsi, btc_change = get_btc_rsi_and_change()
        if btc_rsi <= 40 or btc_change >= 2.0:
            return signals

        if last_1d is None or pd.isna(last_1d.get('EMA_20')) or pd.isna(last_1d.get('EMA_50')):
            return signals
        if last_1d['EMA_20'] <= last_1d['EMA_50']:
            return signals
            
        atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
        if atr_col not in df_4h.columns:
            df_4h.ta.atr(length=14, append=True)
            atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
        if 'ATR_SMA_14' not in df_4h.columns and atr_col in df_4h.columns:
            df_4h['ATR_SMA_14'] = df_4h[atr_col].rolling(window=14).mean()

        current_atr = df_4h['ATRr_14'].iloc[-1]
        atr_sma = df_4h['ATR_SMA_14'].iloc[-1]
        
        if pd.notna(current_atr) and pd.notna(atr_sma) and atr_sma > 0:
            if (current_atr / atr_sma) > getattr(config, 'VWAP_MAX_ATR_RATIO', 2.0):
                return signals

        vwap_val = calculate_anchored_vwap(df_4h, anchor_type="weekly")
        if vwap_val is not None:
            bounce_ok, wick_low = detect_vwap_bounce(df_4h, vwap_val)
            if bounce_ok and wick_low is not None:
                sl = wick_low * config.CRYPTO_VWAP_SL_MULT
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                sl_dist = abs(current_price - sl)
                _tp_c6 = current_price + (sl_dist * 1.50)
                _rr_c6 = abs(_tp_c6 - current_price) / max(abs(current_price - sl), 1e-8)
                
                raw_vars = locals()
                
                _scores_c6 = build_trend_scores(
                    adx=None, adx_prev=None, price=current_price, ema_fast=vwap_val, ema_mid=None, ema_slow=None,
                    rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
                    volume=last_4h.get('volume', 0), vol_sma=None, dollar_vol=last_4h.get('volume', 0) * current_price,
                    rr=_rr_c6, has_engulfing=True, regime="BULL", macro_aligned=btc_ok,
                    consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO"
                )
                _conv_c6 = calculate_conviction(_scores_c6, ctx=ctx)
                if _conv_c6.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "KRIPTO",
                        "last_1d": ctx.get("last_1d"),
                        "strategy": self.name, "signal": "AL",
                        "entry_price": current_price, "sl": sl, "tp": _tp_c6,
                        "conviction_score": _conv_c6.total_score, "conviction_grade": _conv_c6.grade,
                        "conviction_details": _conv_c6.component_scores, "position_size_pct": _conv_c6.position_size_pct,
                        "reason": (
                            f"⚓ VWAP Bounce (Kurumsal Mıknatıs)!\n"
                            f"4S Anchored VWAP: {vwap_val:.4f}\n"
                            f"Pin Bar onayı: VWAP'a değip sıçradı.\n"
                            f"BTC > EMA20 (Piyasa izni var ✅)"
                        ) + _conv_c6.to_reason_suffix()
                    })
        return signals

"""
strategies/crypto/long/liquidation.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_dip_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import detect_bullish_divergence
from data_sources import fetch_crypto_oi_crash
from strategies.helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _is_meaningful_volume,
    _get_consecutive_sl, _get_darth_maul_ratio,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class LiquidationStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 1: LİKİDASYON AVI")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        btc_ok = ctx["btc_ok"]

        has_needed = (
            not pd.isna(last_4h.get('RSI_14')) and
            not pd.isna(last_4h.get('EMA_20')) and
            not pd.isna(last_4h.get('vol_sma_20'))
        )
        if not has_needed:
            return signals

        ema_50_1d = last_1d.get(f'EMA_{config.IND_EMA_SLOW}') if last_1d is not None else None
        trend_aligned = not config.DIP_RSI_1D_EMA50_ALIGN_ENABLED or (
            ema_50_1d is not None and not pd.isna(ema_50_1d) and current_price > ema_50_1d
        )
        if not trend_aligned:
            return signals

        div_found, _, _, _, _ = detect_bullish_divergence(df_4h)
        if not div_found:
            return signals

        guarded_vol_sma = _apply_volume_sma_guard(df_4h, last_4h['vol_sma_20'])
        volume_spike_ok = not config.DIP_VOLUME_SPIKE_REQUIRED or (
            last_4h['volume'] >= guarded_vol_sma * config.DIP_VOLUME_SPIKE_MULT
        )
        if not volume_spike_ok:
            return signals

        if not _is_meaningful_volume(last_4h['volume'], guarded_vol_sma, current_price, "KRIPTO"):
            return signals

        # Golden Filter (Iteration 1)
        if last_4h.get('ADX_14', 100) >= 29.9580:
            return signals

        # Golden Filter (Iteration 2)
        if last_4h.get('ADX_14', 0) <= 22.0293:
            return signals

        if current_price > last_4h['open']:
            oi_crash = fetch_crypto_oi_crash(symbol)
            
            lowest_wick = last_4h['low']
            atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
            if atr_val is None or pd.isna(atr_val):
                atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
            dynamic_mult = ctx.get("dynamic_atr_mult", config.ATR_MULTIPLIER_CRYPTO)
            raw_atr_sl = dynamic_mult * atr_val
            
            sl = lowest_wick - raw_atr_sl
            sl = apply_5x_sl_cap(sl, current_price, ctx)
            sl_dist = abs(current_price - sl)
            tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
            _rr_c1 = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
            _prev_4h = df_4h.iloc[-2] if len(df_4h) >= 2 else last_4h
            dm_ratio = _get_darth_maul_ratio(last_4h)
            
            raw_vars = locals()
            
            _scores_c1 = build_dip_scores(
                rsi_daily=last_4h.get('RSI_14', 50), rsi_hourly=last_4h.get('RSI_14', 50),
                rsi_prev=_prev_4h.get('RSI_14', 50),
                price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'),
                volume=last_4h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_4h['volume'] * current_price,
                rr=_rr_c1, has_engulfing=False, regime="BULL",
                macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
                dg_is_darth_maul=dm_ratio,
                oi_crash=oi_crash
            )
            _conv_c1 = calculate_conviction(_scores_c1, ctx=ctx)
            if _conv_c1.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                reason_str = "4S Pozitif Uyumsuzluk + Hacim Zirvesi"
                if oi_crash:
                    reason_str += " + OI Çöküşü (Balina Alımı)"
                reason_str += "." + _conv_c1.to_reason_suffix()

                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "KRIPTO",
                    "last_1d": ctx.get("last_1d"),
                    "strategy": self.name, "signal": "AL",
                    "entry_price": current_price, "sl": sl, "tp": tp,
                    "conviction_score": _conv_c1.total_score, "conviction_grade": _conv_c1.grade,
                    "conviction_details": _conv_c1.component_scores, "position_size_pct": _conv_c1.position_size_pct,
                    "reason": reason_str
                })
        return signals

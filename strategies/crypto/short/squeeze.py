"""
strategies/crypto/short/squeeze.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_breakout_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import detect_squeeze
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_short
class VolatilitySqueezeShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 5: VOLATİLİTE SIKIŞMASI (SQUEEZE)")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        btc_ok = ctx["btc_ok"]

        sq_fired, sq_dir, sq_candle = detect_squeeze(df_4h)
        if sq_fired and sq_dir == "down":
            trend_up = (not pd.isna(last_1d.get(f'EMA_{config.IND_EMA_MID}')) and not pd.isna(last_1d.get(f'EMA_{config.IND_EMA_SLOW}')) and
                        last_1d[f'EMA_{config.IND_EMA_MID}'] > last_1d[f'EMA_{config.IND_EMA_SLOW}'])
            if not trend_up:
                sq_mid = (sq_candle['high'] + sq_candle['low']) / 2
                ema20_4h = last_4h.get('EMA_20', current_price)

                sl = max(sq_mid, ema20_4h) if not pd.isna(ema20_4h) else sq_mid
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                sl_dist = abs(sl - current_price)
                tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
                _rr_c5 = abs(current_price - tp) / max(abs(sl - current_price), 1e-8)
                
                raw_vars = locals()
                
                _scores_c5 = build_breakout_scores(
                    bb_width=None, price=current_price, ema_fast=ema20_4h, ema_mid=None, ema_slow=None,
                    volume=last_4h.get('volume', 0),
                    vol_sma=last_4h.get('vol_sma_20'),
                    dollar_vol=last_4h.get('volume', 0) * current_price,
                    rr=_rr_c5, regime="BEAR", macro_aligned=btc_ok,
                    consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
                    rsi=last_4h.get('RSI_14'),
                    rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else last_4h.get('RSI_14'),
                    is_long=False,
                    strategy_type="TREND_BREAKOUT",
                    rsi_1h=last_4h.get('RSI_14'),
                    sma200_1d=last_1d.get('SMA_200') if last_1d is not None else None
                )
                _conv_c5 = calculate_conviction(_scores_c5, ctx=ctx)
                if _conv_c5.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "KRIPTO",
                        "last_1d": ctx.get("last_1d"),
                        "strategy": self.name, "signal": "SAT",
                        "entry_price": current_price, "sl": sl, "tp": tp,
                        "conviction_score": _conv_c5.total_score, "conviction_grade": _conv_c5.grade,
                        "conviction_details": _conv_c5.component_scores, "position_size_pct": _conv_c5.position_size_pct,
                        "reason": (
                            f"🗜️ Squeeze Patlaması (DOWN)!\n"
                            f"4S BB(20,2) Keltner(20,1.5) içinden kırıldı.\n"
                            f"1G Trend Aşağı ✅ ile uyumlu.\n"
                            f"Hacimli kırmızı mum onayı."
                        ) + _conv_c5.to_reason_suffix()
                    })
        return signals

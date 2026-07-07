"""
strategies/crypto/long/obv.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_breakout_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import detect_obv_accumulation, calculate_cmf
from data_sources import get_btc_dominance_trend
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class ObvStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 7: SESSİZ BİRİKİM RADARI (OBV)")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        current_price = ctx["current_price"]
        df_1d = ctx["df_1d"]
        df_4h = ctx.get("df_4h")

        if df_4h is not None and not df_4h.empty:
            current_hour = df_4h.index[-1].hour
            if current_hour == 20:
                return signals

        if df_4h is not None and not df_4h.empty:
            bbu_col = [c for c in df_4h.columns if 'BBU' in c]
            if not bbu_col:
                df_4h = df_4h.copy()
                df_4h.ta.bbands(length=20, std=2.0, append=True)
                bbu_col = [c for c in df_4h.columns if 'BBU' in c]
            bbl_col = [c for c in df_4h.columns if 'BBL' in c]
            bbm_col = [c for c in df_4h.columns if 'BBM' in c]
            if bbu_col and bbl_col and bbm_col:
                bbu = df_4h[bbu_col[0]].iloc[-1]
                bbl = df_4h[bbl_col[0]].iloc[-1]
                bbm = df_4h[bbm_col[0]].iloc[-1]
                if bbm != 0:
                    bbw = (bbu - bbl) / bbm
                    if bbw >= 0.1964:
                        return signals

        obv_ok, obv_box_high, obv_box_low = detect_obv_accumulation(df_1d, max_change_pct=config.CRYPTO_OBV_ACC_MAX_CHANGE_PCT)
        if obv_ok and obv_box_high is not None:
            btcdom_trend = get_btc_dominance_trend()
            if btcdom_trend != "UP":
                last_4h = df_4h.iloc[-1] if df_4h is not None and not df_4h.empty else None
                atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14')) if last_4h is not None else None
                if atr_val is None or pd.isna(atr_val):
                    atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
                sl = current_price - (atr_val * 0.75)
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                cmf_val = calculate_cmf(df_1d)
                cmf_label = f"CMF: {cmf_val:.3f} ✅" if cmf_val is not None else "CMF: N/A"
                sl_dist = abs(current_price - sl)
                _tp_c7 = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
                _rr_c7 = abs(_tp_c7 - current_price) / max(abs(current_price - sl), 1e-8)
                
                raw_vars = locals()
                
                vol_sma_col = 'vol_sma_20'
                if vol_sma_col not in df_1d.columns:
                    df_1d = df_1d.copy()
                    df_1d[vol_sma_col] = df_1d['volume'].rolling(window=20).mean()
                daily_vol_sma = df_1d[vol_sma_col].iloc[-1] if not df_1d.empty else None

                _scores_c7 = build_breakout_scores(
                    bb_width=None, price=current_price,
                    ema_fast=last_1d.get(f'EMA_{config.IND_EMA_MID}'), ema_mid=last_1d.get(f'EMA_{config.IND_EMA_SLOW}'), ema_slow=None,
                    volume=last_1d.get('volume', 0), vol_sma=daily_vol_sma, dollar_vol=last_1d.get('volume', 0) * current_price,
                    rr=_rr_c7, regime="BULL",
                    macro_aligned=(btcdom_trend != 'UP'), consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
                    rsi=last_1d.get('RSI_14'),
                    rsi_prev=df_1d['RSI_14'].iloc[-2] if (len(df_1d) >= 2 and 'RSI_14' in df_1d.columns) else last_1d.get('RSI_14'),
                    rsi_1h=None,
                    is_long=True,
                    strategy_type="TREND_BREAKOUT",
                    sma200_1d=last_1d.get('SMA_200') if last_1d is not None else None,
                    cmf=cmf_val if cmf_val is not None else 0.0,
                    has_engulfing=False
                )
                _conv_c7 = calculate_conviction(_scores_c7, ctx=ctx)
                if _conv_c7.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "KRIPTO",
                        "last_1d": ctx.get("last_1d"),
                        "strategy": self.name, "signal": "AL",
                        "entry_price": current_price, "sl": sl, "tp": _tp_c7,
                        "conviction_score": _conv_c7.total_score, "conviction_grade": _conv_c7.grade,
                        "conviction_details": _conv_c7.component_scores, "position_size_pct": _conv_c7.position_size_pct,
                        "reason": (
                            f"🕵️ Sessiz Birikim + CMF Onaylı!\n"
                            f"1G 20 gün yatay kutu: {obv_box_low:.4f} - {obv_box_high:.4f}\n"
                            f"OBV yeni tepeler + {cmf_label}\n"
                            f"BTC Dominans '{btcdom_trend}' (Altcoin dostu ✅)"
                        ) + _conv_c7.to_reason_suffix()
                    })
        return signals

"""
strategies/crypto/long/mega_trend.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import get_trend_sma
from data_sources import get_btc_dominance_trend
from strategies.helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _is_meaningful_volume,
    _get_consecutive_sl, _get_darth_maul_ratio,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class MegaTrendStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 2: MEGA TREND TAKİBİ")

    def _check_mega_trend_1d_squeeze(self, last_1d, df_1d):
        if not config.TREND_BB_SQUEEZE_BLOCKED:
            return True
        bb_upper = [c for c in df_1d.columns if 'BBU' in c]
        bb_lower = [c for c in df_1d.columns if 'BBL' in c]
        bb_mid = [c for c in df_1d.columns if 'BBM' in c]
        if not (bb_upper and bb_lower and bb_mid):
            return True
        bbu = last_1d[bb_upper[0]]
        bbl = last_1d[bb_lower[0]]
        bbm = last_1d[bb_mid[0]]
        if bbm == 0:
            return True
        return (bbu - bbl) / bbm >= config.CRYPTO_SQUEEZE_WIDTH_LIMIT

    def _check_mega_trend_1d_trend(self, last_1d):
        ema_mid_val = last_1d.get(f'EMA_{config.IND_EMA_MID}')
        ema_slow_val = last_1d.get(f'EMA_{config.IND_EMA_SLOW}')
        if ema_mid_val is None or ema_slow_val is None or pd.isna(ema_mid_val) or pd.isna(ema_slow_val):
            return False
        return ema_mid_val > ema_slow_val and last_1d['close'] > ema_mid_val

    def _check_mega_trend_4h_indicators(self, last_4h, current_price):
        atr_col = 'ATRr_14' if 'ATRr_14' in last_4h.index else 'ATR_14'
        if pd.isna(last_4h.get('ADX_14')) or pd.isna(last_4h.get('EMA_20')) or pd.isna(last_4h.get(atr_col)):
            return False
        ema_mid_4h = last_4h.get(f'EMA_{config.IND_EMA_MID}')
        if ema_mid_4h is None or pd.isna(ema_mid_4h):
            return False
        is_pullback = (
            last_4h['low'] <= ema_mid_4h and
            current_price > ema_mid_4h and
            current_price > last_4h['open']
        )
        return is_pullback and not pd.isna(last_4h.get('vol_sma_20'))

    def _is_mega_trend_valid(self, last_1d, last_4h, df_1d, df_4h, current_price):
        if pd.isna(last_1d.get('EMA_20')) or pd.isna(last_1d.get('EMA_50')):
            return False
        if not self._check_mega_trend_1d_squeeze(last_1d, df_1d):
            return False
        if not self._check_mega_trend_1d_trend(last_1d):
            return False
        return self._check_mega_trend_4h_indicators(last_4h, current_price)

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_1d = ctx["df_1d"]
        df_4h = ctx["df_4h"]
        btc_ok = ctx["btc_ok"]

        if not self._is_mega_trend_valid(last_1d, last_4h, df_1d, df_4h, current_price):
            return signals

        guarded_vol_sma = _apply_volume_sma_guard(df_4h, last_4h['vol_sma_20'])
        if last_4h['volume'] < guarded_vol_sma * config.CRYPTO_TREND_VOLUME_SMA_MULT:
            return signals
        if not _is_meaningful_volume(last_4h['volume'], guarded_vol_sma, current_price, "KRIPTO"):
            return signals

        vol_sma = last_4h.get('vol_sma_20', 0)
        vol = last_4h.get('volume', 0)
        rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
        if rel_vol_4h >= 6.3000:
            return signals

        btcdom_trend = get_btc_dominance_trend()

        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if atr_val is None or pd.isna(atr_val):
            atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
            
        sl = current_price - (atr_val * 1.25)
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = abs(current_price - sl)
        _tp_c2 = current_price + (sl_dist * 2.50)
        _rr_c2 = abs(_tp_c2 - current_price) / max(abs(current_price - sl), 1e-8)
        _adx_prev_c2 = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
        _prev_4h_c2 = df_4h.iloc[-2] if len(df_4h) >= 2 else last_4h
        dm_ratio = _get_darth_maul_ratio(last_4h)
        
        raw_vars = locals()
        
        _scores_c2 = build_trend_scores(
            adx=last_4h['ADX_14'], adx_prev=_adx_prev_c2,
            price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
            rsi=last_4h.get('RSI_14'), rsi_prev=_prev_4h_c2.get('RSI_14'),
            volume=last_4h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_4h['volume'] * current_price,
            rr=_rr_c2, has_engulfing=False, regime="BULL",
            macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
            dg_is_darth_maul=dm_ratio
        )

        btcdom_warning = ""
        if btcdom_trend == "UP":
            _scores_c2["conflict_penalty"] -= 15.0
            btcdom_warning = " (Riskli: BTC Dominans UP)"

        _conv_c2 = calculate_conviction(_scores_c2, ctx=ctx)
        if _conv_c2.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": _tp_c2,
                "conviction_score": _conv_c2.total_score, "conviction_grade": _conv_c2.grade,
                "conviction_details": _conv_c2.component_scores, "position_size_pct": _conv_c2.position_size_pct,
                "reason": f"1G EMA20>50 Trendi. BTC Dominans '{btcdom_trend}' yönünde{btcdom_warning}. Hacim onaylı. ATR Stop aktif." + _conv_c2.to_reason_suffix()
            })
        return signals

"""
strategies/crypto/long/breakout.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_breakout_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from data_sources import get_funding_rate, check_token_unlocks
from strategies.helpers import (
    _extract_raw_indicators, _has_absolute_hourly_volume,
    _get_consecutive_sl, _get_darth_maul_ratio,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class BreakoutStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 3: SAHTE KIRILIM FİLTRESİ (RETEST)")

    def _is_breakout_setup(self, symbol, last_4h, current_price, df_1d, df_4h):
        bb_upper_col = [c for c in df_1d.columns if 'BBU' in c]
        bb_lower_col = [c for c in df_1d.columns if 'BBL' in c]
        bb_mid_col = [c for c in df_1d.columns if 'BBM' in c]

        if not bb_upper_col or not bb_lower_col or not bb_mid_col:
            return False, 0.0

        df_1d['bb_width'] = (df_1d[bb_upper_col[0]] - df_1d[bb_lower_col[0]]) / df_1d[bb_mid_col[0]]
        min_width_30d = df_1d['bb_width'].tail(config.CRYPTO_BREAKOUT_LOOKBACK).min()
        last_width = df_1d['bb_width'].iloc[-1]

        if last_width > min_width_30d * config.CRYPTO_BREAKOUT_WIDTH_MULT:
            return False, 0.0

        vol_sma = last_4h.get('vol_sma_20')
        if pd.isna(vol_sma) or last_4h['volume'] <= config.CRYPTO_BREAKOUT_VOLUME_MULT * vol_sma:
            return False, 0.0

        return True, last_width

    def _is_breakout_retest_valid(self, symbol, last_4h, current_price, df_4h):
        local_high = df_4h['high'].tail(config.CRYPTO_BREAKOUT_RETEST_LOOKBACK).max()
        if config.BREAKOUT_RETEST_REQUIRED:
            if not (local_high <= current_price <= local_high * (1.0 + (config.BREAKOUT_RETEST_TOLERANCE_PCT / 100.0))):
                return False, 0.0
        
        if current_price <= local_high:
            return False, 0.0
        if last_4h['low'] > local_high * config.CRYPTO_BREAKOUT_RETEST_SL_MULT:
            return False, 0.0
        if current_price <= last_4h['open']:
            return False, 0.0

        if check_token_unlocks(symbol):
            return False, 0.0

        return True, local_high

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_1d = ctx["last_1d"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_1d = ctx["df_1d"]
        df_4h = ctx["df_4h"]
        btc_ok = ctx["btc_ok"]

        if ctx.get("is_choppy", False):
            return signals

        ok_setup, last_width = self._is_breakout_setup(symbol, last_4h, current_price, df_1d, df_4h)
        if not ok_setup:
            return signals

        ok_retest, local_high = self._is_breakout_retest_valid(symbol, last_4h, current_price, df_4h)
        if not ok_retest:
            return signals

        funding_rate = get_funding_rate(symbol)
        if funding_rate is not None and funding_rate > config.BREAKOUT_CRYPTO_FUNDING_RATE_MAX:
            return signals

        if not _has_absolute_hourly_volume(last_4h['volume'], current_price, "KRIPTO"):
            return signals

        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if atr_val is None or pd.isna(atr_val):
            atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
        dynamic_mult = ctx.get("dynamic_atr_mult", config.ATR_MULTIPLIER_CRYPTO)
        raw_atr_sl = dynamic_mult * atr_val
        sl_dist = min(max(raw_atr_sl, current_price * config.CRYPTO_BREAKOUT_MIN_SL), current_price * config.CRYPTO_BREAKOUT_MAX_SL)
        sl = current_price - sl_dist
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        _tp_c3 = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
        _rr_c3 = abs(_tp_c3 - current_price) / max(abs(current_price - sl), 1e-8)
        dm_ratio = _get_darth_maul_ratio(last_4h)
        
        raw_vars = locals()
        
        _scores_c3 = build_breakout_scores(
            bb_width=last_width, price=current_price,
            ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
            volume=last_4h['volume'], vol_sma=last_4h['vol_sma_20'], dollar_vol=last_4h['volume'] * current_price,
            rr=_rr_c3, regime="BULL",
            macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
            dg_is_darth_maul=dm_ratio, funding_rate=funding_rate,
            rsi=last_4h.get('RSI_14'),
            rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else last_4h.get('RSI_14'),
            has_engulfing=False
        )
        _conv_c3 = calculate_conviction(_scores_c3, ctx=ctx)
        if _conv_c3.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": _tp_c3,
                "conviction_score": _conv_c3.total_score, "conviction_grade": _conv_c3.grade,
                "conviction_details": _conv_c3.component_scores, "position_size_pct": _conv_c3.position_size_pct,
                "reason": f"1G Daralma, Retest sekmesi. Fonlama: %{funding_rate:.4f}. Hacim: Onaylı." + _conv_c3.to_reason_suffix()
            })
        return signals

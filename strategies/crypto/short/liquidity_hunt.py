"""
strategies/crypto/short/liquidity_hunt.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_short_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators.smc import detect_supply_zones, is_price_in_supply_zone
from indicators import sniper_find_swing_points, sniper_detect_sweep, sniper_detect_msb, sniper_calculate_ote
from data_sources import get_funding_rate
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl, _is_funding_safe_for_short,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_short
class LiquidityHuntShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 1: LİKİDİTE AVI (SFP+CHoCH)")

    def check(self, ctx: dict) -> list:
        signals = []

        last_1d = ctx.get('last_1d')
        if last_1d is not None:
            ema_20_1d = last_1d.get('EMA_20')
            ema_50_1d = last_1d.get('EMA_50')
            rsi_1d = last_1d.get('RSI_14')
            if pd.notna(ema_20_1d) and pd.notna(ema_50_1d) and pd.notna(rsi_1d):
                if ema_20_1d > ema_50_1d and rsi_1d > 60:
                    return signals

        symbol = ctx['symbol']
        last_4h = ctx['last_4h']
        current_price = ctx['current_price']
        df_4h = ctx['df_4h']
        
        supply_zones = detect_supply_zones(df_4h)
        in_supply = is_price_in_supply_zone(current_price, supply_zones)
        swing_highs = sniper_find_swing_points(df_4h, point_type='high')
        sweep_ok, sweep_high = sniper_detect_sweep(df_4h, swing_highs, point_type='high')
        if not sweep_ok:
            return signals

        swing_lows = sniper_find_swing_points(df_4h, point_type='low')
        msb_ok, msb_low, _ = sniper_detect_msb(df_4h, swing_lows, point_type='low')
        if not msb_ok:
            return signals

        ote_top, ote_bottom = sniper_calculate_ote(msb_low, sweep_high)
        if not (ote_bottom <= current_price <= ote_top):
            return signals

        funding_rate = get_funding_rate(symbol)
        if not _is_funding_safe_for_short(funding_rate):
            return signals

        vol_sma = last_4h.get('vol_sma_20', 0)
        if vol_sma > 0 and last_4h.get('volume', 0) < (vol_sma * 1.5):
            return signals

        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if pd.isna(atr_val): atr_val = current_price * 0.02

        sl = sweep_high + (atr_val * 1.00)
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = max(sl - current_price, 1e-8)
        tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
        _rr = abs(current_price - tp) / sl_dist
        if _rr < config.CRYPTO_SHORT_MIN_RR:
            return signals

        _adx_prev = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
        raw_vars = locals()
        _scores = build_short_scores(
            adx=last_4h.get('ADX_14'), adx_prev=_adx_prev,
            price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
            rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
            volume=last_4h.get('volume', 0), vol_sma=vol_sma, dollar_vol=last_4h.get('volume', 0) * current_price,
            rr=_rr, has_engulfing=False, regime='BEAR', macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO',
            funding_rate=funding_rate, strategy_type='MEAN_REVERSION'
        )
        if not in_supply:
            _scores["conflict_penalty"] -= 15.0
            
        if last_4h.get('Vortex_Diff', 0) >= -0.1531:
            return signals

        if last_4h.get('ADX_14', 0) <= 15.0902:
            return signals

        if last_4h.get('RSI_14', 0) <= 36.6882:
            return signals
            
        _conv = calculate_conviction(_scores, ctx=ctx)
        if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            funding_str = f" | Fonlama: %{funding_rate:.4f}" if funding_rate is not None else ""
            signals.append({
                'raw_indicators': _extract_raw_indicators(raw_vars),
                'ticker': symbol, 'market': 'KRIPTO', 'strategy': self.name, 'signal': 'SAT',
                'entry_price': current_price, 'sl': sl, 'tp': tp,
                'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
                'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
                'reason': f'SFP (Likidite Avı) + CHoCH Onaylı. 1:{config.CRYPTO_SHORT_MIN_RR} R:R. OTE Bölgesi ({ote_bottom:.4f}-{ote_top:.4f}){funding_str}.' + _conv.to_reason_suffix()
            })
        return signals

"""
strategies/crypto/short/bear_flag.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_short_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators.smc import detect_supply_zones, is_price_in_supply_zone
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_short
class BearFlagShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 5: AYI BAYRAĞI & EMA DİZİLİMİ")

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
        ema_20 = last_4h.get('EMA_20')
        ema_50 = last_4h.get('EMA_50')
        ema_200 = last_4h.get('EMA_200') or last_4h.get('SMA_200')
        
        if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200):
            return signals
        if not (ema_20 < ema_50 < ema_200):
            return signals
        
        if current_price > ema_50:
            return signals
        
        if last_4h['high'] < ema_20 * 0.98:
            return signals
        
        above_ema20 = current_price > ema_20
        vol_sma = last_4h.get('vol_sma_20', 0)
        high_volume = (vol_sma > 0 and last_4h.get('volume', 0) > (vol_sma * config.CRYPTO_SHORT5_FLAG_VOL_MULT))

        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if pd.isna(atr_val): atr_val = current_price * 0.02

        sl = ema_50 + (atr_val * config.ATR_MULTIPLIER_CRYPTO)
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
            price=current_price, ema_fast=ema_20, ema_mid=ema_50, ema_slow=ema_200,
            rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
            volume=last_4h.get('volume', 0), vol_sma=vol_sma, dollar_vol=last_4h.get('volume', 0) * current_price,
            rr=_rr, has_engulfing=False, regime='BEAR', macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO',
            strategy_type='PULLBACK'
        )
        if not in_supply:
            _scores["conflict_penalty"] -= 15.0
        if above_ema20:
            _scores["conflict_penalty"] -= 10.0
        if high_volume:
            _scores["conflict_penalty"] -= 10.0
        _conv = calculate_conviction(_scores, ctx=ctx)
        if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                'raw_indicators': _extract_raw_indicators(raw_vars),
                'ticker': symbol, 'market': 'KRIPTO', 'strategy': self.name, 'signal': 'SAT',
                'entry_price': current_price, 'sl': sl, 'tp': tp,
                'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
                'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
                'reason': f'EMA Ayı Dizilimi + EMA20 Retest Zayıf Hacim (Ayı Bayrağı). 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
            })
        return signals

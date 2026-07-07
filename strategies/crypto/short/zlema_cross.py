"""
strategies/crypto/short/zlema_cross.py
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
class ZlemaCrossShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 6: ZLEMA ÇAPRAZ KIRILIM")

    def _calculate_zlema(self, series, length):
        lag = int((length - 1) / 2)
        de_lagged = series + (series - series.shift(lag))
        return de_lagged.ewm(span=length, adjust=False).mean()

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx['symbol']
        last_4h = ctx['last_4h']
        current_price = ctx['current_price']
        df_4h = ctx['df_4h']
        
        ema_200 = last_4h.get('EMA_200') or last_4h.get('SMA_200')
        if pd.isna(ema_200) or current_price >= ema_200:
            return signals
            
        adx = last_4h.get('ADX_14')
        if pd.isna(adx) or adx <= 30:
            return signals
            
        close_series = df_4h['close']
        zlema_30 = self._calculate_zlema(close_series, 30)
        zlema_40 = self._calculate_zlema(close_series, 40)
        
        if len(zlema_30) < 2 or len(zlema_40) < 2:
            return signals
            
        zl30_prev = zlema_30.iloc[-2]
        zl30_curr = zlema_30.iloc[-1]
        zl40_prev = zlema_40.iloc[-2]
        zl40_curr = zlema_40.iloc[-1]
        
        if pd.isna(zl30_curr) or pd.isna(zl40_curr) or pd.isna(zl30_prev) or pd.isna(zl40_prev):
            return signals
            
        if not (zl30_prev >= zl40_prev and zl30_curr < zl40_curr):
            return signals
            
        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if pd.isna(atr_val): 
            atr_val = current_price * 0.02
            
        ema_50 = last_4h.get('EMA_50')
        if pd.isna(ema_50):
            ema_50 = current_price
            
        sl = ema_50 + (atr_val * config.ATR_MULTIPLIER_CRYPTO)
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = max(sl - current_price, 1e-8)
        tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
        
        _rr = abs(current_price - tp) / sl_dist
        if _rr < config.CRYPTO_SHORT_MIN_RR:
            return signals
            
        _adx_prev = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
        vol_sma = last_4h.get('vol_sma_20', 0)
        
        supply_zones = detect_supply_zones(df_4h)
        in_supply = is_price_in_supply_zone(current_price, supply_zones)
        
        raw_vars = locals()
        _scores = build_short_scores(
            adx=adx, adx_prev=_adx_prev,
            price=current_price, ema_fast=zl30_curr, ema_mid=zl40_curr, ema_slow=ema_200,
            rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
            volume=last_4h.get('volume', 0), vol_sma=vol_sma, dollar_vol=last_4h.get('volume', 0) * current_price,
            rr=_rr, has_engulfing=False, regime='BEAR', macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO',
            strategy_type='TREND_BREAKOUT'
        )
        
        if not in_supply:
            _scores["conflict_penalty"] -= 15.0
            
        _conv = calculate_conviction(_scores, ctx=ctx)
        if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                'raw_indicators': _extract_raw_indicators(raw_vars),
                'ticker': symbol, 'market': 'KRIPTO', 'strategy': self.name, 'signal': 'SAT',
                'entry_price': current_price, 'sl': sl, 'tp': tp,
                'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
                'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
                'reason': f'ZLEMA(30/40) Aşağı Kesişim + Bearish Trend (Price < EMA200). 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
            })
            
        return signals

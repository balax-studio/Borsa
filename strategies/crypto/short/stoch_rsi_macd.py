"""
strategies/crypto/short/stoch_rsi_macd.py
"""
import pandas as pd
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
import config

@StrategyRegistry.register_short
class StochRsiMacdShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 7: STOCH RSI & MACD UYUMSUZLUK")

    def _calculate_stoch_rsi(self, series, rsi_len=14, stoch_len=14, k_len=5, d_len=3):
        delta = series.diff()
        up = delta.clip(lower=0)
        down = -delta.clip(upper=0)
        ema_up = up.ewm(com=rsi_len - 1, adjust=False).mean()
        ema_down = down.ewm(com=rsi_len - 1, adjust=False).mean()
        rs = ema_up / ema_down.replace(0, 1e-9)
        rsi = 100 - (100 / (1 + rs))
        rsi_min = rsi.rolling(window=stoch_len).min()
        rsi_max = rsi.rolling(window=stoch_len).max()
        stoch_rsi = (rsi - rsi_min) / (rsi_max - rsi_min).replace(0, 1e-9)
        fastk = stoch_rsi.rolling(window=k_len).mean() * 100
        fastd = fastk.rolling(window=d_len).mean()
        return fastk, fastd

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
        if pd.isna(adx) or adx <= 25:
            return signals
            
        close_series = df_4h['close']
        fastk, fastd = self._calculate_stoch_rsi(close_series, 14, 14, 5, 3)
        
        if len(fastk) < 2 or len(fastd) < 2:
            return signals
            
        fk_prev, fk_curr = fastk.iloc[-2], fastk.iloc[-1]
        fd_prev, fd_curr = fastd.iloc[-2], fastd.iloc[-1]
        
        if pd.isna(fk_curr) or pd.isna(fd_curr) or pd.isna(fk_prev) or pd.isna(fd_prev):
            return signals
            
        stoch_cross = (fk_prev >= fd_prev and fk_curr < fd_curr) and (fk_prev > 70 or fd_prev > 70)
        if not stoch_cross:
            return signals
            
        ema_12 = close_series.ewm(span=12, adjust=False).mean()
        ema_26 = close_series.ewm(span=26, adjust=False).mean()
        macd_series = ema_12 - ema_26
        signal_series = macd_series.ewm(span=9, adjust=False).mean()
        hist_series = macd_series - signal_series
        
        macd_curr = macd_series.iloc[-1]
        sig_curr = signal_series.iloc[-1]
        hist_curr = hist_series.iloc[-1]
        
        macd_bearish = (macd_curr < sig_curr) or (hist_curr < 0)
        if not macd_bearish:
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
            price=current_price, ema_fast=macd_curr, ema_mid=sig_curr, ema_slow=ema_200,
            rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
            volume=last_4h.get('volume', 0), vol_sma=vol_sma, dollar_vol=last_4h.get('volume', 0) * current_price,
            rr=_rr, has_engulfing=False, regime='BEAR', macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO',
            strategy_type='MEAN_REVERSION'
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
                'reason': f'StochRSI Aşağı Kesişim (Aşırı Alım) + Bearish MACD. 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
            })
            
        return signals

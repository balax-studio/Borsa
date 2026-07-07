"""
strategies/crypto/long/smc.py
"""
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores, build_dip_scores, build_breakout_scores, build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)
from indicators import (
    sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    detect_bullish_divergence, sniper_calculate_ote,
)
from data_sources import (
    get_funding_rate, get_btc_rsi_and_change,
    get_usdt_dominance_trend, check_token_unlocks,
    fetch_crypto_oi_surge,
)
from strategies.helpers import (
    _extract_raw_indicators, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class SmcLongStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG SMC BİLEŞİK STRATEJİLERİ")

    def _check_crypto_long_sfp_choch(self, ctx):
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        last_4h = ctx["last_4h"]
        
        swing_lows = sniper_find_swing_points(df_4h, point_type="low")
        sweep_ok, sweep_low = sniper_detect_sweep(df_4h, swing_lows, point_type="low")
        swing_highs = sniper_find_swing_points(df_4h, point_type="high")
        choch_ok, msb_price, _ = sniper_detect_msb(df_4h, swing_highs, point_type="high")
        
        if sweep_ok and choch_ok:
            has_fvg, _, _ = sniper_detect_fvg(df_4h, df_4h['high'].iloc[-1], df_4h['low'].iloc[-1], direction="bullish")
            ote_top, ote_bot = sniper_calculate_ote(sweep_low, msb_price)
            in_ote = False
            if ote_top and ote_bot and ote_bot <= current_price <= ote_top:
                in_ote = True
            
            if has_fvg or in_ote:
                if last_4h.get('CMF', 1) >= 0.1768:
                    return signals
                if last_4h.get('ADX_14', 100) >= 20.4955:
                    return signals
                if last_4h.get('Relative_Volume', 1) >= 6.3074:
                    return signals

                btc_rsi, btc_change = get_btc_rsi_and_change()
                if btc_rsi <= 45 or btc_change <= -2.0:
                    return signals
                    
                atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
                if pd.isna(atr_val): atr_val = current_price * 0.02
                sl = current_price - (atr_val * 2.25)
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                
                _tp = current_price + (current_price - sl) * 2.50
                _rr = abs(_tp - current_price) / max(abs(current_price - sl), 1e-8)
                
                raw_vars = locals()
                _scores = build_sniper_scores(
                    price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
                    rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else 50,
                    volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20', 0), dollar_vol=last_4h.get('volume', 0) * current_price,
                    rr=_rr, regime="BULL", macro_aligned=ctx["btc_ok"], consecutive_sl=0,
                    bbw=0, kcw=0, pb=0, fvg_present=has_fvg, sfp_present=True,
                    market="KRIPTO", is_long=True
                )
                _conv = calculate_conviction(_scores, ctx=ctx)
                if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "KRIPTO",
                        "last_1d": ctx.get("last_1d"),
                        "strategy": "KRİPTO LONG 1: SFP+CHoCH (SMC)", "signal": "AL",
                        "entry_price": current_price, "sl": sl, "tp": _tp,
                        "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                        "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                        "reason": f"🟢 Likidite Avı (SFP) + CHoCH tespit edildi. FVG/OTE bölgesi onaylı.\nSL: {sl:.2f}\n" + _conv.to_reason_suffix()
                    })
        return signals

    def _check_crypto_long_short_squeeze(self, ctx):
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        last_4h = ctx["last_4h"]
        
        funding_rate = get_funding_rate(symbol)
        oi_surge = fetch_crypto_oi_surge(symbol, surge_pct=5.0)
        cmf_val = last_4h.get('CMF_20', 0)
        
        if funding_rate is not None and funding_rate < -0.01 and oi_surge and cmf_val > 0:
            atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
            if pd.isna(atr_val): atr_val = current_price * 0.02
            sl = last_4h['low'] - (atr_val * 1.0)
            sl = apply_5x_sl_cap(sl, current_price, ctx)
            
            _tp = current_price + (current_price - sl) * config.BEAR_HUNTER_TP_RR
            _rr = abs(_tp - current_price) / max(abs(current_price - sl), 1e-8)
            
            raw_vars = locals()
            _scores = build_breakout_scores(
                bb_width=0, price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
                volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20', 0), dollar_vol=last_4h.get('volume', 0) * current_price,
                rr=_rr, regime="BULL", macro_aligned=ctx["btc_ok"], consecutive_sl=0,
                market="KRIPTO", funding_rate=funding_rate, rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else 50, has_engulfing=False
            )
            _conv = calculate_conviction(_scores, ctx=ctx)
            
            if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "KRIPTO",
                    "last_1d": ctx.get("last_1d"), "strategy": "KRİPTO LONG 2: Short Squeeze Tuzağı", "signal": "AL",
                    "entry_price": current_price, "sl": sl, "tp": _tp,
                    "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                    "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                    "reason": f"🔥 Short Squeeze!\nFunding: {funding_rate:.4f}%\nOI Surge: ✅\nCMF: {cmf_val:.2f} > 0\nSL: {sl:.2f}\n" + _conv.to_reason_suffix()
                })
        return signals

    def _check_crypto_long_major_divergence(self, ctx):
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        last_4h = ctx["last_4h"]
        
        div_found, _, _, _, _ = detect_bullish_divergence(df_4h)
        if div_found:
            body = abs(last_4h['close'] - last_4h['open'])
            upper_wick = last_4h['high'] - max(last_4h['close'], last_4h['open'])
            lower_wick = min(last_4h['close'], last_4h['open']) - last_4h['low']
            is_pinbar = lower_wick > (body * 2) and upper_wick < body
            is_engulfing = (len(df_4h) >= 2) and (last_4h['close'] > df_4h['open'].iloc[-2]) and (last_4h['open'] < df_4h['close'].iloc[-2])
            
            if is_pinbar or is_engulfing:
                atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
                if pd.isna(atr_val): atr_val = current_price * 0.02
                sl = last_4h['low'] - (atr_val * 1.0)
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                
                _tp = current_price + (current_price - sl) * config.BEAR_HUNTER_TP_RR
                _rr = abs(_tp - current_price) / max(abs(current_price - sl), 1e-8)
                
                raw_vars = locals()
                _scores = build_dip_scores(
                    rsi_daily=50, rsi_hourly=last_4h.get('RSI_14', 50), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else 50,
                    price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'),
                    volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20', 0), dollar_vol=last_4h.get('volume', 0) * current_price,
                    rr=_rr, has_engulfing=is_engulfing, regime="BULL",
                    macro_aligned=ctx["btc_ok"], consecutive_sl=0, market="KRIPTO", dg_is_darth_maul=0, oi_crash=False
                )
                _conv = calculate_conviction(_scores, ctx=ctx)
                
                if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "KRIPTO",
                        "last_1d": ctx.get("last_1d"), "strategy": "KRİPTO LONG 3: Majör Destekte Uyumsuzluk", "signal": "AL",
                        "entry_price": current_price, "sl": sl, "tp": _tp,
                        "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                        "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                        "reason": f"📈 Pozitif Uyumsuzluk + Dönüş Mumu (Pinbar/Engulfing)\nSL: {sl:.2f}\n" + _conv.to_reason_suffix()
                    })
        return signals

    def _check_crypto_long_sr_flip_fvg(self, ctx):
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        last_4h = ctx["last_4h"]
        
        if len(df_4h) < 3: return signals
        
        vol_sma = last_4h.get('vol_sma_20', 0)
        prev_vol = df_4h['volume'].iloc[-2]
        
        if prev_vol >= vol_sma * 1.5:
            if last_4h['close'] < last_4h['open'] and last_4h['volume'] < vol_sma:
                ema21 = last_4h.get('EMA_20', 0)
                atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
                if pd.isna(atr_val): atr_val = current_price * 0.02
                
                if ema21 > 0 and abs(current_price - ema21) < (atr_val * 1.5):
                    if (atr_val / current_price) * 100 >= 1.3104:
                        return signals
                        
                    sl = current_price - (atr_val * 2.0)
                    sl = apply_5x_sl_cap(sl, current_price, ctx)
                    _tp = current_price + (current_price - sl) * config.BEAR_HUNTER_TP_RR
                    _rr = abs(_tp - current_price) / max(abs(current_price - sl), 1e-8)
                    
                    raw_vars = locals()
                    _scores = build_breakout_scores(
                        bb_width=0, price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
                        volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20', 0), dollar_vol=last_4h.get('volume', 0) * current_price,
                        rr=_rr, regime="BULL", macro_aligned=ctx["btc_ok"], consecutive_sl=0,
                        market="KRIPTO", funding_rate=get_funding_rate(symbol), rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else 50, has_engulfing=False
                    )
                    _conv = calculate_conviction(_scores, ctx=ctx)
                    
                    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                        signals.append({
                            "raw_indicators": _extract_raw_indicators(raw_vars),
                            "ticker": symbol, "market": "KRIPTO",
                            "last_1d": ctx.get("last_1d"), "strategy": "KRİPTO LONG 4: S/R Flip ve Zayıf Retest", "signal": "AL",
                            "entry_price": current_price, "sl": sl, "tp": _tp,
                            "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                            "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                            "reason": f"🔄 Direnç Kırılımı + EMA21 Retest (Hacimsiz düşüş)\nSL: {sl:.2f}\n" + _conv.to_reason_suffix()
                        })
        return signals

    def _check_crypto_long_bull_flag_ote(self, ctx):
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        last_4h = ctx["last_4h"]
        
        ema20 = last_4h.get('EMA_20', 0)
        ema50 = last_4h.get('EMA_50', 0)
        ema200 = last_4h.get('SMA_200', 0)
        
        if ema20 > ema50 > ema200 > 0:
            vol_sma = last_4h.get('vol_sma_20', 0)
            if last_4h['close'] > last_4h['open'] and last_4h['volume'] > vol_sma:
                swing_lows = sniper_find_swing_points(df_4h, point_type="low")
                swing_highs = sniper_find_swing_points(df_4h, point_type="high")
                if not swing_lows or not swing_highs:
                    return signals
                ote_top, ote_bot = sniper_calculate_ote(swing_lows[-1][1], swing_highs[-1][1])
                if ote_bot and ote_bot <= current_price <= ote_top:
                    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
                    if pd.isna(atr_val): atr_val = current_price * 0.02
                    sl = ote_bot - (atr_val * 1.0)
                    sl = apply_5x_sl_cap(sl, current_price, ctx)
                    
                    _tp = current_price + (current_price - sl) * config.BEAR_HUNTER_TP_RR
                    _rr = abs(_tp - current_price) / max(abs(current_price - sl), 1e-8)
                    
                    raw_vars = locals()
                    _scores = build_trend_scores(
                        adx=last_4h.get('ADX_14'), adx_prev=df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None,
                        price=current_price, ema_fast=ema20, ema_mid=ema50, ema_slow=ema200,
                        rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else 50,
                        volume=last_4h.get('volume', 0), vol_sma=vol_sma, dollar_vol=last_4h.get('volume', 0) * current_price,
                        rr=_rr, has_engulfing=False, regime="BULL", macro_aligned=ctx["btc_ok"],
                        consecutive_sl=0, market="KRIPTO"
                    )
                    _conv = calculate_conviction(_scores, ctx=ctx)
                    
                    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                        signals.append({
                            "raw_indicators": _extract_raw_indicators(raw_vars),
                            "ticker": symbol, "market": "KRIPTO",
                            "last_1d": ctx.get("last_1d"), "strategy": "KRİPTO LONG 5: Altın Trend & Boğa Bayrağı", "signal": "AL",
                            "entry_price": current_price, "sl": sl, "tp": _tp,
                            "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                            "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                            "reason": f"🚩 Boğa Bayrağı + OTE Teması (Altın Trend)\nSL: {sl:.2f} (OTE altı)\n" + _conv.to_reason_suffix()
                        })
        return signals

    def check(self, ctx: dict) -> list:
        signals = []
        
        usdt_trend = get_usdt_dominance_trend()
        unlock_risk = check_token_unlocks(ctx["symbol"])
        
        ctx["usdt_trend"] = usdt_trend
            
        if unlock_risk:
            return signals
            
        signals.extend(self._check_crypto_long_sfp_choch(ctx))
        signals.extend(self._check_crypto_long_short_squeeze(ctx))
        signals.extend(self._check_crypto_long_major_divergence(ctx))
        signals.extend(self._check_crypto_long_sr_flip_fvg(ctx))
        signals.extend(self._check_crypto_long_bull_flag_ote(ctx))
        
        return signals

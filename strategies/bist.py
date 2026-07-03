"""
strategies/bist.py — BIST Strateji Katmanı
Tüm BIST strateji fonksiyonları + scan_orb_bist.
"""
import logging
import math
import pandas as pd
import pandas_ta as ta
import config
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo

from config import (
    ATR_MULTIPLIER_BIST, ATR_CAP_BIST,
)
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores, build_dip_scores, build_breakout_scores, build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH, SNIPER_BIST_WEIGHTS,
)
from indicators import (
    sniper_get_htf_bias, sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    detect_bullish_divergence, detect_vwap_bounce, detect_obv_accumulation_bist,
    calculate_orb_cage, calculate_time_specific_rvol,
    detect_bullish_candlestick_pattern, check_near_support,
    check_bullish_engulfing_momentum, calculate_cmf,
    sniper_calculate_ote_body, calculate_anchored_vwap, get_trend_sma,
)
from meta_engine import get_bist100_trend, get_bist100_intraday_trend
from .helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _is_meaningful_volume,
    _get_consecutive_sl, _get_bist_regime, _has_absolute_hourly_volume,
    _adx_momentum_ok
)


def _check_bist_1_dip_hunter(ctx):
    signals = []
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    df_1h = ctx["df_1h"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]
    sl_pct = ctx["sl_pct"]

    if pd.isna(last_1d.get('RSI_14')):
        return signals

    trend_sma = get_trend_sma(last_1d)
    trend_aligned = not config.DIP_RSI_1D_SMA200_ALIGN_ENABLED or (
        trend_sma is not None and not pd.isna(trend_sma) and current_price > trend_sma
    )
    if not trend_aligned:
        return signals

    has_needed_cols = (
        not pd.isna(last_1h.get('RSI_14')) and not pd.isna(prev_1h.get('RSI_14')) and
        not pd.isna(last_1h.get('EMA_8')) and not pd.isna(last_1h.get('vol_sma_20'))
    )
    if not has_needed_cols:
        return signals

    is_turnaround = (
        last_1h['close'] > last_1h['EMA_8'] and
        prev_1h['close'] <= prev_1h['EMA_8'] and
        last_1h['close'] > last_1h['open']
    )
    if not is_turnaround:
        return signals

    if not check_bullish_engulfing_momentum(df_1h):
        return signals

    guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h['vol_sma_20'])
    volume_spike_ok = (
        not config.DIP_VOLUME_SPIKE_REQUIRED or
        (last_1h['volume'] >= guarded_vol_sma * config.DIP_VOLUME_SPIKE_MULT)
    )
    if not volume_spike_ok:
        return signals

    if not _is_meaningful_volume(last_1h['volume'], guarded_vol_sma, current_price, "BIST"):
        return signals

    if last_1h['RSI_14'] > prev_1h['RSI_14']:
        sl = current_price - dynamic_sl_dist
        tp = last_1d.get('EMA_21', current_price * config.BIST_DIP_HUNTER_DEFAULT_TP_MULT)
        _rr = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
        
        # Extract variables dynamically from local context
        raw_vars = locals()
        
        _scores = build_dip_scores(
            rsi_daily=last_1d['RSI_14'], rsi_hourly=last_1h['RSI_14'], rsi_prev=prev_1h['RSI_14'],
            price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'),
            volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
            rr=_rr, has_engulfing=True, regime=bist_regime,
            macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
        )
        _conv = calculate_conviction(_scores, ctx=ctx)
        if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 1: DİP AVCILIĞI", "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": tp,
                "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                "indicators": {"RSI_1G": round(last_1d.get("RSI_14", 0), 2), "RSI_1S": round(last_1h.get("RSI_14", 0), 2)},
                "reason": f"1G RSI<35 + Engulfing Onaylı Turnaround. (ATR Stop: -%{sl_pct:.1f})" + _conv.to_reason_suffix()
            })
    return signals


def _check_bist_2_trend_following(ctx):
    signals = []
    last_4h = ctx["last_4h"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    df_1h = ctx["df_1h"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]
    sl_pct = ctx["sl_pct"]

    has_needed = (
        not pd.isna(last_4h.get('ADX_14')) and
        not pd.isna(last_4h.get('EMA_8')) and
        not pd.isna(last_4h.get('EMA_21'))
    )
    if not has_needed:
        return signals

    if last_4h['EMA_8'] <= last_4h['EMA_21']:
        return signals

    if pd.isna(last_1h.get('EMA_21')):
        return signals

    is_pullback = (
        last_1h['low'] <= last_1h['EMA_21'] and
        last_1h['close'] > last_1h['EMA_21'] and
        last_1h['close'] > last_1h['open']
    )
    if is_pullback:
        has_engulfing = check_bullish_engulfing_momentum(df_1h)
        sl = current_price - dynamic_sl_dist
        _tp2 = current_price * config.BIST_TREND_FOLLOW_TP_MULT
        _rr2 = abs(_tp2 - current_price) / max(abs(current_price - sl), 1e-8)
        _adx_prev2 = df_4h.iloc[-2].get(f'ADX_{config.IND_ADX_LENGTH}') if len(df_4h) >= 2 else None
        
        raw_vars = locals()
        
        _scores2 = build_trend_scores(
            adx=last_4h['ADX_14'], adx_prev=_adx_prev2,
            price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=None,
            rsi=last_1h.get('RSI_14'), rsi_prev=prev_1h.get('RSI_14') if len(df_1h) >= 2 else None,
            volume=last_1h['volume'], vol_sma=last_1h.get('vol_sma_20', 0), dollar_vol=last_1h['volume'] * current_price,
            rr=_rr2, has_engulfing=has_engulfing, regime=bist_regime,
            macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
        )
        _conv2 = calculate_conviction(_scores2, ctx=ctx)
        if _conv2.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 2: TREND TAKİBİ", "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": _tp2,
                "conviction_score": _conv2.total_score, "conviction_grade": _conv2.grade,
                "conviction_details": _conv2.component_scores, "position_size_pct": _conv2.position_size_pct,
                "indicators": {"ADX_4S": round(last_4h.get("ADX_14", 0), 2), "RSI_1S": round(last_1h.get("RSI_14", 0), 2)},
                "reason": (
                    f"4S ADX>25 Trend{ ' + Engulfing Momentum' if has_engulfing else ''}. "
                    f"1S EMA21 pullback. (ATR Stop: -%{sl_pct:.1f})"
                ) + _conv2.to_reason_suffix()
            })
    return signals


def _is_bist_3_retest_and_price_ok(current_price, month_high):
    if current_price <= month_high:
        return False
    if config.BREAKOUT_RETEST_REQUIRED:
        max_limit = month_high * (1.0 + (config.BREAKOUT_RETEST_TOLERANCE_PCT / 100.0))
        if not (month_high <= current_price <= max_limit):
            return False
    return True


def _get_bist_3_bb_width_prev(df_1d, last_1d):
    bb_upper_col = [c for c in df_1d.columns if 'BBU' in c]
    bb_lower_col = [c for c in df_1d.columns if 'BBL' in c]
    bb_mid_col = [c for c in df_1d.columns if 'BBM' in c]

    if not bb_upper_col or not bb_lower_col or not bb_mid_col:
        return None

    prev_1d = last_1d
    if len(df_1d) >= 2:
        prev_1d = df_1d.iloc[-2]
        
    bbu_prev = prev_1d[bb_upper_col[0]]
    bbl_prev = prev_1d[bb_lower_col[0]]
    bbm_prev = prev_1d[bb_mid_col[0]]
    
    if bbm_prev == 0:
        return 1.0
    return (bbu_prev - bbl_prev) / bbm_prev


def _check_bist_3_squeeze_breakout(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    current_price = ctx["current_price"]
    month_high = ctx["month_high"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]
    sl_pct = ctx["sl_pct"]

    bb_width_prev = _get_bist_3_bb_width_prev(df_1d, last_1d)
    if bb_width_prev is None or bb_width_prev >= config.BIST_SQUEEZE_PREV_WIDTH_LIMIT:
        return signals

    if not _is_bist_3_retest_and_price_ok(current_price, month_high):
        return signals

    if pd.isna(last_1h.get('vol_sma_20')):
        return signals
        
    # (Relaxed)
    # if last_1h.get('RSI_14', 0) <= 51.94:
    #     return signals
        
    guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h['vol_sma_20'])
    if not _is_meaningful_volume(last_1h['volume'], guarded_vol_sma, current_price, "BIST"):
        return signals
    if not _has_absolute_hourly_volume(last_1h['volume'], current_price, "BIST"):
        return signals
        
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.time() < dt_time(10, 0):
        return signals
        
    prev_close = current_price
    if len(df_1d) >= 2:
        prev_close = df_1d.iloc[-2]['close']
    gap_pct = abs(last_1h['open'] - prev_close) / max(prev_close, 1e-8) * 100
    
    sl = current_price - dynamic_sl_dist
    _tp3 = current_price + (dynamic_sl_dist * 3.0)
    _rr3 = abs(_tp3 - current_price) / max(abs(current_price - sl), 1e-8)
    
    raw_vars = locals()
    _scores3 = build_breakout_scores(
        bb_width=bb_width_prev, price=current_price,
        ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
        volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
        rr=_rr3, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
        dg_gap_pct=gap_pct, rsi=last_1h.get('RSI_14')
    )
    _conv3 = calculate_conviction(_scores3, ctx=ctx)
    if _conv3.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 3: SQUEEZE KIRILIMI", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": _tp3,
            "conviction_score": _conv3.total_score, "conviction_grade": _conv3.grade,
            "conviction_details": _conv3.component_scores, "position_size_pct": _conv3.position_size_pct,
            "reason": f"Günlükte daralma, hacimli direnç kırılımı + Mutlak TL hacmi onaylı. (ATR Stop: -%{sl_pct:.1f})" + _conv3.to_reason_suffix()
        })
    return signals


def _check_bist_4_sniper_ote(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]

    htf_bias = sniper_get_htf_bias(df_1d)
    if htf_bias != 1:
        return signals

    swing_lows = sniper_find_swing_points(df_4h, point_type="low")
    swing_highs = sniper_find_swing_points(df_4h, point_type="high")

    sweep_ok, sweep_low = sniper_detect_sweep(df_4h, swing_lows, point_type="low")
    if not sweep_ok:
        return signals

    msb_ok, msb_high, msb_idx = sniper_detect_msb(df_4h, swing_highs, point_type="high")
    if msb_ok:
        sweep_idx = swing_lows[-1][0] if swing_lows else None
        ote_top, ote_bottom = sniper_calculate_ote_body(df_4h, sweep_idx, msb_idx, direction="long")
        if ote_top > 0 and ote_bottom > 0 and ote_bottom <= current_price <= ote_top:
            has_fvg, _, _ = sniper_detect_fvg(df_4h, ote_top, ote_bottom, direction="bullish")
            fvg_ok = not config.SMC_FVG_REQUIRED or has_fvg
            
            if fvg_ok and not pd.isna(last_1h.get('vol_sma_20')):
                guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h['vol_sma_20'])
                if _has_absolute_hourly_volume(last_1h['volume'], current_price, "BIST") and \
                   last_1h['volume'] <= guarded_vol_sma * config.BIST_SMC_BREAKOUT_VOL_MULT:
                    sl = sweep_low * config.BIST_SMC_BREAKOUT_SL_MULT
                    sl_dist = max(current_price - sl, 1e-8)
                    tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
                    fvg_label = " + FVG Onaylı ✅" if has_fvg else ""
                    _rr4 = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
                    
                    raw_vars = locals()
                    
                    _scores4 = build_breakout_scores(
                        bb_width=None, price=current_price,
                        ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
                        volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
                        rr=_rr4, regime=bist_regime,
                        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
                        rsi=last_1h.get('RSI_14')
                    )
                    if has_fvg:
                        _scores4["engulfing"] = min(100.0, _scores4["engulfing"] + config.SMC_FVG_BONUS)
                    _conv4 = calculate_conviction(_scores4, ctx=ctx)
                    if _conv4.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                        signals.append({
                            "raw_indicators": _extract_raw_indicators(raw_vars),
                            "ticker": symbol, "market": "BIST",
                            "strategy": "BIST LONG 4: KESKİN NİŞANCI (OTE)", "signal": "AL",
                            "entry_price": current_price, "sl": sl, "tp": tp,
                            "conviction_score": _conv4.total_score, "conviction_grade": _conv4.grade,
                            "conviction_details": _conv4.component_scores, "position_size_pct": _conv4.position_size_pct,
                            "reason": (
                                f"🎯 SMC Kurulum (Gövde Fibo){fvg_label}\n"
                                f"🧹 Likidite: Eski dip ({sweep_low:.2f}) temizlendi.\n"
                                f"📐 MSB: Yapı kırılımı ({msb_high:.2f}) onaylı.\n"
                                f"🎣 OTE Bölgesi (Gövde): {ote_bottom:.2f} - {ote_top:.2f} (Temas anında & Düşük Hacim)\n"
                                f"🛡️ İşlem %4 kâra geçince Break-Even uygula."
                            ) + _conv4.to_reason_suffix()
                        })
    return signals


def _get_bist_daily_squeeze_setup(df_1d):
    df_1d.ta.kc(length=config.IND_BBANDS_LENGTH, scalar=config.KC_SCALAR, append=True)
    bbu_cols = [c for c in df_1d.columns if 'BBU' in c]
    bbl_cols = [c for c in df_1d.columns if 'BBL' in c]
    kcu_cols = [c for c in df_1d.columns if 'KCU' in c]
    kcl_cols = [c for c in df_1d.columns if 'KCL' in c]
    
    if not (bbu_cols and bbl_cols and kcu_cols and kcl_cols):
        return False, None, None
        
    bbu_c, bbl_c, kcu_c, kcl_c = bbu_cols[0], bbl_cols[0], kcu_cols[0], kcl_cols[0]
    if len(df_1d) < 2:
        return False, None, None
        
    prev_bbu_1d = df_1d.iloc[-2][bbu_c]
    prev_bbl_1d = df_1d.iloc[-2][bbl_c]
    
    squeeze_count = sum(
        1 for idx in range(-7, -1)
        if abs(idx) <= len(df_1d) and
        not pd.isna(df_1d.iloc[idx].get(bbu_c)) and
        df_1d.iloc[idx][bbu_c] < df_1d.iloc[idx][kcu_c] and
        df_1d.iloc[idx][bbl_c] > df_1d.iloc[idx][kcl_c]
    )
    return (squeeze_count >= 3), prev_bbu_1d, prev_bbl_1d

def _check_bist_5_vol_squeeze_long(ctx, prev_bbu_1d, guarded_vol_sma):
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    current_price = ctx["current_price"]
    xu100_down = ctx["xu100_down"]
    df_1h = ctx["df_1h"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]
    sl_pct = ctx["sl_pct"]
    
    if prev_bbu_1d is None or current_price <= prev_bbu_1d:
        return None
    if last_1h['close'] <= last_1h['open']:
        return None
    if not _is_meaningful_volume(last_1h['volume'], guarded_vol_sma, current_price, "BIST"):
        return None
        
    trend_aligned = not config.SQUEEZE_TREND_ALIGN_REQUIRED or (
        last_1d.get('EMA_21') is not None and not pd.isna(last_1d.get('EMA_21')) and
        current_price > last_1d.get('EMA_21')
    )
        
    sq_mid = (last_1h['high'] + last_1h['low']) / 2
    ema21_1h = last_1h.get('EMA_21', current_price * config.BIST_VWAP_EMA_SL_FALLBACK_LONG)
    sl = min(
        min(sq_mid, ema21_1h) if not pd.isna(ema21_1h) else sq_mid,
        current_price * (1.0 - config.BIST_MIN_SL_PCT)
    )
    _tp5u = current_price + (dynamic_sl_dist * config.BEAR_HUNTER_TP_RR)
    _rr5u = abs(_tp5u - current_price) / max(abs(current_price - sl), 1e-8)
    
    raw_vars = locals()
    _scores5u = build_breakout_scores(
        bb_width=None, price=current_price,
        ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
        volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
        rr=_rr5u, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
        rsi=last_1h.get('RSI_14')
    )
    
    if not trend_aligned:
        _scores5u["conflict_penalty"] = _scores5u.get("conflict_penalty", 0) + getattr(config, 'PENALTY_TREND_MISMATCH', 15.0)
        
    _conv5u = calculate_conviction(_scores5u, ctx=ctx)
    if _conv5u.grade not in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        return None
        
    return {
        "raw_indicators": _extract_raw_indicators(raw_vars),
        "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 5: HACİMLİ KIRILIM", "signal": "AL",
        "entry_price": current_price, "sl": sl, "tp": _tp5u,
        "conviction_score": _conv5u.total_score, "conviction_grade": _conv5u.grade,
        "conviction_details": _conv5u.component_scores, "position_size_pct": _conv5u.position_size_pct,
        "reason": (
            f"🗜️ Günlük Squeeze Hacimli Yukarı Kırıldı!\n"
            f"1G BB Keltner içindeydi (Daralma). 1S Fiyat Günlük BBU ({prev_bbu_1d:.2f}) üzerine çıktı.\n"
            f"Hacimli yeşil mum ile 1S EMA21 üzerinde breakout.\n"
            f"SL: 1S Kırılım barının %50'si veya 1S EMA21 ({sl:.2f})"
        ) + _conv5u.to_reason_suffix()
    }

def _check_bist_5_vol_squeeze_short(ctx, prev_bbl_1d, guarded_vol_sma):
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    current_price = ctx["current_price"]
    xu100_down = ctx["xu100_down"]
    df_1h = ctx["df_1h"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]
    
    if prev_bbl_1d is None or current_price >= prev_bbl_1d:
        return None
    if last_1h['close'] >= last_1h['open']:
        return None
    if not _is_meaningful_volume(last_1h['volume'], guarded_vol_sma, current_price, "BIST"):
        return None
        
    trend_aligned = not config.SQUEEZE_TREND_ALIGN_REQUIRED or (
        last_1d.get('EMA_21') is not None and not pd.isna(last_1d.get('EMA_21')) and
        current_price < last_1d.get('EMA_21')
    )
        
    sq_mid = (last_1h['high'] + last_1h['low']) / 2
    ema21_1h = last_1h.get('EMA_21', current_price * config.BIST_VWAP_EMA_SL_FALLBACK_SHORT)
    sl = max(
        max(sq_mid, ema21_1h) if not pd.isna(ema21_1h) else sq_mid,
        current_price * (1.0 + config.BIST_MIN_SL_PCT)
    )
    _tp5d = current_price - (dynamic_sl_dist * config.BEAR_HUNTER_TP_RR)
    _rr5d = abs(current_price - _tp5d) / max(abs(sl - current_price), 1e-8)
    
    raw_vars = locals()
    _scores5d = build_breakout_scores(
        bb_width=None, price=current_price,
        ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
        volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
        rr=_rr5d, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
        is_long=False, rsi=last_1h.get('RSI_14')
    )
    
    if not trend_aligned:
        _scores5d["conflict_penalty"] = _scores5d.get("conflict_penalty", 0) + getattr(config, 'PENALTY_TREND_MISMATCH', 15.0)
        
    _conv5d = calculate_conviction(_scores5d, ctx=ctx)
    if _conv5d.grade not in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        return None
        
    return {
        "raw_indicators": _extract_raw_indicators(raw_vars),
        "ticker": symbol, "market": "BIST", "strategy": "BIST SHORT 5: HACİMLİ KIRILIM", "signal": "SAT",
        "entry_price": current_price, "sl": sl, "tp": _tp5d,
        "conviction_score": _conv5d.total_score, "conviction_grade": _conv5d.grade,
        "conviction_details": _conv5d.component_scores, "position_size_pct": _conv5d.position_size_pct,
        "reason": (
            f"🗜️ Günlük Squeeze Hacimli Aşağı Kırıldı!\n"
            f"1G BB Keltner içindeydi (Daralma). 1S Fiyat Günlük BBL ({prev_bbl_1d:.2f}) altına indi.\n"
            f"Hacimli kırmızı mum ile 1S EMA21 altında breakout.\n"
            f"SL: 1S Kırılım barının %50'si veya 1S EMA21 ({sl:.2f})"
        ) + _conv5d.to_reason_suffix()
    }

def _check_bist_5_vol_squeeze(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1h = ctx["last_1h"]
    
    daily_squeeze_setup, prev_bbu_1d, prev_bbl_1d = _get_bist_daily_squeeze_setup(df_1d)
    if not daily_squeeze_setup:
        return signals
        
    if pd.isna(last_1h.get('vol_sma_20')):
        return signals
        
    guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h['vol_sma_20'])
    
    long_sig = _check_bist_5_vol_squeeze_long(ctx, prev_bbu_1d, guarded_vol_sma)
    if long_sig:
        signals.append(long_sig)
        
    short_sig = _check_bist_5_vol_squeeze_short(ctx, prev_bbl_1d, guarded_vol_sma)
    if short_sig:
        signals.append(short_sig)
        
    return signals


def _check_bist_6_rs(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    xu100_daily = ctx["xu100_daily"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]

    if xu100_daily is None:
        return signals

    from indicators import calculate_relative_strength
    rs_strong, rs_trend_up, idx_stressed, idx_recovering = calculate_relative_strength(df_1d, xu100_daily)
    if rs_strong and rs_trend_up and (idx_recovering or not idx_stressed):
        rsi_timing_ok = pd.isna(last_1h.get('RSI_14')) or last_1h['RSI_14'] <= config.RS_ENTRY_TIMING_RSI_LIMIT
        if rsi_timing_ok and not pd.isna(last_1h.get('vol_sma_20')):
            guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h['vol_sma_20'])
            if _is_meaningful_volume(last_1h['volume'], guarded_vol_sma, current_price, "BIST"):
                sl = current_price - dynamic_sl_dist
                _tp6 = current_price + (dynamic_sl_dist * 3.0)
                _rr6 = abs(_tp6 - current_price) / max(abs(current_price - sl), 1e-8)
                
                raw_vars = locals()
                
                _scores6 = build_trend_scores(
                    adx=None, adx_prev=None,
                    price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
                    rsi=last_1h.get('RSI_14'), rsi_prev=prev_1h.get('RSI_14') if len(df_1h) >= 2 else None,
                    volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
                    rr=_rr6, has_engulfing=False, regime=bist_regime,
                    macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
                )
                _conv6 = calculate_conviction(_scores6, ctx=ctx)
                if _conv6.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({
                        "raw_indicators": _extract_raw_indicators(raw_vars),
                        "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 6: GÖRECELİ GÜÇ RADARI (RS)", "signal": "AL",
                        "entry_price": current_price, "sl": sl, "tp": _tp6,
                        "conviction_score": _conv6.total_score, "conviction_grade": _conv6.grade,
                        "conviction_details": _conv6.component_scores, "position_size_pct": _conv6.position_size_pct,
                        "reason": (
                            f"🏋️ Endekse Kafa Tutan Hisse!\n"
                            f"RS Çizgisi > 50G SMA (Güçlü ✅)\n"
                            f"Endeks toparlandı, EMA8 üzerine çıktı.\n"
                            f"Bu hisse endeks düşerken düşmedi → Kurumsal birikim."
                        ) + _conv6.to_reason_suffix()
                    })
    return signals


def _check_bist_7_vwap(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]

    last_1h_time = pd.to_datetime(last_1h.name) if last_1h.name is not None else None
    if last_1h_time is not None and last_1h_time.weekday() in (0, 1):
        return signals

    sma_50 = last_1d.get('SMA_50')
    trend_sma = get_trend_sma(last_1d)
    is_bear_regime = (not pd.isna(sma_50) and not pd.isna(trend_sma) and current_price < sma_50 and current_price < trend_sma)
    ema_21_daily = last_1d.get('EMA_21')
    mtf_trend_down = (not pd.isna(ema_21_daily) and last_1d['close'] < ema_21_daily)

    # VWAP Golden Filters (RSI & Volatilite/ATR) (Relaxed)
    # if not pd.isna(last_1h.get('RSI_14')) and last_1h['RSI_14'] >= getattr(config, 'VWAP_LONG_MAX_RSI', 60.0):
    #     return signals
        
    if f'ATRr_14' not in df_1h.columns:
        df_1h.ta.atr(length=14, append=True)
    if 'ATR_SMA_14' not in df_1h.columns:
        df_1h['ATR_SMA_14'] = df_1h['ATRr_14'].rolling(window=14).mean()

    current_atr = df_1h['ATRr_14'].iloc[-1]
    atr_sma = df_1h['ATR_SMA_14'].iloc[-1]
    
    high_volatility = False
    if pd.notna(current_atr) and pd.notna(atr_sma) and atr_sma > 0:
        if (current_atr / atr_sma) > getattr(config, 'VWAP_MAX_ATR_RATIO', 2.0):
            high_volatility = True

    vwap_val = calculate_anchored_vwap(df_1h, anchor_type="weekly")
    if vwap_val is None:
        return signals

    bounce_ok, wick_low = detect_vwap_bounce(df_1h, vwap_val)
    if not bounce_ok or wick_low is None:
        return signals

    vol_sma_20 = last_1h.get('vol_sma_20')
    guarded_vol_sma = _apply_volume_sma_guard(df_1h, vol_sma_20)
    
    if not _has_absolute_hourly_volume(last_1h['volume'], current_price, "BIST"):
        pass  # Volume check handled by conviction scorer penalty

    sl = wick_low * (1.0 - config.VWAP_SL_BUFFER_PCT / 100.0)
    _tp7 = current_price + (dynamic_sl_dist * config.BEAR_HUNTER_TP_RR)
    _rr7 = abs(_tp7 - current_price) / max(abs(current_price - sl), 1e-8)
    
    raw_vars = locals()
    _scores7 = build_trend_scores(
        adx=None, adx_prev=None,
        price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
        rsi=last_1h.get('RSI_14'), rsi_prev=prev_1h.get('RSI_14') if len(df_1h) >= 2 else None,
        volume=last_1h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_1h['volume'] * current_price,
        rr=_rr7, has_engulfing=False, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
    )
    
    if is_bear_regime:
        _scores7["conflict_penalty"] = _scores7.get("conflict_penalty", 0) + getattr(config, 'PENALTY_BEAR_REGIME', 20.0)
    if mtf_trend_down:
        _scores7["conflict_penalty"] = _scores7.get("conflict_penalty", 0) + getattr(config, 'PENALTY_MTF_TREND_DOWN', 15.0)
    if high_volatility:
        _scores7["conflict_penalty"] = _scores7.get("conflict_penalty", 0) + getattr(config, 'PENALTY_HIGH_VOLATILITY', 15.0)
        
    _conv7 = calculate_conviction(_scores7, ctx=ctx)
    if _conv7.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 7: VWAP KURUMSAL MIKNATISI", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": _tp7,
            "conviction_score": _conv7.total_score, "conviction_grade": _conv7.grade,
            "conviction_details": _conv7.component_scores, "position_size_pct": _conv7.position_size_pct,
            "reason": (
                f"⚓ VWAP Bounce (Kurumsal Mıknatıs) + 4 Kapı Zırhı!\n"
                f"✅ Rejim: Boğa | ✅ Trend: Uyumlu | ✅ Endeks: Güvenli\n"
                f"Anchored VWAP: {vwap_val:.2f} (1.5x Hacimle Sıçradı)\n"
                f"SL: Fitil ucunun altı ({sl:.2f}) — Dar stop."
            ) + _conv7.to_reason_suffix()
        })
    return signals


def _check_bist_8_obv(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    dynamic_sl_dist = ctx["dynamic_sl_dist"]

    obv_ok, obv_box_high, obv_box_low = detect_obv_accumulation_bist(df_1d, max_change_pct=config.BIST_OBV_ACC_MAX_CHANGE_PCT)
    if obv_ok and obv_box_high is not None:
        # (Relaxed)
        # if last_1h.get('RSI_14', 50) >= 52.20:
        #     return signals
        cmf_val = calculate_cmf(df_1d)
        if cmf_val is not None and not pd.isna(cmf_val) and cmf_val >= config.BIST_OBV_CMF_THRESHOLD:
            sl = (obv_box_high + obv_box_low) / 2
            _tp8 = current_price + (dynamic_sl_dist * config.BEAR_HUNTER_TP_RR)
            _rr8 = abs(_tp8 - current_price) / max(abs(current_price - sl), 1e-8)
            
            raw_vars = locals()
            
            _scores8 = build_breakout_scores(
                bb_width=None, price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=None,
                volume=last_1h.get('volume', 0), vol_sma=last_1h.get('vol_sma_20', 0),
                dollar_vol=last_1h.get('volume', 0) * current_price,
                rr=_rr8, regime=bist_regime,
                macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
                rsi=last_1d.get('RSI_14', 50),
                rsi_prev=df_1d['RSI_14'].iloc[-2] if len(df_1d) >= 2 else last_1d.get('RSI_14', 50),
                rsi_1h=last_1h.get('RSI_14', 50),
                has_engulfing=False, cmf=cmf_val
            )
            _conv8 = calculate_conviction(_scores8, ctx=ctx)
            if _conv8.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 8: SESSİZ BİRİKİM RADARI (OBV)", "signal": "AL",
                    "entry_price": current_price, "sl": sl, "tp": _tp8,
                    "conviction_score": _conv8.total_score, "conviction_grade": _conv8.grade,
                    "conviction_details": _conv8.component_scores, "position_size_pct": _conv8.position_size_pct,
                    "reason": (
                        f"🕵️ Sessiz Birikim + CMF Onaylı!\n"
                        f"20 gün yatay kutu: {obv_box_low:.2f} - {obv_box_high:.2f}\n"
                        f"OBV sürekli yeni tepeler yapıyor + CMF: {cmf_val:.3f}\n"
                        f"Kutu direnci hacimli kırıldı → Ralli başlıyor."
                    ) + _conv8.to_reason_suffix()
                })
    return signals


def _get_bist_10_sniper_setup(df_1h_sniper):
    kc_upper_col = [c for c in df_1h_sniper.columns if 'KCU' in c]
    kc_lower_col = [c for c in df_1h_sniper.columns if 'KCL' in c]
    bb_upper_col = [c for c in df_1h_sniper.columns if 'BBU' in c]
    bb_lower_col = [c for c in df_1h_sniper.columns if 'BBL' in c]
    bb_mid_col = [c for c in df_1h_sniper.columns if 'BBM' in c]
    
    if not (kc_upper_col and kc_lower_col and bb_upper_col and bb_lower_col and bb_mid_col):
        return None

    bbu_s = df_1h_sniper[bb_upper_col[0]]
    bbl_s = df_1h_sniper[bb_lower_col[0]]
    bbm_s = df_1h_sniper[bb_mid_col[0]]
    
    bbw_series = (bbu_s - bbl_s) / bbm_s
    bbw_lowest_30 = bbw_series.rolling(config.BIST_SQUEEZE_ROLLING_WINDOW).quantile(config.BIST_SQUEEZE_QUANTILE)
    is_squeeze = bbw_series.iloc[-1] <= bbw_lowest_30.iloc[-1]
    
    bb_pct_series = (df_1h_sniper['close'] - bbl_s) / (bbu_s - bbl_s)
    has_bb_pct_touch = (bb_pct_series.iloc[-3:] <= config.BIST_SQUEEZE_BB_PCT_TOUCH_LIMIT).any()
    
    is_weak = not (is_squeeze or has_bb_pct_touch)

    bbw = bbw_series.iloc[-1]
    kcu = df_1h_sniper[kc_upper_col[0]].iloc[-1]
    kcl = df_1h_sniper[kc_lower_col[0]].iloc[-1]
    kcw = (kcu - kcl) / bbm_s.iloc[-1] if bbm_s.iloc[-1] != 0 else 0
    bb_pct = bb_pct_series.iloc[-1]

    return {
        "is_squeeze": is_squeeze,
        "bbw": bbw,
        "kcw": kcw,
        "bb_pct": bb_pct,
        "bbl_s_last": bbl_s.iloc[-1],
        "is_weak": is_weak
    }


def _check_bist_10_sniper(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_1h = ctx["df_1h"]
    last_1d = ctx["last_1d"]
    last_1h = ctx["last_1h"]
    prev_1h = ctx["prev_1h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]

    df_1h_sniper = df_1h.copy()
    df_1h_sniper.ta.kc(length=config.IND_BBANDS_LENGTH, scalar=config.KC_SCALAR, append=True)
    df_1h_sniper.ta.bbands(length=config.IND_BBANDS_LENGTH, std=config.IND_BBANDS_STD, append=True)
    
    setup = _get_bist_10_sniper_setup(df_1h_sniper)
    if setup is None:
        return signals

    is_squeeze = setup["is_squeeze"]
    bbw = setup["bbw"]
    kcw = setup["kcw"]
    bb_pct = setup["bb_pct"]
    bbl_s_last = setup["bbl_s_last"]
    is_weak = setup.get("is_weak", False)
    
    has_fvg, _, _ = sniper_detect_fvg(df_1h_sniper, df_1h_sniper['high'].iloc[-1], df_1h_sniper['low'].iloc[-1], direction="bullish")
    swing_lows_s = sniper_find_swing_points(df_1h_sniper, point_type="low")
    sweep_ok, _ = sniper_detect_sweep(df_1h_sniper, swing_lows_s, point_type="low")
    has_sfp = sweep_ok
    
    sl = min(
        max(bbl_s_last * config.BIST_SQUEEZE_SL_BBL_MULT, current_price * config.BIST_SQUEEZE_SL_MIN_MULT),
        current_price * (1.0 - config.BIST_MIN_SL_PCT)
    )
    _tp_sn = current_price + 2.0 * (current_price - sl)
    _rr_sn = abs(_tp_sn - current_price) / max(abs(current_price - sl), 1e-8)
    guarded_vol_sma = _apply_volume_sma_guard(df_1h, last_1h.get('vol_sma_20', 0))
    
    asset_trend_aligned = last_1d.get("EMA_8", 0) > last_1d.get("EMA_21", float("inf"))
    raw_vars = locals()
    _scores_sn = build_sniper_scores(
        price=current_price, ema_fast=last_1h.get('EMA_8'), ema_mid=last_1h.get('EMA_21'), ema_slow=last_1d.get('SMA_50'),
        rsi=last_1h.get('RSI_14'), rsi_prev=prev_1h.get('RSI_14') if len(df_1h) >= 2 else None,
        volume=last_1h.get('volume', 0), vol_sma=guarded_vol_sma, dollar_vol=last_1h.get('volume', 0) * current_price,
        rr=_rr_sn, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol),
        bbw=bbw, kcw=kcw, pb=bb_pct, fvg_present=has_fvg, sfp_present=has_sfp,
        market="BIST", is_squeeze=is_squeeze,
        asset_trend_aligned=asset_trend_aligned
    )
    if is_weak:
        _scores_sn["setup_weak_penalty"] = -8.0
        
    _conv_sn = calculate_conviction(_scores_sn, weights=SNIPER_BIST_WEIGHTS, ctx=ctx)
    if _conv_sn.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "BIST",
            "strategy": "BIST LONG 10: KESKİN NİŞANCI (SNIPER)", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": _tp_sn,
            "conviction_score": _conv_sn.total_score, "conviction_grade": _conv_sn.grade,
            "conviction_details": _conv_sn.component_scores, "position_size_pct": _conv_sn.position_size_pct,
            "reason": (
                f"🎯 Keskin Nişancı!\n"
                f"Kanunlar: Squeeze: {_scores_sn['bbw_squeeze']:.1f}, %B: {_scores_sn['percent_b']:.1f}, FVG/SFP: {_scores_sn['fvg_sfp']:.1f}\n"
                f"SL: Bollinger Alt Band Altı ({sl:.2f})"
            ) + _conv_sn.to_reason_suffix()
        })
    return signals


def _detect_bist11_pattern(df_4h, df_1d):
    pattern_name, pattern_details = detect_bullish_candlestick_pattern(df_4h)
    if not pattern_name:
        return None, None, False
        
    rsi_d_filter = df_1d['RSI_14'].iloc[-1] if 'RSI_14' in df_1d.columns else 50.0
    pattern_type = pattern_details.get("pattern", "")
    pattern_ok = True
    if pattern_type in ["Hammer", "Inverted Hammer", "Dragonfly Doji", "Tweezer Bottom"] and rsi_d_filter >= config.BIST_CANDLE_RSI_D_LIMIT_HAMMER:
        pattern_ok = False
    elif pattern_type in ["Bullish Engulfing", "Piercing Line", "Morning Star"] and config.BIST_CANDLE_RSI_D_LIMIT_ENGULFING_MIN <= rsi_d_filter <= config.BIST_CANDLE_RSI_D_LIMIT_ENGULFING_MAX:
        pattern_ok = False
    elif pattern_type == "Three White Soldiers" and rsi_d_filter >= config.BIST_CANDLE_RSI_D_LIMIT_SOLDIERS:
        pattern_ok = False
        
    return pattern_name, pattern_details, pattern_ok

def _verify_bist11_volume(df_4h):
    vol_4h = df_4h['volume'].values
    vol_sma_period = config.BIST11_VOLUME_SMA_PERIOD
    recent_vols = vol_4h[-(vol_sma_period+1):-1]
    avg_vol_prev = recent_vols.mean() if len(recent_vols) > 0 else 0
    vol_ratio = vol_4h[-1] / avg_vol_prev if avg_vol_prev > 0 else 0
    volume_ok = vol_ratio >= config.BIST11_VOLUME_MULT
    return volume_ok, vol_ratio, avg_vol_prev

def _build_bist11_signal(ctx, pattern_name, support_reason, vol_ratio, avg_vol_prev, div_ok, is_weak=False):
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    
    vol_4h = df_4h['volume'].values
    atr_series_4h = df_4h['ATR_14'] if 'ATR_14' in df_4h.columns else df_4h.ta.atr(length=14)
    atr_4h = float(atr_series_4h.iloc[-1]) if atr_series_4h is not None and not atr_series_4h.empty and not pd.isna(atr_series_4h.iloc[-1]) else (df_4h['high'].iloc[-1] - df_4h['low'].iloc[-1])
    if atr_4h <= 0:
        atr_4h = 1e-8
        
    sl = current_price - (atr_4h * config.BIST11_ATR_MULTIPLIER)
    tp = current_price + config.BEAR_HUNTER_TP_RR * (current_price - sl)
    rr = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)

    rsi_d = df_1d['RSI_14'].iloc[-1] if 'RSI_14' in df_1d.columns else 50.0
    rsi_h = df_4h['RSI_14'].iloc[-1] if 'RSI_14' in df_4h.columns else 50.0
    rsi_p = df_4h['RSI_14'].iloc[-2] if len(df_4h) >= 2 and 'RSI_14' in df_4h.columns else rsi_h
    
    is_engulfing = "Engulfing" in pattern_name
    _scores_cand = build_dip_scores(
        rsi_daily=rsi_d, rsi_hourly=rsi_h, rsi_prev=rsi_p,
        price=current_price,
        ema_fast=df_4h['EMA_8'].iloc[-1] if 'EMA_8' in df_4h.columns else None,
        ema_mid=df_4h['EMA_21'].iloc[-1] if 'EMA_21' in df_4h.columns else None,
        volume=vol_4h[-1], vol_sma=avg_vol_prev, dollar_vol=vol_4h[-1] * current_price,
        rr=rr, has_engulfing=is_engulfing, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
    )
    
    if div_ok:
        _scores_cand['rsi'] = min(100.0, _scores_cand.get('rsi', 0) + 15.0)

    if is_weak:
        _scores_cand["setup_weak_penalty"] = -8.0

    _conv_cand = calculate_conviction(_scores_cand, ctx=ctx)
    if _conv_cand.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        raw_vars = locals()
        return {
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 11: MUM FORMASYONLARI (CANDLESTICK)", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": tp,
            "conviction_score": _conv_cand.total_score, "conviction_grade": _conv_cand.grade,
            "conviction_details": _conv_cand.component_scores, "position_size_pct": _conv_cand.position_size_pct,
            "body_close_stop_required": True, "timeframe": "4h",
            "reason": (
                f"🕯️ 4H Mum Formasyonu: {pattern_name}\n"
                f"🛡️ Destek Teyidi: {support_reason}\n"
                f"📊 Hacim Teyidi: {vol_ratio:.1f}x (Eşik: {config.BIST11_VOLUME_MULT:.1f}x)\n"
                f"📈 Uyumsuzluk Teyidi: {'Evet' if div_ok else 'Hayır'}"
            ) + _conv_cand.to_reason_suffix()
        }
    return None

def _check_bist_11_candlestick(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    current_price = ctx["current_price"]

    try:
        if len(df_4h) < 20:
            return signals

        pattern_name, pattern_details, pattern_ok = _detect_bist11_pattern(df_4h, df_1d)
        if not pattern_name:
            return signals

        is_weak = not pattern_ok

        near_support, support_reason = check_near_support(current_price, df_4h, df_1d, tolerance_pct=config.BIST11_SUPPORT_TOLERANCE_PCT)
        if not near_support:
            is_weak = True

        volume_ok, vol_ratio, avg_vol_prev = _verify_bist11_volume(df_4h)
        if not volume_ok:
            is_weak = True

        div_ok = not config.BIST11_DIVERGENCE_REQUIRED or detect_bullish_divergence(df_4h, neighbors=3)[0]

        sig = _build_bist11_signal(ctx, pattern_name, support_reason, vol_ratio, avg_vol_prev, div_ok, is_weak)
        if sig:
            signals.append(sig)
    except Exception as e:
        logging.warning(f"[analyze_strategies_bist] BIST 11 Hata: {e}", exc_info=True)
    return signals


def _check_bist12_timing_filters(df_1d, df_4h, current_price):
    rsi_1d = df_1d['RSI_14'].iloc[-1] if 'RSI_14' in df_1d.columns else 50.0
    ema_21_4h = df_4h['EMA_21'].iloc[-1] if 'EMA_21' in df_4h.columns else current_price
    dist_to_ema21 = abs(current_price - ema_21_4h) / ema_21_4h * 100 if ema_21_4h > 0 else 0
    adx_4h = df_4h['ADX_14'].iloc[-1] if 'ADX_14' in df_4h.columns else 25.0
    
    if rsi_1d >= config.BIST_CHART_RSI_D_LIMIT or dist_to_ema21 >= config.BIST_CHART_EMA21_DIST_LIMIT:
        return False
    # HARD FILTER REMOVED: ADX Threshold delegated to conviction_scorer
    # if adx_4h < 20.0:
    #     return False
    return True

def _build_bist12_signal(ctx, pattern_name, pattern_details, is_weak=False):
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    current_price = ctx["current_price"]
    symbol = ctx["symbol"]
    bist_regime = ctx["bist_regime"]
    xu100_down = ctx["xu100_down"]
    
    import numpy as np
    sl = pattern_details.get("sl")
    atr_series_4h = df_4h['ATR_14'] if 'ATR_14' in df_4h.columns else df_4h.ta.atr(length=14)
    atr_4h = float(atr_series_4h.iloc[-1]) if atr_series_4h is not None and not atr_series_4h.empty and not pd.isna(atr_series_4h.iloc[-1]) else (df_4h['high'].iloc[-1] - df_4h['low'].iloc[-1])
    if atr_4h <= 0:
        atr_4h = 1e-8

    if sl is None or sl <= 0 or sl >= current_price:
        sl = current_price - (atr_4h * config.BIST12_ATR_MULTIPLIER)
    
    atr_sl_dist = atr_4h * config.BIST12_ATR_MULTIPLIER
    min_sl_dist = current_price * config.BIST12_MIN_SL_PCT
    pattern_sl_dist = current_price - sl
    final_sl_dist = max(pattern_sl_dist, atr_sl_dist, min_sl_dist)
    
    sl = current_price - final_sl_dist
    tp = current_price + config.BEAR_HUNTER_TP_RR * (current_price - sl)
    rr = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
    
    vol_4h = df_4h['volume'].values
    current_hour = df_4h.index[-1].hour
    session_bars = df_4h[df_4h.index.hour == current_hour]
    avg_vol_prev = float(session_bars.iloc[:-1]['volume'].mean()) if len(session_bars) >= 2 else float(np.mean(vol_4h[-config.BIST_CHART_RVOL_LOOKBACK:-1]))
    if avg_vol_prev <= 0:
        avg_vol_prev = 1.0
    
    _scores_pattern = build_trend_scores(
        adx=df_4h['ADX_14'].iloc[-1] if 'ADX_14' in df_4h.columns else 25.0,
        adx_prev=df_4h['ADX_14'].iloc[-2] if len(df_4h) >= 2 and 'ADX_14' in df_4h.columns else 25.0,
        price=current_price,
        ema_fast=df_4h['EMA_8'].iloc[-1] if 'EMA_8' in df_4h.columns else None,
        ema_mid=df_4h['EMA_21'].iloc[-1] if 'EMA_21' in df_4h.columns else None,
        ema_slow=df_1d['SMA_50'].iloc[-1] if 'SMA_50' in df_1d.columns else None,
        rsi=df_4h['RSI_14'].iloc[-1] if 'RSI_14' in df_4h.columns else 50.0,
        rsi_prev=df_4h['RSI_14'].iloc[-2] if len(df_4h) >= 2 and 'RSI_14' in df_4h.columns else 50.0,
        volume=vol_4h[-1], vol_sma=avg_vol_prev, dollar_vol=vol_4h[-1] * current_price,
        rr=rr, has_engulfing=False, regime=bist_regime,
        macro_aligned=not xu100_down, consecutive_sl=_get_consecutive_sl(symbol), market="BIST"
    )
    # Manual double penalty removed: volume checks are handled by conviction scorer / autopsy penalty
    if is_weak:
        _scores_pattern["setup_weak_penalty"] = -8.0
        
    _conv_pattern = calculate_conviction(_scores_pattern, ctx=ctx)
    if _conv_pattern.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        raw_vars = locals()
        return {
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "BIST", "strategy": "BIST LONG 12: GRAFİK FORMASYONLARI (CHART PATTERNS)", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": tp,
            "conviction_score": _conv_pattern.total_score, "conviction_grade": _conv_pattern.grade,
            "conviction_details": _conv_pattern.component_scores, "position_size_pct": _conv_pattern.position_size_pct,
            "body_close_stop_required": True, "timeframe": "4h",
            "reason": (
                f"📈 Grafik Formasyonu Kırılımı: {pattern_name}\n"
                f"ℹ️ Detay: {pattern_details.get('details', 'N/A')}\n"
                f"📊 Hacim Teyidi: {vol_4h[-1] / avg_vol_prev:.1f}x (Eşik: {config.BIST12_VOLUME_MULT:.1f}x)\n"
                f"🎯 R:R Oranı: {rr:.2f}:1"
            ) + _conv_pattern.to_reason_suffix()
        }
    return None

def _check_bist_12_chart_patterns(ctx):
    signals = []
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    current_price = ctx["current_price"]

    try:
        if len(df_4h) < 30:
            return signals

        from indicators import detect_chart_patterns
        pattern_name, pattern_details = detect_chart_patterns(df_4h)
        if not pattern_name or pattern_details.get("signal") != "AL":
            return signals

        is_weak = not _check_bist12_timing_filters(df_1d, df_4h, current_price)

        sig = _build_bist12_signal(ctx, pattern_name, pattern_details, is_weak)
        if sig:
            signals.append(sig)
    except Exception as e:
        logging.warning(f"[analyze_strategies_bist] BIST 12 Hata: {e}", exc_info=True)
    return signals


# 1. BIST 100 STRATEJİ MOTORU
def analyze_strategies_bist(symbol, df_1d, df_4h, df_1h, xu100_down=False, xu100_daily=None, metrics_collector=None):
    signals = []

    # Pandas Mutability koruması
    df_1d = df_1d.copy()
    df_4h = df_4h.copy()
    df_1h = df_1h.copy()

    df_1d.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_1d.ta.ema(length=config.IND_EMA_FAST, append=True)
    df_1d.ta.ema(length=config.IND_EMA_21, append=True)
    df_1d.ta.sma(length=config.IND_SMA_SLOW, append=True)
    df_1d.ta.sma(length=config.IND_SMA_TREND, append=True)
    df_1d.ta.bbands(length=config.IND_BBANDS_LENGTH, std=config.IND_BBANDS_STD, append=True)
    df_1d.ta.atr(length=config.IND_ATR_LENGTH, append=True)

    month_high = df_1d['high'].tail(config.BIST_MONTH_HIGH_LOOKBACK).max() if len(df_1d) >= config.BIST_MONTH_HIGH_LOOKBACK else df_1d['high'].max()

    df_4h.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    df_4h.ta.ema(length=config.IND_EMA_FAST, append=True)
    df_4h.ta.ema(length=config.IND_EMA_21, append=True)
    df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_4h.ta.atr(length=config.IND_ATR_LENGTH, append=True)

    df_1h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_1h.ta.ema(length=config.IND_EMA_FAST, append=True)
    df_1h.ta.ema(length=config.IND_EMA_21, append=True)
    df_1h['vol_sma_20'] = ta.sma(df_1h['volume'], length=config.IND_VOL_SMA_LENGTH)

    if len(df_1d) < 50 or len(df_4h) < 20 or len(df_1h) < 20:
        return signals

    last_1d = df_1d.iloc[-1]
    last_4h = df_4h.iloc[-1]
    last_1h = df_1h.iloc[-1]
    prev_1h = df_1h.iloc[-2]
    current_price = last_1h['close']

    # 1. HARD BLOCK - Likidite ve Fiyat Filtreleri
    volume_ma20_tl = (df_1d['close'] * df_1d['volume']).rolling(20).mean()
    if volume_ma20_tl.empty or pd.isna(volume_ma20_tl.iloc[-1]) or volume_ma20_tl.iloc[-1] < config.BIST_SWING_MIN_VOLUME_TL:
        return signals
    if current_price < config.BIST_MIN_STOCK_PRICE_TL:
        pass # return signals

    atr_val = last_1d.get('ATRr_14', last_1d.get('ATR_14'))
    if atr_val is None or pd.isna(atr_val):
        atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
        
    raw_sl_dist = ATR_MULTIPLIER_BIST * atr_val
    dynamic_sl_dist = max(
        min(raw_sl_dist, current_price * ATR_CAP_BIST),
        current_price * config.BIST_MIN_SL_PCT
    )
    sl_pct = (dynamic_sl_dist / current_price) * 100
    bist_regime = _get_bist_regime(xu100_daily)

    if metrics_collector is not None:
        metrics_collector[symbol] = {
            "Symbol": symbol, "Market": "BIST", "Price": current_price,
            "1D RSI": round(last_1d.get("RSI_14", 0), 2) if pd.notna(last_1d.get("RSI_14")) else None,
            "4H ADX": round(last_4h.get("ADX_14", 0), 2) if pd.notna(last_4h.get("ADX_14")) else None,
            "1H RSI": round(last_1h.get("RSI_14", 0), 2) if pd.notna(last_1h.get("RSI_14")) else None,
            "1D SMA 50": round(last_1d.get("SMA_50", 0), 2) if pd.notna(last_1d.get("SMA_50")) else None,
            "1D Trend SMA": round(get_trend_sma(last_1d), 2) if pd.notna(get_trend_sma(last_1d)) else None,
            "Trend": "Bullish" if last_1d.get("EMA_8", 0) > last_1d.get("EMA_21", float('inf')) else "Bearish",
            "1H Volume": last_1h.get("volume")
        }

    # Context packaging to keep signatures clean & A-Grade
    ctx = {
        "symbol": symbol, "last_1d": last_1d, "last_4h": last_4h, "last_1h": last_1h, "prev_1h": prev_1h,
        "current_price": current_price, "dynamic_sl_dist": dynamic_sl_dist, "sl_pct": sl_pct,
        "bist_regime": bist_regime, "xu100_down": xu100_down, "xu100_daily": xu100_daily,
        "df_1d": df_1d, "df_4h": df_4h, "df_1h": df_1h, "month_high": month_high
    }

    signals.extend(_check_bist_1_dip_hunter(ctx))
    signals.extend(_check_bist_2_trend_following(ctx))
    signals.extend(_check_bist_3_squeeze_breakout(ctx))
    signals.extend(_check_bist_4_sniper_ote(ctx))
    signals.extend(_check_bist_5_vol_squeeze(ctx))
    signals.extend(_check_bist_6_rs(ctx))
    signals.extend(_check_bist_7_vwap(ctx))
    signals.extend(_check_bist_8_obv(ctx))
    signals.extend(_check_bist_10_sniper(ctx))
    signals.extend(_check_bist_11_candlestick(ctx))
    signals.extend(_check_bist_12_chart_patterns(ctx))

    return signals


# 2. BIST 9: ZAMAN KAFESİ (ORB)
def _check_orb_long(symbol, current_price, cage_high, cage_low, cage_mid, today_vwap, rvol, last, ema21, df_15m, cage_width_pct):
    signals = []
    bist100_trend = get_bist100_trend()
    bist100_intraday_trend = get_bist100_intraday_trend()

    if current_price > cage_high and last['close'] > last['open']:
        if bist100_trend == "BEAR" or bist100_intraday_trend == "BEAR":
            return signals

        body_ok = not config.ORB_BODY_CLOSE_REQUIRED or (last['close'] > cage_high)
        vol_ok = last['volume'] >= rvol * config.ORB_VOLUME_MULT
             
        if body_ok and vol_ok:
            entry_price = cage_high * config.BIST_ORB_LONG_ENTRY_OFFSET if not config.ORB_BODY_CLOSE_REQUIRED else current_price
            _sl9u = cage_mid
            _risk9u = entry_price - _sl9u
            _tp9u = entry_price + (_risk9u * 2.0)
            
            ctx = {"symbol": symbol}
            df_15m_copy = df_15m.copy()
            df_15m_copy.ta.rsi(length=14, append=True)
            rsi_val = float(df_15m_copy['RSI_14'].iloc[-1]) if 'RSI_14' in df_15m_copy.columns and not pd.isna(df_15m_copy['RSI_14'].iloc[-1]) else 50.0
            rsi_prev_val = float(df_15m_copy['RSI_14'].iloc[-2]) if 'RSI_14' in df_15m_copy.columns and len(df_15m_copy) >= 2 and not pd.isna(df_15m_copy['RSI_14'].iloc[-2]) else rsi_val
            
            raw_vars = locals()
            
            _scores9u = build_breakout_scores(
                bb_width=None, price=entry_price,
                ema_fast=ema21, ema_mid=today_vwap, ema_slow=None,
                volume=last['volume'], vol_sma=rvol, dollar_vol=last['volume'] * entry_price,
                rr=2.0, regime="BULL",
                macro_aligned=True, consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
                rsi=rsi_val, rsi_prev=rsi_prev_val
            )
            _conv9u = calculate_conviction(_scores9u, ctx=ctx)
            if _conv9u.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "BIST",
                    "strategy": "BIST LONG 9: ZAMAN KAFESİ (ORB)", "signal": "AL", "is_day_trade": True,
                    "entry_price": entry_price, "sl": _sl9u, "tp": _tp9u,
                    "conviction_score": _conv9u.total_score, "conviction_grade": _conv9u.grade,
                    "conviction_details": _conv9u.component_scores, "position_size_pct": _conv9u.position_size_pct,
                    "reason": (
                        f"⏱️ Açılış Kafesi Kırılımı (ORB)\n"
                        f"📊 Kafes: {cage_low:.2f} - {cage_high:.2f} (Genişlik: %{cage_width_pct:.2f})\n"
                        f"📍 Fiyat: {entry_price:.2f} TL (EMA21: {ema21:.2f}, VWAP: {today_vwap:.2f})\n"
                        f"📈 Hacim: {last['volume']:,.0f} (Ort. RVOL: {rvol:,.0f}, Oran: {last['volume']/max(rvol, 1e-8):.2f}x)\n"
                        f"🎯 Hedef: +{_tp9u-entry_price:.2f} TL\n"
                        f"⚠️ DAY TRADE: 17:55'te otomatik kapatılır."
                    ) + _conv9u.to_reason_suffix()
                })
    return signals


def _check_orb_short(symbol, current_price, cage_high, cage_low, cage_mid, today_vwap, rvol, last, ema21, df_15m, cage_width_pct):
    signals = []
    bist100_trend = get_bist100_trend()
    bist100_intraday_trend = get_bist100_intraday_trend()

    if current_price < cage_low and last['close'] < last['open']:
        if bist100_trend == "BULL" or bist100_intraday_trend == "BULL":
            return signals

        body_ok = not config.ORB_BODY_CLOSE_REQUIRED or (last['close'] < cage_low)
        vol_ok = last['volume'] >= rvol * config.ORB_VOLUME_MULT
             
        if body_ok and vol_ok:
            entry_price = cage_low * config.BIST_ORB_SHORT_ENTRY_OFFSET if not config.ORB_BODY_CLOSE_REQUIRED else current_price
            _sl9d = cage_mid
            _risk9d = _sl9d - entry_price
            _tp9d = entry_price - (_risk9d * 2.0)
            
            ctx = {"symbol": symbol}
            df_15m_copy = df_15m.copy()
            df_15m_copy.ta.rsi(length=14, append=True)
            rsi_val = float(df_15m_copy['RSI_14'].iloc[-1]) if 'RSI_14' in df_15m_copy.columns and not pd.isna(df_15m_copy['RSI_14'].iloc[-1]) else 50.0
            rsi_prev_val = float(df_15m_copy['RSI_14'].iloc[-2]) if 'RSI_14' in df_15m_copy.columns and len(df_15m_copy) >= 2 and not pd.isna(df_15m_copy['RSI_14'].iloc[-2]) else rsi_val
            
            raw_vars = locals()
            
            _scores9d = build_breakout_scores(
                bb_width=None, price=entry_price, ema_fast=today_vwap, ema_mid=ema21, ema_slow=None,
                volume=last['volume'], vol_sma=rvol, dollar_vol=last['volume'] * entry_price,
                rr=2.0, regime="BEAR", macro_aligned=True,
                consecutive_sl=_get_consecutive_sl(symbol), market="BIST",
                is_long=False, rsi=rsi_val, rsi_prev=rsi_prev_val
            )
            _conv9d = calculate_conviction(_scores9d, ctx=ctx)
            if _conv9d.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "BIST",
                    "strategy": "BIST SHORT 9: ZAMAN KAFESİ (ORB)", "signal": "SAT", "is_day_trade": True,
                    "entry_price": entry_price, "sl": _sl9d, "tp": _tp9d,
                    "conviction_score": _conv9d.total_score, "conviction_grade": _conv9d.grade,
                    "conviction_details": _conv9d.component_scores, "position_size_pct": _conv9d.position_size_pct,
                    "reason": (
                        f"⏱️ Açılış Kafesi Aşağı Kırılımı (ORB)\n"
                        f"📊 Kafes: {cage_low:.2f} - {cage_high:.2f} (Genişlik: %{cage_width_pct:.2f})\n"
                        f"📍 Fiyat: {entry_price:.2f} TL (EMA21: {ema21:.2f}, VWAP: {today_vwap:.2f})\n"
                        f"📈 Hacim: {last['volume']:,.0f} (Ort. RVOL: {rvol:,.0f}, Oran: {last['volume']/max(rvol, 1e-8):.2f}x)\n"
                        f"🎯 Hedef: -{entry_price-_tp9d:.2f} TL\n"
                        f"⚠️ DAY TRADE: 17:55'te otomatik kapatılır."
                    ) + _conv9d.to_reason_suffix()
                })
    return signals


def scan_orb_bist(symbol, df_15m):
    """BIST 9: ZAMAN KAFESİ (ORB) taraması."""
    signals = []
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    start_time = now.replace(hour=config.BIST9_TRADE_START_HOUR, minute=config.BIST9_TRADE_START_MINUTE, second=0, microsecond=0)
    end_time = now.replace(hour=config.BIST9_TRADE_END_HOUR, minute=config.BIST9_TRADE_END_MINUTE, second=0, microsecond=0)
    
    if not (start_time <= now <= end_time):
        return signals

    cage_high, cage_low, cage_mid, today_vwap = calculate_orb_cage(df_15m)
    if cage_high is None or today_vwap is None:
        return signals

    last = df_15m.iloc[-1]
    current_price = float(last['close'])
    tp_range = cage_high - cage_low
    cage_width_pct = (tp_range / cage_low) * 100
    if cage_width_pct > config.BIST9_MAX_CAGE_WIDTH_PCT:
        return signals

    candle_time = df_15m.index[-1]
    rvol = calculate_time_specific_rvol(df_15m, target_hour=candle_time.hour, target_minute=candle_time.minute, period=config.BIST9_RVOL_PERIOD)

    df = df_15m.copy()
    df.ta.ema(length=config.BIST9_EMA_LENGTH, append=True)
    ema21 = float(df[f'EMA_{config.BIST9_EMA_LENGTH}'].iloc[-1])
    if math.isnan(ema21):
        return signals

    signals.extend(_check_orb_long(symbol, current_price, cage_high, cage_low, cage_mid, today_vwap, rvol, last, ema21, df_15m, cage_width_pct))
    signals.extend(_check_orb_short(symbol, current_price, cage_high, cage_low, cage_mid, today_vwap, rvol, last, ema21, df_15m, cage_width_pct))

    return signals

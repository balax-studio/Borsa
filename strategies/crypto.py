"""
strategies/crypto.py — Kripto Strateji Katmanı
Tüm Kripto strateji fonksiyonları.
"""
import math
import pandas as pd
import pandas_ta as ta
import config
from indicators.smc import detect_supply_zones, is_price_in_supply_zone

from config import (
    ATR_MULTIPLIER_CRYPTO, ATR_CAP_CRYPTO,
)
from conviction_scorer import (
    calculate_conviction,
    build_trend_scores, build_dip_scores, build_breakout_scores,
    build_short_scores, build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH, SNIPER_CRYPTO_WEIGHTS,
    check_hard_blocks,
)
from indicators import (
    sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    detect_bullish_divergence, detect_bearish_divergence,
    detect_vwap_bounce, detect_obv_accumulation,
    detect_squeeze, calculate_cmf, sniper_calculate_ote_body,
    sniper_calculate_ote, calculate_anchored_vwap, get_trend_sma,
)
from data_sources import (
    get_crypto_1h_data, get_funding_rate, fetch_crypto_oi_crash,
    get_btc_dominance_trend, check_btc_not_pumping, check_token_unlocks,
    get_btc_rsi_and_change,
)
from .helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _is_meaningful_volume,
    _get_consecutive_sl, _has_absolute_hourly_volume, _get_darth_maul_ratio,
_is_funding_safe_for_short,
)


def apply_5x_sl_cap(sl: float, current_price: float, ctx: dict = None) -> float:
    """
    5x kaldıraç için SL mesafesini %2 ile sınırlar ve
    eğer mesafe %2'yi aşıyorsa dinamik/orantılı soft ceza uygular.
    """
    original_dist_pct = abs(current_price - sl) / current_price
    
    if original_dist_pct > 0.02:
        if ctx is not None:
            # Kripto oynaklığı yüksek olduğu için mesafe sınırı cezalandırılmaz, sadece SL cap'lenir.
            ctx["sl_distance_penalty"] = 0.0
            
        max_dist = current_price * 0.02
        if sl < current_price:
            return current_price - max_dist
        else:
            return current_price + max_dist
    else:
        if ctx is not None:
            ctx["sl_distance_penalty"] = 0.0
        return sl
def _check_crypto_1_liquidation(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_1d = ctx["last_1d"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    btc_ok = ctx["btc_ok"]

    has_needed = (
        not pd.isna(last_4h.get('RSI_14')) and
        not pd.isna(last_4h.get('EMA_20')) and
        not pd.isna(last_4h.get('vol_sma_20'))
    )
    if not has_needed:
        return signals

    ema_50_1d = last_1d.get(f'EMA_{config.IND_EMA_SLOW}') if last_1d is not None else None
    trend_aligned = not config.DIP_RSI_1D_EMA50_ALIGN_ENABLED or (
        ema_50_1d is not None and not pd.isna(ema_50_1d) and current_price > ema_50_1d
    )
    if not trend_aligned:
        return signals

    div_found, _, _, _, _ = detect_bullish_divergence(df_4h)
    if not div_found:
        return signals

    guarded_vol_sma = _apply_volume_sma_guard(df_4h, last_4h['vol_sma_20'])
    volume_spike_ok = not config.DIP_VOLUME_SPIKE_REQUIRED or (
        last_4h['volume'] >= guarded_vol_sma * config.DIP_VOLUME_SPIKE_MULT
    )
    if not volume_spike_ok:
        return signals

    if not _is_meaningful_volume(last_4h['volume'], guarded_vol_sma, current_price, "KRIPTO"):
        return signals

    # Golden Filter (Iteration 1)
    if last_4h.get('ADX_14', 100) >= 29.9580:
        return signals

    # Golden Filter (Iteration 2)
    if last_4h.get('ADX_14', 0) <= 22.0293:
        return signals

    # Golden Filter: ADX > 64.14 (Relaxed/Removed to increase signals)
    # if last_4h.get('ADX_14', 0) <= 64.14:
    #     return signals

    if current_price > last_4h['open']:
        oi_crash = fetch_crypto_oi_crash(symbol)
        
        lowest_wick = last_4h['low']
        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
        if atr_val is None or pd.isna(atr_val):
            atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
        dynamic_mult = ctx.get("dynamic_atr_mult", config.ATR_MULTIPLIER_CRYPTO)
        raw_atr_sl = dynamic_mult * atr_val
        
        sl = lowest_wick - raw_atr_sl
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = abs(current_price - sl)
        tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
        _rr_c1 = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
        _prev_4h = df_4h.iloc[-2] if len(df_4h) >= 2 else last_4h
        dm_ratio = _get_darth_maul_ratio(last_4h)
        
        raw_vars = locals()
        
        _scores_c1 = build_dip_scores(
            rsi_daily=last_4h.get('RSI_14', 50), rsi_hourly=last_4h.get('RSI_14', 50),
            rsi_prev=_prev_4h.get('RSI_14', 50),
            price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'),
            volume=last_4h['volume'], vol_sma=guarded_vol_sma, dollar_vol=last_4h['volume'] * current_price,
            rr=_rr_c1, has_engulfing=False, regime="BULL",
            macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
            dg_is_darth_maul=dm_ratio,
            oi_crash=oi_crash
        )
        _conv_c1 = calculate_conviction(_scores_c1, ctx=ctx)
        if _conv_c1.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            reason_str = "4S Pozitif Uyumsuzluk + Hacim Zirvesi"
            if oi_crash:
                reason_str += " + OI Çöküşü (Balina Alımı)"
            reason_str += "." + _conv_c1.to_reason_suffix()

            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx.get("last_1d"),
                "strategy": "KRİPTO LONG 1: LİKİDASYON AVI", "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": tp,
                "conviction_score": _conv_c1.total_score, "conviction_grade": _conv_c1.grade,
                "conviction_details": _conv_c1.component_scores, "position_size_pct": _conv_c1.position_size_pct,
                "reason": reason_str
            })
    return signals


def _check_mega_trend_1d_squeeze(last_1d, df_1d):
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


def _check_mega_trend_1d_trend(last_1d):
    ema_mid_val = last_1d.get(f'EMA_{config.IND_EMA_MID}')
    ema_slow_val = last_1d.get(f'EMA_{config.IND_EMA_SLOW}')
    if ema_mid_val is None or ema_slow_val is None or pd.isna(ema_mid_val) or pd.isna(ema_slow_val):
        return False
    return ema_mid_val > ema_slow_val and last_1d['close'] > ema_mid_val


def _check_mega_trend_4h_indicators(last_4h, current_price):
    atr_col = 'ATRr_14' if 'ATRr_14' in last_4h.index else 'ATR_14'
    if pd.isna(last_4h.get('ADX_14')) or pd.isna(last_4h.get('EMA_20')) or pd.isna(last_4h.get(atr_col)):
        return False
    # HARD FILTER REMOVED: ADX Threshold delegated to conviction_scorer
    # if last_4h['ADX_14'] <= config.CRYPTO_TREND_ADX_MIN:
    #     return False
    ema_mid_4h = last_4h.get(f'EMA_{config.IND_EMA_MID}')
    if ema_mid_4h is None or pd.isna(ema_mid_4h):
        return False
    is_pullback = (
        last_4h['low'] <= ema_mid_4h and
        current_price > ema_mid_4h and
        current_price > last_4h['open']
    )
    return is_pullback and not pd.isna(last_4h.get('vol_sma_20'))


def _is_mega_trend_valid(last_1d, last_4h, df_1d, df_4h, current_price):
    if pd.isna(last_1d.get('EMA_20')) or pd.isna(last_1d.get('EMA_50')):
        return False
    if not _check_mega_trend_1d_squeeze(last_1d, df_1d):
        return False
    if not _check_mega_trend_1d_trend(last_1d):
        return False
    return _check_mega_trend_4h_indicators(last_4h, current_price)


def _check_crypto_2_mega_trend(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_1d = ctx["last_1d"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_1d = ctx["df_1d"]
    df_4h = ctx["df_4h"]
    btc_ok = ctx["btc_ok"]

    if not _is_mega_trend_valid(last_1d, last_4h, df_1d, df_4h, current_price):
        return signals

    guarded_vol_sma = _apply_volume_sma_guard(df_4h, last_4h['vol_sma_20'])
    if last_4h['volume'] < guarded_vol_sma * config.CRYPTO_TREND_VOLUME_SMA_MULT:
        return signals
    if not _is_meaningful_volume(last_4h['volume'], guarded_vol_sma, current_price, "KRIPTO"):
        return signals

    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO LONG 2: MEGA TREND TAKİBİ
    # Filter: Relative_Volume < 6.3000 (+8.00R Improvement)
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
            "strategy": "KRİPTO LONG 2: MEGA TREND TAKİBİ", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": _tp_c2,
            "conviction_score": _conv_c2.total_score, "conviction_grade": _conv_c2.grade,
            "conviction_details": _conv_c2.component_scores, "position_size_pct": _conv_c2.position_size_pct,
            "reason": f"1G EMA20>50 Trendi. BTC Dominans '{btcdom_trend}' yönünde{btcdom_warning}. Hacim onaylı. ATR Stop aktif." + _conv_c2.to_reason_suffix()
        })
    return signals


def _is_breakout_setup(symbol, last_4h, current_price, df_1d, df_4h):
    bb_upper_col = [c for c in df_1d.columns if 'BBU' in c]
    bb_lower_col = [c for c in df_1d.columns if 'BBL' in c]
    bb_mid_col = [c for c in df_1d.columns if 'BBM' in c]

    if not bb_upper_col:
        return False, 0.0
    if not bb_lower_col:
        return False, 0.0
    if not bb_mid_col:
        return False, 0.0

    df_1d['bb_width'] = (df_1d[bb_upper_col[0]] - df_1d[bb_lower_col[0]]) / df_1d[bb_mid_col[0]]
    min_width_30d = df_1d['bb_width'].tail(config.CRYPTO_BREAKOUT_LOOKBACK).min()
    last_width = df_1d['bb_width'].iloc[-1]

    if last_width > min_width_30d * config.CRYPTO_BREAKOUT_WIDTH_MULT:
        return False, 0.0

    vol_sma = last_4h.get('vol_sma_20')
    if pd.isna(vol_sma):
        return False, 0.0
    if last_4h['volume'] <= config.CRYPTO_BREAKOUT_VOLUME_MULT * vol_sma:
        return False, 0.0

    return True, last_width


def _is_breakout_retest_valid(symbol, last_4h, current_price, df_4h):
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


def _check_crypto_3_breakout(ctx):
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

    # HARD FILTER REMOVED: RSI and ADX constraints delegated to conviction_scorer
    # if last_4h.get('RSI_14', 0) >= config.CRYPTO_RETEST_RSI_MAX:
    #     return signals
    # if last_4h.get('ADX_14', 0) < config.CRYPTO_RETEST_ADX_MIN:
    #     return signals

    ok_setup, last_width = _is_breakout_setup(symbol, last_4h, current_price, df_1d, df_4h)
    if not ok_setup:
        return signals

    ok_retest, local_high = _is_breakout_retest_valid(symbol, last_4h, current_price, df_4h)
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
            "strategy": "KRİPTO LONG 3: SAHTE KIRILIM FİLTRESİ (RETEST)", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": _tp_c3,
            "conviction_score": _conv_c3.total_score, "conviction_grade": _conv_c3.grade,
            "conviction_details": _conv_c3.component_scores, "position_size_pct": _conv_c3.position_size_pct,
            "reason": f"1G Daralma, Retest sekmesi. Fonlama: %{funding_rate:.4f}. Hacim: Onaylı." + _conv_c3.to_reason_suffix()
        })
    return signals


def _check_crypto_short_1_liquidity_hunt(ctx):
    signals = []

    # Anti-Rekt HTF Trend filtresi (Güçlü boğa trendinde short arama)
    last_1d = ctx.get('last_1d')
    if last_1d is not None:
        import pandas as pd
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
    
    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    in_supply = is_price_in_supply_zone(current_price, supply_zones)
    swing_highs = sniper_find_swing_points(df_4h, point_type='high')
    sweep_ok, sweep_high = sniper_detect_sweep(df_4h, swing_highs, point_type='high')
    if not sweep_ok:
        return signals

    swing_lows = sniper_find_swing_points(df_4h, point_type='low')
    msb_ok, msb_low, _ = sniper_detect_msb(df_4h, swing_lows, point_type='low')
    if not msb_ok:
        return signals

    # OTE (Optimal Trade Entry) kuralı
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
        
    # Golden Filter (Iteration 1)
    if last_4h.get('Vortex_Diff', 0) >= -0.1531:
        return signals

    # Golden Filter (Iteration 2)
    if last_4h.get('ADX_14', 0) <= 15.0902:
        return signals

    # Golden Filter (Iteration 3)
    if last_4h.get('RSI_14', 0) <= 36.6882:
        return signals
        
    _conv = calculate_conviction(_scores, ctx=ctx)
    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        funding_str = f" | Fonlama: %{funding_rate:.4f}" if funding_rate is not None else ""
        signals.append({
            'raw_indicators': _extract_raw_indicators(raw_vars),
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 1: LİKİDİTE AVI (SFP+CHoCH)', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'SFP (Likidite Avı) + CHoCH Onaylı. 1:{config.CRYPTO_SHORT_MIN_RR} R:R. OTE Bölgesi ({ote_bottom:.4f}-{ote_top:.4f}){funding_str}.' + _conv.to_reason_suffix()
        })
    return signals

def _check_crypto_short_2_oi_trap(ctx):
    signals = []
    symbol = ctx['symbol']
    last_4h = ctx['last_4h']
    current_price = ctx['current_price']
    df_4h = ctx['df_4h']

    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO SHORT 2: OI & TÜREV TUZAĞI
    # Filter: Vortex_Diff > -0.3613 (+43.00R Improvement)
    if last_4h.get('Vortex_Diff', 0) <= -0.3613:
        return signals

    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    in_supply = is_price_in_supply_zone(current_price, supply_zones)
    funding_rate = get_funding_rate(symbol)
    if funding_rate is None or funding_rate < config.CRYPTO_SHORT2_FUNDING_MIN:
        return signals

    cmf_val = calculate_cmf(df_4h)
    if cmf_val is None or cmf_val > 0:
        return signals

    ema_20 = last_4h.get('EMA_20')
    ema_50 = last_4h.get('EMA_50')
    if pd.notna(ema_20) and pd.notna(ema_50) and ema_20 > ema_50:
        return signals

    vol_sma = last_4h.get('vol_sma_20', 0)
    if vol_sma > 0 and last_4h.get('volume', 0) < (vol_sma * 1.5):
        return signals

    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
    if pd.isna(atr_val): atr_val = current_price * 0.02

    sl = last_4h['high'] + (atr_val * 1.00)
    sl = apply_5x_sl_cap(sl, current_price, ctx)
    sl_dist = max(sl - current_price, 1e-8)
    tp = current_price - (sl_dist * 2.25)
    _rr = abs(current_price - tp) / sl_dist
    if _rr < config.CRYPTO_SHORT_MIN_RR:
        return signals

    _adx_prev = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
    raw_vars = locals()
    _scores = build_short_scores(
        adx=last_4h.get('ADX_14'), adx_prev=_adx_prev,
        price=current_price, ema_fast=last_4h.get('EMA_20'), ema_mid=last_4h.get('EMA_50'), ema_slow=None,
        rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
        volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20'), dollar_vol=last_4h.get('volume', 0) * current_price,
        rr=_rr, has_engulfing=False, regime='BEAR', macro_aligned=True,
        consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO', funding_rate=funding_rate,
        strategy_type='MEAN_REVERSION'
    )
    if not in_supply:
        _scores["conflict_penalty"] -= 15.0
    _conv = calculate_conviction(_scores, ctx=ctx)
    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            'raw_indicators': _extract_raw_indicators(raw_vars),
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 2: OI & TÜREV TUZAĞI', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'Yüksek Fonlama (+%{funding_rate:.4f}) + CMF < 0 Balina Satışı. 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
    return signals

def _check_crypto_short_3_divergence(ctx):
    signals = []
    symbol = ctx['symbol']
    last_4h = ctx['last_4h']
    current_price = ctx['current_price']
    df_4h = ctx['df_4h']

    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    in_supply = is_price_in_supply_zone(current_price, supply_zones)
    div_found, _, _, _, _ = detect_bearish_divergence(df_4h)
    if not div_found:
        return signals

    is_bearish_engulfing = (last_4h['close'] < last_4h['open']) and (df_4h.iloc[-2]['close'] > df_4h.iloc[-2]['open']) and (last_4h['open'] >= df_4h.iloc[-2]['close']) and (last_4h['close'] < df_4h.iloc[-2]['open'])
    upper_wick = last_4h['high'] - max(last_4h['close'], last_4h['open'])
    body = abs(last_4h['close'] - last_4h['open'])
    is_pin_bar = upper_wick > (body * 2) and last_4h['close'] < last_4h['open']

    if not (is_bearish_engulfing or is_pin_bar):
        return signals

    ema_20 = last_4h.get('EMA_20')
    ema_50 = last_4h.get('EMA_50')
    if pd.notna(ema_20) and pd.notna(ema_50) and ema_20 > ema_50:
        return signals

    vol_sma = last_4h.get('vol_sma_20', 0)
    if vol_sma > 0 and last_4h.get('volume', 0) < (vol_sma * 1.5):
        return signals

    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
    if pd.isna(atr_val): atr_val = current_price * 0.02

    sl = last_4h['high'] + (atr_val * config.ATR_MULTIPLIER_CRYPTO)
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
        volume=last_4h.get('volume', 0), vol_sma=last_4h.get('vol_sma_20'), dollar_vol=last_4h.get('volume', 0) * current_price,
        rr=_rr, has_engulfing=is_bearish_engulfing, regime='BEAR', macro_aligned=True,
        consecutive_sl=_get_consecutive_sl(symbol), market='KRIPTO',
        strategy_type='MEAN_REVERSION'
    )
    if not in_supply:
        _scores["conflict_penalty"] -= 15.0
    _conv = calculate_conviction(_scores, ctx=ctx)
    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            'raw_indicators': _extract_raw_indicators(raw_vars),
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 3: MAJÖR DİRENÇ UYUMSUZLUĞU', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'RSI/MACD Negatif Uyumsuzluk + Dönüş Mumu Teyidi. 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
    return signals

def _check_crypto_short_4_sr_flip(ctx):
    signals = []
    symbol = ctx['symbol']
    last_4h = ctx['last_4h']
    current_price = ctx['current_price']
    df_4h = ctx['df_4h']

    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    in_supply = is_price_in_supply_zone(current_price, supply_zones)
    if len(df_4h) < 30: return signals

    recent_lows = df_4h['low'].rolling(window=20).min().shift(5)
    support_level = recent_lows.iloc[-1]
    
    retest_zone = support_level * (1 - config.CRYPTO_SHORT4_RETEST_TOLERANCE)
    ema_20 = last_4h.get('EMA_20')
    ema_50 = last_4h.get('EMA_50')
    vol_sma = last_4h.get('vol_sma_20', 0)
    
    is_above_support = current_price > support_level
    is_below_retest = current_price < retest_zone
    is_ema_bullish = pd.notna(ema_20) and pd.notna(ema_50) and ema_20 > ema_50
    is_high_vol = vol_sma > 0 and last_4h.get('volume', 0) > vol_sma
    is_green_candle = last_4h['close'] >= last_4h['open']

    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
    if pd.isna(atr_val): atr_val = current_price * 0.02

    sl = support_level + (atr_val * config.ATR_MULTIPLIER_CRYPTO)
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
        strategy_type='PULLBACK'
    )
    if not in_supply:
        _scores["conflict_penalty"] -= 15.0
    if is_above_support: _scores["conflict_penalty"] -= 10.0
    if is_below_retest: _scores["conflict_penalty"] -= 5.0
    if is_ema_bullish: _scores["conflict_penalty"] -= 10.0
    if is_high_vol: _scores["conflict_penalty"] -= 10.0
    if is_green_candle: _scores["conflict_penalty"] -= 5.0
    
    # Golden Filter (Iteration 1)
    if last_4h.get('ADX_14', 0) <= 23.1613:
        return signals

    # Golden Filter (Iteration 2)
    if last_4h.get('RSI_14', 0) <= 40.8206:
        return signals

    _conv = calculate_conviction(_scores, ctx=ctx)
    if _conv.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            'raw_indicators': _extract_raw_indicators(raw_vars),
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 4: S/R FLIP RETEST', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'Kırılan Destek Dirence Dönüştü (Retest). Zayıf Hacim. 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
    return signals

def _check_crypto_short_5_bear_flag(ctx):
    signals = []

    # Anti-Rekt HTF Trend filtresi (Güçlü boğa trendinde short arama)
    last_1d = ctx.get('last_1d')
    if last_1d is not None:
        import pandas as pd
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

    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    in_supply = is_price_in_supply_zone(current_price, supply_zones)
    ema_20 = last_4h.get('EMA_20')
    ema_50 = last_4h.get('EMA_50')
    ema_200 = last_4h.get('EMA_200') or last_4h.get('SMA_200')
    
    if pd.isna(ema_20) or pd.isna(ema_50) or pd.isna(ema_200): return signals
    if not (ema_20 < ema_50 < ema_200): return signals
    
    if current_price > ema_50: return signals
    
    if last_4h['high'] < ema_20 * 0.98: return signals
    
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
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 5: AYI BAYRAĞI & EMA DİZİLİMİ', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'EMA Ayı Dizilimi + EMA20 Retest Zayıf Hacim (Ayı Bayrağı). 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
    return signals


def _calculate_zlema(series, length):
    lag = int((length - 1) / 2)
    de_lagged = series + (series - series.shift(lag))
    return de_lagged.ewm(span=length, adjust=False).mean()


def _calculate_stoch_rsi(series, rsi_len=14, stoch_len=14, k_len=5, d_len=3):
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


def _check_crypto_short_6_zlema_cross(ctx):
    signals = []
    symbol = ctx['symbol']
    last_4h = ctx['last_4h']
    current_price = ctx['current_price']
    df_4h = ctx['df_4h']
    
    # 1. Trend Filter: Price < EMA 200 (HTF bearish)
    ema_200 = last_4h.get('EMA_200') or last_4h.get('SMA_200')
    if pd.isna(ema_200) or current_price >= ema_200:
        return signals
        
    # 2. ADX Filter: ADX > 30 (strong trend)
    adx = last_4h.get('ADX_14')
    if pd.isna(adx) or adx <= 30:
        return signals
        
    # 3. ZLEMA Calculation
    close_series = df_4h['close']
    zlema_30 = _calculate_zlema(close_series, 30)
    zlema_40 = _calculate_zlema(close_series, 40)
    
    if len(zlema_30) < 2 or len(zlema_40) < 2:
        return signals
        
    # 4. ZLEMA Cross Down condition
    zl30_prev = zlema_30.iloc[-2]
    zl30_curr = zlema_30.iloc[-1]
    zl40_prev = zlema_40.iloc[-2]
    zl40_curr = zlema_40.iloc[-1]
    
    if pd.isna(zl30_curr) or pd.isna(zl40_curr) or pd.isna(zl30_prev) or pd.isna(zl40_prev):
        return signals
        
    if not (zl30_prev >= zl40_prev and zl30_curr < zl40_curr):
        return signals
        
    # 5. Risk Management: Stoploss and Take Profit
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
        
    # 6. Conviction Scoring
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
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 6: ZLEMA ÇAPRAZ KIRILIM', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'ZLEMA(30/40) Aşağı Kesişim + Bearish Trend (Price < EMA200). 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
        
    return signals


def _check_crypto_short_7_stoch_rsi_macd(ctx):
    signals = []
    symbol = ctx['symbol']
    last_4h = ctx['last_4h']
    current_price = ctx['current_price']
    df_4h = ctx['df_4h']
    
    # 1. Trend Filter: Price < EMA 200 (HTF bearish)
    ema_200 = last_4h.get('EMA_200') or last_4h.get('SMA_200')
    if pd.isna(ema_200) or current_price >= ema_200:
        return signals
        
    # 2. ADX Filter: ADX > 25 (strong trend)
    adx = last_4h.get('ADX_14')
    if pd.isna(adx) or adx <= 25:
        return signals
        
    # 3. Calculate StochRSI (14, 5, 3)
    close_series = df_4h['close']
    fastk, fastd = _calculate_stoch_rsi(close_series, 14, 14, 5, 3)
    
    if len(fastk) < 2 or len(fastd) < 2:
        return signals
        
    fk_prev, fk_curr = fastk.iloc[-2], fastk.iloc[-1]
    fd_prev, fd_curr = fastd.iloc[-2], fastd.iloc[-1]
    
    if pd.isna(fk_curr) or pd.isna(fd_curr) or pd.isna(fk_prev) or pd.isna(fd_prev):
        return signals
        
    stoch_cross = (fk_prev >= fd_prev and fk_curr < fd_curr) and (fk_prev > 70 or fd_prev > 70)
    if not stoch_cross:
        return signals
        
    # 4. MACD bearish confirmation
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
        
    # 5. Risk Management: Stoploss and Take Profit
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
        
    # 6. Conviction Scoring
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
            'ticker': symbol, 'market': 'KRIPTO', 'strategy': 'KRİPTO SHORT 7: STOCH RSI & MACD UYUMSUZLUK', 'signal': 'SAT',
            'entry_price': current_price, 'sl': sl, 'tp': tp,
            'conviction_score': _conv.total_score, 'conviction_grade': _conv.grade,
            'conviction_details': _conv.component_scores, 'position_size_pct': _conv.position_size_pct,
            'reason': f'StochRSI Aşağı Kesişim (Aşırı Alım) + Bearish MACD. 1:{config.CRYPTO_SHORT_MIN_RR} R:R.' + _conv.to_reason_suffix()
        })
        
    return signals


def _check_crypto_shorts(ctx):
    signals = []

    btc_not_pumping = check_btc_not_pumping()
    if not btc_not_pumping:
        return signals

    signals.extend(_check_crypto_short_1_liquidity_hunt(ctx))
    signals.extend(_check_crypto_short_2_oi_trap(ctx))
    signals.extend(_check_crypto_short_3_divergence(ctx))
    signals.extend(_check_crypto_short_4_sr_flip(ctx))
    signals.extend(_check_crypto_short_5_bear_flag(ctx))
    signals.extend(_check_crypto_short_6_zlema_cross(ctx))
    signals.extend(_check_crypto_short_7_stoch_rsi_macd(ctx))
    return signals

def _check_crypto_4_sniper_ote_long(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    btc_sniper_bias = ctx["btc_sniper_bias"]

    if btc_sniper_bias not in (1, 0):
        return signals

    swing_lows_s = sniper_find_swing_points(df_4h, point_type="low")
    swing_highs_s = sniper_find_swing_points(df_4h, point_type="high")
    sweep_ok, sweep_low = sniper_detect_sweep(df_4h, swing_lows_s, point_type="low")
    if not sweep_ok:
        # print("FAIL: No sweep")
        return signals

    msb_ok, msb_high, msb_idx = sniper_detect_msb(df_4h, swing_highs_s, point_type="high")
    if not msb_ok:
        # print("FAIL: No MSB")
        return signals

    sweep_idx = swing_lows_s[-1][0] if swing_lows_s else None
    ote_top, ote_bottom = sniper_calculate_ote_body(df_4h, sweep_idx, msb_idx, direction="long")
    if ote_top <= 0 or ote_bottom <= 0 or not (ote_bottom <= current_price <= ote_top):
        # print(f"FAIL: Not in OTE {ote_bottom} < {current_price} < {ote_top}")
        return signals

    has_fvg, _, _ = sniper_detect_fvg(df_4h, ote_top, ote_bottom, direction="bullish")
    if config.SMC_FVG_REQUIRED and not has_fvg:
        # print("FAIL: No FVG")
        return signals

    # ltf_confirm = True
    # df_1h_crypto = None
    # if config.SMC_LTF_MSB_CONFIRM:
    #     df_1h_crypto = get_crypto_1h_data(symbol)
    #     if df_1h_crypto is not None and not df_1h_crypto.empty:
    #         df_1h_crypto = df_1h_crypto.copy()
    #         df_1h_crypto.ta.ema(length=config.IND_EMA_FAST, append=True)
    #         df_1h_crypto.ta.ema(length=config.IND_EMA_21, append=True)
    #         swing_highs_1h = sniper_find_swing_points(df_1h_crypto, point_type="high", neighbors=2)
    #         ltf_confirm, _, _ = sniper_detect_msb(df_1h_crypto, swing_highs_1h, point_type="high")
    #     else:
    #         ltf_confirm = False

    # if not ltf_confirm:
    #     return signals

    funding_rate = get_funding_rate(symbol)
    df_1h_crypto = None

    sl = sweep_low * config.CRYPTO_LONG4_SL_MULT
    sl = apply_5x_sl_cap(sl, current_price, ctx)
    sl_dist = max(current_price - sl, 1e-8)
    tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
    fvg_label = " + FVG Onaylı ✅" if has_fvg else ""
    _rr_c4l = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
    
    ema_fast_val = None
    ema_mid_val = None
    if config.SMC_LTF_MSB_CONFIRM and df_1h_crypto is not None and not df_1h_crypto.empty:
        ema_fast_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_FAST}')
        ema_mid_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_21}')
    else:
        ema_fast_val = last_4h.get('EMA_20')
        ema_mid_val = last_4h.get('EMA_50')

    raw_vars = locals()
    
    _scores_c4l = build_breakout_scores(
        bb_width=None, price=current_price,
        ema_fast=ema_fast_val, ema_mid=ema_mid_val, ema_slow=None,
        volume=last_4h['volume'], vol_sma=last_4h.get('vol_sma_20'),
        dollar_vol=last_4h['volume'] * current_price,
        rr=_rr_c4l, regime="BULL", macro_aligned=True,
        consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
        funding_rate=funding_rate,
        rsi=last_4h.get('RSI_14'),
        rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else last_4h.get('RSI_14'),
        has_engulfing=False
    )
    if has_fvg:
        _scores_c4l["engulfing"] = min(100.0, _scores_c4l["engulfing"] + config.SMC_FVG_BONUS)
        
    _conv_c4l = calculate_conviction(_scores_c4l, ctx=ctx)
    if _conv_c4l.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx.get("last_1d"),
            "strategy": "KRİPTO LONG 4: KESKİN NİŞANCI (OTE)", "signal": "AL",
            "entry_price": current_price, "sl": sl, "tp": tp,
            "conviction_score": _conv_c4l.total_score, "conviction_grade": _conv_c4l.grade,
            "conviction_details": _conv_c4l.component_scores, "position_size_pct": _conv_c4l.position_size_pct,
            "reason": (
                f"🎯 SMC Kurulum (Gövde Fibo){fvg_label}\n"
                f"🧹 Likidite: Eski dip ({sweep_low:.4f}) temizlendi.\n"
                f"📐 MSB: Yapı kırılımı ({msb_high:.4f}) onaylı.\n"
                f"🎣 OTE Bölgesi (Gövde): {ote_bottom:.4f} - {ote_top:.4f}\n"
                f"📊 Fonlama: %{funding_rate:.4f} (Negatif Yakıt)\n"
                f"🛡️ İşlem %4 kâra geçince Break-Even uygula."
            ) + _conv_c4l.to_reason_suffix()
        })
    return signals


def _check_crypto_4_sniper_ote_short(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    supply_zones = detect_supply_zones(df_4h if 'df_4h' in locals() else ctx.get('df_4h'))
    if not is_price_in_supply_zone(current_price, supply_zones):
        return signals
    btc_sniper_bias = ctx["btc_sniper_bias"]

    if btc_sniper_bias not in (-1, 0):
        return signals

    swing_highs_s = sniper_find_swing_points(df_4h, point_type="high")
    swing_lows_s = sniper_find_swing_points(df_4h, point_type="low")
    sweep_ok, sweep_high = sniper_detect_sweep(df_4h, swing_highs_s, point_type="high")
    if not sweep_ok:
        return signals

    msb_ok, msb_low, msb_idx = sniper_detect_msb(df_4h, swing_lows_s, point_type="low")
    if not msb_ok:
        return signals

    ote_top, ote_bottom = sniper_calculate_ote(msb_low, sweep_high)
    if not (ote_bottom <= current_price <= ote_top):
        return signals

    has_fvg, _, _ = sniper_detect_fvg(df_4h, ote_top, ote_bottom, direction="bearish")
    if config.SMC_FVG_REQUIRED and not has_fvg:
        return signals

    # ltf_confirm = True
    # df_1h_crypto = None
    # if config.SMC_LTF_MSB_CONFIRM:
    #     df_1h_crypto = get_crypto_1h_data(symbol)
    #     if df_1h_crypto is not None and not df_1h_crypto.empty:
    #         df_1h_crypto = df_1h_crypto.copy()
    #         df_1h_crypto.ta.ema(length=config.IND_EMA_FAST, append=True)
    #         df_1h_crypto.ta.ema(length=config.IND_EMA_21, append=True)
    #         swing_lows_1h = sniper_find_swing_points(df_1h_crypto, point_type="low", neighbors=2)
    #         ltf_confirm, _, _ = sniper_detect_msb(df_1h_crypto, swing_lows_1h, point_type="low")
    #     else:
    #         ltf_confirm = False

    # if not ltf_confirm:
    #     return signals

    funding_rate = get_funding_rate(symbol)
    df_1h_crypto = None

    sl = sweep_high * config.CRYPTO_SHORT4_SL_MULT
    sl = apply_5x_sl_cap(sl, current_price, ctx)
    sl_dist = max(sl - current_price, 1e-8)
    tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
    fvg_label = " + FVG Onaylı ✅" if has_fvg else ""
    _rr_c4s = abs(current_price - tp) / max(abs(sl - current_price), 1e-8)
    _adx_prev_c4s = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
    
    ema_fast_val = None
    ema_mid_val = None
    if config.SMC_LTF_MSB_CONFIRM and df_1h_crypto is not None and not df_1h_crypto.empty:
        ema_fast_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_FAST}')
        ema_mid_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_21}')
    else:
        ema_fast_val = last_4h.get('EMA_20')
        ema_mid_val = last_4h.get('EMA_50')

    raw_vars = locals()
    
    _scores_c4s = build_short_scores(
        adx=last_4h.get('ADX_14'), adx_prev=_adx_prev_c4s,
        price=current_price, ema_fast=ema_fast_val, ema_mid=ema_mid_val, ema_slow=None,
        rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
        volume=last_4h['volume'], vol_sma=last_4h.get('vol_sma_20'),
        dollar_vol=last_4h['volume'] * current_price,
        rr=_rr_c4s, has_engulfing=False, regime="BEAR", macro_aligned=True,
        consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
        funding_rate=funding_rate
    )
    if has_fvg:
        _scores_c4s["engulfing"] = min(100.0, _scores_c4s["engulfing"] + config.SMC_FVG_BONUS)
        
    _conv_c4s = calculate_conviction(_scores_c4s, ctx=ctx)
    if _conv_c4s.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx.get("last_1d"),
            "strategy": "KRİPTO SHORT 4: KESKİN NİŞANCI (OTE)", "signal": "SAT",
            "entry_price": current_price, "sl": sl, "tp": tp,
            "conviction_score": _conv_c4s.total_score, "conviction_grade": _conv_c4s.grade,
            "conviction_details": _conv_c4s.component_scores, "position_size_pct": _conv_c4s.position_size_pct,
            "reason": (
                f"🎯 SHORT SMC Kurulum{fvg_label}\n"
                f"🧹 Likidite: Eski tepe ({sweep_high:.4f}) temizlendi.\n"
                f"📐 Bearish MSB: Yapı kırılımı ({msb_low:.4f}) aşağı onaylı.\n"
                f"🎣 Premium OTE: {ote_bottom:.4f} - {ote_top:.4f}\n"
                f"📊 Fonlama: +%{funding_rate:.4f} (Pozitif = Short Yakıtı)\n"
                f"🛡️ İşlem %4 kâra geçince Break-Even uygula."
            ) + _conv_c4s.to_reason_suffix()
        })
    return signals


def _check_crypto_4_sniper_ote(ctx):
    signals = []
    signals.extend(_check_crypto_4_sniper_ote_long(ctx))
    signals.extend(_check_crypto_4_sniper_ote_short(ctx))
    return signals


def _check_crypto_5_vol_squeeze(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_1d = ctx["last_1d"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    btc_ok = ctx["btc_ok"]

    sq_fired, sq_dir, sq_candle = detect_squeeze(df_4h)
    if sq_fired and sq_dir is not None:
        # HARD FILTER REMOVED: ADX constraint delegated to conviction_scorer
        # if pd.isna(last_4h.get('ADX_14')) or last_4h['ADX_14'] < config.CRYPTO_SQUEEZE_ADX_MIN:
        #     return signals
        trend_up = (not pd.isna(last_1d.get(f'EMA_{config.IND_EMA_MID}')) and not pd.isna(last_1d.get(f'EMA_{config.IND_EMA_SLOW}')) and
                    last_1d[f'EMA_{config.IND_EMA_MID}'] > last_1d[f'EMA_{config.IND_EMA_SLOW}'])
        valid_breakout = (sq_dir == "up" and trend_up) or (sq_dir == "down" and not trend_up)
        if valid_breakout:
            sq_mid = (sq_candle['high'] + sq_candle['low']) / 2
            ema20_4h = last_4h.get('EMA_20', current_price)
            if sq_dir == "up":
                # --- GOLDEN FILTER INJECTION ---
                # KRİPTO LONG 5: VOLATİLİTE SIKIŞMASI (SQUEEZE)
                # Filter: Relative_Volume < 3.9577 (+11.00R Improvement)
                vol_sma = last_4h.get('vol_sma_20', 0)
                vol = last_4h.get('volume', 0)
                rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
                if rel_vol_4h >= 3.9577:
                    return signals

                sl = min(sq_mid, ema20_4h) if not pd.isna(ema20_4h) else sq_mid
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                sl_dist = abs(current_price - sl)
                tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
                sig_type = "AL"
            else:
                sl = max(sq_mid, ema20_4h) if not pd.isna(ema20_4h) else sq_mid
                sl = apply_5x_sl_cap(sl, current_price, ctx)
                sl_dist = abs(sl - current_price)
                tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
                sig_type = "SAT"
            _rr_c5 = abs(tp - current_price) / max(abs(current_price - sl), 1e-8) if sig_type == "AL" else abs(current_price - tp) / max(abs(sl - current_price), 1e-8)
            
            raw_vars = locals()
            
            _scores_c5 = build_breakout_scores(
                bb_width=None, price=current_price, ema_fast=ema20_4h, ema_mid=None, ema_slow=None,
                volume=last_4h.get('volume', 0),
                vol_sma=last_4h.get('vol_sma_20'),
                dollar_vol=last_4h.get('volume', 0) * current_price,
                rr=_rr_c5, regime="BULL" if sq_dir == "up" else "BEAR", macro_aligned=btc_ok,
                consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
                rsi=last_4h.get('RSI_14'),
                rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else last_4h.get('RSI_14'),
                is_long=(sq_dir == "up"),
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
                    "strategy": f"KRİPTO {'LONG' if sig_type == 'AL' else 'SHORT'} 5: VOLATİLİTE SIKIŞMASI (SQUEEZE)", "signal": sig_type,
                    "entry_price": current_price, "sl": sl, "tp": tp,
                    "conviction_score": _conv_c5.total_score, "conviction_grade": _conv_c5.grade,
                    "conviction_details": _conv_c5.component_scores, "position_size_pct": _conv_c5.position_size_pct,
                    "reason": (
                        f"🗜️ Squeeze Patlaması ({sq_dir.upper()})!\n"
                        f"4S BB(20,2) Keltner(20,1.5) içinden kırıldı.\n"
                        f"1G Trend {'Yukarı ✅' if trend_up else 'Aşağı ✅'} ile uyumlu.\n"
                        f"Hacimli {'yeşil' if sq_dir == 'up' else 'kırmızı'} mum onayı."
                    ) + _conv_c5.to_reason_suffix()
                })
    return signals


def _check_crypto_6_vwap(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_1d = ctx["last_1d"]
    last_4h = ctx["last_4h"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    btc_ok = ctx["btc_ok"]

    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO LONG 6: VWAP KURUMSAL MIKNATISI
    # Filter: Relative_Volume > 1.2126 (+10.00R Improvement)
    vol_sma = last_4h.get('vol_sma_20', 0)
    vol = last_4h.get('volume', 0)
    rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
    if rel_vol_4h <= 1.2126:
        return signals

    # --- BTC FILTER INJECTION ---
    # Filter: BTC RSI > 40 AND BTC 24h Change < 2.0% (+6.00R Improvement)
    btc_rsi, btc_change = get_btc_rsi_and_change()
    if btc_rsi <= 40 or btc_change >= 2.0:
        return signals

    if last_1d is None or pd.isna(last_1d.get('EMA_20')) or pd.isna(last_1d.get('EMA_50')):
        return signals
    if last_1d['EMA_20'] <= last_1d['EMA_50']:
        return signals
    # HARD FILTER REMOVED: ADX constraint delegated to conviction_scorer
    # if pd.isna(last_4h.get('ADX_14')) or last_4h['ADX_14'] <= config.CRYPTO_VWAP_ADX_MIN:
    #     return signals

    # VWAP Golden Filters (RSI & Volatilite/ATR)
    # HARD FILTER REMOVED: RSI constraint delegated to conviction_scorer
    # if not pd.isna(last_4h.get('RSI_14')) and last_4h['RSI_14'] >= getattr(config, 'VWAP_LONG_MAX_RSI', 60.0):
    #     return signals
        
    atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
    if atr_col not in df_4h.columns:
        df_4h.ta.atr(length=14, append=True)
        atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
    if 'ATR_SMA_14' not in df_4h.columns and atr_col in df_4h.columns:
        df_4h['ATR_SMA_14'] = df_4h[atr_col].rolling(window=14).mean()

    current_atr = df_4h['ATRr_14'].iloc[-1]
    atr_sma = df_4h['ATR_SMA_14'].iloc[-1]
    
    if pd.notna(current_atr) and pd.notna(atr_sma) and atr_sma > 0:
        if (current_atr / atr_sma) > getattr(config, 'VWAP_MAX_ATR_RATIO', 2.0):
            return signals # Aşırı Volatilite İptali

    vwap_val = calculate_anchored_vwap(df_4h, anchor_type="weekly")
    if vwap_val is not None:
        bounce_ok, wick_low = detect_vwap_bounce(df_4h, vwap_val)
        if bounce_ok and wick_low is not None:
            sl = wick_low * config.CRYPTO_VWAP_SL_MULT
            sl = apply_5x_sl_cap(sl, current_price, ctx)
            sl_dist = abs(current_price - sl)
            _tp_c6 = current_price + (sl_dist * 1.50)
            _rr_c6 = abs(_tp_c6 - current_price) / max(abs(current_price - sl), 1e-8)
            
            raw_vars = locals()
            
            _scores_c6 = build_trend_scores(
                adx=None, adx_prev=None, price=current_price, ema_fast=vwap_val, ema_mid=None, ema_slow=None,
                rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
                volume=last_4h.get('volume', 0), vol_sma=None, dollar_vol=last_4h.get('volume', 0) * current_price,
                rr=_rr_c6, has_engulfing=True, regime="BULL", macro_aligned=btc_ok,
                consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO"
            )
            _conv_c6 = calculate_conviction(_scores_c6, ctx=ctx)
            if _conv_c6.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({
                    "raw_indicators": _extract_raw_indicators(raw_vars),
                    "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx.get("last_1d"),
                    "strategy": "KRİPTO LONG 6: VWAP KURUMSAL MIKNATISI", "signal": "AL",
                    "entry_price": current_price, "sl": sl, "tp": _tp_c6,
                    "conviction_score": _conv_c6.total_score, "conviction_grade": _conv_c6.grade,
                    "conviction_details": _conv_c6.component_scores, "position_size_pct": _conv_c6.position_size_pct,
                    "reason": (
                        f"⚓ VWAP Bounce (Kurumsal Mıknatıs)!\n"
                        f"4S Anchored VWAP: {vwap_val:.4f}\n"
                        f"Pin Bar onayı: VWAP'a değip sıçradı.\n"
                        f"BTC > EMA20 (Piyasa izni var ✅)"
                    ) + _conv_c6.to_reason_suffix()
                })
    return signals


def _check_crypto_7_obv(ctx):
    signals = []
    symbol = ctx["symbol"]
    last_1d = ctx["last_1d"]
    current_price = ctx["current_price"]
    df_1d = ctx["df_1d"]
    df_4h = ctx.get("df_4h")

    # --- HOURLY BLACKLIST INJECTION ---
    # Layer 3: Hourly Blacklist [20] (+10.00R Improvement)
    if df_4h is not None and not df_4h.empty:
        current_hour = df_4h.index[-1].hour
        if current_hour == 20:
            return signals

    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO LONG 7: SESSİZ BİRİKİM RADARI (OBV)
    # Filter: 4H BB_Width < 0.1964 (+9.00R Improvement)
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
                    "strategy": "KRİPTO LONG 7: SESSİZ BİRİKİM RADARI (OBV)", "signal": "AL",
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


def _check_crypto_sniper_1h_long(ctx_1h):
    signals = []
    bbw = ctx_1h["bbw"]
    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO LONG 10: KESKİN NİŞANCI (SNIPER)
    # Filter: BB_Width < 0.1000 (Sıkı Squeeze - İkinci Optimizasyon Aşaması)
    if bbw is not None and pd.notna(bbw) and bbw >= 0.1000:
        return signals

    symbol = ctx_1h["symbol"]
    current_price = ctx_1h["current_price"]
    btc_ok = ctx_1h["btc_ok"]
    df_1h_sniper = ctx_1h["df_1h_sniper"]
    last_1h_s = ctx_1h["last_1h_s"]
    prev_1h_s = ctx_1h["prev_1h_s"]
    guarded_vol_sma = ctx_1h["guarded_vol_sma"]
    kcw = ctx_1h["kcw"]
    bb_pct = ctx_1h["bb_pct"]
    bbl = ctx_1h["bbl"]

    has_fvg_long, _, _ = sniper_detect_fvg(df_1h_sniper, df_1h_sniper['high'].iloc[-1], df_1h_sniper['low'].iloc[-1], direction="bullish")
    swing_lows_s = sniper_find_swing_points(df_1h_sniper, point_type="low")
    sweep_ok_long, _ = sniper_detect_sweep(df_1h_sniper, swing_lows_s, point_type="low")
    has_sfp_long = sweep_ok_long
    
    last_4h = ctx_1h.get("last_4h")
    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14')) if last_4h is not None else None
    if atr_val is None or pd.isna(atr_val):
        atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
    sl_long = current_price - (atr_val * 1.25)
    sl_long = apply_5x_sl_cap(sl_long, current_price, ctx_1h)
    _tp_sn_long = current_price + config.BEAR_HUNTER_TP_RR * (current_price - sl_long)
    _rr_sn_long = abs(_tp_sn_long - current_price) / max(abs(current_price - sl_long), 1e-8)
    
    is_nan_ind = (pd.isna(last_1h_s.get('volume', float('nan'))) or pd.isna(current_price))
    
    supply_zones = detect_supply_zones(ctx_1h.get("df_4h"))
    in_supply_zone = is_price_in_supply_zone(current_price, supply_zones)
    blocked, block_reason = check_hard_blocks(
        volume=last_1h_s.get('volume', 0),
        in_supply_zone=in_supply_zone,
        price=current_price,
        vol_sma=guarded_vol_sma,
        is_quarantined=False,
        is_circuit_open=False,
        sl_direction_ok=(sl_long < current_price),
        rr_ratio=_rr_sn_long,
        consecutive_sl=_get_consecutive_sl(symbol),
        is_core_indicators_nan=is_nan_ind,
        min_volume_usd=config.VOL_ABSOLUTE_MIN_CRYPTO,
        willy_ema=last_1h_s.get('WILLR_21_EMA_13'),
        is_long=True
    )
    if blocked:
        return signals
        
    _scores_sn_long = build_sniper_scores(
        price=current_price, ema_fast=last_1h_s.get(f'EMA_{config.IND_EMA_FAST}'), ema_mid=last_1h_s.get(f'EMA_{config.IND_EMA_21}'), ema_slow=None,
        rsi=last_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'), rsi_prev=prev_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'),
        volume=last_1h_s.get('volume', 0), vol_sma=guarded_vol_sma, dollar_vol=last_1h_s.get('volume', 0) * current_price,
        rr=_rr_sn_long, regime="BULL" if btc_ok else "BEAR",
        macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol),
        bbw=bbw, kcw=kcw, pb=bb_pct, fvg_present=has_fvg_long, sfp_present=has_sfp_long,
        market="KRIPTO", is_long=True
    )
    _conv_sn_long = calculate_conviction(_scores_sn_long, weights=SNIPER_CRYPTO_WEIGHTS, ctx=ctx_1h)
    if _conv_sn_long.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM):
        raw_vars = locals()
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx_1h.get("last_1d"),
            "strategy": "KRİPTO LONG 10: KESKİN NİŞANCI (SNIPER)", "signal": "AL",
            "entry_price": current_price, "sl": sl_long, "tp": _tp_sn_long,
            "conviction_score": _conv_sn_long.total_score, "conviction_grade": _conv_sn_long.grade,
            "conviction_details": _conv_sn_long.component_scores, "position_size_pct": _conv_sn_long.position_size_pct,
            "reason": (
                f"🎯 Keskin Nişancı LONG!\n"
                f"Kanunlar: Squeeze: {_scores_sn_long['bbw_squeeze']:.1f}, %B: {_scores_sn_long['percent_b']:.1f}, FVG/SFP: {_scores_sn_long['fvg_sfp']:.1f}\n"
                f"SL: Bollinger Alt Band Altı ({sl_long:.2f})"
            ) + _conv_sn_long.to_reason_suffix()
        })
    return signals


def _check_crypto_sniper_1h_short(ctx_1h):
    signals = []
    symbol = ctx_1h["symbol"]
    current_price = ctx_1h["current_price"]

    # --- GOLDEN FILTER INJECTION ---
    # KRİPTO SHORT 10: KESKİN NİŞANCI (SNIPER)
    # Filter: ATR_Pct > 1.0771 (+30.00R Improvement)
    last_4h = ctx_1h.get('last_4h')
    if last_4h is not None:
        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14', 0))
        atr_pct = (atr_val / current_price) * 100.0 if current_price > 0 else 0
        if atr_pct <= 1.0771:
            return signals

    # Anti-Rekt HTF Trend filtresi (Güçlü boğa trendinde short arama)
    last_1d = ctx_1h.get('last_1d')
    if last_1d is not None:
        import pandas as pd
        ema_20_1d = last_1d.get('EMA_20')
        ema_50_1d = last_1d.get('EMA_50')
        rsi_1d = last_1d.get('RSI_14')
        if pd.notna(ema_20_1d) and pd.notna(ema_50_1d) and pd.notna(rsi_1d):
            if ema_20_1d > ema_50_1d and rsi_1d > 60:
                return signals
    btc_ok = ctx_1h["btc_ok"]
    df_1h_sniper = ctx_1h["df_1h_sniper"]
    last_1h_s = ctx_1h["last_1h_s"]
    prev_1h_s = ctx_1h["prev_1h_s"]
    guarded_vol_sma = ctx_1h["guarded_vol_sma"]
    bbw = ctx_1h["bbw"]
    kcw = ctx_1h["kcw"]
    bb_pct = ctx_1h["bb_pct"]
    bbu = ctx_1h["bbu"]

    funding_rate = get_funding_rate(symbol)
    cmf_1h = last_1h_s.get('CMF_20')
    
    has_fvg_short, _, _ = sniper_detect_fvg(df_1h_sniper, df_1h_sniper['high'].iloc[-1], df_1h_sniper['low'].iloc[-1], direction="bearish")
    swing_highs_s = sniper_find_swing_points(df_1h_sniper, point_type="high")
    sweep_ok_short, _ = sniper_detect_sweep(df_1h_sniper, swing_highs_s, point_type="high")
    has_sfp_short = sweep_ok_short
    
    sl_short = min(bbu * config.CRYPTO_SQUEEZE_SHORT_SL_BBU_MULT, current_price * config.CRYPTO_SQUEEZE_SHORT_SL_MAX_MULT)
    sl_short = apply_5x_sl_cap(sl_short, current_price, ctx_1h)
    _tp_sn_short = current_price - 2.25 * (sl_short - current_price)
    _rr_sn_short = abs(_tp_sn_short - current_price) / max(abs(sl_short - current_price), 1e-8)
    
    is_nan_ind = (pd.isna(last_1h_s.get('volume', float('nan'))) or pd.isna(current_price))
    
    supply_zones = detect_supply_zones(ctx_1h.get("df_4h"))
    in_supply_zone = is_price_in_supply_zone(current_price, supply_zones)
    blocked, block_reason = check_hard_blocks(
        volume=last_1h_s.get('volume', 0),
        in_supply_zone=in_supply_zone,
        price=current_price,
        vol_sma=guarded_vol_sma,
        is_quarantined=False,
        is_circuit_open=False,
        sl_direction_ok=(sl_short > current_price),
        rr_ratio=_rr_sn_short,
        consecutive_sl=_get_consecutive_sl(symbol),
        is_core_indicators_nan=is_nan_ind,
        min_volume_usd=config.VOL_ABSOLUTE_MIN_CRYPTO,
        willy_ema=last_1h_s.get('WILLR_21_EMA_13'),
        is_long=False
    )
    if blocked:
        return signals
        
    _scores_sn_short = build_sniper_scores(
        price=current_price, ema_fast=last_1h_s.get(f'EMA_{config.IND_EMA_FAST}'), ema_mid=last_1h_s.get(f'EMA_{config.IND_EMA_21}'), ema_slow=None,
        rsi=last_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'), rsi_prev=prev_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'),
        volume=last_1h_s.get('volume', 0), vol_sma=guarded_vol_sma, dollar_vol=last_1h_s.get('volume', 0) * current_price,
        rr=_rr_sn_short, regime="BEAR",
        macro_aligned=not btc_ok, consecutive_sl=_get_consecutive_sl(symbol),
        bbw=bbw, kcw=kcw, pb=bb_pct, fvg_present=has_fvg_short, sfp_present=has_sfp_short,
        market="KRIPTO", is_long=False, funding_rate=funding_rate,
        cmf=cmf_1h if cmf_1h is not None and not math.isnan(cmf_1h) else 0.0
    )
    df_4h = ctx_1h.get("df_4h")
    if df_4h is not None and not df_4h.empty:
        last_4h = df_4h.iloc[-1]
        ema_50 = last_4h.get("EMA_50")
        import pandas as pd
        if ema_50 is not None and pd.notna(ema_50) and current_price > 0:
            dist_below_ema50 = (ema_50 - current_price) / current_price
            if dist_below_ema50 > 0.04:
                oversold_stretch = dist_below_ema50 - 0.04
                _scores_sn_short["conflict_penalty"] -= min(35.0, oversold_stretch * 200.0 + 10.0)


    _conv_sn_short = calculate_conviction(_scores_sn_short, weights=SNIPER_CRYPTO_WEIGHTS, ctx=ctx_1h)
    if _conv_sn_short.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM):
        raw_vars = locals()
        signals.append({
            "raw_indicators": _extract_raw_indicators(raw_vars),
            "ticker": symbol, "market": "KRIPTO",
        "last_1d": ctx_1h.get("last_1d"),
            "strategy": "KRİPTO SHORT 10: KESKİN NİŞANCI (SNIPER)", "signal": "SAT",
            "entry_price": current_price, "sl": sl_short, "tp": _tp_sn_short,
            "conviction_score": _conv_sn_short.total_score, "conviction_grade": _conv_sn_short.grade,
            "conviction_details": _conv_sn_short.component_scores, "position_size_pct": _conv_sn_short.position_size_pct,
            "reason": (
                f"🎯 Keskin Nişancı SHORT!\n"
                f"Kanunlar: Squeeze: {_scores_sn_short['bbw_squeeze']:.1f}, %B: {_scores_sn_short['percent_b']:.1f}, FVG/SFP: {_scores_sn_short['fvg_sfp']:.1f}\n"
                f"SL: ~%5-7 Dinamik Stop ({sl_short:.2f})"
            ) + _conv_sn_short.to_reason_suffix()
        })
    return signals


def _check_crypto_sniper_1h(ctx):
    signals = []
    symbol = ctx["symbol"]
    current_price = ctx["current_price"]
    btc_ok = ctx["btc_ok"]

    df_1h_sniper = ctx.get("df_1h_sniper")
    if df_1h_sniper is None:
        df_1h_sniper = get_crypto_1h_data(symbol)
    if df_1h_sniper is None or df_1h_sniper.empty:
        return signals

    df_1h_sniper = df_1h_sniper.copy()
    df_1h_sniper.ta.kc(length=20, scalar=1.5, append=True)
    df_1h_sniper.ta.bbands(length=20, std=2.0, append=True)
    df_1h_sniper.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_1h_sniper.ta.ema(length=config.IND_EMA_FAST, append=True)
    df_1h_sniper.ta.ema(length=config.IND_EMA_21, append=True)
    df_1h_sniper.ta.cmf(length=20, append=True)
    df_1h_sniper['vol_sma_20'] = ta.sma(df_1h_sniper['volume'], length=config.IND_VOL_SMA_LENGTH)
    
    kc_upper_col = [c for c in df_1h_sniper.columns if 'KCU' in c]
    if not kc_upper_col:
        return signals
    kc_lower_col = [c for c in df_1h_sniper.columns if 'KCL' in c]
    if not kc_lower_col:
        return signals
    bb_upper_col = [c for c in df_1h_sniper.columns if 'BBU' in c]
    if not bb_upper_col:
        return signals
    bb_lower_col = [c for c in df_1h_sniper.columns if 'BBL' in c]
    if not bb_lower_col:
        return signals
    bb_mid_col = [c for c in df_1h_sniper.columns if 'BBM' in c]
    if not bb_mid_col:
        return signals
    bb_pct_col = [c for c in df_1h_sniper.columns if 'BBP' in c]
    if not bb_pct_col:
        return signals

    last_1h_s = df_1h_sniper.iloc[-1]
    prev_1h_s = last_1h_s
    if len(df_1h_sniper) >= 2:
        prev_1h_s = df_1h_sniper.iloc[-2]
    
    bbu = last_1h_s[bb_upper_col[0]]
    bbl = last_1h_s[bb_lower_col[0]]
    bbm = last_1h_s[bb_mid_col[0]]
    
    bbw = 0.0
    kcw = 0.0
    if bbm != 0:
        bbw = (bbu - bbl) / bbm
        kcw = (last_1h_s[kc_upper_col[0]] - last_1h_s[kc_lower_col[0]]) / bbm
    
    bb_pct = last_1h_s[bb_pct_col[0]]
    guarded_vol_sma = _apply_volume_sma_guard(df_1h_sniper, last_1h_s.get('vol_sma_20', 0))

    ctx_1h = {
        "df_4h": ctx.get("df_4h"),
        "last_4h": ctx.get("last_4h"),
        "symbol": symbol,
        "current_price": current_price,
        "btc_ok": btc_ok,
        "df_1h_sniper": df_1h_sniper,
        "last_1h_s": last_1h_s,
        "prev_1h_s": prev_1h_s,
        "guarded_vol_sma": guarded_vol_sma,
        "bbw": bbw,
        "kcw": kcw,
        "bb_pct": bb_pct,
        "bbl": bbl,
        "bbu": bbu,
        "market": "KRIPTO",
        "last_1d": ctx.get("last_1d")
    }

    signals.extend(_check_crypto_sniper_1h_long(ctx_1h))
    signals.extend(_check_crypto_sniper_1h_short(ctx_1h))
    return signals


def _check_crypto_long_sfp_choch(ctx):
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
            # Golden Filter (Iteration 1)
            if last_4h.get('CMF', 1) >= 0.1768:
                return signals

            # Golden Filter (Iteration 2 - Optimized)
            if last_4h.get('ADX_14', 100) >= 20.4955:
                return signals

            # Golden Filter (Iteration 3)
            if last_4h.get('Relative_Volume', 1) >= 6.3074:
                return signals

            # --- BTC FILTER INJECTION ---
            # Filter: BTC RSI > 45 AND BTC 24h Change > -2.0%
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
        "last_1d": ctx.get("last_1d"), "strategy": "KRİPTO LONG 1: SFP+CHoCH (SMC)", "signal": "AL",
                    "entry_price": current_price, "sl": sl, "tp": _tp,
                    "conviction_score": _conv.total_score, "conviction_grade": _conv.grade,
                    "conviction_details": _conv.component_scores, "position_size_pct": _conv.position_size_pct,
                    "reason": f"🟢 Likidite Avı (SFP) + CHoCH tespit edildi. FVG/OTE bölgesi onaylı.\nSL: {sl:.2f}\n" + _conv.to_reason_suffix()
                })
    return signals

def _check_crypto_long_short_squeeze(ctx):
    signals = []
    symbol = ctx["symbol"]
    current_price = ctx["current_price"]
    df_4h = ctx["df_4h"]
    last_4h = ctx["last_4h"]
    
    funding_rate = get_funding_rate(symbol)
    from data_sources import fetch_crypto_oi_surge
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

def _check_crypto_long_major_divergence(ctx):
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

def _check_crypto_long_sr_flip_fvg(ctx):
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
                # Golden Filter (Iteration 1)
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

def _check_crypto_long_bull_flag_ote(ctx):
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

def _check_crypto_long_smc(ctx):
    signals = []
    from data_sources import get_usdt_dominance_trend, check_token_unlocks
    
    usdt_trend = get_usdt_dominance_trend()
    unlock_risk = check_token_unlocks(ctx["symbol"])
    
    # usdt_trend == "UP" durumunda direkt çıkmak yerine (hard block),
    # ctx içerisine ekleyip conviction_scorer içerisinde soft ceza (veya risk uyarısı) uygulayacağız.
    ctx["usdt_trend"] = usdt_trend
        
    if unlock_risk:
        return signals
        
    signals.extend(_check_crypto_long_sfp_choch(ctx))
    signals.extend(_check_crypto_long_short_squeeze(ctx))
    signals.extend(_check_crypto_long_major_divergence(ctx))
    signals.extend(_check_crypto_long_sr_flip_fvg(ctx))
    signals.extend(_check_crypto_long_bull_flag_ote(ctx))
    
    return signals

# Ensure indicators and filter signals are helper functions
def _ensure_crypto_1d_indicators(df_1d):
    if 'RSI_14' not in df_1d.columns:
        df_1d.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    if 'EMA_20' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_MID, append=True)
    if 'EMA_50' not in df_1d.columns:
        df_1d.ta.ema(length=config.IND_EMA_SLOW, append=True)
    if 'ADX_14' not in df_1d.columns:
        df_1d.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    if not any(c in df_1d.columns for c in ['BBP_20_2.0', 'BBU_20_2.0']):
        df_1d.ta.bbands(length=config.IND_BBANDS_LENGTH, std=config.IND_BBANDS_STD, append=True)
    if 'SMA_200' not in df_1d.columns and len(df_1d) >= 200:
        df_1d.ta.sma(length=200, append=True)


def _ensure_crypto_4h_indicators(df_4h):
    if 'RSI_14' not in df_4h.columns:
        df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    if 'EMA_20' not in df_4h.columns:
        df_4h.ta.ema(length=config.IND_EMA_MID, append=True)
    if 'EMA_50' not in df_4h.columns:
        df_4h.ta.ema(length=config.IND_EMA_SLOW, append=True)
    if 'ADX_14' not in df_4h.columns:
        df_4h.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    if 'ATRr_14' not in df_4h.columns and 'ATR_14' not in df_4h.columns:
        df_4h.ta.atr(length=config.IND_ATR_LENGTH, append=True)
    if 'CMF_20' not in df_4h.columns:
        df_4h.ta.cmf(length=20, append=True)
    if 'SMA_200' not in df_4h.columns:
        df_4h.ta.sma(length=200, append=True)
    if 'EMA_200' not in df_4h.columns:
        df_4h.ta.ema(length=200, append=True)
    if 'vol_sma_20' not in df_4h.columns:
        df_4h['vol_sma_20'] = ta.sma(df_4h['volume'], length=config.IND_VOL_SMA_LENGTH)
        
    if 'chop' not in df_4h.columns:
        import numpy as np
        atr_col = 'ATRr_14' if 'ATRr_14' in df_4h.columns else 'ATR_14'
        if atr_col in df_4h.columns:
            atr_sum = df_4h[atr_col].rolling(14).sum()
            high_max = df_4h['high'].rolling(14).max()
            low_min = df_4h['low'].rolling(14).min()
            df_4h['chop'] = 100 * np.log10(atr_sum / (high_max - low_min).replace(0, 1e-9)) / np.log10(14)
        else:
            df_4h['chop'] = 50.0
        df_4h['chop'] = df_4h['chop'].fillna(50.0)


def _ensure_crypto_indicators(df_1d, df_4h):
    _ensure_crypto_1d_indicators(df_1d)
    _ensure_crypto_4h_indicators(df_4h)


def _is_crypto_signal_valid(sig, rel_vol_4h, ema_diff_pct, cmf_4h):
    score = sig.get('conviction_score', 0)
    direction = "LONG" if sig.get('signal') == "AL" else "SHORT"
    if score < 50:
        return False
    if rel_vol_4h < 0.7:
        return False
    if ema_diff_pct > 8.0:
        return False
    if direction == 'LONG' and cmf_4h < -0.10:
        return False
    if direction == 'SHORT' and cmf_4h > 0.10:
        return False
    return True


def _filter_crypto_signals(signals, symbol, current_price, last_4h, ctx):
    vol_sma = last_4h.get('vol_sma_20', 0)
    vol = last_4h.get('volume', 0)
    rel_vol_4h = vol / vol_sma if (pd.notna(vol_sma) and vol_sma > 0) else 1.0
    
    ema_20_val = last_4h.get('EMA_20', 0)
    ema_50_val = last_4h.get('EMA_50', 0)
    ema_diff_pct = 0.0
    if pd.notna(ema_20_val) and pd.notna(ema_50_val) and pd.notna(current_price) and current_price > 0:
        ema_diff_pct = (abs(ema_20_val - ema_50_val) / current_price) * 100
        
    cmf_4h = last_4h.get('CMF_20', 0)
    if pd.isna(cmf_4h):
        cmf_4h = 0.0

    filtered_signals = [
        sig for sig in signals if _is_crypto_signal_valid(sig, rel_vol_4h, ema_diff_pct, cmf_4h)
    ]

    al_signals = [s for s in filtered_signals if s.get("signal") == "AL"]
    sat_signals = [s for s in filtered_signals if s.get("signal") == "SAT"]

    if len(al_signals) >= 3:
        confluence_details = {f"Signal_{i+1}": s["strategy"] for i, s in enumerate(al_signals)}
        filtered_signals.append({
            "raw_indicators": al_signals[0].get("raw_indicators", {}),
            "ticker": symbol, "market": "KRIPTO",
            "last_1d": ctx.get("last_1d"),
            "strategy": "SÜPER SİNYAL: CONFLUENCE (LONG)", "signal": "AL",
            "entry_price": current_price, 
            "sl": al_signals[0].get("sl", current_price * 0.95), 
            "tp": al_signals[0].get("tp", current_price * 1.05),
            "conviction_score": 95, "conviction_grade": CONVICTION_STRONG,
            "conviction_details": {"Confluence_Count": len(al_signals), **confluence_details}, 
            "position_size_pct": 5.0,
            "reason": f"Süper Sinyal: Aynı anda {len(al_signals)} farklı strateji AL verdi!"
        })

    if len(sat_signals) >= 3:
        confluence_details = {f"Signal_{i+1}": s["strategy"] for i, s in enumerate(sat_signals)}
        filtered_signals.append({
            "raw_indicators": sat_signals[0].get("raw_indicators", {}),
            "ticker": symbol, "market": "KRIPTO",
            "last_1d": ctx.get("last_1d"),
            "strategy": "SÜPER SİNYAL: CONFLUENCE (SHORT)", "signal": "SAT",
            "entry_price": current_price, 
            "sl": sat_signals[0].get("sl", current_price * 1.05), 
            "tp": sat_signals[0].get("tp", current_price * 0.95),
            "conviction_score": 95, "conviction_grade": CONVICTION_STRONG,
            "conviction_details": {"Confluence_Count": len(sat_signals), **confluence_details}, 
            "position_size_pct": 5.0,
            "reason": f"Süper Sinyal: Aynı anda {len(sat_signals)} farklı strateji SAT verdi!"
        })

    return filtered_signals


# KRİPTO STRATEJİ MOTORU
def analyze_strategies_crypto(symbol, df_1d, df_4h, btc_ok=False, btc_sniper_bias=0, metrics_collector=None, df_1h_sniper=None):
    signals = []

    if len(df_1d) < 50 or len(df_4h) < 20:
        return signals

    df_1d = df_1d.copy()
    df_4h = df_4h.copy()

    # Calculate indicators
    _ensure_crypto_indicators(df_1d, df_4h)

    last_1d = df_1d.iloc[-1]
    last_4h = df_4h.iloc[-1]
    current_price = last_4h['close']

    adx_1d = last_1d.get('ADX_14', 0)
    is_choppy = adx_1d < 25 if not pd.isna(adx_1d) else False

    body = abs(last_4h['close'] - last_4h['open'])
    adx_val = last_4h.get('ADX_14', 0)
    if pd.isna(adx_val): adx_val = 0
        
    dynamic_atr_mult = 2.0 if adx_val > 25 else 1.2

    if metrics_collector is not None:
        metrics_collector[symbol] = {
            "Symbol": symbol, "Market": "KRIPTO", "Price": current_price,
            "1D RSI": round(last_1d.get("RSI_14", 0), 2) if pd.notna(last_1d.get("RSI_14")) else None,
            "4H ADX": round(last_4h.get("ADX_14", 0), 2) if pd.notna(last_4h.get("ADX_14")) else None,
            "1H RSI": None,
            "1D SMA 50": round(last_1d.get("EMA_50", 0), 2) if pd.notna(last_1d.get("EMA_50")) else None,
            "1D Trend SMA": round(get_trend_sma(last_1d), 2) if pd.notna(get_trend_sma(last_1d)) else None,
            "Trend": "Bullish" if last_1d.get("EMA_20", 0) > last_1d.get("EMA_50", float('inf')) else "Bearish",
            "1H Volume": last_4h.get("volume")
        }

    ctx = {
        "symbol": symbol, "last_1d": last_1d, "last_4h": last_4h,
        "current_price": current_price, "df_1d": df_1d, "df_4h": df_4h,
        "btc_ok": btc_ok, "btc_sniper_bias": btc_sniper_bias,
        "dynamic_atr_mult": dynamic_atr_mult,
        "is_choppy": is_choppy, "adx_1d": adx_1d,
        "market": "KRIPTO",
        "df_1h_sniper": df_1h_sniper,
    }

    signals.extend(_check_crypto_1_liquidation(ctx))
    signals.extend(_check_crypto_2_mega_trend(ctx))
    signals.extend(_check_crypto_3_breakout(ctx))
    signals.extend(_check_crypto_shorts(ctx))
    signals.extend(_check_crypto_4_sniper_ote(ctx))
    signals.extend(_check_crypto_5_vol_squeeze(ctx))
    signals.extend(_check_crypto_6_vwap(ctx))
    signals.extend(_check_crypto_7_obv(ctx))
    signals.extend(_check_crypto_sniper_1h(ctx))
    signals.extend(_check_crypto_long_smc(ctx))

    # Filter signals
    return _filter_crypto_signals(signals, symbol, current_price, last_4h, ctx)

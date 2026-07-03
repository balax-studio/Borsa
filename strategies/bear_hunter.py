"""
strategies.py — Strateji Katmanı
Tüm BIST, Kripto, Emtia ve Ayı Avcısı strateji fonksiyonları + scan_all_markets.
ThreadPoolExecutor ile toplu tarama.
"""
import pandas as pd
import config

from config import (
    TOP_BIST, TOP_CRYPTO, TOP_EMTIA, TOP_HEAVY_SHORT, MEME_BLACKLIST,
    EMTIA_ATR_MULT, DXY_SENSITIVE, EMTIA_NAMES,
    API_SLEEP_CRYPTO, API_SLEEP_EMTIA,
    ATR_MULTIPLIER_BIST, ATR_MULTIPLIER_CRYPTO,
    ATR_CAP_BIST, ATR_CAP_CRYPTO, ATR_CAP_EMTIA,
    MIN_DOLLAR_VOL_CRYPTO, MIN_DOLLAR_VOL_BIST,
    DARTH_MAUL_BODY_RATIO,
    VOL_SMA_LONG_RATIO, ADX_TOO_LATE,
    MIN_HOURLY_DOLLAR_VOL_CRYPTO, MIN_HOURLY_TL_VOL_BIST,
    FUNDING_SHORT_BLOCK_THRESHOLD,
    LIQUIDITY_WINDOW_START_HOUR, LIQUIDITY_WINDOW_START_MIN,
    LIQUIDITY_WINDOW_END_HOUR, LIQUIDITY_WINDOW_END_MIN,
    RR_MINIMUM,
)
from data_sources import (
    get_funding_rate, fetch_crypto_oi_crash,
)
from indicators import (
    sniper_get_htf_bias, sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_calculate_ote, sniper_detect_fvg,
    detect_sfp, detect_premium_rejection, detect_bearish_divergence,
    detect_bullish_divergence, detect_squeeze, calculate_relative_strength,
    calculate_anchored_vwap, detect_vwap_bounce, detect_obv_accumulation,
    calculate_orb_cage, calculate_time_specific_rvol,
    check_bullish_engulfing_momentum, calculate_cmf,
    sniper_calculate_ote_body,
)
from conviction_scorer import (
    calculate_conviction, build_short_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
)

from .helpers import _extract_raw_indicators, _get_consecutive_sl

# ════════════════════════════════════════
def _check_bear_hunter_1_sfp(symbol, df_4h, current_price, atr_val, funding_note, oi_note, btc_bullish, _base_scores):
    signals = []
    sfp_found, swing_high, sfp_candle = detect_sfp(df_4h)
    
    # --- GOLDEN FILTER INJECTION ---
    # SHORT 1: ZİRVE TUZAĞI (SFP)
    # Filter: ADX > 24.5540 (+10.00R Improvement)
    import pandas as pd
    adx_val = df_4h.iloc[-1].get(f'ADX_14')  # using 14 as config.IND_ADX_LENGTH default
    if adx_val is not None and pd.notna(adx_val):
        if adx_val <= 24.5540:
            return signals
            
    if sfp_found and sfp_candle is not None:
        sl = float(sfp_candle['high']) + (atr_val * config.BEAR_HUNTER_SFP_ATR_SL_MULT)
        sl_dist = max(sl - current_price, 1e-8)
        tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
        risk = sl - current_price
        reward = current_price - tp
        if risk > 0 and reward > 0 and (reward / risk) >= config.RR_MINIMUM:
            rr_ratio = reward / risk
            sl_pct = (risk / current_price) * 100
            _scores_bh1 = build_short_scores(rr=rr_ratio, **_base_scores)
            _conv_bh1 = calculate_conviction(_scores_bh1, ctx={"symbol": symbol})
            if _conv_bh1.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({ "raw_indicators": _extract_raw_indicators(locals()),
                    "ticker": symbol, "market": "AYI_AVCISI",
                    "strategy": "SHORT 1: ZİRVE TUZAĞI (SFP)", "signal": "SAT",
                    "entry_price": current_price, "sl": sl, "tp": tp,
                    "conviction_score": _conv_bh1.total_score, "conviction_grade": _conv_bh1.grade, "conviction_details": _conv_bh1.component_scores,
                    "position_size_pct": _conv_bh1.position_size_pct,
                    "reason": (
                        f"🧹 {symbol} Zirve Tuzağı!\n"
                        f"Önceki tepe ({swing_high:.2f}) süpürüldü.\n"
                        f"Devasa üst fitil + kırmızı gövde.\n"
                        f"📐 R:R Oranı: {rr_ratio:.1f}:1\n"
                        f"🛑 SL: {sl:.2f} ({sl_pct:.1f}%)\n"
                        f"👑 BTC: Zayıf ✅{funding_note}{oi_note}"
                    ) + _conv_bh1.to_reason_suffix()
                })
    return signals


def _check_bear_hunter_2_premium(symbol, df_4h, df_1d, current_price, atr_val, funding_note, oi_note, btc_bullish, _base_scores):
    signals = []
    prem_found, fib_618, fib_786, prem_candle = detect_premium_rejection(df_4h, df_1d)
    
    # --- GOLDEN FILTER INJECTION ---
    # SHORT 2: PAHALI BÖLGE REDDİ (SMC PREMIUM)
    # Filter: RSI < 58.3958 (+5.00R Improvement)
    import pandas as pd
    rsi_val = df_4h.iloc[-1].get(f'RSI_{config.IND_RSI_LENGTH}')
    if rsi_val is not None and pd.notna(rsi_val):
        if rsi_val >= 58.3958:
            return signals
            
    if prem_found:
        sl = fib_786 + (atr_val * config.BEAR_HUNTER_PREMIUM_ATR_SL_MULT)
        sl_dist = max(sl - current_price, 1e-8)
        tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
        risk = sl - current_price
        reward = current_price - tp
        if risk > 0 and reward > 0 and (reward / risk) >= config.RR_MINIMUM:
            rr_ratio = reward / risk
            sl_pct = (risk / current_price) * 100
            _scores_bh2 = build_short_scores(rr=rr_ratio, **_base_scores)
            _conv_bh2 = calculate_conviction(_scores_bh2, ctx={"symbol": symbol})
            if _conv_bh2.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                signals.append({ "raw_indicators": _extract_raw_indicators(locals()),
                    "ticker": symbol, "market": "AYI_AVCISI",
                    "strategy": "SHORT 2: PAHALI BÖLGE REDDİ (SMC PREMIUM)", "signal": "SAT",
                    "entry_price": current_price, "sl": sl, "tp": tp,
                    "conviction_score": _conv_bh2.total_score, "conviction_grade": _conv_bh2.grade, "conviction_details": _conv_bh2.component_scores,
                    "position_size_pct": _conv_bh2.position_size_pct,
                    "reason": (
                        f"🎯 {symbol} Premium Bölge Reddi!\n"
                        f"1G düşüş trendi (EMA20 &lt; EMA50) onaylı.\n"
                        f"Fib 0.618-0.786 bölgesinde bearish red.\n"
                        f"📐 Premium: {fib_618:.2f} - {fib_786:.2f}\n"
                        f"📐 R:R Oranı: {rr_ratio:.1f}:1\n"
                        f"👑 BTC: Zayıf ✅{funding_note}{oi_note}"
                    ) + _conv_bh2.to_reason_suffix()
                })
    return signals


def _check_bear_hunter_3_divergence(symbol, df_1d, df_4h, current_price, atr_val, funding_note, btc_bullish, _base_scores):
    signals = []
    trend_aligned = True
    ema_slow_col = f"EMA_{config.IND_EMA_SLOW}"
    if config.SHORT_TREND_ALIGN_REQUIRED:
        ema_50_1d = df_1d.iloc[-1].get(ema_slow_col) if df_1d is not None and not df_1d.empty else None
        sma_200_1d = df_1d.iloc[-1].get(f'SMA_{config.IND_SMA_TREND}') if df_1d is not None and not df_1d.empty else None
        if ema_50_1d is not None and not pd.isna(ema_50_1d) and current_price >= ema_50_1d:
            trend_aligned = False
        if sma_200_1d is not None and not pd.isna(sma_200_1d) and current_price >= sma_200_1d:
            trend_aligned = False

    if trend_aligned:
        div_found, sh_1, sh_2, rsi_1, rsi_2 = detect_bearish_divergence(df_4h)
        if div_found:
            sl = sh_2 + (atr_val * config.BEAR_HUNTER_DIV_ATR_SL_MULT)
            sl_dist = max(sl - current_price, 1e-8)
            tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
            risk = sl - current_price
            reward = current_price - tp
            if risk > 0 and reward > 0 and (reward / risk) >= config.RR_MINIMUM:
                rr_ratio = reward / risk
                sl_pct = (risk / current_price) * 100
                _scores_bh3 = build_short_scores(rr=rr_ratio, **_base_scores)
                _conv_bh3 = calculate_conviction(_scores_bh3, ctx={"symbol": symbol})
                if _conv_bh3.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
                    signals.append({ "raw_indicators": _extract_raw_indicators(locals()),
                        "ticker": symbol, "market": "AYI_AVCISI",
                        "strategy": "SHORT 3: YORGUNLUK TEPESİ (DİVERGENCE)", "signal": "SAT",
                        "entry_price": current_price, "sl": sl, "tp": tp,
                        "conviction_score": _conv_bh3.total_score, "conviction_grade": _conv_bh3.grade, "conviction_details": _conv_bh3.component_scores,
                        "position_size_pct": _conv_bh3.position_size_pct,
                        "reason": (
                            f"🪫 {symbol} Yorgunluk Tepesi!\n"
                            f"Fiyat: {sh_1:.2f} → {sh_2:.2f} (Higher High)\n"
                            f"RSI: {rsi_1:.0f} → {rsi_2:.0f} (Lower High) ⚠️\n"
                            f"Hacim düştü + 4S EMA20 kırıldı.\n"
                            f"📐 R:R Oranı: {rr_ratio:.1f}:1\n"
                            f"👑 BTC: Zayıf ✅{funding_note}"
                        ) + _conv_bh3.to_reason_suffix()
                    })
    return signals


def _prepare_bh_indicators(df_1d, df_4h):
    if df_1d is not None:
        df_1d.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
        df_1d.ta.ema(length=config.IND_EMA_MID, append=True)
        df_1d.ta.ema(length=config.IND_EMA_SLOW, append=True)
        if len(df_1d) >= config.IND_SMA_TREND:
            df_1d.ta.sma(length=config.IND_SMA_TREND, append=True)

    df_4h.ta.atr(length=config.IND_ATR_LENGTH, append=True)
    df_4h.ta.ema(length=config.IND_EMA_MID, append=True)
    df_4h.ta.ema(length=config.IND_EMA_SLOW, append=True)
    df_4h.ta.adx(length=config.IND_ADX_LENGTH, append=True)
    df_4h.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
    df_4h['vol_sma_20'] = df_4h['volume'].rolling(config.IND_VOL_SMA_LENGTH).mean()


def _collect_bear_hunter_metrics(symbol, df_1d, last_4h, current_price, ema_mid_col, ema_slow_col, metrics_collector):
    if metrics_collector is not None and symbol not in metrics_collector:
        def _get_metric(s, k): return round(s.get(k, 0), 2) if s is not None and not s.empty and pd.notna(s.get(k)) else None
        metrics_collector[symbol] = {
            "Symbol": symbol, "Market": "KRIPTO (Ayı)", "Price": current_price,
            "1D RSI": _get_metric(df_1d.iloc[-1] if df_1d is not None else None, f"RSI_{config.IND_RSI_LENGTH}"),
            "4H ADX": _get_metric(last_4h, f"ADX_{config.IND_ADX_LENGTH}"),
            "1H RSI": None,
            "1D SMA 50": _get_metric(df_1d.iloc[-1] if df_1d is not None else None, ema_slow_col),
            "1D SMA 200": _get_metric(df_1d.iloc[-1] if df_1d is not None else None, f"SMA_{config.IND_SMA_TREND}"),
            "Trend": "Bullish" if df_1d is not None and not df_1d.empty and df_1d.iloc[-1].get(ema_mid_col, 0) > df_1d.iloc[-1].get(ema_slow_col, float('inf')) else "Bearish",
            "1H Volume": last_4h.get("volume")
        }


def analyze_bear_hunter(symbol, df_1d, df_4h, btc_bullish=False, metrics_collector=None):
    """Ağır Sıklet SHORT tarayıcı. 3 strateji + 3 çelik kalkan."""
    signals = []

    if df_4h is None or len(df_4h) < 20:
        return signals

    _prepare_bh_indicators(df_1d, df_4h)

    last_4h = df_4h.iloc[-1]
    current_price = float(last_4h['close'])

    ema_mid_col = f"EMA_{config.IND_EMA_MID}"
    ema_slow_col = f"EMA_{config.IND_EMA_SLOW}"

    _collect_bear_hunter_metrics(symbol, df_1d, last_4h, current_price, ema_mid_col, ema_slow_col, metrics_collector)
    
    _adx_prev_bh = df_4h.iloc[-2].get(f'ADX_{config.IND_ADX_LENGTH}') if len(df_4h) >= 2 else None
    _base_scores = {
        "adx": last_4h.get(f'ADX_{config.IND_ADX_LENGTH}'), "adx_prev": _adx_prev_bh,
        "price": current_price, "ema_fast": last_4h.get(ema_mid_col), "ema_mid": last_4h.get(ema_slow_col), "ema_slow": None,
        "rsi": last_4h.get(f'RSI_{config.IND_RSI_LENGTH}'), "rsi_prev": df_4h.iloc[-2].get(f'RSI_{config.IND_RSI_LENGTH}') if len(df_4h) >= 2 else None,
        "volume": last_4h.get('volume', 0), "vol_sma": last_4h.get('vol_sma_20'),
        "dollar_vol": last_4h.get('volume', 0) * current_price,
        "has_engulfing": False, "regime": "BEAR",
        "macro_aligned": (not btc_bullish), "consecutive_sl": _get_consecutive_sl(symbol), "market": "KRIPTO"
    }

    atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14'))
    if atr_val is None or pd.isna(atr_val):
        atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT

    funding_rate = get_funding_rate(symbol)
    funding_ok = True
    funding_note = ""
    if funding_rate is not None:
        if funding_rate < config.FUNDING_SHORT_BLOCK:
            funding_ok = False
        elif funding_rate >= 0:
            funding_note = f"\n🧲 Funding: +{funding_rate:.4f}% (Shortçu az) ✅"
        else:
            funding_note = f"\n🧲 Funding: {funding_rate:.4f}% (Normal)"

    if not funding_ok:
        return signals

    if hasattr(last_4h.name, 'hour') and last_4h.name.hour == 20:
        return signals
        
    vol = last_4h.get('volume', 0)
    vol_sma = last_4h.get('vol_sma_20', 1)
    rel_vol = vol / vol_sma if (pd.notna(vol_sma) and vol_sma != 0) else 1.0
        
    if rel_vol < 1.5:
        return signals

    oi_crashed = fetch_crypto_oi_crash(symbol)
    oi_note = "\n🩸 OI (Açık Pozisyon) Çöküşü: Onaylı ✅" if oi_crashed else ""

    # Call sub-strategies
    signals.extend(_check_bear_hunter_1_sfp(symbol, df_4h, current_price, atr_val, funding_note, oi_note, btc_bullish, _base_scores))
    signals.extend(_check_bear_hunter_2_premium(symbol, df_4h, df_1d, current_price, atr_val, funding_note, oi_note, btc_bullish, _base_scores))
    signals.extend(_check_bear_hunter_3_divergence(symbol, df_1d, df_4h, current_price, atr_val, funding_note, btc_bullish, _base_scores))

    return signals


# ════════════════════════════════════════
# TOPLU TARAMA ORKESTRATÖRLERİ

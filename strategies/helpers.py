"""
strategies.py — Strateji Katmanı
Tüm BIST, Kripto, Emtia ve Ayı Avcısı strateji fonksiyonları + scan_all_markets.
ThreadPoolExecutor ile toplu tarama.
"""
import logging
import pandas as pd
import config
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
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



def _extract_raw_indicators(l_vars):
    """
    Sinyal üretildiği andaki (locals() üzerinden) mevcut zaman dilimlerine ait 
    ham indikatör verilerini dinamik olarak toplar.
    """
    import pandas as pd
    res = {}
    for prefix in ['last_15m', 'last_1h', 'last_4h', 'last_1d', 'last_1w']:
        if prefix in l_vars and isinstance(l_vars[prefix], pd.Series):
            tf = prefix.split('_')[1].upper()
            s = l_vars[prefix]
            
            # Basic Price & Vol
            if 'close' in s: res[f'Close_{tf}'] = round(s['close'], 4)
            if 'volume' in s: res[f'Vol_{tf}'] = round(s['volume'], 2)
            
            # RSI
            rsi_val = s.get('RSI_14')
            if rsi_val is not None and not pd.isna(rsi_val):
                res[f'RSI_{tf}'] = round(rsi_val, 2)
                
            # ADX
            adx_val = s.get('ADX_14')
            if adx_val is not None and not pd.isna(adx_val):
                res[f'ADX_{tf}'] = round(adx_val, 2)
                
            # CMF
            cmf_val = s.get('CMF_20')
            if cmf_val is not None and not pd.isna(cmf_val):
                res[f'CMF_{tf}'] = round(cmf_val, 2)
                
            # Chop
            chop_val = s.get('chop')
            if chop_val is not None and not pd.isna(chop_val):
                res[f'Chop_{tf}'] = round(chop_val, 2)
                
            # ATR
            atr_val = s.get('ATRr_14') or s.get('ATR_14')
            if atr_val is not None and not pd.isna(atr_val):
                res[f'ATR_{tf}'] = round(atr_val, 4)
                
            # EMAs / SMAs
            for ema_name in ['EMA_20', 'EMA_50', 'EMA_200', 'SMA_200']:
                ema_val = s.get(ema_name)
                if ema_val is not None and not pd.isna(ema_val):
                    res[f'{ema_name}_{tf}'] = round(ema_val, 4)
                    
            # Bollinger Bands
            bbl = s.get('BBL_20_2.0')
            bbu = s.get('BBU_20_2.0')
            if bbl is not None and not pd.isna(bbl):
                res[f'BBL_20_{tf}'] = round(bbl, 4)
            if bbu is not None and not pd.isna(bbu):
                res[f'BBU_20_{tf}'] = round(bbu, 4)
            
    # 1D Trend durumunu l_vars üzerinden dinamik olarak çıkar (ConflictResolver için)
    if 'df_1d' in l_vars and l_vars['df_1d'] is not None and not l_vars['df_1d'].empty:
        df_1d_val = l_vars['df_1d']
        last_row = df_1d_val.iloc[-1]
        if 'EMA_8' in last_row and 'EMA_21' in last_row:
            res['Trend_1D'] = 'Bullish' if last_row['EMA_8'] > last_row['EMA_21'] else 'Bearish'
        elif 'EMA_20' in last_row and 'EMA_50' in last_row:
            res['Trend_1D'] = 'Bullish' if last_row['EMA_20'] > last_row['EMA_50'] else 'Bearish'
            
    return res

def _get_consecutive_sl(symbol):
    """Penalty box'tan ardışık SL sayısını al (yoksa 0)."""
    try:
        from penalty_box import get_penalty_status
        status = get_penalty_status(symbol)
        return status.get('consecutive_sl', 0) if status else 0
    except Exception:
        return 0


# ════════════════════════════════════════
# 🔴 RED TEAM YARDIMCI FONKSİYONLARI
# ════════════════════════════════════════
def _is_darth_maul(candle):
    """RED-06: Darth Maul mum tespiti — flash crash kaos filtresi."""
    body = abs(candle['close'] - candle['open'])
    total_range = candle['high'] - candle['low']
    if total_range <= 0:
        return False
    return (body / total_range) < DARTH_MAUL_BODY_RATIO


def _get_darth_maul_ratio(candle):
    """RED-06: Darth Maul mumunun gövde/toplam boy oranını döndürür."""
    body = abs(candle['close'] - candle['open'])
    total_range = candle['high'] - candle['low']
    if total_range <= 0:
        return 1.0
    return body / total_range


def _get_upper_wick_ratio(candle):
    """Gövdeye kıyasla üst fitilin oranını hesaplar.
    Dönen değer > 2.0 ise üst fitil gövdenin 2 katından uzundur."""
    body = abs(candle['close'] - candle['open'])
    upper_wick = candle['high'] - max(candle['open'], candle['close'])
    if body <= 0:
        return 999.0 if upper_wick > 0 else 0.0
    return upper_wick / body


def _get_lower_wick_ratio(candle):
    """Gövdeye kıyasla alt fitilin oranını hesaplar.
    Dönen değer > 2.0 ise alt fitil gövdenin 2 katından uzundur."""
    body = abs(candle['close'] - candle['open'])
    lower_wick = min(candle['open'], candle['close']) - candle['low']
    if body <= 0:
        return 999.0 if lower_wick > 0 else 0.0
    return lower_wick / body


def _is_meaningful_volume(volume, vol_sma_20, price, market="KRIPTO"):
    """RED-07: Hacim gerçekten anlamlı mı, yoksa ölü piyasada gürültü mü?"""
    if pd.isna(vol_sma_20) or vol_sma_20 <= 0:
        return False
    dollar_volume = volume * price
    min_dollar = MIN_DOLLAR_VOL_CRYPTO if market == "KRIPTO" else MIN_DOLLAR_VOL_BIST
    if dollar_volume < min_dollar:
        return False
    return volume > (config.MEANINGFUL_VOLUME_MULT * vol_sma_20)


def _adx_momentum_ok(df, last_row):
    """RED-02: ADX momentum kontrolü — gecikmeli ve olgunlaşmış trend filtresi."""
    adx_current = last_row.get('ADX_14')
    if adx_current is None or pd.isna(adx_current):
        return False
    if adx_current <= config.EMTIA_TREND_ADX_MIN:
        return False
    if adx_current > ADX_TOO_LATE:
        return False  # Trend olgunlaşmış, geç kaldın
    if len(df) >= 2:
        adx_prev = df.iloc[-2].get('ADX_14')
        if adx_prev is not None and not pd.isna(adx_prev):
            if adx_current < adx_prev:
                return False  # ADX düşüyor, trend gücünü kaybediyor
    return True


def _apply_volume_sma_guard(df, vol_sma_20):
    """RED-01: Volume SMA(20) manipülasyona açık mı? SMA(50) ile çapraz kontrol."""
    if pd.isna(vol_sma_20) or vol_sma_20 <= 0:
        return vol_sma_20
    vol_sma_50 = df['volume'].rolling(config.IND_VOL_SMA_SLOW).mean().iloc[-1] if len(df) >= config.IND_VOL_SMA_SLOW else None
    if vol_sma_50 is not None and not pd.isna(vol_sma_50) and vol_sma_50 > 0:
        if vol_sma_20 < (vol_sma_50 * VOL_SMA_LONG_RATIO):
            return vol_sma_50 * config.VOL_SMA_FLOOR_MULT  # Baskılanmış SMA'yı yukarı düzelt
    return vol_sma_20


def _is_unclosed_candle(df, timeframe="1d"):
    """RED-13: Son mum henüz kapanmamış mı kontrol et."""
    from datetime import timezone
    if not hasattr(df.index, 'tz') or df.index.tz is None:
        return False  # Timezone bilgisi yoksa güvenli tarafta kal
    now_utc = datetime.now(timezone.utc)
    last_ts = df.index[-1]
    if hasattr(last_ts, 'tzinfo') and last_ts.tzinfo is not None:
        last_ts_utc = last_ts.astimezone(timezone.utc)
    else:
        return False
    if timeframe == "1d":
        return last_ts_utc.date() == now_utc.date()
    elif timeframe == "4h":
        return (now_utc - last_ts_utc).total_seconds() < 4 * config.COOLDOWN_SECONDS_1H
    elif timeframe == "1h":
        return (now_utc - last_ts_utc).total_seconds() < config.COOLDOWN_SECONDS_1H
    return False


def _resolve_dual_signals(signals):
    """RED-17: Aynı varlıkta hem AL hem SAT sinyali varsa,
    en iyi R:R oranına sahip olanı tut, diğerini sil."""
    from collections import defaultdict
    ticker_groups = defaultdict(list)
    for sig in signals:
        ticker_groups[sig["ticker"]].append(sig)

    resolved = []
    for ticker, sigs in ticker_groups.items():
        directions = set(s["signal"] for s in sigs)
        if "AL" in directions and "SAT" in directions:
            # Çatışma var — en iyi R:R'yi seç
            best = None
            best_rr = -1
            for s in sigs:
                entry = s.get("entry_price", 0)
                sl = s.get("sl", entry)
                tp = s.get("tp", entry)
                risk = abs(entry - sl) if abs(entry - sl) > 0 else 1e-8
                reward = abs(tp - entry)
                rr = reward / risk
                if rr > best_rr:
                    best_rr = rr
                    best = s
            if best:
                logging.warning(
                    f"[RED-17] {ticker}: AL+SAT çatışması → '{best['signal']}' "
                    f"({best['strategy']}) kazandı (R:R={best_rr:.2f})"
                )
                resolved.append(best)
        else:
            resolved.extend(sigs)
    return resolved


def _apply_rr_filter(signals, min_rr=None):
    """FM-01: Kurumsal R:R Filtresi.
    Reward:Risk oranı minimum eşiğin altındaki sinyalleri çöpe atar.
    Ayı Avcısı (SHORT) stratejileri zaten kendi R:R kapısına sahip → atlanır.
    """
    if min_rr is None:
        min_rr = RR_MINIMUM
    filtered = []
    for sig in signals:
        # Conviction-scored sinyaller kendi R:R puanlamasına sahip, ancak yine de minimum R:R filtresine tabi olmalı (BIST-1 & BIST-2 hariç)
        if sig.get('conviction_score') is not None:
            entry = sig.get("entry_price", 0)
            sl = sig.get("sl", entry)
            tp = sig.get("tp", entry)
            direction = sig.get("signal", "AL")
            if direction == "AL":
                risk = entry - sl
                reward = tp - entry
            else:
                risk = sl - entry
                reward = entry - tp
            rr = reward / risk if risk > 0 else 0
            sig['rr_ratio'] = round(rr, 2) if rr > 0 else 0
            
            strategy = sig.get("strategy", "")
            if strategy in ["BIST 1: DİP AVCILIĞI", "BIST 2: TREND TAKİBİ"]:
                filtered.append(sig)
            elif rr >= min_rr:
                filtered.append(sig)
            else:
                logging.info(
                    f"[FM-01 RR_VETO (Conviction)] {sig.get('ticker')} ({strategy}) → "
                    f"R:R={rr:.2f} < {min_rr} → SİNYAL REDDEDİLDİ. "
                    f"Entry={entry:.4f} SL={sl:.4f} TP={tp:.4f}"
                )
            continue

        entry = sig.get("entry_price", 0)
        sl = sig.get("sl", entry)
        tp = sig.get("tp", entry)
        direction = sig.get("signal", "AL")

        if direction == "AL":
            risk = entry - sl
            reward = tp - entry
        else:  # SAT
            risk = sl - entry
            reward = entry - tp

        if risk <= 0:
            # SL yönü yanlış → sinyal zaten bozuk, geçir (DG-06 yakalayacak)
            filtered.append(sig)
            continue

        rr = reward / risk
        if rr >= min_rr:
            # R:R bilgisini sinyale ekle (Telegram mesajında kullanılacak)
            sig["rr_ratio"] = round(rr, 2)
            filtered.append(sig)
        else:
            logging.info(
                f"[FM-01 RR_VETO] {sig.get('ticker')} ({sig.get('strategy')}) → "
                f"R:R={rr:.2f} < {min_rr} → SİNYAL REDDEDİLDİ. "
                f"Entry={entry:.4f} SL={sl:.4f} TP={tp:.4f}"
            )
    return filtered


# ════════════════════════════════════════
# FM-04: REJİM YÖNETİCİSİ (Regime Manager)
# ════════════════════════════════════════
# İzin/Yasak Matrisi:
# BOĞA  → Tüm stratejiler serbest
# NÖTR  → Kırılım/Squeeze/VWAP engelli (sahte kırılım riski yüksek)
# AYI   → Sadece RS, Dip Avcılığı, OBV, Ayı Avcısı serbest

_BIST_BEAR_BLOCKED = {
    "BIST 2: MEGA TREND TAKİBİ",
    "BIST 3: KIRILIM AVCILIĞI",
    "BIST 5: VOLATİLİTE SIKIŞMASI (SQUEEZE)",
    "BIST 7: VWAP KURUMSAL MIKNATISI",
}

_BIST_NEUTRAL_BLOCKED = {
    "BIST 3: KIRILIM AVCILIĞI",
    "BIST 5: VOLATİLİTE SIKIŞMASI (SQUEEZE)",
}


def _get_bist_regime(xu100_daily):
    """XU100 endeksine bakarak BIST piyasa rejimini belirle.
    
    Returns:
        'BULL'    → EMA20 > EMA50, fiyat EMA20 üstünde
        'BEAR'    → Fiyat EMA20 ve EMA50 altında
        'NEUTRAL' → Aradaki her şey
    """
    if xu100_daily is None or len(xu100_daily) < 50:
        return "NEUTRAL"  # Veri yetersiz → güvenli mod
    try:
        close = xu100_daily['close']
        ema20 = close.ewm(span=20, adjust=False).mean()
        ema50 = close.ewm(span=50, adjust=False).mean()
        last_close = float(close.iloc[-1])
        last_ema20 = float(ema20.iloc[-1])
        last_ema50 = float(ema50.iloc[-1])

        if last_close > last_ema20 and last_ema20 > last_ema50:
            return "BULL"
        elif last_close < last_ema20 and last_close < last_ema50:
            return "BEAR"
        else:
            return "NEUTRAL"
    except Exception as e:
        logging.warning(f"[FM-04 Regime] XU100 rejim hesaplanamadı: {e}")
        return "NEUTRAL"


def _apply_regime_filter(signals, regime, market="BIST"):
    """FM-04: Rejime göre uygun olmayan stratejileri filtrele."""
    if regime == "BULL":
        return signals  # Boğa → hepsine izin

    if market == "BIST":
        blocked = _BIST_BEAR_BLOCKED if regime == "BEAR" else _BIST_NEUTRAL_BLOCKED
    else:
        return signals  # Kripto/Emtia rejim filtresi başka yerde (BTC/DXY)

    filtered = []
    for sig in signals:
        # Conviction-scored sinyaller rejim puanını zaten içeriyor
        if sig.get('conviction_score') is not None:
            filtered.append(sig)
            continue
        strat = sig.get("strategy", "")
        if strat in blocked:
            logging.info(
                f"[FM-04 REGIME_BLOCK] {sig.get('ticker')} ({strat}) → "
                f"Rejim={regime}, strateji ENGELLENDİ."
            )
        else:
            filtered.append(sig)
    return filtered


def _has_absolute_hourly_volume(volume, price, market="KRIPTO"):
    """AM-03: Saatlik mutlak dolar/TL hacim eşiği.
    SMA20'nin 5 katı bile olsa, mutlak hacim düşükse sahte kırılım."""
    dollar_vol = volume * price
    if market == "KRIPTO":
        return dollar_vol >= MIN_HOURLY_DOLLAR_VOL_CRYPTO
    else:
        return dollar_vol >= MIN_HOURLY_TL_VOL_BIST


def _is_funding_safe_for_short(funding_rate):
    """AM-04: Fonlama Vampiri Kalkanı.
    Negatif fonlama = herkes zaten short → balina likidasyonu riski.
    Returns: True (short güvenli), False (short YASAK)
    """
    if funding_rate is None:
        return False  # Veri yoksa short açma
    return funding_rate >= FUNDING_SHORT_BLOCK_THRESHOLD


def _is_in_liquidity_window():
    """AM-06: Likidite Saatleri Zaman Kilidi.
    DXY/Emtia korelasyon stratejileri sadece TSİ 15:30-20:00 arasında tetiklenir.
    Asya seansı hacimsiz yataylıklara kanmayı engeller.
    """
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    start = dt_time(LIQUIDITY_WINDOW_START_HOUR, LIQUIDITY_WINDOW_START_MIN)
    end = dt_time(LIQUIDITY_WINDOW_END_HOUR, LIQUIDITY_WINDOW_END_MIN)
    return start <= now.time() <= end


# ════════════════════════════════════════

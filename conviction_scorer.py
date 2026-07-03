# pylint: disable=too-many-arguments,too-many-locals,too-many-branches,too-many-statements,too-many-return-statements,wrong-import-position,unused-argument,redefined-outer-name,reimported,too-many-lines,multiple-statements,line-too-long,trailing-whitespace,too-many-positional-arguments,broad-exception-caught,import-outside-toplevel,no-else-return,unspecified-encoding,logging-fstring-interpolation,chained-comparison
"""
conviction_scorer.py — Ağırlıklı İnanç Puanlama Motoru (V3.3)
════════════════════════════════════════════════════════════════

Boolean AND hapishanesini yıkar, fuzzy scoring ile değiştirir.
Hard Block'lar korunur (güvenlik), Soft Score'lar esneklik sağlar.

Mimari:
  1. Hard Block → Asla esnetilmez (sıfır likidite, karantina, flash crash)
  2. Soft Score → Sigmoid/linear/log fonksiyonlarla 0-100 puanlanır
  3. Weighted Sum → Ağırlıklı toplam → Conviction Grade
  4. Position Sizing → STRONG=%100, MEDIUM=%50, WATCH=sadece izle

Kullanım:
  from conviction_scorer import (
      check_hard_blocks, score_adx, score_volume_ratio,
      calculate_conviction, ConvictionResult, CONVICTION_STRONG
  )
"""

import math
import logging
import os
import json
import threading
import inspect
from dataclasses import dataclass, field

import pandas as pd
import config

from config import (
    SOFT_ADX_CENTER, SOFT_ADX_K,
    SOFT_VOL_RATIO_CENTER, SOFT_VOL_RATIO_K,
    SOFT_RR_CENTER, SOFT_RR_K,
    SOFT_RSI_TREND_CENTER, SOFT_RSI_TREND_MULT,
    SOFT_RSI_OVERSOLD_BIST_CENTER, SOFT_RSI_OVERSOLD_CRYPTO_CENTER,
    SOFT_SQUEEZE_MIN, SOFT_SQUEEZE_MAX,
    SOFT_EMA_DIP_MULT,
    REGIME_THRESHOLDS_BULL, REGIME_THRESHOLDS_NEUTRAL, REGIME_THRESHOLDS_BEAR,
    # V3.4 Soft Score Magic Number Aktarımı
    SOFT_ADX_MATURITY_START, SOFT_ADX_MATURITY_MULT, SOFT_ADX_MATURITY_MIN,
    SOFT_ADX_MOMENTUM_UP, SOFT_ADX_MOMENTUM_DOWN,
    SOFT_RSI_OVERSOLD_MIN_DIST, SOFT_RSI_OVERSOLD_MAX_DIST,
    SOFT_EMA_ALIGN_PRICE_FAST, SOFT_EMA_ALIGN_FAST_MID, SOFT_EMA_ALIGN_MID_SLOW,
    SOFT_EMA_ALIGN_FAST_SLOW, SOFT_EMA_ALIGN_NO_SLOW,
    SOFT_EMA_DIP_MAX_SCORE, SOFT_EMA_DIP_MIN_SCORE, SOFT_EMA_DIP_STRUCT_BULL,
    SOFT_EMA_DIP_STRUCT_BEAR, SOFT_EMA_DIP_SLOW_BULL, SOFT_EMA_DIP_SLOW_HALF,
    SOFT_EMA_DIP_SLOW_NONE,
    SOFT_EMA_SHORT_PRICE_FAST, SOFT_EMA_SHORT_FAST_MID, SOFT_EMA_SHORT_MID_SLOW,
    SOFT_EMA_SHORT_FAST_SLOW, SOFT_EMA_SHORT_NO_SLOW,
    SOFT_REGIME_BULL, SOFT_REGIME_NEUTRAL, SOFT_REGIME_BEAR,
    SOFT_ENGULFING_YES, SOFT_ENGULFING_NO,
    SOFT_RSI_DIR_UP, SOFT_RSI_DIR_DOWN,
    SOFT_MACRO_ALIGNED, SOFT_MACRO_NOT_ALIGNED,
    SOFT_PENALTY_0, SOFT_PENALTY_1, SOFT_PENALTY_2, SOFT_PENALTY_3_PLUS,
    SOFT_DOLLAR_VOL_CRYPTO_MIN, SOFT_DOLLAR_VOL_CRYPTO_MAX,
    SOFT_DOLLAR_VOL_EMTIA_MIN, SOFT_DOLLAR_VOL_EMTIA_MAX,
    SOFT_DOLLAR_VOL_BIST_MIN, SOFT_DOLLAR_VOL_BIST_MAX,
    SOFT_UNCERTAINTY_PENALTY
)


logger = logging.getLogger(__name__)


# ════════════════════════════════════════
# Conviction Grade Sabitleri
# ════════════════════════════════════════
CONVICTION_STRONG = "STRONG"   # 75-100 → Normal pozisyon
CONVICTION_MEDIUM = "MEDIUM"   # 60-74  → Yarım pozisyon
CONVICTION_WATCH  = "WATCH"    # 45-59  → Sadece izle (Telegram watchlist)
CONVICTION_REJECT = "REJECT"   # 0-44   → Sinyal yok

from config import GLOBAL_STRONG_CONVICTION_SCORE, GLOBAL_MEDIUM_CONVICTION_SCORE, GLOBAL_MIN_CONVICTION_SCORE

# Grade eşikleri
THRESHOLD_STRONG = GLOBAL_STRONG_CONVICTION_SCORE
THRESHOLD_MEDIUM = GLOBAL_MEDIUM_CONVICTION_SCORE
THRESHOLD_WATCH  = GLOBAL_MIN_CONVICTION_SCORE

# Position sizing
POSITION_SIZE_MAP = {
    CONVICTION_STRONG: 100,
    CONVICTION_MEDIUM: 50,
    CONVICTION_WATCH:  0,
    CONVICTION_REJECT: 0,
}


# ════════════════════════════════════════
# Ağırlık Tablosu (toplamı 1.0)
# ════════════════════════════════════════
WEIGHTS = {
    "adx":              0.12,   # 0.15→0.12 (close-price küme azalt)
    "ema_alignment":    0.08,   # 0.10→0.08
    "rsi":              0.10,   # 0.12→0.10
    "rsi_direction":    0.03,   # 0.05→0.03 (RSI gölgesi minimize)
    "volume_ratio":     0.15,   # aynı
    "dollar_volume":    0.08,   # aynı
    "rr_ratio":         0.15,   # 0.12→0.15 (risk/ödül önem arttı)
    "engulfing":        0.07,   # 0.05→0.07
    "regime":           0.08,   # aynı
    "macro":            0.07,   # 0.05→0.07
    "penalty":          0.07,   # 0.05→0.07
}
# Close-price efektif küme: 0.42→0.33 | Bağımsız: 0.35→0.44

# Sanity check: ağırlıklar toplamı 1.0 olmalı
assert (abs(sum(WEIGHTS.values()) - 1.0) < 0.001), f"WEIGHTS toplamı 1.0 olmalı, şu an: {sum(WEIGHTS.values())}"  # nosec

CRYPTO_WEIGHTS = {
    "adx":              0.10,
    "ema_alignment":    0.05,
    "rsi":              0.08,
    "rsi_direction":    0.02,
    "volume_ratio":     0.15,
    "dollar_volume":    0.08,
    "rr_ratio":         0.12,
    "engulfing":        0.05,
    "regime":           0.08,
    "macro":            0.07,
    "penalty":          0.05,
    "oi_crash":         0.05,
    "funding_rate":     0.10,
}
assert (abs(sum(CRYPTO_WEIGHTS.values()) - 1.0) < 0.001), f"CRYPTO_WEIGHTS toplamı 1.0 olmalı, şu an: {sum(CRYPTO_WEIGHTS.values())}"  # nosec


SNIPER_BIST_WEIGHTS = {
    "bbw_squeeze":      0.20,
    "percent_b":        0.20,
    "fvg_sfp":          0.20,
    "volume_ratio":     0.15,
    "dollar_volume":    0.05,
    "rr_ratio":         0.10,
    "regime":           0.05,
    "macro":            0.05,
}
assert (abs(sum(SNIPER_BIST_WEIGHTS.values()) - 1.0) < 0.001), f"SNIPER_BIST_WEIGHTS toplamı 1.0 olmalı, şu an: {sum(SNIPER_BIST_WEIGHTS.values())}"  # nosec

SNIPER_CRYPTO_WEIGHTS = {
    "bbw_squeeze":      0.10,
    "percent_b":        0.10,
    "fvg_sfp":          0.10,
    "volume_ratio":     0.25,
    "dollar_volume":    0.05,
    "rr_ratio":         0.15,
    "regime":           0.10,
    "macro":            0.10,
    "funding_rate":     0.05,
}
assert (abs(sum(SNIPER_CRYPTO_WEIGHTS.values()) - 1.0) < 0.001), f"SNIPER_CRYPTO_WEIGHTS toplamı 1.0 olmalı, şu an: {sum(SNIPER_CRYPTO_WEIGHTS.values())}"  # nosec



# ════════════════════════════════════════
# Sigmoid / Fuzzy Puanlama Fonksiyonları
# ════════════════════════════════════════

def _is_nan(value):
    """NaN kontrolü — None veya float NaN."""
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    return False


def gaussian_score(value: float, center: float, width: float) -> float:
    """
    Gaussian (Bell Curve) tabanlı yumuşak eşik puanlama.
    
    Belirli bir 'center' noktasında maksimum puanı (100) verir.
    'width' parametresi eğrinin genişliğini belirler.
    
    Örnek (center=18.5, width=5.0):
      ADX=18.5 → 100
      ADX=13.5 → ~60
      ADX=23.5 → ~60
      ADX=28.5 → ~13
    """
    if _is_nan(value) or width == 0:
        return 0.0
    try:
        return 100.0 * math.exp(-0.5 * ((value - center) / width) ** 2)
    except OverflowError:
        return 0.0



def sigmoid_score(value: float, center: float, k: float = 0.3) -> float:
    """
    Sigmoid tabanlı yumuşak eşik puanlama.

    Hard threshold'un yarattığı 0/1 uçurumu yerine yumuşak S-eğrisi geçişi.
    center = eski hard threshold (örn: ADX=25)
    k = geçiş keskinliği (düşük=daha yumuşak, yüksek=daha keskin)

    Örnek (center=25, k=0.3):
      ADX=15 → ~5,  ADX=20 → ~18,  ADX=25 → 50,
      ADX=30 → ~82, ADX=35 → ~95
    """
    if _is_nan(value):
        return 0.0
    try:
        return 100.0 / (1.0 + math.exp(-k * (value - center)))
    except OverflowError:
        return 0.0 if value < center else 100.0


def linear_score(value: float, low: float, high: float) -> float:
    """Lineer interpolasyon: low → 0 puan, high → 100 puan."""
    if _is_nan(value):
        return 0.0
    if value <= low:
        return 0.0
    if value >= high:
        return 100.0
    return (value - low) / (high - low) * 100.0


def inverse_linear_score(value: float, low: float, high: float) -> float:
    """Ters lineer: low → 100 puan (oversold iyi), high → 0 puan."""
    return 100.0 - linear_score(value, low, high)


def log_score(value: float, min_val: float, max_val: float) -> float:
    """Logaritmik puanlama (hacim gibi geniş aralıklı metrikler için)."""
    if _is_nan(value) or value <= 0 or min_val <= 0:
        return 0.0
    log_val = math.log(max(value, min_val))
    log_min = math.log(min_val)
    log_max = math.log(max(max_val, min_val + 1))
    if log_max <= log_min:
        return 50.0
    return min(100.0, max(0.0, (log_val - log_min) / (log_max - log_min) * 100))


# ════════════════════════════════════════
# Soft Score Hesaplayıcılar
# ════════════════════════════════════════

def calculate_autopsy_soft_penalty(
    price: float,
    sma200_1d: float,
    rsi_1h: float,
    volume_ratio: float,
    is_long: bool = True,
    strategy_type: str = "TREND"
) -> float:
    """
    Simülasyon otopsi bulgularına göre soft puan cezası hesaplar (V3.5).
    Hard block uygulamaz, puanı düşürerek pozisyon büyüklüğünü (grade) aşağı çeker.
    Strateji tipine duyarlıdır (Trend-Following vs Mean Reversion).
    """
    # Dynamic parameter auto-resolution from caller frame stack
    if sma200_1d is None or rsi_1h is None or volume_ratio is None or strategy_type == "TREND":
        try:
            import inspect
            curr_frame = inspect.currentframe()
            for _ in range(5):
                if curr_frame is None:
                    break
                locs = curr_frame.f_locals
                if "last_1d" in locs and sma200_1d is None:
                    last_1d = locs["last_1d"]
                    if hasattr(last_1d, "get"):
                        sma200_1d = last_1d.get("SMA_200")
                if "last_1h" in locs and rsi_1h is None:
                    last_1h = locs["last_1h"]
                    if hasattr(last_1h, "get"):
                        rsi_1h = last_1h.get("RSI_14")
                elif "last_4h" in locs and rsi_1h is None:
                    last_4h = locs["last_4h"]
                    if hasattr(last_4h, "get"):
                        rsi_1h = last_4h.get("RSI_14")
                if "volume_ratio" in locs and volume_ratio is None:
                    volume_ratio = locs["volume_ratio"]
                if "strategy_type" in locs and strategy_type == "TREND":
                    strategy_type = locs["strategy_type"]
                curr_frame = curr_frame.f_back
        except Exception:  # nosec
            pass

    penalty = 0.0
    
    # Check if strategy is Trend-Following/Breakout
    strat_upper = str(strategy_type).upper() if strategy_type else "TREND"
    is_trend = "TREND" in strat_upper or "BREAKOUT" in strat_upper

    if is_trend:
        # 1. Günlük SMA 200 Uzaklık Soft Cezası - Sadece Trend/Breakout stratejilerinde
        if not _is_nan(price) and not _is_nan(sma200_1d) and sma200_1d > 0:
            if is_long:
                # Long işlem: Fiyat SMA 200'ün altındaysa ceza uygula
                if price < sma200_1d:
                    dist_pct = ((sma200_1d - price) / sma200_1d) * 100
                    # %0-%7.5 arası lineer ceza (maks -7.5 puan - %25 indirimli)
                    penalty -= min(7.5, dist_pct * 0.75)
            else:
                # Short işlem: Fiyat SMA 200'ün üzerindeyken ceza uygula
                if price > sma200_1d:
                    dist_pct = ((price - sma200_1d) / sma200_1d) * 100
                    # %0-%7.5 arası lineer ceza (maks -7.5 puan - %25 indirimli)
                    penalty -= min(7.5, dist_pct * 0.75)
                    
        # 2. 1H RSI Soft Cezası - KALDIRILDI (Trend kırılımlarında RSI'ın doğal olarak yüksek olması çelişki yaratıyordu)

    # 3. Kırılım Hacmi (Volume Ratio) Soft Cezası
    # Zayıf breakout volume (displacement eksikliği) cezalandırılır
    # Eşik: Trend/Breakout için 3.5x, Mean Reversion/Dip için 3.0x
    # Gelen hacim oranına göre ceza lineer ölçeklenir (sabit -10 yerine, maks -3.75 - %25 indirimli)
    if not _is_nan(volume_ratio):
        threshold = 3.5 if is_trend else 3.0
        if volume_ratio < threshold:
            ratio_clamped = max(0.0, volume_ratio)
            penalty -= ((threshold - ratio_clamped) / threshold) * 3.75
            
    return round(penalty, 2)


def score_adx(
    adx_value: float, 
    adx_prev: float = None,
    adx_mode: str = "sigmoid",
    adx_center: float = SOFT_ADX_CENTER,
    adx_width: float = SOFT_ADX_K
) -> float:
    """
    ADX puanlama — sigmoid veya gaussian (bell curve) geçişi.

    Sigmoid modu: Düşükten yükseğe S-eğrisi (k=keskinlik) + olgunlaşma cezası.
    Gaussian modu: Optimum bir 'center' etrafında zirve yapan çan eğrisi (width=genişlik).
    """
    if _is_nan(adx_value):
        return 0.0

    if adx_mode == "gaussian":
        base = gaussian_score(adx_value, center=adx_center, width=adx_width)
    else:
        base = sigmoid_score(adx_value, center=adx_center, k=adx_width)

        # Olgunlaşma cezası (sadece sigmoid modunda eski davranış korunsun)
        if adx_value > SOFT_ADX_MATURITY_START:
            decay = (adx_value - SOFT_ADX_MATURITY_START) * SOFT_ADX_MATURITY_MULT
            base = max(SOFT_ADX_MATURITY_MIN, base - decay)

    # Momentum bonusu/cezası: yükselen ADX = güç artıyor
    if not _is_nan(adx_prev):
        if adx_value > adx_prev:
            base = min(100, base * SOFT_ADX_MOMENTUM_UP)
        else:
            base *= SOFT_ADX_MOMENTUM_DOWN

    return round(base, 1)


def score_rsi_oversold(rsi: float, market: str = "BIST") -> float:
    """
    RSI dip avcılığı puanlama (düşük RSI = yüksek puan).

    Eski: RSI < 35 → GİR, RSI >= 35 → TAM REDDET
    Yeni: RSI=15→100, 25→80, 35→50, 40→30, 50→0
    """
    if _is_nan(rsi):
        return 0.0
    center = SOFT_RSI_OVERSOLD_BIST_CENTER if market == "BIST" else SOFT_RSI_OVERSOLD_CRYPTO_CENTER
    return round(inverse_linear_score(rsi, center - SOFT_RSI_OVERSOLD_MIN_DIST, center + SOFT_RSI_OVERSOLD_MAX_DIST), 1)


def score_rsi_trend(rsi: float, market: str = "BIST") -> float:
    """
    RSI trend takibi puanlama (orta bant = güçlü trend).
    Trend stratejilerinde RSI 40-60 bandı en sağlıklı.
    """
    if _is_nan(rsi):
        return 0.0
    center = SOFT_RSI_TREND_CENTER
    dist = abs(rsi - center)
    return round(max(0, 100 - dist * SOFT_RSI_TREND_MULT), 1)


def score_volume_ratio(volume: float, vol_sma: float) -> float:
    """
    Hacim/SMA oranı puanlama — sigmoid ile yumuşak eşik.

    Eski: vol > 1.5x SMA → OK, değilse → TAM REDDET
    Yeni: 0.5x→2, 0.8x→10, 1.0x→27, 1.5x→73, 2.0x→95, 3.0x→100
    """
    if _is_nan(vol_sma) or vol_sma <= 0 or _is_nan(volume):
        return 0.0
    ratio = volume / vol_sma
    return round(sigmoid_score(ratio, center=SOFT_VOL_RATIO_CENTER, k=SOFT_VOL_RATIO_K), 1)


def score_dollar_volume(dollar_vol: float, market: str = "KRIPTO") -> float:
    """Mutlak dolar/TL hacim puanlama (logaritmik ölçek)."""
    if _is_nan(dollar_vol) or dollar_vol <= 0:
        return 0.0
    if market == "KRIPTO":
        return round(log_score(dollar_vol, SOFT_DOLLAR_VOL_CRYPTO_MIN, SOFT_DOLLAR_VOL_CRYPTO_MAX), 1)
    elif market == "EMTIA":
        return round(log_score(dollar_vol, SOFT_DOLLAR_VOL_EMTIA_MIN, SOFT_DOLLAR_VOL_EMTIA_MAX), 1)
    else:  # BIST
        return round(log_score(dollar_vol, SOFT_DOLLAR_VOL_BIST_MIN, SOFT_DOLLAR_VOL_BIST_MAX), 1)


def score_oi_crash(oi_crashed: bool) -> float:
    """OI çöküşü (Açık Pozisyon sıfırlaması) puanlama. Varsa bonus (100), yoksa nötr (50)."""
    if oi_crashed:
        return 100.0
    return 50.0


def score_funding_rate(funding_rate: float, direction: str = "long") -> float:
    """
    Fonlama Oranı puanlama.
    Long yönü için: negatif fonlama çok iyi (100), aşırı pozitif fonlama kötü (0).
    Short yönü için: aşırı pozitif fonlama çok iyi (100), negatif fonlama kötü (0).
    """
    if funding_rate is None or _is_nan(funding_rate):
        return 50.0

    if direction == "long":
        if funding_rate <= -0.01:
            return 100.0
        elif funding_rate <= 0.0:
            return 80.0
        elif funding_rate <= 0.01:
            return 50.0
        else:
            return max(0.0, 50.0 - (funding_rate * 1000))
    else:
        if funding_rate >= 0.01:
            return 100.0
        elif funding_rate >= 0.0:
            return 80.0
        elif funding_rate >= -0.01:
            return 50.0
        else:
            return max(0.0, 50.0 + (funding_rate * 1000))


def score_rr_ratio(rr: float, regime: str = "NEUTRAL", is_short: bool = False) -> float:
    """
    R:R oranı puanlama — sigmoid ile yumuşak eşik, piyasa rejimine duyarlı.

    Avantajlı rejimde (Long için BULL, Short için BEAR) -> center = 1.5 (esnek)
    Dezavantajlı rejimde (Long için BEAR, Short için BULL) -> center = 2.5 (seçici)
    Nötr rejimde -> center = 2.0 (config.SOFT_RR_CENTER)
    """
    if _is_nan(rr) or rr <= 0:
        return 0.0

    is_favorable = (regime == "BULL" and not is_short) or (regime == "BEAR" and is_short)
    is_unfavorable = (regime == "BEAR" and not is_short) or (regime == "BULL" and is_short)

    if is_favorable:
        center = 1.5
    elif is_unfavorable:
        center = 2.5
    else:
        center = SOFT_RR_CENTER  # 2.0

    return round(sigmoid_score(rr, center=center, k=SOFT_RR_K), 1)



def score_ema_alignment(price: float, ema_fast: float, ema_mid: float,
                        ema_slow: float = None) -> float:
    """
    EMA dizilimi puanlama — kademeli ödül.
    Tam dizilim (price > fast > mid > slow) = 100
    Kısmi dizilim = kademeli puan (sıfır değil!)
    """
    if _is_nan(price) or _is_nan(ema_fast) or _is_nan(ema_mid):
        return 0.0

    score = 0.0
    if price > ema_fast:
        score += SOFT_EMA_ALIGN_PRICE_FAST
    if ema_fast > ema_mid:
        score += SOFT_EMA_ALIGN_FAST_MID
    if not _is_nan(ema_slow):
        if ema_mid > ema_slow:
            score += SOFT_EMA_ALIGN_MID_SLOW
        elif ema_fast > ema_slow:
            score += SOFT_EMA_ALIGN_FAST_SLOW
    else:
        score += SOFT_EMA_ALIGN_NO_SLOW  # Veri yoksa nötr

    return score


def score_ema_dip_distance(price: float, ema_fast: float, ema_mid: float,
                           ema_slow: float = None) -> float:
    """
    Dip avcılığı için EMA mesafe puanlama (B-01 fix).
    Fiyat EMA altındaysa → yüksek puan (derin dip = iyi fırsat).
    EMA yapısı hala boğaysa (fast > mid) → bonus.
    """
    if _is_nan(price) or _is_nan(ema_fast) or _is_nan(ema_mid):
        return 0.0

    score = 0.0

    # Fiyatın EMA'dan uzaklığı: daha aşağıda = daha iyi
    if price < ema_fast:
        pct_below = (ema_fast - price) / ema_fast * 100
        score += min(SOFT_EMA_DIP_MAX_SCORE, pct_below * SOFT_EMA_DIP_MULT)  # Dinamik çarpan
    else:
        score += SOFT_EMA_DIP_MIN_SCORE  # EMA üstünde = minimal puan

    # EMA yapısal sağlık: hala boğa dizilimi = bounce şansı yüksek
    if ema_fast > ema_mid:
        score += SOFT_EMA_DIP_STRUCT_BULL  # Yapı bozulmamış = büyük bonus
    else:
        score += SOFT_EMA_DIP_STRUCT_BEAR  # Yapı bozuk = düşük bonus

    # Slow EMA bonus
    if not _is_nan(ema_slow):
        if ema_mid > ema_slow:
            score += SOFT_EMA_DIP_SLOW_BULL  # Uzun vadeli trend sağlam
        elif ema_fast > ema_slow:
            score += SOFT_EMA_DIP_SLOW_HALF
    else:
        score += SOFT_EMA_DIP_SLOW_NONE  # Veri yoksa nötr

    return min(100.0, score)


# 99 yapılmıştır
# SHORT EMA dizilimi puanlamasında eksik veri durumunda nötr 50.0 yerine SOFT_UNCERTAINTY_PENALTY (0.0) dönülmektedir.
def score_ema_short(price: float, ema_fast: float, ema_mid: float,
                    ema_slow: float = None) -> float:
    """
    SHORT stratejileri için EMA dizilimi puanlama (1D fix).
    Fiyat < EMA'lar → yüksek puan (düşüş trendi = SHORT için iyi).
    Ters dizilim (fast < mid < slow) = en iyi.
    """
    if _is_nan(price) or _is_nan(ema_fast) or _is_nan(ema_mid):
        return SOFT_UNCERTAINTY_PENALTY  # Veri yoksa belirsizlik cezası uygulanır

    score = 0.0
    if price < ema_fast:
        score += SOFT_EMA_SHORT_PRICE_FAST  # Fiyat EMA altında = SHORT güçlü
    if ema_fast < ema_mid:
        score += SOFT_EMA_SHORT_FAST_MID  # Death cross = düşüş trendi
    if not _is_nan(ema_slow):
        if ema_mid < ema_slow:
            score += SOFT_EMA_SHORT_MID_SLOW  # Tam ayı dizilimi
        elif ema_fast < ema_slow:
            score += SOFT_EMA_SHORT_FAST_SLOW
    else:
        score += SOFT_EMA_SHORT_NO_SLOW  # Veri yoksa nötr

    return score


def score_regime(regime: str) -> float:
    """Piyasa rejimi puanlama — LONG stratejiler (BULL/NEUTRAL/BEAR)."""
    return {"BULL": SOFT_REGIME_BULL, "NEUTRAL": SOFT_REGIME_NEUTRAL, "BEAR": SOFT_REGIME_BEAR}.get(regime, SOFT_UNCERTAINTY_PENALTY)


def score_regime_short(regime: str) -> float:
    """SHORT stratejileri için piyasa rejimi (BEAR = iyi, BULL = kötü)."""
    return {"BEAR": SOFT_REGIME_BULL, "NEUTRAL": SOFT_REGIME_NEUTRAL, "BULL": SOFT_REGIME_BEAR}.get(regime, SOFT_UNCERTAINTY_PENALTY)


def score_engulfing(has_engulfing: bool) -> float:
    """
    Engulfing mum onayı puanlama.
    Eski: yoksa → TAM REDDET
    Yeni: var→SOFT_ENGULFING_YES, yok→SOFT_ENGULFING_NO (cezalandır ama öldürme)
    """
    return SOFT_ENGULFING_YES if has_engulfing else SOFT_ENGULFING_NO


# 99 yapılmıştır
# RSI yön puanlamasında eksik veri durumunda nötr 50.0 yerine SOFT_UNCERTAINTY_PENALTY (0.0) dönülmektedir.
def score_rsi_direction(rsi_current: float, rsi_prev: float) -> float:
    """RSI yön puanlama: yükseliyor mu düşüyor mu."""
    if _is_nan(rsi_current) or _is_nan(rsi_prev):
        return SOFT_UNCERTAINTY_PENALTY
    if rsi_current > rsi_prev:
        return SOFT_RSI_DIR_UP
    elif rsi_current < rsi_prev:
        return SOFT_RSI_DIR_DOWN
    return 50.0


def score_macro_alignment(is_aligned: bool) -> float:
    """Makro uyum puanlama (endeks/BTC yönü)."""
    return SOFT_MACRO_ALIGNED if is_aligned else SOFT_MACRO_NOT_ALIGNED


def score_penalty_level(consecutive_sl: int) -> float:
    """
    Ceza seviyesi puanlama.
    """
    if consecutive_sl <= 0:
        return SOFT_PENALTY_0
    elif consecutive_sl == 1:
        return SOFT_PENALTY_1
    elif consecutive_sl == 2:
        return SOFT_PENALTY_2
    return SOFT_PENALTY_3_PLUS


# ════════════════════════════════════════
# Sniper (Keskin Nişancı) Soft Skorlama
# ════════════════════════════════════════

def score_bbw_squeeze(bbw: float, kcw: float) -> float:
    """
    Sniper Kanun 1: Volatilite Patlaması (BBW >= KCW).
    Yumuşatılmış Kural: %10 tolerans ile soft geçiş (aşırı sinyal fışkırmasını önler).
    """
    if _is_nan(bbw) or _is_nan(kcw) or kcw == 0:
        return 0.0
    if bbw >= kcw:
        return 100.0  # Tam isabet (Patlama gerçekleşti)
    
    # %10 tolerans ile lineer yumuşak geçiş
    if bbw >= kcw * 0.90:
        return (bbw - kcw * 0.90) / (kcw * 0.10) * 100.0
    return 0.0

def score_percent_b(pb: float, pb_min: float = 0.0, pb_max: float = 1.0) -> float:
    """
    Sniper Kanun 2: %B Pullback sınırları.
    Sınır dışına taşmalarda lineer soft ceza. Tolerans %8'e çekilerek dengelendi.
    """
    if _is_nan(pb):
        return 0.0
    
    if pb_min <= pb <= pb_max:
        return 100.0
    
    # Sınırın %8 (0.08) dışına kadar tolerans.
    tolerance = 0.08
    if pb < pb_min:
        dist = pb_min - pb
        if dist < tolerance:
            return 100.0 * (1.0 - (dist / tolerance))
        return 0.0
    else:
        dist = pb - pb_max
        if dist < tolerance:
            return 100.0 * (1.0 - (dist / tolerance))
        return 0.0

def score_fvg_sfp(fvg_present: bool, sfp_present: bool) -> float:
    """
    Sniper Kanun 3: Mıknatıs/Tuzak (FVG veya SFP).
    Biri varsa 100 puan. Yoksa soft geçiş için baseline 15 puan verilerek filtrelendi.
    """
    if fvg_present or sfp_present:
        return 100.0
    return 15.0  # 30'dan 15'e düşürülerek sinyal kalitesi artırıldı.

def score_smc_zones(in_supply_zone: bool, in_demand_zone: bool, is_long: bool) -> float:
    """
    SMC Arz/Talep (Supply/Demand) bölgelerini puanlar. (Eski HB-9'un Soft versiyonu)
    Kullanıcı talebi üzerine SADECE LONG işlemlerde aktif (HB SADECE LONG İŞLEMDE BAKILSIN).
    - LONG işlemler için Demand bölgesindeyse ödüllendirir, Supply bölgesindeyse cezalandırır.
    """
    if is_long:
        if in_demand_zone:
            return 80.0  # Bonus (Destekten long)
        if in_supply_zone:
            return 20.0  # Ceza (Dirençten long)
    return 50.0  # Nötr

# ════════════════════════════════════════
# Hard Block Kontrolleri
# ════════════════════════════════════════

# 99 yapılmıştır
# check_hard_blocks fonksiyonuna kritik veri eksikliği için is_core_indicators_nan parametresi ve HB-8 bloğu eklenmiştir.
def check_hard_blocks(
    volume: float = None,
    price: float = None,
    vol_sma: float = None,
    is_quarantined: bool = False,
    is_circuit_open: bool = False,
    sl_direction_ok: bool = True,
    rr_ratio: float = None,
    consecutive_sl: int = 0,
    is_core_indicators_nan: bool = False,
    min_volume_usd: float = 50_000,
    in_supply_zone: bool = None,
    willy_ema: float = None,
    is_long: bool = False,
) -> tuple:
    """
    Asla esnetilemeyen güvenlik kontrolleri. (Sadece NaN, Karantina, Devre Kesici)
    Diğer filtreler Soft Score tarafında değerlendirilir.
    """
    if is_quarantined:
        return True, "HB-2: Varlık karantinada — veri güvenilmez"

    if is_circuit_open:
        return True, "HB-3: Devre Kesici aktif — sistem korumada"

    if is_core_indicators_nan:
        return True, "HB-8: Kritik teknik gösterge verisi eksik (NaN) — işlem yapılamaz"
        
    if volume is not None and price is not None:
        if (volume * price) < min_volume_usd:
            return True, f"HB-4: Dolar Hacmi Yetersiz ({(volume * price):.0f} < {min_volume_usd})"
            
    if vol_sma is not None and volume is not None and vol_sma > 0:
        if (volume / vol_sma) < 0.8:
            return True, f"HB-5: Göreceli Hacim Çok Düşük (Kırılım hacimsiz - {(volume/vol_sma):.2f}x)"

    if not sl_direction_ok:
        return True, "HB-6: Stop Loss yönü hatalı"
        
    if rr_ratio is not None and rr_ratio < 0.4:
        return True, "HB-7: R:R oranı çok düşük (< 0.4)"

    if in_supply_zone is not None:
        if is_long and in_supply_zone:
            return True, "HB-9: Fiyat geçerli bir Supply (Arz) bölgesinde, LONG girilemez"
            
    if willy_ema is not None:
        if not is_long and willy_ema < -80:
            return True, "HB-10: Varlık aşırı satımda (Willy EMA < -80), SHORT girilemez"

    return False, ""


# ════════════════════════════════════════
# Conviction Sonuç Yapısı
# ════════════════════════════════════════

@dataclass
class ConvictionResult:
    """Conviction hesaplama sonucu."""
    total_score: float = 0.0
    grade: str = CONVICTION_REJECT
    hard_blocked: bool = False
    hard_block_reason: str = ""
    component_scores: dict = field(default_factory=dict)
    position_size_pct: float = 0.0

    def to_reason_suffix(self) -> str:
        """Sinyal reason metnine eklenecek conviction özeti."""
        emoji = {
            CONVICTION_STRONG: "🟢",
            CONVICTION_MEDIUM: "🟡",
            CONVICTION_WATCH: "🟠",
            CONVICTION_REJECT: "🔴",
        }
        e = emoji.get(self.grade, "⚪")
        return f"\n{e} Conviction: {self.total_score:.0f}/100 ({self.grade}) | Poz: %{self.position_size_pct:.0f}"

    def top_factors(self, n: int = 3) -> list:
        """En yüksek puanlı faktörleri döndür."""
        return sorted(self.component_scores.items(),
                      key=lambda x: x[1], reverse=True)[:n]

    def weak_factors(self, n: int = 2) -> list:
        """En düşük puanlı faktörleri döndür."""
        return sorted(self.component_scores.items(),
                      key=lambda x: x[1])[:n]


# ════════════════════════════════════════
# Conviction A/B Test — Shadow Evaluation
# ════════════════════════════════════════

_ab_lock = threading.Lock()
AB_STATS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'ab_test_stats.json')


def _ab_evaluate(ticker, score, control_grade, experiment_thresholds):
    """A/B Test: Experiment eşikleriyle shadow grade hesapla ve logla."""
    # Experiment grade hesapla
    if score >= experiment_thresholds['STRONG']:
        exp_grade = 'STRONG'
    elif score >= experiment_thresholds['MEDIUM']:
        exp_grade = 'MEDIUM'
    elif score >= experiment_thresholds['WATCH']:
        exp_grade = 'WATCH'
    else:
        exp_grade = 'REJECT'

    # Fark varsa logla
    if control_grade != exp_grade:
        logging.info(
            f'[A/B Test] {ticker}: Score={score:.0f} '
            f'Control={control_grade} vs Experiment={exp_grade} — '
            f'FARK TESPİT EDİLDİ'
        )

    # İstatistikleri dosyaya yaz (atomik)
    _ab_update_stats(ticker, score, control_grade, exp_grade)


def _ab_update_stats(ticker, score, control_grade, exp_grade):
    """A/B test istatistiklerini JSON dosyasına atomik olarak yaz."""
    with _ab_lock:
        stats = {}
        if os.path.exists(AB_STATS_FILE):
            try:
                with open(AB_STATS_FILE, 'r') as f:
                    stats = json.load(f)
            except (json.JSONDecodeError, IOError):
                stats = {}

        # Toplam sayaçlar
        stats.setdefault('total_evaluations', 0)
        stats['total_evaluations'] += 1
        stats.setdefault('divergence_count', 0)
        if control_grade != exp_grade:
            stats['divergence_count'] += 1

        # Grade dağılımları
        for group_name, grade in [('control', control_grade), ('experiment', exp_grade)]:
            key = f'{group_name}_grades'
            stats.setdefault(key, {})
            stats[key][grade] = stats[key].get(grade, 0) + 1

        # Son 10 farklılık kaydı (ring buffer)
        if control_grade != exp_grade:
            divergences = stats.setdefault('recent_divergences', [])
            from datetime import datetime, timezone
            divergences.append({
                'ticker': ticker,
                'score': round(score, 1),
                'control': control_grade,
                'experiment': exp_grade,
                'time': datetime.now(timezone.utc).isoformat()
            })
            stats['recent_divergences'] = divergences[-10:]  # Son 10

        # Atomik yaz
        import tempfile
        tmp_fd, tmp_path = tempfile.mkstemp(
            dir=os.path.dirname(AB_STATS_FILE), suffix='.tmp'
        )
        try:
            with os.fdopen(tmp_fd, 'w') as f:
                json.dump(stats, f, indent=2, ensure_ascii=False)
            os.replace(tmp_path, AB_STATS_FILE)
        except Exception:
            if os.path.exists(tmp_path):
                os.remove(tmp_path)


# ════════════════════════════════════════
# Data Guard ve Conflict Resolver Ceza Hesaplayıcıları (V3.4)
# ════════════════════════════════════════

def score_data_guard(
    dollar_volume: float,
    is_darth_maul,
    is_gap,
    cmf: float,
    is_liquidity_window: bool,
    min_volume_usd: float = 50_000,
    optimum_volume_usd: float = 250_000
) -> float:
    """
    Data Guard soft cezalarını hesaplar.
    Dönen değer toplam skordan düşülecek ceza puanıdır (negatif veya 0).
    """
    from config import (
        GAP_THRESHOLD_PCT,
        DARTH_MAUL_BODY_RATIO,
        DATA_GUARD_PENALTY_GAP,
        DATA_GUARD_PENALTY_LIQUIDITY_WINDOW,
        DATA_GUARD_PENALTY_DARTH_MAUL,
        DATA_GUARD_PENALTY_CMF_WASH_TRADE
    )
    
    # Handle boolean or float for is_gap (treating it as gap_pct)
    if isinstance(is_gap, bool):
        gap_val = GAP_THRESHOLD_PCT if is_gap else 0.0
    else:
        gap_val = float(is_gap) if is_gap is not None else 0.0

    # Handle boolean or float for is_darth_maul (treating it as darth_maul_ratio)
    if isinstance(is_darth_maul, bool):
        # If True, it is a Darth Maul candle (extreme dev candle, ratio = 0.0)
        # If False, it is not a Darth Maul candle (ratio = 1.0)
        dm_ratio_val = 0.0 if is_darth_maul else 1.0
    else:
        dm_ratio_val = float(is_darth_maul) if is_darth_maul is not None else 1.0

    penalty = 0.0
    
    # 1. Proportional Gap Penalty
    if gap_val > 0.0:
        penalty -= min(abs(DATA_GUARD_PENALTY_GAP), (gap_val / GAP_THRESHOLD_PCT) * abs(DATA_GUARD_PENALTY_GAP))
        
    # 2. Proportional Darth Maul Penalty
    if not _is_nan(dm_ratio_val) and dm_ratio_val < DARTH_MAUL_BODY_RATIO:
        penalty -= min(abs(DATA_GUARD_PENALTY_DARTH_MAUL), (1.0 - (dm_ratio_val / DARTH_MAUL_BODY_RATIO)) * abs(DATA_GUARD_PENALTY_DARTH_MAUL))
        
    # 3. Proportional CMF Penalty
    if not _is_nan(cmf) and cmf < 0.0:
        penalty -= min(abs(DATA_GUARD_PENALTY_CMF_WASH_TRADE), abs(cmf) * 37.5)
        
    # 4. Liquidity Window Penalty
    if not is_liquidity_window:
        penalty += DATA_GUARD_PENALTY_LIQUIDITY_WINDOW
        
    # Hacim soft cezası: mutlak alt sınır ile optimum arasında orantısal ceza (maks -18.75 - %25 indirimli)
    if dollar_volume > 0 and dollar_volume < optimum_volume_usd:
        clamped_vol = max(min_volume_usd, dollar_volume)
        ratio = (optimum_volume_usd - clamped_vol) / (optimum_volume_usd - min_volume_usd) if optimum_volume_usd > min_volume_usd else 1.0
        # Dinamik hacim cezası
        base_penalty = 11.25
        # Eğer hacim çok düşükse (min_volume_usd'ye yakınsa) çarpan artar
        vol_urgency = 1.0 + (ratio * 0.5) 
        penalty -= (base_penalty * ratio * vol_urgency)
        
    return penalty


def score_conflict_resolver(
    adx: float,
    regime: str,
    strategy_type: str,
    is_long: bool = True
) -> tuple:
    """
    Sinyal çelişki cezalarını hesaplar.
    Döner: (penalty_points, apply_bear_multiplier)
    """
    from config import (
        CONFLICT_RESOLVER_ADX_TREND_LIMIT,
        CONFLICT_RESOLVER_ADX_RANGING_LIMIT,
        CONFLICT_RESOLVER_ADX_TREND_PENALTY_MULT,
        CONFLICT_RESOLVER_ADX_RANGING_PENALTY_MULT,
    )
    
    penalty = 0.0
    apply_bear_penalty = False
    
    if not _is_nan(adx):
        strat_upper = str(strategy_type).upper()
        is_mr = "MEAN_REVERSION" in strat_upper or "DIP" in strat_upper or "REVERSAL" in strat_upper
        is_tf = "TREND" in strat_upper or "BREAKOUT" in strat_upper or "FOLLOWING" in strat_upper
        
        if is_mr and adx > CONFLICT_RESOLVER_ADX_TREND_LIMIT:
            diff = adx - CONFLICT_RESOLVER_ADX_TREND_LIMIT
            penalty -= (diff * CONFLICT_RESOLVER_ADX_TREND_PENALTY_MULT)
        elif is_tf and adx < CONFLICT_RESOLVER_ADX_RANGING_LIMIT:
            diff = CONFLICT_RESOLVER_ADX_RANGING_LIMIT - adx
            penalty -= (diff * CONFLICT_RESOLVER_ADX_RANGING_PENALTY_MULT)
            
    if is_long and regime == "BEAR":
        apply_bear_penalty = True
        
    return penalty, apply_bear_penalty


# ════════════════════════════════════════
# Ana Conviction Hesaplayıcı
# ════════════════════════════════════════

def calculate_conviction(
    scores: dict,
    hard_blocked: bool = False,
    block_reason: str = "",
    weights: dict = None,
    ctx: dict = None,
) -> ConvictionResult:
    """
    Ağırlıklı conviction skoru hesapla.

    Args:
        scores: Her soft faktör için 0-100 arası puan dict'i.
        hard_blocked: Hard block tetiklendi mi
        block_reason: Hard block nedeni
        weights: Özel ağırlıklar (None ise global WEIGHTS kullanılır)
        ctx: Strateji bağlamı (is_quarantined, is_circuit_open kontrolleri için)

    Returns:
        ConvictionResult
    """
    result = ConvictionResult()
    
    # 99 yapılmıştır
    # Gönderilen soft skorlar içinde NaN varsa halüsinasyonu önlemek için HB-8 ile veto edilirdi.
    # Yeni yapı: Direkt reddetmek yerine -9.0 soft ceza (Soft Penalty) verilir.
    is_core_indicators_nan = False
    for factor in (weights or WEIGHTS).keys():
        if factor in scores and _is_nan(scores[factor]):
            is_core_indicators_nan = True
            break

    nan_penalty = 0.0
    if is_core_indicators_nan:
        nan_penalty = -6.75
        logger.debug("[Conviction] NaN veri tespit edildi, -6.75 soft ceza uygulandı.")

    if not hard_blocked and ctx is not None:
        symbol = ctx.get("symbol")
        is_q = ctx.get("is_quarantined", False)
        is_cb = ctx.get("is_circuit_open", False)

        if symbol and not is_q:
            try:
                from quarantine import is_quarantined as _is_q
                is_q = _is_q(symbol)
            except Exception as eq:
                logger.error(f"[Conviction] Karantina durumu kontrol edilemedi ({symbol}): {eq}")
        if symbol and not is_cb:
            try:
                from circuit_breaker import is_circuit_open as _is_cb
                is_cb = _is_cb(symbol)
            except Exception as ecb:
                logger.error(f"[Conviction] Devre kesici durumu kontrol edilemedi ({symbol}): {ecb}")

        if is_q:
            hard_blocked = True
            block_reason = "HB-2: Varlık karantinada — veri güvenilmez"
        elif is_cb:
            hard_blocked = True
            block_reason = "HB-3: Devre Kesici aktif — sistem korumada"

    result.hard_blocked = hard_blocked
    result.hard_block_reason = block_reason

    if hard_blocked:
        result.grade = CONVICTION_REJECT
        result.total_score = 0
        result.position_size_pct = 0
        logger.debug(f"[Conviction] Hard Block: {block_reason}")
        return result

    is_crypto = False
    if ctx is not None:
        market = str(ctx.get("market", "")).upper()
        if market in ["KRIPTO", "KRİPTO", "CRYPTO"]:
            is_crypto = True

    if weights is None:
        w = CRYPTO_WEIGHTS if is_crypto else WEIGHTS
    else:
        w = weights
    total = 0.0

    for factor, weight in w.items():
        factor_score = scores.get(factor, 0.0)
        net_contribution = factor_score * weight
        result.component_scores[factor] = round(net_contribution, 1)
        total += net_contribution

    # V3.4: Data Guard & Conflict Resolver Soft Cezaları
    data_guard_penalty = scores.get("data_guard_penalty", 0.0)
    conflict_penalty = scores.get("conflict_penalty", 0.0)
    apply_bear_penalty = scores.get("apply_bear_penalty", False)
    # V3.5: Autopsy Soft Penalty
    autopsy_penalty = scores.get("autopsy_penalty", 0.0)
    # V3.6: Weak Setup Penalty (BIST 10 vb. için)
    setup_weak_penalty = scores.get("setup_weak_penalty", 0.0)
    
    # V3.7: Long Risk Penalty & USDT Trend Penalty
    long_risk_penalty = scores.get("long_risk_penalty", 0.0)
    
    usdt_penalty = 0.0
    if ctx is not None and ctx.get("usdt_trend") == "UP" and scores.get("is_long_strategy", True):
        # Dinamik USDT Dominance cezası
        usdt_strength = ctx.get("usdt_dominance_rsi", 60.0)
        excess_strength = max(0.0, usdt_strength - 50.0)
        usdt_penalty = -min(11.25, excess_strength * 0.1875)
        
    # NEW FUZZY REGIME & SESSION FILTERS (No Boolean Prison)
    fuzzy_filter_penalty = 0.0
    if ctx is not None and "last_4h" in ctx:
        last_4h = ctx["last_4h"]
        
        # Get ADX and CHOP
        adx_val = last_4h.get("ADX_14", 0.0)
        if pd.isna(adx_val): adx_val = 0.0
        
        chop_val = last_4h.get("chop", 50.0)
        if pd.isna(chop_val): chop_val = 50.0
        
        # Check scores first to avoid slow inspect.stack() call
        strategy_type = scores.get("strategy_type")
        if strategy_type is not None:
            strategy_type_lower = str(strategy_type).lower()
            # MR strategies contain specific keywords
            mr_keywords = ['liquidation', 'vwap', 'liquidity_hunt', 'divergence', 'sfp', 'short_squeeze', 'fomo', 'mean_reversion', 'mr', 'dip']
            is_trend = not any(kw in strategy_type_lower for kw in mr_keywords)
            is_sniper_1h = "sniper_1h" in strategy_type_lower
        else:
            # Fallback to slow inspect.stack() reflection
            caller_func = ""
            for frame in inspect.stack():
                if frame.function.startswith("_check_crypto_") or frame.function.startswith("_check_bist_"):
                    caller_func = frame.function
                    break
                    
            is_trend = True
            # MR strategies contain specific keywords
            mr_keywords = ['liquidation', 'vwap', 'liquidity_hunt', 'divergence', 'sfp', 'short_squeeze', 'fomo']
            if any(kw in caller_func.lower() for kw in mr_keywords):
                is_trend = False
                
            is_sniper_1h = "sniper_1h" in caller_func.lower()
            
        # 1. Regime-Aware soft penalties (skip for 1H sniper strategies)
        if not is_sniper_1h:
            if is_trend:
                if adx_val < 25.0:
                    fuzzy_filter_penalty -= max(0.0, min(7.5, (25.0 - adx_val) * 0.75))
                if chop_val > 50.0:
                    fuzzy_filter_penalty -= max(0.0, min(7.5, (chop_val - 50.0) * 0.5625))
            else:
                if adx_val > 20.0:
                    fuzzy_filter_penalty -= max(0.0, min(7.5, (adx_val - 20.0) * 0.75))
                if chop_val < 50.0:
                    fuzzy_filter_penalty -= max(0.0, min(7.5, (50.0 - chop_val) * 0.5625))
                
        # 2. Time-Session soft penalty
        eval_time = ctx.get("current_time")
        if eval_time is None:
            eval_time = last_4h.name + pd.Timedelta(hours=4)
            
        if hasattr(eval_time, "hour"):
            hour = eval_time.hour
            if not is_crypto:
                if hour < 8 or hour > 20:
                    fuzzy_filter_penalty -= 11.25  # Off-hours BIST penalty (eski: -15.0)
            else:
                is_long_strat = scores.get("is_long_strategy", True)
                if is_trend and not is_long_strat:
                    # Crypto trend/breakout off-hours penalty (UTC 22:00 to 06:00) for shorts only
                    if hour < 6 or hour >= 22:
                        fuzzy_filter_penalty -= 9.0  # eski: -12.0
                
        # 3. Volatility-Relative Stop Loss soft penalty
        atr_val = last_4h.get("ATRr_14", last_4h.get("ATR_14", 0.0))
        close_val = last_4h.get("close", 1.0)
        if pd.isna(atr_val): atr_val = 0.0
        if pd.isna(close_val) or close_val <= 0: close_val = 1.0
        
        atr_pct = (atr_val / close_val) * 100.0
        if atr_pct > 3.5:
            fuzzy_filter_penalty -= max(0.0, min(7.5, (atr_pct - 3.5) * 2.25))
    
    total += data_guard_penalty
    total += conflict_penalty
    total += autopsy_penalty
    total += nan_penalty
    total += setup_weak_penalty
    total += long_risk_penalty
    total += usdt_penalty
    total += fuzzy_filter_penalty
    
    # 10x leverage penalty for SL > 1%
    sl_distance_penalty = 0.0
    if ctx is not None:
        sl_distance_penalty = ctx.get("sl_distance_penalty", 0.0)
    total += sl_distance_penalty
    
    result.component_scores["data_guard_penalty"] = round(data_guard_penalty, 1)
    result.component_scores["conflict_penalty"] = round(conflict_penalty, 1)
    result.component_scores["autopsy_penalty"] = round(autopsy_penalty, 1)
    result.component_scores["nan_penalty"] = round(nan_penalty, 1)
    result.component_scores["setup_weak_penalty"] = round(setup_weak_penalty, 1)
    result.component_scores["long_risk_penalty"] = round(long_risk_penalty, 1)
    result.component_scores["usdt_penalty"] = round(usdt_penalty, 1)
    result.component_scores["sl_distance_penalty"] = round(sl_distance_penalty, 1)
    result.component_scores["fuzzy_filter_penalty"] = round(fuzzy_filter_penalty, 1)
    
    # V3.8: Apply global CONVICTION_SCORE_MULTIPLIER to the final score
    score_multiplier = getattr(config, "CONVICTION_SCORE_MULTIPLIER", 1.0)
    total *= score_multiplier
    
    if apply_bear_penalty:
        bear_penalty = getattr(config, "CONFLICT_RESOLVER_BEAR_TREND_PENALTY", 1.0)
        total *= bear_penalty
        
    total = max(0.0, total)
    result.total_score = round(total, 1)

    # Rejim-adaptif eşikler: config tabanlı sıkılaştırılmış limitler
    regime_score = scores.get("regime", SOFT_UNCERTAINTY_PENALTY)
    if regime_score >= 80:       # BULL (veya SHORT'ta BEAR → iyi)
        t_strong = REGIME_THRESHOLDS_BULL["STRONG"]
        t_medium = REGIME_THRESHOLDS_BULL["MEDIUM"]
        t_watch = REGIME_THRESHOLDS_BULL["WATCH"]
    elif regime_score >= 40:     # NEUTRAL
        t_strong = REGIME_THRESHOLDS_NEUTRAL["STRONG"]
        t_medium = REGIME_THRESHOLDS_NEUTRAL["MEDIUM"]
        t_watch = REGIME_THRESHOLDS_NEUTRAL["WATCH"]
    else:                        # BEAR long pozisyonlar (regime=10)
        t_strong = REGIME_THRESHOLDS_BEAR["STRONG"]
        t_medium = REGIME_THRESHOLDS_BEAR["MEDIUM"]
        t_watch = REGIME_THRESHOLDS_BEAR["WATCH"]

    if total >= t_strong:
        result.grade = CONVICTION_STRONG
    elif total >= t_medium:
        result.grade = CONVICTION_MEDIUM
    elif total >= t_watch:
        result.grade = CONVICTION_WATCH
    else:
        result.grade = CONVICTION_REJECT

    result.position_size_pct = POSITION_SIZE_MAP.get(result.grade, 0)

    logger.info(
        "[Conviction] Score=%.0f Grade=%s Pos=%s%%",
        result.total_score,
        result.grade,
        result.position_size_pct
    )

    # ════════════════════════════════════════
    # Conviction A/B Test — Shadow Evaluation
    # ════════════════════════════════════════
    try:
        ab_enabled = getattr(config, "CONVICTION_AB_ENABLED", False)
        if ab_enabled:
            thresholds_exp = getattr(config, "CONVICTION_THRESHOLDS_EXPERIMENT", {})
            _ticker = scores.get('_ticker', 'N/A')
            _ab_evaluate(_ticker, result.total_score, result.grade, thresholds_exp)
    except (AttributeError, KeyError, TypeError, ValueError) as e:
        logger.debug("[A/B Test] Hata: %s", e)

    return result


# ════════════════════════════════════════
# Strateji Tipleri İçin Hazır Score Paketleri
# ════════════════════════════════════════

def build_trend_scores(
    adx, adx_prev,
    price, ema_fast, ema_mid, ema_slow,
    rsi, rsi_prev,
    volume, vol_sma, dollar_vol,
    rr,
    has_engulfing,
    regime,
    macro_aligned,
    consecutive_sl,
    market="BIST",
    oi_crash=False,
    funding_rate=None,
    dg_is_darth_maul=False,
    dg_gap_pct=0.0,
    cmf=0.0,
    is_liquidity_window=True,
    strategy_type="TREND_BREAKOUT",
    is_long=True,
    adx_mode="gaussian",
    adx_center=None,
    adx_width=None,
    sma200_1d=None,
    rsi_1h=None,
    volume_ratio=None,
):
    """Trend stratejileri (BIST 2, KRİPTO 2) için skor paketi."""
    if adx_center is None:
        adx_center = config.SOFT_ADX_GAUSSIAN_CENTER
    if adx_width is None:
        adx_width = config.SOFT_ADX_GAUSSIAN_WIDTH
    
    is_gap = (dg_gap_pct >= config.GAP_THRESHOLD_PCT) if dg_gap_pct else False
    
    optimum_vol = 500_000 if market == "KRIPTO" else 10_000_000
    min_vol = config.SOFT_DOLLAR_VOL_CRYPTO_MIN if market == "KRIPTO" else config.SOFT_DOLLAR_VOL_BIST_MIN
    
    dg_penalty = score_data_guard(
        dollar_volume=dollar_vol,
        is_darth_maul=dg_is_darth_maul,
        is_gap=is_gap,
        cmf=cmf,
        is_liquidity_window=is_liquidity_window,
        min_volume_usd=min_vol,
        optimum_volume_usd=optimum_vol
    )
    
    conflict_penalty, apply_bear = score_conflict_resolver(
        adx=adx,
        regime=regime,
        strategy_type=strategy_type,
        is_long=is_long
    )

    vol_ratio_calc = volume_ratio
    if vol_ratio_calc is None and volume is not None and vol_sma and vol_sma > 0:
        vol_ratio_calc = volume / vol_sma

    autopsy_pen = calculate_autopsy_soft_penalty(
        price=price,
        sma200_1d=sma200_1d,
        rsi_1h=rsi_1h,
        volume_ratio=vol_ratio_calc,
        is_long=is_long,
        strategy_type=strategy_type
    )

    return {
        "adx":           score_adx(adx, adx_prev, adx_mode=adx_mode, adx_center=adx_center, adx_width=adx_width),
        "ema_alignment": score_ema_alignment(price, ema_fast, ema_mid, ema_slow),
        "rsi":           score_rsi_trend(rsi, market),
        "rsi_direction": score_rsi_direction(rsi, rsi_prev),
        "volume_ratio":  score_volume_ratio(volume, vol_sma),
        "dollar_volume": score_dollar_volume(dollar_vol, market),
        "rr_ratio":      score_rr_ratio(rr, regime),
        "engulfing":     score_engulfing(has_engulfing),
        "regime":        score_regime(regime),
        "macro":         score_macro_alignment(macro_aligned),
        "penalty":       score_penalty_level(consecutive_sl),
        "oi_crash":      score_oi_crash(oi_crash),
        "funding_rate":  score_funding_rate(funding_rate, direction="long"),
        "data_guard_penalty": dg_penalty,
        "conflict_penalty": conflict_penalty,
        "apply_bear_penalty": apply_bear,
        "autopsy_penalty": autopsy_pen,
        "strategy_type": strategy_type,
        "is_long_strategy": is_long,
    }


def build_dip_scores(
    rsi_daily, rsi_hourly, rsi_prev,
    price, ema_fast, ema_mid,
    volume, vol_sma, dollar_vol,
    rr,
    has_engulfing,
    regime,
    macro_aligned,
    consecutive_sl,
    market="BIST",
    oi_crash=False,
    funding_rate=None,
    dg_is_darth_maul=False,
    dg_gap_pct=0.0,
    cmf=0.0,
    is_liquidity_window=True,
    strategy_type="MEAN_REVERSION_DIP",
    is_long=True,
    sma200_1d=None,
    rsi_1h=None,
    volume_ratio=None,
):
    """Dip avcılığı stratejileri (BIST 1, KRİPTO 1) için skor paketi."""
    is_gap = (dg_gap_pct >= config.GAP_THRESHOLD_PCT) if dg_gap_pct else False
    
    optimum_vol = 500_000 if market == "KRIPTO" else 10_000_000
    min_vol = config.SOFT_DOLLAR_VOL_CRYPTO_MIN if market == "KRIPTO" else config.SOFT_DOLLAR_VOL_BIST_MIN
    
    dg_penalty = score_data_guard(
        dollar_volume=dollar_vol,
        is_darth_maul=dg_is_darth_maul,
        is_gap=is_gap,
        cmf=cmf,
        is_liquidity_window=is_liquidity_window,
        min_volume_usd=min_vol,
        optimum_volume_usd=optimum_vol
    )
    
    conflict_penalty, apply_bear = score_conflict_resolver(
        adx=None,
        regime=regime,
        strategy_type=strategy_type,
        is_long=is_long
    )

    vol_ratio_calc = volume_ratio
    if vol_ratio_calc is None and volume is not None and vol_sma and vol_sma > 0:
        vol_ratio_calc = volume / vol_sma

    autopsy_pen = calculate_autopsy_soft_penalty(
        price=price,
        sma200_1d=sma200_1d,
        rsi_1h=rsi_1h,
        volume_ratio=vol_ratio_calc,
        is_long=is_long,
        strategy_type=strategy_type
    )

    return {
        "adx":           50.0,  # Dip avcılığında ADX önemsiz → nötr
        "ema_alignment": score_ema_dip_distance(price, ema_fast, ema_mid),
        "rsi":           score_rsi_oversold(rsi_daily, market),
        "rsi_direction": score_rsi_direction(rsi_hourly, rsi_prev),
        "volume_ratio":  score_volume_ratio(volume, vol_sma),
        "dollar_volume": score_dollar_volume(dollar_vol, market),
        "rr_ratio":      score_rr_ratio(rr, regime),
        "engulfing":     score_engulfing(has_engulfing),
        "regime":        score_regime(regime),
        "macro":         score_macro_alignment(macro_aligned),
        "penalty":       score_penalty_level(consecutive_sl),
        "oi_crash":      score_oi_crash(oi_crash),
        "funding_rate":  score_funding_rate(funding_rate, direction="long"),
        "data_guard_penalty": dg_penalty,
        "conflict_penalty": conflict_penalty,
        "apply_bear_penalty": apply_bear,
        "autopsy_penalty": autopsy_pen,
        "strategy_type": strategy_type,
        "is_long_strategy": is_long,
    }


def build_breakout_scores(
    bb_width,
    price, ema_fast, ema_mid, ema_slow,
    volume, vol_sma, dollar_vol,
    rr,
    regime,
    macro_aligned,
    consecutive_sl,
    market="BIST",
    oi_crash=False,
    funding_rate=None,
    dg_is_darth_maul=False,
    dg_gap_pct=0.0,
    cmf=0.0,
    is_liquidity_window=True,
    strategy_type="TREND_BREAKOUT",
    is_long=True,
    rsi=None,
    sma200_1d=None,
    rsi_1h=None,
    volume_ratio=None,
    rsi_prev=None,
    has_engulfing=None,
):
    """Kırılım/Squeeze stratejileri (BIST 3/5, KRİPTO 3) için skor paketi."""
    is_gap = (dg_gap_pct >= config.GAP_THRESHOLD_PCT) if dg_gap_pct else False
    
    optimum_vol = 500_000 if market == "KRIPTO" else 10_000_000
    min_vol = config.SOFT_DOLLAR_VOL_CRYPTO_MIN if market == "KRIPTO" else config.SOFT_DOLLAR_VOL_BIST_MIN
    
    dg_penalty = score_data_guard(
        dollar_volume=dollar_vol,
        is_darth_maul=dg_is_darth_maul,
        is_gap=is_gap,
        cmf=cmf,
        is_liquidity_window=is_liquidity_window,
        min_volume_usd=min_vol,
        optimum_volume_usd=optimum_vol
    )
    
    conflict_penalty, apply_bear = score_conflict_resolver(
        adx=None,
        regime=regime,
        strategy_type=strategy_type,
        is_long=is_long
    )
    
    # Overbought/Oversold breakout penalty
    if rsi is not None and not _is_nan(rsi):
        if is_long and rsi > config.BREAKOUT_RSI_MAX_LIMIT:
            excess = rsi - config.BREAKOUT_RSI_MAX_LIMIT
            conflict_penalty -= min(25.0, excess * 1.5)
        elif not is_long and rsi < (100.0 - config.BREAKOUT_RSI_MAX_LIMIT):
            excess = (100.0 - config.BREAKOUT_RSI_MAX_LIMIT) - rsi
            conflict_penalty -= min(25.0, excess * 1.5)

    squeeze_score = inverse_linear_score(bb_width, SOFT_SQUEEZE_MIN, SOFT_SQUEEZE_MAX) if bb_width else SOFT_UNCERTAINTY_PENALTY

    vol_ratio_calc = volume_ratio
    if vol_ratio_calc is None and volume is not None and vol_sma and vol_sma > 0:
        vol_ratio_calc = volume / vol_sma

    autopsy_pen = calculate_autopsy_soft_penalty(
        price=price,
        sma200_1d=sma200_1d,
        rsi_1h=rsi_1h,
        volume_ratio=vol_ratio_calc,
        is_long=is_long,
        strategy_type=strategy_type
    )

    return {
        "adx":           squeeze_score,
        "ema_alignment": score_ema_alignment(price, ema_fast, ema_mid, ema_slow),
        "rsi":           score_rsi_trend(rsi, market) if rsi is not None else 50.0,
        "rsi_direction": score_rsi_direction(rsi, rsi_prev) if rsi is not None and rsi_prev is not None else 50.0,
        "volume_ratio":  score_volume_ratio(volume, vol_sma),
        "dollar_volume": score_dollar_volume(dollar_vol, market),
        "rr_ratio":      score_rr_ratio(rr, regime),
        "engulfing":     score_engulfing(has_engulfing) if has_engulfing is not None else score_engulfing(False),
        "regime":        score_regime(regime),
        "macro":         score_macro_alignment(macro_aligned),
        "penalty":       score_penalty_level(consecutive_sl),
        "oi_crash":      score_oi_crash(oi_crash),
        "funding_rate":  score_funding_rate(funding_rate, direction="long" if is_long else "short"),
        "data_guard_penalty": dg_penalty,
        "conflict_penalty": conflict_penalty,
        "apply_bear_penalty": apply_bear,
        "autopsy_penalty": autopsy_pen,
        "strategy_type": strategy_type,
        "is_long_strategy": is_long,
    }


def build_short_scores(
    adx, adx_prev,
    price, ema_fast, ema_mid, ema_slow,
    rsi, rsi_prev,
    volume, vol_sma, dollar_vol,
    rr,
    has_engulfing,
    regime,
    macro_aligned,
    consecutive_sl,
    market="KRIPTO",
    oi_crash=False,
    funding_rate=None,
    dg_is_darth_maul=False,
    dg_gap_pct=0.0,
    cmf=0.0,
    is_liquidity_window=True,
    strategy_type="TREND_BREAKOUT",
    is_long=False,
    adx_mode="gaussian",
    adx_center=None,
    adx_width=None,
    sma200_1d=None,
    rsi_1h=None,
    volume_ratio=None,
):
    """SHORT stratejileri (SHORT 1-4, Bear Hunter) için skor paketi."""
    if adx_center is None:
        adx_center = config.SOFT_ADX_GAUSSIAN_CENTER
    if adx_width is None:
        adx_width = config.SOFT_ADX_GAUSSIAN_WIDTH
    
    is_gap = (dg_gap_pct >= config.GAP_THRESHOLD_PCT) if dg_gap_pct else False
    
    optimum_vol = 500_000 if market == "KRIPTO" else 10_000_000
    min_vol = config.SOFT_DOLLAR_VOL_CRYPTO_MIN if market == "KRIPTO" else config.SOFT_DOLLAR_VOL_BIST_MIN
    
    dg_penalty = score_data_guard(
        dollar_volume=dollar_vol,
        is_darth_maul=dg_is_darth_maul,
        is_gap=is_gap,
        cmf=cmf,
        is_liquidity_window=is_liquidity_window,
        min_volume_usd=min_vol,
        optimum_volume_usd=optimum_vol
    )
    
    conflict_penalty, apply_bear = score_conflict_resolver(
        adx=adx,
        regime=regime,
        strategy_type=strategy_type,
        is_long=is_long
    )

    vol_ratio_calc = volume_ratio
    if vol_ratio_calc is None and volume is not None and vol_sma and vol_sma > 0:
        vol_ratio_calc = volume / vol_sma

    autopsy_pen = calculate_autopsy_soft_penalty(
        price=price,
        sma200_1d=sma200_1d,
        rsi_1h=rsi_1h,
        volume_ratio=vol_ratio_calc,
        is_long=is_long,
        strategy_type=strategy_type
    )

    long_risk_penalty = 0.0
    
    # 1. Aşırı satım (RSI çok düşük) -> Short için tehlikeli (yukarı tepki riski)
    if rsi is not None:
        if rsi < 40:
            excess = 40 - rsi
            long_risk_penalty -= min(11.25, excess * 0.75) # eski: min(15.0, excess * 1.0)

    # 2. SMA 200 Desteği (Fiyat 1D SMA200'e çok yakın ve üstünde ise destek olarak çalışabilir)
    if sma200_1d is not None and sma200_1d > 0 and price is not None:
        dist_to_sma200 = (price - sma200_1d) / sma200_1d
        if 0 <= dist_to_sma200 < 0.05:  # SMA200'ün %5 kadar üstünde
            proximity = 0.05 - dist_to_sma200
            long_risk_penalty -= min(11.25, proximity * 150.0) # eski: min(15.0, proximity * 200.0)

    # 3. Rejim Uyarısı (Zaten Boğa Piyasasındayken shortlamak risklidir)
    if regime == "BULL":
        long_risk_penalty -= 7.5 # eski: -10.0
        
    # 4. Momentum (Eğer ema_fast > ema_mid > ema_slow ise trend güçlü boğadır, short tehlikeli)
    if ema_fast and ema_mid and ema_slow:
        if ema_fast > ema_mid > ema_slow:
            # Momentum farkına göre dinamik ceza
            momentum_gap = (ema_fast - ema_slow) / ema_slow
            long_risk_penalty -= min(11.25, momentum_gap * 75.0 + 3.75) # eski: min(15.0, momentum_gap * 100.0 + 5.0)

    # 5. Aşırı Satım Uzaklık Cezası (Fiyat 4H EMA50'nin çok altındaysa short tehlikeli)
    if ema_mid is not None and price is not None and price > 0 and not is_long:
        dist_below_ema50 = (ema_mid - price) / price
        if dist_below_ema50 > 0.04:  # EMA50'den %4'ten fazla uzakta
            oversold_stretch = dist_below_ema50 - 0.04
            long_risk_penalty -= min(15.0, oversold_stretch * 75.0 + 3.75) # eski: min(20.0, oversold_stretch * 100.0 + 5.0)


    return {
        "adx":           score_adx(adx, adx_prev, adx_mode=adx_mode, adx_center=adx_center, adx_width=adx_width),
        "ema_alignment": score_ema_short(price, ema_fast, ema_mid, ema_slow),
        "rsi":           score_rsi_trend(rsi, market),
        "rsi_direction": score_rsi_direction(rsi, rsi_prev),
        "volume_ratio":  score_volume_ratio(volume, vol_sma),
        "dollar_volume": score_dollar_volume(dollar_vol, market),
        "rr_ratio":      score_rr_ratio(rr, regime, is_short=True),
        "engulfing":     score_engulfing(has_engulfing),
        "regime":        score_regime_short(regime),
        "macro":         score_macro_alignment(macro_aligned),
        "penalty":       score_penalty_level(consecutive_sl),
        "oi_crash":      score_oi_crash(oi_crash),
        "funding_rate":  score_funding_rate(funding_rate, direction="short"),
        "data_guard_penalty": dg_penalty,
        "conflict_penalty": conflict_penalty,
        "apply_bear_penalty": apply_bear,
        "autopsy_penalty": autopsy_pen,
        "long_risk_penalty": long_risk_penalty,
        "is_long_strategy": False,
        "strategy_type": strategy_type,
    }


def _get_sniper_pb_limits(market: str, is_long: bool) -> tuple[float, float]:
    """
    Sniper limitlerini çeker.
    """
    if not is_long and market == "KRIPTO":
        pb_min = getattr(config, "SHORT4_BBP_MIN_PULLBACK", 0.0)
        pb_max = getattr(config, "SHORT4_BBP_MAX_PULLBACK", 1.0)
    else:
        # Sniper Long (BIST & KRIPTO) için %B daraltması
        pb_min = 0.0
        pb_max = 0.85
    return pb_min, pb_max


def _calculate_sniper_confluences(
    price: float,
    ema_mid: float,
    volume: float,
    vol_sma: float,
    sfp_present: bool,
    has_squeeze_breakout: bool
) -> float:
    """
    Dinamik Confluence (Ödül / Ceza) Mekanizması.
    """
    penalty_bonus = 0.0

    # 1. EMA 21 Yakınlığı (FOMO / Kovalamaca Cezası)
    if ema_mid and not _is_nan(ema_mid) and ema_mid > 0:
        dist_ema21 = abs(price - ema_mid) / ema_mid
        if dist_ema21 > 0.015:
            excess = dist_ema21 - 0.015
            penalty_bonus -= min(excess * 200.0, 10.0)

    # 2. Squeeze & Hacim Confluence (Hacimli Patlama Ödülü ve Hacimsizlik Cezası)
    if volume and vol_sma and vol_sma > 0:
        vol_ratio = volume / vol_sma
        if has_squeeze_breakout and vol_ratio >= 1.5:
            penalty_bonus += min((vol_ratio - 1.5) * 8.0, 8.0)
        elif vol_ratio < 1.0:
            # Dinamik sığ hacim cezası
            deficit = 1.0 - vol_ratio
            penalty_bonus -= min(25.0, deficit * 30.0)  # Sığ hacimli hareketleri orantılı cezalandır

    # 3. SFP (Likidite Avı) + EMA Desteği Confluence (Güvenilir Dip Ödülü)
    if sfp_present and ema_mid and not _is_nan(ema_mid) and ema_mid > 0:
        dist_ema21 = abs(price - ema_mid) / ema_mid
        if dist_ema21 <= 0.015:
            penalty_bonus += 5.0

    return penalty_bonus


def _calculate_sniper_base_scores(
    market: str,
    bbw: float,
    kcw: float,
    pb: float,
    pb_min: float,
    pb_max: float,
    fvg_present: bool,
    sfp_present: bool,
    is_squeeze: bool
) -> tuple[float, float, float]:
    """
    BIST veya diğer marketler için base (bbw, pb, fvg_sfp) skorlarını hesaplar.
    """
    bbw_score = score_bbw_squeeze(bbw, kcw)
    if market == "BIST" and is_squeeze:
        bbw_score = max(bbw_score, 100.0)
        
    pb_score = score_percent_b(pb, pb_min=pb_min, pb_max=pb_max)
    fvg_sfp_score = score_fvg_sfp(fvg_present, sfp_present)
    
    return bbw_score, pb_score, fvg_sfp_score


def build_sniper_scores(
    price, ema_fast, ema_mid, ema_slow,
    rsi, rsi_prev,
    volume, vol_sma, dollar_vol,
    rr,
    regime="BULL",
    macro_aligned=True,
    consecutive_sl=0,
    bbw=0.0, kcw=0.0, pb=0.5, fvg_present=False, sfp_present=False,
    has_engulfing=False,
    market="BIST",
    oi_crash=False,
    funding_rate=None,
    dg_is_darth_maul=False,
    dg_gap_pct=0.0,
    cmf=0.0,
    is_liquidity_window=True,
    strategy_type="MEAN_REVERSION",
    is_long=True,
    is_squeeze=None,
    asset_trend_aligned=True,
    sma200_1d=None,
    rsi_1h=None,
    volume_ratio=None,
    in_supply_zone=False,
    in_demand_zone=False,
):
    """Keskin Nişancı stratejisi (BIST Sniper, KRIPTO Sniper) için skor paketi."""
    is_gap = (dg_gap_pct >= config.GAP_THRESHOLD_PCT) if dg_gap_pct else False
    
    optimum_vol = 500_000 if market == "KRIPTO" else 10_000_000
    min_vol = config.SOFT_DOLLAR_VOL_CRYPTO_MIN if market == "KRIPTO" else config.SOFT_DOLLAR_VOL_BIST_MIN
    
    dg_penalty = score_data_guard(
        dollar_volume=dollar_vol,
        is_darth_maul=dg_is_darth_maul,
        is_gap=is_gap,
        cmf=cmf,
        is_liquidity_window=is_liquidity_window,
        min_volume_usd=min_vol,
        optimum_volume_usd=optimum_vol
    )
    
    conflict_penalty, apply_bear = score_conflict_resolver(
        adx=None,
        regime=regime,
        strategy_type=strategy_type,
        is_long=is_long
    )

    if is_long:
        smc_bonus = score_smc_zones(in_supply_zone, in_demand_zone, is_long)
        # 50.0 is neutral. If 80, it's +30. If 20, it's -30.
        conflict_penalty += (smc_bonus - 50.0)

    if not asset_trend_aligned:
        conflict_penalty -= 10.0

    pb_min, pb_max = _get_sniper_pb_limits(market, is_long)

    # Formasyon Kontrolü: FVG, SFP veya Sıkışma (Squeeze) Kırılımı durumlarına göre kademeli soft ceza
    has_squeeze_breakout = is_squeeze if is_squeeze is not None else (bbw >= (kcw * 0.90))
    
    if not has_squeeze_breakout and not fvg_present and not sfp_present:
        conflict_penalty -= 10.0  # Hiçbir setup yok
    elif not has_squeeze_breakout and not fvg_present:
        conflict_penalty -= 9.0   # Sadece SFP var
    elif not has_squeeze_breakout and not sfp_present:
        conflict_penalty -= 9.0   # Sadece FVG var
    elif not fvg_present and not sfp_present:
        conflict_penalty -= 8.0   # Sadece Squeeze var (likidite yok)

    # ════════════════════════════════════════
    # Dinamik Confluence (Ödül / Ceza) Mekanizması
    # ════════════════════════════════════════
    confluence_impact = _calculate_sniper_confluences(
        price=price,
        ema_mid=ema_mid,
        volume=volume,
        vol_sma=vol_sma,
        sfp_present=sfp_present,
        has_squeeze_breakout=has_squeeze_breakout
    )
    conflict_penalty += confluence_impact

    # Base Skorlar
    bbw_score, pb_score, fvg_sfp_score = _calculate_sniper_base_scores(
        market=market,
        bbw=bbw,
        kcw=kcw,
        pb=pb,
        pb_min=pb_min,
        pb_max=pb_max,
        fvg_present=fvg_present,
        sfp_present=sfp_present,
        is_squeeze=is_squeeze
    )

    vol_ratio_calc = volume_ratio
    if vol_ratio_calc is None and volume is not None and vol_sma and vol_sma > 0:
        vol_ratio_calc = volume / vol_sma

    autopsy_pen = calculate_autopsy_soft_penalty(
        price=price,
        sma200_1d=sma200_1d,
        rsi_1h=rsi_1h,
        volume_ratio=vol_ratio_calc,
        is_long=is_long,
        strategy_type=strategy_type
    )

    return {
        "bbw_squeeze":   bbw_score,
        "percent_b":     pb_score,
        "fvg_sfp":       fvg_sfp_score,
        "volume_ratio":  score_volume_ratio(volume, vol_sma),
        "dollar_volume": score_dollar_volume(dollar_vol, market),
        "rr_ratio":      score_rr_ratio(rr, regime),
        "regime":        score_regime(regime),
        "macro":         score_macro_alignment(macro_aligned),
        "funding_rate":  score_funding_rate(funding_rate, direction="long" if is_long else "short") if market == "KRIPTO" else 0.0,
        "data_guard_penalty": dg_penalty,
        "conflict_penalty": conflict_penalty,
        "apply_bear_penalty": apply_bear,
        "autopsy_penalty": autopsy_pen,
        "is_long_strategy": is_long,
        "strategy_type": strategy_type,
    }

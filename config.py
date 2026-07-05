# pylint: disable=line-too-long,trailing-whitespace,broad-exception-caught,logging-fstring-interpolation
"""
config.py — Quant Bot Merkezi Yapılandırma
Tüm sabitler, eşikler, varlık listeleri ve magic number'lar burada toplanır.
Hiçbir strateji parametresi başka dosyada hardcoded olmamalıdır.
"""
import os
import json
import logging
from dotenv import load_dotenv

load_dotenv()

# ════════════════════════════════════════
# Sunucu / Altyapı
# ════════════════════════════════════════
IS_USA_SERVER: bool = os.getenv("IS_USA_SERVER", "true").lower() == "true"
CCXT_EXCHANGE: str = os.getenv("CCXT_EXCHANGE", "bybit")  # CCXT borsa seçimi
CCXT_FETCH_FUTURES_DATA: bool = True  # OI ve Funding rate çekilsin mi?

# ════════════════════════════════════════
# Dosya / Durum Yönetim Yolları (Modüler Konfigürasyon)
# ════════════════════════════════════════
ACTIVE_TRADES_FILE = os.getenv("ACTIVE_TRADES_FILE", "active_trades.json")
TRADE_HISTORY_FILE = os.getenv("TRADE_HISTORY_FILE", "trade_history.json")
TRADE_JOURNAL_CSV = os.getenv("TRADE_JOURNAL_CSV", "trade_journal.csv")
POSTMORTEM_FILE = os.getenv("POSTMORTEM_FILE", "trade_postmortems.json")
SCORECARD_STATE_FILE = os.getenv("SCORECARD_STATE_FILE", "strategy_scorecard_state.json")
QUARANTINE_STATE_FILE = os.getenv("QUARANTINE_STATE_FILE", "quarantine_state.json")
CIRCUIT_BREAKER_STATE_FILE = os.getenv("CIRCUIT_BREAKER_STATE_FILE", "circuit_breaker_state.json")

# ════════════════════════════════════════
# Varlık Listeleri Yükleyici
# ════════════════════════════════════════
def _load_assets():
    default_bist10 = ["THYAO.IS", "TUPRS.IS", "KCHOL.IS", "AKBNK.IS", "YKBNK.IS", "ISCTR.IS", "SAHOL.IS", "EREGL.IS", "BIMAS.IS", "PGSUS.IS"]
    default_crypto8 = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT", "AVAX/USDT", "LINK/USDT"]
    default_crypto_scan = [
        "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", "ADA/USDT",
        "AVAX/USDT", "LINK/USDT", "DOT/USDT", "TRX/USDT", "TON/USDT", "NEAR/USDT", 
        "SUI/USDT", "APT/USDT", "ARB/USDT", "OP/USDT", "POL/USDT", "LTC/USDT", 
        "BCH/USDT", "UNI/USDT"
    ]
    default_bist = ["THYAO.IS", "ISCTR.IS", "SASA.IS", "HEKTS.IS", "TUPRS.IS", "EREGL.IS"]
    default_emtia_usd = ["GC=F", "SI=F", "CL=F", "BZ=F", "NG=F", "HG=F", "ZW=F"]
    default_emtia_try = ["GLDTR.IS", "GMSTR.IS"]
    default_blacklist = {
        "DOGE/USDT", "SHIB/USDT", "PEPE/USDT", "WIF/USDT", "FLOKI/USDT",
        "BONK/USDT", "MEME/USDT", "BABYDOGE/USDT", "NEIRO/USDT", "TURBO/USDT",
        "BRETT/USDT", "POPCAT/USDT", "BOME/USDT", "BOOK/USDT", "MEW/USDT",
        "NOT/USDT", "PEOPLE/USDT", "SATS/USDT"
    }

    assets_path = os.path.join(os.path.dirname(__file__), "assets.json")
    if os.path.exists(assets_path):
        try:
            with open(assets_path, "r", encoding="utf-8") as f:
                data = json.load(f)
                return (
                    data.get("TOP_BIST_10", default_bist10),
                    data.get("TOP_CRYPTO_8", default_crypto8),
                    data.get("TOP_CRYPTO_SCAN", default_crypto_scan),
                    data.get("TOP_BIST", default_bist),
                    data.get("TOP_EMTIA_USD", default_emtia_usd),
                    data.get("TOP_EMTIA_TRY", default_emtia_try),
                    set(data.get("MEME_BLACKLIST", default_blacklist))
                )
        except Exception as e:
            logging.error(f"[config] assets.json yuklenirken hata olustu: {e}")
    
    return default_bist10, default_crypto8, default_crypto_scan, default_bist, default_emtia_usd, default_emtia_try, set(default_blacklist)

(
    TOP_BIST_10,
    TOP_CRYPTO_8,
    TOP_CRYPTO_SCAN,
    TOP_BIST,
    TOP_EMTIA_USD,
    TOP_EMTIA_TRY,
    MEME_BLACKLIST
) = _load_assets()

TOP_CRYPTO = TOP_CRYPTO_SCAN
TOP_BIST_50 = TOP_BIST[:50]
TOP_EMTIA = TOP_EMTIA_USD + TOP_EMTIA_TRY
TOP_HEAVY_SHORT = TOP_CRYPTO_SCAN

# ════════════════════════════════════════
# ATR & Stop Parametreleri
# ════════════════════════════════════════
ATR_MULTIPLIER_BIST = 1.8
ATR_MULTIPLIER_CRYPTO = 1.6   # 5x kaldıraç için güncellendi (eski: 1.0)
MIN_SL_PCT = 0.03          # Minimum SL yüzdesi
BIST_MIN_SL_PCT = 0.015    # BIST için minimum SL yüzdesi (En az %1.5)
BREAKOUT_RSI_MAX_LIMIT = 68.0  # Kırılım işlemlerinde saatlik RSI tavanı
TRAILING_ATR_FLOOR_RATIO = 0.3
TRAILING_TIGHT_PCT = 0.005   # %15+ kâr → sıkı trailing
TRAILING_MEDIUM_PCT = 0.015  # %10+ kâr → orta trailing
TRAILING_STOP_ACTIVATION_RR = 1.5  # Freqtrade stili: R:R bu değeri geçince trailing başla
TRAILING_STOP_DISTANCE_PCT = 0.02  # Freqtrade stili: Aktif olunca %2 takip mesafesi

# ════════════════════════════════════════
# Scale-Out & Kâr Eşikleri
# ════════════════════════════════════════
SCALE_OUT_THRESHOLD_LONG = 5.0     # LONG %5'te kademeli çıkış
SCALE_OUT_THRESHOLD_SHORT_FOMO = 10.0  # FOMO İnfazı %10'da
TRAILING_TIER_1_PCT = 15.0  # Sıkı trailing başlar
TRAILING_TIER_2_PCT = 10.0  # Orta trailing başlar

# ════════════════════════════════════════
# RSI Eşikleri
# ════════════════════════════════════════
RSI_OVERSOLD_BIST = 40
RSI_OVERSOLD_CRYPTO = 28
RSI_OVERBOUGHT_FOMO = 85
RSI_BTC_PUMP_LIMIT = 70
BTC_PUMP_PCT_CHANGE = 4.0

# ════════════════════════════════════════
# ADX & Trend Eşikleri
# ════════════════════════════════════════
ADX_TREND_THRESHOLD = 25
ADX_STRONG_TREND = 30

# ════════════════════════════════════════
# BIST 9 (ORB) Parametreleri
# ════════════════════════════════════════
BIST9_MAX_CAGE_WIDTH_PCT = 2.0  # Kafes genişliği maksimum % kaç olabilir
BIST9_RVOL_MULTIPLIER = 1.5     # Kırılım mumunun hacmi, ortalama hacmin kaç katı olmalı
BIST9_EMA_LENGTH = 21           # Onay için kullanılacak EMA periyodu
BIST9_RVOL_PERIOD = 20          # RVOL hesaplaması için geriye dönük bakılacak gün sayısı
BIST9_TRADE_START_HOUR = 11     # Sinyal arama başlangıç saati
BIST9_TRADE_START_MINUTE = 15   # Sinyal arama başlangıç dakikası
BIST9_TRADE_END_HOUR = 13       # Sinyal arama bitiş saati
BIST9_TRADE_END_MINUTE = 0      # Sinyal arama bitiş dakikası


ORB_CAGE_HOUR = 10              # Kafes oluşum saati
ORB_MIN_BARS = 4                # Kafes hesaplama için minimum gün içi bar sayısı

# ════════════════════════════════════════
# Indikatör ve Hareketli Ortalama Periyotları
# ════════════════════════════════════════
IND_RSI_LENGTH = 14
IND_ATR_LENGTH = 14
IND_ADX_LENGTH = 14

IND_EMA_FAST = 8
IND_EMA_MID = 20
IND_EMA_21 = 21
IND_EMA_SLOW = 50
IND_EMA_55 = 55

IND_SMA_SLOW = 50
IND_SMA_TREND = 200

IND_BBANDS_LENGTH = 20
IND_BBANDS_STD = 2.0

IND_VOL_SMA_LENGTH = 20
IND_VOL_BREAKOUT_MULTIPLIER = 1.5

IND_RS_SMA_LENGTH = 50
IND_RS_MOMENTUM_SHORT = 5
IND_RS_MOMENTUM_LONG_START = 15
IND_RS_MOMENTUM_LONG_END = 10

IND_OBV_ACC_MIN_LEN = 21
IND_OBV_ACC_PERIOD = 20
IND_OBV_ACC_SHORT_PERIOD = 5
IND_OBV_ACC_MAX_CHANGE_PCT = 2.0
IND_OBV_ACC_VOL_MULTIPLIER = 1.5

# ════════════════════════════════════════
# V3.4 Conviction Soft Score Eşikleri (Sıkılaştırılmış)
# ════════════════════════════════════════
SOFT_ADX_CENTER = 28.0
SOFT_ADX_K = 0.35

# V3.5 Gaussian (Bell Curve) ADX Model Sınırları
SOFT_ADX_GAUSSIAN_CENTER = 18.5
SOFT_ADX_GAUSSIAN_WIDTH = 5.0

SOFT_VOL_RATIO_CENTER = 1.8
SOFT_VOL_RATIO_K = 3.0

SOFT_RR_CENTER = 2.5
SOFT_RR_K = 2.5

SOFT_RSI_TREND_CENTER = 55.0
SOFT_RSI_TREND_MULT = 2.5

SOFT_RSI_OVERSOLD_BIST_CENTER = 30.0
SOFT_RSI_OVERSOLD_CRYPTO_CENTER = 25.0

SOFT_SQUEEZE_MIN = 0.03
SOFT_SQUEEZE_MAX = 0.25

SOFT_EMA_DIP_MULT = 5.7
SOFT_EMA_DIP_MAX_PCT = 7.0

# ════════════════════════════════════════
# V3.4 Conviction Scorer Alt-Puanlama (Fuzzy Logic) Sabitleri (SSOT)
# 
# Bu bölümdeki katsayılar conviction_scorer.py içindeki algoritmik esnetmeleri, 
# cezaları ve bonusları (magic numbers) kontrol eder. İnsan/Yapay Zeka anlaşılabilirliği
# için kategorize edilmiştir.
# ════════════════════════════════════════

# --- ADX & Momentum Sınırları ---
# ADX > 40 olduğunda trendin olgunlaştığı kabul edilir ve "decay" (ceza) uygulanır.
SOFT_ADX_MATURITY_START = 40.0       # Olgunlaşma cezasının başladığı ADX seviyesi
SOFT_ADX_MATURITY_MULT = 3.0         # Aşılan her 1 puan için uygulanacak ceza katsayısı
SOFT_ADX_MATURITY_MIN = 15.0         # Ceza sonrası düşülebilecek minimum puan sınırı
SOFT_ADX_MOMENTUM_UP = 1.10          # ADX yükseliyorsa uygulanacak bonus çarpanı (%10)
SOFT_ADX_MOMENTUM_DOWN = 0.85        # ADX düşüyorsa uygulanacak ceza çarpanı (%15)

# --- RSI Esneklik Limitleri ---
# RSI dip avcılığında hedeflenen merkez noktadan uzaklaşıldıkça puanı düşüren sapan sınırları
SOFT_RSI_OVERSOLD_MIN_DIST = 20.0    # Merkezden aşağı sapma toleransı (örn: merkez 30 ise 10'da puan 100 olur)
SOFT_RSI_OVERSOLD_MAX_DIST = 15.0    # Merkezden yukarı sapma toleransı (örn: merkez 30 ise 45'te puan sıfırlanır)

# --- EMA Hizalama ve Dizilim Puanları (LONG Stratejiler) ---
# Fiyatın ve EMA'ların birbiri üzerindeki konumlarına göre verilen kısmi puanlar (Toplamı ~100)
SOFT_EMA_ALIGN_PRICE_FAST = 30.0     # Fiyat > EMA(Fast) ise verilecek puan
SOFT_EMA_ALIGN_FAST_MID = 40.0       # EMA(Fast) > EMA(Mid) ise verilecek puan
SOFT_EMA_ALIGN_MID_SLOW = 30.0       # EMA(Mid) > EMA(Slow) ise verilecek puan
SOFT_EMA_ALIGN_FAST_SLOW = 15.0      # Sadece EMA(Fast) > EMA(Slow) ise verilecek puan (Mid tutmuyorsa)
SOFT_EMA_ALIGN_NO_SLOW = 15.0        # EMA(Slow) verisi yoksa verilen varsayılan puan

# --- EMA Dip Mesafesi Puanlaması (Dip Avcılığı İçin) ---
# Fiyatın EMA altındaki derinliğini fırsat bilip ekstra puan üreten lojik
SOFT_EMA_DIP_MAX_SCORE = 40.0        # Fiyat EMA'dan çok uzaktaysa alınabilecek maksimum puan
SOFT_EMA_DIP_MIN_SCORE = 5.0         # Fiyat EMA'nın üzerindeyse verilen minimum puan
SOFT_EMA_DIP_STRUCT_BULL = 35.0      # Fiyat dipte ama EMA yapısı boğa (Fast > Mid) kaldıysa fırsat bonusu
SOFT_EMA_DIP_STRUCT_BEAR = 10.0      # EMA yapısı ayıya döndüyse düşük fırsat bonusu
SOFT_EMA_DIP_SLOW_BULL = 25.0        # EMA(Slow) bazlı uzun vadeli yapı boğaysa eklenecek puan
SOFT_EMA_DIP_SLOW_HALF = 12.0        # Kısmi boğa yapısı (sadece Fast > Slow)
SOFT_EMA_DIP_SLOW_NONE = 12.0        # Slow verisi eksikse verilecek nötr puan

# --- EMA Hizalama ve Dizilim Puanları (SHORT Stratejiler) ---
# Fiyatın ve EMA'ların altında olması durumunda verilen puanlar (Ayı dizilimi)
SOFT_EMA_SHORT_PRICE_FAST = 30.0     # Fiyat < EMA(Fast) ise
SOFT_EMA_SHORT_FAST_MID = 40.0       # EMA(Fast) < EMA(Mid) ise (Death cross)
SOFT_EMA_SHORT_MID_SLOW = 30.0       # EMA(Mid) < EMA(Slow) ise (Tam ayı dizilimi)
SOFT_EMA_SHORT_FAST_SLOW = 15.0      # Kısmi ayı dizilimi
SOFT_EMA_SHORT_NO_SLOW = 15.0        # Veri yoksa verilen puan

# --- Piyasa Rejimi (Regime) Puan Çarpanları ---
# Makro rejim algılandığında temel skorun üzerine eklenecek taban puanlar
SOFT_REGIME_BULL = 100.0             # Boğa rejimindeyken
SOFT_REGIME_NEUTRAL = 50.0           # Nötr (Yatay) rejimdeyken
SOFT_REGIME_BEAR = 10.0              # Ayı rejimindeyken

# --- Mantıksal Eşik Puanları (İkili Durumlar) ---
SOFT_ENGULFING_YES = 85.0            # Yutan mum formasyonu varsa
SOFT_ENGULFING_NO = 30.0             # Yoksa (Cezalandırılır ama tamamen sıfırlanmaz)
SOFT_RSI_DIR_UP = 80.0               # RSI yukarı yönlüyse
SOFT_RSI_DIR_DOWN = 20.0             # RSI aşağı yönlüyse
SOFT_MACRO_ALIGNED = 90.0            # Endeks/BTC yönü stratejiyle aynı yöndeyse
SOFT_MACRO_NOT_ALIGNED = 30.0        # Makro uyum yoksa

# --- Stop Loss (SL) Ceza Seviyeleri ---
# ponytail: 25% penalty discount applied
SOFT_PENALTY_0 = 100.0               # Ardışık SL yok (Tertemiz, tam puan)
SOFT_PENALTY_1 = 81.25               # 1 ardışık SL yemiş (eski: 75.0 - %25 indirimli ceza: -18.75)
SOFT_PENALTY_2 = 58.75               # 2 ardışık SL yemiş (eski: 45.0 - %25 indirimli ceza: -41.25)
SOFT_PENALTY_3_PLUS = 25.0           # 3 veya daha fazla ardışık SL (eski: 0.0 - %25 indirimli ceza: -75.0)

# --- Logaritmik Hacim Ölçeklemesi (Min-Max Aralıkları) ---
# Mutlak hacimlerin logaritmik olarak puanlanmasında kullanılan sınırlar
SOFT_DOLLAR_VOL_CRYPTO_MIN = 100_000         # Kriptoda 0 puan getirecek en alt USD hacim sınırı
SOFT_DOLLAR_VOL_CRYPTO_MAX = 10_000_000      # Kriptoda 100 puan getirecek tavan USD hacim sınırı
SOFT_DOLLAR_VOL_EMTIA_MIN = 50_000           # Emtiada alt sınır
SOFT_DOLLAR_VOL_EMTIA_MAX = 5_000_000        # Emtiada üst sınır
SOFT_DOLLAR_VOL_BIST_MIN = 1_000_000         # BIST'te (TL) alt sınır
SOFT_DOLLAR_VOL_BIST_MAX = 100_000_000       # BIST'te (TL) üst sınır

# Genel Güven (Conviction) Taban Eşikleri (Global Hard Limits)
GLOBAL_STRONG_CONVICTION_SCORE = 75.0
GLOBAL_MEDIUM_CONVICTION_SCORE = 60.0
GLOBAL_MIN_CONVICTION_SCORE = 50.0

# Rejim-adaptif Conviction Eşikleri
REGIME_THRESHOLDS_BULL = {"STRONG": 75.0, "MEDIUM": 60.0, "WATCH": 50.0}
REGIME_THRESHOLDS_NEUTRAL = {"STRONG": 75.0, "MEDIUM": 60.0, "WATCH": 50.0}
REGIME_THRESHOLDS_BEAR = {"STRONG": 80.0, "MEDIUM": 60.0, "WATCH": 50.0}

# ════════════════════════════════════════
# Bollinger & Squeeze
# ════════════════════════════════════════
BB_SQUEEZE_WIDTH = 0.15
BB_LENGTH = 20
BB_STD = 2
KC_SCALAR = 1.5
SQUEEZE_MIN_COUNT = 3
VOLUME_BREAKOUT_MULT = 1.5

# ════════════════════════════════════════
# Fibonacci Seviyeleri
# ════════════════════════════════════════
FIB_OTE_LOW = 0.618
FIB_OTE_HIGH = 0.786

# ════════════════════════════════════════
# Funding Rate & Risk
# ════════════════════════════════════════
FUNDING_CRITICAL_LONG = 0.05
FUNDING_CRITICAL_SHORT = -0.05
FUNDING_SHORT_BLOCK = -0.01
OI_CRASH_PCT = 15.0
DANGER_ZONE_PCT = 2.0
DANGER_SAFE_PCT = 5.0
DANGER_COOLDOWN_SEC = 1800
BLACK_SWAN_PCT = 0.03
RR_MINIMUM = 1.5

# ════════════════════════════════════════
# Emtia Spesifik
# ════════════════════════════════════════
EMTIA_ATR_MULT = {
    "GC=F": 2.0, "GLDTR.IS": 2.0,
    "SI=F": 2.0, "GMSTR.IS": 2.0,
    "CL=F": 2.5, "BZ=F": 2.5,
    "HG=F": 2.5,
    "NG=F": 3.0,
    "ZW=F": 3.0,
}
DXY_SENSITIVE = {"GC=F", "SI=F", "GLDTR.IS", "GMSTR.IS"}

EMTIA_NAMES = {
    "GC=F": "Altın (USD)", "SI=F": "Gümüş (USD)",
    "GLDTR.IS": "Altın (TL)", "GMSTR.IS": "Gümüş (TL)",
    "CL=F": "WTI Petrol", "BZ=F": "Brent Petrol",
    "NG=F": "Doğal Gaz", "HG=F": "Bakır", "ZW=F": "Buğday",
}

# ════════════════════════════════════════
# Zamanlama
# ════════════════════════════════════════
CACHE_TTL_SECONDS = 300        # 5 dk cache
SCAN_INTERVAL_MINUTES = 15
HEARTBEAT_INTERVAL = 6 * 3600  # 6 saatte 1 heartbeat
COOLDOWN_SECONDS = 3600        # 1 saat sinyal cooldown
PAIR_LOCK_HOURS = 4            # Her SL sonrası bu varlığın kilitleneceği süre (saat)
# 99 yapılmıştır
# Veri çekim derinliği ve eksik veri durumunda uygulanacak belirsizlik cezası parametreleri eklenmiştir.
DATA_PERIOD_1D = "12mo"            # 1D timeframe için 1 yıllık veri çekilir (en az 250+ bar, SMA 200 için şarttır)
DATA_PERIOD_1H = "1mo"             # 1H timeframe için 1 aylık veri çekilir
DATA_PERIOD_15M = "5d"             # 15m timeframe için 5 günlük veri çekilir
SOFT_UNCERTAINTY_PENALTY = 0.0     # Eksik teknik veri durumunda verilecek ceza puanı (nötr 50.0 yerine 0.0)

OHLCV_LIMIT = 300              # API'den çekilecek mum sayısı (SMA200 için en az 200+ gerekir)
API_SLEEP_BIST = 0.1
API_SLEEP_CRYPTO = 0.1
API_SLEEP_EMTIA = 0.2
BATCH_MAX_WORKERS = 3          # ThreadPoolExecutor max paralel çağrı

# ════════════════════════════════════════
# 🔴 Red Team Sabitleri (V3.1 Mantık Yamaları)
# ════════════════════════════════════════
# RED-08: ATR Stop üst sınırları (flash crash koruması)
ATR_CAP_BIST = 0.08            # BIST maksimum %8 stop
ATR_CAP_CRYPTO = 0.08          # Kripto maksimum %8 stop (5x kaldıraç için, eski: 0.05)
ATR_CAP_EMTIA = 0.10           # Emtia maksimum %10 stop

# RED-07: Minimum anlamlı dolar hacmi (hayalet likidite filtresi)
MIN_DOLLAR_VOL_CRYPTO = 500_000    # 500K USD
MIN_DOLLAR_VOL_BIST = 5_000_000    # 5M TL

# RED-05: Gap-Up/Down filtresi
GAP_THRESHOLD_PCT = 3.0        # %3 üstü gap → sahte kırılım riski

# RED-06: Darth Maul mum filtresi
DARTH_MAUL_BODY_RATIO = 0.15   # Gövde/Toplam aralık < %15 → kaos mumu

# DG-04: Tek mum anomali eşikleri (yüzde)
SINGLE_CANDLE_ANOMALY_PCT_BIST = 25.0
SINGLE_CANDLE_ANOMALY_PCT_CRYPTO = 60.0
SINGLE_CANDLE_ANOMALY_PCT_EMTIA = 25.0


# RED-03: Swing point minimum amplitude
SWING_MIN_AMPLITUDE_PCT = 0.5  # %0.5 altı swing → gürültü

# RED-16: Divergence tazelik kontrolü
DIVERGENCE_MAX_AGE_CANDLES = 10 # Divergence en fazla 10 mum eski olabilir

# RED-04: Squeeze minimum onay mum sayısı
SQUEEZE_CONFIRM_CANDLES = 2    # 2 mum onayı (tek mum sahte patlama engelleyici)

# RED-01: Volume SMA manipülasyon koruma oranı
VOL_SMA_LONG_RATIO = 0.6      # SMA(20) < SMA(50)*0.6 ise baskılanmış

# RED-02: ADX olgunlaşma eşiği
ADX_TOO_LATE = 45              # ADX > 45 → trend olgunlaşmış

# ════════════════════════════════════════
# 🛡️ Anti-Manipülasyon Kalkanları (AM Serisi)
# ════════════════════════════════════════
# AM-01: Engulfing / Momentum Onayı (Ölü Kedi Giyotini)
ENGULFING_MIN_BODY_RATIO = 0.50   # Yeşil mum, önceki kırmızı mumun en az %50'sini yutmalı

# AM-02: CMF Wash-Trade Kalkanı
CMF_PERIOD = 20                   # Chaikin Money Flow hesaplama periyodu
CMF_WASH_TRADE_THRESHOLD = 0.0    # CMF < 0 → Kurumsal boşaltma, ALIM REDDET

# AM-03: Mutlak Hacim Eşikleri (Sıfıra Bölünme Anomalisi)
MIN_HOURLY_DOLLAR_VOL_CRYPTO = 500_000   # Optimum: Saatlik 500K USD
MIN_HOURLY_TL_VOL_BIST = 10_000_000      # Optimum: Saatlik 10M TL
VOL_ABSOLUTE_MIN_CRYPTO = 50_000         # Hard block alt sınır (Crypto)
VOL_ABSOLUTE_MIN_BIST = 1_000_000        # Hard block alt sınır (BIST)

# AM-04: Funding Rate Short Kalkanı (Fonlama Vampiri)
FUNDING_SHORT_BLOCK_THRESHOLD = 0.0  # Negatif fonlamada SHORT AÇMA

# AM-05: Minimum Dalga Amplitüdü (Fraktal Körlüğü)
OTE_MIN_WAVE_PCT = 0.5          # SMC OTE: Dalga boyu en az %0.5 olmalı

# AM-06: Likidite Saatleri Zaman Kilidi (Zıt Korelasyon)
LIQUIDITY_WINDOW_START_HOUR = 15  # TSİ 15:30
LIQUIDITY_WINDOW_START_MIN = 30
LIQUIDITY_WINDOW_END_HOUR = 20    # TSİ 20:00
LIQUIDITY_WINDOW_END_MIN = 0

# ════════════════════════════════════════
# 🧬 V3.2 Kaos Çözümleri — Yeni Modül Parametreleri
# ════════════════════════════════════════

# --- Kaos #1: Adaptif Parametre Motoru (adaptive_params.py) ---
ADAPTIVE_VOL_SHORT_WINDOW = 30     # Kısa vadeli volatilite penceresi (gün)
ADAPTIVE_VOL_LONG_WINDOW = 180     # Uzun vadeli volatilite penceresi (gün)
ADAPTIVE_ATR_MIN_RATIO = 0.6      # ATR çarpan alt sınırı (base × 0.6)
ADAPTIVE_ATR_MAX_RATIO = 1.8      # ATR çarpan üst sınırı (base × 1.8)

# --- Kaos #2: Karantina Protokolü (quarantine.py) ---
QUARANTINE_STALE_SECONDS = 1800    # 30 dk fiyat gelmezse karantina
QUARANTINE_AUTO_CLOSE_HOURS = 72   # 72 saat sonra otomatik kapat
QUARANTINE_ENABLED = True          # Karantina modülü aktif mi

# --- Kaos #3: Sinyal Erime Motoru (signal_decay.py) ---
SIGNAL_DECAY_WARM_SECONDS = 1800        # 30 dk → ılık
SIGNAL_DECAY_STALE_SECONDS = 7200       # 2 saat → bayat
SIGNAL_DECAY_DEAD_CRYPTO_SECONDS = 21600  # 6 saat → kripto sinyali öldü
SIGNAL_DECAY_DEAD_BIST_SECONDS = 28800    # 8 saat → BIST sinyali öldü
SIGNAL_DECAY_MIN_RR = 1.5               # Bu R:R altında sinyal iptal
SIGNAL_DECAY_ENABLED = True              # Sinyal erime aktif mi

# --- Kaos #4: Ceza Kutusu (penalty_box.py) ---
PENALTY_CONSECUTIVE_WARNING = 2    # 2 ardışık SL → uyarı (R:R > 3:1 gerekir)
PENALTY_CONSECUTIVE_PENALTY = 3    # 3 ardışık SL → 24 saat yasak
PENALTY_CONSECUTIVE_BANNED = 5     # 5 ardışık SL → 72 saat yasak
PENALTY_DAILY_COMMISSION_LIMIT = 99.0  # Günlük komisyon limiti (sermaye % - test için 99.0 yapıldı)
PENALTY_BOX_ENABLED = True         # Ceza kutusu aktif mi

# --- Kaos #5: Strateji Karnesi (strategy_scorecard.py) ---
SCORECARD_MAX_TRADES = 500         # Kayıtta tutulacak max işlem sayısı
SCORECARD_AUTO_DISABLE_DAYS = 60   # Darwinizm penceresi (gün)
SCORECARD_MIN_TRADES = 5           # Darwinizm için minimum işlem sayısı
SCORECARD_ENABLED = True           # Strateji karnesi aktif mi
SCORECARD_WEEKLY_REPORT_DAY = 6    # Pazar = 6 (0=Pazartesi)

# ════════════════════════════════════════
# Conviction A/B Test Konfigürasyonu
# ════════════════════════════════════════
CONVICTION_AB_ENABLED: bool = os.getenv('CONVICTION_AB_ENABLED', 'true').lower() == 'true'

# Control grubu (mevcut üretim eşikleri)
CONVICTION_THRESHOLDS_CONTROL = {
    'STRONG': 75,
    'MEDIUM': 60.1,
    'WATCH': 45,
}

# Experiment grubu (test eşikleri — daha agresif)
CONVICTION_THRESHOLDS_EXPERIMENT = {
    'STRONG': 70,
    'MEDIUM': 55,
    'WATCH': 40,
}

# ════════════════════════════════════════
# Devre Kesici (Circuit Breaker)
# ════════════════════════════════════════
MAX_CONSECUTIVE_SL = 3       # Ard arda 3 SL → devre aç (sessiz mod)
COOLDOWN_HOURS = 24          # Sessiz mod süresi (saat)
DAILY_MAX_SL = 99            # Günlük toplam SL limiti (ardışık olmasa bile)

# ════════════════════════════════════════
# ⚖️ Sinyal Çelişki Çözücü & Data Guard Soft Penalties
# ════════════════════════════════════════
CONFLICT_RESOLVER_ENABLED = True
CONFLICT_RESOLVER_ADX_TREND_LIMIT = 40.0   # Bu ADX üstünde Mean Reversion ceza alır
CONFLICT_RESOLVER_ADX_RANGING_LIMIT = 20.0 # Bu ADX altında Trend Takip/Breakout ceza alır
CONFLICT_RESOLVER_ADX_TREND_PENALTY_MULT = 3.75    # Mean Reversion için ADX > 40 aşımı çarpanı (eski: 5.0)
CONFLICT_RESOLVER_ADX_RANGING_PENALTY_MULT = 5.25  # Trend Takip için ADX < 20 sapması çarpanı (eski: 7.0)
CONFLICT_RESOLVER_BEAR_TREND_PENALTY = 0.7         # 1D Bearish rejimde Long sinyallere uygulanacak ceza katsayısı (eski: 0.6 - %40 kesinti yerine %30 kesinti)

# 🎯 Nihai Conviction Skor Çarpanı (V3.8)
# Hesaplanmış nihai inanç (conviction) skorunu doğrudan bu oranla çarpar. 
# 0.91 çarpanı, 66 alan bir skoru 60'a düşürür (yaklaşık %9-10 genel skor kesintisi).
CONVICTION_SCORE_MULTIPLIER = 0.91

DATA_GUARD_PENALTY_DARTH_MAUL = -18.75       # Flash crash mumu soft cezası (eski: -25.0)
DATA_GUARD_PENALTY_GAP = -15.0              # Gap mumu soft cezası (eski: -20.0)
DATA_GUARD_PENALTY_CMF_WASH_TRADE = -11.25   # CMF sıfır altı wash trade cezası (eski: -15.0)
DATA_GUARD_PENALTY_LIQUIDITY_WINDOW = -22.5 # Ters saatlerde (bölge dışı) açılan işlem cezası (eski: -30.0)

# ⚖️ Dinamik Strateji Filtreleri & Teyit Ayarları (V3.4)
# ────────────────────────────────────────────────────────────────

# --- 1. Dip Avcılığı & Mean Reversion ---
DIP_RSI_1D_SMA200_ALIGN_ENABLED: bool = True  # BIST: Long için 1D Fiyat > SMA200 filtresi aktif mi
DIP_RSI_1D_EMA50_ALIGN_ENABLED: bool = False   # Kripto: Long için 1D Fiyat > EMA50 filtresi aktif mi (kapatıldı)
DIP_VOLUME_SPIKE_REQUIRED: bool = True        # Düşen bıçağı tutmamak için dönüş anında kurumsal hacim patlaması şartı
DIP_VOLUME_SPIKE_MULT: float = 1.5            # Dönüş mumundaki hacmin, hacim SMA'sına oranı (min 1.5 katı)

# --- 2. Trend Takibi & Volatilite (ADX & EMA) ---
TREND_BB_SQUEEZE_BLOCKED: bool = True         # Dar bant squeeze içindeyken trend takibini bloke et

# --- 3. Kırılım Avcılığı (Breakout) & Retest ---
BREAKOUT_RETEST_REQUIRED: bool = True         # Sahte kırılımları (fakeout) engellemek için retest şartı
BREAKOUT_RETEST_TOLERANCE_PCT: float = 1.5    # Retest için kırılan direnç seviyesine maksimum uzaklık yüzdesi (%1.5)

# --- 4. Keskin Nişancı (SMC - OTE / FVG) ---
SMC_FVG_REQUIRED: bool = False                # FVG'yi zorunlu kılmaz (Soft score bonusudur)
SMC_FVG_BONUS: float = 15.0                   # FVG varlığında verilecek ek puan
SMC_LTF_MSB_CONFIRM: bool = False              # OTE bölgesi içindeyken 1H grafikte MSB teyidi ara (kapatıldı)

# --- 5. Volatilite Sıkışması (Squeeze) ---
SQUEEZE_MOMENTUM_ALIGN_REQUIRED: bool = True  # Momentum histogram yönünün kırılım yönüyle uyumu
SQUEEZE_TREND_ALIGN_REQUIRED: bool = True     # Squeeze patlama yönünün 1D ana trendiyle uyumu

# --- 6. Relative Strength (RS) ---
RS_ENTRY_TIMING_RSI_LIMIT: float = 90.0       # RS hissesinde işleme giriş için 1H RSI tavanı

# --- 7. VWAP Mıknatısı (VWAP Bounce) ---
VWAP_SLOPE_CONFIRMATION: bool = True          # VWAP eğiminin pozitif (long) / negatif (short) olması şartı
VWAP_SLOPE_LOOKBACK: int = 3                  # Eğim hesaplanacak mum sayısı
VWAP_BOUNCE_CANDLE_CONFIRM: int = 1           # VWAP üzerinde tutunmayı gösteren mum kapanışı sayısı (Örn: 1 veya 2)
VWAP_SL_BUFFER_PCT: float = 1.5               # VWAP altı güvenli stop tampon yüzdesi (Örn: %1.5)
VWAP_BOUNCE_LOWER_SHADOW_MULT: float = 0.5    # Pin-bar yerine, güçlü gövdeli ralli mumlarına izin verir (2.0'dan 0.5'e).
VWAP_TOLERANCE_PCT: float = 0.003             # VWAP'a %0.3 oranında yaklaşmayı temas (bounce) olarak kabul eder.

# --- 8. OBV Accumulation ---
OBV_SMA_ALIGN_REQUIRED: bool = True           # OBV'nin kendi 20 SMA'sı üzerinde olması koşulu
OBV_SMA_PERIOD: int = 20

# --- 9. Opening Range Breakout (ORB) ---
ORB_BODY_CLOSE_REQUIRED: bool = False          # ORB kırılımlarında mumun gövdeyle dışarıda kapanması koşulu

ORB_VOLUME_MULT: float = 1.3                  # ORB kırılım barı hacminin ortalama RVOL'e oranı

# --- 10. Parabolic Reversal Short ---
SHORT_TREND_ALIGN_REQUIRED: bool = True       # Short için fiyatın 50 EMA / 200 SMA altında olması koşulu
SHORT_RSI_OVERBOUGHT_LIMIT: float = 80.0      # Parabolic Short için RSI aşırı alım taban limiti

# --- 11. Divergence (Uyumsuzluk) & MACD ---
DIVERGENCE_MACD_CONFIRMATION_REQUIRED: bool = True  # Uyumsuzluk teyidi için MACD histogram doğrulaması şartı

# --- 12. Swing Failure Pattern (SFP) ---
SFP_BODY_CLOSE_INSIDE_REQUIRED: bool = True   # Mum gövdesinin eski seviye içinde kalması koşulu (wick grab)
SFP_VOLUME_CONFIRMATION_MULT: float = 1.3     # SFP barı hacminin 20 SMA hacmine oranı
SFP_MFE_TIME_FILTER_REQUIRED: bool = True     # SFP sonrası fiyatın hızlıca dönmesini bekleyen zaman/MFE filtresi
SFP_MFE_TIME_LIMIT_HOURS: int = 12            # SFP işlemi açıldıktan sonra geçen maksimum saat (örn: 3 mum = 12 saat)
SFP_MFE_MIN_PROFIT_PCT: float = 0.5           # Limit süresi sonunda bu kâr oranına ulaşılmadıysa pozisyon kapatılır

# 99 yapılmıştır
# --- 13. Zaman Stopu (Time Stop) ---
TIME_STOP_ENABLED: bool = True                # Zaman stopu mekanizması aktif/pasif
TIME_STOP_HOURS: int = 4                      # Kırılım işlemlerinde sahte kırılım tespiti için maksimum süre (saat)
TIME_STOP_MIN_PROFIT_PCT: float = 0.5         # Belirlenen süre sonunda hedeflenen minimum kâr (%)
TIME_STOP_STRATEGIES: list = [                # Zaman stopu uygulanacak kırılım stratejileri listesi (BIST/Kripto/Emtia)
    "BIST 3: SQUEEZE KIRILIMI",
    "BIST 5: HACİMLİ KIRILIM",
    "BIST 9: ZAMAN KAFESİ (ORB)",
    "BIST 11: MUM FORMASYONLARI (CANDLESTICK)",
    "BIST 12: GRAFİK FORMASYONLARI (CHART PATTERNS)",
    "KRİPTO 3: SAHTE KIRILIM FİLTRESİ (RETEST)",
    "EMTİA 3: VOLATİLİTE SIKIŞMASI (SQUEEZE)",
    "BEAR 3: YAPI KIRILIMI"
]

# --- 14. Hibrit Stop Motoru ---
HYBRID_STOP_ENABLED: bool = True             # Hibrit dinamik trailing stop aktif/pasif
ANTI_HUNT_OFFSET_PCT: float = 0.00034         # Balinaların stop-avı yapmasını önleyici asimetrik kaydırma oranı (%0.034)
STRUCTURAL_STOP_ENABLED: bool = True          # EMA-20 yapısal trend desteği alt zemin koruması aktif/pasif

# --- 15. Kripto Short-4 (Keskin Nişancı) ---
SHORT4_BBP_MAX_PULLBACK = 1.05
SHORT4_BBP_MIN_PULLBACK = 0.85

# ════════════════════════════════════════
# 🚀 Vectorbt Yüksek Hızlı Backtest Motoru (V4.0)
# ════════════════════════════════════════
VBT_COMMISSION: float = 0.001                 # Binde 1 komisyon varsayımı
VBT_SLIPPAGE: float = 0.001                   # Binde 1 slippage (kayma) varsayımı
VBT_INITIAL_CASH: float = 10000.0             # Başlangıç portföy büyüklüğü

# --- 16. Hibrit Piyasa Zamanlayıcı (Test Modu) ---
BYPASS_TIME_ROUTING: bool = False             # Zamanlayıcıyı tamamen devre dışı bırakıp her şeyi aynı anda taratmak için True yapın

# --- 17. BIST 11: Mum Formasyonları (Candlestick) ---
BIST11_ATR_MULTIPLIER: float = 2.0            # ATR tabanlı trailing stop çarpanı
BIST11_VOLUME_SMA_PERIOD: int = 10            # Hacim teyidi için geriye dönük SMA periyodu
BIST11_VOLUME_MULT: float = 1.25              # Formasyon mumunun hacminin ortalama hacme oranı
BIST11_SUPPORT_TOLERANCE_PCT: float = 2.0     # Destek seviyelerine maksimum yakınlık yüzdesi
BIST11_DIVERGENCE_REQUIRED: bool = False      # RSI/MACD uyumsuzluğu zorunlu mu

# --- 18. BIST 12: Grafik Formasyonları (Chart Patterns) ---
BIST12_PROMINENCE_ATR_MULT: float = 0.75      # find_peaks için dinamik prominence (ATR oranı)
BIST12_VOLATILITY_TOLERANCE_MULT: float = 1.0 # volatiliteye duyarlı tolerans katsayısı
BIST12_OBO_BASE_TOLERANCE_PCT: float = 5.0    # OBO/TOBO omuzları arası taban tolerans yüzdesi (%)
BIST12_NECK_TOLERANCE_PCT: float = 3.0       # OBO/TOBO boyun çizgisi hizalama toleransı (%)
BIST12_DOUBLE_BASE_TOLERANCE_PCT: float = 4.5 # İkili dip/tepe taban tolerans yüzdesi (%)
BIST12_RECTANGLE_HEIGHT_PCT: float = 5.5      # Dikdörtgen konsolidasyon kutusu maksimum yüksekliği (%)
BIST12_FLAG_POLE_MIN_PCT: float = 10.0        # Bayrak direği minimum yükseliş/düşüş oranı (%)
BIST12_FLAG_CONSOLIDATION_BARS: int = 6       # Bayrak konsolidasyon bar sayısı
BIST12_VOLUME_MULT: float = 2.15              # Kırılım mumunun hacminin aynı seans ortalamasına oranı (min 2.15 kat)
BIST12_RSI_DIVERGENCE_REQUIRED: bool = True   # İkili Dip, TOBO ve Elmas için RSI pozitif uyumsuzluğu zorunlu
BIST12_ATR_MULTIPLIER: float = 2.0            # ATR tabanlı stop loss çarpanı
BIST12_MIN_SL_PCT: float = 0.025              # Minimum stop loss mesafesi (örneğin %2.5)
BIST12_WEDGE_CONVERGENCE_FACTOR: float = 0.1  # Takoz formasyonlarında çizgilerin yakınsama oranı (fark)
BIST12_TRIANGLE_SLOPE_TOLERANCE: float = 0.08 # Yönlü üçgenlerde direnç/destek çizgisinin maksimum eğimi
BIST12_HARMONIC_TOLERANCE: float = 0.07       # Harmonik formasyonlarda Fibonacci oran toleransı (%) (katı %2 limit -> esnek %7)
BIST12_SMC_STRICT_MODE: bool = False          # Eğer True ise Takoz ve Üçgenlerde FVG ve Likidite Temizliği arar. False ise sadece CHoCH yeterlidir.

# --- 19. Kripto Optimizasyon Ayarları (KURAL 6 SSOT) ---
BREAKOUT_CRYPTO_FUNDING_RATE_MAX: float = 0.05  # Kripto 3 Breakout için maksimum fonlama oranı
SHORT1_CRYPTO_FUNDING_RATE_MIN: float = 0.0001  # Short 1 Fomo İnfazı için minimum fonlama oranı
SHORT3_CANYON_PROXIMITY_MIN: float = -0.02       # Short 3 Uçurum Çöküşü için minimum yakınlık toleransı
SHORT3_CANYON_PROXIMITY_MAX: float = 0.10        # Short 3 Uçurum Çöküşü için maksimum yakınlık toleransı

# ════════════════════════════════════════
# 21. Extracted Magic Numbers & Thresholds (SSOT Alignment)
# ════════════════════════════════════════

# --- Bear Hunter Specific ---
BEAR_HUNTER_DEFAULT_ATR_MULT = 0.02
BEAR_HUNTER_SFP_ATR_SL_MULT = 0.3
BEAR_HUNTER_PREMIUM_ATR_SL_MULT = 0.5
BEAR_HUNTER_DIV_ATR_SL_MULT = 0.5
BEAR_HUNTER_TP_RR = 2.0   # 10x kaldıraç risk/ödül oranı için (eski: 1.5)

# --- BIST Strategy Specific ---
BIST_DIP_HUNTER_RSI_1D_LIMIT = 35.0
BIST_DIP_HUNTER_DEFAULT_TP_MULT = 1.05
BIST_TREND_FOLLOW_TP_MULT = 1.10
BIST_SQUEEZE_PREV_WIDTH_LIMIT = 0.25
BIST_SMC_BREAKOUT_VOL_MULT = 1.5
BIST_SMC_BREAKOUT_SL_MULT = 0.995
BIST_VWAP_EMA_SL_FALLBACK_LONG = 0.95
BIST_VWAP_EMA_SL_FALLBACK_SHORT = 1.05
BIST_OBV_ACC_MAX_CHANGE_PCT = 7.0
BIST_OBV_CMF_THRESHOLD = 0.05
BIST_SQUEEZE_BB_PCT_TOUCH_LIMIT = 0.1
BIST_SQUEEZE_SL_MIN_MULT = 0.95
BIST_SQUEEZE_SL_BBL_MULT = 0.985
BIST_CANDLE_RSI_D_LIMIT_HAMMER = 45.0
BIST_CANDLE_RSI_D_LIMIT_ENGULFING_MIN = 45.0
BIST_CANDLE_RSI_D_LIMIT_ENGULFING_MAX = 52.0
BIST_CANDLE_RSI_D_LIMIT_SOLDIERS = 70.0
BIST_CHART_RSI_D_LIMIT = 60.0
BIST_CHART_EMA21_DIST_LIMIT = 6.0
BIST_CHART_RVOL_LOOKBACK = 11
BIST_MONTH_HIGH_LOOKBACK = 30
BIST_SWING_MIN_VOLUME_TL = 20_000_000
BIST_MIN_STOCK_PRICE_TL = 3.0
BIST_ORB_LONG_ENTRY_OFFSET = 1.001
BIST_ORB_SHORT_ENTRY_OFFSET = 0.999

# --- Crypto Strategy Specific ---
CRYPTO_DIP_SL_MULT = 0.99
CRYPTO_SQUEEZE_WIDTH_LIMIT = 0.15
CRYPTO_TREND_SL_EMA_MULT = 0.98
CRYPTO_BREAKOUT_WIDTH_MULT = 1.20
CRYPTO_BREAKOUT_RETEST_SL_MULT = 0.99
CRYPTO_BREAKOUT_MIN_SL = 0.03  # 5x kaldıraç için güncellendi (eski: 0.015)
CRYPTO_BREAKOUT_MAX_SL = 0.06  # 5x kaldıraç için güncellendi (eski: 0.04)
CRYPTO_SHORT1_SL_MULT = 1.02
CRYPTO_SHORT3_SL_MULT = 1.03
CRYPTO_SHORT3_TP_RR = 2.5
CRYPTO_LONG4_SL_MULT = 0.995
CRYPTO_SHORT4_SL_MULT = 1.005
CRYPTO_VWAP_SL_MULT = 0.99
CRYPTO_VWAP_ADX_MIN = 35.0
CRYPTO_SQUEEZE_SL_BBL_MULT = 0.98
CRYPTO_SQUEEZE_SL_MIN_MULT = 0.93
CRYPTO_SQUEEZE_SHORT_SL_BBU_MULT = 1.01
CRYPTO_SQUEEZE_SHORT_SL_MAX_MULT = 1.07
CRYPTO_BREAKOUT_LOOKBACK = 30
CRYPTO_BREAKOUT_RETEST_LOOKBACK = 15
CRYPTO_BREAKOUT_VOLUME_MULT = 2.0
CRYPTO_SHORT3_SUPPORT_LOOKBACK = 75
CRYPTO_SHORT3_BREAKOUT_ZONE = 15
CRYPTO_TREND_ADX_MIN = 25
CRYPTO_OBV_ACC_MAX_CHANGE_PCT = 5.0
CRYPTO_SHORT2_ADX_MIN = 40.0
CRYPTO_SHORT2_VOLUME_SMA_MULT = 0.35
CRYPTO_TREND_VOLUME_SMA_MULT = 0.8
CRYPTO_RETEST_RSI_MAX = 70.0
CRYPTO_RETEST_ADX_MIN = 22.0
CRYPTO_SQUEEZE_LONG_ADX_MIN = 15.0

# --- New Crypto Short Strategies Requirements ---
CRYPTO_SHORT_MIN_RR = 2.0
CRYPTO_SHORT1_SFP_TOLERANCE = 0.005
CRYPTO_SHORT2_FUNDING_MIN = 0.0001
CRYPTO_SHORT4_RETEST_TOLERANCE = 0.015
CRYPTO_SHORT5_FLAG_VOL_MULT = 0.7

# --- Emtia Strategy Specific ---
EMTIA_SMC_LONG_ATR_SL_MULT = 0.5
EMTIA_SMC_SHORT_ATR_SL_MULT = 0.5
EMTIA_LOOKBACK_LIMIT = 30
EMTIA_SQUEEZE_WIDTH_LIMIT = 0.15
EMTIA_TREND_ADX_MIN = 25

# --- Indicators Specific ---
PATTERN_SL_BUFFER = 0.01
WEDGE_SLOPE_THRESHOLD = -0.05
TRIANGLE_HEIGHT_VAR_LIMIT = 2.5
DOUBLE_TOP_HIGH_TOLERANCE = 1.05
FIB_618 = 0.618
FIB_786 = 0.786
OBV_ACC_MAX_CHANGE_PCT = 8.0
CANDLE_HAMMER_LOWER_SHADOW_MULT = 2.0
CANDLE_HAMMER_UPPER_SHADOW_LIMIT = 0.1
CANDLE_DRAGONFLY_BODY_LIMIT = 0.05
CANDLE_DRAGONFLY_LOWER_SHADOW_MULT = 0.7
CANDLE_MORNING_STAR_BODY_LIMIT = 0.3
CANDLE_SOLDIERS_BODY_MIN = 0.1
CANDLE_SOLDIERS_SHADOW_LIMIT = 0.2

# --- Conviction Scorer Specific ---
SNIPER_NO_SETUP_PENALTY = 12.0
BIST_SNIPER_CONFLUENCE_BONUS = 1.5

# --- Additional Squeeze Constants ---
BIST_SQUEEZE_ROLLING_WINDOW = 10
BIST_SQUEEZE_QUANTILE = 0.3
CRYPTO_SQUEEZE_ADX_MIN = 22.5

# --- Helpers Specific ---
IND_VOL_SMA_SLOW = 50
VOL_SMA_FLOOR_MULT = 0.7
MEANINGFUL_VOLUME_MULT = 1.5
COOLDOWN_SECONDS_1H = 3600


# --- VWAP Strategy Filters ---
VWAP_LONG_MAX_RSI = 60.0
VWAP_MAX_ATR_RATIO = 2.0
OTE_TOLERANCE_PCT = 0.02
MSB_TOLERANCE_PCT = 0.001

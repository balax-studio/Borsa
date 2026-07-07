
"""
data_sources.py — Veri Katmanı
Tüm API bağlantıları, veri çekme fonksiyonları ve cache yönetimi.
"""
import ccxt
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import time as _time
import logging
import gc
import threading
import warnings
from datetime import datetime, time as dt_time
from zoneinfo import ZoneInfo
from typing import NamedTuple, Any, Optional

# 99 yapılmıştır
# yfinance yedek veri çekimlerinde config.DATA_PERIOD_1D gibi tanımlara erişebilmek için
# config modülü import edilmiştir.
import config
from config import IS_USA_SERVER, CACHE_TTL_SECONDS, OHLCV_LIMIT, OI_CRASH_PCT, API_SLEEP_BIST
from data_guard import guard_dataframe

warnings.filterwarnings('ignore')

YF_CRYPTO_TICKER_MAP = {
    "SUI/USDT": "SUI20947-USD",
    "APT/USDT": "APT21794-USD"
}

def get_yf_crypto_ticker(symbol: str) -> str:
    if symbol in YF_CRYPTO_TICKER_MAP:
        return YF_CRYPTO_TICKER_MAP[symbol]
    return symbol.replace("/USDT", "-USD")

# ════════════════════════════════════════
# Exchange Instances
# ════════════════════════════════════════
exchange = ccxt.bybit({
    'enableRateLimit': True,
    'timeout': 20000,
    'options': {'defaultType': 'linear'} if config.CCXT_FETCH_FUTURES_DATA else {}
})
exchange_futures = ccxt.bybit({
    'enableRateLimit': True,
    'timeout': 20000,
    'options': {'defaultType': 'linear'}
})
exchange_fallback = ccxt.kraken({
    'enableRateLimit': True,
    'timeout': 20000
})

import ccxt.async_support as ccxt_async
import asyncio
exchange_async = ccxt_async.bybit({
    'enableRateLimit': True,
    'timeout': 20000,
    'options': {'defaultType': 'linear'} if config.CCXT_FETCH_FUTURES_DATA else {}
})
exchange_fallback_async = ccxt_async.kraken({
    'enableRateLimit': True,
    'timeout': 20000
})

_primary_symbols = None
_kraken_symbols = None
_markets_loading_lock = asyncio.Lock()

async def _ensure_markets_loaded():
    global _primary_symbols, _kraken_symbols
    if _primary_symbols is not None and _kraken_symbols is not None:
        return
        
    async with _markets_loading_lock:
        if _primary_symbols is not None and _kraken_symbols is not None:
            return
            
        logging.info("[data_sources] Loading exchange markets for fast routing...")
        try:
            await exchange_async.load_markets()
            _primary_symbols = set(exchange_async.symbols)
        except Exception as e:
            logging.warning(f"[data_sources] Failed to load Primary (Bybit) markets: {e}")
            _primary_symbols = set()
            
        try:
            await exchange_fallback_async.load_markets()
            _kraken_symbols = set(exchange_fallback_async.symbols)
        except Exception as e:
            logging.warning(f"[data_sources] Failed to load Kraken markets: {e}")
            _kraken_symbols = set()
        logging.info(f"[data_sources] Markets loaded. Primary Futures: {len(_primary_symbols)}, Kraken: {len(_kraken_symbols)}")

# ════════════════════════════════════════

# Unified Cache
# ════════════════════════════════════════
class _CacheEntry(NamedTuple):
    timestamp: float
    value: Any

_cache: dict[str, _CacheEntry] = {}
_cache_lock = threading.Lock()  # Thread-Safety: _cache erişimini korur

def _get_cached(key: str, ttl: int = CACHE_TTL_SECONDS) -> Optional[Any]:
    with _cache_lock:
        entry = _cache.get(key)
        if entry and (_time.time() - entry.timestamp) < ttl:
            return entry.value
        return None

def _set_cached(key: str, value: Any) -> None:
    with _cache_lock:
        _cache[key] = _CacheEntry(_time.time(), value)

def purge_expired_cache():
    """Süresi dolmuş cache entry'lerini siler → E2-micro RAM koruma."""
    with _cache_lock:
        now = _time.time()
        expired = [k for k, v in _cache.items() if now - v.timestamp > CACHE_TTL_SECONDS * 3]
        for k in expired:
            del _cache[k]
        if expired:
            logging.debug(f"[purge_expired_cache] {len(expired)} eski cache entry silindi.")


# ════════════════════════════════════════
# Cycle Cache (Döngü Başına Tek Çekim)
# ════════════════════════════════════════
_ohlcv_cycle_cache: dict[str, tuple] = {}
_cycle_cache_lock = threading.Lock()  # Thread-Safety: cycle cache erişimini korur

def get_crypto_data_cached(symbol):
    """OHLCV'yi döngü başına BİR KEZ çeker. Ayı Avcısı duplikasyonunu önler (~96 API çağrısı tasarruf)."""
    with _cycle_cache_lock:
        if symbol in _ohlcv_cycle_cache:
            return _ohlcv_cycle_cache[symbol]
    result = get_crypto_data(symbol)
    with _cycle_cache_lock:
        _ohlcv_cycle_cache[symbol] = result
    return result

async def get_crypto_data_async_cached(symbol):
    """Async cache check for scanner."""
    with _cycle_cache_lock:
        if symbol in _ohlcv_cycle_cache:
            return _ohlcv_cycle_cache[symbol]
    result = await async_get_crypto_data(symbol)
    with _cycle_cache_lock:
        _ohlcv_cycle_cache[symbol] = result
    return result

def clear_cycle_cache():
    """Her tarama döngüsü başında çağrılır. Eski veriyi temizler."""
    with _cycle_cache_lock:
        _ohlcv_cycle_cache.clear()


# ════════════════════════════════════════
# Yardımcı Fonksiyonlar
# ════════════════════════════════════════
def clean_yf_df(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    
    # 🚀 V4.0 Vektörel NaN/Stale Sanitizasyonu
    # Boşlukları önce önceki fiyatla doldur, kalırsa sonrakiyle, kalırsa sil.
    df = df.ffill().bfill().dropna()
    
    df.columns = [c.lower() for c in df.columns]
    return df

def is_weekend_fakeout_time():
    """Cuma 23:00'dan Pazar 23:00'a kadar Hafta Sonu Fakeout süresi."""
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.weekday() == 4 and now.hour >= 23:
        return True
    if now.weekday() == 5:
        return True
    if now.weekday() == 6 and now.hour < 23:
        return True
    return False

def is_bist_open():
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.weekday() > 4: return False
    return dt_time(9, 55) <= now.time() <= dt_time(18, 10)

def check_xu100_wind():
    try:
        df = yf.download("XU100.IS", period="5d", interval="1d", progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        if len(df) >= 2:
            prev_close = df['Close'].iloc[-2]
            curr_close = df['Close'].iloc[-1]
            pct_change = ((curr_close - prev_close) / prev_close) * 100
            return pct_change < -1.0
    except Exception as e:
        logging.warning(f"[check_xu100_wind] {e}")
    return False

def _is_macro_news_hour():
    """TSİ 15:00-16:30 arası → True (Emtia taraması durdurulur). Hafta içi."""
    now = datetime.now(ZoneInfo("Europe/Istanbul"))
    if now.weekday() >= 5:
        return False
    if now.hour == 15 or (now.hour == 16 and now.minute <= 30):
        return True
    return False


# ════════════════════════════════════════
# Kripto Yardımcıları
# ════════════════════════════════════════
def get_funding_rate(symbol):
    """Anlık Funding Rate'i çeker (Yüzde olarak döner, örn: 0.01)"""
    if not config.CCXT_FETCH_FUTURES_DATA or IS_USA_SERVER:
        return 0.0
    try:
        # Bybit'te funding rate sorgularken format BTC/USDT:USDT şeklindedir.
        target_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        funding = exchange_futures.fetch_funding_rate(target_symbol)
        if funding and 'fundingRate' in funding:
            return float(funding['fundingRate']) * 100
    except ccxt.BadSymbol:
        pass
    except Exception as e:
        if "does not have market symbol" not in str(e):
            logging.warning(f"[get_funding_rate] {symbol}: {e}")
    return 0.0

def get_open_interest(symbol):
    """Anlık Open Interest (Açık Pozisyon) Değerini çeker"""
    if not config.CCXT_FETCH_FUTURES_DATA or IS_USA_SERVER:
        return 0.0
    try:
        target_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        oi_data = exchange_futures.fetch_open_interest(target_symbol)
        if oi_data and 'openInterestValue' in oi_data and oi_data['openInterestValue'] is not None:
            return float(oi_data['openInterestValue'])
    except ccxt.BadSymbol:
        pass
    except Exception as e:
        if "does not have market symbol" not in str(e):
            logging.warning(f"[get_open_interest] {symbol}: {e}")
    return 0.0

def get_order_book_imbalance(symbol, depth=20):
    """
    Emir defterini (Order Book) çeker ve Alış (Bid) ile Satış (Ask) arasındaki
    hacimsel dengesizliği (imbalance) hesaplar.
    + değer: Alıcılar baskın (Buy wall)
    - değer: Satıcılar baskın (Sell wall)
    """
    if IS_USA_SERVER:
        return 0.0
    try:
        orderbook = exchange.fetch_order_book(symbol, limit=depth)
        bids = orderbook['bids']
        asks = orderbook['asks']
        
        bid_vol = sum([amount for price, amount in bids])
        ask_vol = sum([amount for price, amount in asks])
        
        if (bid_vol + ask_vol) == 0:
            return 0.0
            
        imbalance = (bid_vol - ask_vol) / (bid_vol + ask_vol)
        return imbalance
    except ccxt.BadSymbol:
        return 0.0
    except Exception as e:
        if "does not have market symbol" not in str(e):
            logging.warning(f"[get_order_book_imbalance] {symbol}: {e}")
        return 0.0


def fetch_crypto_oi_crash(symbol):
    """Son 24 saat içinde Open Interest (OI) verisinde %15 veya daha büyük bir çöküş var mı?"""
    if IS_USA_SERVER:
        return False
    try:
        ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        oi_hist = exchange_futures.fetch_open_interest_history(ccxt_symbol, timeframe='1h', limit=24)
        if len(oi_hist) > 0:
            key = 'openInterestValue' if 'openInterestValue' in oi_hist[-1] else 'openInterestAmount'
            if key in oi_hist[-1] and oi_hist[-1][key] is not None:
                current_oi = float(oi_hist[-1][key])
                max_oi = max([float(x[key]) for x in oi_hist if x[key] is not None])
                if max_oi > 0:
                    drop_pct = ((max_oi - current_oi) / max_oi) * 100
                    if drop_pct >= OI_CRASH_PCT:
                        return True
    except ccxt.BadSymbol:
        pass
    except Exception as e:
        if "does not have market symbol" not in str(e):
            logging.warning(f"[fetch_crypto_oi_crash] {symbol}: {e}")
    return False

def fetch_crypto_oi_surge(symbol, surge_pct=5.0):
    """Son 1-4 saat içinde Open Interest (OI) verisinde belirtilen oranda bir artış (surge) var mı?"""
    if IS_USA_SERVER:
        return False
    try:
        ccxt_symbol = f"{symbol}:USDT" if ":" not in symbol else symbol
        oi_hist = exchange_futures.fetch_open_interest_history(ccxt_symbol, timeframe='1h', limit=5)
        if len(oi_hist) > 1:
            key = 'openInterestValue' if 'openInterestValue' in oi_hist[-1] else 'openInterestAmount'
            if key in oi_hist[-1] and oi_hist[-1][key] is not None:
                current_oi = float(oi_hist[-1][key])
                min_oi = min([float(x[key]) for x in oi_hist[:-1] if x[key] is not None])
                if min_oi > 0:
                    surge = ((current_oi - min_oi) / min_oi) * 100
                    if surge >= surge_pct:
                        return True
    except ccxt.BadSymbol:
        pass
    except Exception as e:
        if "does not have market symbol" not in str(e):
            logging.warning(f"[fetch_crypto_oi_surge] {symbol}: {e}")
    return False

def get_btc_dominance_trend():
    """BTCDOM/USDT grafiğinden BTC Dominans Trendini hesaplar"""
    if IS_USA_SERVER:
        return "UNKNOWN"
    try:
        ohlcv = exchange_futures.fetch_ohlcv("BTCDOM/USDT", '1d', limit=60)
        df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df.ta.ema(length=20, append=True)
        df.ta.ema(length=50, append=True)
        if len(df) >= 50:
            last_ema20 = df['EMA_20'].iloc[-1]
            last_ema50 = df['EMA_50'].iloc[-1]
            if last_ema20 > last_ema50:
                return "UP"
            else:
                return "DOWN"
    except Exception as e:
        logging.warning(f"[get_btc_dominance_trend] {e}")
    return "UNKNOWN"

def get_usdt_dominance_trend():
    """USDT.D (Tether Dominance) trendini döndürür. STUB: API'den USDT.D grafiği çekilemediği için varsayılan DOWN döner."""
    return "DOWN"

def check_token_unlocks(symbol):
    """Token kilit açılım takvimini kontrol eder. STUB: API altyapısı kurulana kadar False döner."""
    try:
        base_coin = symbol.split('/')[0].lower()
        logging.debug(f"[check_token_unlocks] {symbol}: STUB - API bağlantısı yok, False döndürülüyor.")
        return False
    except Exception as e:
        logging.warning(f"[check_token_unlocks] {symbol}: {e}")
        return False


# ════════════════════════════════════════
# Veri Çekme Fonksiyonları
# ════════════════════════════════════════
# 99 yapılmıştır
# BIST günlük ve saatlik veri periyotları config.DATA_PERIOD_1D ve DATA_PERIOD_1H'a bağlanmıştır.
def get_bist_data(symbol):
    try:
        df_1d = yf.download(symbol, period=config.DATA_PERIOD_1D, interval="1d", progress=False)
        df_1d = clean_yf_df(df_1d)

        df_1h = yf.download(symbol, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
        df_1h = clean_yf_df(df_1h)

        if df_1h.empty or df_1d.empty: return None, None, None

        df_4h = df_1h.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()

        # DataGuard: DG-01 + DG-02 + DG-04 kontrolleri
        df_1d = guard_dataframe(df_1d, symbol, "1d")
        df_4h = guard_dataframe(df_4h, symbol, "4h")
        if df_1d is None or df_4h is None:
            return None, None, None
        df_1h = guard_dataframe(df_1h, symbol, "1h")

        return df_1d, df_4h, df_1h
    except Exception as e:
        logging.warning(f"[get_bist_data] {symbol}: {e}")
        return None, None, None

def get_bist_data_batch(symbols, batch_size=25):
    """
    Toplu yfinance indirme ile BIST verisi çeker.
    200 ayrı HTTP çağrısı → ~8 toplu çağrıya düşürür.
    E2-micro RAM koruması için küçük batch'ler (25 ticker) kullanır.
    """
    results = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        tickers_str = " ".join(batch)
        logging.info(f"[get_bist_data_batch] Batch {i//batch_size + 1}: {len(batch)} ticker indiriliyor...")

        try:
            # 99 yapılmıştır
            # Toplu veri çekiminde period parametreleri config.DATA_PERIOD_1D ve DATA_PERIOD_1H'dan alınmaktadır.
            raw_1d = yf.download(tickers_str, period=config.DATA_PERIOD_1D, interval="1d",
                                  group_by="ticker", threads=True, progress=False)
            raw_1h = yf.download(tickers_str, period=config.DATA_PERIOD_1H, interval="1h",
                                  group_by="ticker", threads=True, progress=False)
        except Exception as e:
            logging.warning(f"[get_bist_data_batch] Batch download hata: {e}, tekli fallback...")
            for sym in batch:
                results[sym] = get_bist_data(sym)
                _time.sleep(API_SLEEP_BIST)
            continue

        for sym in batch:
            try:
                if len(batch) == 1:
                    # Tek ticker → MultiIndex yok
                    df_1d = raw_1d.copy()
                    df_1h = raw_1h.copy()
                else:
                    # MultiIndex → ticker bazında ayır
                    level_values = raw_1d.columns.get_level_values(0).unique()
                    if sym not in level_values:
                        results[sym] = (None, None, None)
                        continue
                    df_1d = raw_1d[sym].copy()
                    df_1h = raw_1h[sym].copy() if sym in raw_1h.columns.get_level_values(0).unique() else pd.DataFrame()

                df_1d = clean_yf_df(df_1d) if not df_1d.empty else pd.DataFrame()
                df_1h = clean_yf_df(df_1h) if not df_1h.empty else pd.DataFrame()

                if df_1d.empty or df_1h.empty:
                    results[sym] = (None, None, None)
                    continue

                df_4h = df_1h.resample('4h').agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'
                }).dropna()

                # K-02: DataGuard — batch BIST verisini de koru
                df_1d = guard_dataframe(df_1d, sym, '1d')
                df_4h = guard_dataframe(df_4h, sym, '4h')
                if df_1d is None:
                    results[sym] = (None, None, None)
                    continue

                results[sym] = (df_1d, df_4h, df_1h)
            except Exception as e:
                logging.warning(f"[get_bist_data_batch] {sym} parse: {e}")
                results[sym] = (None, None, None)

        # Batch arası RAM temizliği (E2-micro koruma)
        del raw_1d, raw_1h
        gc.collect()
        _time.sleep(0.5)  # Batch'ler arası Yahoo throttle koruması

    return results

def get_bist_15m_batch(symbols, batch_size=25):
    """ORB için 15dk batch download."""
    results = {}
    for i in range(0, len(symbols), batch_size):
        batch = symbols[i:i + batch_size]
        tickers_str = " ".join(batch)
        try:
            raw = yf.download(tickers_str, period="5d", interval="15m",
                              group_by="ticker", threads=True, progress=False)
            for sym in batch:
                try:
                    if len(batch) == 1:
                        df = raw.copy()
                    else:
                        level_values = raw.columns.get_level_values(0).unique()
                        if sym not in level_values:
                            results[sym] = None
                            continue
                        df = raw[sym].copy()
                    df = clean_yf_df(df) if not df.empty else None
                    # K-02: DataGuard — 15m batch verisini de koru
                    if df is not None:
                        df = guard_dataframe(df, sym, '15m')
                    results[sym] = df
                except Exception as e:
                    logging.warning(f"[get_bist_15m_batch] {sym}: {e}")
                    results[sym] = None
        except Exception as e:
            logging.warning(f"[get_bist_15m_batch] Batch hata: {e}")
            for sym in batch:
                results[sym] = None

        del raw
        gc.collect()
        _time.sleep(0.3)

    return results

async def async_get_crypto_data(symbol):
    """Fully asynchronous crypto data fetcher with retry logic for scanner."""
    await _ensure_markets_loaded()
    limit = OHLCV_LIMIT
    retries = 3
    base_delay = 1.0
    
    usd_sym = symbol.replace("/USDT", "/USD")

    # 1. Primary Exchange (Bybit)
    primary_symbol = symbol if (_primary_symbols and symbol in _primary_symbols) else (f"{symbol}:USDT" if (_primary_symbols and f"{symbol}:USDT" in _primary_symbols) else None)
    if not IS_USA_SERVER and primary_symbol is not None:
        for attempt in range(retries):
            try:
                ohlcv_1d, ohlcv_4h = await asyncio.gather(
                    exchange_async.fetch_ohlcv(primary_symbol, '1d', limit=limit),
                    exchange_async.fetch_ohlcv(primary_symbol, '4h', limit=limit)
                )
                df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
                df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

                for df in [df_1d, df_4h]:
                    df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                    df.set_index('timestamp', inplace=True)

                df_1d = guard_dataframe(df_1d, symbol, "1d")
                df_4h = guard_dataframe(df_4h, symbol, "4h")
                if df_1d is not None and df_4h is not None:
                    return df_1d, df_4h
                break  # If DataGuard fails, don't retry, move to fallback
            except ccxt.BadSymbol:
                logging.warning(f"[async_get_crypto_data] {symbol} not in Primary Exchange Futures. Skipping.")
                break
            except Exception as e:
                logging.warning(f"[async_get_crypto_data] attempt {attempt+1} failed for {symbol}: {e}")
                if attempt < retries - 1:
                    await asyncio.sleep(base_delay * (2 ** attempt))

    # 2. Kraken Fallback
    if _kraken_symbols is not None and (symbol in _kraken_symbols or usd_sym in _kraken_symbols):
        try:
            target_sym = symbol if symbol in _kraken_symbols else usd_sym
            ohlcv_1d, ohlcv_4h = await asyncio.gather(
                exchange_fallback_async.fetch_ohlcv(target_sym, '1d', limit=limit),
                exchange_fallback_async.fetch_ohlcv(target_sym, '4h', limit=limit)
            )
            df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            for df in [df_1d, df_4h]:
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)

            df_1d = guard_dataframe(df_1d, symbol, '1d')
            df_4h = guard_dataframe(df_4h, symbol, '4h')
            if df_1d is not None and df_4h is not None:
                return df_1d, df_4h
        except Exception as ekr:
            logging.warning(f"[async_get_crypto_data] Kraken failed for {symbol}: {ekr}")

    # 3. Fallback to yfinance via thread
    try:
        yf_ticker = get_yf_crypto_ticker(symbol)
        
        def fetch_yf():
            df1 = yf.download(yf_ticker, period=config.DATA_PERIOD_1D, interval="1d", progress=False)
            df1 = clean_yf_df(df1)
            df_h = yf.download(yf_ticker, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
            df_h = clean_yf_df(df_h)
            return df1, df_h

        df_1d, df_1h = await asyncio.to_thread(fetch_yf)
        
        if df_1h.empty or df_1d.empty:
            return None, None

        df_4h = df_1h.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()

        df_1d = guard_dataframe(df_1d, symbol, '1d')
        df_4h = guard_dataframe(df_4h, symbol, '4h')
        if df_1d is None or df_4h is None:
            logging.warning(f'[async_get_crypto_data] {symbol}: yfinance rejected by DataGuard')
            return None, None
        return df_1d, df_4h
    except Exception as eyf:
        logging.warning(f"[async_get_crypto_data] {symbol} failed all sources: {eyf}")
        return None, None


async def async_get_crypto_1h_data(symbol):
    """Fully asynchronous LTF 1H data fetcher."""
    await _ensure_markets_loaded()
    limit = OHLCV_LIMIT
    usd_sym = symbol.replace("/USDT", "/USD")

    # 1. Try Primary Exchange (Bybit)
    primary_symbol = symbol if (_primary_symbols and symbol in _primary_symbols) else (f"{symbol}:USDT" if (_primary_symbols and f"{symbol}:USDT" in _primary_symbols) else None)
    if not IS_USA_SERVER and primary_symbol is not None:
        try:
            ohlcv_1h = await exchange_async.fetch_ohlcv(primary_symbol, '1h', limit=limit)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
            df_1h.set_index('timestamp', inplace=True)
            df_1h = guard_dataframe(df_1h, symbol, "1h")
            if df_1h is not None:
                return df_1h
        except Exception as e:
            logging.info(f"[async_get_crypto_1h_data] Primary exchange futures failed: {e}")

    # 2. Try Kraken Fallback
    if _kraken_symbols is not None and (symbol in _kraken_symbols or usd_sym in _kraken_symbols):
        try:
            target_sym = symbol if symbol in _kraken_symbols else usd_sym
            ohlcv_1h = await exchange_fallback_async.fetch_ohlcv(target_sym, '1h', limit=limit)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
            df_1h.set_index('timestamp', inplace=True)
            df_1h = guard_dataframe(df_1h, symbol, '1h')
            if df_1h is not None:
                return df_1h
        except Exception as e:
            logging.info(f"[async_get_crypto_1h_data] Kraken failed: {e}")

    # 3. Fallback to yfinance via thread
    try:
        yf_ticker = get_yf_crypto_ticker(symbol)
        
        def fetch_yf_1h():
            df_h = yf.download(yf_ticker, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
            return clean_yf_df(df_h)

        df_1h = await asyncio.to_thread(fetch_yf_1h)
        df_1h = guard_dataframe(df_1h, symbol, '1h')
        return df_1h
    except Exception as eyf:
        logging.warning(f"[async_get_crypto_1h_data] yfinance failed for {symbol}: {eyf}")
        return None



def get_crypto_data(symbol):
    limit = OHLCV_LIMIT
    if not IS_USA_SERVER:
        try:
            ohlcv_1d = exchange.fetch_ohlcv(symbol, '1d', limit=limit)
            df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            ohlcv_4h = exchange.fetch_ohlcv(symbol, '4h', limit=limit)
            df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

            for df in [df_1d, df_4h]:
                df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
                df.set_index('timestamp', inplace=True)

            # DataGuard: DG-01 + DG-02 + DG-04
            df_1d = guard_dataframe(df_1d, symbol, "1d")
            df_4h = guard_dataframe(df_4h, symbol, "4h")
            if df_1d is None or df_4h is None:
                logging.warning(f"[get_crypto_data] {symbol}: DataGuard reddetti, yedeklere geçiliyor")
            else:
                return df_1d, df_4h
        except Exception as e:
            logging.info(f"[get_crypto_data] Primary exchange hatası, yedeklere geçiliyor: {e}")

    # Kraken ve yfinance yedekleri
    try:
        try:
            ohlcv_1d = exchange_fallback.fetch_ohlcv(symbol, '1d', limit=limit)
            ohlcv_4h = exchange_fallback.fetch_ohlcv(symbol, '4h', limit=limit)
        except Exception:
            usd_sym = symbol.replace("/USDT", "/USD")
            ohlcv_1d = exchange_fallback.fetch_ohlcv(usd_sym, '1d', limit=limit)
            ohlcv_4h = exchange_fallback.fetch_ohlcv(usd_sym, '4h', limit=limit)

        df_1d = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_4h = pd.DataFrame(ohlcv_4h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])

        for df in [df_1d, df_4h]:
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)

        # K-02: DataGuard — Kraken yedek verisi de korunsun
        df_1d = guard_dataframe(df_1d, symbol, '1d')
        df_4h = guard_dataframe(df_4h, symbol, '4h')
        if df_1d is None or df_4h is None:
            raise ValueError(f'[get_crypto_data] {symbol}: Kraken verisi DataGuard reddetti')
        else:
            return df_1d, df_4h
    except Exception as ekr:
        try:
            yf_ticker = get_yf_crypto_ticker(symbol)
            # 99 yapılmıştır
            # Kripto yfinance yedek veri çekiminde periyotlar config.DATA_PERIOD_1D ve DATA_PERIOD_1H'a bağlanmıştır.
            df_1d = yf.download(yf_ticker, period=config.DATA_PERIOD_1D, interval="1d", progress=False)
            df_1d = clean_yf_df(df_1d)

            df_1h = yf.download(yf_ticker, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
            df_1h = clean_yf_df(df_1h)

            if df_1h.empty or df_1d.empty: return None, None

            df_4h = df_1h.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()

            # K-02: DataGuard — yfinance yedek verisi de korunsun
            df_1d = guard_dataframe(df_1d, symbol, '1d')
            df_4h = guard_dataframe(df_4h, symbol, '4h')
            if df_1d is None or df_4h is None:
                logging.warning(f'[get_crypto_data] {symbol}: yfinance verisi DataGuard reddetti')
                return None, None

            return df_1d, df_4h
        except Exception as eyf:
            logging.warning(f"[get_crypto_data] {symbol} hiçbir kaynaktan çekilemedi: {eyf}")
            return None, None

# 99 yapılmıştır
# Emtia veri çekim periyotları config.DATA_PERIOD_1D ve config.DATA_PERIOD_1H'a bağlanmıştır.
def get_emtia_data(symbol):
    """Emtia vadeli kontrat için 1D ve 4H verisini yfinance üzerinden çeker."""
    try:
        df_1d = yf.download(symbol, period=config.DATA_PERIOD_1D, interval="1d", progress=False)
        df_1d = clean_yf_df(df_1d)
        if df_1d.empty:
            return None, None
        df_1h_raw = yf.download(symbol, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
        df_1h_raw = clean_yf_df(df_1h_raw)
        df_4h = None
        if not df_1h_raw.empty:
            df_4h = df_1h_raw.resample('4h').agg({
                'open': 'first', 'high': 'max', 'low': 'min',
                'close': 'last', 'volume': 'sum'
            }).dropna()

        # DataGuard: DG-01 + DG-02 + DG-04
        df_1d = guard_dataframe(df_1d, symbol, "1d")
        if df_1d is None:
            return None, None
        if df_4h is not None:
            df_4h = guard_dataframe(df_4h, symbol, "4h")

        return df_1d, df_4h
    except Exception as e:
        logging.warning(f"[get_emtia_data] {symbol}: {e}")
        return None, None

def get_bist_15m_data(symbol):
    """BIST hissesi için 15 dakikalık veri çeker (ORB stratejisi için)."""
    try:
        df = yf.download(symbol, period="5d", interval="15m", progress=False)
        df = clean_yf_df(df)
        if df.empty:
            return None
        # K-02/FIX4: DataGuard — 15m verisi de korunsun
        df = guard_dataframe(df, symbol, '15m')
        return df
    except Exception as e:
        logging.warning(f"[get_bist_15m_data] {symbol}: {e}")
        return None


# ════════════════════════════════════════
# Cache'li BTC & Endeks Fonksiyonları
# ════════════════════════════════════════
def get_btc_status():
    """BTC > EMA20 Kontrolü (Zorunlu BTC İzni Ana Şalteri)"""
    cached = _get_cached('btc_status')
    if cached is not None:
        return cached

    res = False
    df = None
    if not IS_USA_SERVER:
        try:
            ohlcv_1d = exchange.fetch_ohlcv("BTC/USDT", '1d', limit=50)
            df = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            logging.info(f"[get_btc_status] Binance hatası, yedeklere geçiliyor: {e}")

    if df is None:
        try:
            ohlcv_1d = exchange_fallback.fetch_ohlcv("BTC/USDT", '1d', limit=50)
            df = pd.DataFrame(ohlcv_1d, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as ekr:
            try:
                df = yf.download("BTC-USD", period="3mo", interval="1d", progress=False)
                df = clean_yf_df(df)
            except Exception as eyf:
                logging.warning(f"[get_btc_status] Veri çekilemedi: {eyf}")
                _set_cached('btc_status', False)
                return False

    try:
        df.ta.ema(length=20, append=True)
        ema_col = 'ema_20' if 'ema_20' in df.columns else 'EMA_20'
        close_col = 'close' if 'close' in df.columns else 'Close'
        if len(df) >= 20:
            last_close = df[close_col].iloc[-1]
            last_ema = df[ema_col].iloc[-1]
            if not pd.isna(last_ema) and last_close > last_ema:
                res = True
    except Exception as e:
        logging.warning(f"[get_btc_status] analiz hatası: {e}")

    _set_cached('btc_status', res)
    return res

def check_btc_not_pumping():
    """BTC aşırı yükselişte (pumping) ise altcoin şortlamayı engeller. (RSI > 70 veya devasa mum)"""
    cached = _get_cached('btc_pumping')
    if cached is not None:
        return cached

    res = True
    df = None
    if not IS_USA_SERVER:
        try:
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", '4h', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            logging.info(f"[check_btc_not_pumping] Binance hatası, yedeklere geçiliyor: {e}")

    if df is None:
        try:
            ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '4h', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as ekr:
            try:
                # 99 yapılmıştır
                # BTC 1H veri periyodu config.DATA_PERIOD_1H ile dinamik hale getirilmiştir.
                df_1h = yf.download("BTC-USD", period=config.DATA_PERIOD_1H, interval="1h", progress=False)
                df_1h = clean_yf_df(df_1h)
                df = df_1h.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
            except Exception as eyf:
                logging.warning(f"[check_btc_not_pumping] Veri çekilemedi: {eyf}")
                _set_cached('btc_pumping', True)
                return True

    try:
        df.ta.rsi(length=14, append=True)
        rsi_col = 'rsi_14' if 'rsi_14' in df.columns else 'RSI_14'
        close_col = 'close' if 'close' in df.columns else 'Close'
        open_col = 'open' if 'open' in df.columns else 'Open'
        if len(df) >= 14:
            last_rsi = df[rsi_col].iloc[-1]
            last_close = df[close_col].iloc[-1]
            last_open = df[open_col].iloc[-1]
            pct_change = ((last_close - last_open) / last_open) * 100

            if last_rsi > 70 or pct_change > 4.0:
                res = False
    except Exception as e:
        logging.warning(f"[check_btc_not_pumping] analiz hatası: {e}")

    _set_cached('btc_pumping', res)
    return res

def check_btc_flash_crash():
    """HB-12 BTC Flash Volatility Guard
    Checks if BTC has experienced extreme volatility recently (e.g. > 3% in last 1H candle or huge ATR spike).
    Returns True if crashed/highly volatile.
    """
    cached = _get_cached('btc_flash_crash')
    if cached is not None:
        return cached

    res = False
    df = None
    if not IS_USA_SERVER:
        try:
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", '1h', limit=14)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            logging.info(f"[check_btc_flash_crash] Binance hatası, yedeklere geçiliyor: {e}")

    if df is None:
        try:
            ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '1h', limit=14)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as ekr:
            try:
                df = yf.download("BTC-USD", period="2d", interval="1h", progress=False)
                df = clean_yf_df(df)
            except Exception as eyf:
                logging.warning(f"[check_btc_flash_crash] Veri çekilemedi: {eyf}")
                _set_cached('btc_flash_crash', False)
                return False

    try:
        if len(df) >= 2:
            last_close = df['close'].iloc[-1]
            last_open = df['open'].iloc[-1]
            prev_close = df['close'].iloc[-2]
            
            candle_pct = 0.0
            if last_open > 0:
                candle_pct = abs((last_close - last_open) / last_open) * 100
                
            drop_pct = 0.0
            if prev_close > 0:
                drop_pct = abs((last_close - prev_close) / prev_close) * 100
            
            if candle_pct > 2.5 or drop_pct > 3.5:
                res = True
                
            if len(df) >= 14:
                df.ta.atr(length=14, append=True)
                atr_col = 'ATRr_14' if 'ATRr_14' in df.columns else df.columns[-1]
                last_atr = df[atr_col].iloc[-1]
                if not pd.isna(last_atr) and last_close > 0:
                    atr_pct = (last_atr / last_close) * 100
                    if atr_pct > 2.0: 
                        res = True
    except Exception as e:
        logging.warning(f"[check_btc_flash_crash] analiz hatası: {e}")

    _set_cached('btc_flash_crash', res)
    return res

def _get_btc_htf_bias():
    """BTC'nin 1 Günlük HTF Bias'ını kontrol et (altcoin taraması için 1 kez çağrılır)."""
    cached = _get_cached('btc_htf_bias')
    if cached is not None:
        return cached

    # Lazy import to avoid circular dependency
    from indicators import sniper_get_htf_bias

    res = 0
    df = None
    if not IS_USA_SERVER:
        try:
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", '1d', limit=60)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as e:
            logging.info(f"[_get_btc_htf_bias] Binance hatası, yedeklere geçiliyor: {e}")

    if df is None:
        try:
            ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '1d', limit=60)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception as ekr:
            try:
                df = yf.download("BTC-USD", period="3mo", interval="1d", progress=False)
                df = clean_yf_df(df)
            except Exception as eyf:
                logging.warning(f"[_get_btc_htf_bias] Veri çekilemedi: {eyf}")
                _set_cached('btc_htf_bias', 0)
                return 0

    try:
        res = sniper_get_htf_bias(df)
    except Exception as e:
        logging.warning(f"[_get_btc_htf_bias] analiz hatası: {e}")

    _set_cached('btc_htf_bias', res)
    return res

def get_btc_rsi_and_change():
    """Returns (btc_rsi_14, btc_24h_change) for BTC/USDT daily."""
    cached = _get_cached('btc_rsi_and_change')
    if cached is not None:
        return cached

    df = None
    if not IS_USA_SERVER:
        try:
            ohlcv = exchange.fetch_ohlcv("BTC/USDT", '1d', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception:
            pass

    if df is None:
        try:
            ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '1d', limit=50)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        except Exception:
            try:
                df = yf.download("BTC-USD", period="3mo", interval="1d", progress=False)
                df = clean_yf_df(df)
            except Exception:
                return 50.0, 0.0

    try:
        df.ta.rsi(length=14, append=True)
        rsi_col = 'rsi_14' if 'rsi_14' in df.columns else 'RSI_14'
        close_col = 'close' if 'close' in df.columns else 'Close'
        
        last_rsi = df[rsi_col].iloc[-1]
        last_close = df[close_col].iloc[-1]
        prev_close = df[close_col].iloc[-2]
        
        change_24h = ((last_close - prev_close) / prev_close) * 100.0
        res = (float(last_rsi), float(change_24h))
    except Exception:
        res = (50.0, 0.0)

    _set_cached('btc_rsi_and_change', res)
    return res



def _check_dxy_shield():
    """DXY (Dolar Endeksi) yükseliş trendinde mi? 5dk cache ile kontrol eder."""
    cached = _get_cached('dxy_shield')
    if cached is not None:
        return cached
    try:
        # 99 yapılmıştır
        # DXY veri periyodu config.DATA_PERIOD_1D ile dinamik hale getirilmiştir.
        df = yf.download("DX-Y.NYB", period=config.DATA_PERIOD_1D, interval="1d", progress=False)
        df = clean_yf_df(df)
        if df.empty:
            return False
        df.ta.ema(length=50, append=True)
        last = df.iloc[-1]
        ema50 = last.get('EMA_50')
        if ema50 is None or pd.isna(ema50):
            return False
        dxy_bullish = bool(last['close'] > ema50)
        _set_cached('dxy_shield', dxy_bullish)
        return dxy_bullish
    except Exception as e:
        logging.warning(f"[_check_dxy_shield] {e}")
        return False

def _is_btc_bullish_for_shorts():
    """BTC 4H'da EMA20 üstünde + hacimli mi? True ise TÜM altcoin SHORT'lar engellenir."""
    cached = _get_cached('btc_short_bias')
    if cached is not None:
        return cached

    result = False
    try:
        df = None
        if not IS_USA_SERVER:
            try:
                ohlcv = exchange.fetch_ohlcv("BTC/USDT", '4h', limit=50)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            except Exception as e_bin:
                logging.warning(f"[_is_btc_bullish_for_shorts] Binance hatası: {e_bin}")
                ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '4h', limit=50)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        else:
            try:
                ohlcv = exchange_fallback.fetch_ohlcv("BTC/USDT", '4h', limit=50)
                df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            except Exception as e_kr:
                logging.warning(f"[_is_btc_bullish_for_shorts] Kraken hatası: {e_kr}")
                # 99 yapılmıştır
                # BTC 1H yedek veri periyodu config.DATA_PERIOD_1H ile dinamik hale getirilmiştir.
                df_1h = yf.download("BTC-USD", period=config.DATA_PERIOD_1H, interval="1h", progress=False)
                df_1h = clean_yf_df(df_1h)
                df = df_1h.resample('4h').agg({
                    'open': 'first', 'high': 'max', 'low': 'min',
                    'close': 'last', 'volume': 'sum'
                }).dropna()

        if len(df) >= 25:
            df.ta.ema(length=20, append=True)
            df['vol_sma_20'] = df['volume'].rolling(20).mean()
            last = df.iloc[-1]
            ema20 = last.get('EMA_20')
            vol_sma = last.get('vol_sma_20')
            if ema20 is not None and not pd.isna(ema20):
                price_above_ema = float(last['close']) > float(ema20)
                volume_strong = True
                if vol_sma is not None and not pd.isna(vol_sma):
                    volume_strong = float(last['volume']) > float(vol_sma) * 0.8
                result = price_above_ema and volume_strong
    except Exception as e:
        logging.warning(f"[_is_btc_bullish_for_shorts] {e}")

    _set_cached('btc_short_bias', result)
    return result

def _get_xu100_daily_data():
    """XU100 endeksinin günlük verisini çeker (RS stratejisi için, 5dk cache)."""
    cached = _get_cached('xu100_daily')
    if cached is not None:
        return cached
    try:
        # 99 yapılmıştır
        # XU100 endeks veri periyodu config.DATA_PERIOD_1D ile dinamik hale getirilmiştir.
        df = yf.download("XU100.IS", period=config.DATA_PERIOD_1D, interval="1d", progress=False)
        df = clean_yf_df(df)
        if not df.empty:
            _set_cached('xu100_daily', df)
            return df
    except Exception as e:
        logging.warning(f"[_get_xu100_daily_data] {e}")
    # Return stale cache if available
    with _cache_lock:
        entry = _cache.get('xu100_daily')
        return entry.value if entry else None


# ════════════════════════════════════════
# Anlık Fiyat Çekme
# ════════════════════════════════════════
# K-08: Son bilinen fiyatlar — Price Sanity Band için referans
_last_known_prices: dict[str, float] = {}

def _check_price_sanity(ticker: str, price: float) -> float:
    """
    K-08: Price Sanity Band — %50'den fazla sapma tespit edilirse eski fiyat korunur.
    """
    if ticker in _last_known_prices:
        prev = _last_known_prices[ticker]
        if prev > 0:
            change_pct = abs(price - prev) / prev
            if change_pct > 0.50:
                logging.warning(
                    f'[Price Sanity] {ticker}: Fiyat %{change_pct*100:.1f} sapma! '
                    f'{prev:.4f} → {price:.4f} — ESKİ FİYAT KORUNUYOR'
                )
                return prev
    _last_known_prices[ticker] = price
    return price


def _fetch_yf_prices(tickers: list[str], prices: dict[str, float]) -> None:
    """
    yfinance kullanarak BIST ve Emtia fiyatlarını çeker.
    """
    for ticker in tickers:
        try:
            t_obj = yf.Ticker(ticker)
            try:
                last_price = t_obj.fast_info.last_price
            except Exception:
                hist = t_obj.history(period="1d")
                last_price = hist['Close'].iloc[-1] if not hist.empty else None

            if last_price is not None:
                prices[ticker] = _check_price_sanity(ticker, float(last_price))
        except Exception as e:
            logging.warning(f"[get_current_prices] BIST/Emtia {ticker}: {e}")


def _fetch_crypto_prices(tickers: list[str], prices: dict[str, float]) -> None:
    """
    Crypto fiyatlarını Binance (varsayılan), Kraken (yedek) veya yfinance (son çare) ile çeker.
    """
    # 1. Binance toplu fiyat çekimi
    if not IS_USA_SERVER:
        try:
            tickers_data = exchange.fetch_tickers(tickers)
            for t in tickers:
                if t in tickers_data and 'last' in tickers_data[t] and tickers_data[t]['last']:
                    prices[t] = _check_price_sanity(t, float(tickers_data[t]['last']))
        except Exception as e:
            logging.info(f"[get_current_prices] Binance toplu fiyat hatası: {e}")

    # Eksik kalan kripto ticker'lar için yedeklere (Kraken veya yfinance) geç
    missing_crypto = [t for t in tickers if t not in prices]
    if not missing_crypto:
        return

    # 2. Kraken yedek fiyat çekimi
    try:
        tickers_data = exchange_fallback.fetch_tickers()
        for t in missing_crypto:
            _price = None
            if t in tickers_data and 'last' in tickers_data[t] and tickers_data[t]['last']:
                _price = float(tickers_data[t]['last'])
            else:
                usd_t = t.replace("/USDT", "/USD")
                if usd_t in tickers_data and 'last' in tickers_data[usd_t] and tickers_data[usd_t]['last']:
                    _price = float(tickers_data[usd_t]['last'])
            
            if _price is not None:
                prices[t] = _check_price_sanity(t, _price)
    except Exception as ekr:
        logging.warning(f"[get_current_prices] Kraken fiyat hatası: {ekr}, yfinance deneniyor...")

    # Son kalan eksik kriptolar için 3. yfinance yedek fiyat çekimi
    still_missing = [t for t in missing_crypto if t not in prices]
    for t in still_missing:
        try:
            yf_ticker = get_yf_crypto_ticker(t)
            t_obj = yf.Ticker(yf_ticker)
            try:
                last_price = t_obj.fast_info.last_price
            except Exception:
                hist = t_obj.history(period="1d")
                last_price = hist['Close'].iloc[-1] if not hist.empty else None
            
            if last_price is not None:
                prices[t] = _check_price_sanity(t, float(last_price))
        except Exception as eyf:
            logging.warning(f"[get_current_prices] Kripto {t} yfinance hatası: {eyf}")


def get_current_prices(tickers):
    """
    Verilen ticker listesi için anlık fiyatları çeker.
    BIST, KRİPTO ve EMTİA karışık olabilir.
    K-08: Price Sanity Band — %50'den fazla sapma tespit edilirse eski fiyat korunur.
    """
    prices = {}
    crypto_tickers = [t for t in tickers if "/" in t]
    bist_tickers = [t for t in tickers if ".IS" in t]
    emtia_tickers = [t for t in tickers if "=F" in t or "=X" in t]

    # BIST + EMTİA Fiyatlarını Çek (yfinance)
    yf_tickers = bist_tickers + emtia_tickers
    if yf_tickers:
        _fetch_yf_prices(yf_tickers, prices)

    # KRIPTO Fiyatlarını Çek
    if crypto_tickers:
        _fetch_crypto_prices(crypto_tickers, prices)

    return prices



def get_crypto_1h_data(symbol):
    """SMC LTF MSB onayı için 1H verisini çeker."""
    limit = OHLCV_LIMIT
    if not IS_USA_SERVER:
        try:
            ohlcv_1h = exchange.fetch_ohlcv(symbol, '1h', limit=limit)
            df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
            df_1h.set_index('timestamp', inplace=True)
            df_1h = guard_dataframe(df_1h, symbol, "1h")
            if df_1h is not None:
                return df_1h
        except Exception as e:
            logging.info(f"[get_crypto_1h_data] Binance hatası, yedeklere geçiliyor: {e}")

    try:
        try:
            ohlcv_1h = exchange_fallback.fetch_ohlcv(symbol, '1h', limit=limit)
        except Exception:
            usd_sym = symbol.replace("/USDT", "/USD")
            ohlcv_1h = exchange_fallback.fetch_ohlcv(usd_sym, '1h', limit=limit)

        df_1h = pd.DataFrame(ohlcv_1h, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_1h['timestamp'] = pd.to_datetime(df_1h['timestamp'], unit='ms')
        df_1h.set_index('timestamp', inplace=True)
        df_1h = guard_dataframe(df_1h, symbol, '1h')
        return df_1h
    except Exception as ekr:
        try:
            yf_ticker = get_yf_crypto_ticker(symbol)
            # 99 yapılmıştır
            # Kripto 1H yedek veri çekim periyodu config.DATA_PERIOD_1H ile dinamik hale getirilmiştir.
            df_1h = yf.download(yf_ticker, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
            df_1h = clean_yf_df(df_1h)
            df_1h = guard_dataframe(df_1h, symbol, '1h')
            return df_1h
        except Exception as eyf:
            logging.warning(f"[get_crypto_1h_data] {symbol} 1H verisi çekilemedi: {eyf}")
            return None

def get_crypto_15m_data(symbol):
    """SMC LTF onayı ve Sniper stratejisi için 15m verisini çeker."""
    limit = OHLCV_LIMIT
    if not IS_USA_SERVER:
        try:
            ohlcv_15m = exchange.fetch_ohlcv(symbol, '15m', limit=limit)
            df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
            df_15m.set_index('timestamp', inplace=True)
            df_15m = guard_dataframe(df_15m, symbol, "15m")
            if df_15m is not None:
                return df_15m
        except Exception as e:
            logging.info(f"[get_crypto_15m_data] Binance hatası, yedeklere geçiliyor: {e}")

    try:
        try:
            ohlcv_15m = exchange_fallback.fetch_ohlcv(symbol, '15m', limit=limit)
        except Exception:
            usd_sym = symbol.replace("/USDT", "/USD")
            ohlcv_15m = exchange_fallback.fetch_ohlcv(usd_sym, '15m', limit=limit)

        df_15m = pd.DataFrame(ohlcv_15m, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        df_15m['timestamp'] = pd.to_datetime(df_15m['timestamp'], unit='ms')
        df_15m.set_index('timestamp', inplace=True)
        df_15m = guard_dataframe(df_15m, symbol, '15m')
        return df_15m
    except Exception as ekr:
        try:
            yf_ticker = get_yf_crypto_ticker(symbol)
            df_15m = yf.download(yf_ticker, period=config.DATA_PERIOD_15M, interval="15m", progress=False)
            df_15m = clean_yf_df(df_15m)
            df_15m = guard_dataframe(df_15m, symbol, '15m')
            return df_15m
        except Exception as eyf:
            logging.warning(f"[get_crypto_15m_data] {symbol} 15m verisi çekilemedi: {eyf}")
            return None


# 99 yapılmıştır
# Emtia 1H veri periyodu config.DATA_PERIOD_1H parametresine bağlanmıştır.
def get_emtia_1h_data(symbol):
    """SMC LTF MSB onayı için 1H emtia verisini yfinance üzerinden çeker ve doğrular."""
    try:
        df_1h = yf.download(symbol, period=config.DATA_PERIOD_1H, interval="1h", progress=False)
        df_1h = clean_yf_df(df_1h)
        df_1h = guard_dataframe(df_1h, symbol, '1h')
        return df_1h
    except Exception as e:
        logging.warning(f"[get_emtia_1h_data] {symbol} 1H verisi çekilemedi: {e}")
        return None


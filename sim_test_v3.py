
import pandas as pd
import pandas_ta as ta
import yfinance as yf
import ccxt
import json
from datetime import datetime, timedelta
import warnings

# pandas hatalarını gizle
warnings.filterwarnings("ignore")

import sys
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# ══════════════════════════════════════════════════════════════════
# RENK KODLARI (X-Ray Loglaması için)
# ══════════════════════════════════════════════════════════════════
class Colors:
    HEADER = '\033[95m'
    BLUE = '\033[94m'
    CYAN = '\033[96m'
    GREEN = '\033[92m'
    WARNING = '\033[93m'
    FAIL = '\033[91m'
    ENDC = '\033[0m'
    BOLD = '\033[1m'

# ══════════════════════════════════════════════════════════════════
# SİMÜLASYON AYARLARI & İZOLASYON
# ══════════════════════════════════════════════════════════════════
TEST_ACTIVE_TRADES_FILE = "test_active_trades.json"
TEST_TRADE_HISTORY_FILE = "test_trade_history.json"

# Sadece hızlı test için BIST ve Kripto'dan popüler semboller
TEST_TICKERS_BIST = ["THYAO.IS", "TUPRS.IS", "KCHOL.IS", "AKBNK.IS", "EREGL.IS"]
TEST_TICKERS_CRYPTO = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "AVAX/USDT", "XRP/USDT"]

# Zaman Makinesi: Sadece Son 7 Gün
END_DATE = datetime.now()
START_DATE = END_DATE - timedelta(days=7)

# Analiz İstatistikleri
stats = {
    "total_bars_scanned": 0,
    "rejected_gate_1_regime": 0,
    "rejected_gate_2_mtf": 0,
    "rejected_gate_3_macro": 0,
    "rejected_gate_4_volume": 0,
    "trades_taken": 0,
    "trades_won_tp": 0,
    "trades_lost_sl": 0
}

open_trades = {}
trade_history = []

def save_test_state():
    with open(TEST_ACTIVE_TRADES_FILE, "w") as f:
        json.dump(open_trades, f, indent=4)
    with open(TEST_TRADE_HISTORY_FILE, "w") as f:
        json.dump(trade_history, f, indent=4)

# ══════════════════════════════════════════════════════════════════
# VERİ ÇEKME (ZAMAN MAKİNESİ) - yfinance ve ccxt
# ══════════════════════════════════════════════════════════════════
def fetch_historical_data_test(symbol, market="BIST"):
    """
    Belirtilen sembol için son 10 günlük (3 gün buffer + 7 gün test) 1 Saatlik (1h) veriyi çeker.
    """
    df = pd.DataFrame()
    start_str = (START_DATE - timedelta(days=3)).strftime('%Y-%m-%d')
    end_str = END_DATE.strftime('%Y-%m-%d')
    
    try:
        if market == "BIST":
            df = yf.download(symbol, start=start_str, end=end_str, interval="1h", progress=False)
            if not df.empty:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.droplevel(1)
                df.columns = [c.lower() for c in df.columns]
                
        elif market == "CRYPTO":
            exchange = ccxt.binance({'enableRateLimit': True})
            # 1H verisi al (son 10 gün = 240 mum civarı)
            since = exchange.parse8601(start_str + "T00:00:00Z")
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe='1h', since=since, limit=300)
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.set_index('timestamp', inplace=True)
            
    except Exception as e:
        print(f"{Colors.FAIL}[HATA] {symbol} veri çekilemedi: {e}{Colors.ENDC}")
        
    return df

# ══════════════════════════════════════════════════════════════════
# 4 KATMANLI FİLTRE & İZLEYEN STOP
# ══════════════════════════════════════════════════════════════════
def calculate_indicators_test(df):
    """Dataframe üzerine simülasyon için gereken indikatörleri hesaplar."""
    if len(df) < 50: return df
    
    # Gate 1: Market Regime (SMA200) -> 1h grafikte trendi anlamak için 100 period
    df.ta.sma(length=100, append=True)
    
    # Gate 2: MTF Momentum (MACD)
    df.ta.macd(fast=12, slow=26, signal=9, append=True)
    
    # Gate 4: Volume (Hacim Ortalaması)
    df['vol_sma_20'] = ta.sma(df['volume'], length=20)
    
    # Stop hesaplaması için ATR
    df.ta.atr(length=14, append=True)
    
    return df

def simulate_market_test(market, tickers):
    """Piyasayı bar bar geçmişe dönük tarar."""
    print(f"\n{Colors.HEADER}>>> {market} SİMÜLASYONU BAŞLIYOR (Son 7 Gün) <<<{Colors.ENDC}")
    
    # Macro Gravity (Gate 3) için referans (BIST: XU100, Kripto: BTC)
    macro_symbol = "XU100.IS" if market == "BIST" else "BTC/USDT"
    macro_df = fetch_historical_data_test(macro_symbol, market)
    if not macro_df.empty:
        macro_df.ta.sma(length=50, append=True)
        
    for symbol in tickers:
        df = fetch_historical_data_test(symbol, market)
        df = calculate_indicators_test(df)
        
        if df.empty or len(df) < 100:
            continue
            
        # Sadece son 7 günlük test alanını al (Buffer'ı at)
        test_df = df[df.index >= START_DATE]
        
        print(f"{Colors.CYAN}[TARAMA] {symbol} | Test Mum Sayısı: {len(test_df)}{Colors.ENDC}")
        
        for i in range(len(test_df)):
            stats["total_bars_scanned"] += 1
            current_bar = test_df.iloc[i]
            timestamp = test_df.index[i]
            current_price = current_bar['close']
            
            # ══ YÖNETİM: AÇIK İŞLEMLERİN KONTROLÜ (İzleyen Stop / TP) ══
            if symbol in open_trades:
                trade = open_trades[symbol]
                # TP Vurma Kontrolü
                if current_bar['high'] >= trade['tp']:
                    print(f"{Colors.GREEN}[TEST-KÂR] {timestamp} | {symbol} Hedefe (TP) Ulaştı! R:R Kârı Alındı.{Colors.ENDC}")
                    stats["trades_won_tp"] += 1
                    trade_history.append({"symbol": symbol, "result": "TP", "entry": trade['entry'], "exit": trade['tp']})
                    del open_trades[symbol]
                    continue
                    
                # SL Vurma Kontrolü
                if current_bar['low'] <= trade['sl']:
                    print(f"{Colors.FAIL}[TEST-ZARAR] {timestamp} | {symbol} Stop-Loss (SL) Patladı!{Colors.ENDC}")
                    stats["trades_lost_sl"] += 1
                    trade_history.append({"symbol": symbol, "result": "SL", "entry": trade['entry'], "exit": trade['sl']})
                    del open_trades[symbol]
                    continue
                    
                # İzleyen Stop (Fiyat %3 arttıysa Stop'u girişe çek)
                if current_price >= trade['entry'] * 1.03 and trade['sl'] < trade['entry']:
                    trade['sl'] = trade['entry'] * 1.005 # Break-even + ufak komisyon
                    print(f"{Colors.BLUE}[TEST-STOP-GÜNCELLEME] {timestamp} | {symbol} fiyatı yükseldi, SL maliyete çekildi.{Colors.ENDC}")
                continue # Açık işlem varken yeni sinyal arama
            
            # ══ STRATEJİ: DİPTEN DÖNÜŞ (Örnek Basit Sinyal Üretici) ══
            # Sinyal Koşulu: Fiyat son 5 mumun en düşüğünde ama yeşil mum kapattı
            last_5_low = df['low'].iloc[:df.index.get_loc(timestamp)].tail(5).min()
            if current_price > current_bar['open'] and current_bar['low'] <= last_5_low:
                # SİNYAL BULUNDU! Şimdi 4 Kapılı Röntgen Kontrolü:
                
                # Kapı 1: Market Regime (Fiyat SMA100'ün üzerinde mi?)
                sma100 = current_bar.get('SMA_100', 0)
                if current_price < sma100:
                    print(f"{Colors.WARNING}[TEST-LOG] {timestamp} | {symbol} Dipten Dönüş Sinyali Üretti AMA 1. Kapı (Piyasa Rejimi SMA100) Ayı ({current_price:.2f} < {sma100:.2f}) olduğu için SİNYAL REDDEDİLDİ.{Colors.ENDC}")
                    stats["rejected_gate_1_regime"] += 1
                    continue
                
                # Kapı 2: MTF Momentum (MACD Histogram Pozitif mi?)
                macd_hist = current_bar.get('MACDh_12_26_9', 0)
                if pd.isna(macd_hist) or macd_hist <= 0:
                    print(f"{Colors.WARNING}[TEST-LOG] {timestamp} | {symbol} Dipten Dönüş Sinyali Üretti AMA 2. Kapı (MTF Momentum - MACD) Negatif olduğu için SİNYAL REDDEDİLDİ.{Colors.ENDC}")
                    stats["rejected_gate_2_mtf"] += 1
                    continue
                    
                # Kapı 3: Macro Gravity (XU100 / BTC SMA50'nin üzerinde mi?)
                macro_ok = True
                if not macro_df.empty:
                    try:
                        macro_bar = macro_df.loc[:timestamp].iloc[-1]
                        if macro_bar['close'] < macro_bar.get('SMA_50', 0):
                            macro_ok = False
                    except:
                        pass
                if not macro_ok:
                    print(f"{Colors.WARNING}[TEST-LOG] {timestamp} | {symbol} Sinyal Üretti AMA 3. Kapı (Macro Gravity - {macro_symbol} SMA50 Altında) sebebiyle REDDEDİLDİ.{Colors.ENDC}")
                    stats["rejected_gate_3_macro"] += 1
                    continue
                    
                # Kapı 4: Volume (Hacim SMA20'nin %150'si mi?)
                vol = current_bar['volume']
                vol_sma = current_bar.get('vol_sma_20', 1)
                if vol < (vol_sma * 1.5):
                    print(f"{Colors.WARNING}[TEST-LOG] {timestamp} | {symbol} Sinyal Üretti AMA 4. Kapı (Hacim Yetersiz: {vol:.0f} < {vol_sma*1.5:.0f}) sebebiyle REDDEDİLDİ.{Colors.ENDC}")
                    stats["rejected_gate_4_volume"] += 1
                    continue
                
                # 4 KAPIYI DA GEÇTİ -> İŞLEME GİR
                atr = current_bar.get('ATRr_14', current_price*0.02)
                if pd.isna(atr): atr = current_price * 0.02
                
                sl = current_price - (atr * 2)
                tp = current_price + (atr * 4) # 1:2 RR Oranı
                
                open_trades[symbol] = {
                    "entry": current_price,
                    "sl": sl,
                    "tp": tp,
                    "timestamp": str(timestamp)
                }
                stats["trades_taken"] += 1
                save_test_state()
                print(f"{Colors.GREEN}{Colors.BOLD}[TEST-GİRİŞ] {timestamp} | {symbol} 4 Kapıyı Geçti! LONG İşlem Açıldı. Giriş: {current_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f}{Colors.ENDC}")

# ══════════════════════════════════════════════════════════════════
# OTOPSİ RAPORU (SUMMARY)
# ══════════════════════════════════════════════════════════════════
def print_autopsy_report():
    print(f"\n{Colors.HEADER}{Colors.BOLD}════════════════════════════════════════════════════════════")
    print(f"📊 SİMÜLASYON OTOPSİ RAPORU (Son 7 Gün Zaman Makinesi)")
    print(f"════════════════════════════════════════════════════════════{Colors.ENDC}")
    print(f"{Colors.CYAN}Tarih Aralığı: {START_DATE.strftime('%Y-%m-%d %H:%M')} -> {END_DATE.strftime('%Y-%m-%d %H:%M')}{Colors.ENDC}")
    print(f"Taranan Toplam Mum (Bar): {stats['total_bars_scanned']:,}")
    print(f"\n{Colors.WARNING}🛑 REDDEDİLEN SAHTE SİNYALLER (FİLTRE PERFORMANSI){Colors.ENDC}")
    print(f"  ├─ 1. Kapı (Piyasa Rejimi)   : {stats['rejected_gate_1_regime']} sinyal engellendi.")
    print(f"  ├─ 2. Kapı (MTF Momentum)    : {stats['rejected_gate_2_mtf']} sinyal engellendi.")
    print(f"  ├─ 3. Kapı (Macro Gravity)   : {stats['rejected_gate_3_macro']} sinyal engellendi.")
    print(f"  └─ 4. Kapı (Hacim Şiddeti)   : {stats['rejected_gate_4_volume']} sinyal engellendi.")
    
    total_rejected = stats['rejected_gate_1_regime'] + stats['rejected_gate_2_mtf'] + stats['rejected_gate_3_macro'] + stats['rejected_gate_4_volume']
    print(f"{Colors.BOLD}Toplam Kurtarılan Kasa (Engellenen Kötü Sinyal): {total_rejected}{Colors.ENDC}")
    
    print(f"\n{Colors.GREEN}✅ ALINAN İŞLEMLER VE SONUÇLAR{Colors.ENDC}")
    print(f"Açılan Toplam İşlem : {stats['trades_taken']}")
    print(f"Başarılı (TP Vuran) : {stats['trades_won_tp']}")
    print(f"Başarısız (SL Vuran): {stats['trades_lost_sl']}")
    win_rate = (stats['trades_won_tp'] / (stats['trades_won_tp'] + stats['trades_lost_sl']) * 100) if (stats['trades_won_tp'] + stats['trades_lost_sl']) > 0 else 0
    print(f"Kazanma Oranı (Win Rate): %{win_rate:.1f}")
    
    if len(open_trades) > 0:
        print(f"\n{Colors.BLUE}Hala Açık Olan İşlemler:{Colors.ENDC}")
        for sym, trade in open_trades.items():
            print(f"  - {sym} | Giriş: {trade['entry']:.2f} | R:R Oranı: 1:2")
            
    print(f"{Colors.HEADER}════════════════════════════════════════════════════════════{Colors.ENDC}\n")

if __name__ == "__main__":
    print(f"{Colors.CYAN}Zaman Makinesi Başlatılıyor... Live ortam korunuyor.{Colors.ENDC}")
    
    # Simülasyonu Çalıştır
    simulate_market_test("BIST", TEST_TICKERS_BIST)
    simulate_market_test("CRYPTO", TEST_TICKERS_CRYPTO)
    
    # Otopsi Raporunu Yazdır
    print_autopsy_report()

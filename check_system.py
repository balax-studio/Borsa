import os
import sys
import json
import logging
import asyncio

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Setup logging to console only for diagnostics
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("system_check")

def check_file(path, name):
    if not os.path.exists(path):
        print(f"❌ {name} dosyası EKSİK: {path}")
        return False
    try:
        with open(path, 'r', encoding='utf-8') as f:
            json.load(f)
        print(f"✅ {name} dosyası OK (Geçerli JSON)")
        return True
    except Exception as e:
        print(f"❌ {name} dosyası HASARLI (Geçersiz JSON): {e}")
        return False

async def main():
    print("==================================================")
    print("🔍 BORSA ADAY SİNYAL SİSTEMİ DİAGNOSTİK KONTROLÜ")
    print("==================================================")

    # 1. Modül İthalat Kontrolleri
    print("\n📦 [1] Modül İthalat Kontrolleri:")
    try:
        import data_guard
        print("✅ data_guard: OK")
        import circuit_breaker
        print("✅ circuit_breaker: OK")
        import penalty_box
        print("✅ penalty_box: OK")
        import strategy_scorecard
        print("✅ strategy_scorecard: OK")
        import trade_tracker
        from trade_tracker import TradeEngine
        print("✅ trade_tracker & TradeEngine: OK")
        from core.notifier import NotificationService
        print("✅ core.notifier & NotificationService: OK")
        from core.scanner import ScannerService
        print("✅ core.scanner & ScannerService: OK")
        from core.scheduler import TaskScheduler
        print("✅ core.scheduler & TaskScheduler: OK")
    except Exception as e:
        print(f"❌ Modül ithalat hatası: {e}")
        sys.exit(1)

    # 2. Çevre Değişkenleri (.env) Kontrolü
    print("\n🔑 [2] Çevre Değişkenleri (.env) Kontrolü:")
    from dotenv import load_dotenv
    load_dotenv()
    
    tokens = ["TELEGRAM_BOT_TOKEN", "TELEGRAM_WATCH_BOT_TOKEN", "TELEGRAM_SYSTEM_BOT_TOKEN"]
    chat_ids = ["TELEGRAM_CHAT_ID", "TELEGRAM_WATCH_CHAT_ID", "TELEGRAM_SYSTEM_CHAT_ID"]
    
    for t in tokens:
        val = os.getenv(t)
        if val:
            print(f"✅ {t}: Tanımlı (***{val[-5:] if len(val) > 5 else ''})")
        else:
            print(f"⚠️ {t}: EKSİK (Console modu aktif)")
            
    for c in chat_ids:
        val = os.getenv(c)
        if val:
            print(f"✅ {c}: Tanımlı ({val})")
        else:
            print(f"⚠️ {c}: EKSİK")

    # 3. Durum (State) Dosyaları Kontrolü
    print("\n💾 [3] Durum (State) Dosyaları Kontrolü:")
    check_file("circuit_breaker_state.json", "Circuit Breaker")
    check_file("penalty_box_state.json", "Penalty Box")
    check_file("active_trades.json", "Aktif İşlemler (active_trades)")

    # 4. İnternet ve Veri Kaynağı (yfinance) Kontrolü
    print("\n🌐 [4] Veri Kaynağı (yfinance) Bağlantı Kontrolü:")
    try:
        import yfinance as yf
        import pandas as pd
        test_ticker = "THYAO.IS"
        df = yf.download(test_ticker, period="1d", interval="1d", progress=False)
        if not df.empty:
            if isinstance(df.columns, pd.MultiIndex):
                df.columns = df.columns.droplevel(1)
            df.columns = [c.lower() for c in df.columns]
            price = float(df['close'].iloc[-1])
            print(f"✅ yfinance bağlantısı başarılı. {test_ticker} son fiyat: {price:.2f}")
        else:
            print("❌ yfinance boş veri döndürdü.")
    except Exception as e:
        print(f"❌ yfinance bağlantı hatası: {e}")

    # 5. Notifier Test (Mock)
    print("\n🔔 [5] Telegram Notifier Durum Kontrolü:")
    try:
        notifier = NotificationService()
        if notifier.bot:
            print("✅ Telegram bot bağlantı objesi oluşturuldu.")
        else:
            print("⚠️ Telegram bot token tanımlı değil, bildirimler gönderilemez.")
    except Exception as e:
        print(f"❌ Notifier başlatma hatası: {e}")

    print("\n==================================================")
    print("🎉 DİAGNOSTİK TAMAMLANDI!")
    print("==================================================")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())

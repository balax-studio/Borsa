import asyncio
import logging
import sys

# Loglamayı terminalde görmek için ayarlayalım
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")


async def run_manual_scan():
    print("==================================================")
    print("🔍 MANUEL TARAMA BAŞLATILIYOR (Telegram bildirimleri kapalı)")
    print("==================================================")
    
    # Sadece tarayıcıyı içe aktarıyoruz
    from strategies.scanner import scan_all_markets
    
    # Taramayı çalıştır
    signals, metrics = await scan_all_markets()
    
    print("\n==================================================")
    print(f"✅ Tarama Tamamlandı! Üretilen Sinyal Sayısı: {len(signals)}")
    print("==================================================")
    for sig in signals:
        print(f"Sinyal: {sig.get('ticker')} | {sig.get('signal')} | Puan: {sig.get('conviction_score')} | Strateji: {sig.get('strategy')}")

if __name__ == "__main__":
    if sys.platform == 'win32':
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(run_manual_scan())

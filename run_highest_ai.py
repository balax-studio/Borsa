import asyncio
import os
import sys
import json
import re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from core.ai_commentary import get_ai_commentary

async def main():
    log_file = "/home/ubuntu/quant_bot/bot.log"
    signals = []
    
    # Read bot.log and extract signals using regex
    # Format might look like: Puan: 85, Sinyal: AL, Ticker: BTCUSDT etc.
    # We will search for 'Yeni Kripto Sinyali' or something similar.
    # Alternatively, we can check active_trades.json
    active_trades_path = "/home/ubuntu/quant_bot/active_trades.json"
    
    try:
        with open(active_trades_path, "r", encoding="utf-8") as f:
            data = json.load(f)
            for trade in data:
                if trade.get("market") == "KRIPTO" or "USDT" in trade.get("ticker", ""):
                    signals.append(trade)
    except Exception as e:
        print(f"Error reading active_trades: {e}")

    if not signals:
        print("Aktif kripto islemi bulunamadi. Son 500 loga bakiliyor...")
        try:
            with open(log_file, "r", encoding="utf-8", errors="ignore") as f:
                lines = f.readlines()[-500:]
                for line in lines:
                    if "Sinyal:" in line and "Puan:" in line:
                        pass
                        # Implement parsing if needed
        except Exception as e:
            import logging
            logging.error(f"Beklenmeyen hata: {e}")

    if not signals:
        # Fallback to a mock high score signal to demonstrate
        signals = [{
            "ticker": "SOLUSDT",
            "signal": "AL",
            "conviction_score": 92,
            "reason": "Agresif Hacim Artisi ve Golden Cross",
            "strategy": "MOMENTUM"
        }]

    # Find the one with highest conviction score
    highest_signal = max(signals, key=lambda x: int(x.get("conviction_score", 0) or 0))
    print(f"Secilen En Yuksek Puanli Sinyal: {highest_signal.get('ticker')} (Puan: {highest_signal.get('conviction_score')})")

    commentary = await get_ai_commentary([highest_signal])
    print("\n--- GROQ AI YORUMU ---")
    print(commentary)

if __name__ == "__main__":
    asyncio.run(main())

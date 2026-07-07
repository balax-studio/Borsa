#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
monitoring.py — Günlük Tutarlılık Kontrol Modülü (V3.4)
═════════════════════════════════════════════════════════
Tüm aktif işlemleri (active_trades.json) tarar ve kurumsal R:R kurallarına (RR_MINIMUM)
uyup uymadıklarını denetler. 

- BIST-1 (Dip Avcılığı) ve BIST-2 (Trend Takibi) stratejileri R:R kurallarından muaftır.
- R:R ihlali tespit edilirse Telegram üzerinden anında bildirim gönderir.
- Cron ile sabah 09:00'da çalıştırılmak üzere tasarlanmıştır.
"""

import os
import sys
import json
import logging
import urllib.request
import urllib.parse
from logging.handlers import RotatingFileHandler
from dotenv import load_dotenv

# Windows üzerinde terminal UTF-8 uyumluluğu için stdout'u yeniden yapılandır
if sys.stdout.encoding != "utf-8":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception as e:
        import logging
        logging.error(f"Beklenmeyen hata: {e}")

# ════ Profesyonel Logging Yapılandırması ════
logger = logging.getLogger("quant_bot_monitor")
logger.setLevel(logging.INFO)

file_handler = RotatingFileHandler(
    "monitoring.log", maxBytes=2*1024*1024, backupCount=2, encoding="utf-8"
)
file_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
logger.addHandler(file_handler)

console_handler = logging.StreamHandler(sys.stdout)
console_handler.setFormatter(logging.Formatter(
    "%(asctime)s [%(levelname)s] %(message)s"
))
logger.addHandler(console_handler)

# ════ Sabitler ve Ayarlar ════
TRACKER_FILE = "active_trades.json"
RR_MINIMUM = 2.0  # config.py'den bağımsız olarak acil durum referansı, yüklenemezse fallback

# config.py'den dinamik RR_MINIMUM yükleme denemesi
try:
    from config import RR_MINIMUM as CONFIG_RR_MINIMUM
    RR_MINIMUM = CONFIG_RR_MINIMUM
    logger.info(f"config.py'den RR_MINIMUM yüklendi: {RR_MINIMUM}")
except Exception as e:
    logger.warning(f"config.py okunamadı, varsayılan RR_MINIMUM={RR_MINIMUM} kullanılacak. Hata: {e}")

# .env yükleme
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_ID_ENV = os.getenv("TELEGRAM_CHAT_ID", "")
CHAT_IDS = [cid.strip() for cid in CHAT_ID_ENV.split(",") if cid.strip()]

def send_telegram_alert(message: str):
    """Urllib kullanarak Telegram'a senkron mesaj gönderir."""
    if not BOT_TOKEN or not CHAT_IDS:
        logger.warning("Telegram Bot Token veya Chat ID bulunamadı. Alert gönderilemiyor.")
        return

    for cid in CHAT_IDS:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        if not url.startswith("https://"):
            logger.error("Güvensiz URL şeması tespit edildi.")
            continue
        
        payload = {
            "chat_id": cid,
            "text": message,
            "parse_mode": "HTML"
        }
        data = urllib.parse.urlencode(payload).encode("utf-8")
        try:
            req = urllib.request.Request(url, data=data, method="POST")
            with urllib.request.urlopen(req, timeout=10) as response:
                res = json.loads(response.read().decode("utf-8"))
                if not res.get("ok"):
                    logger.warning(f"Telegram mesajı {cid} için başarısız oldu: {res}")
        except Exception as ex:
            logger.error(f"Telegram alert gönderilirken hata oluştu ({cid}): {ex}")

def daily_consistency_check() -> bool:
    """Aktif işlemleri tarar ve R:R kurallarına uygunluğunu doğrular."""
    logger.info("Günlük R:R tutarlılık kontrolü başlatılıyor...")

    if not os.path.exists(TRACKER_FILE):
        logger.info(f"Aktif işlem dosyası ({TRACKER_FILE}) bulunamadı. Tarama atlanıyor.")
        return True

    try:
        with open(TRACKER_FILE, 'r', encoding='utf-8') as f:
            trades = json.load(f)
    except Exception as ex:
        logger.error(f"Aktif işlemler JSON dosyası okunamadı: {ex}")
        send_telegram_alert(f"🚨 <b>MONITOR ERROR</b>: {TRACKER_FILE} dosyası okunamıyor!")
        return False

    if not isinstance(trades, list):
        logger.error("Aktif işlem verisi geçersiz formatta (liste olmalı).")
        return False

    active_trades = [t for t in trades if t.get("status") == "ACTIVE"]
    logger.info(f"Toplam aktif işlem sayısı: {len(active_trades)}")

    violations = []
    skipped_bist_1_2 = []

    for t in active_trades:
        ticker = t.get("ticker", "UNKNOWN")
        entry = t.get("entry_price")
        sl = t.get("sl")
        tp = t.get("tp")
        strategy = t.get("strategy", "")
        reason = t.get("reason", "")
        direction = t.get("signal", "AL")

        # Eksik veri kontrolü
        if entry is None or sl is None or tp is None:
            logger.warning(f"Eksik parametreler barındıran işlem atlanıyor: {ticker}")
            continue

        # R:R hesaplama
        if direction == "AL":
            risk = entry - sl
            reward = tp - entry
        else:
            risk = sl - entry
            reward = entry - tp

        rr = reward / risk if risk > 0 else 0

        # BIST 1 ve BIST 2 için özel muafiyet (Bypass)
        # Not: Eski işlemler için reason alanını da kontrol ediyoruz.
        is_bist1 = "BIST 1:" in strategy or "BIST 1" in reason
        is_bist2 = "BIST 2:" in strategy or "BIST 2" in reason or "EMA13 pullback" in reason or "EMA21 pullback" in reason

        if is_bist1 or is_bist2:
            strategy_name = strategy or ("BIST 1: DİP AVCILIĞI" if is_bist1 else "BIST 2: TREND TAKİBİ")
            skipped_bist_1_2.append((ticker, strategy_name, rr))
            logger.info(f"[BYPASS] {ticker} ({strategy_name}) R:R={rr:.2f} (BIST-1/2 Muafiyetiyle Izin Verildi)")
            continue

        # R:R Kural İhlal Kontrolü
        if rr < RR_MINIMUM:
            violations.append({
                "ticker": ticker,
                "strategy": strategy or "Bilinmeyen Strateji",
                "entry": entry,
                "sl": sl,
                "tp": tp,
                "rr": rr,
                "min_required": RR_MINIMUM
            })
            logger.warning(f"[FAIL] IHLAL: {ticker} ({strategy}) R:R={rr:.2f} < {RR_MINIMUM}")
        else:
            logger.info(f"[PASS] UYUMLU: {ticker} ({strategy}) R:R={rr:.2f} >= {RR_MINIMUM}")

    # Raporlama ve Bildirimler
    if violations:
        alert_msg = f"🚨 <b>MONİTÖR ALARMI: R:R Kural İhlali!</b>\n\n"
        alert_msg += f"Toplam <b>{len(violations)}</b> adet aktif işlemde minimum R:R ({RR_MINIMUM}:1) kuralı ihlal edilmiştir:\n\n"
        for v in violations:
            alert_msg += (
                f"• <b>{v['ticker']}</b> ({v['strategy']})\n"
                f"  R:R: <code>{v['rr']:.2f}</code> &lt; <code>{v['min_required']:.2f}</code>\n"
                f"  Giriş: <code>{v['entry']:.2f}</code> | SL: <code>{v['sl']:.2f}</code> | TP: <code>{v['tp']:.2f}</code>\n\n"
            )
        alert_msg += "⚠️ Lütfen bu işlemlerin SL/TP değerlerini kontrol ediniz."
        send_telegram_alert(alert_msg)
        return False
    else:
        logger.info("[OK] Basarili: Tum islemler R:R kurallarina tam uyumlu.")
        return True

if __name__ == "__main__":
    success = daily_consistency_check()
    sys.exit(0 if success else 1)

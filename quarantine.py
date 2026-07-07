"""
quarantine.py — Karantina Protokolü (V3.2 Kaos Çözümü #2)
Siyah Kuğu ve veri kesilmesi senaryolarında açık pozisyonları korur.
Stale/zombie işlemleri tespit eder ve otomatik karantinaya alır.
"""
import logging
import threading
from datetime import datetime, timezone
from typing import Any, Dict
from core.defensive_engine import DefensiveStateGuard
import config

QUARANTINE_STATE_FILE = config.QUARANTINE_STATE_FILE
_quarantine_lock = threading.Lock()

# Karantina eşikleri (Config.py SSOT eşleşmesi)
STALE_DATA_THRESHOLD_SEC = getattr(config, 'QUARANTINE_STALE_SECONDS', 1800)
AUTO_CLOSE_THRESHOLD_HOURS = getattr(config, 'QUARANTINE_AUTO_CLOSE_HOURS', 72)
ZOMBIE_CHECK_INTERVAL_SEC = 900    # Her 15 dk'da bir zombie kontrolü


def _load_state_unlocked() -> Dict[str, Any]:
    """Kilit olmadan karantina durumunu dosyadan yükle (dahili kullanım)."""
    def _default_quarantine() -> Dict[str, Any]:
        return {"quarantined": {}, "stale_alerts": {}, "stats": {"total_quarantines": 0}}
    
    return DefensiveStateGuard.load_state_safe(QUARANTINE_STATE_FILE, _default_quarantine)


def _save_state_unlocked(state: Dict[str, Any]) -> None:
    """Kilit olmadan atomik yazma ile durumu kaydet (dahili kullanım)."""
    success = DefensiveStateGuard.save_state_atomic(QUARANTINE_STATE_FILE, state)
    if not success:
        logging.error("[Quarantine] State kaydedilemedi (DefensiveStateGuard başarısız).")


def _load_state() -> dict:
    """Karantina durumunu dosyadan yükle (kilitli)."""
    with _quarantine_lock:
        return _load_state_unlocked()


def _save_state(state: dict):
    """Karantina durumunu dosyaya kaydet (kilitli)."""
    with _quarantine_lock:
        _save_state_unlocked(state)


def check_staleness(trade: dict, last_price_update: str = None) -> dict | None:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return None

    """
    Bir işlem için veri bayatlığını kontrol eder.
    
    Args:
        trade: İşlem verisi (active_trades.json'dan)
        last_price_update: Son fiyat güncellemesinin ISO zamanı
    
    Returns:
        Karantina raporu dict'i veya None (sorun yoksa)
    """
    ticker = trade.get("ticker", "UNKNOWN")
    now = datetime.now(timezone.utc)
    
    # Son fiyat güncelleme zamanını kontrol et
    if last_price_update:
        try:
            last_update = datetime.fromisoformat(last_price_update)
            if last_update.tzinfo is None:
                last_update = last_update.replace(tzinfo=timezone.utc)
            
            age_seconds = (now - last_update).total_seconds()
            
            if age_seconds > STALE_DATA_THRESHOLD_SEC:
                report = {
                    "ticker": ticker,
                    "reason": "STALE_DATA",
                    "data_age_minutes": round(age_seconds / 60, 1),
                    "last_update": last_price_update,
                    "quarantine_time": now.isoformat(),
                    "action": "FREEZE_SL_TP"
                }
                _add_to_quarantine(ticker, report)
                return report
        except Exception as e:
            logging.warning(f"[Quarantine] Zaman ayrıştırma hatası ({ticker}): {e}")
    
    # İşlem yaşını kontrol et (zombie tespiti)
    entry_time_str = trade.get("entry_time") or trade.get("signal_time")
    if entry_time_str:
        try:
            entry_time = datetime.fromisoformat(entry_time_str)
            if entry_time.tzinfo is None:
                entry_time = entry_time.replace(tzinfo=timezone.utc)
            
            trade_age_hours = (now - entry_time).total_seconds() / 3600
            
            if trade_age_hours > AUTO_CLOSE_THRESHOLD_HOURS:
                report = {
                    "ticker": ticker,
                    "reason": "ZOMBIE_TRADE",
                    "trade_age_hours": round(trade_age_hours, 1),
                    "entry_time": entry_time_str,
                    "quarantine_time": now.isoformat(),
                    "action": "RECOMMEND_CLOSE"
                }
                _add_to_quarantine(ticker, report)
                return report
        except Exception as e:
            logging.warning(f"[Quarantine] İşlem yaşı hesaplama hatası ({ticker}): {e}")
    
    return None


def _add_to_quarantine(ticker: str, report: dict):
    """Varlığı karantinaya ekle."""
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _quarantine_lock:
        state = _load_state_unlocked()
        quarantined = state.get("quarantined", {})
        
        # Önceden karantinada değilse ekle
        if ticker not in quarantined:
            state["stats"]["total_quarantines"] = state["stats"].get("total_quarantines", 0) + 1
        
        quarantined[ticker] = report
        state["quarantined"] = quarantined
        _save_state_unlocked(state)
    logging.warning(f"[Quarantine] 🔒 {ticker} karantinaya alındı: {report['reason']}")


def remove_from_quarantine(ticker: str) -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return False
    """Varlığı karantinadan çıkar (veri düzeldiğinde)."""
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _quarantine_lock:
        state = _load_state_unlocked()
        quarantined = state.get("quarantined", {})
        
        if ticker in quarantined:
            del quarantined[ticker]
            state["quarantined"] = quarantined
            _save_state_unlocked(state)
            logging.info(f"[Quarantine] 🔓 {ticker} karantinadan çıkarıldı.")
            return True
        return False


def is_quarantined(ticker: str) -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return False
    """Varlık karantinada mı?"""
    state = _load_state()
    return ticker in state.get("quarantined", {})


def get_quarantine_report(ticker: str) -> dict | None:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return None
    """Belirli bir varlığın karantina raporunu döner."""
    state = _load_state()
    return state.get("quarantined", {}).get(ticker)


def generate_quarantine_alert(report: dict) -> str:
    """Karantina raporu için Telegram bildirim mesajı üretir."""
    reason_emoji = {
        "STALE_DATA": "📡",
        "ZOMBIE_TRADE": "🧟",
        "EXCHANGE_DOWN": "🔌",
        "CIRCUIT_BREAKER": "⚡",
    }
    
    reason = report.get("reason", "UNKNOWN")
    emoji = reason_emoji.get(reason, "🔒")
    ticker = report.get("ticker", "?")
    
    if reason == "STALE_DATA":
        msg = (
            f"{emoji} <b>KARANTİNA — VERİ BAYATLADI</b>\n"
            f"Varlık: <code>{ticker}</code>\n"
            f"Son güncelleme: <b>{report.get('data_age_minutes', '?')} dk</b> önce\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"⛔ SL/TP güncellenmesi DURDURULDU.\n"
            f"📌 Mevcut SL/TP değerleri korunuyor.\n"
            f"⚠️ Manuel müdahale gerekebilir."
        )
    elif reason == "ZOMBIE_TRADE":
        msg = (
            f"{emoji} <b>KARANTİNA — ZOMBİ İŞLEM</b>\n"
            f"Varlık: <code>{ticker}</code>\n"
            f"İşlem yaşı: <b>{report.get('trade_age_hours', '?')} saat</b>\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🔴 Bu işlem çok uzun süredir açık.\n"
            f"📌 Önerilen: Piyasa fiyatından kapatın.\n"
            f"⚠️ Strateji bu varlıktan çıkış sinyali üretmemiş."
        )
    else:
        msg = (
            f"{emoji} <b>KARANTİNA — {reason}</b>\n"
            f"Varlık: <code>{ticker}</code>\n"
            f"Zaman: <b>{report.get('quarantine_time', '?')}</b>"
        )
    
    return msg


def check_exchange_health(market: str, tickers: list, last_prices: dict) -> list:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return []
    """
    Borsa sağlık kontrolü. Birden fazla varlıkta eşzamanlı veri kaybı varsa
    borsanın kendisi sorunlu olabilir.
    
    Args:
        market: "BIST" veya "CRYPTO"
        tickers: İzlenen semboller listesi
        last_prices: {ticker: {"price": float, "time": str}} dict'i
    
    Returns:
        Karantina raporlarının listesi
    """
    stale_count = 0
    total = len(tickers) if tickers else 1
    reports = []
    now = datetime.now(timezone.utc)
    
    for ticker in (tickers or []):
        info = last_prices.get(ticker, {})
        last_time = info.get("time")
        
        if last_time:
            try:
                lt = datetime.fromisoformat(last_time)
                if lt.tzinfo is None:
                    lt = lt.replace(tzinfo=timezone.utc)
                age = (now - lt).total_seconds()
                if age > STALE_DATA_THRESHOLD_SEC:
                    stale_count += 1
            except Exception:
                stale_count += 1
        else:
            stale_count += 1
    
    # %50'den fazlası bayatsa → borsa seviyesinde sorun
    stale_ratio = stale_count / total if total > 0 else 0
    if stale_ratio > 0.5:
        report = {
            "ticker": f"EXCHANGE_{market}",
            "reason": "EXCHANGE_DOWN",
            "stale_ratio": round(stale_ratio, 2),
            "stale_count": stale_count,
            "total_tickers": total,
            "quarantine_time": now.isoformat(),
            "action": "FREEZE_ALL_SIGNALS"
        }
        reports.append(report)
        logging.critical(f"[Quarantine] 🔌 {market} BORSASI İLETİŞİM SORUNLU! "
                        f"{stale_count}/{total} varlık bayat.")
    
    return reports


def get_quarantine_status() -> str:
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return "Karantina Devre Dışı"
    """Telegram heartbeat için karantina durumu."""
    state = _load_state()
    quarantined = state.get("quarantined", {})
    
    if not quarantined:
        return "🟢 Karantina: Temiz"
    
    lines = [f"🔒 Karantina ({len(quarantined)} varlık):"]
    for ticker, report in quarantined.items():
        reason = report.get("reason", "?")
        lines.append(f"  • {ticker}: {reason}")
    
    return "\n".join(lines)


def cleanup_expired_quarantines(max_age_hours: int = 168):
    # 99 yapılmıştır
    if not getattr(config, 'QUARANTINE_ENABLED', True):
        return []
    """
    1 haftadan eski karantina kayıtlarını temizle.
    Haftalık bakım için çağrılır.
    """
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _quarantine_lock:
        state = _load_state_unlocked()
        quarantined = state.get("quarantined", {})
        now = datetime.now(timezone.utc)
        
        expired = []
        for ticker, report in quarantined.items():
            qt = report.get("quarantine_time")
            if qt:
                try:
                    qt_time = datetime.fromisoformat(qt)
                    if qt_time.tzinfo is None:
                        qt_time = qt_time.replace(tzinfo=timezone.utc)
                    age_hours = (now - qt_time).total_seconds() / 3600
                    if age_hours > max_age_hours:
                        expired.append(ticker)
                except Exception as e:
                    import logging
                    logging.error(f"Beklenmeyen hata: {e}")
        
        for ticker in expired:
            del quarantined[ticker]
            logging.info(f"[Quarantine] 🧹 {ticker} karantina kaydı temizlendi (süresi dolmuş).")
        
        state["quarantined"] = quarantined
        _save_state_unlocked(state)
    return expired

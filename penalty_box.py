"""
penalty_box.py — Varlık Bazlı Ceza Kutusu (V3.2 Kaos Çözümü #4)
Aynı varlıkta ardışık SL'leri takip eder ve kademeli ceza uygular.
Whipsaw döngülerini ve komisyon erozyonunu önler.
"""
import json
import logging
import os
import threading
from datetime import datetime, timezone, timedelta

PENALTY_STATE_FILE = "penalty_box_state.json"
_penalty_lock = threading.Lock()

# 99 yapılmıştır
import config

# Ceza seviyeleri (Config.py SSOT eşleşmesi)
PENALTY_LEVELS = {
    1: {"level": "NORMAL", "min_rr": 2.0, "cooldown_hours": 0},
    getattr(config, 'PENALTY_CONSECUTIVE_WARNING', 2): {"level": "WARNING", "min_rr": 3.0, "cooldown_hours": 0},
    getattr(config, 'PENALTY_CONSECUTIVE_PENALTY', 3): {"level": "PENALTY", "min_rr": 999, "cooldown_hours": 24},
    getattr(config, 'PENALTY_CONSECUTIVE_BANNED', 5): {"level": "BANNED", "min_rr": 999, "cooldown_hours": 72},
}

# Günlük komisyon limiti (sermayenin yüzdesi olarak)
DAILY_COMMISSION_LIMIT_PCT = getattr(config, 'PENALTY_DAILY_COMMISSION_LIMIT', 1.0)


def _load_state_unlocked() -> dict:
    """Lock TUTULMADAN state oku — sadece lock zaten alınmışken kullan."""
    if os.path.exists(PENALTY_STATE_FILE):
        try:
            with open(PENALTY_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"[PenaltyBox] State dosyası okunamadı: {e}")
    return {"assets": {}, "daily_trades": {"date": "", "count": 0, "commission_pct": 0.0}}


def _save_state_unlocked(state: dict):
    """Lock TUTULMADAN atomik yaz — sadece lock zaten alınmışken kullan."""
    tmp_path = None
    try:
        import tempfile
        tmp = tempfile.NamedTemporaryFile(mode='w', dir='.', suffix='.tmp', delete=False, encoding='utf-8')
        tmp_path = tmp.name
        json.dump(state, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        os.replace(tmp_path, PENALTY_STATE_FILE)
    except Exception as e:
        logging.warning(f"[PenaltyBox] State kaydedilemedi: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                import logging
                logging.error(f"Beklenmeyen hata: {e}")


def _load_state() -> dict:
    """Thread-safe state okuma (dış kullanım için)."""
    with _penalty_lock:
        return _load_state_unlocked()


def _save_state(state: dict):
    """Thread-safe atomik state yazma (dış kullanım için)."""
    with _penalty_lock:
        _save_state_unlocked(state)


def _reset_daily_if_needed(state: dict) -> dict:
    """Gün değiştiyse günlük sayaçları sıfırla."""
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    daily = state.get("daily_trades", {})
    if daily.get("date") != today:
        state["daily_trades"] = {"date": today, "count": 0, "commission_pct": 0.0}
    return state


def record_asset_sl(ticker: str) -> str | None:
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return None
    """
    Bir varlıkta SL gerçekleştiğinde çağrılır.
    Varlık bazlı ardışık SL sayacını artırır.
    Eşik aşılırsa ceza seviyesini yükseltir.

    Returns:
        Telegram bildirim mesajı (ceza uygulandıysa), yoksa None.
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        state = _reset_daily_if_needed(state)
        assets = state.get("assets", {})

        if ticker not in assets:
            assets[ticker] = {
                "consecutive_sl": 0,
                "total_sl": 0,
                "total_tp": 0,
                "cooldown_until": None,
                "last_sl_time": None,
            }

        asset = assets[ticker]
        asset["consecutive_sl"] = asset.get("consecutive_sl", 0) + 1
        asset["total_sl"] = asset.get("total_sl", 0) + 1
        asset["last_sl_time"] = datetime.now(timezone.utc).isoformat()

        # Günlük işlem sayısı
        state["daily_trades"]["count"] = state["daily_trades"].get("count", 0) + 1
        # Tahmini komisyon ekleme (BIST %0.2, Kripto %0.1)
        comm = 0.2 if ".IS" in ticker else 0.1
        state["daily_trades"]["commission_pct"] = state["daily_trades"].get("commission_pct", 0.0) + comm

        consec = asset["consecutive_sl"]
        notification = None

        # Ceza seviyesi belirleme
        if consec >= 5:
            level_info = PENALTY_LEVELS[5]
        elif consec >= 3:
            level_info = PENALTY_LEVELS[3]
        elif consec >= 2:
            level_info = PENALTY_LEVELS[2]
        else:
            level_info = PENALTY_LEVELS[1]

        # Her SL işleminde en az 4 saatlik Pair Lock uygula
        lock_hours = getattr(config, 'PAIR_LOCK_HOURS', 4)
        cooldown_hours = max(lock_hours, level_info["cooldown_hours"])

        # Cooldown uygula
        if cooldown_hours > 0:
            until = datetime.now(timezone.utc) + timedelta(hours=cooldown_hours)
            asset["cooldown_until"] = until.isoformat()

            notification = (
                f"🥊 <b>CEZA KUTUSU — PAİR LOCK / {level_info['level']}</b>\n"
                f"Varlık: <code>{ticker}</code>\n"
                f"Ardışık SL: <b>{consec}</b>\n"
                f"━━━━━━━━━━━━━━━━━━\n"
                f"🛑 Bu varlıkta <b>{cooldown_hours} saat</b> sinyal üretilmeyecek.\n"
                f"⏰ Yasak bitişi: <b>{until.strftime('%d/%m %H:%M')} UTC</b>\n"
            )

            if consec >= 5:
                notification += (
                    f"\n⚠️ <b>DİKKAT:</b> Bu varlıkta yapısal bir sorun olabilir.\n"
                    f"Stratejilerin bu varlıkla uyumunu gözden geçirin."
                )

            logging.warning(f"[PenaltyBox] 🥊 {ticker} → {level_info['level']} ({consec} ardışık SL, {cooldown_hours} saat Cooldown)")

        elif consec >= 2:
            notification = (
                f"⚠️ <b>CEZA KUTUSU — UYARI</b>\n"
                f"Varlık: <code>{ticker}</code>\n"
                f"Ardışık SL: <b>{consec}</b>\n"
                f"Bir sonraki sinyalde minimum R:R <b>{level_info['min_rr']}:1</b> olacak."
            )

        assets[ticker] = asset
        state["assets"] = assets
        _save_state_unlocked(state)
        return notification


def record_asset_tp(ticker: str):
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return
    """
    Bir varlıkta TP gerçekleştiğinde çağrılır.
    Ardışık SL sayacını 1 düşürür (tamamen sıfırlamaz — hafıza korunur).
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        assets = state.get("assets", {})

        if ticker in assets:
            asset = assets[ticker]
            asset["consecutive_sl"] = max(0, asset.get("consecutive_sl", 0) - 1)
            asset["total_tp"] = asset.get("total_tp", 0) + 1
            assets[ticker] = asset
            state["assets"] = assets
            _save_state_unlocked(state)


def is_asset_penalized(ticker: str) -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return False
    """
    Varlık şu an cezalı mı? (Cooldown aktif mi?)

    Returns:
        True → Bu varlıkta sinyal ÜRETME
        False → Normal çalış
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        assets = state.get("assets", {})

        if ticker not in assets:
            return False

        asset = assets[ticker]
        cooldown_until = asset.get("cooldown_until")

        if cooldown_until:
            try:
                until = datetime.fromisoformat(cooldown_until)
                if datetime.now(timezone.utc) < until:
                    return True
                else:
                    # Süre doldu → cooldown'u temizle
                    asset["cooldown_until"] = None
                    assets[ticker] = asset
                    state["assets"] = assets
                    _save_state_unlocked(state)
                    return False
            except Exception:
                return False

        return False


def get_min_rr_for_asset(ticker: str) -> float:
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return 0.0
    """
    Bu varlık için minimum R:R gereksinimi.
    Cezalı varlıklarda daha yüksek R:R gerekir.
    """
    state = _load_state()
    assets = state.get("assets", {})

    if ticker not in assets:
        return 2.0  # Varsayılan

    consec = assets[ticker].get("consecutive_sl", 0)

    if consec >= 5:
        return PENALTY_LEVELS[5]["min_rr"]
    elif consec >= 3:
        return PENALTY_LEVELS[3]["min_rr"]
    elif consec >= 2:
        return PENALTY_LEVELS[2]["min_rr"]
    return PENALTY_LEVELS[1]["min_rr"]


def is_daily_commission_exceeded() -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return False
    """
    Günlük tahmini komisyon limiti aşıldı mı?
    Aşıldıysa TÜM sinyaller durdurulur.
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        state = _reset_daily_if_needed(state)
        daily = state.get("daily_trades", {})
        return daily.get("commission_pct", 0.0) >= DAILY_COMMISSION_LIMIT_PCT


def record_trade_commission(ticker: str):
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return
    """
    Her işlem açılışında tahmini komisyonu kaydet.
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        state = _reset_daily_if_needed(state)
        comm = 0.2 if ".IS" in ticker else 0.1
        state["daily_trades"]["commission_pct"] = state["daily_trades"].get("commission_pct", 0.0) + comm
        state["daily_trades"]["count"] = state["daily_trades"].get("count", 0) + 1
        _save_state_unlocked(state)


def get_penalty_status() -> str:
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return "Ceza Kutusu Devre Dışı"
    """Telegram heartbeat için ceza kutusu durumu."""
    with _penalty_lock:
        state = _load_state_unlocked()
        state = _reset_daily_if_needed(state)
        assets = state.get("assets", {})
        now = datetime.now(timezone.utc)

        penalized = []
        warned = []
        for ticker, data in assets.items():
            consec = data.get("consecutive_sl", 0)
            # Lock zaten tutulduğu için is_asset_penalized çağırmak deadlock yapar;
            # inline kontrol yapıyoruz
            cooldown_until = data.get("cooldown_until")
            is_penalized = False
            if cooldown_until:
                try:
                    until = datetime.fromisoformat(cooldown_until)
                    is_penalized = now < until
                except Exception as e:
                    import logging
                    logging.error(f"Beklenmeyen hata: {e}")
            if is_penalized:
                penalized.append(f"{ticker}({consec}SL)")
            elif consec >= 2:
                warned.append(f"{ticker}({consec}SL)")

        daily = state.get("daily_trades", {})
        lines = []

        if penalized:
            lines.append(f"🥊 Cezalı: {', '.join(penalized)}")
        if warned:
            lines.append(f"⚠️ Uyarıda: {', '.join(warned)}")

        lines.append(f"📊 Günlük İşlem: {daily.get('count', 0)} | Komisyon: %{daily.get('commission_pct', 0):.2f}")

        return "\n".join(lines) if lines else "🟢 Ceza Kutusu: Temiz"


def prune_old_assets(max_age_days: int = 90):
    # 99 yapılmıştır
    if not getattr(config, 'PENALTY_BOX_ENABLED', True):
        return 0
    """
    90 gündür SL yaşamamış ve cezası olmayan varlıkları temizle.
    Haftalık Darwinizm ile birlikte çağrılır.
    """
    with _penalty_lock:
        state = _load_state_unlocked()
        assets = state.get("assets", {})
        now = datetime.now(timezone.utc)
        to_remove = []
        for ticker, data in assets.items():
            # Aktif cezası varsa dokunma
            cooldown = data.get("cooldown_until")
            if cooldown:
                try:
                    until = datetime.fromisoformat(cooldown)
                    if now < until:
                        continue
                except Exception as e:
                    import logging
                    logging.error(f"Beklenmeyen hata: {e}")
            # Son SL zamanına bak
            last_sl = data.get("last_sl_time", "")
            if last_sl:
                try:
                    last_dt = datetime.fromisoformat(last_sl)
                    if (now - last_dt).days > max_age_days:
                        to_remove.append(ticker)
                except Exception:
                    to_remove.append(ticker)  # Parse edilemiyorsa sil
            else:
                to_remove.append(ticker)  # Hiç SL zamanı yoksa sil

        for t in to_remove:
            del assets[t]
        state["assets"] = assets
        _save_state_unlocked(state)

        if to_remove:
            logging.info(f"[PenaltyBox] Pruning: {len(to_remove)} eski varlık temizlendi: {to_remove[:10]}")
        return len(to_remove)

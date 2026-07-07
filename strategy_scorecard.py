"""
strategy_scorecard.py — Strateji Karnesi & Darwinizm Motoru (V3.2 Kaos Çözümü #5)
Her stratejiyi gerçek sonuçlarıyla izler, karne puanı verir ve
en kötü performanslıları otomatik devre dışı bırakır.
Meta-Körlüğü (kendi stratejilerimizi sorgulamayı unutma) önler.
"""
import json
import logging
import os
import tempfile
import threading
from datetime import datetime, timezone, timedelta

# 99 yapılmıştır
import config

SCORECARD_STATE_FILE = config.SCORECARD_STATE_FILE
_scorecard_lock = threading.Lock()


def _load_state_unlocked() -> dict:
    """Kilit olmadan strateji karnesi durumunu dosyadan yükle (dahili kullanım)."""
    if os.path.exists(SCORECARD_STATE_FILE):
        try:
            with open(SCORECARD_STATE_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception as e:
            logging.warning(f"[Scorecard] State okunamadı: {e}")
    return {"strategies": {}, "disabled": [], "last_report": None}


def _save_state_unlocked(state: dict):
    """Kilit olmadan atomik yazma ile durumu kaydet (dahili kullanım)."""
    tmp_path = None
    try:
        tmp = tempfile.NamedTemporaryFile(mode='w', dir='.', suffix='.tmp', delete=False, encoding='utf-8')
        tmp_path = tmp.name
        json.dump(state, tmp, indent=2, ensure_ascii=False)
        tmp.close()
        os.replace(tmp_path, SCORECARD_STATE_FILE)
    except Exception as e:
        logging.warning(f"[Scorecard] State kaydedilemedi: {e}")
        if tmp_path and os.path.exists(tmp_path):
            try:
                os.remove(tmp_path)
            except Exception as e:
                import logging
                logging.error(f"Beklenmeyen hata: {e}")


def _load_state() -> dict:
    """Strateji karnesi durumunu dosyadan yükle (kilitli)."""
    with _scorecard_lock:
        return _load_state_unlocked()


def _save_state(state: dict):
    """Strateji karnesi durumunu dosyaya kaydet (kilitli)."""
    with _scorecard_lock:
        _save_state_unlocked(state)


def record_trade_result(strategy_name: str, result: dict):
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return
    """
    Bir işlem sonucunu strateji karnesine kaydet.
    
    Args:
        strategy_name: Strateji adı (örn: "RSI_Oversold_Reversal")
        result: {
            "ticker": str,
            "outcome": "TP" | "SL" | "MANUAL",
            "pnl_pct": float,          # Yüzdesel kar/zarar
            "hold_hours": float,       # Tutma süresi (saat)
            "entry_time": str,         # Giriş zamanı ISO
            "exit_time": str,          # Çıkış zamanı ISO
            "rr_achieved": float,      # Gerçekleşen R:R oranı
        }
    """
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _scorecard_lock:
        state = _load_state_unlocked()
        strategies = state.get("strategies", {})
        
        if strategy_name not in strategies:
            strategies[strategy_name] = {
                "trades": [],
                "total_tp": 0,
                "total_sl": 0,
                "total_manual": 0,
                "total_pnl_pct": 0.0,
                "best_trade_pnl": 0.0,
                "worst_trade_pnl": 0.0,
                "avg_hold_hours": 0.0,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "is_disabled": False,
                "disabled_reason": None,
            }
        
        strat = strategies[strategy_name]
        outcome = result.get("outcome", "MANUAL")
        pnl = result.get("pnl_pct", 0.0)
        
        # İşlemi kaydet (son SCORECARD_MAX_TRADES işlem tutulur)
        trade_record = {
            "ticker": result.get("ticker", "?"),
            "outcome": outcome,
            "pnl_pct": pnl,
            "hold_hours": result.get("hold_hours", 0),
            "rr_achieved": result.get("rr_achieved", 0),
            "time": datetime.now(timezone.utc).isoformat()
        }
        
        strat["trades"].append(trade_record)
        max_trades = getattr(config, 'SCORECARD_MAX_TRADES', 500)
        if len(strat["trades"]) > max_trades:
            strat["trades"] = strat["trades"][-max_trades:]
        
        # Sayaçları güncelle
        if outcome == "TP":
            strat["total_tp"] = strat.get("total_tp", 0) + 1
        elif outcome == "SL":
            strat["total_sl"] = strat.get("total_sl", 0) + 1
        else:
            strat["total_manual"] = strat.get("total_manual", 0) + 1
        
        strat["total_pnl_pct"] = strat.get("total_pnl_pct", 0.0) + pnl
        strat["best_trade_pnl"] = max(strat.get("best_trade_pnl", 0.0), pnl)
        strat["worst_trade_pnl"] = min(strat.get("worst_trade_pnl", 0.0), pnl)
        
        # Ortalama tutma süresi
        all_hours = [t.get("hold_hours", 0) for t in strat["trades"] if t.get("hold_hours")]
        strat["avg_hold_hours"] = sum(all_hours) / len(all_hours) if all_hours else 0
        
        strategies[strategy_name] = strat
        state["strategies"] = strategies
        _save_state_unlocked(state)


def get_strategy_score(strategy_name: str) -> dict:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return {
            "score": 100, "win_rate": 0.0, "total_trades": 0,
            "avg_pnl": 0.0, "expectancy": 0.0, "grade": "N/A",
            "is_disabled": False,
        }
    """
    Strateji karnesi puanını hesapla.
    
    Returns: {
        "score": float (0-100),
        "win_rate": float,
        "total_trades": int,
        "avg_pnl": float,
        "expectancy": float,
        "grade": str ("A", "B", "C", "D", "F"),
        "is_disabled": bool,
    }
    """
    state = _load_state()
    strat = state.get("strategies", {}).get(strategy_name)
    
    if not strat or not strat.get("trades"):
        return {
            "score": 50, "win_rate": 0, "total_trades": 0,
            "avg_pnl": 0, "expectancy": 0, "grade": "N/A",
            "is_disabled": False,
        }
    
    trades = strat["trades"]
    total = len(trades)
    tp = strat.get("total_tp", 0)
    sl = strat.get("total_sl", 0)
    
    true_tp = len([t for t in trades if t.get("pnl_pct", 0) > 0])
    true_sl = len([t for t in trades if t.get("pnl_pct", 0) < 0])
    
    win_rate = true_tp / total if total > 0 else 0
    avg_pnl = strat.get("total_pnl_pct", 0) / total if total > 0 else 0
    
    # Expectancy (beklenen kazanç) = avg_win × win_rate - avg_loss × loss_rate
    wins = [t["pnl_pct"] for t in trades if t.get("pnl_pct", 0) > 0]
    losses = [abs(t["pnl_pct"]) for t in trades if t.get("pnl_pct", 0) < 0]
    
    avg_win = sum(wins) / true_tp if true_tp > 0 else 0
    avg_loss = sum(losses) / true_sl if true_sl > 0 else 0
    loss_rate = true_sl / total if total > 0 else 0
    
    expectancy = (avg_win * win_rate) - (avg_loss * loss_rate)
    
    # Skor hesaplama (0-100)
    # Win rate katkısı: 40 puan
    wr_score = min(win_rate * 80, 40)
    
    # Expectancy katkısı: 30 puan  
    exp_score = min(max(expectancy * 10, 0), 30)
    
    # Ortalama PnL katkısı: 20 puan
    pnl_score = min(max(avg_pnl * 10 + 10, 0), 20)
    
    # İşlem sayısı bonus: 10 puan (güvenilirlik)
    trade_bonus = min(total / 2, 10)
    
    score = wr_score + exp_score + pnl_score + trade_bonus
    score = max(0, min(100, score))
    
    # Not verme
    if score >= 80:
        grade = "A"
    elif score >= 60:
        grade = "B"
    elif score >= 40:
        grade = "C"
    elif score >= 25:
        grade = "D"
    else:
        grade = "F"
    
    return {
        "score": round(score, 1),
        "win_rate": round(win_rate * 100, 1),
        "total_trades": total,
        "avg_pnl": round(avg_pnl, 2),
        "expectancy": round(expectancy, 3),
        "grade": grade,
        "is_disabled": strat.get("is_disabled", False),
    }


def is_strategy_disabled(strategy_name: str) -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return False
    """Strateji Darwinizm tarafından devre dışı bırakılmış mı?"""
    state = _load_state()
    strat = state.get("strategies", {}).get(strategy_name)
    if strat:
        return strat.get("is_disabled", False)
    return False


def run_darwinism(min_trades: int = None, window_days: int = None) -> list:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return []
    
    if min_trades is None:
        min_trades = getattr(config, 'SCORECARD_MIN_TRADES', 5)
    if window_days is None:
        window_days = getattr(config, 'SCORECARD_AUTO_DISABLE_DAYS', 60)

    """
    Darwinizm motoru: Son X gün içinde Y'den fazla işlemi olan stratejilerin
    karnelerini kontrol et ve en kötüleri devre dışı bırak.
    
    Rules:
        - Grade "F" → Devre dışı bırak
        - Grade "D" + negative expectancy → Devre dışı bırak
        - Grade "D" + positive expectancy → Uyarı ver
    
    Returns:
        Değişiklik listesi: [{"strategy": str, "action": str, "reason": str}]
    """
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _scorecard_lock:
        state = _load_state_unlocked()
        strategies = state.get("strategies", {})
        changes = []
        cutoff = datetime.now(timezone.utc) - timedelta(days=window_days)
        
        for name, strat in strategies.items():
            if strat.get("is_disabled"):
                continue
            
            # Son window_days gündeki işlemleri filtrele
            recent_trades = []
            for t in strat.get("trades", []):
                try:
                    t_time = datetime.fromisoformat(t.get("time", ""))
                    if t_time.tzinfo is None:
                        t_time = t_time.replace(tzinfo=timezone.utc)
                    if t_time > cutoff:
                        recent_trades.append(t)
                except Exception:
                    continue
            
            if len(recent_trades) < min_trades:
                continue
            
            score = get_strategy_score(name)
            
            if score["grade"] == "F":
                strat["is_disabled"] = True
                strat["disabled_reason"] = f"Darwinizm: Grade F (Skor: {score['score']})"
                changes.append({
                    "strategy": name,
                    "action": "DISABLED",
                    "reason": f"Grade F | Win Rate: {score['win_rate']}% | Skor: {score['score']}",
                    "score": score
                })
                logging.warning(f"[Scorecard] 🦕 Darwinizm: {name} DEVRE DIŞI (Grade F)")
            
            elif score["grade"] == "D" and score["expectancy"] < 0:
                strat["is_disabled"] = True
                strat["disabled_reason"] = f"Darwinizm: Grade D + Negatif Expectancy ({score['expectancy']})"
                changes.append({
                    "strategy": name,
                    "action": "DISABLED",
                    "reason": f"Grade D + Negatif Beklenti | Exp: {score['expectancy']}",
                    "score": score
                })
                logging.warning(f"[Scorecard] 🦕 Darwinizm: {name} DEVRE DIŞI (Grade D, Negatif)")
            
            elif score["grade"] == "D":
                changes.append({
                    "strategy": name,
                    "action": "WARNING",
                    "reason": f"Grade D ama pozitif beklenti | Exp: {score['expectancy']}",
                    "score": score
                })
                logging.info(f"[Scorecard] ⚠️ {name} tehlike bölgesinde ama beklenti pozitif.")
        
        state["strategies"] = strategies
        state["disabled"] = [n for n, s in strategies.items() if s.get("is_disabled")]
        _save_state_unlocked(state)
    
    return changes


def re_enable_strategy(strategy_name: str) -> bool:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return False
    """Manuel olarak strateji yeniden etkinleştirme."""
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _scorecard_lock:
        state = _load_state_unlocked()
        strategies = state.get("strategies", {})
        
        if strategy_name in strategies:
            strategies[strategy_name]["is_disabled"] = False
            strategies[strategy_name]["disabled_reason"] = None
            state["strategies"] = strategies
            state["disabled"] = [n for n, s in strategies.items() if s.get("is_disabled")]
            _save_state_unlocked(state)
            logging.info(f"[Scorecard] 🔄 {strategy_name} yeniden etkinleştirildi.")
            return True
        return False


def generate_weekly_report() -> str:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return "Strateji Karnesi Devre Dışı"
    """
    Haftalık karne raporu üretir (Telegram bildirimi için).
    """
    # TOCTOU koruması: load→modify→save tek kilit altında
    with _scorecard_lock:
        state = _load_state_unlocked()
        strategies = state.get("strategies", {})
        
        if not strategies:
            return "📊 <b>Haftalık Karne:</b> Henüz işlem kaydı yok."
        
        lines = ["📊 <b>HAFTALIK STRATEJİ KARNESİ</b>", "━━━━━━━━━━━━━━━━━━"]
        
        # Tüm stratejileri skora göre sırala
        scored = []
        for name in strategies:
            score = get_strategy_score(name)
            score["name"] = name
            scored.append(score)
        
        scored.sort(key=lambda x: x["score"], reverse=True)
        
        # Genel istatistikler
        total_trades = sum(s["total_trades"] for s in scored)
        active_count = sum(1 for s in scored if not s.get("is_disabled"))
        disabled_count = sum(1 for s in scored if s.get("is_disabled"))
        
        lines.append(f"📈 Toplam İşlem: <b>{total_trades}</b>")
        lines.append(f"🟢 Aktif Strateji: <b>{active_count}</b> | 🔴 Devre Dışı: <b>{disabled_count}</b>")
        lines.append("")
        
        # Top 5 strateji
        lines.append("<b>🏆 En İyi 5:</b>")
        for i, s in enumerate(scored[:5], 1):
            emoji = ["🥇", "🥈", "🥉", "4️⃣", "5️⃣"][i-1]
            disabled_mark = " 🔴" if s.get("is_disabled") else ""
            lines.append(
                f"{emoji} <b>{s['name']}</b>{disabled_mark}\n"
                f"   Skor: {s['score']} ({s['grade']}) | "
                f"WR: {s['win_rate']}% | "
                f"PnL: %{s['avg_pnl']}"
            )
        
        # Bottom 3 (uyarı için)
        if len(scored) > 5:
            lines.append("")
            lines.append("<b>⚠️ En Kötü 3:</b>")
            for s in scored[-3:]:
                disabled_mark = " 🔴" if s.get("is_disabled") else ""
                lines.append(
                    f"❌ <b>{s['name']}</b>{disabled_mark}\n"
                    f"   Skor: {s['score']} ({s['grade']}) | "
                    f"WR: {s['win_rate']}% | "
                    f"Exp: {s['expectancy']}"
                )
        
        # Darwinizm durumu
        disabled_strats = state.get("disabled", [])
        if disabled_strats:
            lines.append("")
            lines.append(f"🦕 <b>Darwinizm Devre Dışı:</b> {', '.join(disabled_strats)}")
        
        state["last_report"] = datetime.now(timezone.utc).isoformat()
        _save_state_unlocked(state)
    
    return "\n".join(lines)


def get_scorecard_status() -> str:
    # 99 yapılmıştır
    if not getattr(config, 'SCORECARD_ENABLED', True):
        return "Strateji Karnesi Devre Dışı"
    """Telegram heartbeat için karne durumu."""
    state = _load_state()
    strategies = state.get("strategies", {})
    disabled = state.get("disabled", [])
    
    total = len(strategies)
    active = total - len(disabled)
    
    if total == 0:
        return "📊 Karne: Veri bekleniyor"
    
    return f"📊 Karne: {active}/{total} strateji aktif" + (
        f" | 🦕 Devre dışı: {', '.join(disabled)}" if disabled else ""
    )

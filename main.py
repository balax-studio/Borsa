import socket
socket.setdefaulttimeout(20)

import asyncio
import logging
from logging.handlers import RotatingFileHandler
import traceback
import sys
import os
import signal
import time

from core.notifier import NotificationService
from core.scanner import ScannerService
from core.scheduler import TaskScheduler
from core.defensive_engine import DefensiveExceptionManager

# ════ Profesyonel Logging Yapılandırması ════
logger = logging.getLogger("quant_bot")
logger.setLevel(logging.INFO)
logger.propagate = False

if not logger.handlers:
    _file_handler = RotatingFileHandler(
        "bot.log", maxBytes=5*1024*1024, backupCount=3, encoding="utf-8"
    )
    _file_handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
    ))
    logger.addHandler(_file_handler)
    
    _console_handler = logging.StreamHandler()
    _console_handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_console_handler)

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

def enforce_single_instance():
    pid_file = "bot.pid"
    current_pid = os.getpid()

    # Eski bot süreçlerini bul ve öldür (Linux için pgrep kullanımı daha kesin çözüm)
    try:
        if sys.platform != "win32":
            import subprocess
            try:
                output = subprocess.check_output(["pgrep", "-f", "main.py"]).decode()
                pids = output.strip().split('\n')
                for pid_str in pids:
                    if pid_str:
                        pid = int(pid_str)
                        if pid != current_pid:
                            try:
                                os.kill(pid, signal.SIGKILL)
                                print(f"⚠️ Eski bot süreci (PID: {pid}) bulundu ve sonlandırıldı.")
                            except OSError:
                                pass
            except subprocess.CalledProcessError:
                pass # Eşleşen işlem bulunamadı
        else:
            # Windows fallback
            if os.path.exists(pid_file):
                with open(pid_file, "r") as f:
                    old_pid_str = f.read().strip()
                    if old_pid_str:
                        old_pid = int(old_pid_str)
                        if old_pid != current_pid:
                            import ctypes
                            handle = ctypes.windll.kernel32.OpenProcess(1, False, old_pid)
                            if handle:
                                ctypes.windll.kernel32.TerminateProcess(handle, -1)
                                ctypes.windll.kernel32.CloseHandle(handle)
                                print(f"⚠️ Eski bot süreci (PID: {old_pid}) sonlandırıldı.")
    except Exception as e:
        print(f"Süreç kontrol hatası: {e}")

    try:
        with open(pid_file, "w") as f:
            f.write(str(current_pid))
    except Exception as e:
        print(f"PID dosyası yazılamadı: {e}")

async def main():
    enforce_single_instance()
    print("==================================================")
    print("🤖 Borsa Asistanı (Clean Architecture) Başlatılıyor...")
    print("==================================================")
    
    try:
        # 1. Bildirim Servisi (Telegram)
        notifier = NotificationService()
        if notifier.bot:
            print("✅ Telegram Bot bağlantısı kuruldu.")
        else:
            print("⚠️ Uyarı: TELEGRAM_BOT_TOKEN eksik. Konsol modu aktif.")
            
        if notifier.watch_bot:
            print('✅ WATCH Telegram Bot bağlantısı kuruldu.')
            
        if getattr(notifier, 'system_bot_token', None):
            print('✅ SYSTEM Telegram Bot bağlantısı kuruldu.')
        
        import data_guard
        from circuit_breaker import cb_observer
        import penalty_box
        import strategy_scorecard
        import trade_tracker.postmortem as postmortem
        from trade_tracker import TradeEngine

        trade_engine = TradeEngine(
            data_guard=data_guard,
            cb_observer=cb_observer,
            penalty_box=penalty_box,
            strategy_scorecard=strategy_scorecard,
            postmortem=postmortem
        )

        # 2. Tarayıcı Servisi (Piyasa Taraması & Kaos Modülleri)
        scanner = ScannerService(notifier=notifier, trade_engine=trade_engine)
        
        # 3. Görev Zamanlayıcı (APScheduler)
        scheduler = TaskScheduler(scanner=scanner, notifier=notifier, trade_engine=trade_engine)
        scheduler.start()
        
        # Sistemi ilk açılışta bir kez tara
        await scanner.run_scan()
        
        # Sonsuz döngü (Scheduler arkaplanda çalışmaya devam edecek)
        while True:
            # V3.4: Defensive Safe Mode Check
            if DefensiveExceptionManager.is_system_in_safe_mode():
                logger.critical("🚨 SİSTEM SAFE-MODE POZİSYONUNDA! Görev zamanlayıcı durduruluyor...")
                scheduler.shutdown()
                raise RuntimeError("Defensive Safeguard: Sistem ardışık hatalar sebebiyle durduruldu.")
            await asyncio.sleep(60) # 1 saat yerine 1 dakika aralıklarla kontrol et
            
    except KeyboardInterrupt:
        print("\n👋 Bot durduruluyor...")
    except Exception as e:
        print(f"❌ Döngü hatası: {e}")
        try:
            error_msg = f"⚠️ <b>SİSTEM HATASI (CRITICAL)</b>\n<code>{e}</code>\n<pre>{traceback.format_exc()[-500:]}</pre>"
            if 'notifier' in locals():
                # Notifier async metodu çağrılıyor
                loop = asyncio.get_event_loop()
                if loop.is_running():
                    loop.create_task(notifier.send_message(error_msg, is_system=True))
        except Exception as notify_err:
            logger.error(f"Hata bildirimi gönderilemedi: {notify_err}")
        
        # AAA Kalite: Hatayı yutma, fırlat ve programın çöktüğünden emin ol (Fail-Fast)
        DefensiveExceptionManager.log_and_raise(e, "main.py critical loop failure")

if __name__ == "__main__":
    try:
        # Fix for Windows asyncio loop
        if sys.platform == 'win32':
            asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Bot kapatıldı.")

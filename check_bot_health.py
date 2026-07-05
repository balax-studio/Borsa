import os
import sys
import time
import subprocess
import urllib.request
import urllib.parse
from dotenv import load_dotenv

# Working directory to absolute path of the bot
working_dir = os.path.dirname(os.path.abspath(__file__))
os.chdir(working_dir)

# Load env to get telegram credentials
load_dotenv()
BOT_TOKEN = os.getenv("TELEGRAM_SYSTEM_BOT_TOKEN")
CHAT_ID = os.getenv("TELEGRAM_SYSTEM_CHAT_ID")

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
        data = urllib.parse.urlencode({"chat_id": CHAT_ID, "text": msg, "parse_mode": "HTML"}).encode("utf-8")
        req = urllib.request.Request(url, data=data)
        with urllib.request.urlopen(req, timeout=10) as response:
            response.read()
    except Exception as e:
        print(f"Failed to send Telegram message: {e}")

def is_service_active():
    try:
        # systemctl is-active --quiet quant_bot returns 0 if active, non-zero otherwise
        res = subprocess.run(["systemctl", "is-active", "--quiet", "quant_bot"])
        return res.returncode == 0
    except Exception:
        # Fallback to True if systemctl is not found or fails (e.g. running on Windows)
        return sys.platform != "win32"

def main():
    # 1. Check if service is active (so we don't restart it if user stopped it intentionally)
    if not is_service_active():
        print("quant_bot service is inactive (stopped intentionally). Skipping health check.")
        return

    # 2. Check if bot.pid exists
    pid_file = "bot.pid"
    if not os.path.exists(pid_file):
        print("bot.pid does not exist, system might be stopped.")
        return

    # 3. Check last_scan_metrics.json age
    metrics_file = "last_scan_metrics.json"
    if not os.path.exists(metrics_file):
        print(f"{metrics_file} does not exist.")
        metrics_file = pid_file

    mtime = os.path.getmtime(metrics_file)
    age_seconds = time.time() - mtime
    age_minutes = age_seconds / 60.0

    print(f"Bot last scan age: {age_minutes:.1f} minutes.")

    # If age is greater than 15 minutes, we assume it is hung/stuck
    if age_minutes > 15.0:
        msg = f"⚠️ <b>[BOT SAĞLIK KONTROLÜ]</b>\nBot son <b>{age_minutes:.1f}</b> dakikadır yeni tarama yapmadı (kilitlendi).\n🔄 <code>quant_bot</code> servisi otomatik olarak yeniden başlatılıyor..."
        print(msg)
        send_telegram(msg)
        
        # Restart the systemd service
        try:
            subprocess.run(["sudo", "systemctl", "restart", "quant_bot"], check=True)
            print("Restart command executed successfully.")
        except Exception as e:
            err_msg = f"❌ Bot yeniden başlatılamadı: {e}"
            print(err_msg)
            send_telegram(err_msg)
    else:
        print("Bot is healthy.")

if __name__ == "__main__":
    main()

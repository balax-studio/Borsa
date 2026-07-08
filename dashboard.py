"""
dashboard.py
Quant Bot v3.3 Cockpit — Minimalist GitHub/Terminal Dashboard
SIFIR animasyon, SIFIR gradient, SIFIR glassmorphism.
Tüm veriler dosya okumasıyla — E2-micro dostu.
"""
import os
import re
import sys
import json
import urllib.parse
from http.server import BaseHTTPRequestHandler, HTTPServer
import datetime
import time
import config
# Dosya yolları
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
TRACKER_FILE = os.path.join(BASE_DIR, "active_trades.json")
LOG_FILE = os.path.join(BASE_DIR, "bot.log")
ENV_FILE = os.path.join(BASE_DIR, ".env")

# .env'den şifre oku (varsayılan rastgele güvenli şifre)
DASHBOARD_PASSWORD = None
if os.path.exists(ENV_FILE):
    with open(ENV_FILE, "r", encoding="utf-8") as f:
        for line in f:
            if line.startswith("DASHBOARD_PASSWORD="):
                val = line.split("=")[1].strip().replace('"', '').replace("'", "")
                if val and val != "admin":
                    DASHBOARD_PASSWORD = val

if not DASHBOARD_PASSWORD:
    import secrets
    DASHBOARD_PASSWORD = secrets.token_hex(8)
    msg = f"\n{'='*60}\n🚨 GUVENLIK UYARISI: .env dosyasinda DASHBOARD_PASSWORD bulunamadi veya guvensiz.\n🔐 Otomatik guvenli sifre olusturuldu: {DASHBOARD_PASSWORD}\nLutfen bu sifreyi kaydedin veya .env dosyasina kendi sifrenizi ekleyin.\n{'='*60}\n"
    print(msg)
    try:
        with open(LOG_FILE, "a", encoding="utf-8") as lf:
            lf.write(f"[DASHBOARD] Otomatik Guvenli Sifre Uretildi: {DASHBOARD_PASSWORD}\n")
    except Exception as e:
        import logging
        logging.error(f"Beklenmeyen hata: {e}")

# Uptime hesabı için başlangıç zamanı
START_TIME = time.time()

def get_uptime():
    diff = int(time.time() - START_TIME)
    days, remain = divmod(diff, 86400)
    hours, remain = divmod(remain, 3600)
    minutes, seconds = divmod(remain, 60)
    return f"{days}g {hours}s {minutes}d {seconds}sn"

def _tail_file(filepath, num_lines=100, chunk_size=4096):
    """Verimli şekilde dosyanın sonundan n satır okur (RAM dostu)."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'rb') as f:
            f.seek(0, 2)
            pos = f.tell()
            data = bytearray()
            while pos > 0:
                to_read = min(chunk_size, pos)
                pos -= to_read
                f.seek(pos)
                data = bytearray(f.read(to_read)) + data
                if data.count(b'\n') > num_lines:
                    break
            lines_str = data.decode('utf-8', errors='ignore').split('\n')
            return [l for l in lines_str if l][-num_lines:]
    except Exception as e:
        import logging
        logging.error(f"Tail read error: {e}")
        return []

def read_last_logs(num_lines=50):
    lines = _tail_file(LOG_FILE, num_lines)
    if not lines:
        return ["Log dosyası henüz oluşturulmadı. Bot başladığında loglar burada görünecektir."]
    return [line.strip() for line in lines]

def read_trades():
    if not os.path.exists(TRACKER_FILE):
        return []
    try:
        with open(TRACKER_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []

# ════════════════════════════════════════
# Ek Veri Kaynakları — Sadece dosya okuma (CPU yok)
# ════════════════════════════════════════

def _read_ab_stats():
    """A/B test istatistiklerini ab_test_stats.json'dan oku."""
    path = os.path.join(BASE_DIR, 'ab_test_stats.json')
    if not os.path.exists(path):
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def _read_circuit_breaker():
    """Circuit breaker durumunu oku."""
    path = os.path.join(BASE_DIR, 'circuit_breaker_state.json')
    if not os.path.exists(path):
        return {'strategies': {}}
    try:
        with open(path, 'r', encoding='utf-8') as f:
            state = json.load(f)
            return {'strategies': state.get('strategies', {})}
    except Exception:
        return {'strategies': {}}

def _read_penalty_box():
    """Penalty box durumunu oku."""
    active_path = os.path.join(BASE_DIR, 'penalty_box_state.json')
    legacy_path = os.path.join(BASE_DIR, 'penalty_box.json')
    res = []
    if os.path.exists(active_path):
        try:
            with open(active_path, 'r', encoding='utf-8') as f:
                state = json.load(f)
                assets = state.get("assets", {})
                for ticker, val in assets.items():
                    consec = val.get("consecutive_sl", 0)
                    cooldown = val.get("cooldown_until", None)
                    if consec > 0 or cooldown:
                        res.append({
                            'ticker': ticker,
                            'sl_count': consec,
                            'until': cooldown if cooldown else 'N/A'
                        })
        except Exception as e:
            import logging
            logging.error(f"Beklenmeyen hata: {e}")
    if not res and os.path.exists(legacy_path):
        try:
            with open(legacy_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for k, v in data.items():
                    sl_count = v.get('sl_count', 0)
                    if sl_count > 0:
                        res.append({
                            'ticker': k,
                            'sl_count': sl_count,
                            'until': v.get('penalty_until', 'N/A')
                        })
        except Exception as e:
            import logging
            logging.error(f"Beklenmeyen hata: {e}")
    return res[:10]

def _read_scorecard():
    """Strateji scorecard'ını oku."""
    path = os.path.join(BASE_DIR, 'strategy_scorecard.json')
    if not os.path.exists(path):
        return []
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
            return [{'strategy': k, 'tp': v.get('tp_count', 0),
                     'sl': v.get('sl_count', 0), 'active': v.get('active', True)}
                    for k, v in data.items()][:15]
    except Exception:
        return []

def _parse_scan_stats():
    """Bot.log'dan son tarama bilgilerini çıkar — E2 dostu: sadece son 100 satır."""
    lines = _tail_file(LOG_FILE, 100)
    if not lines:
        return {}
    try:
        total_signals = 0
        last_scan_duration = 'N/A'
        dg_rejects = 0
        conviction_scores = []
        for line in lines:
            if 'Toplam sinyal' in line or 'sinyal üretildi' in line:
                nums = re.findall(r'(\d+)\s*sinyal', line)
                if nums:
                    total_signals = int(nums[-1])
            if 'Tarama süresi' in line or 'sürdü' in line:
                dur = re.findall(r'([\d.]+)\s*(?:sn|saniye|dk|min)', line)
                if dur:
                    last_scan_duration = f'{dur[-1]}s'
            if '[DataGuard]' in line and ('reddetti' in line or 'reddedildi' in line):
                dg_rejects += 1
            if 'Score=' in line:
                sc = re.findall(r'Score=(\d+)', line)
                if sc:
                    conviction_scores.append(int(sc[0]))
        avg_score = round(sum(conviction_scores) / len(conviction_scores), 1) if conviction_scores else 0
        return {
            'total_signals': total_signals,
            'scan_duration': last_scan_duration,
            'dg_rejects': dg_rejects,
            'avg_conviction': avg_score,
            'conviction_distribution': {
                'strong': sum(1 for s in conviction_scores if s >= 80),
                'medium': sum(1 for s in conviction_scores if 61 <= s < 80),
                'watch': sum(1 for s in conviction_scores if 50 <= s < 61),
                'reject': sum(1 for s in conviction_scores if s < 50),
            } if conviction_scores else {}
        }
    except Exception:
        return {}

def _parse_dataguard_stats():
    """Bot.log'dan DataGuard istatistiklerini çıkar."""
    lines = _tail_file(LOG_FILE, 200)
    if not lines:
        return {}
    try:
        dg_counts = {'DG-01': 0, 'DG-02': 0, 'DG-04': 0, 'DG-06': 0}
        for line in lines:
            for dg in dg_counts:
                if dg in line:
                    dg_counts[dg] += 1
        return dg_counts
    except Exception:
        return {}


class DashboardHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        # Konsol log kalabalığını önlemek için HTTP loglarını sessize alıyoruz
        return

    def check_auth(self):
        # Cookies'ten token kontrolü yap
        cookie_header = self.headers.get('Cookie')
        if cookie_header:
            cookies = urllib.parse.parse_qs(cookie_header.replace('; ', '&'))
            if 'token' in cookies and cookies['token'][0] == DASHBOARD_PASSWORD:
                return True
        return False

    def do_GET(self):
        # 1. API: Login kontrolü
        if self.path == "/api/login-check":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"authenticated": self.check_auth()}).encode('utf-8'))
            return

        # 2. Sayfa: Login HTML
        if not self.check_auth():
            if self.path.startswith("/api/"):
                self.send_response(401)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.get_login_html().encode('utf-8'))
            return

        # 3. Birleşik API: Tüm veriler tek istekte
        if self.path.startswith("/api/all"):
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
            self.end_headers()

            # Sunucu Metrikleri
            ram_info = "N/A"
            try:
                t, f, a, b, c = 0, 0, 0, 0, 0
                with open("/proc/meminfo", "r") as fh:
                    for line in fh:
                        if line.startswith("MemTotal:"): t = int(line.split()[1]) // 1024
                        elif line.startswith("MemFree:"): f = int(line.split()[1]) // 1024
                        elif line.startswith("MemAvailable:"): a = int(line.split()[1]) // 1024
                        elif line.startswith("Buffers:"): b = int(line.split()[1]) // 1024
                        elif line.startswith("Cached:"): c = int(line.split()[1]) // 1024
                used = t - (a if a > 0 else (f + b + c))
                ram_info = f"{used}/{t}MB"
            except Exception:
                pass
                
            cpu_load = "N/A"
            try:
                l1, l5, l15 = os.getloadavg()
                cpu_load = f"{l1:.2f} (1m)"
            except Exception:
                pass
                
            disk_info = "N/A"
            try:
                st = os.statvfs('/')
                t_gb = (st.f_blocks * st.f_frsize) / (1024**3)
                f_gb = (st.f_bfree * st.f_frsize) / (1024**3)
                u_gb = t_gb - f_gb
                disk_info = f"{u_gb:.1f}/{t_gb:.1f}GB"
            except Exception:
                pass

            response_data = {
                "status": {
                    "uptime": get_uptime(),
                    "ram": ram_info,
                    "cpu": cpu_load,
                    "disk": disk_info,
                    "last_scan": datetime.datetime.now().strftime("%H:%M:%S"),
                    "status": "AKTIF"
                },
                "trades": [t for t in read_trades() if not t.get("is_watch", False) and t.get("conviction_grade") != "WATCH"],
                "logs": read_last_logs(40),
                "scan_stats": _parse_scan_stats(),
                "ab_test": _read_ab_stats(),
                "circuit_breaker": _read_circuit_breaker(),
                "dataguard": _parse_dataguard_stats(),
                "penalty_box": _read_penalty_box(),
                "scorecard": _read_scorecard(),
            }
            self.wfile.write(json.dumps(response_data).encode('utf-8'))
            return

        # 4. Sayfa: Ana Dashboard
        if self.path == "/" or self.path == "/index.html":
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(self.get_dashboard_html().encode('utf-8'))
            return

        self.send_response(404)
        self.end_headers()

    def do_POST(self):
        if self.path == "/api/login":
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(post_data)
                password = data.get("password")
                if password == DASHBOARD_PASSWORD:
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Set-Cookie", f"token={DASHBOARD_PASSWORD}; Path=/; Max-Age=2592000; HttpOnly")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Hatalı şifre!"}).encode('utf-8'))
            except Exception:
                self.send_response(500)
                self.end_headers()
            return
            
        if self.path == "/api/close":
            if not self.check_auth():
                self.send_response(401)
                self.end_headers()
                return
            content_length = int(self.headers['Content-Length'])
            post_data = self.rfile.read(content_length).decode('utf-8')
            try:
                data = json.loads(post_data)
                ticker_to_close = data.get("ticker")
                
                from trade_tracker.repository import load_trades, save_trades, _archive_closed_trades
                trades = load_trades()
                
                closed_any = False
                closed_trades = []
                remaining_trades = []
                for t in trades:
                    if t.get("ticker") == ticker_to_close and t.get("status") == "ACTIVE":
                        t["status"] = "CLOSED_MANUAL"
                        # Exit price and time are updated here to reflect manual close
                        t["exit_time"] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S+00:00')
                        closed_trades.append(t)
                        closed_any = True
                    else:
                        remaining_trades.append(t)
                
                if closed_any:
                    _archive_closed_trades(closed_trades)
                    save_trades(remaining_trades)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Aktif pozisyon bulunamadı."}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
            return
            
            
        if self.path == "/api/close_all":
            if not self.check_auth():
                self.send_response(401)
                self.end_headers()
                return
            
            try:
                from trade_tracker.repository import load_trades, save_trades, _archive_closed_trades
                trades = load_trades()
                
                closed_any = False
                closed_trades = []
                remaining_trades = []
                for t in trades:
                    if t.get("status") == "ACTIVE":
                        t["status"] = "CLOSED_MANUAL"
                        t["exit_time"] = datetime.datetime.now(datetime.timezone.utc).strftime('%Y-%m-%d %H:%M:%S+00:00')
                        closed_trades.append(t)
                        closed_any = True
                    else:
                        remaining_trades.append(t)
                
                if closed_any:
                    _archive_closed_trades(closed_trades)
                    save_trades(remaining_trades)
                    self.send_response(200)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
                else:
                    self.send_response(400)
                    self.send_header("Content-Type", "application/json")
                    self.end_headers()
                    self.wfile.write(json.dumps({"success": False, "message": "Aktif pozisyon bulunamadı."}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
            return
            
        if self.path == "/api/reset_penalty":
            if not self.check_auth():
                self.send_response(401)
                self.end_headers()
                return
            try:
                active_path = os.path.join(BASE_DIR, 'penalty_box_state.json')
                legacy_path = os.path.join(BASE_DIR, 'penalty_box.json')
                
                if os.path.exists(active_path):
                    try:
                        with open(active_path, 'r', encoding='utf-8') as f:
                            state = json.load(f)
                    except Exception:
                        state = {}
                else:
                    state = {}
                
                state["assets"] = {}
                state["daily_trades"] = {"date": datetime.datetime.now().strftime('%Y-%m-%d'), "count": 0, "commission_pct": 0.0}
                
                # Atomik yazalım
                import tempfile
                tmp_path = None
                try:
                    tmp = tempfile.NamedTemporaryFile(mode='w', dir=BASE_DIR, suffix='.tmp', delete=False, encoding='utf-8')
                    tmp_path = tmp.name
                    json.dump(state, tmp, indent=2, ensure_ascii=False)
                    tmp.close()
                    os.replace(tmp_path, active_path)
                except Exception:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception as e:
                            import logging
                            logging.error(f"Beklenmeyen hata: {e}")
                    with open(active_path, 'w', encoding='utf-8') as f:
                        json.dump(state, f, indent=2, ensure_ascii=False)
                
                # Legacy dosyayı da temizleyelim
                with open(legacy_path, 'w', encoding='utf-8') as f:
                    json.dump({}, f)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
            return

        if self.path == "/api/reset_circuit_breaker":
            if not self.check_auth():
                self.send_response(401)
                self.end_headers()
                return
            try:
                cb_path = os.path.join(BASE_DIR, 'circuit_breaker_state.json')
                default_cb_state = {
                    "daily_date": datetime.datetime.now().strftime('%Y-%m-%d'),
                    "total_daily_sl": 0,
                    "strategies": {}
                }
                
                # Atomik yazalım
                import tempfile
                tmp_path = None
                try:
                    tmp = tempfile.NamedTemporaryFile(mode='w', dir=BASE_DIR, suffix='.tmp', delete=False, encoding='utf-8')
                    tmp_path = tmp.name
                    json.dump(default_cb_state, tmp, indent=2, ensure_ascii=False)
                    tmp.close()
                    os.replace(tmp_path, cb_path)
                except Exception:
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.remove(tmp_path)
                        except Exception as e:
                            import logging
                            logging.error(f"Beklenmeyen hata: {e}")
                    with open(cb_path, 'w', encoding='utf-8') as f:
                        json.dump(default_cb_state, f, indent=2, ensure_ascii=False)
                
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(json.dumps({"success": True}).encode('utf-8'))
            except Exception as e:
                self.send_response(500)
                self.end_headers()
                self.wfile.write(json.dumps({"success": False, "message": str(e)}).encode('utf-8'))
            return
            
        self.send_response(404)
        self.end_headers()

    def get_login_html(self):
        return """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Bot v3.3 — Login</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Geist+Mono&family=Inter:wght@400;500;600&display=swap');
        *{box-sizing:border-box;margin:0;padding:0}
        body{background:#111111;color:#EAEAEA;font-family:'Inter','Helvetica Neue',sans-serif;height:100vh;display:flex;align-items:center;justify-content:center;line-height:1.6}
        .login{width:360px;padding:40px;border:1px solid #2F3437}
        h2{font-family:'Geist Mono',monospace;font-size:14px;font-weight:500;margin-bottom:8px;letter-spacing:-0.02em}
        .sub{color:#787774;font-size:12px;margin-bottom:32px}
        input{width:100%;padding:10px 0;background:transparent;border:none;border-bottom:1px solid #2F3437;color:#EAEAEA;font-family:'Geist Mono',monospace;font-size:13px;margin-bottom:24px;border-radius:0}
        input:focus{outline:none;border-bottom-color:#EAEAEA}
        button{width:100%;padding:10px;background:#EAEAEA;color:#111111;border:none;font-family:'Geist Mono',monospace;font-size:12px;font-weight:600;cursor:pointer;transition:transform 0.2s}
        button:hover{transform:scale(0.98)}
        .err{color:#FDEBEC;background:#9F2F2D;padding:4px 8px;font-size:11px;margin-top:16px;display:none;text-align:center;font-family:'Geist Mono',monospace}
    </style>
</head>
<body>
    <div class="login">
        <h2>QUANT.BOT_V3.3</h2>
        <div class="sub">Authentication Required</div>
        <input type="password" id="pw" placeholder="Enter passphrase" autofocus>
        <button onclick="doLogin()">AUTHENTICATE</button>
        <div class="err" id="err">ACCESS DENIED</div>
    </div>
    <script>
        document.getElementById('pw').addEventListener('keydown',e=>{if(e.key==='Enter')doLogin()});
        function doLogin(){
            const pw=document.getElementById('pw').value;
            fetch('/api/login',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({password:pw})})
            .then(r=>{if(r.ok){location.reload()}else{document.getElementById('err').style.display='block'}})
            .catch(()=>{document.getElementById('err').style.display='block'});
        }
    </script>
</body>
</html>"""

    def get_dashboard_html(self):
        return """<!DOCTYPE html>
<html lang="tr">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Quant Bot v3.3 Cockpit</title>
    <style>
        @import url('https://fonts.googleapis.com/css2?family=Geist+Mono&family=Inter:wght@400;500;600&display=swap');
        *{box-sizing:border-box;margin:0;padding:0}
        body{background:#111111;color:#EAEAEA;font-family:'Inter','Helvetica Neue',sans-serif;font-size:13px;padding:32px;max-width:1100px;margin:0 auto;line-height:1.6}
        .hdr{display:flex;align-items:center;justify-content:space-between;border-bottom:1px solid #2F3437;padding-bottom:16px;margin-bottom:48px}
        .hdr h1{font-family:'Geist Mono',monospace;font-size:16px;font-weight:500;letter-spacing:-0.02em}
        .hdr .st{font-family:'Geist Mono',monospace;font-size:11px;color:#787774}
        .hdr a{color:#787774;font-family:'Geist Mono',monospace;font-size:11px;text-decoration:none;transition:color 0.2s}
        .hdr a:hover{color:#EAEAEA}
        .section{margin-bottom:48px;border-top:1px solid #2F3437;padding-top:24px}
        .section-hdr{display:flex;justify-content:space-between;align-items:baseline;margin-bottom:24px;flex-wrap:wrap;gap:12px;}
        h3{font-family:'Geist Mono',monospace;font-size:11px;color:#787774;text-transform:uppercase;letter-spacing:0.05em;font-weight:500}
        .kv{display:grid;grid-template-columns:repeat(auto-fit, minmax(130px, 1fr));gap:16px;margin-bottom:24px;}
        .kv .k{color:#787774;font-family:'Geist Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:0.05em;margin-bottom:8px;display:block}
        .kv .v{color:#EAEAEA;font-size:16px;font-weight:500}
        .table-responsive{width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch;}
        table{width:100%;border-collapse:collapse;font-size:13px;min-width:700px;}
        th{text-align:left;color:#787774;font-family:'Geist Mono',monospace;font-size:10px;text-transform:uppercase;letter-spacing:0.05em;font-weight:400;padding:8px 0;border-bottom:1px solid #2F3437}
        td{padding:12px 0;border-bottom:1px solid #2F3437;color:#EAEAEA}
        .badge{display:inline-block;padding:2px 8px;border-radius:2px;font-family:'Geist Mono',monospace;font-size:10px;letter-spacing:0.05em;border:1px solid currentColor}
        .b-strong{background:transparent;color:#4CAF50}
        .b-medium{background:transparent;color:#FFC107}
        .b-watch{background:transparent;color:#EAEAEA}
        .b-reject{background:transparent;color:#F44336}
        .bar-container{display:flex;gap:2px;margin-top:8px;height:4px;width:100%}
        .bar{height:100%;border-radius:0}
        .log-box{font-family:'Geist Mono',monospace;font-size:11px;line-height:1.8;max-height:400px;overflow-y:auto;color:#EAEAEA;padding:16px;background:#0A0A0A;border:1px solid #2F3437;}
        .log-box .info{color:#787774}
        .log-box .warn{color:#FFC107}
        .log-box .error{color:#F44336}
        .log-box .conviction{color:#03A9F4}
        .empty{color:#787774;font-style:italic}
        .metric-block{display:flex;flex-direction:column;border:1px solid #2F3437;padding:16px;background:#111;}
        .close-btn{background:transparent;color:#F44336;border:1px solid #F44336;padding:4px 8px;border-radius:0;font-family:'Geist Mono',monospace;font-size:10px;cursor:pointer;transition:background 0.2s, color 0.2s;white-space:nowrap;}
        .close-btn:hover{background:#F44336;color:#111;}
        .btn-secondary{background:transparent;color:#EAEAEA;border:1px solid #2F3437;padding:4px 8px;border-radius:0;font-family:'Geist Mono',monospace;font-size:10px;cursor:pointer;white-space:nowrap;}
        .btn-secondary:hover{background:#EAEAEA;color:#111;}
        .fade-in{animation:fadeIn 0.6s cubic-bezier(0.16,1,0.3,1) both}
        @keyframes fadeIn { from{opacity:0;transform:translateY(12px)} to{opacity:1;transform:translateY(0)} }
        @media (max-width:768px){
            body{padding:16px;}
            .hdr{flex-direction:column;align-items:flex-start;gap:12px;}
            .kv{grid-template-columns:1fr 1fr;gap:12px;}
            .section-hdr{flex-direction:column;gap:12px;align-items:flex-start;}
        }
    </style>
</head>
<body>
    <div class="hdr fade-in">
        <h1>QUANT.BOT_V3.3</h1>
        <div>
            <span class="st" id="s-uptime">—</span>
            &nbsp;&nbsp;&nbsp;&nbsp;
            <a href="#" onclick="document.cookie='token=;Max-Age=0;Path=/';location.reload()">LOGOUT</a>
        </div>
    </div>

    <!-- SYSTEM & SCAN -->
    <div class="section fade-in" style="animation-delay: 80ms">
        <div class="section-hdr">
            <h3>System Intelligence</h3>
            <div style="display:flex; gap:8px; flex-wrap:wrap;">
                <button class="btn-secondary" onclick="resetPenalty()">RESET PENALTY BOX</button>
                <button class="btn-secondary" onclick="resetCircuitBreaker()">RESET CIRCUIT BREAKER</button>
            </div>
        </div>
        <div class="kv">
            <div class="metric-block"><span class="k">RAM</span><span class="v" id="s-ram" style="font-family:'Geist Mono',monospace;">—</span></div>
            <div class="metric-block"><span class="k">CPU</span><span class="v" id="s-cpu" style="font-family:'Geist Mono',monospace;">—</span></div>
            <div class="metric-block"><span class="k">Disk</span><span class="v" id="s-disk" style="font-family:'Geist Mono',monospace;">—</span></div>
            <div class="metric-block"><span class="k">Last Scan</span><span class="v" id="s-scan">—</span></div>
            <div class="metric-block"><span class="k">Duration</span><span class="v" id="sc-dur">—</span></div>
            <div class="metric-block"><span class="k">Signals</span><span class="v" id="sc-sig">—</span></div>
            <div class="metric-block"><span class="k">Win Rate</span><span class="v" id="s-winrate">—</span></div>
            <div class="metric-block" style="grid-column: 1 / -1;"><span class="k">Circuit Breaker</span><span class="v" id="s-cb" style="font-family:'Geist Mono',monospace;font-size:12px">—</span></div>
        </div>
        <div id="penalty-box-container" style="margin-top:24px; display:none;">
            <span class="k" style="margin-bottom:8px; color:#787774; font-family:'Geist Mono',monospace; font-size:10px; text-transform:uppercase;">Ceza Kutusundaki Varlıklar</span>
            <div id="penalty-assets-list" style="font-family:'Geist Mono',monospace; font-size:12px; color:#EAEAEA;"></div>
        </div>
        <div id="conv-dist" style="margin-top:24px;width:100%;max-width:300px"></div>
    </div>

    <!-- ACTIVE TRADES -->
    <div class="section fade-in" style="animation-delay: 160ms">
        <div class="section-hdr">
            <h3>Active Deployments (<span id="trade-count">0</span>)</h3>
            <button class="close-btn" onclick="closeAllTrades()">CLOSE ALL TRADES</button>
        </div>
        <div class="table-responsive">
            <table id="trade-table">
                <thead><tr><th onclick="sortTrades('ticker')" style="cursor:pointer" title="Sırala">Ticker ↕</th><th>Dir</th><th>Strategy</th><th>Entry</th><th>SL</th><th>TP</th><th onclick="sortTrades('conviction_score')" style="cursor:pointer" title="Sırala">Conviction ↕</th><th>Status</th><th>Aksiyon</th></tr></thead>
                <tbody id="trade-body"><tr><td colspan="9" class="empty">No active deployments.</td></tr></tbody>
            </table>
        </div>
    </div>

    <!-- PERFORMANCE ANALYTICS -->
    <div class="section fade-in" style="animation-delay: 200ms">
        <div class="section-hdr"><h3>Performance Analytics</h3></div>
        <div id="analytics-content">
            <div style="color:#787774;font-style:italic">Loading analytics...</div>
        </div>
    </div>

    <!-- LOGS -->
    <div class="section fade-in" style="animation-delay: 240ms">
        <div class="section-hdr"><h3>System Logs</h3></div>
        <div class="log-box" id="log-box"></div>
    </div>

    <script>
        function esc(s){if(!s)return'';return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;')}

        let sortCol = 'conviction_score';
        let sortDesc = true;
        let currentTrades = [];

        function sortTrades(col) {
            if (sortCol === col) {
                sortDesc = !sortDesc;
            } else {
                sortCol = col;
                sortDesc = true;
            }
            renderTrades();
        }

        function closeTrade(ticker) {
            if(!confirm(ticker + ' pozisyonunu manuel olarak kapatmak istediginize emin misiniz?')) return;
            fetch('/api/close', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ticker: ticker})
            })
            .then(r=>r.json())
            .then(d=>{
                if(d.success) { updateAllData(); }
                else { alert('Hata: ' + d.message); }
            })
            .catch(err=>alert('Bağlantı hatası: '+err));
        }

        function closeAllTrades() {
            if(!confirm('TÜM aktif pozisyonları kapatmak istediğinize emin misiniz?')) return;
            fetch('/api/close_all', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
            })
            .then(r=>r.json())
            .then(d=>{
                if(d.success) { updateAllData(); }
                else { alert('Hata: ' + d.message); }
            })
            .catch(err=>alert('Bağlantı hatası: '+err));
        }

        function resetPenalty() {
            if(!confirm('Ceza kutusundaki tüm varlıkları sıfırlamak istediğinize emin misiniz?')) return;
            fetch('/api/reset_penalty', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
            })
            .then(r=>r.json())
            .then(d=>{
                if(d.success) { updateAllData(); }
                else { alert('Hata: ' + d.message); }
            })
            .catch(err=>alert('Bağlantı hatası: '+err));
        }

        function resetCircuitBreaker() {
            if(!confirm('Tüm sessiz modları sıfırlamak istediğinize emin misiniz?')) return;
            fetch('/api/reset_circuit_breaker', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({})
            })
            .then(r=>r.json())
            .then(d=>{
                if(d.success) { updateAllData(); }
                else { alert('Hata: ' + d.message); }
            })
            .catch(err=>alert('Bağlantı hatası: '+err));
        }

        function renderTrades() {
            const trades = currentTrades;
            document.getElementById('trade-count').textContent=trades.length;
            const tBody=document.getElementById('trade-body');
            
            if(trades.length){
                let sortedTrades = [...trades];
                sortedTrades.sort((a,b) => {
                    let valA = a[sortCol];
                    let valB = b[sortCol];
                    if (valA === undefined || valA === null) valA = sortCol === 'conviction_score' ? -9999 : '';
                    if (valB === undefined || valB === null) valB = sortCol === 'conviction_score' ? -9999 : '';
                    
                    if (valA < valB) return sortDesc ? 1 : -1;
                    if (valA > valB) return sortDesc ? -1 : 1;
                    return 0;
                });
                
                tBody.innerHTML=sortedTrades.map(t=>{
                    const cs=t.conviction_score;
                    const cg=t.conviction_grade||'';
                    let convHtml='—';
                    if(cs!==undefined&&cs!==null){
                        const bc=cg==='STRONG'?'b-strong':cg==='MEDIUM'?'b-medium':cg==='WATCH'?'b-watch':'b-reject';
                        convHtml=`${Math.round(cs)} <span class="badge ${bc}">${cg}</span>`;
                    }
                    const status=t.status||'ACTIVE';
                    const stColor=status==='ACTIVE'?'#EAEAEA':'#787774';
                    let actionHtml = '';
                    if(status === 'ACTIVE') {
                        actionHtml = `<button class="close-btn" onclick="closeTrade('${t.ticker}')">CLOSE</button>`;
                    }
                    return `<tr>
                        <td style="font-family:'Geist Mono',monospace">${esc(t.ticker)}</td>
                        <td style="color:${t.signal==='AL'?'#EAEAEA':'#787774'}">${t.signal}</td>
                        <td>${esc(t.strategy)}</td>
                        <td style="font-family:'Geist Mono',monospace">${t.entry_price}</td>
                        <td style="font-family:'Geist Mono',monospace;color:#787774">${t.sl}</td>
                        <td style="font-family:'Geist Mono',monospace">${t.tp}</td>
                        <td>${convHtml}</td>
                        <td style="color:${stColor};font-family:'Geist Mono',monospace;font-size:11px">${status}</td>
                        <td>${actionHtml}</td>
                    </tr>`;
                }).join('');
            } else {
                tBody.innerHTML='<tr><td colspan="9" class="empty">No active deployments.</td></tr>';
            }
        }

        function updateAllData(){
            fetch('/api/all?t=' + Date.now())
            .then(r=>{if(!r.ok)throw new Error(r.status);return r.json()})
            .then(d=>{
                // SYSTEM
                const s=d.status||{};
                document.getElementById('s-uptime').textContent='UPTIME: '+ (s.uptime||'—');
                document.getElementById('s-scan').textContent=s.last_scan||'—';
                if(document.getElementById('s-ram')) document.getElementById('s-ram').textContent = s.ram || '—';
                if(document.getElementById('s-cpu')) document.getElementById('s-cpu').textContent = s.cpu || '—';
                if(document.getElementById('s-disk')) document.getElementById('s-disk').textContent = s.disk || '—';
                
                const cb=d.circuit_breaker||{};
                const strats=cb.strategies||{};
                const keys=Object.keys(strats);
                let cbHtml='';
                if(keys.length===0){
                    cbHtml='<span style="color:#787774">ALL SYSTEMS NOMINAL</span>';
                }else{
                    cbHtml=keys.map(k=>{
                        const isOpen=strats[k].silent_mode;
                        return `<span style="color:${isOpen?'#9F2F2D':'#787774'}">${k}:${isOpen?'SILENT':'ACTIVE'}</span>`;
                    }).join(' · ');
                }
                document.getElementById('s-cb').innerHTML=cbHtml;

                // WIN RATE (Analysis)
                const scard=d.scorecard||[];
                let totalTP=0, totalSL=0;
                scard.forEach(x=>{ totalTP+=x.tp; totalSL+=x.sl; });
                const totalTrades = totalTP+totalSL;
                const winRate = totalTrades>0 ? ((totalTP/totalTrades)*100).toFixed(1)+'%' : 'N/A';
                document.getElementById('s-winrate').textContent = winRate;

                // TARAMA
                const sc=d.scan_stats||{};
                document.getElementById('sc-dur').textContent=sc.scan_duration||'N/A';
                document.getElementById('sc-sig').textContent=sc.total_signals||0;
                
                const cd=sc.conviction_distribution||{};
                const totC=(cd.strong||0)+(cd.medium||0)+(cd.watch||0)+(cd.reject||0);
                if(totC>0){
                    const pS=(cd.strong/totC)*100; const pM=(cd.medium/totC)*100; const pW=(cd.watch/totC)*100; const pR=(cd.reject/totC)*100;
                    document.getElementById('conv-dist').innerHTML=`
                        <div style="font-family:'Geist Mono',monospace;font-size:10px;color:#787774;margin-bottom:4px;letter-spacing:0.05em">SIGNAL QUALITY DISTRIBUTION</div>
                        <div class="bar-container">
                            <div class="bar" style="width:${pS}%;background:#4CAF50"></div>
                            <div class="bar" style="width:${pM}%;background:#FFC107"></div>
                            <div class="bar" style="width:${pW}%;background:#EAEAEA"></div>
                            <div class="bar" style="width:${pR}%;background:#F44336"></div>
                        </div>
                    `;
                } else {
                    document.getElementById('conv-dist').innerHTML='';
                }

                // AKTİF İŞLEMLER
                currentTrades = (d.trades || []).filter(t => t.conviction_grade !== 'WATCH');
                renderTrades();

                // CEZA KUTUSU GÜNCELLEME
                const pb = d.penalty_box || [];
                const pbContainer = document.getElementById('penalty-box-container');
                if (pb.length > 0) {
                    pbContainer.style.display = 'block';
                    document.getElementById('penalty-assets-list').innerHTML = pb.map(x => {
                        const dateStr = x.until && x.until !== 'N/A' ? new Date(x.until).toLocaleString('tr-TR') : 'N/A';
                        return `<div style="margin-bottom: 4px;">🎯 <span style="color:#9F2F2D; font-weight: 500;">${esc(x.ticker)}</span> — Ardışık SL: <b>${x.sl_count}</b> · Cooldown Bitiş: <b>${dateStr}</b></div>`;
                    }).join('');
                } else {
                    pbContainer.style.display = 'none';
                }

                // STRATEGY ANALYTICS
                let analyticsHtml = '<div style="display:flex; gap:48px; flex-wrap:wrap;">';
                analyticsHtml += `<div><span style="color:#787774;font-family:'Geist Mono',monospace;font-size:10px;text-transform:uppercase;">Global Win Rate</span><br><span style="font-size:24px;color:#EAEAEA;">${winRate}</span><br><span style="color:#787774;font-size:11px;">${totalTP} TP / ${totalSL} SL</span></div>`;
                
                let topStrat = 'N/A', topWinRate = -1;
                let strategyStatsHtml = '<table style="width:100%;max-width:400px;margin-top:24px;"><thead><tr><th>Strategy</th><th>Win Rate</th><th>TP/SL</th></tr></thead><tbody>';
                let scard_sorted = [...scard].sort((a,b)=>(b.tp+b.sl)-(a.tp+a.sl));
                scard_sorted.slice(0,5).forEach(s => {
                    const stTotal = s.tp + s.sl;
                    if(stTotal > 0) {
                        const stWR = (s.tp / stTotal) * 100;
                        if(stWR > topWinRate && stTotal >= 1) { topWinRate = stWR; topStrat = s.strategy; }
                        strategyStatsHtml += `<tr><td style="font-family:'Geist Mono',monospace">${s.strategy}</td><td style="font-family:'Geist Mono',monospace">${stWR.toFixed(1)}%</td><td style="font-family:'Geist Mono',monospace;color:#787774">${s.tp}/${s.sl}</td></tr>`;
                    }
                });
                if(topStrat === 'N/A') strategyStatsHtml += '<tr><td colspan="3" style="color:#787774;padding-top:12px;">Not enough data.</td></tr>';
                strategyStatsHtml += '</tbody></table>';

                analyticsHtml += `<div><span style="color:#787774;font-family:'Geist Mono',monospace;font-size:10px;text-transform:uppercase;">Top Strategy</span><br><span style="font-size:15px;color:#EAEAEA;font-family:'Geist Mono',monospace;">${topStrat}</span><br><span style="color:#787774;font-size:11px;">Based on highest win rate</span></div>`;
                
                analyticsHtml += '</div>' + strategyStatsHtml;
                document.getElementById('analytics-content').innerHTML = analyticsHtml;

                // CANLI LOG
                const logs=d.logs||[];
                const logBox=document.getElementById('log-box');
                logBox.innerHTML=logs.map(l=>{
                    let cls='info';
                    if(l.includes('[WARN]')||l.includes('[WARNING]'))cls='warn';
                    else if(l.includes('[ERROR]'))cls='error';
                    else if(l.includes('Score=')||l.includes('Conviction'))cls='conviction';
                    return `<div class="${cls}">> ${esc(l)}</div>`;
                }).join('');
                logBox.scrollTop=logBox.scrollHeight;
            })
            .catch(err=>console.error('Güncelleme hatası:',err));
        }

        updateAllData();
        setInterval(updateAllData, 15000);
    </script>
</body>
</html>"""

def run_server(port=8080):
    server_address = ('', port)
    httpd = HTTPServer(server_address, DashboardHandler)
    print(f"\n==================================================")
    print(f"  Quant Bot Dashboard: http://localhost:{port}")
    print(f"  Erişim Şifresi: {DASHBOARD_PASSWORD}")
    print(f"==================================================\n")
    httpd.serve_forever()

if __name__ == "__main__":
    port = 8080
    if len(sys.argv) > 1:
        port = int(sys.argv[1])
    run_server(port)

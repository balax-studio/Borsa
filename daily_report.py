import os
import csv
import requests
from datetime import datetime, timezone
from dotenv import load_dotenv

try:
    from openpyxl import Workbook
    from openpyxl.styles import Font, Alignment
    HAS_OPENPYXL = True
except ImportError:
    HAS_OPENPYXL = False

load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
CHAT_IDS_ENV = os.getenv("TELEGRAM_CHAT_ID", "")
CHAT_IDS = [cid.strip() for cid in CHAT_IDS_ENV.split(",") if cid.strip()]

WATCH_BOT_TOKEN = os.getenv("TELEGRAM_WATCH_BOT_TOKEN")
WATCH_CHAT_IDS_ENV = os.getenv("TELEGRAM_WATCH_CHAT_ID", "")
WATCH_CHAT_IDS = [cid.strip() for cid in WATCH_CHAT_IDS_ENV.split(",") if cid.strip()]

def send_telegram(msg):
    if not BOT_TOKEN or not CHAT_IDS:
        print("Telegram bot token or chat ID is missing.")
        return
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    for cid in CHAT_IDS:
        payload = {
            "chat_id": cid,
            "text": msg,
            "parse_mode": "HTML"
        }
        try:
            r = requests.post(url, json=payload, timeout=10)
            r.raise_for_status()
            print(f"Message sent to {cid}")
        except Exception as e:
            print(f"Failed to send to {cid}: {e}")

def send_telegram_document_watch_bot(file_path, caption=""):
    if not WATCH_BOT_TOKEN or not WATCH_CHAT_IDS:
        print("Watch Bot token or chat ID is missing. Document not sent.")
        return
        
    url = f"https://api.telegram.org/bot{WATCH_BOT_TOKEN}/sendDocument"
    for cid in WATCH_CHAT_IDS:
        try:
            with open(file_path, "rb") as f:
                payload = {"chat_id": cid, "caption": caption, "parse_mode": "HTML"}
                files = {"document": f}
                r = requests.post(url, data=payload, files=files, timeout=10)
                r.raise_for_status()
            print(f"Document {file_path} sent via Watch Bot to {cid}")
        except Exception as e:
            print(f"Failed to send document to {cid}: {e}")

def convert_csv_to_excel(csv_file, excel_file):
    if not HAS_OPENPYXL:
        print("openpyxl kurulu degil, csv dondurulecek.")
        return csv_file
        
    wb = Workbook()
    ws = wb.active
    ws.title = "Trade Journal"
    
    with open(csv_file, mode='r', encoding='utf-8-sig') as f:
        reader = csv.reader(f)
        for i, row in enumerate(reader):
            ws.append(row)
            if i == 0:
                for cell in ws[1]:
                    cell.font = Font(bold=True)
                    cell.alignment = Alignment(horizontal="center")
    
    for col in ws.columns:
        max_length = 0
        column = col[0].column_letter
        for cell in col:
            try:
                if len(str(cell.value)) > max_length:
                    max_length = len(str(cell.value))
            except Exception as e:
                import logging
                logging.error(f"Beklenmeyen hata: {e}")
        ws.column_dimensions[column].width = min(max_length + 2, 50)
        
    wb.save(excel_file)
    return excel_file

def main():
    today = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    csv_file = "trade_journal.csv"
    excel_file = f"trade_journal_{today}.xlsx"
    
    if not os.path.exists(csv_file):
        send_telegram(f"📊 <b>GÜNLÜK İŞLEM RAPORU</b>\n\n{today} tarihinde işlem (trade_journal.csv) bulunamadı.")
        return
        
    trades_today = []
    with open(csv_file, mode='r', encoding='utf-8-sig') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get("tarih") == today:
                trades_today.append(row)
                
    if not trades_today:
        send_telegram(f"📊 <b>GÜNLÜK İŞLEM RAPORU</b>\n\n{today} tarihinde henüz işlem kapanmadı.")
        if os.path.exists(csv_file):
            final_file = convert_csv_to_excel(csv_file, excel_file)
            send_telegram_document_watch_bot(final_file, caption=f"📁 {today} Güncel İşlem Defteri (İşlem yoktu)")
        return
        
    total_trades = len(trades_today)
    wins = len([t for t in trades_today if t.get("sonuc") == "KAZANC"])
    losses = len([t for t in trades_today if t.get("sonuc") in ["KAYIP", "KARA_KUGU"]])
    
    total_pnl = sum([float(t.get("net_pnl_pct", 0.0)) for t in trades_today])
    
    msg = f"📊 <b>GÜNLÜK İŞLEM RAPORU ({today})</b>\n"
    msg += f"━━━━━━━━━━━━━━━━━━━━━━━\n"
    msg += f"Toplam İşlem: {total_trades}\n"
    msg += f"Kazanılan: {wins}\n"
    msg += f"Kaybedilen: {losses}\n"
    msg += f"Günlük PnL (Kümülatif %): <b>%{total_pnl:.2f}</b>\n\n"
    
    msg += f"🛑 <b>ZARAR KESİLEN (SL) İŞLEMLERİN ANALİZİ:</b>\n"
    sl_count = 0
    for t in trades_today:
        if t.get("sonuc") in ["KAYIP", "KARA_KUGU"]:
            sl_count += 1
            ind_info = t.get("indicators", "N/A")
            msg += f"• <code>{t['sembol']}</code> ({t['strateji']}): PnL %{t.get('net_pnl_pct')}\n"
            msg += f"  <i>İndikatörler:</i> {ind_info}\n\n"
            
    if sl_count == 0:
        msg += "Bugün hiç SL işlemi olmadı! 🎉\n"
        
    send_telegram(msg)
    
    if os.path.exists(csv_file):
        final_file = convert_csv_to_excel(csv_file, excel_file)
        caption_msg = f"📁 <b>{today} Excel İşlem Raporu</b>\nDetaylı trade data ektedir."
        send_telegram_document_watch_bot(final_file, caption=caption_msg)

if __name__ == "__main__":
    main()

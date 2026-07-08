import aiohttp
import logging
import os
from dotenv import load_dotenv

logger = logging.getLogger("quant_bot.ai_commentary")

load_dotenv()
GROQ_API_KEY = os.getenv("GROQ_API_KEY")
if not GROQ_API_KEY:
    logger.warning("GROQ_API_KEY environment variable is not set. AI commentary will be skipped.")
GROQ_API_URL = "https://api.groq.com/openai/v1/chat/completions"

TEXT_MODEL = "llama-3.3-70b-versatile"

async def get_ai_commentary(signals, chart_path=None, df_4h=None):
    if not GROQ_API_KEY:
        return None
    if not signals:
        return None
        
    # Support both single signal dict and list of signals
    if isinstance(signals, dict):
        signals_list = [signals]
    else:
        signals_list = list(signals)

    try:
        signal_details = []
        for s in signals_list:
            ticker = s.get("ticker", "Bilinmiyor")
            direction = "LONG" if s.get("signal") == "AL" else "SHORT"
            reason = s.get("reason", "")
            strategy = s.get("strategy", "")
            entry = s.get("entry_price", 0.0)
            sl = s.get("sl", 0.0)
            tp = s.get("tp", 0.0)
            
            # Combine indicators and raw_indicators
            inds = s.get("indicators", {}) or {}
            raw_inds = s.get("raw_indicators", {}) or {}
            
            all_inds = {}
            if isinstance(inds, dict):
                all_inds.update(inds)
            if isinstance(raw_inds, dict):
                all_inds.update(raw_inds)
                
            ind_strings = []
            for k, v in all_inds.items():
                if isinstance(v, float):
                    ind_strings.append(f"{k}: {v:.2f}")
                else:
                    ind_strings.append(f"{k}: {v}")
            inds_formatted = ", ".join(ind_strings) if ind_strings else "Veri bulunmuyor"
            
            signal_details.append(
                f"- Varlık: {ticker}\n"
                f"  Yön: {direction}\n"
                f"  Seviyeler: Giriş={entry:.4f} | SL={sl:.4f} | TP={tp:.4f}\n"
                f"  Strateji: {strategy}\n"
                f"  Sistem Gerekçesi: {reason}\n"
                f"  Teknik İndikatörler: {inds_formatted}"
            )
            
        prompt = (
            "Sen üst düzey ve efsanevi bir Kripto Para ve Borsa Kantitatif Analistisin (Master Crypto Trader).\n"
            "Görevin, sana iletilen sınırlı verilere bağımlı kalmadan, sanki şu an TradingView'da veya profesyonel bir terminalde o varlığın grafiğini kendi başına açıp inceliyormuş gibi düşünmektir.\n"
            "DİKKAT: Biz uzun vadeli yatırımcı değiliz, günlük al-sat (Day Trading) yapıyoruz. Yorumlarını uzun vadeye göre değil, gün içi / kısa vadeli (intraday) trendlere ve fiyat hareketlerine göre yap.\n\n"
            "Sinyaller (yalnızca referans için):\n" + "\n\n".join(signal_details) + "\n\n"
            "Görev: Bizim gönderdiğimiz teknik verilere sınırlı kalma. Varlığın kısa vadeli piyasa durumunu, gün içi destek/direnç seviyelerini ve trend yapısını kendi usta trader vizyonunla değerlendir.\n"
            "Bize gönderilen işlem yönünün (LONG veya SHORT) kısa vadeli piyasa şartlarına uyup uymadığını kendi teknik öngörülerini kullanarak bağımsız şekilde analiz et.\n\n"
        )

        if df_4h is not None and not df_4h.empty:
            import pandas as pd
            df_slice = df_4h.tail(15)
            ohlcv_lines = []
            for dt, row in df_slice.iterrows():
                dt_str = dt.strftime("%Y-%m-%d %H:%M")
                ohlcv_lines.append(
                    f"| {dt_str} | {row['open']:.4f} | {row['high']:.4f} | {row['low']:.4f} | {row['close']:.4f} | {int(row['volume'])} |"
                )
            ohlcv_table = (
                "| Tarih (UTC) | Açılış | Yüksek | Düşük | Kapanış | Hacim |\n"
                "| :--- | :--- | :--- | :--- | :--- | :--- |\n"
                + "\n".join(ohlcv_lines)
            )
            prompt += f"Grafik Mum Verileri (Son 15 Bar - 4 Saatlik):\n{ohlcv_table}\n\n"

        prompt += (
            "KESİN KURALLAR VE ANALİZ YAKLAŞIMI:\n"
            "1. Kendi Analizini Yap: Sana verilen veriler sadece bir uyarandır. Sen TradingView ekranında grafiği kendi başına inceliyormuş gibi piyasa yapısını, likidite bölgelerini ve trendi kendi geniş uzmanlığınla yorumla.\n"
            "2. Acımasızca Seçici Ol: Eğer piyasa yapısında belirsizlik varsa, trend sıkışmışsa veya içinden 'temkinli olmalıyım' diyorsan ASLA 'İŞLEME GİR' deme. Yarım ağızla işleme girilmez.\n"
            "3. Mantıksal Tutarlılık (ÇOK ÖNEMLİ): Eğer analiz metninde 'belirsiz, kararsız, yatay, temkinli' gibi risk ifade eden bir görüş belirtiyorsan, Kararın KESİNLİKLE 'İŞLEME GİRME' (Skor < 50) olmalıdır.\n"
            "4. Kısa ve Öz Tut: Analizini ve piyasa görüşünü son derece kısa, net ve öz tut (toplamda en fazla 3-4 cümle).\n"
            "5. Objektif Skorlama (0-100):\n"
            "   - 0-49: Sinyal zayıf, yön belirsiz, yatay piyasa veya çelişkili durum. (Karar: İŞLEME GİRME)\n"
            "   - 50-100: Sadece teknik görünümün çok net, güçlü ve yüksek potansiyelli olduğu durumlar. (Karar: İŞLEME GİR)\n\n"
            "ÇIKTI FORMATI: Analizini yaparken aşağıdaki şablonu KESİNLİKLE bozmadan kullan. Başka metin ekleme:\n\n"
            "🤖 **[Varlık Adı]**\n"
            "🧠 **Analiz:** [1-2 cümlelik kendi bağımsız ve usta işi analiziniz, fiyat hareketi veya trend yorumunuz]\n"
            "Skor: [0-100]\n"
            "Karar: [İŞLEME GİR veya İŞLEME GİRME]\n"
            "Neden: [Tüm kararın tek cümlelik özeti]"
        )

        headers = {
            "Authorization": f"Bearer {GROQ_API_KEY}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": TEXT_MODEL,
            "messages": [
                {
                    "role": "system", 
                    "content": "Sen efsanevi bir Kripto Para yatırımcısı ve analistisin. Verilen kısıtlı verilere bağlı kalmaz, kendi derin piyasa tecrübenle TradingView grafiğine bakıyormuşçasına bağımsız ve keskin yorumlar yaparsın."
                },
                {"role": "user", "content": prompt}
            ],
            "temperature": 0.3,
            "max_tokens": 1200
        }

        async with aiohttp.ClientSession() as session:
            async with session.post(GROQ_API_URL, headers=headers, json=payload, timeout=20) as response:
                if response.status == 200:
                    data = await response.json()
                    return data["choices"][0]["message"]["content"].strip()
                else:
                    error_text = await response.text()
                    logger.error(f"Groq API Hatası: {response.status} - {error_text}")
                    return None
    except Exception as e:
        logger.error(f"AI Yorumlama sırasında istisna oluştu: {e}")
        return None

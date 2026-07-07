import pandas as pd
import time
import random
from data_fetcher import TOP_50_COINS, exchange
from ai_analyzer import get_ai_decisions_batch

LIMIT = 131 

def run_analysis():
    print("Kaçırılan Kazanan İşlemler (Missed Wins) Analizi...\n")
    all_data = {}
    
    # Yeni bir test seti oluşturalım
    selected_coins = random.sample(TOP_50_COINS, min(10, len(TOP_50_COINS)))
    print(f"Rastgele 10 coin: {selected_coins}")
    
    for symbol in selected_coins:
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, '15m', limit=LIMIT)
            if not ohlcv or len(ohlcv) < 50: continue
            
            df = pd.DataFrame(ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
            df['timestamp'] = pd.to_datetime(df['timestamp'], unit='ms')
            df.ta.rsi(length=14, append=True)
            df.ta.macd(fast=12, slow=26, signal=9, append=True)
            df.ta.atr(length=14, append=True)
            
            all_data[symbol] = df
            time.sleep(0.1)
        except Exception as e:
            import logging
            logging.error(f"Beklenmeyen hata: {e}")

    try:
        btc_ohlcv = exchange.fetch_ohlcv('BTC/USDT', '15m', limit=LIMIT)
        btc_df = pd.DataFrame(btc_ohlcv, columns=['timestamp', 'open', 'high', 'low', 'close', 'volume'])
        btc_df['timestamp'] = pd.to_datetime(btc_df['timestamp'], unit='ms')
        btc_df.ta.ema(length=50, append=True)
    except Exception as e:
        print(f"[BTC veri çekme hatası] {e}")
        btc_df = None

    if not all_data: return
    
    first_coin = list(all_data.keys())[0]
    timestamps = all_data[first_coin]['timestamp'].dropna().tolist()[35:]
    
    all_filtered_data = []
    
    for ts in timestamps:
        for symbol, df in all_data.items():
            idx = df[df['timestamp'] == ts].index
            if len(idx) == 0: continue
            idx = idx[0]
            if idx < 2: continue
            
            prev_closed = df.iloc[idx - 2]
            last_closed = df.iloc[idx - 1]
            row = df.iloc[idx]
            
            current_price = row['close']
            rsi_val = last_closed['RSI_14']
            prev_rsi_val = prev_closed['RSI_14']
            macd_val = last_closed['MACD_12_26_9']
            macds_val = last_closed['MACDs_12_26_9']
            atr_val = last_closed['ATRr_14'] if 'ATRr_14' in last_closed else (current_price * 0.02)
            
            if pd.isna(rsi_val) or pd.isna(macd_val) or pd.isna(prev_rsi_val): continue
                
            btc_trend = "Neutral"
            if btc_df is not None:
                btc_row = btc_df[btc_df['timestamp'] == ts]
                if len(btc_row) > 0:
                    btc_price = btc_row.iloc[0]['close']
                    btc_ema50 = btc_row.iloc[0]['EMA_50']
                    if not pd.isna(btc_ema50):
                        btc_trend = "Bullish" if btc_price > btc_ema50 else "Bearish"
                
            if rsi_val < 35 or rsi_val > 65:
                trend = "Bullish" if idx >= 20 and current_price > df.iloc[idx - 20]['close'] else "Bearish"
                all_filtered_data.append({
                    "timestamp": str(ts),
                    "ticker": symbol,
                    "current_price": current_price,
                    "rsi": round(rsi_val, 2),
                    "prev_rsi": round(prev_rsi_val, 2),
                    "atr": round(atr_val, 4),
                    "macd": round(macd_val, 4),
                    "macd_signal": round(macds_val, 4),
                    "coin_trend": trend,
                    "btc_trend": btc_trend
                })

    # Kör Strateji Sinyalleri Oluştur
    blind_signals = []
    for d in all_filtered_data:
        signal = "AL" if d['rsi'] < 50 else "SAT"
        sl_dist = 1.5 * d['atr']
        tp_dist = 1.5 * d['atr']
        blind_signals.append({
            "timestamp": d['timestamp'],
            "ticker": d['ticker'],
            "signal": signal,
            "sl": d['current_price'] - sl_dist if signal == "AL" else d['current_price'] + sl_dist,
            "tp": d['current_price'] + tp_dist if signal == "AL" else d['current_price'] - tp_dist,
            "original_data": d
        })
        
    print("Yapay zekaya danışılıyor...")
    ai_decisions_raw = get_ai_decisions_batch(all_filtered_data, None)
    
    # AI'nın onayladığı (AL/SAT) kombinasyonları
    ai_approved_keys = set()
    for d in ai_decisions_raw:
        if d.get("signal") in ["AL", "SAT"]:
            ai_approved_keys.add(f"{d.get('timestamp')}_{d.get('ticker')}")

    def simulate_strategy(signals, filter_keys=None):
        active = []
        closed = []
        sig_by_time = {}
        for s in signals:
            key = f"{s['timestamp']}_{s['ticker']}"
            if filter_keys is not None and key not in filter_keys:
                continue
            ts_parsed = pd.to_datetime(s['timestamp'])
            if ts_parsed not in sig_by_time: sig_by_time[ts_parsed] = []
            sig_by_time[ts_parsed].append(s)
            
        for ts in timestamps:
            current_prices_dict = {}
            for symbol, df in all_data.items():
                row_idx = df[df['timestamp'] == ts].index
                if len(row_idx) == 0: continue
                idx = row_idx[0]
                row = df.iloc[idx]
                current_prices_dict[symbol] = {'close': row['close'], 'high': row['high'], 'low': row['low']}
                
                for t in active:
                    if t['ticker'] == symbol and t['status'] == 'ACTIVE':
                        low_price = row['low']
                        high_price = row['high']
                        if t['signal'] == 'AL':
                            if low_price <= t['sl']:
                                t['status'] = 'CLOSED_SL'
                                closed.append(t)
                            elif high_price >= t['tp']:
                                t['status'] = 'CLOSED_TP'
                                closed.append(t)
                        elif t['signal'] == 'SAT':
                            if high_price >= t['sl']:
                                t['status'] = 'CLOSED_SL'
                                closed.append(t)
                            elif low_price <= t['tp']:
                                t['status'] = 'CLOSED_TP'
                                closed.append(t)
            
            active = [t for t in active if t['status'] == 'ACTIVE']
            
            for s in sig_by_time.get(ts, []):
                ticker = s['ticker']
                if any(t['ticker'] == ticker and t['status'] == 'ACTIVE' for t in active): continue
                if ticker not in current_prices_dict: continue
                active.append({
                    'timestamp': s['timestamp'],
                    'ticker': ticker,
                    'signal': s['signal'],
                    'sl': float(s['sl']),
                    'tp': float(s['tp']),
                    'status': 'ACTIVE',
                    'original_data': s['original_data']
                })
        
        return closed

    blind_closed = simulate_strategy(blind_signals, filter_keys=None)
    
    # Kaçırılan Win'leri bul
    missed_wins = []
    for t in blind_closed:
        if t['status'] == 'CLOSED_TP':
            key = f"{t['timestamp']}_{t['ticker']}"
            if key not in ai_approved_keys:
                missed_wins.append(t)
                
    print(f"\nToplam {len(missed_wins)} adet kazanan işlem yapay zeka tarafından reddedildi.")
    
    if missed_wins:
        print("\n=== KAÇIRILAN KAZANAN İŞLEMLERİN ORTAK ÖZELLİKLERİ ===")
        # İstatistikler
        btc_trends = {}
        coin_trends = {}
        rsi_diffs = []
        macds = []
        
        for w in missed_wins:
            d = w['original_data']
            bt = d['btc_trend']
            ct = d['coin_trend']
            btc_trends[bt] = btc_trends.get(bt, 0) + 1
            coin_trends[ct] = coin_trends.get(ct, 0) + 1
            rsi_diffs.append(abs(d['rsi'] - d['prev_rsi']))
            macds.append(d['macd'])
            
        print("BTC Trend Dağılımı:", btc_trends)
        print("Coin Trend Dağılımı:", coin_trends)
        
        avg_rsi_diff = sum(rsi_diffs) / len(rsi_diffs)
        print(f"Ortalama RSI Değişimi (İvme): {round(avg_rsi_diff, 2)}")
        
        # 3 örnek göster
        print("\n--- Örnek 3 Reddedilen Fırsat ---")
        for i, w in enumerate(missed_wins[:3]):
            d = w['original_data']
            print(f"{i+1}. {d['ticker']} ({w['signal']}) - Zaman: {d['timestamp']}")
            print(f"   BTC Trend: {d['btc_trend']}, Coin Trend: {d['coin_trend']}")
            print(f"   RSI: {d['rsi']} (Önceki: {d['prev_rsi']}), MACD: {d['macd']}")

if __name__ == "__main__":
    run_analysis()

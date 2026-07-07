import os
import sys

# Reconfigure stdout to support Turkish and emoji characters on Windows terminals
if hasattr(sys.stdout, 'reconfigure'):
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except Exception:
        pass

import pandas as pd
import yfinance as yf
import json
import warnings
from io import StringIO
from datetime import datetime
warnings.filterwarnings('ignore')

# Set working directory to project root
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

# Monkeypatch data sources to prevent live API calls during backtest
import data_sources
data_sources.get_funding_rate = lambda symbol: 0.01
data_sources.fetch_crypto_oi_crash = lambda symbol: False
data_sources.get_btc_dominance_trend = lambda: "DOWN"
data_sources.check_token_unlocks = lambda symbol: False
data_sources.check_btc_not_pumping = lambda: True
data_sources.is_bist_open = lambda: False
data_sources.get_btc_status = lambda: True
data_sources._get_btc_htf_bias = lambda: 1

import config
import conviction_scorer
import strategies.crypto

# Custom mock function for signal validity with adjustable threshold
def mock_is_crypto_signal_valid(sig, rel_vol_4h, ema_diff_pct, cmf_4h, min_score_limit=50):
    score = sig.get('conviction_score', 0)
    direction = "LONG" if sig.get('signal') == "AL" else "SHORT"
    if score < min_score_limit:
        return False
    if rel_vol_4h < 0.7:
        return False
    if ema_diff_pct > 8.0:
        return False
    if direction == 'LONG' and cmf_4h < -0.10:
        return False
    if direction == 'SHORT' and cmf_4h > 0.10:
        return False
    return True

COINS = [
    "BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT", "XRP/USDT", 
    "ADA/USDT", "AVAX/USDT", "LINK/USDT", "DOT/USDT", "TRX/USDT"
]

YF_MAP = {
    "BTC/USDT": "BTC-USD",
    "ETH/USDT": "ETH-USD",
    "SOL/USDT": "SOL-USD",
    "BNB/USDT": "BNB-USD",
    "XRP/USDT": "XRP-USD",
    "ADA/USDT": "ADA-USD",
    "AVAX/USDT": "AVAX-USD",
    "LINK/USDT": "LINK-USD",
    "DOT/USDT": "DOT-USD",
    "TRX/USDT": "TRX-USD"
}

CACHE_FILE = "backtest_data_cache.json"
if os.path.exists(CACHE_FILE):
    print("Loading data from local cache...")
    with open(CACHE_FILE, 'r') as f:
        raw_cache = json.load(f)
    data_cache = {}
    for coin, timeframes in raw_cache.items():
        data_cache[coin] = {}
        for tf, df_json in timeframes.items():
            data_cache[coin][tf] = pd.read_json(StringIO(df_json), orient='split')
else:
    print("Downloading historical data from yfinance...")
    data_cache = {}
    for coin in COINS:
        yf_ticker = YF_MAP[coin]
        print(f"Downloading {coin} ({yf_ticker})...")
        df_1d = yf.download(yf_ticker, period="12mo", interval="1d", progress=False)
        df_1h = yf.download(yf_ticker, period="1mo", interval="1h", progress=False)
        
        df_1d = data_sources.clean_yf_df(df_1d)
        df_1h = data_sources.clean_yf_df(df_1h)
        
        # Resample to 4h
        df_4h = df_1h.resample('4h').agg({
            'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'
        }).dropna()
        
        data_cache[coin] = {
            '1d': df_1d,
            '4h': df_4h,
            '1h': df_1h
        }
    print("Saving data to cache...")
    raw_cache = {}
    for coin, timeframes in data_cache.items():
        raw_cache[coin] = {}
        for tf, df in timeframes.items():
            raw_cache[coin][tf] = df.to_json(orient='split', date_format='iso')
    with open(CACHE_FILE, 'w') as f:
        json.dump(raw_cache, f)
    print("Data cached successfully!")

def run_simulation(min_score, adx_start, adx_mult):
    # Determine thresholds based on input min_score
    if min_score == 50:
        strong, medium, watch = 75.0, 60.0, 50.0
    elif min_score == 70:
        strong, medium, watch = 70.0, 70.0, 70.0
    elif min_score == 60:
        strong, medium, watch = 70.0, 60.0, 60.0
    else:
        strong, medium, watch = 75.0, 60.0, 50.0
        
    config.GLOBAL_STRONG_CONVICTION_SCORE = strong
    config.GLOBAL_MEDIUM_CONVICTION_SCORE = medium
    config.GLOBAL_MIN_CONVICTION_SCORE = watch
    
    conviction_scorer.THRESHOLD_STRONG = strong
    conviction_scorer.THRESHOLD_MEDIUM = medium
    conviction_scorer.THRESHOLD_WATCH = watch
    
    config.REGIME_THRESHOLDS_BULL = {"STRONG": strong, "MEDIUM": medium, "WATCH": watch}
    config.REGIME_THRESHOLDS_NEUTRAL = {"STRONG": strong, "MEDIUM": medium, "WATCH": watch}
    config.REGIME_THRESHOLDS_BEAR = {"STRONG": strong + 5.0, "MEDIUM": medium, "WATCH": watch}
    
    conviction_scorer.SOFT_ADX_MATURITY_START = adx_start
    conviction_scorer.SOFT_ADX_MATURITY_MULT = adx_mult
    
    # Patch signal validation limits
    strategies.crypto._is_crypto_signal_valid = lambda sig, rv, ed, cmf: mock_is_crypto_signal_valid(sig, rv, ed, cmf, min_score_limit=watch)

    trades = []
    
    for coin in COINS:
        df_1d_full = data_cache[coin]['1d']
        df_4h_full = data_cache[coin]['4h']
        df_1h_full = data_cache[coin]['1h']
        
        # Simulate last 7 days
        end_time = df_4h_full.index[-1]
        start_time = end_time - pd.Timedelta(days=7)
        
        eval_indices = df_4h_full[(df_4h_full.index >= start_time) & (df_4h_full.index <= end_time)].index
        
        for t in eval_indices:
            df_1d_slice = df_1d_full[df_1d_full.index.date <= t.date()]
            df_4h_slice = df_4h_full[df_4h_full.index <= t]
            df_1h_slice = df_1h_full[df_1h_full.index <= t]
            
            if len(df_1d_slice) < 50 or len(df_4h_slice) < 20:
                continue
                
            signals = strategies.crypto.analyze_strategies_crypto(
                coin, df_1d_slice, df_4h_slice, btc_ok=True, btc_sniper_bias=1, df_1h_sniper=df_1h_slice
            )
            
            for s in signals:
                entry_time = t
                entry_price = df_4h_slice['close'].iloc[-1]
                sl = s['sl']
                tp = s['tp']
                direction = s['signal']
                strategy = s['strategy']
                score = s['conviction_score']
                
                # Check future outcome in 1h resolution
                forward_1h = df_1h_full[df_1h_full.index > entry_time]
                outcome = "HOLD"
                pnl = 0.0
                exit_price = entry_price
                exit_time = None
                
                for fut_t, row in forward_1h.iterrows():
                    high = row['high']
                    low = row['low']
                    
                    if direction == 'AL':  # LONG
                        if low <= sl:
                            outcome = "SL"
                            exit_price = sl
                            exit_time = fut_t
                            pnl = (sl - entry_price) / entry_price * 100
                            break
                        elif high >= tp:
                            outcome = "TP"
                            exit_price = tp
                            exit_time = fut_t
                            pnl = (tp - entry_price) / entry_price * 100
                            break
                    else:  # SHORT
                        if high >= sl:
                            outcome = "SL"
                            exit_price = sl
                            exit_time = fut_t
                            pnl = (entry_price - sl) / entry_price * 100
                            break
                        elif low <= tp:
                            outcome = "TP"
                            exit_price = tp
                            exit_time = fut_t
                            pnl = (entry_price - tp) / entry_price * 100
                            break
                
                if outcome == "HOLD" and not forward_1h.empty:
                    last_row = forward_1h.iloc[-1]
                    exit_price = last_row['close']
                    exit_time = forward_1h.index[-1]
                    if direction == 'AL':
                        pnl = (exit_price - entry_price) / entry_price * 100
                    else:
                        pnl = (entry_price - exit_price) / entry_price * 100
                
                # Fee deduction (0.1% commission + 0.1% slippage = 0.2%)
                pnl_net = pnl - 0.2
                
                trades.append({
                    'symbol': coin,
                    'time': entry_time,
                    'direction': direction,
                    'strategy': strategy,
                    'score': score,
                    'entry_price': entry_price,
                    'sl': sl,
                    'tp': tp,
                    'exit_price': exit_price,
                    'exit_time': exit_time,
                    'outcome': outcome,
                    'pnl_net': pnl_net
                })
                
    return trades

def print_results(name, trades):
    print(f"\n==================================================")
    print(f"--- A/B TEST SONUCLARI - {name.upper()} ---")
    print(f"==================================================")
    df = pd.DataFrame(trades)
    if df.empty:
        print("Sinyal üretilmedi.")
        return
        
    total_signals = len(df)
    tp_count = len(df[df['outcome'] == 'TP'])
    sl_count = len(df[df['outcome'] == 'SL'])
    hold_count = len(df[df['outcome'] == 'HOLD'])
    
    win_rate = tp_count / (tp_count + sl_count) * 100 if (tp_count + sl_count) > 0 else 0.0
    total_pnl = df['pnl_net'].sum()
    avg_pnl = df['pnl_net'].mean()
    
    print(f"Toplam Sinyal Sayısı: {total_signals}")
    print(f"TP (Kâr Al) Sayısı  : {tp_count}")
    print(f"SL (Zarar Kes) Sayısı: {sl_count}")
    print(f"HOLD (Açık Kalan)   : {hold_count}")
    print(f"Kazanma Oranı (W/R) : %{win_rate:.2f}")
    print(f"Toplam PnL (Net)    : %{total_pnl:.2f}")
    print(f"Ortalama PnL (Net)  : %{avg_pnl:.2f}")
    print("\nDetaylı Sinyal Tablosu:")
    print(df[['symbol', 'time', 'direction', 'score', 'outcome', 'pnl_net']].to_string())

if __name__ == "__main__":
    # Scenario 1: Control (Strong=75, Medium=60, Watch=50, original ADX limits)
    print("\nRunning Scenario 1: Control...")
    control_trades = run_simulation(min_score=50, adx_start=40.0, adx_mult=3.0)
    print_results("Kontrol (Mevcut Durum)", control_trades)
    
    # Scenario 2: Group A (Watch=70, Medium=70, Strong=75, original ADX limits)
    print("\nRunning Scenario 2: Group A...")
    group_a_trades = run_simulation(min_score=70, adx_start=40.0, adx_mult=3.0)
    print_results("A Grubu (Daha Az Hafifçe)", group_a_trades)
    
    # Scenario 3: Group B (Watch=60, Medium=60, Strong=70, relaxed ADX limits)
    print("\nRunning Scenario 3: Group B...")
    group_b_trades = run_simulation(min_score=60, adx_start=45.0, adx_mult=1.5)
    print_results("B Grubu (Hafifçe)", group_b_trades)

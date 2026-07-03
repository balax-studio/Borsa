import yfinance as yf
import pandas as pd
import numpy as np
import ta
import warnings
import sys
import os

warnings.filterwarnings('ignore')
if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# To import existing modules
sys.path.append(os.path.dirname(os.path.abspath(__file__)))
from config import TOP_CRYPTO_SCAN
import strategies.crypto as strat_crypto

CRYPTO_SYMBOLS = [sym.replace("/", "").replace("USDT", "-USD") for sym in TOP_CRYPTO_SCAN[:25]]

def clean_yf_df(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df = df.ffill().bfill().dropna()
    df.columns = [c.lower() for c in df.columns]
    return df

def fetch_data(symbols):
    print(f"Fetching 1h data for {symbols}...")
    symbol_data = {}
    for sym in symbols:
        raw = yf.download(sym, start="2026-01-01", end="2026-06-30", interval="1h", progress=False)
        df = clean_yf_df(raw)
        if df.empty: continue
        
        df_4h = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        if len(df_4h) < 100: continue
        
        # Calculate indicators needed by strategies
        df_4h['RSI_14'] = ta.momentum.RSIIndicator(df_4h['close'], window=14).rsi()
        df_4h['EMA_8'] = ta.trend.EMAIndicator(df_4h['close'], window=8).ema_indicator()
        df_4h['EMA_20'] = ta.trend.EMAIndicator(df_4h['close'], window=20).ema_indicator()
        df_4h['EMA_21'] = ta.trend.EMAIndicator(df_4h['close'], window=21).ema_indicator()
        df_4h['EMA_50'] = ta.trend.EMAIndicator(df_4h['close'], window=50).ema_indicator()
        df_4h['ADX_14'] = ta.trend.ADXIndicator(df_4h['high'], df_4h['low'], df_4h['close'], window=14).adx()
        df_4h['OBV'] = ta.volume.OnBalanceVolumeIndicator(df_4h['close'], df_4h['volume']).on_balance_volume()
        df_4h['ATRr_14'] = ta.volatility.AverageTrueRange(df_4h['high'], df_4h['low'], df_4h['close'], window=14).average_true_range()
        
        # Deep research indicators for filtering later
        df_4h['CMF'] = ta.volume.ChaikinMoneyFlowIndicator(df_4h['high'], df_4h['low'], df_4h['close'], df_4h['volume'], window=20).chaikin_money_flow()
        vortex = ta.trend.VortexIndicator(df_4h['high'], df_4h['low'], df_4h['close'], window=14)
        df_4h['Vortex_Diff'] = vortex.vortex_indicator_pos() - vortex.vortex_indicator_neg()
        
        df_4h['vol_sma_20'] = df_4h['volume'].rolling(window=20).mean()
        df_4h['Relative_Volume'] = df_4h['volume'] / df_4h['vol_sma_20']
        
        df_1d = df.resample('1d').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        df_1d['EMA_50'] = ta.trend.EMAIndicator(df_1d['close'], window=50).ema_indicator()
        
        df_4h = df_4h.fillna(0)
        symbol_data[sym] = {"4h": df_4h, "1d": df_1d}
    return symbol_data

def run_live_backtest(symbol_data):
    trades = []
    
    # Get list of functions
    func_names = [f for f in dir(strat_crypto) if f.startswith('_check_crypto_') and callable(getattr(strat_crypto, f))]
    
    for sym, data in symbol_data.items():
        df_4h = data["4h"]
        df_1d = data["1d"]
        
        for i in range(100, len(df_4h)-1):
            current_date = df_4h.index[i]
            historical_4h = df_4h.iloc[i-100:i+1]  # Limit size to 100 rows to speed up calculation!
            last_4h = historical_4h.iloc[-1]
            
            # Find closest 1d data
            past_1d = df_1d[df_1d.index <= current_date]
            if past_1d.empty: continue
            last_1d = past_1d.iloc[-1]
            
            ctx = {
                "symbol": sym,
                "current_price": last_4h['close'],
                "last_4h": last_4h,
                "last_1d": last_1d,
                "df_4h": historical_4h,
                "df_1d": past_1d,
                "btc_ok": True,
                "btc_sniper_bias": "BULLISH",
                "dynamic_atr_mult": 1.5,
                "time_score": 0.5,
                "volume_score": 0.5
            }
            
            # To speed up, let's just evaluate a few major ones or all of them
            for fname in func_names:
                func = getattr(strat_crypto, fname)
                try:
                    # Some functions expect ctx_1h, let's skip those or adapt
                    if '1h' in fname: continue 
                    signals = func(ctx)
                    if signals:
                        for sig in signals:
                            # Usually returns a dict or tuple
                            if isinstance(sig, dict):
                                sig_type = sig.get("strategy", "UNKNOWN")
                                sig_dir = "LONG" if sig.get("signal") == "AL" else "SHORT"
                                sl = sig.get("sl", last_4h['close'] * 0.95)
                                tp = sig.get("tp", last_4h['close'] * 1.05)
                            elif isinstance(sig, tuple):
                                sig_type = sig[0]
                                sig_dir = sig[1]
                                atr = last_4h['ATRr_14'] if last_4h['ATRr_14'] > 0 else (last_4h['close'] * 0.02)
                                sl = last_4h['close'] - (atr*1.5) if sig_dir == "LONG" else last_4h['close'] + (atr*1.5)
                                tp = last_4h['close'] + (atr*3) if sig_dir == "LONG" else last_4h['close'] - (atr*3)
                            else:
                                continue
                                
                            # Check forward
                            result = None
                            for j in range(i+1, len(df_4h)):
                                if sig_dir == "LONG":
                                    if df_4h['low'].iloc[j] <= sl: result = "LOSS"; break
                                    elif df_4h['high'].iloc[j] >= tp: result = "WIN"; break
                                else:
                                    if df_4h['high'].iloc[j] >= sl: result = "LOSS"; break
                                    elif df_4h['low'].iloc[j] <= tp: result = "WIN"; break
                            
                            if result:
                                trades.append({
                                    'Symbol': sym, 'Strategy': sig_type, 'Signal': sig_dir, 'Result': result,
                                    'Date': current_date, 'RSI': last_4h['RSI_14'], 'ADX': last_4h['ADX_14'],
                                    'Relative_Volume': last_4h['Relative_Volume'], 'CMF': last_4h['CMF'],
                                    'Vortex_Diff': last_4h['Vortex_Diff'], 'ATR_Pct': (last_4h['ATRr_14'] / last_4h['close']) * 100
                                })
                except Exception as e:
                    print(f"Error in {fname} for {sym} at {current_date}: {e}")
    return pd.DataFrame(trades)

def find_best_filter(df, strategy_name, metric, is_greater_than=True):
    wins = df[df['Result'] == 'WIN']
    losses = df[df['Result'] == 'LOSS']
    if len(wins) == 0 or len(losses) == 0: return None
        
    best_threshold, best_pnl = 0, (len(wins) * 2.0) - len(losses)
    best_filtered_wins, best_filtered_losses = len(wins), len(losses)
    
    min_val, max_val = df[metric].min(), df[metric].max()
    step = (max_val - min_val) / 50
    if step == 0: return None
    
    for thresh in np.arange(min_val, max_val, step):
        if is_greater_than:
            filt_wins, filt_losses = len(wins[wins[metric] > thresh]), len(losses[losses[metric] > thresh])
        else:
            filt_wins, filt_losses = len(wins[wins[metric] < thresh]), len(losses[losses[metric] < thresh])
            
        pnl = (filt_wins * 2.0) - filt_losses
        if pnl > best_pnl and filt_wins >= len(wins) * 0.3:
            best_pnl, best_threshold = pnl, thresh
            best_filtered_wins, best_filtered_losses = filt_wins, filt_losses
            
    if best_pnl > ((len(wins) * 2.0) - len(losses)):
        return {
            'strategy': strategy_name, 'metric': metric, 'condition': '>' if is_greater_than else '<',
            'threshold': best_threshold, 'orig_pnl': (len(wins) * 2.0) - len(losses), 'new_pnl': best_pnl,
            'orig_wins': len(wins), 'orig_losses': len(losses), 'new_wins': best_filtered_wins,
            'new_losses': best_filtered_losses, 'pnl_diff': best_pnl - ((len(wins) * 2.0) - len(losses))
        }
    return None

def main():
    print("Starting Live Signals Optimization...")
    data = fetch_data(CRYPTO_SYMBOLS)
    if not data:
        print("No data fetched.")
        return
        
    print("Running backtest on live strategies...")
    df = run_live_backtest(data)
    if df.empty:
        print("No signals generated.")
        return
        
    metrics = ['RSI', 'ADX', 'Relative_Volume', 'ATR_Pct', 'CMF', 'Vortex_Diff']
    report = "# LIVE CRYPTO SIGNALS OPTIMIZATION REPORT\n"
    
    for strategy in df['Strategy'].unique():
        s_df = df[df['Strategy'] == strategy]
        strat_improvements = []
        for metric in metrics:
            for is_greater in [True, False]:
                res = find_best_filter(s_df, strategy, metric, is_greater)
                if res: strat_improvements.append(res)
                
        strat_improvements.sort(key=lambda x: x['pnl_diff'], reverse=True)
        wins, losses = len(s_df[s_df['Result'] == 'WIN']), len(s_df[s_df['Result'] == 'LOSS'])
        orig_pnl = (wins * 2.0) - losses
        
        report += f"\n## {strategy}\n"
        report += f"- **A-Test (Original):** {wins}W / {losses}L (PnL: {orig_pnl:.2f}R)\n"
        
        if strat_improvements:
            best = strat_improvements[0]
            report += f"- **B-Test (Filtered):** {best['new_wins']}W / {best['new_losses']}L (PnL: {best['new_pnl']:.2f}R)\n"
            report += f"- **Golden Filter:** `{best['metric']} {best['condition']} {best['threshold']:.4f}` (+{best['pnl_diff']:.2f}R Improvement)\n"
        else:
            report += f"- **No mathematical improvement found** that retains >30% win rate.\n"
            
    with open("live_optimization_report.md", "w", encoding="utf-8") as f:
        f.write(report)
    print("Optimization completed! Check live_optimization_report.md")

if __name__ == "__main__":
    main()

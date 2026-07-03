
import yfinance as yf
import pandas as pd
import ta
import numpy as np
import warnings
import sys
from config import TOP_CRYPTO_SCAN

warnings.filterwarnings('ignore')

if sys.stdout.encoding != "utf-8":
    sys.stdout.reconfigure(encoding="utf-8")

# Markets
BIST_SYMBOLS = ["THYAO.IS", "EREGL.IS", "TUPRS.IS", "KCHOL.IS", "ASELS.IS"]
CRYPTO_SYMBOLS = [sym.replace("/", "").replace("USDT", "-USD") for sym in TOP_CRYPTO_SCAN[:25]] # Top 25 to avoid rate limits during loop
PERIOD = "3mo" 

def clean_yf_df(df):
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
    df = df.ffill().bfill().dropna()
    df.columns = [c.lower() for c in df.columns]
    return df

def fetch_data(symbols, interval="1h"):
    print(f"Fetching {interval} data for {symbols}...")
    symbol_data = {}
    for sym in symbols:
        raw = yf.download(sym, start="2026-01-01", end="2026-06-30", interval=interval, progress=False)
        df = clean_yf_df(raw)
        if df.empty: continue
        
        df_4h = df.resample('4h').agg({'open': 'first', 'high': 'max', 'low': 'min', 'close': 'last', 'volume': 'sum'}).dropna()
        df_4h['ATRr_14'] = ta.volatility.AverageTrueRange(df_4h['high'], df_4h['low'], df_4h['close'], window=14).average_true_range()
        df_4h['RSI_14'] = ta.momentum.RSIIndicator(df_4h['close'], window=14).rsi()
        df_4h['EMA_8'] = ta.trend.EMAIndicator(df_4h['close'], window=8).ema_indicator()
        df_4h['EMA_21'] = ta.trend.EMAIndicator(df_4h['close'], window=21).ema_indicator()
        df_4h['EMA_50'] = ta.trend.EMAIndicator(df_4h['close'], window=50).ema_indicator()
        df_4h['ADX_14'] = ta.trend.ADXIndicator(df_4h['high'], df_4h['low'], df_4h['close'], window=14).adx()
        df_4h['OBV'] = ta.volume.OnBalanceVolumeIndicator(df_4h['close'], df_4h['volume']).on_balance_volume()
        
        bb = ta.volatility.BollingerBands(df_4h['close'], window=20, window_dev=2)
        df_4h['BBU'] = bb.bollinger_hband()
        df_4h['BBL'] = bb.bollinger_lband()
        df_4h['BBM'] = bb.bollinger_mavg()
        
        # New Deep Research Metrics
        df_4h['CMF'] = ta.volume.ChaikinMoneyFlowIndicator(df_4h['high'], df_4h['low'], df_4h['close'], df_4h['volume'], window=20).chaikin_money_flow()
        vortex = ta.trend.VortexIndicator(df_4h['high'], df_4h['low'], df_4h['close'], window=14)
        df_4h['Vortex_Diff'] = vortex.vortex_indicator_pos() - vortex.vortex_indicator_neg()
        
        # Choppiness Index manual calculation
        hl = df_4h['high'] - df_4h['low']
        hc = (df_4h['high'] - df_4h['close'].shift(1)).abs()
        lc = (df_4h['low'] - df_4h['close'].shift(1)).abs()
        tr = pd.concat([hl, hc, lc], axis=1).max(axis=1)
        atr_sum = tr.rolling(14).sum()
        high_max = df_4h['high'].rolling(14).max()
        low_min = df_4h['low'].rolling(14).min()
        df_4h['CHOP'] = 100 * np.log10(atr_sum / (high_max - low_min)) / np.log10(14)
        df_4h['CHOP'] = df_4h['CHOP'].fillna(0)
        df_4h['CMF'] = df_4h['CMF'].fillna(0)
        df_4h['Vortex_Diff'] = df_4h['Vortex_Diff'].fillna(0)
        
        df_4h['vol_sma_20'] = df_4h['volume'].rolling(window=20).mean()
        df_4h['Relative_Volume'] = df_4h['volume'] / df_4h['vol_sma_20']
        
        # Approximate 1D indicators
        df_4h['1D_EMA_20'] = df_4h['close'].ewm(span=20*6, adjust=False).mean()
        df_4h['1D_EMA_50'] = df_4h['close'].ewm(span=50*6, adjust=False).mean()
        
        symbol_data[sym] = df_4h.dropna()
        
    return symbol_data

def find_best_filter(df, strategy_name, metric, is_greater_than=True):
    wins = df[df['Result'] == 'WIN']
    losses = df[df['Result'] == 'LOSS']
    
    if len(wins) == 0 or len(losses) == 0: return None
        
    best_threshold, best_pnl = 0, (len(wins) * 2.0) - len(losses)
    best_filtered_wins, best_filtered_losses = len(wins), len(losses)
    
    min_val, max_val = df[metric].min(), df[metric].max()
    step = (max_val - min_val) / 100
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

def run_backtest_for_market(symbol_data, market_name):
    trades = []
    
    for sym, df in symbol_data.items():
        highs, lows, closes, opens, volumes = df['high'].values, df['low'].values, df['close'].values, df['open'].values, df['volume'].values
        rsis = df['RSI_14'].values
        emas8, emas21, emas50 = df['EMA_8'].values, df['EMA_21'].values, df['EMA_50'].values
        emas1d20, emas1d50 = df['1D_EMA_20'].values, df['1D_EMA_50'].values
        atrs, rel_vols = df['ATRr_14'].values, df['Relative_Volume'].values
        adxs = df['ADX_14'].values if 'ADX_14' in df.columns else np.zeros(len(df))
        obvs = df['OBV'].values if 'OBV' in df.columns else np.zeros(len(df))
        
        cmfs = df['CMF'].values
        vortex_diffs = df['Vortex_Diff'].values
        chops = df['CHOP'].values
        
        bbw = np.zeros(len(df))
        bbu = np.zeros(len(df))
        bbl = np.zeros(len(df))
        bbu_cols = [c for c in df.columns if 'BBU' in c]
        bbl_cols = [c for c in df.columns if 'BBL' in c]
        bbm_cols = [c for c in df.columns if 'BBM' in c]
        if bbu_cols and bbl_cols and bbm_cols:
            bbu = df[bbu_cols[0]].values
            bbl = df[bbl_cols[0]].values
            bbw = (bbu - bbl) / df[bbm_cols[0]].values

        for i in range(50, len(df)-1):
            body = abs(closes[i] - opens[i])
            upper_wick = highs[i] - max(closes[i], opens[i])
            lower_wick = min(closes[i], opens[i]) - lows[i]
            signals = []
            
            # --- BIST STRATEGIES ---
            if market_name == "BIST":
                # BIST 1: Dip Hunter
                if rsis[i] < 35 and closes[i] > emas8[i] and closes[i] > opens[i] and rel_vols[i] > 1.2:
                    signals.append(("BIST 1: Dip Hunter", "LONG"))
                # BIST 2: Trend Following
                if adxs[i] > 25 and emas8[i] > emas21[i] and lows[i] <= emas21[i] and closes[i] > emas21[i]:
                    signals.append(("BIST 2: Trend Following", "LONG"))
                # BIST 3: Squeeze Breakout
                if bbw[i-1] < 0.05 and closes[i] > opens[i] and rel_vols[i] > 1.5:
                    signals.append(("BIST 3: Squeeze Breakout", "LONG"))
                # BIST 5: Vol Squeeze
                if bbw[i-1] < 0.04 and rel_vols[i] > 2.0:
                    if closes[i] > opens[i]: signals.append(("BIST 5: Vol Squeeze", "LONG"))
                    else: signals.append(("BIST 5: Vol Squeeze", "SHORT"))
                # BIST 8: OBV Accumulation
                if obvs[i] > obvs[i-5] and closes[i] > emas21[i] and rsis[i] > 50:
                    signals.append(("BIST 8: OBV Accumulation", "LONG"))

            # --- CRYPTO STRATEGIES ---
            elif market_name == "CRYPTO":
                # Variation A (Trend Following)
                if closes[i] > emas1d20[i] and adxs[i] > 25 and rel_vols[i] > 1.2 and chops[i] > 32.8259 and cmfs[i] < 0.3465:
                    signals.append(("Var A: Trend", "LONG"))
                # Variation B (Mean Reversion)
                if rsis[i] < 25 and closes[i] < bbl[i] and cmfs[i] > -0.1 and adxs[i] < 31.9953 and rel_vols[i] > 1.0164:
                    signals.append(("Var B: Reversion", "LONG"))
                # Variation C (Volatility Breakout)
                if bbw[i-1] < 0.05 and bbw[i] > 0.06 and closes[i] > bbu[i] and rel_vols[i] > 1.2 and (atrs[i] / closes[i] * 100) > 1.3008:
                    signals.append(("Var C: Breakout", "LONG"))
                # Variation D (Statistical Proxy)
                if vortex_diffs[i] > 0 and cmfs[i] > 0.1 and chops[i] < 38 and chops[i] > 34.5664 and cmfs[i] < 0.2722:
                    signals.append(("Var D: ML Proxy", "LONG"))

            # --- BEAR HUNTER STRATEGIES ---
            elif market_name == "BEAR_HUNTER":
                # Heavy Short
                if adxs[i] > 25 and rsis[i] > 60 and closes[i] < opens[i] and emas1d20[i] < emas1d50[i]:
                    signals.append(("Bear Hunter: Heavy Short", "SHORT"))
                # Darth Maul
                if upper_wick > (body * 3) and closes[i] < opens[i] and rel_vols[i] > 1.5:
                    signals.append(("Bear Hunter: Darth Maul", "SHORT"))

            for strat_name, sig_dir in signals:
                atr_val = atrs[i] if not np.isnan(atrs[i]) else (closes[i] * 0.02)
                atr_mult = 1.5
                sl = closes[i] - (atr_val * atr_mult) if sig_dir == "LONG" else closes[i] + (atr_val * atr_mult)
                tp = closes[i] + (atr_val * atr_mult * 2.0) if sig_dir == "LONG" else closes[i] - (atr_val * atr_mult * 2.0)
                    
                result = None
                for j in range(i+1, len(df)):
                    if sig_dir == "LONG":
                        if lows[j] <= sl: result = "LOSS"; break
                        elif highs[j] >= tp: result = "WIN"; break
                    else:
                        if highs[j] >= sl: result = "LOSS"; break
                        elif lows[j] <= tp: result = "WIN"; break
                            
                if result:
                    trades.append({
                        'Symbol': sym, 'Strategy': strat_name, 'Signal': sig_dir, 'Result': result,
                        'Date': df.index[i], 'RSI': rsis[i], 'ADX': adxs[i], 'Relative_Volume': rel_vols[i],
                        'ATR_Pct': (atrs[i] / closes[i]) * 100, 'EMA_Diff_Pct': abs(emas8[i] - emas21[i]) / closes[i] * 100,
                        'Candle_Body_Pct': (body / closes[i]) * 100, 'BB_Width': bbw[i],
                        'CMF': cmfs[i], 'Vortex_Diff': vortex_diffs[i], 'CHOP': chops[i]
                    })
    return pd.DataFrame(trades)

def process_market(market_name, symbols):
    data = fetch_data(symbols)
    if not data: return ""
    
    df = run_backtest_for_market(data, market_name)
    if df.empty: return f"No signals generated for {market_name}."
    
    metrics = ['RSI', 'ADX', 'Relative_Volume', 'ATR_Pct', 'EMA_Diff_Pct', 'Candle_Body_Pct', 'BB_Width', 'CMF', 'Vortex_Diff', 'CHOP']
    report = f"\\n\\n# {market_name} MARKET OPTIMIZATION REPORT\\n"
    
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
        
        report += f"\\n## {strategy}\\n"
        report += f"- **A-Test (Original):** {wins}W / {losses}L (PnL: {orig_pnl:.2f}R)\\n"
        
        if strat_improvements:
            best = strat_improvements[0]
            report += f"- **B-Test (Filtered):** {best['new_wins']}W / {best['new_losses']}L (PnL: {best['new_pnl']:.2f}R)\\n"
            report += f"- **Golden Filter:** `{best['metric']} {best['condition']} {best['threshold']:.4f}` (+{best['pnl_diff']:.2f}R Improvement)\\n"
        else:
            report += f"- **No mathematical improvement found** that retains >30% win rate.\\n"
            
    return report

def main():
    final_report = ""
    # final_report += process_market("BIST", BIST_SYMBOLS) # Skip BIST
    final_report += process_market("CRYPTO", CRYPTO_SYMBOLS)
    # final_report += process_market("BEAR_HUNTER", CRYPTO_SYMBOLS) # Skip Bear Hunter
    
    with open("universal_optimization_report.md", "w", encoding="utf-8") as f:
        f.write(final_report)
    print("Optimization completed! Check universal_optimization_report.md")

if __name__ == "__main__":
    main()

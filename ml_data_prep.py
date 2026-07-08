import yfinance as yf
import pandas as pd
import pandas_ta as ta
import numpy as np
from sklearn.model_selection import train_test_split
from sklearn.ensemble import RandomForestClassifier
from sklearn.metrics import accuracy_score, precision_score
import warnings
warnings.filterwarnings('ignore')

SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]

def calculate_features(df):
    if df.empty: return df
    
    df = df.copy()
    
    # Technical Indicators
    df.ta.rsi(length=14, append=True)
    df.ta.atr(length=14, append=True)
    df.ta.ema(length=20, append=True)
    
    # Feature 1: OFI Proxy (Volume Delta proxy based on candle shape)
    candle_range = df['High'] - df['Low']
    candle_range = candle_range.replace(0, 1e-5) # avoid div by zero
    body = df['Close'] - df['Open']
    df['OFI_Proxy'] = (body / candle_range) * df['Volume']
    
    # Feature 2: Volatility Z-Score
    df['ATR_Mean'] = df['ATRr_14'].rolling(50).mean()
    df['ATR_Std'] = df['ATRr_14'].rolling(50).std()
    df['Vol_Z_Score'] = (df['ATRr_14'] - df['ATR_Mean']) / df['ATR_Std'].replace(0, 1e-5)
    
    # Feature 3: Absorption (Wick volume proxy)
    upper_wick = df['High'] - df[['Open', 'Close']].max(axis=1)
    lower_wick = df[['Open', 'Close']].min(axis=1) - df['Low']
    df['Upper_Wick_Ratio'] = upper_wick / candle_range
    df['Lower_Wick_Ratio'] = lower_wick / candle_range
    
    # Breakout Signal Definition (Closing above 20-period high)
    df['Rolling_Max'] = df['High'].rolling(20).max().shift(1)
    df['Is_Breakout'] = (df['Close'] > df['Rolling_Max']).astype(int)
    
    return df

def apply_labels(df, forward_periods=5, tp_pct=0.01, sl_pct=0.01):
    # 0 = Success (Hit TP), 1 = Fakeout (Hit SL or didn't hit TP)
    df['Target_Label'] = np.nan
    
    for i in range(len(df) - forward_periods):
        if df['Is_Breakout'].iloc[i] == 1:
            entry_price = df['Close'].iloc[i]
            future_window = df.iloc[i+1:i+1+forward_periods]
            
            max_high = future_window['High'].max()
            min_low = future_window['Low'].min()
            
            if min_low <= entry_price * (1 - sl_pct):
                df.iloc[i, df.columns.get_loc('Target_Label')] = 1 # Fakeout
            elif max_high >= entry_price * (1 + tp_pct):
                df.iloc[i, df.columns.get_loc('Target_Label')] = 0 # Success
            else:
                df.iloc[i, df.columns.get_loc('Target_Label')] = 1 # Fakeout (Time based)
                
    return df

def prepare_data(interval, period):
    all_data = []
    for sym in SYMBOLS:
        df = yf.download(sym, period=period, interval=interval, progress=False)
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.droplevel(1)
        
        df = calculate_features(df)
        df = apply_labels(df)
        df = df.dropna()
        all_data.append(df)
        
    return pd.concat(all_data)

def evaluate_timeframe(df, name):
    features = ['RSI_14', 'OFI_Proxy', 'Vol_Z_Score', 'Upper_Wick_Ratio', 'Lower_Wick_Ratio']
    
    breakout_df = df[df['Is_Breakout'] == 1].copy()
    if len(breakout_df) < 50:
        print(f"[{name}] Not enough breakout samples: {len(breakout_df)}")
        return 0, 0, breakout_df
        
    X = breakout_df[features]
    y = breakout_df['Target_Label']
    
    X_train, X_test, y_train, y_test = train_test_split(X, y, test_size=0.3, random_state=42)
    
    model = RandomForestClassifier(n_estimators=100, max_depth=5, random_state=42)
    model.fit(X_train, y_train)
    
    preds = model.predict(X_test)
    acc = accuracy_score(y_test, preds)
    prec = precision_score(y_test, preds, zero_division=0)
    
    print(f"--- {name} Benchmark ---")
    print(f"Total Breakouts: {len(breakout_df)}")
    print(f"Fakeout Ratio: {y.mean():.2%}")
    print(f"Model Accuracy (Predicting Fakeout): {acc:.2%}")
    print(f"Model Precision (Fakeout class): {prec:.2%}\n")
    
    return acc, prec, breakout_df

if __name__ == "__main__":
    print("Downloading and processing 15m data...")
    df_15m = prepare_data("15m", "60d")
    
    print("Downloading and processing 1h data...")
    df_1h = prepare_data("1h", "730d") # longer period for 1h to get enough samples
    
    acc_15m, prec_15m, bd_15m = evaluate_timeframe(df_15m, "15m")
    acc_1h, prec_1h, bd_1h = evaluate_timeframe(df_1h, "1h")
    
    optimum = "15m" if acc_15m > acc_1h else "1h"
    optimum_df = bd_15m if optimum == "15m" else bd_1h
    
    print(f"==> Optimum Timeframe Algorithmically Chosen: {optimum}")
    
    optimum_df.to_csv('fakeout_dataset.csv')
    print("Dataset saved to fakeout_dataset.csv")

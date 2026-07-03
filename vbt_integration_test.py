
import pandas as pd
import yfinance as yf
from vbt_backtest import VectorBTBacktester

def test_vbt():
    print("Fetching data for test...")
    df = yf.download("BTC-USD", period="1y", interval="1d", progress=False)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.droplevel(1)
        
    print("Calculating moving averages...")
    df.ta.sma(length=50, append=True)
    df.ta.sma(length=200, append=True)
    
    # Golden Cross / Death Cross as entries/exits
    entries = (df['SMA_50'] > df['SMA_200']) & (df['SMA_50'].shift(1) <= df['SMA_200'].shift(1))
    exits = (df['SMA_50'] < df['SMA_200']) & (df['SMA_50'].shift(1) >= df['SMA_200'].shift(1))
    
    print("Running VectorBT Backtest...")
    backtester = VectorBTBacktester(df)
    stats = backtester.run_custom_signals(entries, exits, freq='1d')
    
    print("\n--- BACKTEST STATS ---")
    print(stats)
    print("----------------------\n")
    print("VectorBT Integration Test Successful!")

if __name__ == "__main__":
    test_vbt()

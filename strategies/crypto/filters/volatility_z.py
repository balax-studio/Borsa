import pandas as pd
import pandas_ta as ta

def calculate_volatility_z(df: pd.DataFrame, window: int = 50) -> pd.DataFrame:
    """
    Volatilite Rejimi (ATR tabanlı Z-Skoru) hesaplaması.
    Kırılımların piyasanın o anki olağanüstü volatilitesine göre 
    aşırı uçlarda gerçekleşip gerçekleşmediğini ölçer.
    """
    if df.empty:
        return df
        
    df = df.copy()
    
    # ATR hesaplanmamışsa hesapla
    atr_col = 'ATRr_14'
    if atr_col not in df.columns:
        df.ta.atr(length=14, append=True)
        
    if atr_col in df.columns:
        df['ATR_Mean'] = df[atr_col].rolling(window=window).mean()
        df['ATR_Std'] = df[atr_col].rolling(window=window).std()
        
        df['Vol_Z_Score'] = (df[atr_col] - df['ATR_Mean']) / df['ATR_Std'].replace(0, 1e-5)
    else:
        df['Vol_Z_Score'] = 0.0
        
    return df

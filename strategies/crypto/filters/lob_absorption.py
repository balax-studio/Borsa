import pandas as pd

def calculate_lob_absorption(df: pd.DataFrame) -> pd.DataFrame:
    """
    LOB (Limit Order Book) Emilim (Absorption) Filtresi.
    Fiyat kilit bir seviyeye geldiğinde, fiyatı iten mumların iğnelerindeki 
    (wick) hacim yoğunluğunu hesaplayarak büyük limit emirlerin trendi 
    durdurup durdurmadığını tespit eder.
    """
    if df.empty:
        return df
        
    df = df.copy()
    
    candle_range = df['high'] - df['low']
    candle_range = candle_range.replace(0, 1e-5) # sıfıra bölme hatasını engelle
    
    upper_wick = df['high'] - df[['open', 'close']].max(axis=1)
    lower_wick = df[['open', 'close']].min(axis=1) - df['low']
    
    df['Upper_Wick_Ratio'] = upper_wick / candle_range
    df['Lower_Wick_Ratio'] = lower_wick / candle_range
    
    return df

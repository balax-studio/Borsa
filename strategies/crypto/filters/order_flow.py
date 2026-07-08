import pandas as pd

def calculate_ofi_proxy(df: pd.DataFrame) -> pd.DataFrame:
    """
    Hacim Deltası Uyumsuzluğu (OFI) proxy hesaplaması.
    Fiyat yeni bir tepe/dip yaparken net alıcı/satıcı hacminin (Delta Volume) 
    zayıflamasını veya tersine dönmesini tespit eder.
    """
    if df.empty:
        return df
    
    df = df.copy()
    candle_range = df['high'] - df['low']
    candle_range = candle_range.replace(0, 1e-5) # sıfıra bölme hatasını engelle
    body = df['close'] - df['open']
    
    df['OFI_Proxy'] = (body / candle_range) * df['volume']
    
    return df

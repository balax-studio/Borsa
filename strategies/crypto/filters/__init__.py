import pandas as pd
from strategies.crypto.filters.order_flow import calculate_ofi_proxy
from strategies.crypto.filters.volatility_z import calculate_volatility_z
from strategies.crypto.filters.lob_absorption import calculate_lob_absorption

def add_fakeout_features(df: pd.DataFrame) -> pd.DataFrame:
    """
    Tüm ML (Fakeout) özelliklerini tek bir DataFrame'e ekler.
    """
    if df.empty:
        return df
        
    df = calculate_ofi_proxy(df)
    df = calculate_volatility_z(df, window=50)
    df = calculate_lob_absorption(df)
    
    return df

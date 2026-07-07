import os
import sys
import hashlib
import pickle
import logging
from datetime import datetime
import pandas as pd
import yfinance as yf

# Set up logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("yf_cache")

# 1. Determine cache directory
WORKSPACE_ROOT = os.path.dirname(os.path.abspath(__file__))
CACHE_DIR = os.path.join(WORKSPACE_ROOT, "yfinance_cache")
os.makedirs(CACHE_DIR, exist_ok=True)

# Save reference to the original download function
_original_download = yf.download

def _get_cache_key(tickers, args, kwargs):
    """
    Generate a unique MD5 hash based on tickers and download parameters.
    """
    normalized = []
    
    # Process tickers
    if isinstance(tickers, list):
        tickers_str = "-".join(sorted(tickers))
    else:
        tickers_str = str(tickers)
    normalized.append(f"tickers:{tickers_str}")
    
    # Process args
    for arg in args:
        normalized.append(str(arg))
        
    # Process kwargs (sorting keys to keep it deterministic)
    for k in sorted(kwargs.keys()):
        # Skip UI/network parameters that do not affect the data contents
        if k in ['progress', 'threads', 'timeout', 'proxy', 'show_errors']:
            continue
        normalized.append(f"{k}:{kwargs[k]}")
        
    # Heuristic check: is this a dynamic query? (e.g. dynamic start/end dates based on relative period)
    # If no start and end dates are specified (but 'period' is used), the window slides day-by-day.
    # Therefore, we append the current date to key to force expiration/refresh every day.
    start = kwargs.get('start') or (args[0] if len(args) > 0 and isinstance(args[0], str) and '-' in args[0] else None)
    end = kwargs.get('end') or (args[1] if len(args) > 1 and isinstance(args[1], str) and '-' in args[1] else None)
    
    if not start and not end:
        today_str = datetime.now().strftime("%Y-%m-%d")
        normalized.append(f"date:{today_str}")
        
    key_str = "|".join(normalized)
    md5_hash = hashlib.md5(key_str.encode('utf-8')).hexdigest()
    
    # Generate a safe, human-readable prefix for the cache file
    safe_prefix = "".join([c if c.isalnum() else "_" for c in tickers_str[:30]])
    return f"{safe_prefix}_{md5_hash}.parquet"

def _should_bypass_cache():
    """
    Determine if cache should be bypassed (e.g., live trading or system checks).
    """
    if not sys.argv or len(sys.argv) == 0:
        return False
    
    script_name = os.path.basename(sys.argv[0]).lower()
    
    # Live bot and scanner/monitoring scripts should never use cached data
    live_scripts = ["main.py", "run_scan_once.py", "auto_accept_bot.py", "monitoring.py", "check_system.py"]
    if script_name in live_scripts:
        return True
        
    return False

def cached_download(tickers, *args, **kwargs):
    """
    Patched version of yfinance.download that caches results on disk.
    """
    if _should_bypass_cache():
        # Live script: bypass cache entirely
        return _original_download(tickers, *args, **kwargs)
        
    cache_file = _get_cache_key(tickers, args, kwargs)
    cache_path = os.path.join(CACHE_DIR, cache_file)
    
    # Try loading from cache
    if os.path.exists(cache_path):
        try:
            df = pd.read_parquet(cache_path, engine='fastparquet')
            if isinstance(df, pd.DataFrame) and not df.empty:
                logger.info(f"[yf_cache] Loaded cached data for {tickers} from {cache_file}")
                return df
        except Exception as e:
            logger.warning(f"[yf_cache] Failed to load cache from {cache_file}: {e}. Downloading fresh data...")
            # If load fails, delete corrupt file
            try:
                os.remove(cache_path)
            except OSError:
                pass
                
    # Download fresh data
    logger.info(f"[yf_cache] Downloading fresh data for {tickers}...")
    df = _original_download(tickers, *args, **kwargs)
    
    # Cache the result if it is valid and non-empty
    if isinstance(df, pd.DataFrame) and not df.empty:
        try:
            df.to_parquet(cache_path, engine='fastparquet', compression='snappy')
            logger.info(f"[yf_cache] Cached data saved to {cache_file}")
        except Exception as e:
            logger.warning(f"[yf_cache] Failed to save cache for {tickers}: {e}")
            
    return df

# Apply monkey patch
yf.download = cached_download
logger.info("[yf_cache] yfinance.download successfully monkey-patched.")

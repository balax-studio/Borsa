"""
strategies/crypto/base.py — Kripto Stratejileri için Taban Sınıf ve Kayıt Mekanizması
"""
from dataclasses import dataclass, field
from typing import Any, List, Dict
import pandas as pd

@dataclass
class StrategyContext:
    symbol: str
    last_1d: pd.Series
    last_4h: pd.Series
    current_price: float
    df_1d: pd.DataFrame
    df_4h: pd.DataFrame
    btc_ok: bool
    btc_sniper_bias: int
    dynamic_atr_mult: float
    is_choppy: bool
    adx_1d: float
    market: str
    df_1h_sniper: pd.DataFrame = None
    sl_distance_penalty: float = 0.0
    
    # Geriye dönük uyumluluk (dict gibi davranması için)
    def get(self, key: str, default: Any = None) -> Any:
        return getattr(self, key, default)
    
    def __getitem__(self, key: str) -> Any:
        if not isinstance(key, str):
            raise KeyError(key)
        return getattr(self, key)
    
    def __setitem__(self, key: str, value: Any):
        setattr(self, key, value)
        
    def __contains__(self, key: str) -> bool:
        return hasattr(self, key)


class BaseStrategy:
    def __init__(self, name: str):
        self.name = name

    def check(self, ctx: StrategyContext) -> list:
        """
        Strateji kontrolünü çalıştırır ve sinyal listesi döner.
        """
        raise NotImplementedError


class StrategyRegistry:
    """
    OCP İhlalini çözen Dinamik Strateji Kayıt Defteri.
    Stratejiler import edildiklerinde kendilerini buraya kaydederler.
    Bu sayede her döngüde baştan yaratılmazlar (Singleton/Cache).
    """
    _long_strategies: List[BaseStrategy] = []
    _short_strategies: List[BaseStrategy] = []

    @classmethod
    def register_long(cls, strategy_cls):
        """Decorator for registering a long strategy"""
        cls._long_strategies.append(strategy_cls())
        return strategy_cls

    @classmethod
    def register_short(cls, strategy_cls):
        """Decorator for registering a short strategy"""
        cls._short_strategies.append(strategy_cls())
        return strategy_cls

    @classmethod
    def get_long_strategies(cls) -> List[BaseStrategy]:
        return cls._long_strategies

    @classmethod
    def get_short_strategies(cls) -> List[BaseStrategy]:
        return cls._short_strategies

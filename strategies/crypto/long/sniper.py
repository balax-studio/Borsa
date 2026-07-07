"""
strategies/crypto/long/sniper.py
"""
import math
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_breakout_scores,
    build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
    SNIPER_CRYPTO_WEIGHTS, check_hard_blocks,
)
from indicators import (
    sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    sniper_calculate_ote_body, sniper_calculate_ote,
    detect_bullish_divergence, detect_adx_divergence,
    detect_vsa_fakeout, detect_bb_exhaustion_trap,
)
from data_sources import get_crypto_1h_data, get_crypto_15m_data, get_funding_rate
from indicators.smc import (
    detect_supply_zones, is_price_in_supply_zone,
    detect_demand_zones, is_price_in_demand_zone
)
from strategies.helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_long
class SniperOteLongStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 4: KESKİN NİŞANCI (OTE)")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        btc_sniper_bias = ctx["btc_sniper_bias"]

        if btc_sniper_bias not in (1, 0):
            return signals

        swing_lows_s = sniper_find_swing_points(df_4h, point_type="low")
        swing_highs_s = sniper_find_swing_points(df_4h, point_type="high")
        sweep_ok, sweep_low = sniper_detect_sweep(df_4h, swing_lows_s, point_type="low")
        if not sweep_ok:
            return signals

        msb_ok, msb_high, msb_idx = sniper_detect_msb(df_4h, swing_highs_s, point_type="high")
        if not msb_ok:
            return signals

        sweep_idx = swing_lows_s[-1][0] if swing_lows_s else None
        ote_top, ote_bottom = sniper_calculate_ote_body(df_4h, sweep_idx, msb_idx, direction="long")
        if ote_top <= 0 or ote_bottom <= 0 or not (ote_bottom <= current_price <= ote_top):
            return signals

        has_fvg, _, _ = sniper_detect_fvg(df_4h, ote_top, ote_bottom, direction="bullish")
        if config.SMC_FVG_REQUIRED and not has_fvg:
            return signals

        rsi_div, _, _, _, _ = detect_bullish_divergence(df_4h)
        adx_div = detect_adx_divergence(df_4h, is_long=True)
        ctx["has_divergence"] = rsi_div or adx_div

        # GOLDEN FILTERS (Fakeout Guards)
        if detect_vsa_fakeout(df_4h, direction="long"):
            return signals
        if detect_bb_exhaustion_trap(df_4h, direction="long"):
            return signals
        if 'CHOP_14_1_100' in df_4h.columns and len(df_4h) > 0:
            if df_4h['CHOP_14_1_100'].iloc[-1] > 61.8:
                return signals

        funding_rate = get_funding_rate(symbol)
        df_1h_crypto = None

        sl = sweep_low * config.CRYPTO_LONG4_SL_MULT
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = max(current_price - sl, 1e-8)
        tp = current_price + (sl_dist * config.BEAR_HUNTER_TP_RR)
        fvg_label = " + FVG Onaylı ✅" if has_fvg else ""
        _rr_c4l = abs(tp - current_price) / max(abs(current_price - sl), 1e-8)
        
        ema_fast_val = None
        ema_mid_val = None
        if config.SMC_LTF_MSB_CONFIRM and df_1h_crypto is not None and not df_1h_crypto.empty:
            ema_fast_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_FAST}')
            ema_mid_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_21}')
        else:
            ema_fast_val = last_4h.get('EMA_20')
            ema_mid_val = last_4h.get('EMA_50')

        raw_vars = locals()
        
        _scores_c4l = build_breakout_scores(
            bb_width=None, price=current_price,
            ema_fast=ema_fast_val, ema_mid=ema_mid_val, ema_slow=None,
            volume=last_4h['volume'], vol_sma=last_4h.get('vol_sma_20'),
            dollar_vol=last_4h['volume'] * current_price,
            rr=_rr_c4l, regime="BULL", macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
            funding_rate=funding_rate,
            rsi=last_4h.get('RSI_14'),
            rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else last_4h.get('RSI_14'),
            has_engulfing=False
        )
        if has_fvg:
            _scores_c4l["engulfing"] = min(100.0, _scores_c4l["engulfing"] + config.SMC_FVG_BONUS)
            
        _conv_c4l = calculate_conviction(_scores_c4l, ctx=ctx)
        if _conv_c4l.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "AL",
                "entry_price": current_price, "sl": sl, "tp": tp,
                "conviction_score": _conv_c4l.total_score, "conviction_grade": _conv_c4l.grade,
                "conviction_details": _conv_c4l.component_scores, "position_size_pct": _conv_c4l.position_size_pct,
                "reason": (
                    f"🎯 SMC Kurulum (Gövde Fibo){fvg_label}\n"
                    f"🧹 Likidite: Eski dip ({sweep_low:.4f}) temizlendi.\n"
                    f"📐 MSB: Yapı kırılımı ({msb_high:.4f}) onaylı.\n"
                    f"🎣 OTE Bölgesi (Gövde): {ote_bottom:.4f} - {ote_top:.4f}\n"
                    f"📊 Fonlama: %{funding_rate:.4f} (Negatif Yakıt)\n"
                    f"🛡️ İşlem %4 kâra geçince Break-Even uygula."
                ) + _conv_c4l.to_reason_suffix()
            })
        return signals

@StrategyRegistry.register_long
class Sniper1hLongStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO LONG 10: KESKİN NİŞANCI (SNIPER)")

    def check(self, ctx: dict) -> list:
        # Note: Sniper 1H logic is run from within the sniper orchestrator which fetches 1h and 15m data.
        # But we want to isolate the LONG check here.
        # We will check if "ctx_1h" structure is provided.
        signals = []
        
        # If this is called from analyze_strategies_crypto directly, it will need to run the data collection.
        # If called with ctx containing 1h pre-processed data (which is what _check_crypto_sniper_1h does), we use it.
        # Let's support both or adapt to the current orchestrator flow.
        # In the original code, `_check_crypto_sniper_1h` collects the 1h data and then calls `_check_crypto_sniper_1h_long(ctx_1h)`.
        # So we can keep a check here that expects `ctx_1h` or constructs it.
        # Let's inspect the `ctx` passed to `_check_crypto_sniper_1h`. It has `df_1h_sniper`.
        
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        btc_ok = ctx["btc_ok"]
        df_1h_sniper = ctx.get("df_1h_sniper")
        
        if df_1h_sniper is None:
            df_1h_sniper = get_crypto_1h_data(symbol)
        if df_1h_sniper is None or df_1h_sniper.empty:
            return signals

        # We need to replicate the preprocessing from _check_crypto_sniper_1h
        df_1h_sniper = df_1h_sniper.copy()
        
        # Avoid duplicate indicator calculations if already present
        if not any(c in df_1h_sniper.columns for c in ['KCU_20_1.5', 'KCL_20_1.5']):
            df_1h_sniper.ta.kc(length=20, scalar=1.5, append=True)
        if not any(c in df_1h_sniper.columns for c in ['BBU_20_2.0', 'BBL_20_2.0']):
            df_1h_sniper.ta.bbands(length=20, std=2.0, append=True)
        if 'RSI_14' not in df_1h_sniper.columns:
            df_1h_sniper.ta.rsi(length=config.IND_RSI_LENGTH, append=True)
        if 'EMA_20' not in df_1h_sniper.columns:
            df_1h_sniper.ta.ema(length=config.IND_EMA_FAST, append=True)
        if 'EMA_50' not in df_1h_sniper.columns:
            df_1h_sniper.ta.ema(length=config.IND_EMA_21, append=True)
        if 'CMF_20' not in df_1h_sniper.columns:
            df_1h_sniper.ta.cmf(length=20, append=True)
        if 'vol_sma_20' not in df_1h_sniper.columns:
            df_1h_sniper['vol_sma_20'] = df_1h_sniper['volume'].rolling(window=config.IND_VOL_SMA_LENGTH).mean()

        willy_ema_15m_val = ctx.get("willy_ema_15m_val")
        if willy_ema_15m_val is None:
            df_15m_sniper = get_crypto_15m_data(symbol)
            if df_15m_sniper is not None and not df_15m_sniper.empty:
                highest_high = df_15m_sniper['high'].rolling(window=21).max()
                lowest_low = df_15m_sniper['low'].rolling(window=21).min()
                df_15m_sniper['WILLR_21'] = ((highest_high - df_15m_sniper['close']) / (highest_high - lowest_low).replace(0, 1e-9)) * -100.0
                df_15m_sniper['WILLR_21_EMA_13'] = df_15m_sniper['WILLR_21'].ewm(span=13, adjust=False).mean()
                willy_ema_15m_val = df_15m_sniper['WILLR_21_EMA_13'].iloc[-1]

        kc_upper_col = [c for c in df_1h_sniper.columns if 'KCU' in c]
        kc_lower_col = [c for c in df_1h_sniper.columns if 'KCL' in c]
        bb_upper_col = [c for c in df_1h_sniper.columns if 'BBU' in c]
        bb_lower_col = [c for c in df_1h_sniper.columns if 'BBL' in c]
        bb_mid_col = [c for c in df_1h_sniper.columns if 'BBM' in c]
        bb_pct_col = [c for c in df_1h_sniper.columns if 'BBP' in c]

        if not (kc_upper_col and kc_lower_col and bb_upper_col and bb_lower_col and bb_mid_col and bb_pct_col):
            return signals

        last_1h_s = df_1h_sniper.iloc[-1]
        prev_1h_s = last_1h_s
        if len(df_1h_sniper) >= 2:
            prev_1h_s = df_1h_sniper.iloc[-2]

        bbu = last_1h_s[bb_upper_col[0]]
        bbl = last_1h_s[bb_lower_col[0]]
        bbm = last_1h_s[bb_mid_col[0]]

        bbw = 0.0
        kcw = 0.0
        if bbm != 0:
            bbw = (bbu - bbl) / bbm
            kcw = (last_1h_s[kc_upper_col[0]] - last_1h_s[kc_lower_col[0]]) / bbm

        bb_pct = last_1h_s[bb_pct_col[0]]
        guarded_vol_sma = _apply_volume_sma_guard(df_1h_sniper, last_1h_s.get('vol_sma_20', 0))

        # Golden Filter (Iteration 2)
        if bbw is not None and pd.notna(bbw) and bbw >= 0.1000:
            return signals

        has_fvg_long, _, _ = sniper_detect_fvg(df_1h_sniper, df_1h_sniper['high'].iloc[-1], df_1h_sniper['low'].iloc[-1], direction="bullish")
        swing_lows_s = sniper_find_swing_points(df_1h_sniper, point_type="low")
        sweep_ok_long, _ = sniper_detect_sweep(df_1h_sniper, swing_lows_s, point_type="low")
        has_sfp_long = sweep_ok_long

        last_4h = ctx.get("last_4h")
        atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14')) if last_4h is not None else None
        if atr_val is None or pd.isna(atr_val):
            atr_val = current_price * config.BEAR_HUNTER_DEFAULT_ATR_MULT
        sl_long = current_price - (atr_val * 1.25)
        
        # Create temporary ctx for helper
        ctx_1h = {
            "df_4h": ctx.get("df_4h"),
            "last_4h": last_4h,
            "symbol": symbol,
            "current_price": current_price,
            "btc_ok": btc_ok,
            "df_1h_sniper": df_1h_sniper,
            "last_1h_s": last_1h_s,
            "prev_1h_s": prev_1h_s,
            "guarded_vol_sma": guarded_vol_sma,
            "bbw": bbw,
            "kcw": kcw,
            "bb_pct": bb_pct,
            "bbl": bbl,
            "bbu": bbu,
            "market": "KRIPTO",
            "last_1d": ctx.get("last_1d"),
            "willy_ema_15m_val": willy_ema_15m_val
        }
        
        sl_long = apply_5x_sl_cap(sl_long, current_price, ctx_1h)
        _tp_sn_long = current_price + config.BEAR_HUNTER_TP_RR * (current_price - sl_long)
        _rr_sn_long = abs(_tp_sn_long - current_price) / max(abs(current_price - sl_long), 1e-8)

        is_nan_ind = (pd.isna(last_1h_s.get('volume', float('nan'))) or pd.isna(current_price))

        supply_zones = detect_supply_zones(ctx.get("df_4h"))
        in_supply_zone = is_price_in_supply_zone(current_price, supply_zones)
        demand_zones = detect_demand_zones(ctx.get("df_4h"))
        in_demand_zone = is_price_in_demand_zone(current_price, demand_zones)
        
        rsi_div, _, _, _, _ = detect_bullish_divergence(ctx.get("df_4h"))
        adx_div = detect_adx_divergence(ctx.get("df_4h"), is_long=True)
        ctx_1h["has_divergence"] = rsi_div or adx_div

        # GOLDEN FILTERS (Fakeout Guards)
        if detect_vsa_fakeout(df_1h_sniper, direction="long"):
            return signals
        if detect_bb_exhaustion_trap(df_1h_sniper, direction="long"):
            return signals
        if 'CHOP_14_1_100' in df_1h_sniper.columns and len(df_1h_sniper) > 0:
            if df_1h_sniper['CHOP_14_1_100'].iloc[-1] > 61.8:
                return signals

        # Calculate wick rejection for LONG (fakeout check)
        c_open = last_1h_s.get('open', current_price)
        c_close = last_1h_s.get('close', current_price)
        c_high = last_1h_s.get('high', current_price)
        body = abs(c_close - c_open)
        upper_wick = c_high - max(c_open, c_close)
        is_wick_rejection_current = (upper_wick > (body * 1.5)) if body > 0 else False
        
        p_open = prev_1h_s.get('open', current_price)
        p_close = prev_1h_s.get('close', current_price)
        p_high = prev_1h_s.get('high', current_price)
        p_body = abs(p_close - p_open)
        p_upper_wick = p_high - max(p_open, p_close)
        is_wick_rejection_prev = (p_upper_wick > (p_body * 2.0)) if p_body > 0 else False
        
        is_wick_rejection = is_wick_rejection_current or is_wick_rejection_prev
        ctx_1h["is_fakeout"] = is_wick_rejection

        blocked, block_reason = check_hard_blocks(
            volume=last_1h_s.get('volume', 0),
            in_supply_zone=in_supply_zone,
            price=current_price,
            vol_sma=guarded_vol_sma,
            is_quarantined=False,
            is_circuit_open=False,
            sl_direction_ok=(sl_long < current_price),
            rr_ratio=_rr_sn_long,
            consecutive_sl=_get_consecutive_sl(symbol),
            is_core_indicators_nan=is_nan_ind,
            min_volume_usd=config.VOL_ABSOLUTE_MIN_CRYPTO,
            willy_ema=willy_ema_15m_val,
            is_long=True,
            is_wick_rejection=is_wick_rejection,
            is_crypto=True
        )
        if blocked:
            return signals

        funding_rate = get_funding_rate(symbol)

        _scores_sn_long = build_sniper_scores(
            price=current_price, ema_fast=last_1h_s.get(f'EMA_{config.IND_EMA_FAST}'), ema_mid=last_1h_s.get(f'EMA_{config.IND_EMA_21}'), ema_slow=None,
            rsi=last_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'), rsi_prev=prev_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'),
            volume=last_1h_s.get('volume', 0), vol_sma=guarded_vol_sma, dollar_vol=last_1h_s.get('volume', 0) * current_price,
            rr=_rr_sn_long, regime="BULL" if btc_ok else "BEAR",
            macro_aligned=btc_ok, consecutive_sl=_get_consecutive_sl(symbol),
            bbw=bbw, kcw=kcw, pb=bb_pct, fvg_present=has_fvg_long, sfp_present=has_sfp_long,
            market="KRIPTO", is_long=True, willy_ema=willy_ema_15m_val,
            funding_rate=funding_rate,
            in_supply_zone=in_supply_zone, in_demand_zone=in_demand_zone
        )
        _conv_sn_long = calculate_conviction(_scores_sn_long, weights=SNIPER_CRYPTO_WEIGHTS, ctx=ctx_1h)
        if _conv_sn_long.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM):
            raw_vars = locals()
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "AL",
                "entry_price": current_price, "sl": sl_long, "tp": _tp_sn_long,
                "conviction_score": _conv_sn_long.total_score, "conviction_grade": _conv_sn_long.grade,
                "conviction_details": _conv_sn_long.component_scores, "position_size_pct": _conv_sn_long.position_size_pct,
                "reason": (
                    f"🎯 Keskin Nişancı LONG!\n"
                    f"Kanunlar: Squeeze: {_scores_sn_long['bbw_squeeze']:.1f}, %B: {_scores_sn_long['percent_b']:.1f}, FVG/SFP: {_scores_sn_long['fvg_sfp']:.1f}\n"
                    f"Willy EMA Score: {_scores_sn_long.get('willy_ema_penalty', 0.0):.1f}\n"
                    f"SL: Bollinger Alt Band Altı ({sl_long:.2f})"
                ) + _conv_sn_long.to_reason_suffix()
            })
        return signals

"""
strategies/crypto/short/sniper.py
"""
import math
import pandas as pd
import config
from conviction_scorer import (
    calculate_conviction,
    build_short_scores, build_sniper_scores,
    CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH,
    SNIPER_CRYPTO_WEIGHTS, check_hard_blocks,
)
from indicators.smc import (
    detect_supply_zones, is_price_in_supply_zone,
    detect_demand_zones, is_price_in_demand_zone
)
from indicators import (
    sniper_find_swing_points, sniper_detect_sweep,
    sniper_detect_msb, sniper_detect_fvg,
    sniper_calculate_ote,
    detect_bearish_divergence, detect_adx_divergence,
    detect_vsa_fakeout, detect_bb_exhaustion_trap,
)
from data_sources import get_crypto_1h_data, get_crypto_15m_data, get_funding_rate
from strategies.crypto.filters import add_fakeout_features
from strategies.helpers import (
    _extract_raw_indicators, _apply_volume_sma_guard, _get_consecutive_sl,
)
from strategies.crypto.shared import apply_5x_sl_cap
from strategies.crypto.base import BaseStrategy, StrategyRegistry

@StrategyRegistry.register_short
class SniperOteShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 4: KESKİN NİŞANCI (OTE)")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        last_4h = ctx["last_4h"]
        current_price = ctx["current_price"]
        df_4h = ctx["df_4h"]
        
        df_4h = add_fakeout_features(df_4h)
        
        supply_zones = detect_supply_zones(df_4h)
        if not is_price_in_supply_zone(current_price, supply_zones):
            return signals
        btc_sniper_bias = ctx["btc_sniper_bias"]

        if btc_sniper_bias not in (-1, 0):
            return signals

        swing_highs_s = sniper_find_swing_points(df_4h, point_type="high")
        swing_lows_s = sniper_find_swing_points(df_4h, point_type="low")
        sweep_ok, sweep_high = sniper_detect_sweep(df_4h, swing_highs_s, point_type="high")
        if not sweep_ok:
            return signals

        msb_ok, msb_low, msb_idx = sniper_detect_msb(df_4h, swing_lows_s, point_type="low")
        if not msb_ok:
            return signals

        ote_top, ote_bottom = sniper_calculate_ote(msb_low, sweep_high)
        if not (ote_bottom <= current_price <= ote_top):
            return signals

        has_fvg, _, _ = sniper_detect_fvg(df_4h, ote_top, ote_bottom, direction="bearish")
        if config.SMC_FVG_REQUIRED and not has_fvg:
            return signals

        rsi_div, _, _, _, _ = detect_bearish_divergence(df_4h)
        adx_div = detect_adx_divergence(df_4h, is_long=False)
        ctx["has_divergence"] = rsi_div or adx_div

        # GOLDEN FILTERS (Fakeout Guards)
        if detect_vsa_fakeout(df_4h, direction="short"):
            return signals
        if detect_bb_exhaustion_trap(df_4h, direction="short"):
            return signals
        if 'CHOP_14_1_100' in df_4h.columns and len(df_4h) > 0:
            if df_4h['CHOP_14_1_100'].iloc[-1] > 61.8:
                return signals

        funding_rate = get_funding_rate(symbol)
        df_1h_crypto = None

        sl = sweep_high * config.CRYPTO_SHORT4_SL_MULT
        sl = apply_5x_sl_cap(sl, current_price, ctx)
        sl_dist = max(sl - current_price, 1e-8)
        tp = current_price - (sl_dist * config.BEAR_HUNTER_TP_RR)
        fvg_label = " + FVG Onaylı ✅" if has_fvg else ""
        _rr_c4s = abs(current_price - tp) / max(abs(sl - current_price), 1e-8)
        _adx_prev_c4s = df_4h.iloc[-2].get('ADX_14') if len(df_4h) >= 2 else None
        
        ema_fast_val = None
        ema_mid_val = None
        if config.SMC_LTF_MSB_CONFIRM and df_1h_crypto is not None and not df_1h_crypto.empty:
            ema_fast_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_FAST}')
            ema_mid_val = df_1h_crypto.iloc[-1].get(f'EMA_{config.IND_EMA_21}')
        else:
            ema_fast_val = last_4h.get('EMA_20')
            ema_mid_val = last_4h.get('EMA_50')

        raw_vars = locals()
        
        _scores_c4s = build_short_scores(
            adx=last_4h.get('ADX_14'), adx_prev=_adx_prev_c4s,
            price=current_price, ema_fast=ema_fast_val, ema_mid=ema_mid_val, ema_slow=None,
            rsi=last_4h.get('RSI_14'), rsi_prev=df_4h.iloc[-2].get('RSI_14') if len(df_4h) >= 2 else None,
            volume=last_4h['volume'], vol_sma=last_4h.get('vol_sma_20'),
            dollar_vol=last_4h['volume'] * current_price,
            rr=_rr_c4s, has_engulfing=False, regime="BEAR", macro_aligned=True,
            consecutive_sl=_get_consecutive_sl(symbol), market="KRIPTO",
            funding_rate=funding_rate
        )
        if has_fvg:
            _scores_c4s["engulfing"] = min(100.0, _scores_c4s["engulfing"] + config.SMC_FVG_BONUS)
            
        _conv_c4s = calculate_conviction(_scores_c4s, ctx=ctx)
        if _conv_c4s.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM, CONVICTION_WATCH):
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "SAT",
                "entry_price": current_price, "sl": sl, "tp": tp,
                "conviction_score": _conv_c4s.total_score, "conviction_grade": _conv_c4s.grade,
                "conviction_details": _conv_c4s.component_scores, "position_size_pct": _conv_c4s.position_size_pct,
                "reason": (
                    f"🎯 SHORT SMC Kurulum{fvg_label}\n"
                    f"🧹 Likidite: Eski tepe ({sweep_high:.4f}) temizlendi.\n"
                    f"📐 Bearish MSB: Yapı kırılımı ({msb_low:.4f}) aşağı onaylı.\n"
                    f"🎣 Premium OTE: {ote_bottom:.4f} - {ote_top:.4f}\n"
                    f"📊 Fonlama: +%{funding_rate:.4f} (Pozitif = Short Yakıtı)\n"
                    f"🛡️ İşlem %4 kâra geçince Break-Even uygula."
                ) + _conv_c4s.to_reason_suffix(),
                "ml_features": {
                    "OFI_Proxy": df_4h['OFI_Proxy'].iloc[-1] if 'OFI_Proxy' in df_4h else 0,
                    "Vol_Z_Score": df_4h['Vol_Z_Score'].iloc[-1] if 'Vol_Z_Score' in df_4h else 0,
                    "Upper_Wick_Ratio": df_4h['Upper_Wick_Ratio'].iloc[-1] if 'Upper_Wick_Ratio' in df_4h else 0,
                    "Lower_Wick_Ratio": df_4h['Lower_Wick_Ratio'].iloc[-1] if 'Lower_Wick_Ratio' in df_4h else 0
                }
            })
        return signals

@StrategyRegistry.register_short
class Sniper1hShortStrategy(BaseStrategy):
    def __init__(self):
        super().__init__("KRİPTO SHORT 10: KESKİN NİŞANCI (SNIPER)")

    def check(self, ctx: dict) -> list:
        signals = []
        symbol = ctx["symbol"]
        current_price = ctx["current_price"]
        btc_ok = ctx["btc_ok"]
        df_1h_sniper = ctx.get("df_1h_sniper")
        
        if df_1h_sniper is None:
            df_1h_sniper = get_crypto_1h_data(symbol)
        if df_1h_sniper is None or df_1h_sniper.empty:
            return signals

        df_1h_sniper = df_1h_sniper.copy()
        
        df_1h_sniper = add_fakeout_features(df_1h_sniper)
        
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

        # Golden Filter (Iteration 1)
        last_4h = ctx.get('last_4h')
        if last_4h is not None:
            atr_val = last_4h.get('ATRr_14', last_4h.get('ATR_14', 0))
            atr_pct = (atr_val / current_price) * 100.0 if current_price > 0 else 0
            if atr_pct <= 1.0771:
                return signals

        # Anti-Rekt HTF Trend filter
        last_1d = ctx.get('last_1d')
        if last_1d is not None:
            ema_20_1d = last_1d.get('EMA_20')
            ema_50_1d = last_1d.get('EMA_50')
            rsi_1d = last_1d.get('RSI_14')
            if pd.notna(ema_20_1d) and pd.notna(ema_50_1d) and pd.notna(rsi_1d):
                if ema_20_1d > ema_50_1d and rsi_1d > 60:
                    return signals

        has_fvg_short, _, _ = sniper_detect_fvg(df_1h_sniper, df_1h_sniper['high'].iloc[-1], df_1h_sniper['low'].iloc[-1], direction="bearish")
        swing_highs_s = sniper_find_swing_points(df_1h_sniper, point_type="high")
        sweep_ok_short, _ = sniper_detect_sweep(df_1h_sniper, swing_highs_s, point_type="high")
        has_sfp_short = sweep_ok_short

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
            "last_1d": last_1d,
            "willy_ema_15m_val": willy_ema_15m_val
        }

        sl_short = min(bbu * config.CRYPTO_SQUEEZE_SHORT_SL_BBU_MULT, current_price * config.CRYPTO_SQUEEZE_SHORT_SL_MAX_MULT)
        sl_short = apply_5x_sl_cap(sl_short, current_price, ctx_1h)
        _tp_sn_short = current_price - 2.25 * (sl_short - current_price)
        _rr_sn_short = abs(_tp_sn_short - current_price) / max(abs(sl_short - current_price), 1e-8)

        is_nan_ind = (pd.isna(last_1h_s.get('volume', float('nan'))) or pd.isna(current_price))

        supply_zones = detect_supply_zones(ctx.get("df_4h"))
        in_supply_zone = is_price_in_supply_zone(current_price, supply_zones)
        demand_zones = detect_demand_zones(ctx.get("df_4h"))
        in_demand_zone = is_price_in_demand_zone(current_price, demand_zones)

        rsi_div, _, _, _, _ = detect_bearish_divergence(ctx.get("df_4h"))
        adx_div = detect_adx_divergence(ctx.get("df_4h"), is_long=False)
        ctx_1h["has_divergence"] = rsi_div or adx_div

        # GOLDEN FILTERS (Fakeout Guards)
        if detect_vsa_fakeout(df_1h_sniper, direction="short"):
            return signals
        if detect_bb_exhaustion_trap(df_1h_sniper, direction="short"):
            return signals
        if 'CHOP_14_1_100' in df_1h_sniper.columns and len(df_1h_sniper) > 0:
            if df_1h_sniper['CHOP_14_1_100'].iloc[-1] > 61.8:
                return signals

        # Calculate wick rejection for SHORT (fakeout check)
        c_open = last_1h_s.get('open', current_price)
        c_close = last_1h_s.get('close', current_price)
        c_low = last_1h_s.get('low', current_price)
        body = abs(c_close - c_open)
        lower_wick = min(c_open, c_close) - c_low
        is_wick_rejection_current = (lower_wick > (body * 1.5)) if body > 0 else False
        
        p_open = prev_1h_s.get('open', current_price)
        p_close = prev_1h_s.get('close', current_price)
        p_low = prev_1h_s.get('low', current_price)
        p_body = abs(p_close - p_open)
        p_lower_wick = min(p_open, p_close) - p_low
        is_wick_rejection_prev = (p_lower_wick > (p_body * 2.0)) if p_body > 0 else False
        
        is_wick_rejection = is_wick_rejection_current or is_wick_rejection_prev
        ctx_1h["is_fakeout"] = is_wick_rejection

        blocked, block_reason = check_hard_blocks(
            volume=last_1h_s.get('volume', 0),
            in_supply_zone=in_supply_zone,
            price=current_price,
            vol_sma=guarded_vol_sma,
            is_quarantined=False,
            is_circuit_open=False,
            sl_direction_ok=(sl_short > current_price),
            rr_ratio=_rr_sn_short,
            consecutive_sl=_get_consecutive_sl(symbol),
            is_core_indicators_nan=is_nan_ind,
            min_volume_usd=config.VOL_ABSOLUTE_MIN_CRYPTO,
            willy_ema=willy_ema_15m_val,
            is_long=False,
            is_wick_rejection=is_wick_rejection,
            is_crypto=True
        )
        if blocked:
            return signals

        funding_rate = get_funding_rate(symbol)
        cmf_1h = last_1h_s.get('CMF_20')

        _scores_sn_short = build_sniper_scores(
            price=current_price, ema_fast=last_1h_s.get(f'EMA_{config.IND_EMA_FAST}'), ema_mid=last_1h_s.get(f'EMA_{config.IND_EMA_21}'), ema_slow=None,
            rsi=last_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'), rsi_prev=prev_1h_s.get(f'RSI_{config.IND_RSI_LENGTH}'),
            volume=last_1h_s.get('volume', 0), vol_sma=guarded_vol_sma, dollar_vol=last_1h_s.get('volume', 0) * current_price,
            rr=_rr_sn_short, regime="BEAR",
            macro_aligned=not btc_ok, consecutive_sl=_get_consecutive_sl(symbol),
            bbw=bbw, kcw=kcw, pb=bb_pct, fvg_present=has_fvg_short, sfp_present=has_sfp_short,
            market="KRIPTO", is_long=False, funding_rate=funding_rate,
            cmf=cmf_1h if cmf_1h is not None and not math.isnan(cmf_1h) else 0.0,
            willy_ema=willy_ema_15m_val,
            in_supply_zone=in_supply_zone, in_demand_zone=in_demand_zone
        )
        
        df_4h = ctx.get("df_4h")
        if df_4h is not None and not df_4h.empty:
            last_4h_ctx = df_4h.iloc[-1]
            ema_50 = last_4h_ctx.get("EMA_50")
            if ema_50 is not None and pd.notna(ema_50) and current_price > 0:
                dist_below_ema50 = (ema_50 - current_price) / current_price
                if dist_below_ema50 > 0.04:
                    oversold_stretch = dist_below_ema50 - 0.04
                    _scores_sn_short["conflict_penalty"] -= min(35.0, oversold_stretch * 200.0 + 10.0)

        _conv_sn_short = calculate_conviction(_scores_sn_short, weights=SNIPER_CRYPTO_WEIGHTS, ctx=ctx_1h)
        if _conv_sn_short.grade in (CONVICTION_STRONG, CONVICTION_MEDIUM):
            raw_vars = locals()
            signals.append({
                "raw_indicators": _extract_raw_indicators(raw_vars),
                "ticker": symbol, "market": "KRIPTO",
                "last_1d": ctx.get("last_1d"),
                "strategy": self.name, "signal": "SAT",
                "entry_price": current_price, "sl": sl_short, "tp": _tp_sn_short,
                "conviction_score": _conv_sn_short.total_score, "conviction_grade": _conv_sn_short.grade,
                "conviction_details": _conv_sn_short.component_scores, "position_size_pct": _conv_sn_short.position_size_pct,
                "reason": (
                    f"🎯 Keskin Nişancı SHORT!\n"
                    f"Kanunlar: Squeeze: {_scores_sn_short['bbw_squeeze']:.1f}, %B: {_scores_sn_short['percent_b']:.1f}, FVG/SFP: {_scores_sn_short['fvg_sfp']:.1f}\n"
                    f"Willy EMA Score: {_scores_sn_short.get('willy_ema_penalty', 0.0):.1f}\n"
                    f"SL: ~%5-7 Dinamik Stop ({sl_short:.2f})"
                ) + _conv_sn_short.to_reason_suffix(),
                "ml_features": {
                    "OFI_Proxy": df_1h_sniper['OFI_Proxy'].iloc[-1] if 'OFI_Proxy' in df_1h_sniper else 0,
                    "Vol_Z_Score": df_1h_sniper['Vol_Z_Score'].iloc[-1] if 'Vol_Z_Score' in df_1h_sniper else 0,
                    "Upper_Wick_Ratio": df_1h_sniper['Upper_Wick_Ratio'].iloc[-1] if 'Upper_Wick_Ratio' in df_1h_sniper else 0,
                    "Lower_Wick_Ratio": df_1h_sniper['Lower_Wick_Ratio'].iloc[-1] if 'Lower_Wick_Ratio' in df_1h_sniper else 0
                }
            })
        return signals

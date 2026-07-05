import config
from conviction_scorer import score_data_guard, score_conflict_resolver, calculate_conviction

def test_data_guard_soft_penalties():
    print("Testing Data Guard Soft Penalties...")
    # Base penalty
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=False, is_gap=False, cmf=0.1, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Base Penalty (Clean): {penalty}")
    assert penalty == 0.0

    # Darth Maul penalty
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=True, is_gap=False, cmf=0.1, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Darth Maul Penalty: {penalty}")
    assert penalty == config.DATA_GUARD_PENALTY_DARTH_MAUL

    # Gap penalty
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=False, is_gap=True, cmf=0.1, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Gap Penalty: {penalty}")
    assert penalty == config.DATA_GUARD_PENALTY_GAP

    # Liquidity Window penalty
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=False, is_gap=False, cmf=0.1, is_liquidity_window=False, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Liquidity Window Penalty: {penalty}")
    assert penalty == config.DATA_GUARD_PENALTY_LIQUIDITY_WINDOW

    # Float Gap Penalty (e.g. 1.5% gap which is half of 3.0%)
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=False, is_gap=1.5, cmf=0.1, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Float Gap Penalty (1.5%): {penalty}")
    assert abs(penalty - (-7.5)) < 0.001

    # Float Darth Maul Penalty (e.g. 0.075 body ratio which is half of 0.15 limit)
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=0.075, is_gap=False, cmf=0.1, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Float Darth Maul Penalty (0.075): {penalty}")
    assert abs(penalty - (-9.375)) < 0.001

    # Negative CMF Penalty (e.g. -0.2)
    penalty = score_data_guard(dollar_volume=10_000_000, is_darth_maul=False, is_gap=False, cmf=-0.2, is_liquidity_window=True, min_volume_usd=100_000, optimum_volume_usd=10_000_000)
    print(f"Negative CMF Penalty (-0.2): {penalty}")
    assert abs(penalty - (-7.5)) < 0.001

def test_conflict_resolver_soft_penalties():
    print("\nTesting Conflict Resolver Soft Penalties...")
    
    # Ranging Market (ADX < 20) with Trend Strategy -> Penalty
    penalty, apply_bear = score_conflict_resolver(adx=15, regime="BULL", strategy_type="TREND_FOLLOWING", is_long=True)
    print(f"Ranging Market + Trend Strategy: Penalty={penalty}, Apply Bear={apply_bear}")
    assert penalty == -26.25  # (20 - 15) * 5.25 (eski: -35.0)
    
    # Strong Trend (ADX > 40) with Mean Reversion Strategy -> Penalty
    penalty, apply_bear = score_conflict_resolver(adx=45, regime="BULL", strategy_type="MEAN_REVERSION", is_long=True)
    print(f"Strong Trend + Mean Reversion: Penalty={penalty}, Apply Bear={apply_bear}")
    assert penalty == -18.75  # (45 - 40) * 3.75 (eski: -25.0)

    # Bear Regime + Long Strategy -> apply_bear = True
    penalty, apply_bear = score_conflict_resolver(adx=30, regime="BEAR", strategy_type="TREND_FOLLOWING", is_long=True)
    print(f"Bear Regime + Long Strategy: Penalty={penalty}, Apply Bear={apply_bear}")
    assert apply_bear == True

def test_calculate_conviction():
    print("\nTesting calculate_conviction integration...")
    scores = {
        "adx": 10.0,
        "ema_alignment": 15.0,
        "rsi": 10.0,
        "rsi_direction": 10.0,
        "volume_ratio": 10.0,
        "dollar_volume": 10.0,
        "rr_ratio": 10.0,
        "engulfing": 10.0,
        "regime": 100.0,  # BULL
        "macro": 10.0,
        "penalty": 10.0,
        "data_guard_penalty": -20.0, # Gap penalty
        "conflict_penalty": -10.0,
        "apply_bear_penalty": True   # Will multiply by 0.7 (eski: 0.6)
    }
    
    weights = {k: 0.1 for k in scores if k not in ["data_guard_penalty", "conflict_penalty", "apply_bear_penalty", "regime"]}
    weights["regime"] = 0.0 # Just for testing math
    
    # Raw total = 10 * 10 = 100.
    # Total with weights = 10 * 0.1 * 10 = 10.0
    # Add penalties: 10.0 - 20.0 - 10.0 = -20.0 -> max(0, -20.0) = 0.0
    # Let's adjust scores so we don't hit 0.0
    scores = {k: 100.0 for k in scores}
    scores["data_guard_penalty"] = -20.0
    scores["conflict_penalty"] = -10.0
    scores["apply_bear_penalty"] = True
    scores["regime"] = 100.0 # Bull to pass hard block check
    
    result = calculate_conviction(scores, weights=weights)
    # Total: 10 factors * 100 * 0.1 = 100.0
    # + (-20.0) + (-10.0) = 70.0
    # apply_bear_penalty -> 70.0 * 0.91 * 0.7 = 44.59 -> 44.6
    print(f"Final Score: {result.total_score}")
    assert result.total_score == 44.6

def test_sniper_soft_scoring():
    print("\nTesting Sniper Soft Scoring...")
    from conviction_scorer import (
        score_bbw_squeeze, score_percent_b, score_fvg_sfp,
        build_sniper_scores, SNIPER_BIST_WEIGHTS, SNIPER_CRYPTO_WEIGHTS
    )
    
    # 1. BBW Squeeze tests
    assert score_bbw_squeeze(12.0, 10.0) == 100.0  # bbw >= kcw (expansion)
    assert abs(score_bbw_squeeze(9.5, 10.0) - 50.0) < 0.01  # deficit 0.5 (tolerance 1.0) -> 50.0 points
    assert score_bbw_squeeze(8.0, 10.0) == 0.0   # deficit 2.0 (> 1.0 tolerance) -> 0.0 points
    
    # 2. Percent B tests
    assert score_percent_b(0.5) == 100.0   # inside [0, 1]
    assert abs(score_percent_b(-0.04) - 50.0) < 0.001   # outside by 0.04 (dist/0.08 = 50% penalty)
    assert score_percent_b(-0.1) == 0.0    # outside by 0.1 (> 0.08 tolerance)
    assert abs(score_percent_b(1.04) - 50.0) < 0.001    # outside by 0.04
    
    # 3. FVG/SFP tests
    assert score_fvg_sfp(True, False) == 100.0
    assert score_fvg_sfp(False, True) == 100.0
    assert score_fvg_sfp(False, False) == 15.0  # partial points
    
    # 4. Scorer Integration
    scores_bist = build_sniper_scores(
        price=100.0, ema_fast=98.0, ema_mid=95.0, ema_slow=90.0,
        rsi=55.0, rsi_prev=50.0,
        volume=1200, vol_sma=1000, dollar_vol=120000,
        rr=3.0, has_engulfing=True, regime="BULL", macro_aligned=True, consecutive_sl=0,
        bbw=12.0, kcw=10.0, pb=0.5, fvg_present=True, sfp_present=False,
        market="BIST"
    )
    
    conv_bist = calculate_conviction(scores_bist, weights=SNIPER_BIST_WEIGHTS)
    print(f"BIST Sniper Total Score: {conv_bist.total_score}")
    assert conv_bist.total_score > 0
    
    # 5. Crypto Short Sniper Integration
    scores_crypto_short = build_sniper_scores(
        price=100.0, ema_fast=102.0, ema_mid=105.0, ema_slow=110.0,
        rsi=45.0, rsi_prev=50.0,
        volume=1200, vol_sma=1000, dollar_vol=120000,
        rr=3.0, has_engulfing=False, regime="BEAR", macro_aligned=True, consecutive_sl=0,
        bbw=12.0, kcw=10.0, pb=0.5, fvg_present=False, sfp_present=True,
        market="KRIPTO", is_long=False, funding_rate=0.005
    )
    conv_crypto_short = calculate_conviction(scores_crypto_short, weights=SNIPER_CRYPTO_WEIGHTS)
    print(f"Crypto Short Sniper Total Score: {conv_crypto_short.total_score}")
    assert conv_crypto_short.total_score > 0
    
    # Ensure weights sum test passes
    print("Sniper tests passed!")

def test_autopsy_soft_penalty():
    print("\nTesting Autopsy Soft Penalty...")
    from conviction_scorer import calculate_autopsy_soft_penalty

    # 1. Trend strategy (Long) below SMA 200, overbought RSI, low volume ratio
    # price = 90, sma200 = 100 (below by 10% -> 7.5 max penalty - %25 discounted)
    # rsi_1h = 65 (>55 by 10 -> NO PENALTY ANYMORE)
    # volume_ratio = 2.0 (below 3.5 threshold -> (3.5 - 2.0)/3.5 * 3.75 = 1.61 penalty - %25 discounted)
    # Total trend penalty should be -7.5 - 0 - 1.61 = -9.11
    pen_trend = calculate_autopsy_soft_penalty(
        price=90.0,
        sma200_1d=100.0,
        rsi_1h=65.0,
        volume_ratio=2.0,
        is_long=True,
        strategy_type="TREND_BREAKOUT"
    )
    print(f"Trend strategy penalty: {pen_trend}")
    assert pen_trend == -9.11

    # 2. Mean Reversion strategy (Long) with same params
    # below SMA 200 -> no penalty
    # overbought RSI -> no penalty
    # volume_ratio = 2.0 (below 3.0 threshold -> (3.0 - 2.0)/3.0 * 3.75 = 1.25 penalty - %25 discounted)
    # Total MR penalty should be -1.25
    pen_mr = calculate_autopsy_soft_penalty(
        price=90.0,
        sma200_1d=100.0,
        rsi_1h=65.0,
        volume_ratio=2.0,
        is_long=True,
        strategy_type="MEAN_REVERSION_DIP"
    )
    print(f"Mean Reversion strategy penalty: {pen_mr}")
    assert pen_mr == -1.25

    # 3. Mean Reversion strategy (Long) above volume threshold 3.0 -> no penalty at all
    pen_mr_clean = calculate_autopsy_soft_penalty(
        price=90.0,
        sma200_1d=100.0,
        rsi_1h=65.0,
        volume_ratio=3.5,
        is_long=True,
        strategy_type="MEAN_REVERSION_DIP"
    )
    print(f"Mean Reversion clean penalty: {pen_mr_clean}")
    assert pen_mr_clean == 0.0

    print("Autopsy soft penalty tests passed!")

if __name__ == "__main__":
    test_data_guard_soft_penalties()
    test_conflict_resolver_soft_penalties()
    test_calculate_conviction()
    test_sniper_soft_scoring()
    test_autopsy_soft_penalty()
    print("\nAll tests passed successfully!")


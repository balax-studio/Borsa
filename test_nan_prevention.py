# 99 yapılmıştır
# Eksik Veri (NaN) ve Veri Halüsinasyonu Önleme mekanizmalarını doğrulamak için yazılmış birim test modülüdür.
# test_nan_prevention.py

import unittest
import numpy as np
import sys
import os
from unittest.mock import patch

# PYTHONPATH ayarı
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import config
import data_guard
import conviction_scorer

class TestNaNPrevention(unittest.TestCase):
    """
    Eksik veri ve halüsinasyon önleme mekanizmaları birim test sınıfı.
    """

    def setUp(self):
        """Test öncesi ayarlar."""
        config.SOFT_UNCERTAINTY_PENALTY = 0.0

    def test_validate_indicators_integrity_success(self):
        """[validate_indicators_integrity](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/data_guard.py) geçerli indikatör verilerini onaylamalıdır."""
        indicators = {
            "RSI_14": 45.5,
            "ADX_14": 26.0,
            "vol_sma_20": 1.2
        }
        required = ["RSI_14", "ADX_14"]
        ok, reason = data_guard.validate_indicators_integrity(indicators, required)
        self.assertTrue(ok)
        self.assertEqual(reason, "")

    def test_validate_indicators_integrity_with_nan(self):
        """[validate_indicators_integrity](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/data_guard.py) NaN indikatör verilerini reddetmelidir (DG-07)."""
        indicators = {
            "RSI_14": np.nan,
            "ADX_14": 26.0,
            "vol_sma_20": 1.2
        }
        required = ["RSI_14", "ADX_14"]
        ok, reason = data_guard.validate_indicators_integrity(indicators, required)
        self.assertFalse(ok)
        self.assertIn("eksik veya NaN", reason)

    def test_validate_indicators_integrity_with_none(self):
        """[validate_indicators_integrity](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/data_guard.py) None indikatör verilerini reddetmelidir (DG-07)."""
        indicators = {
            "RSI_14": None,
            "ADX_14": 26.0,
        }
        required = ["RSI_14", "ADX_14"]
        ok, reason = data_guard.validate_indicators_integrity(indicators, required)
        self.assertFalse(ok)
        self.assertIn("eksik veya NaN", reason)

    # 99 yapılmıştır
    # HB-1 hacim engeline takılmamak için volume=10000 olarak güncellenmiştir.
    def test_check_hard_blocks_hb8(self):
        """[check_hard_blocks](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) is_core_indicators_nan=True durumunda HB-8 ile bloke etmelidir."""
        blocked, reason = conviction_scorer.check_hard_blocks(
            volume=10000,
            price=10.0,
            is_core_indicators_nan=True
        )
        self.assertTrue(blocked)
        self.assertIn("HB-8", reason)

    def test_calculate_conviction_nan_penalty(self):
        """[calculate_conviction](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) girdilerde NaN varsa -6.8 soft penalty uygulamalıdır."""
        scores = {
            "adx": np.nan,
            "rsi": 50.0,
            "volume_ratio": 70.0
        }
        result = conviction_scorer.calculate_conviction(scores)
        self.assertFalse(result.hard_blocked)
        self.assertEqual(result.component_scores.get("nan_penalty"), -6.8)

    def test_score_ema_short_nan_fallback(self):
        """[score_ema_short](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) NaN parametrede SOFT_UNCERTAINTY_PENALTY dönmelidir."""
        score = conviction_scorer.score_ema_short(price=np.nan, ema_fast=10.0, ema_mid=11.0)
        self.assertEqual(score, config.SOFT_UNCERTAINTY_PENALTY)

    def test_score_rsi_direction_nan_fallback(self):
        """[score_rsi_direction](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) NaN parametrede SOFT_UNCERTAINTY_PENALTY dönmelidir."""
        score = conviction_scorer.score_rsi_direction(rsi_current=np.nan, rsi_prev=30.0)
        self.assertEqual(score, config.SOFT_UNCERTAINTY_PENALTY)

    def test_check_hard_blocks_hb9_blocked(self):
        """[check_hard_blocks](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) in_supply_zone=True durumunda HB-9 ile bloke etmelidir."""
        blocked, reason = conviction_scorer.check_hard_blocks(
            volume=10000,
            price=10.0,
            in_supply_zone=True,
            is_long=True
        )
        self.assertTrue(blocked)
        self.assertIn("HB-9", reason)
        
    def test_check_hard_blocks_hb9_passed(self):
        """[check_hard_blocks](file:///c:/Users/YSR_MONSTER/.antigravity/Borsa/conviction_scorer.py) in_supply_zone=False durumunda bloke etmemelidir."""
        blocked, reason = conviction_scorer.check_hard_blocks(
            volume=1000000,
            price=10.0,
            in_supply_zone=False,
            is_long=True
        )
        self.assertFalse(blocked)

    def test_conviction_score_multiplier_math(self):
        """İnanç skoru çarpanının (0.91) 66 olan skoru tam 60'a düşürdüğünü doğrula."""
        # Use single weight to make base score exactly 100.0 before penalties
        scores = {
            "adx": 100.0,
            "autopsy_penalty": -34.0  # Raw score before multiplier is 66.0
        }
        custom_weights = {"adx": 1.0}
        
        # Override CONVICTION_SCORE_MULTIPLIER dynamically to 0.91 for the test
        with patch.object(config, "CONVICTION_SCORE_MULTIPLIER", 0.91):
            result = conviction_scorer.calculate_conviction(scores, weights=custom_weights)
            # Expect: 66 * 0.91 = 60.06 -> rounds to 60.1
            self.assertEqual(result.total_score, 60.1)
            # Autopsy penalty should remain unscaled in component_scores
            self.assertEqual(result.component_scores.get("autopsy_penalty"), -34.0)

if __name__ == "__main__":
    unittest.main()

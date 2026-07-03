# BEAR HUNTER OPTIMIZATION REPORT (7-LAYER)

## Strategy: SHORT 1: ZİRVE TUZAĞI (SFP)

### Baseline Performance
- **A-Test (Original):** 8W / 14L (PnL: 2.00R | Avg: 0.09R)

### Layer 1: Golden Indicator Filter
- **Filter:** `RSI < 60.4674`
- **Performance:** 8W / 7L (PnL: 9.00R)
- **Improvement:** +7.00R

### Layer 2: Stop Loss Placement (ATR Multiplier)
- **Optimal SL Mult:** `3.00x ATR` (Base was 1.5x)
- **Performance:** 12W / 8L (PnL: 3.29R)
- **Improvement:** +1.29R

### Layer 5: Conviction Threshold Tuning
- **Optimal Min Score:** `55` (Base was 50)
- **Performance:** 8W / 11L (PnL: 5.00R)
- **Improvement:** +3.00R

### Layer 7: Combined Master Filter
- No mathematical combination of filters improved performance without breaking constraints.

---

## Strategy: SHORT 2: PAHALI BÖLGE REDDİ (SMC PREMIUM)

### Baseline Performance
- **A-Test (Original):** 5W / 2L (PnL: 8.00R | Avg: 1.14R)

### Layer 7: Combined Master Filter
- No mathematical combination of filters improved performance without breaking constraints.

---

## MASTER FILTER SET RECOMMENDATIONS

The following configuration variables are recommended for `config.py` and strategy files:

```python
```

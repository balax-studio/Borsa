# BEAR HUNTER OPTIMIZATION REPORT (7-LAYER)

## Strategy: SHORT 2: PAHALI BÖLGE REDDİ (SMC PREMIUM)

### Baseline Performance
- **A-Test (Original):** 5W / 2L (PnL: 8.00R | Avg: 1.14R)

### Layer 7: Combined Master Filter
- No mathematical combination of filters improved performance without breaking constraints.

---

## Strategy: SHORT 1: ZİRVE TUZAĞI (SFP)

### Baseline Performance
- **A-Test (Original):** 8W / 7L (PnL: 9.00R | Avg: 0.60R)

### Layer 1: Golden Indicator Filter
- **Filter:** `CHOP < 58.7350`
- **Performance:** 8W / 3L (PnL: 13.00R)
- **Improvement:** +4.00R

### Layer 5: Conviction Threshold Tuning
- **Optimal Min Score:** `40` (Base was 50)
- **Performance:** 9W / 8L (PnL: 10.00R)
- **Improvement:** +1.00R

### Layer 7: Combined Master Filter
- No mathematical combination of filters improved performance without breaking constraints.

---

## MASTER FILTER SET RECOMMENDATIONS

The following configuration variables are recommended for `config.py` and strategy files:

```python
```

"""
ForestGuard — Advanced Change Detection

Algorithms:
  1. Percentage drop detection (original, kept for compatibility)
  2. CUSUM (Cumulative Sum) — detects persistent regime shifts
  3. BFAST-style breakpoint detection — structural change in trend
  4. Sustained decline classifier
  5. Severity grading (Severe / Moderate / Mild)
"""
import numpy as np
from typing import List, Dict, Any


# ════════════════════════════════════════════════════
# 1. PERCENTAGE DROP (original — kept)
# ════════════════════════════════════════════════════

def detect_deforestation(ndvi_series: dict, percent_threshold: float = 5.0) -> List[Dict]:
    """Detect year-on-year NDVI drops exceeding the threshold."""
    alerts = []
    years  = sorted(ndvi_series.keys())

    for i in range(1, len(years)):
        prev, curr = years[i - 1], years[i]
        prev_v, curr_v = ndvi_series[prev], ndvi_series[curr]

        if prev_v == 0:
            continue

        pct = ((curr_v - prev_v) / prev_v) * 100

        if pct < -percent_threshold:
            severity = (
                "Severe"   if pct < -20 else
                "Moderate" if pct < -10 else
                "Mild"
            )
            alerts.append({
                "year":        curr,
                "percent_drop": round(pct, 2),
                "severity":    severity,
                "method":      "percentage_drop"
            })

    return alerts


# ════════════════════════════════════════════════════
# 2. CUSUM — Cumulative Sum Control Chart
# ════════════════════════════════════════════════════

def detect_cusum_breakpoints(
    ndvi_series: dict,
    threshold_sigma: float = 1.5
) -> Dict[str, Any]:
    """
    CUSUM (Page 1954) detects when the mean of a process has shifted.
    Ideal for identifying the YEAR when deforestation began, not just
    year-on-year drops.

    Process:
      1. Compute expected mean (first 2 years as baseline)
      2. Accumulate deviations from baseline
      3. Flag when cumulative sum crosses threshold (sigma-based)

    Returns detected change points and severity.
    """
    years  = sorted(ndvi_series.keys())
    values = np.array([ndvi_series[y] for y in years], dtype=float)

    if len(values) < 4:
        return {"breakpoints": [], "cusum_detected": False, "method": "CUSUM"}

    # Baseline: first 2 years
    baseline_mean = np.mean(values[:2])
    baseline_std  = max(np.std(values[:2]), 1e-6)
    threshold     = threshold_sigma * baseline_std

    cusum_pos = 0.0  # positive CUSUM (detects increase)
    cusum_neg = 0.0  # negative CUSUM (detects decline — main interest)
    breakpoints = []

    for i in range(2, len(values)):
        deviation  = values[i] - baseline_mean
        cusum_pos  = max(0, cusum_pos + deviation - threshold / 4)
        cusum_neg  = max(0, cusum_neg - deviation - threshold / 4)

        if cusum_neg > threshold:
            # Decline detected
            drop_magnitude = baseline_mean - values[i]
            severity = (
                "Severe"   if drop_magnitude > 0.15 else
                "Moderate" if drop_magnitude > 0.07 else
                "Mild"
            )
            breakpoints.append({
                "year":           years[i],
                "cusum_value":    round(float(cusum_neg), 4),
                "ndvi_value":     round(float(values[i]), 4),
                "baseline_mean":  round(float(baseline_mean), 4),
                "drop_magnitude": round(float(drop_magnitude), 4),
                "severity":       severity,
                "method":         "CUSUM"
            })
            # Reset after detection
            cusum_neg = 0.0

    return {
        "breakpoints":    breakpoints,
        "cusum_detected": len(breakpoints) > 0,
        "method":         "CUSUM"
    }


# ════════════════════════════════════════════════════
# 3. BFAST-STYLE STRUCTURAL BREAKPOINT DETECTION
# ════════════════════════════════════════════════════

def detect_structural_breakpoints(ndvi_series: dict) -> Dict[str, Any]:
    """
    BFAST (Breaks For Additive Season and Trend) inspired breakpoint detector.
    Simplified to annual data (no seasonal component).

    Strategy:
      - Fit linear trend to full series
      - Test each possible split point
      - Find the split that minimises total residual sum of squares (RSS)
      - If RSS improvement > threshold → declare structural break

    This identifies the single most significant trend change year.
    """
    years  = sorted(ndvi_series.keys())
    values = np.array([ndvi_series[y] for y in years], dtype=float)
    n      = len(values)

    if n < 5:
        return {"breakpoint_year": None, "bfast_detected": False, "method": "BFAST-simplified"}

    x = np.arange(n)

    def ols_rss(x_seg, y_seg):
        """Ordinary least squares RSS for a segment."""
        if len(y_seg) < 2:
            return np.sum(y_seg ** 2)
        coeffs  = np.polyfit(x_seg, y_seg, 1)
        resids  = y_seg - np.polyval(coeffs, x_seg)
        return float(np.sum(resids ** 2))

    # Full series RSS
    rss_full = ols_rss(x, values)

    best_rss   = rss_full
    best_split = None

    # Test all valid split points (leave at least 2 points on each side)
    for split in range(2, n - 2):
        rss_left  = ols_rss(x[:split],  values[:split])
        rss_right = ols_rss(x[split:],  values[split:])
        rss_split = rss_left + rss_right

        if rss_split < best_rss:
            best_rss   = rss_split
            best_split = split

    if best_split is None:
        return {"breakpoint_year": None, "bfast_detected": False, "method": "BFAST-simplified"}

    # Improvement ratio — must exceed 20% to be significant
    improvement = (rss_full - best_rss) / (rss_full + 1e-9)
    if improvement < 0.20:
        return {"breakpoint_year": None, "bfast_detected": False, "method": "BFAST-simplified"}

    bp_year = years[best_split]

    # Compute pre/post slopes
    slope_pre  = float(np.polyfit(x[:best_split],  values[:best_split],  1)[0])
    slope_post = float(np.polyfit(x[best_split:],  values[best_split:],  1)[0])
    slope_change = slope_post - slope_pre

    return {
        "breakpoint_year": bp_year,
        "slope_before":    round(slope_pre,    6),
        "slope_after":     round(slope_post,   6),
        "slope_change":    round(slope_change, 6),
        "rss_improvement": round(improvement,  3),
        "direction":       "degradation" if slope_change < 0 else "recovery",
        "bfast_detected":  True,
        "method":          "BFAST-simplified"
    }


# ════════════════════════════════════════════════════
# 4. SUSTAINED DECLINE CLASSIFIER
# ════════════════════════════════════════════════════

def detect_sustained_decline(ndvi_series: dict) -> Dict[str, Any]:
    """
    Classify multi-year decline patterns.
    Returns decline type, consecutive drop count, and recovery indicator.
    """
    years  = sorted(ndvi_series.keys())
    values = [ndvi_series[y] for y in years]
    n      = len(values)

    if n < 2:
        return {"status": "Insufficient data", "consecutive_declines": 0}

    drops       = [values[i] < values[i - 1] for i in range(1, n)]
    consec_max  = _max_consecutive(drops)
    total_drops = sum(drops)
    drop_ratio  = total_drops / (n - 1)

    # Recovery: last year improved
    recovering = values[-1] > values[-2] if n >= 2 else False

    if consec_max >= 4 or (drop_ratio > 0.75 and n >= 4):
        status = "Chronic Decline — persistent multi-year deforestation"
    elif consec_max >= 3 or drop_ratio > 0.6:
        status = "Sustained Decline — significant vegetation loss trend"
    elif consec_max >= 2:
        status = "Moderate Decline — possible early-stage deforestation"
    elif recovering:
        status = "Recovering — recent improvement after prior decline"
    else:
        status = "Stable — no significant sustained decline"

    return {
        "status":               status,
        "consecutive_declines": consec_max,
        "total_decline_years":  total_drops,
        "drop_ratio":           round(drop_ratio, 2),
        "recovering":           recovering
    }


def _max_consecutive(bools: list) -> int:
    """Return maximum length of consecutive True values."""
    max_c = cur_c = 0
    for b in bools:
        cur_c = cur_c + 1 if b else 0
        max_c = max(max_c, cur_c)
    return max_c


# ════════════════════════════════════════════════════
# 5. UNIFIED CHANGE DETECTION (all methods combined)
# ════════════════════════════════════════════════════

def run_full_change_detection(ndvi_series: dict) -> Dict[str, Any]:
    """
    Run all change detection algorithms and return unified report.
    Used by the pipeline for maximum diagnostic coverage.
    """
    alerts     = detect_deforestation(ndvi_series)
    cusum      = detect_cusum_breakpoints(ndvi_series)
    bfast      = detect_structural_breakpoints(ndvi_series)
    sustained  = detect_sustained_decline(ndvi_series)

    # Unified alert count
    total_signals = len(alerts) + len(cusum["breakpoints"]) + (1 if bfast["bfast_detected"] else 0)

    return {
        "percentage_alerts":  alerts,
        "cusum":              cusum,
        "bfast":              bfast,
        "sustained_decline":  sustained,
        "total_change_signals": total_signals,
        "change_detected":    total_signals > 0
    }
   

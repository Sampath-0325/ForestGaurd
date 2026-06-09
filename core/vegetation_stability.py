"""
ForestGuard — Advanced Vegetation Stability Analysis

Metrics:
  1. VSI — Vegetation Stability Index (original, enhanced)
  2. Resilience Index — ability to recover after disturbance (post-drop recovery)
  3. Resistance Score — how much NDVI resists external pressures (low sensitivity)
  4. Recovery Rate — speed of recovery after lowest NDVI point
  5. Stability Trend — is the ecosystem becoming more or less stable over time?

References:
  - Pimm (1984) "The complexity and stability of ecosystems" — Nature
  - Holling (1973) "Resilience and Stability of Ecological Systems"
  - Verbesselt et al. (2010) "Phenological change detection while accounting
    for abrupt and gradual trends in satellite image time series"
"""
import numpy as np
import logging
from typing import Dict, Any

logger = logging.getLogger("forestguard.stability")


def compute_vsi(ndvi_series: dict) -> Dict[str, Any]:
    """
    Comprehensive vegetation stability assessment.
    Returns VSI, Resilience, Resistance, Recovery Rate, and Stability Trend.
    """
    if not ndvi_series or len(ndvi_series) < 2:
        return _empty_vsi("Insufficient data")

    years  = sorted(ndvi_series.keys())
    values = np.array([ndvi_series[y] for y in years], dtype=float)
    n      = len(values)

    mean_ndvi = float(np.mean(values))
    std_ndvi  = float(np.std(values))

    # ── 1. VSI — Vegetation Stability Index ──
    # Original: VSI = 1 - (std / (mean + ε))
    vsi = float(1 - (std_ndvi / (mean_ndvi + 1e-6)))
    vsi = max(0.0, min(1.0, vsi))  # clamp to [0, 1]

    # ── 2. Resistance Score ──
    # How much does NDVI resist dropping during stress years?
    # Proxy: 1 - (max single-year decline / range)
    resistance = _compute_resistance(values)

    # ── 3. Resilience Index ──
    # After a disturbance (drop > 1 std), how quickly does NDVI recover?
    resilience, recovery_event = _compute_resilience(values, std_ndvi, mean_ndvi)

    # ── 4. Recovery Rate ──
    # Slope of NDVI from its minimum point to the most recent year
    recovery_rate = _compute_recovery_rate(values)

    # ── 5. Stability Trend ──
    # Is the ecosystem becoming more or less stable?
    # Uses rolling 3-year std deviation — declining std = improving stability
    stability_trend = _compute_stability_trend(values)

    # ── Composite Ecosystem Health Score (0–1) ──
    health_score = round(
        0.35 * vsi +
        0.25 * resilience +
        0.25 * resistance +
        0.15 * max(0, min(1, (recovery_rate + 0.05) / 0.10)),   # normalise
        3
    )

    # ── Stability Status Label ──
    status = _classify_stability(vsi, resilience, resistance, stability_trend)

    return {
        "vsi":                  round(vsi, 3),
        "resilience_index":     round(resilience, 3),
        "resistance_score":     round(resistance, 3),
        "recovery_rate":        round(recovery_rate, 5),
        "stability_trend":      stability_trend,
        "ecosystem_health":     health_score,
        "mean_ndvi":            round(mean_ndvi, 4),
        "variability":          round(std_ndvi, 4),
        "stability_status":     status,
        "disturbance_detected": recovery_event is not None,
        "disturbance_year":     recovery_event,
        "years_analysed":       n,
    }


# ═══════════════════════════════════════════════════
# RESISTANCE
# ═══════════════════════════════════════════════════

def _compute_resistance(values: np.ndarray) -> float:
    """
    Resistance = 1 - (worst single-year drop / total NDVI range).
    High resistance: the ecosystem doesn't drop sharply even under stress.
    """
    if len(values) < 2:
        return 1.0

    drops = [values[i - 1] - values[i] for i in range(1, len(values)) if values[i] < values[i - 1]]
    if not drops:
        return 1.0   # no drops at all = maximum resistance

    ndvi_range = float(np.max(values) - np.min(values))
    if ndvi_range < 1e-6:
        return 1.0

    worst_drop = max(drops)
    resistance = 1 - (worst_drop / ndvi_range)
    return round(max(0.0, min(1.0, resistance)), 3)


# ════════════════════════════════════════════════════
# RESILIENCE
# ════════════════════════════════════════════════════

def _compute_resilience(values: np.ndarray, std_ndvi: float, mean_ndvi: float):
    """
    Resilience = ability to recover after disturbance.
    Detects a disturbance (drop > 1.5σ below mean), then measures
    how much NDVI recovered in the subsequent years.

    Returns (resilience_score 0–1, disturbance_year_index or None).
    """
    if len(values) < 4:
        return 0.5, None   # neutral: not enough data

    threshold = mean_ndvi - 1.5 * std_ndvi

    # Find first disturbance (NDVI below threshold)
    disturbance_idx = None
    for i in range(1, len(values) - 1):
        if values[i] < threshold:
            disturbance_idx = i
            break

    if disturbance_idx is None:
        return 1.0, None   # no disturbance = fully resilient (nothing to recover from)

    # Recovery: how much did NDVI recover after the disturbance?
    pre_disturbance  = values[disturbance_idx - 1]
    post_values      = values[disturbance_idx + 1:]

    if len(post_values) == 0:
        return 0.0, disturbance_idx

    max_recovery = float(np.max(post_values))
    drop_magnitude = pre_disturbance - values[disturbance_idx]

    if drop_magnitude < 1e-6:
        return 1.0, disturbance_idx

    recovery_fraction = (max_recovery - values[disturbance_idx]) / drop_magnitude
    resilience = max(0.0, min(1.0, recovery_fraction))

    return round(resilience, 3), disturbance_idx


# ═══════════════════════════════════════════════════
# RECOVERY RATE
# ═══════════════════════════════════════════════════

def _compute_recovery_rate(values: np.ndarray) -> float:
    """
    Rate of NDVI change from the trough (minimum) to the most recent year.
    Positive rate = recovering. Negative = still declining after trough.
    """
    if len(values) < 3:
        return 0.0

    trough_idx = int(np.argmin(values))
    if trough_idx >= len(values) - 1:
        # Still declining or trough is the last point
        return float(np.polyfit(range(len(values)), values, 1)[0])

    post_trough = values[trough_idx:]
    if len(post_trough) < 2:
        return 0.0

    slope = float(np.polyfit(range(len(post_trough)), post_trough, 1)[0])
    return round(slope, 6)


# ════════════════════════════════════════════════════
# STABILITY TREND
# ════════════════════════════════════════════════════

def _compute_stability_trend(values: np.ndarray) -> str:
    """
    Rolling 3-year standard deviation to detect whether
    ecosystem variability is increasing or decreasing over time.
    Increasing variability → destabilising. Decreasing → stabilising.
    """
    if len(values) < 5:
        return "Insufficient data for trend"

    window = 3
    rolling_stds = [
        float(np.std(values[i:i + window]))
        for i in range(len(values) - window + 1)
    ]

    if len(rolling_stds) < 2:
        return "Stable"

    std_slope = float(np.polyfit(range(len(rolling_stds)), rolling_stds, 1)[0])

    if std_slope > 0.005:
        return "Destabilising — increasing variability"
    elif std_slope < -0.005:
        return "Stabilising — decreasing variability"
    else:
        return "Stable variability"


# ═════════════════════════════════════════════════════
# STATUS CLASSIFIER
# ═════════════════════════════════════════════════════

def _classify_stability(vsi: float, resilience: float, resistance: float, trend: str) -> str:
    composite = (vsi * 0.4 + resilience * 0.35 + resistance * 0.25)

    if composite >= 0.80:
        return "Very Stable — high resistance and resilience"
    elif composite >= 0.65:
        return "Moderately Stable — minor stress detected"
    elif composite >= 0.45:
        return "Unstable — ecosystem under significant pressure"
    elif composite >= 0.25:
        return "Highly Unstable — severe degradation or slow recovery"
    else:
        return "Critical — ecosystem collapse risk"


def _empty_vsi(reason: str) -> Dict[str, Any]:
    return {
        "vsi": 0.0, "resilience_index": 0.0, "resistance_score": 0.0,
        "recovery_rate": 0.0, "stability_trend": reason,
        "ecosystem_health": 0.0, "mean_ndvi": 0.0, "variability": 0.0,
        "stability_status": reason, "disturbance_detected": False,
        "disturbance_year": None, "years_analysed": 0
    }

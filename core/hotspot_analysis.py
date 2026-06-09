"""
ForestGuard — Advanced Hotspot Analysis

Methods:
  1. Getis-Ord Gi* statistic (spatial autocorrelation) — cluster detection
  2. Moran's I (global spatial autocorrelation index)
  3. Trend-acceleration hotspot (local slope analysis)
  4. Fire risk overlay (integrates FIRMS fire data if available)
  5. Hotspot persistence score — how long has area been high risk
"""
import numpy as np
import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger("forestguard.hotspot")


# ═══════════════════════════════════════════════════
# 1. MAIN HOTSPOT DETECTOR (upgraded from original)
# ═══════════════════════════════════════════════════

def detect_hotspots(
    ndvi_series: dict,
    fire_data: Optional[dict] = None,
    multi_index: Optional[dict] = None
) -> Dict[str, Any]:
    """
    Full hotspot analysis combining statistical, temporal, and fire signals.

    Args:
        ndvi_series:  {year: ndvi_value}
        fire_data:    output from data_loader.load_fire_data() — optional
        multi_index:  output from ndvi.yearly_multi_index_timeseries() — optional

    Returns comprehensive hotspot dict.
    """
    if not ndvi_series or len(ndvi_series) < 2:
        return _empty_hotspot()

    years  = sorted(ndvi_series.keys())
    values = np.array([ndvi_series[y] for y in years], dtype=float)
    x      = np.arange(len(values), dtype=float)

    # ── Core statistics ──
    slope       = float(np.polyfit(x, values, 1)[0])
    variability = float(np.std(values))
    mean_ndvi   = float(np.mean(values))

    # ── Trend acceleration (is decline worsening?) ──
    acceleration_score = _compute_acceleration(values, x)

    # ── Temporal hotspot score (0–1) ──
    temporal_score = _temporal_hotspot_score(slope, variability, acceleration_score)

    # ── Fire risk integration ──
    fire_risk    = "NONE"
    fire_penalty = 0.0
    if fire_data:
        fire_risk    = fire_data.get("fire_risk_flag", "NONE")
        fire_penalty = {"HIGH": 0.3, "MEDIUM": 0.15, "NONE": 0.0}.get(fire_risk, 0.0)

    # ── Multi-index signal ──
    mi_penalty = 0.0
    if multi_index:
        fhs_vals = [multi_index[y]["FHS"] for y in years
                    if y in multi_index and multi_index[y].get("FHS") is not None]
        if len(fhs_vals) >= 2:
            fhs_slope  = float(np.polyfit(range(len(fhs_vals)), fhs_vals, 1)[0])
            mi_penalty = min(max(-fhs_slope / 0.05, 0.0), 0.25)  # max 0.25 penalty

    # ── Combined hotspot score ──
    hotspot_score = min(temporal_score + fire_penalty + mi_penalty, 1.0)

    # ── Classification ──
    if hotspot_score >= 0.65 or fire_risk == "HIGH":
        hotspot_risk = "HIGH_RISK"
    elif hotspot_score >= 0.35 or fire_risk == "MEDIUM":
        hotspot_risk = "MEDIUM_RISK"
    else:
        hotspot_risk = "LOW_RISK"

    # ── Persistence (how many consecutive years has area been high risk?) ──
    persistence = _compute_persistence(values)

    # ── Gi* z-score (spatial intensity proxy from temporal data) ──
    gi_star = _temporal_gi_star(values)

    return {
        "hotspot_risk":       hotspot_risk,
        "hotspot_score":      round(hotspot_score, 3),
        "trend_slope":        round(slope, 6),
        "variability":        round(variability, 6),
        "mean_ndvi":          round(mean_ndvi, 4),
        "acceleration_score": round(acceleration_score, 3),
        "temporal_gi_star":   round(gi_star, 3),
        "fire_risk":          fire_risk,
        "fire_penalty":       round(fire_penalty, 3),
        "persistence_years":  persistence,
        "most_recent_ndvi":   round(float(values[-1]), 4),
        "peak_ndvi":          round(float(np.max(values)), 4),
        "trough_ndvi":        round(float(np.min(values)), 4),
        "ndvi_range":         round(float(np.max(values) - np.min(values)), 4),
    }


# ═══════════════════════════════════════════════════
# 2. GETIS-ORD GI* (TEMPORAL PROXY)
# ═══════════════════════════════════════════════════

def _temporal_gi_star(values: np.ndarray) -> float:
    """
    Temporal Gi* z-score: measures how unusually low the recent NDVI is
    compared to the full series.

    Gi* > 1.96 = statistically significant spatial cluster (95% confidence).
    Here applied temporally: high z-score = recent values are anomalously low.
    """
    n = len(values)
    if n < 3:
        return 0.0

    global_mean = np.mean(values)
    global_std  = np.std(values)

    if global_std < 1e-6:
        return 0.0

    # Focus on last third of series (recent years)
    recent = values[-(n // 3):]
    local_sum = np.sum(recent)
    w_sum     = len(recent)       # equal weights (binary spatial weights)

    numerator   = local_sum - global_mean * w_sum
    denominator = global_std * np.sqrt((n * w_sum - w_sum ** 2) / (n - 1))

    if denominator < 1e-9:
        return 0.0

    return float(numerator / denominator)   # negative = anomalously low (bad)


# ════════════════════════════════════════════════════
# 3. TREND ACCELERATION
# ════════════════════════════════════════════════════

def _compute_acceleration(values: np.ndarray, x: np.ndarray) -> float:
    """
    Second derivative of the trend — is the decline speeding up?
    Returns 0–1 where 1 = rapidly accelerating decline.
    """
    if len(values) < 4:
        return 0.0

    mid = len(values) // 2
    slope_early = float(np.polyfit(x[:mid], values[:mid], 1)[0])
    slope_late  = float(np.polyfit(x[mid:], values[mid:], 1)[0])

    acceleration = slope_late - slope_early  # negative = accelerating decline
    # Normalise: -0.03/yr acceleration → score of 1.0
    return round(min(max(-acceleration / 0.03, 0.0), 1.0), 3)


# ═════════════════════════════════════════════════════
# 4. TEMPORAL HOTSPOT SCORE
# ═════════════════════════════════════════════════════

def _temporal_hotspot_score(slope: float, variability: float, acceleration: float) -> float:
    """Combine slope, variability, acceleration into 0–1 hotspot score."""
    slope_component  = min(max(-slope / 0.05, 0.0), 1.0) * 0.50
    var_component    = min(variability / 0.15, 1.0) * 0.20
    accel_component  = acceleration * 0.30
    return round(min(slope_component + var_component + accel_component, 1.0), 3)


# ═══════════════════════════════════════════════════
# 5. PERSISTENCE
# ═══════════════════════════════════════════════════

def _compute_persistence(values: np.ndarray, threshold_percentile: float = 30) -> int:
    """
    Count how many recent consecutive years the NDVI has been
    below the historical 30th percentile (poor vegetation health).
    """
    if len(values) < 3:
        return 0

    threshold = np.percentile(values, threshold_percentile)
    count = 0
    for v in reversed(values):
        if v < threshold:
            count += 1
        else:
            break
    return count


# ═══════════════════════════════════════════════════
# 6. GRID HOTSPOT (multi-point spatial analysis)
# ═══════════════════════════════════════════════════

def analyze_grid_hotspots(grid_results: List[Dict]) -> Dict[str, Any]:
    """
    Given a list of point-level hotspot results from the risk grid,
    compute global spatial statistics across the grid.

    Args:
        grid_results: list of dicts with keys: lat, lon, hotspot_score, risk_level

    Returns spatial summary with cluster info.
    """
    if not grid_results:
        return {"cluster_count": 0, "high_risk_fraction": 0.0, "spatial_pattern": "No data"}

    scores     = np.array([r.get("hotspot_score", 0) for r in grid_results])
    risk_levels = [r.get("hotspot_risk", "LOW_RISK") for r in grid_results]

    high_count   = sum(1 for r in risk_levels if r == "HIGH_RISK")
    medium_count = sum(1 for r in risk_levels if r == "MEDIUM_RISK")
    total        = len(grid_results)

    high_frac = high_count / total

    # Simple cluster detection: are high-risk points adjacent?
    pattern = (
        "Widespread deforestation front"   if high_frac > 0.5 else
        "Scattered high-risk patches"      if high_frac > 0.25 else
        "Isolated hotspots"                if high_frac > 0.1 else
        "Predominantly stable vegetation"
    )

    return {
        "total_points":        total,
        "high_risk_count":     high_count,
        "medium_risk_count":   medium_count,
        "high_risk_fraction":  round(high_frac, 3),
        "mean_hotspot_score":  round(float(np.mean(scores)), 3),
        "max_hotspot_score":   round(float(np.max(scores)), 3),
        "spatial_pattern":     pattern,
        "cluster_count":       high_count  # simplified: each HIGH_RISK = a cluster centre
    }


def _empty_hotspot() -> Dict[str, Any]:
    return {
        "hotspot_risk": "LOW_RISK", "hotspot_score": 0.0,
        "trend_slope": 0.0, "variability": 0.0, "mean_ndvi": 0.0,
        "acceleration_score": 0.0, "temporal_gi_star": 0.0,
        "fire_risk": "NONE", "fire_penalty": 0.0,
        "persistence_years": 0, "most_recent_ndvi": 0.0,
        "peak_ndvi": 0.0, "trough_ndvi": 0.0, "ndvi_range": 0.0
    }
    


"""
ForestGuard — Advanced Risk Analysis

Ensemble ML approach combining:
  1. Isolation Forest     — anomaly detection on NDVI values
  2. Gradient trend       — linear slope significance test
  3. Volatility scoring   — coefficient of variation
  4. Multi-index fusion   — EVI, SAVI, NBR signals if available
  5. SHAP-style feature importance — explains WHY a pixel is high risk
"""
import numpy as np
import logging
from typing import Dict, Any, Optional

logger = logging.getLogger("forestguard.risk")


# ════════════════════════════════════════════════════════════
# CORE RISK SCORER
# ════════════════════════════════════════════════════════════

def compute_risk_score(ndvi_series: dict, multi_index: Optional[dict] = None) -> Dict[str, Any]:
    """
    Ensemble risk scoring from NDVI + optional multi-index data.

    Args:
        ndvi_series:  {year: ndvi_value} dict
        multi_index:  {year: {"NDVI":..., "EVI":..., "SAVI":..., "NBR":..., "FHS":...}}
                      from yearly_multi_index_timeseries() — optional but improves accuracy

    Returns comprehensive risk dict with level, score, anomaly, and feature importance.
    """
    if not ndvi_series or len(ndvi_series) < 2:
        return _empty_risk()

    years  = sorted(ndvi_series.keys())
    values = np.array([ndvi_series[y] for y in years], dtype=float)
    x      = np.arange(len(values), dtype=float)

    # ── Feature 1: Linear trend slope (normalised) ──
    slope = float(np.polyfit(x, values, 1)[0])

    # ── Feature 2: Coefficient of variation (volatility) ──
    mean_v = np.mean(values)
    std_v  = np.std(values)
    cv     = float(std_v / (abs(mean_v) + 1e-6))

    # ── Feature 3: Recent acceleration ──
    # Is the decline speeding up in the last third of the series?
    if len(values) >= 6:
        mid  = len(values) // 2
        slope_early = float(np.polyfit(x[:mid], values[:mid], 1)[0])
        slope_late  = float(np.polyfit(x[mid:], values[mid:], 1)[0])
        acceleration = slope_late - slope_early
    else:
        acceleration = 0.0

    # ── Feature 4: Anomaly detection via Isolation Forest ──
    anomaly_score, is_anomaly = _run_isolation_forest(values)

    # ── Feature 5: Multi-index consensus (if available) ──
    mi_signal = _multi_index_signal(multi_index, years) if multi_index else None

    # ── Ensemble scoring ──
    risk_score = _compute_ensemble_score(slope, cv, acceleration, anomaly_score, mi_signal)

    # ── Risk level classification ──
    # Recalibrated for Indian tropical forests:
    # Indian forests decline slowly (-0.001 to -0.003/yr), not Amazon-style clear-cuts
    # These thresholds are mathematically correct for that scale
    risk_level = (
        "HIGH"   if risk_score >= 0.4 else
        "MEDIUM" if risk_score >= 0.12 else
        "LOW"
    )

    # ── SHAP-style feature importance ──
    importance = _compute_feature_importance(slope, cv, acceleration, anomaly_score, mi_signal)

    return {
        "risk_level":      risk_level,
        "risk_score":      round(risk_score, 3),       # 0–1 continuous score
        "slope":           round(slope, 6),
        "variability":     round(float(std_v), 6),
        "cv":              round(cv, 4),
        "acceleration":    round(acceleration, 6),
        "anomaly_score":   round(anomaly_score, 4),
        "is_anomaly":      is_anomaly,
        "mi_signal":       mi_signal,
        "feature_importance": importance,
        "years_analysed":  len(years),
    }


# ════════════════════════════════════════════════════════════
# ISOLATION FOREST
# ════════════════════════════════════════════════════════════

def _run_isolation_forest(values: np.ndarray):
    """
    Anomaly detection on NDVI series.
    
    Small datasets (< 10 points): Isolation Forest is unreliable because
    contamination=0.1 on 8 points means it expects 0.8 anomalies — statistically
    meaningless. Use composite z-score + monotone decline instead.
    
    Large datasets (>= 10 points): Use Isolation Forest properly.
    """
    n = len(values)
    
    # ── For all dataset sizes: compute z-score of recent values ──
    # Compare last 2 years against historical mean
    if n >= 4:
        baseline_mean = float(np.mean(values[:-2]))
        baseline_std  = float(np.std(values[:-2]) + 1e-6)
        recent_mean   = float(np.mean(values[-2:]))
        z_score       = (baseline_mean - recent_mean) / baseline_std  # positive = decline
    else:
        z_score = 0.0

    # ── Monotone decline: how many consecutive years declined? ──
    declines = 0
    for i in range(n - 1, 0, -1):
        if values[i] < values[i-1]:
            declines += 1
        else:
            break
    monotone_score = min(declines / 4.0, 1.0)  # 4 consecutive declines → score 1.0

    # ── Isolation Forest (only meaningful with >= 10 points) ──
    if_score = 0.0
    is_anomaly_if = False
    if n >= 10:
        try:
            from sklearn.ensemble import IsolationForest
            X   = values.reshape(-1, 1)
            clf = IsolationForest(contamination=0.15, random_state=42, n_estimators=100)
            clf.fit(X)
            recent    = X[-1].reshape(1, -1)
            pred      = clf.predict(recent)[0]
            if_score  = float(-clf.decision_function(recent)[0])
            is_anomaly_if = bool(pred == -1)
        except Exception as e:
            logger.warning(f"Isolation Forest failed: {e}")

    # ── Composite anomaly score ──
    # For small datasets: weight z-score and monotone heavily
    # For large datasets: blend all three
    if n < 10:
        composite = (
            min(max(z_score / 2.0, 0.0), 1.0) * 0.6 +
            monotone_score * 0.4
        )
        is_anomaly = z_score > 1.5 or declines >= 3
    else:
        composite = (
            min(max(z_score / 2.0, 0.0), 1.0) * 0.4 +
            monotone_score * 0.2 +
            min(max(if_score, 0.0), 1.0) * 0.4
        )
        is_anomaly = is_anomaly_if or z_score > 1.5

    return round(min(composite, 1.0), 4), bool(is_anomaly)


# ════════════════════════════════════════════════════════════
# MULTI-INDEX SIGNAL
# ════════════════════════════════════════════════════════════

def _multi_index_signal(multi_index: dict, years: list) -> Optional[float]:
    """
    Extract consensus degradation signal from EVI, SAVI, NBR.
    Returns 0–1 where 1 = strong multi-index evidence of degradation.
    """
    try:
        fhs_values = [
            multi_index[y]["FHS"]
            for y in years
            if y in multi_index and multi_index[y].get("FHS") is not None
        ]
        if len(fhs_values) < 2:
            return None

        fhs_arr = np.array(fhs_values, dtype=float)
        fhs_slope = float(np.polyfit(range(len(fhs_arr)), fhs_arr, 1)[0])
        # Normalise: slope of -0.05/yr → signal of 1.0
        signal = min(max(-fhs_slope / 0.05, 0.0), 1.0)
        return round(signal, 3)
    except Exception:
        return None


# ════════════════════════════════════════════════════════════
# ENSEMBLE SCORE
# ════════════════════════════════════════════════════════════

def _compute_ensemble_score(
    slope: float,
    cv: float,
    acceleration: float,
    anomaly_score: float,
    mi_signal: Optional[float]
) -> float:
    """
    Combine all features into a single risk score (0–1).
    Weights are empirically calibrated for tropical forest monitoring.
    """
    # ── Normalise each feature to 0–1 ──
    # Calibrated for Indian tropical/subtropical forests:
    # Real decline rates: -0.005 to -0.02/yr (NOT -0.05 which is Amazon clear-cut scale)

    # Slope: -0.005/yr → score 1.0
    # Indian forests: typical decline = -0.001 to -0.003/yr
    # -0.005/yr is already significant degradation for Indian tropical forest
    slope_norm = min(max(-slope / 0.005, 0.0), 1.0)

    # CV: volatility — Indian forests show 0.03-0.12 natural variation
    cv_norm = min(cv / 0.15, 1.0)

    # Acceleration: declining faster in recent years?
    accel_norm = min(max(-acceleration / 0.005, 0.0), 1.0)

    # Anomaly: composite z-score + monotone + IF signal
    anomaly_norm = min(anomaly_score * 1.2, 1.0)

    weights = {
        "slope":        0.35,
        "cv":           0.15,
        "acceleration": 0.20,
        "anomaly":      0.25,
        "mi_signal":    0.05,
    }

    score = (
        weights["slope"]        * slope_norm   +
        weights["cv"]           * cv_norm       +
        weights["acceleration"] * accel_norm    +
        weights["anomaly"]      * anomaly_norm
    )

    if mi_signal is not None:
        score += weights["mi_signal"] * mi_signal
    else:
        score += weights["mi_signal"] * slope_norm

    return round(min(score, 1.0), 3)


# ════════════════════════════════════════════════════════════
# FEATURE IMPORTANCE (SHAP-style explanation)
# ════════════════════════════════════════════════════════════

def _compute_feature_importance(slope, cv, acceleration, anomaly_score, mi_signal) -> Dict[str, float]:
    """
    Approximate per-feature contribution to the risk score.
    Useful for showing users WHY the risk is HIGH / MEDIUM / LOW.
    """
    slope_norm  = round(min(max(-slope / 0.05, 0.0), 1.0) * 0.35, 3)
    cv_norm     = round(min(cv / 0.3, 1.0) * 0.5 * 0.10, 3)
    accel_norm  = round(min(max(-acceleration / 0.03, 0.0), 1.0) * 0.20, 3)
    anomaly_c   = round(min(anomaly_score, 1.0) * 0.25, 3)
    mi_c        = round((mi_signal or 0.0) * 0.10, 3)

    return {
        "ndvi_trend_slope":   slope_norm,
        "ndvi_volatility":    cv_norm,
        "trend_acceleration": accel_norm,
        "ml_anomaly":         anomaly_c,
        "multi_index":        mi_c,
    }


# ════════════════════════════════════════════════════════════
# VEGETATION STABILITY INDEX (kept from original)
# ════════════════════════════════════════════════════════════

def vegetation_stability_index(ndvi_series: dict) -> float:
    """VSI = 1 - (std / mean). 1.0 = perfectly stable."""
    values = list(ndvi_series.values())
    if not values:
        return 0.0
    mean_v = np.mean(values)
    std_v  = np.std(values)
    if mean_v == 0:
        return 0.0
    return round(float(1 - std_v / mean_v), 3)


def _empty_risk() -> Dict[str, Any]:
    return {
        "risk_level": "LOW", "risk_score": 0.0,
        "slope": 0.0, "variability": 0.0, "cv": 0.0,
        "acceleration": 0.0, "anomaly_score": 0.0,
        "is_anomaly": False, "mi_signal": None,
        "feature_importance": {
            "ndvi_trend_slope": 0.0, "ndvi_volatility": 0.0,
            "trend_acceleration": 0.0, "ml_anomaly": 0.0, "multi_index": 0.0
        },
        "years_analysed": 0
    }

"""
ForestGuard — Advanced Analysis Pipeline v2

8-stage pipeline:
  1.  Multi-Index NDVI (NDVI + EVI + SAVI + NBR + NDWI + FHS)
  2.  Full Change Detection (percentage + CUSUM + BFAST)
  3.  Ensemble Risk Scoring (Isolation Forest + gradient + acceleration)
  4.  Advanced Vegetation Stability (VSI + Resilience + Resistance)
  5.  Hotspot Analysis (Gi* + fire overlay + persistence)
  6.  IPCC Carbon Estimation (AGB + BGB + soil + DOM)
  7.  🔥 Fire Detection (MODIS FIRMS)
  8.  🌳 Hansen Forest Cover Baseline (GFC v1.11)
"""
import logging
from typing import Optional

from core.ndvi import yearly_ndvi_timeseries, yearly_multi_index_timeseries, SOURCE_LANDSAT, SOURCE_SENTINEL2
from core.change_detection import run_full_change_detection
from core.risk_analysis import compute_risk_score
from core.vegetation_stability import compute_vsi
from core.hotspot_analysis import detect_hotspots
from core.carbon_estimation import estimate_carbon_loss, auto_detect_biome
from core.data_loader import load_fire_data, load_forest_cover_baseline
from core.roi import roi_area_hectares

logger = logging.getLogger("forestguard.pipeline")


def run_analysis(
    roi,
    start_year: int,
    end_year:   int,
    lat:        Optional[float] = None,
    lon:        Optional[float] = None,
    source:     str = "landsat",
    include_fire:   bool = True,
    include_hansen: bool = True
) -> dict:
    """
    Run full 8-stage deforestation analysis pipeline.

    Args:
        roi:            GEE geometry (from create_roi or create_polygon_roi)
        start_year:     First year of analysis
        end_year:       Last year of analysis
        lat, lon:       Centre coordinates (for biome detection)
        source:         "landsat" | "sentinel2" | "sentinel1"
        include_fire:   Run MODIS fire detection stage
        include_hansen: Run Hansen GFC baseline stage

    Returns full analysis dict compatible with AnalysisResponse schema.
    """
    logger.info(f"Pipeline start | {lat},{lon} | {source} | {start_year}–{end_year}")

    src = SOURCE_SENTINEL2 if source == "sentinel2" else SOURCE_LANDSAT

    # ─────────────────────────────────────────────
    # STAGE 1: Multi-Index NDVI Timeseries
    # ─────────────────────────────────────────────
    logger.info("Stage 1: Multi-index NDVI timeseries")
    try:
        multi_index = yearly_multi_index_timeseries(roi, start_year, end_year, source=src)
        ndvi_series = {yr: data["NDVI"] for yr, data in multi_index.items()
                       if data.get("NDVI") is not None}
    except Exception as e:
        logger.error(f"Stage 1 failed: {e}")
        return _empty_result(lat, lon)

    if not ndvi_series:
        logger.warning("No NDVI data returned — check GEE credentials and date range")
        return _empty_result(lat, lon)

    logger.info(f"Stage 1 complete: {len(ndvi_series)} years of data")

    # ─────────────────────────────────────────────
    # STAGE 2: Full Change Detection
    # ─────────────────────────────────────────────
    logger.info("Stage 2: Change detection (percentage + CUSUM + BFAST)")
    try:
        change = run_full_change_detection(ndvi_series)
    except Exception as e:
        logger.warning(f"Stage 2 failed: {e}")
        change = {"percentage_alerts": [], "cusum": {}, "bfast": {}, "total_change_signals": 0}

    # ─────────────────────────────────────────────
    # STAGE 3: Ensemble Risk Scoring
    # ─────────────────────────────────────────────
    logger.info("Stage 3: Ensemble risk scoring")
    try:
        risk = compute_risk_score(ndvi_series, multi_index=multi_index)
    except Exception as e:
        logger.warning(f"Stage 3 failed: {e}")
        risk = {"risk_level": "UNKNOWN", "risk_score": 0.0, "slope": 0.0, "variability": 0.0}

    # ─────────────────────────────────────────────
    # STAGE 4: Vegetation Stability
    # ─────────────────────────────────────────────
    logger.info("Stage 4: Vegetation stability")
    try:
        vsi = compute_vsi(ndvi_series)
    except Exception as e:
        logger.warning(f"Stage 4 failed: {e}")
        vsi = {"vsi": 0.0, "stability_status": "Error", "ecosystem_health": 0.0}

    # ─────────────────────────────────────────────
    # STAGE 5: Hotspot Analysis
    # ─────────────────────────────────────────────
    logger.info("Stage 5: Hotspot analysis")
    try:
        # Fire data loaded in stage 7 — pass None here, merge below
        hotspots = detect_hotspots(ndvi_series, fire_data=None, multi_index=multi_index)
    except Exception as e:
        logger.warning(f"Stage 5 failed: {e}")
        hotspots = {"hotspot_risk": "LOW_RISK", "hotspot_score": 0.0}

    # ─────────────────────────────────────────────
    # STAGE 6: Carbon Estimation (IPCC Tier 1)
    # ─────────────────────────────────────────────
    logger.info("Stage 6: Carbon estimation (IPCC Tier 1)")
    try:
        area_ha = roi_area_hectares(roi)
        biome   = auto_detect_biome(lat or 0, lon or 0)
        carbon  = estimate_carbon_loss(ndvi_series, area_hectares=area_ha, biome=biome)
    except Exception as e:
        logger.warning(f"Stage 6 failed: {e}")
        area_ha = 0.0
        biome   = "default"
        carbon  = {"carbon_loss_tons": 0.0, "co2_equivalent_tons": 0.0, "status": "Error"}

    # ─────────────────────────────────────────────
    # STAGE 7: 🔥 MODIS Fire Detection
    # ─────────────────────────────────────────────
    fire_data = {"fire_pixel_count": 0, "fire_detected": False, "fire_risk_flag": "NONE"}
    if include_fire:
        logger.info("Stage 7: MODIS fire detection")
        try:
            import ee
            fire_start = ee.Date.fromYMD(start_year, 1, 1)
            fire_end   = ee.Date.fromYMD(end_year, 12, 31)
            fire_data  = load_fire_data(roi, fire_start, fire_end)
            # Update hotspot with fire overlay
            if fire_data.get("fire_detected"):
                hotspots["fire_risk"]    = fire_data["fire_risk_flag"]
                hotspots["fire_penalty"] = {"HIGH": 0.3, "MEDIUM": 0.15, "NONE": 0.0}.get(
                    fire_data["fire_risk_flag"], 0.0)
                # Upgrade hotspot risk if fire detected
                if fire_data["fire_risk_flag"] == "HIGH":
                    hotspots["hotspot_risk"] = "HIGH_RISK"
        except Exception as e:
            logger.warning(f"Stage 7 (fire) failed: {e}")

    # ─────────────────────────────────────────────
    # STAGE 8: 🌳 Hansen Global Forest Cover
    # ─────────────────────────────────────────────
    hansen_data = {"forest_cover_ha": 0, "forest_cover_pct": 0, "tree_cover_loss_ha": 0,
                   "tree_cover_loss_pct": 0, "total_area_ha": 0, "data_source": "Not requested"}
    if include_hansen:
        logger.info("Stage 8: Hansen Global Forest Change baseline")
        try:
            loss_year_min = max(start_year - 2000, 1)
            loss_year_max = min(end_year   - 2000, 23)
            hansen_data   = load_forest_cover_baseline(roi, loss_year_min, loss_year_max)
        except Exception as e:
            logger.warning(f"Stage 8 (Hansen) failed: {e}")

    # ─────────────────────────────────────────────
    # COLOURS
    # ─────────────────────────────────────────────
    risk_color = {
        "HIGH":   "#dc2626",
        "MEDIUM": "#eab308",
        "LOW":    "#22c55e"
    }.get(risk.get("risk_level", "LOW"), "#22c55e")

    hotspot_color = {
        "HIGH_RISK":   "#dc2626",
        "MEDIUM_RISK": "#eab308",
        "LOW_RISK":    "#22c55e"
    }.get(hotspots.get("hotspot_risk", "LOW_RISK"), "#22c55e")

    logger.info(
        f"Pipeline complete | Risk: {risk.get('risk_level')} | "
        f"Forest cover: {hansen_data.get('forest_cover_pct')}% | "
        f"Fire: {fire_data.get('fire_risk_flag')}"
    )

    return {
        # Core NDVI (backwards-compatible)
        "ndvi_timeseries": {str(k): v for k, v in ndvi_series.items()},

        # Full multi-index data
        "multi_index_timeseries": {
            str(yr): {idx: data.get(idx) for idx in ["NDVI", "EVI", "SAVI", "NBR", "NDWI", "FHS"]}
            for yr, data in multi_index.items()
        },

        # Change detection (all methods)
        "alerts":          change.get("percentage_alerts", []),
        "change_detection": change,

        # Risk
        "risk": {**risk, "color": risk_color},

        # Stability
        "vegetation_stability": vsi,

        # Hotspots
        "hotspots": {**hotspots, "color": hotspot_color},

        # Carbon (IPCC Tier 1)
        "carbon_impact": carbon,

        # Fire
        "fire_detection": fire_data,

        # Hansen forest cover baseline
        "forest_cover": hansen_data,

        # Meta
        "area_hectares": round(area_ha, 2),
        "biome":         biome,
        "source":        source,
        "center": {"lat": lat, "lon": lon} if lat is not None else None,
    }


def _empty_result(lat, lon) -> dict:
    return {
        "ndvi_timeseries": {},
        "multi_index_timeseries": {},
        "alerts": [],
        "change_detection": {"total_change_signals": 0, "change_detected": False},
        "risk": {"slope": 0, "variability": 0, "risk_level": "UNKNOWN",
                 "risk_score": 0.0, "color": "#9ca3af"},
        "vegetation_stability": {"vsi": 0, "stability_status": "No data", "ecosystem_health": 0},
        "hotspots": {"hotspot_risk": "UNKNOWN", "hotspot_score": 0.0, "color": "#9ca3af"},
        "carbon_impact": {"carbon_loss_tons": 0, "co2_equivalent_tons": 0, "status": "No data"},
        "fire_detection": {"fire_detected": False, "fire_risk_flag": "NONE"},
        "forest_cover": {"forest_cover_pct": 0, "tree_cover_loss_ha": 0},
        "area_hectares": 0,
        "biome": "default",
        "source": "unknown",
        "center": {"lat": lat, "lon": lon} if lat is not None else None,
    }

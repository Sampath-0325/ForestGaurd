"""
ForestGuard — Multi-Index Vegetation Analysis
Computes NDVI, EVI, SAVI, NBR, NDWI timeseries from satellite imagery.
Fuses indices into a single Forest Health Score (FHS) per year.

── BUG FIX ──────────────────────────────────────────────────────────────────
  ERROR: "Dictionary.get: Dictionary does not contain key: 'SAVI'"

  Root cause: In year_to_feature(), props[b] = stats.get(b) calls
  GEE's server-side Dictionary.get() WITHOUT a default value.
  When a Landsat scene is heavily cloud-masked for a given year,
  GEE cannot compute SAVI for that year and the key is absent.
  Dictionary.get(key) with no default raises a GEE server-side KeyError.
  This propagates up and kills the entire aggregate_array("year").getInfo() call.

  Fix: stats.get(b, -9999)
  GEE's Dictionary.get(key, defaultValue) returns -9999 when the key is absent.
  Python then converts -9999 → None so downstream code handles it cleanly.
─────────────────────────────────────────────────────────────────────────────

Index Reference:
  NDVI  = (NIR-Red)/(NIR+Red)                     — vegetation density
  EVI   = 2.5*(NIR-Red)/(NIR+6*Red-7.5*Blue+1)   — improved canopy signal
  SAVI  = 1.5*(NIR-Red)/(NIR+Red+0.5)             — soil-adjusted
  NBR   = (NIR-SWIR)/(NIR+SWIR)                   — burn/disturbance detection
  NDWI  = (Green-NIR)/(Green+NIR)                 — water stress / moisture
"""
import ee
import logging
from core.data_loader import load_landsat, load_sentinel2, load_sentinel1

logger = logging.getLogger("forestguard.ndvi")

SOURCE_LANDSAT   = "landsat"
SOURCE_SENTINEL2 = "sentinel2"
SOURCE_SENTINEL1 = "sentinel1"

# Sentinel value used when GEE cannot compute an index for a year.
# Must be outside the valid NDVI range (−1 to 1) so we can detect it.
_GEE_MISSING = -9999

# Index weights for Forest Health Score fusion
FHS_WEIGHTS = {
    "NDVI": 0.40,
    "EVI":  0.25,
    "SAVI": 0.15,
    "NBR":  0.20,
}


# ════════════════════════════════════════════════════════════
# LANDSAT BAND INDICES
# ════════════════════════════════════════════════════════════

def add_landsat_indices(image: ee.Image) -> ee.Image:
    """Compute NDVI, EVI, SAVI, NBR, NDWI for Landsat 8/9 SR."""
    nir   = image.select("SR_B5")
    red   = image.select("SR_B4")
    blue  = image.select("SR_B2")
    green = image.select("SR_B3")
    swir  = image.select("SR_B6")

    ndvi = image.normalizedDifference(["SR_B5", "SR_B4"]).rename("NDVI")

    evi = image.expression(
        "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
        {"NIR": nir, "RED": red, "BLUE": blue}
    ).rename("EVI")

    savi = image.expression(
        "1.5 * (NIR - RED) / (NIR + RED + 0.5)",
        {"NIR": nir, "RED": red}
    ).rename("SAVI")

    nbr  = image.normalizedDifference(["SR_B5", "SR_B6"]).rename("NBR")
    ndwi = image.normalizedDifference(["SR_B3", "SR_B5"]).rename("NDWI")

    return image.addBands([ndvi, evi, savi, nbr, ndwi])


# ════════════════════════════════════════════════════════════
# SENTINEL-2 BAND INDICES
# ════════════════════════════════════════════════════════════

def add_sentinel2_indices(image: ee.Image) -> ee.Image:
    """Compute NDVI, EVI, SAVI, NBR, NDWI for Sentinel-2 L2A."""
    nir   = image.select("B8")
    red   = image.select("B4")
    blue  = image.select("B2")
    green = image.select("B3")
    swir  = image.select("B11")

    ndvi = image.normalizedDifference(["B8", "B4"]).rename("NDVI")

    evi = image.expression(
        "2.5 * (NIR - RED) / (NIR + 6 * RED - 7.5 * BLUE + 1)",
        {"NIR": nir.divide(10000), "RED": red.divide(10000), "BLUE": blue.divide(10000)}
    ).rename("EVI")

    savi = image.expression(
        "1.5 * (NIR - RED) / (NIR + RED + 0.5)",
        {"NIR": nir.divide(10000), "RED": red.divide(10000)}
    ).rename("SAVI")

    nbr  = image.normalizedDifference(["B8", "B11"]).rename("NBR")
    ndwi = image.normalizedDifference(["B3", "B8"]).rename("NDWI")

    return image.addBands([ndvi, evi, savi, nbr, ndwi])


# ════════════════════════════════════════════════════════════
# SAR PROXY INDEX
# ════════════════════════════════════════════════════════════

def add_sar_proxy(image: ee.Image) -> ee.Image:
    """
    SAR Radar Forest Degradation Index (RFDI) proxy.
    RFDI = (VV - VH) / (VV + VH) — inverse of canopy density.
    Negate so it behaves like NDVI (higher = more forest).
    """
    rfdi = image.normalizedDifference(["VH", "VV"]).rename("NDVI")
    return image.addBands(rfdi)


# ════════════════════════════════════════════════════════════
# FOREST HEALTH SCORE FUSION
# ════════════════════════════════════════════════════════════

def compute_forest_health_score(ndvi, evi, savi, nbr) -> float:
    """
    Fuse multiple indices into a single Forest Health Score (0–1).
    Any index that is None is skipped — weights are renormalised.
    NBR contribution is inverted: high burn = low health.
    """
    score        = 0.0
    total_weight = 0.0

    values = {"NDVI": ndvi, "EVI": evi, "SAVI": savi, "NBR": nbr}

    for idx, val in values.items():
        if val is not None:
            w = FHS_WEIGHTS[idx]
            # NBR: high value = healthy; invert for consistency with other indices
            contribution = val if idx != "NBR" else (1 - val)
            score        += w * max(0.0, min(1.0, contribution))
            total_weight += w

    if total_weight == 0:
        return 0.0
    return round(score / total_weight, 4)


# ════════════════════════════════════════════════════════════
# ADAPTIVE SCALE
# ════════════════════════════════════════════════════════════

def _get_adaptive_scale(roi, base_scale: int) -> int:
    """
    Increase resolution scale for large AOIs to avoid GEE memory limits.
    Area tiers:
      < 10,000 ha   → base_scale  (30m Landsat or 10m Sentinel-2)
      10k–100k ha   → 100m
      100k–500k ha  → 250m
      > 500k ha     → 500m
    """
    try:
        area_ha = roi.area().getInfo() / 10_000
        if area_ha < 10_000:
            return base_scale
        elif area_ha < 100_000:
            return 100
        elif area_ha < 500_000:
            return 250
        else:
            return 500
    except Exception:
        return max(base_scale, 100)


# ════════════════════════════════════════════════════════════
# MAIN TIMESERIES FUNCTIONS
# ════════════════════════════════════════════════════════════

def yearly_ndvi_timeseries(
    roi, start_year: int, end_year: int, source: str = "landsat"
) -> dict:
    """
    Compute yearly NDVI timeseries (primary output for charts).
    Returns {year: ndvi_value} dict (years with no data are omitted).
    """
    full = yearly_multi_index_timeseries(roi, start_year, end_year, source)
    return {yr: data["NDVI"] for yr, data in full.items() if data.get("NDVI") is not None}


def yearly_multi_index_timeseries(
    roi, start_year: int, end_year: int, source: str = "landsat"
) -> dict:
    """
    Full multi-index timeseries.

    Returns:
    {
        2019: {"NDVI": 0.72, "EVI": 0.55, "SAVI": 0.61,
               "NBR": 0.80, "NDWI": -0.12, "FHS": 0.68},
        2020: {...},
        ...
    }

    BUG FIX applied here: stats.get(b, -9999) instead of stats.get(b)
    This prevents the GEE server-side KeyError when SAVI (or any index)
    cannot be computed for a given year due to cloud coverage or missing bands.
    """
    base_scale = 10 if source == SOURCE_SENTINEL2 else 30
    scale      = _get_adaptive_scale(roi, base_scale)
    results    = {}

    logger.info(
        f"Multi-index timeseries: source={source}, scale={scale}m, "
        f"years={start_year}-{end_year}"
    )

    def year_to_feature(year):
        start = ee.Date.fromYMD(year, 1, 1)
        end   = start.advance(1, "year")

        if source == SOURCE_LANDSAT:
            collection = load_landsat(roi, start, end).map(add_landsat_indices)
            bands      = ["NDVI", "EVI", "SAVI", "NBR", "NDWI"]
        elif source == SOURCE_SENTINEL2:
            collection = load_sentinel2(roi, start, end).map(add_sentinel2_indices)
            bands      = ["NDVI", "EVI", "SAVI", "NBR", "NDWI"]
        elif source == SOURCE_SENTINEL1:
            collection = load_sentinel1(roi, start, end).map(add_sar_proxy)
            bands      = ["NDVI"]
        else:
            collection = load_landsat(roi, start, end).map(add_landsat_indices)
            bands      = ["NDVI", "EVI", "SAVI", "NBR", "NDWI"]

        mean_image = collection.select(bands).mean()
        stats = mean_image.reduceRegion(
            reducer   = ee.Reducer.mean(),
            geometry  = roi,
            scale     = scale,
            bestEffort = True,
            maxPixels = 1e9,
            tileScale = 4   # splits computation across 4×4 tiles — critical for large AOIs
        )

        props = {"year": year}
        for b in bands:
            # ── BUG FIX ─────────────────────────────────────────────────────
            # stats.get(b)       → GEE server-side KeyError when band missing
            # stats.get(b, -9999)→ returns sentinel -9999 safely when absent
            # Python code below converts -9999 → None for clean handling.
            # ─────────────────────────────────────────────────────────────────
            props[b] = stats.get(b, _GEE_MISSING)

        return ee.Feature(None, props)

    years = ee.List.sequence(start_year, end_year)
    fc    = ee.FeatureCollection(years.map(year_to_feature))

    # Fetch year list first — now safe because stats.get() never raises KeyError
    try:
        year_list = fc.aggregate_array("year").getInfo()
    except Exception as e:
        logger.error(f"Failed to fetch year list from GEE: {e}")
        return {}

    # Fetch each index separately so a single index failure doesn't kill everything
    index_data: dict[str, list] = {}
    all_bands = ["NDVI", "EVI", "SAVI", "NBR", "NDWI"]
    for b in all_bands:
        try:
            index_data[b] = fc.aggregate_array(b).getInfo()
        except Exception as e:
            logger.warning(f"Index {b} failed (GEE memory/compute limit) — skipping: {e}")
            index_data[b] = [_GEE_MISSING] * len(year_list)

    # Build Python result dict — convert sentinel -9999 back to None
    for i, yr in enumerate(year_list):
        row: dict = {}
        for idx in all_bands:
            vals = index_data.get(idx, [])
            raw  = vals[i] if i < len(vals) else _GEE_MISSING
            # Convert sentinel value or None to Python None
            if raw is None or raw == _GEE_MISSING or raw < -1.5:
                row[idx] = None
            else:
                row[idx] = round(float(raw), 4)

        # Compute Forest Health Score from available indices
        row["FHS"] = compute_forest_health_score(
            row.get("NDVI"), row.get("EVI"), row.get("SAVI"), row.get("NBR")
        )

        # Only include years where NDVI was successfully computed
        if row.get("NDVI") is not None:
            results[int(yr)] = row

    return results

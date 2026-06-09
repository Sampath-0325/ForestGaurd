"""
ForestGuard Data Loader
Loads satellite imagery from multiple sources:
  - Landsat 8/9 (30m, optical)
  - Sentinel-2 L2A Harmonized (10m, optical)
  - Sentinel-1 SAR GRD (10m, radar — cloud-penetrating)
  - MODIS FIRMS (fire/thermal anomaly detection)
  - Hansen Global Forest Change (forest cover baseline)
"""
import ee
import logging

logger = logging.getLogger("forestguard.data")

# ═══════════════════════════════════════════════════
# LANDSAT 8/9
# ═══════════════════════════════════════════════════

def mask_landsat_clouds(image: ee.Image) -> ee.Image:
    """Cloud + shadow + snow masking via QA_PIXEL (Landsat C02)."""
    qa = image.select("QA_PIXEL")
    mask = (
        qa.bitwiseAnd(1 << 3).eq(0)   # cloud
        .And(qa.bitwiseAnd(1 << 4).eq(0))  # cloud shadow
        .And(qa.bitwiseAnd(1 << 5).eq(0))  # snow
        .And(qa.bitwiseAnd(1 << 1).eq(0))  # dilated cloud
    )
    return image.updateMask(mask)


def scale_landsat(image: ee.Image) -> ee.Image:
    """Apply official Landsat C02 L2 scaling factors."""
    optical = image.select("SR_B.").multiply(0.0000275).add(-0.2)
    thermal = image.select("ST_B.*").multiply(0.00341802).add(149.0)
    return image.addBands(optical, overwrite=True).addBands(thermal, overwrite=True)


def load_landsat(roi, start_date, end_date, cloud_cover_max=30, use_qa_mask=True):
    """
    Load Landsat 8 & 9 merged Surface Reflectance with cloud masking.
    Merging L8 + L9 maximises temporal coverage.
    """
    def _load(collection_id):
        col = (
            ee.ImageCollection(collection_id)
            .filterBounds(roi)
            .filterDate(start_date, end_date)
            .filter(ee.Filter.lt("CLOUD_COVER", cloud_cover_max))
        )
        if use_qa_mask:
            col = col.map(mask_landsat_clouds)
        return col.map(scale_landsat)

    l8 = _load("LANDSAT/LC08/C02/T1_L2")
    l9 = _load("LANDSAT/LC09/C02/T1_L2")
    return l8.merge(l9).sort("system:time_start")


# ════════════════════════════════════════════════════
# SENTINEL-2
# ════════════════════════════════════════════════════

def mask_s2_clouds(image: ee.Image) -> ee.Image:
    """Cloud masking via QA60 band (bits 10=opaque cloud, 11=cirrus)."""
    qa = image.select("QA60")
    mask = qa.bitwiseAnd(1 << 10).eq(0).And(qa.bitwiseAnd(1 << 11).eq(0))
    return image.updateMask(mask)


def add_s2_ndvi(image: ee.Image) -> ee.Image:
    """Add NDVI band: (B8-B4)/(B8+B4)."""
    return image.addBands(image.normalizedDifference(["B8", "B4"]).rename("NDVI"))


def load_sentinel2(roi, start_date, end_date, cloud_prob_max=50):
    """
    Load Sentinel-2 L2A Harmonized with cloud masking + NDVI.
    10m resolution — finer-grained than Landsat.
    """
    return (
        ee.ImageCollection("COPERNICUS/S2_SR_HARMONIZED")
        .filterBounds(roi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.lt("CLOUDY_PIXEL_PERCENTAGE", cloud_prob_max))
        .map(mask_s2_clouds)
        .map(add_s2_ndvi)
    )


# ════════════════════════════════════════════════════
# SENTINEL-1 SAR
# ════════════════════════════════════════════════════

def load_sentinel1(roi, start_date, end_date):
    """
    Load Sentinel-1 SAR GRD (IW mode, VV+VH).
    SAR penetrates clouds — critical for tropical/monsoon regions.
    """
    return (
        ee.ImageCollection("COPERNICUS/S1_GRD")
        .filterBounds(roi)
        .filterDate(start_date, end_date)
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VV"))
        .filter(ee.Filter.listContains("transmitterReceiverPolarisation", "VH"))
        .filter(ee.Filter.eq("instrumentMode", "IW"))
        .select(["VV", "VH"])
    )


# ════════════════════════════════════════════════════
# 🔥 NEW: MODIS FIRMS Fire Detection
# ════════════════════════════════════════════════════

def load_fire_data(roi, start_date, end_date) -> dict:
    """
    Load MODIS FIRMS active fire detections.
    Returns fire pixel count and confidence stats for the ROI.

    Dataset: MODIS/061/MOD14A1 (daily global fire mask, 1km)
    """
    try:
        fire_col = (
            ee.ImageCollection("MODIS/061/MOD14A1")
            .filterBounds(roi)
            .filterDate(start_date, end_date)
            .select("FireMask")
        )

        # FireMask values 7,8,9 = high/medium/low confidence fire
        fire_composite = fire_col.max()
        fire_pixels = fire_composite.gte(7)  # confident fire detections only

        stats = fire_pixels.reduceRegion(
            reducer=ee.Reducer.sum(),
            geometry=roi,
            scale=1000,
            maxPixels=1e8,
            bestEffort=True
        )

        fire_count = stats.get("FireMask").getInfo() or 0

        return {
            "fire_pixel_count": int(fire_count),
            "fire_detected": fire_count > 0,
            "fire_risk_flag": "HIGH" if fire_count > 10 else ("MEDIUM" if fire_count > 2 else "NONE")
        }
    except Exception as e:
        logger.warning(f"Fire data load failed: {e}")
        return {"fire_pixel_count": 0, "fire_detected": False, "fire_risk_flag": "NONE"}


# ═════════════════════════════════════════════════════
# 🌳 NEW: Hansen Global Forest Change
# ═════════════════════════════════════════════════════

def load_forest_cover_baseline(roi, loss_year_min: int = 1, loss_year_max: int = 23) -> dict:
    """
    Load Hansen Global Forest Change v1.11 (2000–2023).
    Returns baseline forest cover % and cumulative tree cover loss area.

    Bands used:
      - treecover2000: % canopy cover in year 2000 (baseline)
      - loss: binary mask of pixels that lost cover 2001–2023
      - lossyear: year of loss (1=2001 … 23=2023)

    Reference: Hansen et al. 2013, Science
    """
    try:
        hansen = ee.Image("UMD/hansen/global_forest_change_2024_v1_12")

        # Baseline forest cover (>30% canopy)
        forest_mask = hansen.select("treecover2000").gte(30)

        # Loss pixels within requested year range
        loss_year = hansen.select("lossyear")
        loss_in_range = (
            hansen.select("loss")
            .And(loss_year.gte(loss_year_min))
            .And(loss_year.lte(loss_year_max))
        )

        area_image = ee.Image.pixelArea().divide(10000)  # m² → hectares

        # Forest cover area (ha)
        forest_area_stats = (
            area_image.updateMask(forest_mask)
            .reduceRegion(ee.Reducer.sum(), roi, 30, maxPixels=1e9, bestEffort=True)
        )

        # Loss area (ha)
        loss_area_stats = (
            area_image.updateMask(loss_in_range)
            .reduceRegion(ee.Reducer.sum(), roi, 30, maxPixels=1e9, bestEffort=True)
        )

        forest_ha = forest_area_stats.get("area").getInfo() or 0
        loss_ha   = loss_area_stats.get("area").getInfo() or 0

        # Total ROI area
        total_ha = roi.area().getInfo() / 10000
        forest_pct = (forest_ha / total_ha * 100) if total_ha > 0 else 0
        loss_pct   = (loss_ha  / forest_ha * 100) if forest_ha > 0 else 0

        return {
            "forest_cover_ha":       round(forest_ha, 2),
            "forest_cover_pct":      round(forest_pct, 1),
            "tree_cover_loss_ha":    round(loss_ha, 2),
            "tree_cover_loss_pct":   round(loss_pct, 1),
            "total_area_ha":         round(total_ha, 2),
            "data_source":           "Hansen GFC v1.12 (2000–2024)"
        }

    except Exception as e:
        logger.warning(f"Hansen GFC load failed: {e}")
        return {
            "forest_cover_ha": 0, "forest_cover_pct": 0,
            "tree_cover_loss_ha": 0, "tree_cover_loss_pct": 0,
            "total_area_ha": 0, "data_source": "Unavailable"
        }
        

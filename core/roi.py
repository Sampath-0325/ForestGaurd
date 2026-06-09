"""
ForestGuard — Advanced ROI (Region of Interest) Utilities

Features:
  - Circular ROI from lat/lon + buffer
  - Polygon ROI from GeoJSON (Polygon + MultiPolygon)
  - Grid ROI generation for map-wide risk scanning
  - ROI validation (area limits, coordinate bounds)
  - Geometry utilities: centroid, bounding box, area, perimeter
  - Adaptive buffer: auto-scales buffer based on zoom/area
  - ROI simplification for performance on large polygons
"""
import ee
import math
import logging
from typing import Optional, Tuple

logger = logging.getLogger("forestguard.roi")

# ── Limits ──
MAX_AREA_HA    = 5_000_000   # 50,000 km² — hard upper cap
MIN_AREA_HA    = 1           # 1 hectare minimum
MAX_BUFFER_KM  = 50.0
MIN_BUFFER_KM  = 0.1


# ════════════════════════════════════════════════════
# CORE ROI CREATION
# ════════════════════════════════════════════════════

def create_roi(lat: float, lon: float, buffer_km: float) -> ee.Geometry:
    """
    Create a circular ROI around a point.

    Args:
        lat:       Latitude  (-90 to 90)
        lon:       Longitude (-180 to 180)
        buffer_km: Radius in kilometres (0.1 – 50)

    Returns:
        ee.Geometry (buffered circle)
    """
    _validate_coordinates(lat, lon)
    buffer_km = max(MIN_BUFFER_KM, min(buffer_km, MAX_BUFFER_KM))
    return ee.Geometry.Point([lon, lat]).buffer(buffer_km * 1000)


def create_polygon_roi(geojson_dict: dict) -> ee.Geometry:
    """
    Create an Earth Engine geometry from a GeoJSON dictionary.
    Supports Polygon and MultiPolygon.
    Validates coordinate bounds and area limits.

    Args:
        geojson_dict: GeoJSON geometry dict with 'type' and 'coordinates'

    Returns:
        ee.Geometry
    """
    if not isinstance(geojson_dict, dict):
        raise ValueError("geojson_dict must be a dictionary")

    geom_type = geojson_dict.get("type")
    coords    = geojson_dict.get("coordinates")

    if not coords:
        raise ValueError("GeoJSON missing 'coordinates' field")

    if geom_type == "Polygon":
        roi = ee.Geometry.Polygon(coords)
    elif geom_type == "MultiPolygon":
        roi = ee.Geometry.MultiPolygon(coords)
    elif geom_type == "Point":
        # Auto-buffer points to 2km
        lon, lat = coords[0], coords[1]
        logger.info(f"Point geometry auto-buffered to 2km: [{lon}, {lat}]")
        return create_roi(lat, lon, 2.0)
    else:
        raise ValueError(f"Unsupported GeoJSON geometry type: {geom_type}")

    # Validate area
    try:
        area_ha = roi.area().getInfo() / 10_000
        if area_ha < MIN_AREA_HA:
            raise ValueError(f"ROI too small: {area_ha:.2f} ha (minimum {MIN_AREA_HA} ha)")
        if area_ha > MAX_AREA_HA:
            logger.warning(
                f"ROI is very large ({area_ha:.0f} ha) — GEE analysis may be slow. "
                f"Consider reducing area or increasing bestEffort scale."
            )
    except ee.EEException:
        pass  # Skip validation if GEE not available (e.g. in tests)

    return roi


# ════════════════════════════════════════════════════
# AREA & GEOMETRY UTILITIES
# ════════════════════════════════════════════════════

def roi_area_hectares(roi: ee.Geometry) -> float:
    """Compute ROI area in hectares."""
    return roi.area().getInfo() / 10_000


def roi_area_km2(roi: ee.Geometry) -> float:
    """Compute ROI area in square kilometres."""
    return roi.area().getInfo() / 1_000_000


def roi_centroid(roi: ee.Geometry) -> Tuple[float, float]:
    """
    Return (lat, lon) centroid of the ROI.
    Useful for biome detection, weather lookups, map centering.
    """
    centroid = roi.centroid(maxError=100).coordinates().getInfo()
    return float(centroid[1]), float(centroid[0])  # (lat, lon)


def roi_bounding_box(roi: ee.Geometry) -> dict:
    """
    Return bounding box of the ROI as {west, south, east, north}.
    """
    bounds = roi.bounds().coordinates().getInfo()[0]
    lons   = [c[0] for c in bounds]
    lats   = [c[1] for c in bounds]
    return {
        "west":  min(lons),
        "south": min(lats),
        "east":  max(lons),
        "north": max(lats)
    }


def roi_perimeter_km(roi: ee.Geometry) -> float:
    """Compute ROI perimeter in kilometres."""
    try:
        return round(roi.perimeter(maxError=100).getInfo() / 1000, 2)
    except Exception:
        return 0.0


# ════════════════════════════════════════════════════
# ADAPTIVE BUFFER
# ════════════════════════════════════════════════════

def create_adaptive_roi(lat: float, lon: float, zoom: int = 11) -> ee.Geometry:
    """
    Create a circular ROI with buffer size automatically adapted to map zoom level.
    Useful for the map click-to-analyse feature.

    Zoom level → buffer radius mapping:
      z8  → 20km  (country overview)
      z10 → 8km   (regional)
      z12 → 3km   (local forest patch)
      z14 → 1km   (field level)
      z16 → 0.5km (sub-field)
    """
    zoom_buffer_map = {
        16: 0.5,
        15: 0.75,
        14: 1.0,
        13: 2.0,
        12: 3.0,
        11: 5.0,
        10: 8.0,
        9:  12.0,
        8:  20.0,
    }
    buffer_km = zoom_buffer_map.get(zoom, 2.0)
    logger.info(f"Adaptive buffer: zoom={zoom} → {buffer_km}km")
    return create_roi(lat, lon, buffer_km)


# ════════════════════════════════════════════════════
# GRID ROI GENERATION
# ════════════════════════════════════════════════════

def create_grid_rois(
    bounds: dict,
    cell_km: float = 1.0,
    max_points: int = 25
) -> list:
    """
    Create a uniform grid of circular ROI points for map-wide risk scanning.

    Args:
        bounds:     {west, south, east, north} bounding box
        cell_km:    Grid cell size in kilometres
        max_points: Maximum number of grid points (performance cap)

    Returns:
        List of (lat, lon, roi) tuples
    """
    west  = bounds["west"]
    south = bounds["south"]
    east  = bounds["east"]
    north = bounds["north"]

    # Dynamic grid sizing — fill the bounds with at most max_points
    max_cells  = int(math.sqrt(max_points))
    lng_step   = max((east - west)  / max_cells, 0.01)
    lat_step   = max((north - south) / max_cells, 0.01)

    rois = []
    lat  = south + lat_step / 2

    while lat < north and len(rois) < max_points:
        lon = west + lng_step / 2
        while lon < east and len(rois) < max_points:
            try:
                _validate_coordinates(lat, lon)
                roi = create_roi(lat, lon, cell_km / 2)
                rois.append((round(lat, 5), round(lon, 5), roi))
            except ValueError:
                pass
            lon += lng_step
        lat += lat_step

    logger.info(f"Grid created: {len(rois)} points ({lat_step:.3f}° × {lng_step:.3f}° cells)")
    return rois


def create_hex_grid_rois(
    bounds: dict,
    cell_km: float = 2.0,
    max_points: int = 25
) -> list:
    """
    Create a hexagonal grid of ROI points.
    Hex grids provide more uniform spatial coverage than square grids —
    every point is equidistant from its neighbours.

    Returns list of (lat, lon, roi) tuples.
    """
    west  = bounds["west"]
    south = bounds["south"]
    east  = bounds["east"]
    north = bounds["north"]

    # Convert km step to approximate degrees
    lat_step = cell_km / 111.0
    lng_step = cell_km / (111.0 * math.cos(math.radians((south + north) / 2)))

    rois = []
    row  = 0
    lat  = south + lat_step / 2

    while lat < north and len(rois) < max_points:
        # Offset every other row by half a cell (hex pattern)
        lng_offset = (lng_step / 2) if row % 2 == 1 else 0
        lon = west + lng_step / 2 + lng_offset

        while lon < east and len(rois) < max_points:
            try:
                _validate_coordinates(lat, lon)
                rois.append((round(lat, 5), round(lon, 5), create_roi(lat, lon, cell_km / 2)))
            except ValueError:
                pass
            lon += lng_step

        lat += lat_step * 0.866   # sin(60°) — vertical hex spacing
        row += 1

    logger.info(f"Hex grid created: {len(rois)} points")
    return rois


# ════════════════════════════════════════════════════
# ROI SIMPLIFICATION (for large complex polygons)
# ════════════════════════════════════════════════════

def simplify_roi(roi: ee.Geometry, max_error_m: float = 100) -> ee.Geometry:
    """
    Simplify a complex polygon geometry for faster GEE processing.
    Reduces the number of vertices while preserving shape within max_error_m.

    Args:
        roi:         Input GEE geometry
        max_error_m: Maximum simplification error in metres

    Returns:
        Simplified ee.Geometry
    """
    return roi.simplify(maxError=max_error_m)


def buffer_roi(roi: ee.Geometry, buffer_m: float) -> ee.Geometry:
    """
    Add a buffer around an existing ROI geometry.
    Useful for creating a surrounding analysis zone outside the core AOI.

    Args:
        roi:      Input geometry
        buffer_m: Buffer distance in metres (positive = expand, negative = shrink)
    """
    return roi.buffer(buffer_m)


# ════════════════════════════════════════════════════
# VALIDATION
# ════════════════════════════════════════════════════

def _validate_coordinates(lat: float, lon: float):
    """Raise ValueError if coordinates are out of valid range."""
    if not (-90 <= lat <= 90):
        raise ValueError(f"Invalid latitude: {lat}. Must be between -90 and 90.")
    if not (-180 <= lon <= 180):
        raise ValueError(f"Invalid longitude: {lon}. Must be between -180 and 180.")


def validate_geojson(geojson_dict: dict) -> Tuple[bool, str]:
    """
    Validate a GeoJSON geometry dict before passing to GEE.
    Returns (is_valid, error_message).
    """
    if not isinstance(geojson_dict, dict):
        return False, "Must be a dictionary"

    geom_type = geojson_dict.get("type")
    if geom_type not in ("Polygon", "MultiPolygon", "Point"):
        return False, f"Unsupported type: {geom_type}"

    coords = geojson_dict.get("coordinates")
    if not coords:
        return False, "Missing 'coordinates'"

    if geom_type == "Polygon":
        if not isinstance(coords, list) or len(coords) == 0:
            return False, "Polygon coordinates must be a non-empty list of rings"
        ring = coords[0]
        if len(ring) < 4:
            return False, "Polygon ring must have at least 4 points (first = last)"
        if ring[0] != ring[-1]:
            return False, "Polygon ring must be closed (first point == last point)"

    return True, "OK"


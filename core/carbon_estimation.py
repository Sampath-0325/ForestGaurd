"""
ForestGuard — Advanced Carbon Loss Estimation

Methods:
  1. IPCC Tier 1 biomass density lookup by biome type
  2. Above-ground biomass (AGB) from NDVI proxy
  3. Below-ground biomass (BGB) via root-to-shoot ratio (IPCC default: 0.26)
  4. Dead organic matter & soil carbon estimates
  5. CO₂ equivalent conversion (× 3.67)

References:
  - IPCC (2006) Guidelines for National GHG Inventories, Vol. 4 Chapter 4
  - Saatchi et al. (2011) Benchmark map of forest carbon stocks in tropical regions
  - Spawn et al. (2020) Harmonized global maps of AGB and BGB carbon
"""
import numpy as np
import logging
from typing import Optional

logger = logging.getLogger("forestguard.carbon")


# ══════════════════════════════════════════════════
# IPCC TIER 1 BIOMASS DENSITY TABLE (tC/ha)
# Source: IPCC 2006 Guidelines, Table 4.7
# ══════════════════════════════════════════════════

BIOME_BIOMASS_DENSITY = {
    # Biome: (AGB_mean_tC_ha, BGB_ratio)
    "tropical_moist":         (120.0, 0.37),
    "tropical_dry":           (55.0,  0.28),
    "tropical_montane":       (80.0,  0.30),
    "subtropical_moist":      (70.0,  0.28),
    "subtropical_dry":        (40.0,  0.28),
    "temperate_oceanic":      (90.0,  0.26),
    "temperate_continental":  (50.0,  0.26),
    "boreal":                 (30.0,  0.24),
    "mangrove":               (85.0,  0.49),
    "savanna":                (20.0,  0.40),
    "default":                (65.0,  0.26),   # IPCC global default
}

# Fraction of biomass that is carbon
CARBON_FRACTION = 0.47  # IPCC default (Penman et al. 2003)

# CO₂ equivalent multiplier (44/12)
CO2_MULTIPLIER = 3.6667


def estimate_carbon_loss(
    ndvi_series: dict,
    area_hectares: float = 100.0,
    biome: str = "default",
    include_soil: bool = True
) -> dict:
    """
    Estimate carbon loss using IPCC Tier 1 methodology.

    Strategy:
      1. Map NDVI decline to fractional forest cover loss
      2. Apply biome-specific AGB density
      3. Add BGB (root-to-shoot ratio)
      4. Optionally include soil organic carbon (30% of AGB as proxy)
      5. Convert biomass loss → carbon → CO₂e

    Args:
        ndvi_series:   {year: ndvi_value}
        area_hectares: AOI area in hectares
        biome:         key from BIOME_BIOMASS_DENSITY (auto-detect if "default")
        include_soil:  add soil organic carbon pool estimate

    Returns full carbon accounting dict.
    """
    if not ndvi_series or len(ndvi_series) < 2:
        return _empty_carbon("Insufficient NDVI data")

    years  = sorted(ndvi_series.keys())
    values = [ndvi_series[y] for y in years]

    ndvi_start = values[0]
    ndvi_end   = values[-1]
    ndvi_change = ndvi_start - ndvi_end  # positive = loss

    if ndvi_change <= 0:
        return _empty_carbon("No significant carbon loss — NDVI stable or improving")

    # ── Biome lookup ──
    biome_key = biome if biome in BIOME_BIOMASS_DENSITY else "default"
    agb_density, bgb_ratio = BIOME_BIOMASS_DENSITY[biome_key]

    # ── Fractional forest cover loss from NDVI proxy ──
    # NDVI 0.8 = dense forest, 0.3 = sparse/degraded
    # Linear interpolation: decline from 0.8→0.3 = 100% loss
    ndvi_forest_max = 0.80
    ndvi_bare_min   = 0.30
    clamp_change    = max(0, min(ndvi_change, ndvi_forest_max - ndvi_bare_min))
    forest_loss_frac = clamp_change / (ndvi_forest_max - ndvi_bare_min)

    affected_ha = area_hectares * forest_loss_frac

    # ── Carbon pools ──
    # Above-ground biomass carbon
    agb_carbon = affected_ha * agb_density * CARBON_FRACTION

    # Below-ground biomass carbon (root-to-shoot)
    bgb_carbon = agb_carbon * bgb_ratio

    # Dead organic matter (10% of AGB carbon — IPCC approximation)
    dom_carbon = agb_carbon * 0.10

    # Soil organic carbon (proxy: 30% of AGB for tropical, less for others)
    soil_carbon = (agb_carbon * 0.30) if include_soil else 0.0

    # Total ecosystem carbon loss
    total_carbon = agb_carbon + bgb_carbon + dom_carbon + soil_carbon
    co2_equivalent = total_carbon * CO2_MULTIPLIER

    # ── Deforestation rate ──
    n_years = max(years[-1] - years[0], 1)
    annual_loss_ha  = affected_ha / n_years
    annual_carbon   = total_carbon / n_years
    annual_co2      = co2_equivalent / n_years

    return {
        "carbon_loss_tons":      round(total_carbon, 2),
        "co2_equivalent_tons":   round(co2_equivalent, 2),
        "agb_carbon_tons":       round(agb_carbon, 2),
        "bgb_carbon_tons":       round(bgb_carbon, 2),
        "soil_carbon_tons":      round(soil_carbon, 2),
        "dom_carbon_tons":       round(dom_carbon, 2),
        "affected_area_ha":      round(affected_ha, 2),
        "forest_loss_fraction":  round(forest_loss_frac, 3),
        "annual_carbon_loss":    round(annual_carbon, 2),
        "annual_co2_loss":       round(annual_co2, 2),
        "annual_area_loss_ha":   round(annual_loss_ha, 2),
        "ndvi_decline":          round(ndvi_change, 4),
        "biome":                 biome_key,
        "years_covered":         n_years,
        "methodology":           "IPCC Tier 1 (2006 Guidelines, Ch. 4)",
        "carbon_fraction":       CARBON_FRACTION,
        "status":                "Estimated carbon loss detected"
    }


def auto_detect_biome(lat: float, lon: float) -> str:
    """
    Simple lat/lon based biome classification.
    A real implementation would use WWF Biome shapefile lookup.
    """
    abs_lat = abs(lat)

    if abs_lat < 10:
        return "tropical_moist"
    elif abs_lat < 23.5:
        # Tropics — check for dry regions (rough heuristic by longitude)
        if 60 < lon < 80 or (lon < 0 and abs_lat > 5):
            return "tropical_dry"
        return "tropical_moist"
    elif abs_lat < 35:
        return "subtropical_moist"
    elif abs_lat < 50:
        return "temperate_oceanic"
    elif abs_lat < 65:
        return "boreal"
    else:
        return "default"


def _empty_carbon(status: str) -> dict:
    return {
        "carbon_loss_tons": 0.0, "co2_equivalent_tons": 0.0,
        "agb_carbon_tons": 0.0, "bgb_carbon_tons": 0.0,
        "soil_carbon_tons": 0.0, "dom_carbon_tons": 0.0,
        "affected_area_ha": 0.0, "forest_loss_fraction": 0.0,
        "annual_carbon_loss": 0.0, "annual_co2_loss": 0.0,
        "annual_area_loss_ha": 0.0, "ndvi_decline": 0.0,
        "biome": "default", "years_covered": 0,
        "methodology": "IPCC Tier 1 (2006 Guidelines, Ch. 4)",
        "carbon_fraction": CARBON_FRACTION,
        "status": status
    }


from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, JSONResponse
from contextlib import asynccontextmanager
from pathlib import Path
import logging
import time

from core.auth import initialize_gee
from core.roi import create_roi, create_grid_rois
from core.pipeline import run_analysis
from core.ndvi import yearly_ndvi_timeseries
from core.risk_analysis import compute_risk_score
from models.response import AnalysisResponse

# ─────────────────────────────────────────────
# CONFIG
# ─────────────────────────────────────────────

PROJECT_ROOT = Path(__file__).resolve().parent.parent
FRONTEND_PATH = PROJECT_ROOT / "frontend" / "dist"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)
logger = logging.getLogger("forestguard")

# ─────────────────────────────────────────────
# LIFECYCLE
# ─────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("  ForestGuard starting...")
    try:
        initialize_gee()
        logger.info("  GEE Initialized successfully")
    except Exception as e:
        logger.error(f"  GEE init failed: {e}")
    yield
    logger.info("  ForestGuard shutdown")

app = FastAPI(
    title="ForestGuard API",
    description="Advanced deforestation monitoring & early warning system",
    version="4.0.0",
    lifespan=lifespan
)

# ─────────────────────────────────────────────
# MIDDLEWARE
# ─────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────
# ENTERPRISE API ROUTES
# ─────────────────────────────────────────────

from backend.api import router as api_router
app.include_router(api_router, prefix="/api", tags=["Enterprise"])

# ─────────────────────────────────────────────
# HEALTH
# ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "forestguard",
        "timestamp": int(time.time())
    }

# ─────────────────────────────────────────────
# CORE ANALYSIS ENDPOINT
# ─────────────────────────────────────────────

@app.get("/analysis", response_model=AnalysisResponse)
def analyze(
    lat: float = Query(..., ge=-90, le=90),
    lon: float = Query(..., ge=-180, le=180),
    buffer_km: float = Query(2, ge=0.5, le=20),
    start_year: int = Query(2018, ge=2000),
    end_year: int = Query(2024),
    # ✅ FIXED: regex → pattern (removes deprecation warning)
    source: str = Query("landsat", pattern="^(landsat|sentinel2)$"),
):
    try:
        start = time.time()
        roi = create_roi(lat, lon, buffer_km)
        result = run_analysis(
            roi,
            start_year,
            end_year,
            lat=lat,
            lon=lon,
            source=source
        )
        logger.info(
            f"  Analysis | {lat},{lon} | {source} | "
            f"{end_year - start_year + 1} yrs | {round(time.time() - start, 2)}s"
        )
        return result
    except Exception as e:
        logger.exception("  Analysis failed")
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────────
# GRID / MAP RISK SCAN
# ─────────────────────────────────────────────

@app.get("/map/risk-grid")
def risk_grid(
    west: float,
    south: float,
    east: float,
    north: float,
    cell_km: float = 2,
    start_year: int = 2019,
    end_year: int = 2023,
    max_points: int = 15
):
    bounds = dict(west=west, south=south, east=east, north=north)
    rois = create_grid_rois(bounds, cell_km)[:max_points]
    features = []

    for lat, lon, roi in rois:
        try:
            ndvi = yearly_ndvi_timeseries(roi, start_year, end_year)
            risk = compute_risk_score(ndvi)
            color = {
                "HIGH": "#dc2626",
                "MEDIUM": "#eab308",
                "LOW": "#22c55e"
            }.get(risk["risk_level"], "#9ca3af")

            features.append({
                "type": "Feature",
                "geometry": {"type": "Point", "coordinates": [lon, lat]},
                "properties": {
                    "risk_level": risk["risk_level"],
                    "slope": risk["slope"],
                    "variability": risk["variability"],
                    "color": color
                }
            })
        except Exception as e:
            logger.warning(f"Grid point failed: {e}")

    return {"type": "FeatureCollection", "features": features}

# ─────────────────────────────────────────────
# FRONTEND SERVING
# ✅ FIXED: safe check before mounting static files
# ─────────────────────────────────────────────

@app.get("/")
def frontend():
    index = FRONTEND_PATH / "index.html"
    if index.exists():
        return FileResponse(index)
    return JSONResponse({"message": "ForestGuard API running. Frontend not built yet."})

assets_path = FRONTEND_PATH / "assets"
if assets_path.exists():
    app.mount("/assets", StaticFiles(directory=assets_path), name="assets")
else:
    logger.warning(f"Frontend assets not found at {assets_path} — static serving disabled")

from pydantic import BaseModel
from typing import Dict, Any, List, Optional


class AnalysisResponse(BaseModel):
    ndvi_timeseries: Dict[str, float]
    alerts: List[Dict[str, Any]]
    risk: Dict[str, Any]
    vegetation_stability: Dict[str, Any]
    hotspots: Dict[str, Any]
    carbon_impact: Dict[str, Any]
    center: Optional[Dict[str, float]]

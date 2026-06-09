"""
ForestGuard Enterprise API
All routes: Auth, AOIs, Alerts, Dashboard, Reports, Admin
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime
from typing import List, Dict, Any, Optional
import json
import logging
import re

from backend.database import get_db
from backend.models import User, Organization, AOI, Alert, InviteCode, OfficerContact
from backend.deps import get_current_user, get_user_from_query_token, get_optional_user, require_admin, require_analyst_or_above
from backend.auth_utils import verify_password, create_access_token, get_password_hash
from backend.tasks import scan_aoi_background
from backend.config import settings
from pydantic import BaseModel
import uuid

logger = logging.getLogger("forestguard")
router = APIRouter()


# ═══════════════════════════════════════════════════════════════
# SCHEMAS
# ═══════════════════════════════════════════════════════════════

class Token(BaseModel):
    access_token: str
    token_type: str


class AOICreate(BaseModel):
    name: str
    geojson_polygon: Dict[str, Any]


class AOIResponse(BaseModel):
    id: int
    name: str
    is_active: bool
    geojson_polygon: str
    created_at: datetime
    last_scanned: Optional[datetime] = None
    last_risk_level: Optional[str] = None
    last_carbon_loss: Optional[float] = None

    class Config:
        from_attributes = True


class AlertResponse(BaseModel):
    id: int
    aoi_id: int
    risk_level: str
    details: str
    resolved: bool
    resolved_at: Optional[datetime] = None
    created_at: datetime
    confidence_score: Optional[float] = None
    carbon_loss_tons: Optional[float] = None

    class Config:
        from_attributes = True


class DashboardStats(BaseModel):
    total_aois: int
    active_aois: int
    total_alerts: int
    unresolved_alerts: int
    high_risk_count: int
    medium_risk_count: int
    low_risk_count: int
    last_scan_time: Optional[datetime] = None


class UserCreate(BaseModel):
    email: str
    password: str
    full_name: str
    role: str = "viewer"


class UserResponse(BaseModel):
    id: int
    email: str
    full_name: str
    role: str
    is_active: bool
    created_at: datetime

    class Config:
        from_attributes = True


class WebhookUpdate(BaseModel):
    webhook_url: str


class AlertResolve(BaseModel):
    resolved: bool


class RegisterOrgRequest(BaseModel):
    org_name:   str
    full_name:  str
    email:      str
    password:   str

class JoinOrgRequest(BaseModel):
    invite_code: str
    full_name:   str
    email:       str
    password:    str

class InviteCodeCreate(BaseModel):
    role:      str = "analyst"   # "analyst" | "viewer"
    max_uses:  int = 1           # -1 = unlimited
    expires_days: Optional[int] = None   # None = never

class InviteCodeResponse(BaseModel):
    code:      str
    role:      str
    max_uses:  int
    use_count: int
    is_active: bool
    created_at: datetime
    expires_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class ChatRequest(BaseModel):
    message:     str
    session_id:  Optional[str] = "default"
    aoi_context: Optional[Dict[str, Any]] = None

class ChatResponse(BaseModel):
    answer:     str
    session_id: str

class VoiceCommandRequest(BaseModel):
    transcript:      str
    session_id:      Optional[str] = "voice-default"
    current_aoi_id:  Optional[int] = None

class VoiceCommandResponse(BaseModel):
    speech_text:    str
    action:         Optional[str] = None
    action_payload: Optional[Dict[str, Any]] = None

class OfficerContactCreate(BaseModel):
    name:        str
    email:       Optional[str] = None
    phone:       Optional[str] = None
    alert_types: list[str] = ["HIGH", "MEDIUM"]


# ═══════════════════════════════════════════════════════════════
# AUTH ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.post("/auth/token", response_model=Token)
def login_for_access_token(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    user = db.query(User).filter(User.email == form_data.username).first()
    if not user or not verify_password(form_data.password, user.hashed_password):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(subject=user.email)
    return {"access_token": access_token, "token_type": "bearer"}


@router.get("/auth/me", response_model=UserResponse)
def get_me(current_user: User = Depends(get_current_user)):
    """Return current user profile."""
    return current_user


@router.post("/auth/seed")
def seed_test_user(db: Session = Depends(get_db)):
    """Seed ForestGuard with 5 Telangana forest AOIs + admin user."""
    if settings.environment == "production":
        raise HTTPException(status_code=403, detail="Not allowed in production")

    if db.query(Organization).first():
        return {"message": "Seed data already exists"}

    # Create org
    org = Organization(name="ForestGuard Telangana")
    db.add(org)
    db.flush()

    # Create admin user
    user = User(
        email="admin@forestguard.org",
        hashed_password=get_password_hash("forestguard2024"),
        full_name="ForestGuard Admin",
        role="admin",
        org_id=org.id
    )
    db.add(user)
    db.flush()

    # 5 Telangana forest AOIs with real approximate polygons
    forests = [
        {
            "name": "Nallamala Forest",
            "geojson": {"type":"Polygon","coordinates":[[[78.5,15.9],[79.0,15.8],[79.6,16.0],[79.8,16.5],[79.6,17.0],[79.2,17.2],[78.8,17.1],[78.5,16.8],[78.3,16.4],[78.4,16.0],[78.5,15.9]]]},
        },
        {
            "name": "Adilabad Forests",
            "geojson": {"type":"Polygon","coordinates":[[[78.0,19.1],[78.5,19.0],[79.2,19.1],[79.5,19.4],[79.4,19.8],[79.0,19.9],[78.5,19.8],[78.1,19.6],[77.9,19.3],[78.0,19.1]]]},
        },
        {
            "name": "Bhadradri Kothagudem Forests",
            "geojson": {"type":"Polygon","coordinates":[[[80.4,17.6],[80.8,17.5],[81.2,17.7],[81.3,18.1],[81.1,18.4],[80.7,18.5],[80.3,18.3],[80.2,17.9],[80.3,17.6],[80.4,17.6]]]},
        },
        {
            "name": "Mulugu Forests",
            "geojson": {"type":"Polygon","coordinates":[[[79.8,17.9],[80.2,17.8],[80.6,18.0],[80.7,18.4],[80.4,18.6],[80.0,18.5],[79.7,18.3],[79.6,18.0],[79.7,17.9],[79.8,17.9]]]},
        },
        {
            "name": "Vikarabad / Ananthagiri Forests",
            "geojson": {"type":"Polygon","coordinates":[[[77.6,17.1],[77.9,17.0],[78.1,17.2],[78.0,17.5],[77.8,17.6],[77.5,17.5],[77.4,17.3],[77.5,17.1],[77.6,17.1]]]},
        },
    ]

    for f in forests:
        aoi = AOI(
            name=f["name"],
            org_id=org.id,
            geojson_polygon=json.dumps(f["geojson"]),
            is_active=True
        )
        db.add(aoi)
        db.flush()
        # Queue a scan for each forest
        try:
            scan_aoi_background(aoi.id)
        except Exception as e:
            logger.warning(f"Could not queue scan for {f['name']}: {e}")

    db.commit()
    logger.info("Seeded 5 Telangana forest AOIs")
    return {
        "message": "ForestGuard seeded with 5 Telangana forests",
        "admin": "admin@forestguard.org",
        "password": "forestguard2024",
        "forests": [f["name"] for f in forests]
    }


# ── Registration: new organization ──
@router.post("/auth/register", status_code=201)
def register_new_org(req: RegisterOrgRequest, db: Session = Depends(get_db)):
    """
    Public registration — creates a brand new organization + admin account.
    Used by: new customers signing up for ForestGuard.
    """
    # Validate
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")
    if db.query(Organization).filter(Organization.name == req.org_name).first():
        raise HTTPException(status_code=400, detail="Organization name already taken")

    # Create org
    org = Organization(name=req.org_name)
    db.add(org)
    db.flush()

    # Create admin user
    user = User(
        email=req.email,
        hashed_password=get_password_hash(req.password),
        full_name=req.full_name,
        role="admin",
        org_id=org.id
    )
    db.add(user)
    db.commit()

    token = create_access_token(subject=user.email)
    logger.info(f"New org registered: {org.name} by {user.email}")
    return {
        "message": f"Organization '{org.name}' created successfully",
        "access_token": token,
        "token_type": "bearer",
        "role": "admin",
        "full_name": user.full_name,
        "org_name": org.name
    }


# ── Registration: join existing org via invite code ──
@router.post("/auth/join", status_code=201)
def join_org_with_invite(req: JoinOrgRequest, db: Session = Depends(get_db)):
    """
    Join an existing organization using an invite code.
    Used by: analysts/viewers invited by their org admin.
    """
    from datetime import timezone

    # Validate invite code
    invite = db.query(InviteCode).filter(
        InviteCode.code == req.invite_code.upper().strip(),
        InviteCode.is_active == True
    ).first()

    if not invite:
        raise HTTPException(status_code=400, detail="Invalid or expired invite code")
    if invite.expires_at and invite.expires_at < datetime.utcnow():
        raise HTTPException(status_code=400, detail="Invite code has expired")
    if invite.max_uses != -1 and invite.use_count >= invite.max_uses:
        raise HTTPException(status_code=400, detail="Invite code has reached its usage limit")

    # Validate user fields
    if len(req.password) < 8:
        raise HTTPException(status_code=400, detail="Password must be at least 8 characters")
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(status_code=400, detail="Email already registered")

    # Create user
    user = User(
        email=req.email,
        hashed_password=get_password_hash(req.password),
        full_name=req.full_name,
        role=invite.role,
        org_id=invite.org_id
    )
    db.add(user)

    # Increment invite use count
    invite.use_count += 1
    if invite.max_uses != -1 and invite.use_count >= invite.max_uses:
        invite.is_active = False

    db.commit()

    org = db.query(Organization).filter(Organization.id == invite.org_id).first()
    token = create_access_token(subject=user.email)
    logger.info(f"User joined org via invite: {user.email} → {org.name} as {invite.role}")
    return {
        "message": f"Joined '{org.name}' as {invite.role}",
        "access_token": token,
        "token_type": "bearer",
        "role": invite.role,
        "full_name": user.full_name,
        "org_name": org.name
    }


# ── Admin: generate invite code ──
@router.post("/auth/invite", response_model=InviteCodeResponse)
def generate_invite_code(
    req: InviteCodeCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Generate an invite code for the admin's organization."""
    import secrets
    from datetime import timedelta

    if req.role not in ("analyst", "viewer"):
        raise HTTPException(status_code=400, detail="Role must be 'analyst' or 'viewer'")

    # Generate readable code: ORG-XXXXX
    org = db.query(Organization).filter(Organization.id == current_user.org_id).first()
    prefix = (org.name[:3] if org else "FG").upper()
    code = f"{prefix}-{secrets.token_hex(3).upper()}"

    expires_at = None
    if req.expires_days:
        from datetime import timedelta
        expires_at = datetime.utcnow() + timedelta(days=req.expires_days)

    invite = InviteCode(
        code=code,
        org_id=current_user.org_id,
        role=req.role,
        created_by=current_user.id,
        max_uses=req.max_uses,
        expires_at=expires_at
    )
    db.add(invite)
    db.commit()
    db.refresh(invite)
    logger.info(f"Invite code generated: {code} for org {current_user.org_id} by {current_user.email}")
    return invite


# ── Admin: list invite codes ──
@router.get("/auth/invites", response_model=List[InviteCodeResponse])
def list_invite_codes(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """List all invite codes for the admin's organization."""
    return db.query(InviteCode).filter(
        InviteCode.org_id == current_user.org_id
    ).order_by(InviteCode.created_at.desc()).all()


# ── Admin: revoke invite code ──
@router.delete("/auth/invite/{code}")
def revoke_invite_code(
    code: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Revoke an invite code."""
    invite = db.query(InviteCode).filter(
        InviteCode.code == code.upper(),
        InviteCode.org_id == current_user.org_id
    ).first()
    if not invite:
        raise HTTPException(status_code=404, detail="Invite code not found")
    invite.is_active = False
    db.commit()
    return {"message": f"Invite code {code} revoked"}


# ═══════════════════════════════════════════════════════════════
# PUBLIC ENDPOINTS — No authentication required
# ═══════════════════════════════════════════════════════════════

# Forest metadata — known risks and descriptions for each AOI
FOREST_META = {
    "Nallamala Forest": {
        "district": "Nagarkurnool / Nalgonda",
        "area_km2": 3700,
        "description": "Largest forest in Telangana, part of Nagarjunasagar-Srisailam Tiger Reserve. Critical tiger and leopard habitat.",
        "risks": ["Illegal logging", "Forest fires", "Road & infrastructure development", "Mining proposals", "Human encroachment"],
        "color": "#10b981"
    },
    "Adilabad Forests": {
        "district": "Adilabad District",
        "area_km2": 2000,
        "description": "Teak-rich forest in northern Telangana. Home to tribal communities. Under pressure from agricultural expansion.",
        "risks": ["Illegal teak wood cutting", "Agricultural expansion", "Tribal settlement expansion", "Forest fires"],
        "color": "#10b981"
    },
    "Bhadradri Kothagudem Forests": {
        "district": "Godavari Basin",
        "area_km2": 1800,
        "description": "Dense forests along the Godavari river. Facing severe pressure from coal mining and industrial expansion.",
        "risks": ["Coal mining expansion", "Industrial development", "Illegal wood cutting", "Infrastructure projects"],
        "color": "#10b981"
    },
    "Mulugu Forests": {
        "district": "Godavari Forest Region",
        "area_km2": 1500,
        "description": "Dense tribal forest along the Godavari. Bhupalpally region. Affected by shifting cultivation.",
        "risks": ["Forest fires", "Illegal logging", "Shifting cultivation", "Agricultural encroachment"],
        "color": "#10b981"
    },
    "Vikarabad / Ananthagiri Forests": {
        "district": "Vikarabad District",
        "area_km2": 500,
        "description": "Closest green lung to Hyderabad. Ananthagiri Hills biodiversity hotspot under severe urban and tourism pressure.",
        "risks": ["Tourism expansion", "Real estate development", "Road construction", "Tree cutting for resorts"],
        "color": "#10b981"
    },
}


@router.get("/public/forests")
def get_public_forests(db: Session = Depends(get_db)):
    """
    Public endpoint — returns all 5 Telangana forest AOIs with
    their latest risk data, alerts, and known threats.
    No authentication required.
    """
    # Get the org (ForestGuard Telangana)
    org = db.query(Organization).filter(
        Organization.name == "ForestGuard Telangana"
    ).first()

    if not org:
        return {"forests": [], "seeded": False,
                "message": "Run POST /api/auth/seed to initialise the forests"}

    aois = db.query(AOI).filter(
        AOI.org_id == org.id,
        AOI.is_active == True
    ).order_by(AOI.id).all()

    result = []
    for aoi in aois:
        meta = FOREST_META.get(aoi.name, {})

        # Get unresolved alert count
        alert_count = db.query(Alert).filter(
            Alert.aoi_id == aoi.id,
            Alert.resolved == False
        ).count()

        # Latest alert
        latest_alert = db.query(Alert).filter(
            Alert.aoi_id == aoi.id
        ).order_by(Alert.created_at.desc()).first()

        risk_level = aoi.last_risk_level or "PENDING"
        risk_color = {
            "HIGH":    "#ef4444",
            "MEDIUM":  "#f59e0b",
            "LOW":     "#10b981",
            "PENDING": "#64748b"
        }.get(risk_level, "#64748b")

        result.append({
            "id":              aoi.id,
            "name":            aoi.name,
            "district":        meta.get("district", ""),
            "description":     meta.get("description", ""),
            "area_km2":        meta.get("area_km2", 0),
            "known_risks":     meta.get("risks", []),
            "geojson_polygon": json.loads(aoi.geojson_polygon),
            "risk_level":      risk_level,
            "risk_color":      risk_color,
            "carbon_loss_tons": aoi.last_carbon_loss,
            "last_scanned":    aoi.last_scanned.isoformat() if aoi.last_scanned else None,
            "unresolved_alerts": alert_count,
            "latest_alert": {
                "risk_level":       latest_alert.risk_level,
                "confidence_score": latest_alert.confidence_score,
                "carbon_loss_tons": latest_alert.carbon_loss_tons,
                "created_at":       latest_alert.created_at.isoformat(),
            } if latest_alert else None
        })

    return {
        "forests":      result,
        "total":        len(result),
        "high_risk":    sum(1 for f in result if f["risk_level"] == "HIGH"),
        "medium_risk":  sum(1 for f in result if f["risk_level"] == "MEDIUM"),
        "low_risk":     sum(1 for f in result if f["risk_level"] == "LOW"),
        "last_updated": datetime.utcnow().isoformat()
    }


@router.get("/public/forests/{forest_id}/ndvi")
def get_public_forest_ndvi(
    forest_id: int,
    start_year: int = 2018,
    end_year:   int = 2026,
    db: Session = Depends(get_db)
):
    """
    Public endpoint — NDVI timeseries for a forest.
    Used by the public comparison chart. No auth required.
    """
    aoi = db.query(AOI).filter(AOI.id == forest_id).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="Forest not found")

    try:
        from core.roi import create_polygon_roi
        from core.ndvi import yearly_ndvi_timeseries, SOURCE_LANDSAT
        from core.risk_analysis import compute_risk_score

        geojson     = json.loads(aoi.geojson_polygon)
        roi         = create_polygon_roi(geojson)
        ndvi_series = yearly_ndvi_timeseries(roi, start_year, end_year, source=SOURCE_LANDSAT)
        risk        = compute_risk_score(ndvi_series)

        return {
            "aoi_id":          forest_id,
            "aoi_name":        aoi.name,
            "ndvi_timeseries": ndvi_series,
            "risk_level":      aoi.last_risk_level or risk.get("risk_level", "PENDING"),
            "risk_score":      risk.get("risk_score", 0),
        }
    except Exception as e:
        logger.error(f"Public NDVI fetch failed for forest {forest_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Analysis failed: {str(e)}")


@router.get("/public/forests/{forest_id}")
def get_public_forest_detail(forest_id: int, db: Session = Depends(get_db)):
    """
    Public endpoint — returns detailed data for a single forest AOI.
    No authentication required.
    """
    aoi = db.query(AOI).filter(AOI.id == forest_id).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="Forest not found")

    alerts = db.query(Alert).filter(
        Alert.aoi_id == forest_id
    ).order_by(Alert.created_at.desc()).limit(10).all()

    meta = FOREST_META.get(aoi.name, {})

    return {
        "id":              aoi.id,
        "name":            aoi.name,
        "district":        meta.get("district", ""),
        "description":     meta.get("description", ""),
        "area_km2":        meta.get("area_km2", 0),
        "known_risks":     meta.get("risks", []),
        "geojson_polygon": json.loads(aoi.geojson_polygon),
        "risk_level":      aoi.last_risk_level or "PENDING",
        "carbon_loss_tons": aoi.last_carbon_loss,
        "last_scanned":    aoi.last_scanned.isoformat() if aoi.last_scanned else None,
        "alerts": [{
            "id":               a.id,
            "risk_level":       a.risk_level,
            "confidence_score": a.confidence_score,
            "carbon_loss_tons": a.carbon_loss_tons,
            "created_at":       a.created_at.isoformat(),
            "resolved":         a.resolved,
        } for a in alerts]
    }


@router.get("/public/report/{forest_id}", response_class=HTMLResponse)
def get_public_forest_report(forest_id: int, db: Session = Depends(get_db)):
    """
    Public HTML report — no auth required, no auto-print, no hardcoded threats.
    Only shows satellite-derived data from DB.
    """
    aoi = db.query(AOI).filter(AOI.id == forest_id).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="Forest not found")

    alerts = db.query(Alert).filter(
        Alert.aoi_id == forest_id
    ).order_by(Alert.created_at.desc()).all()

    RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
    risk_level = aoi.last_risk_level or "PENDING"
    risk_color = RISK_COLORS.get(risk_level, "#94a3b8")

    # Alert rows
    ALERT_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
    alerts_rows = ""
    for a in alerts:
        ac       = ALERT_COLORS.get(a.risk_level, "#94a3b8")
        conf_str = f"{a.confidence_score:.1f}%" if a.confidence_score is not None else "N/A"
        carb_str = f"{a.carbon_loss_tons:.2f} t" if a.carbon_loss_tons  is not None else "N/A"
        res_str  = "Resolved" if a.resolved else "Active"
        res_col  = "#10b981" if a.resolved else "#ef4444"
        alerts_rows += f"""
        <tr>
          <td>{a.created_at.strftime("%Y-%m-%d %H:%M")}</td>
          <td><span style="color:{ac};font-weight:700;">{a.risk_level}</span></td>
          <td>{conf_str}</td><td>{carb_str}</td>
          <td><span style="color:{res_col};">{res_str}</span></td>
        </tr>"""

    alerts_section = f"""
    <table>
      <thead><tr><th>Date (UTC)</th><th>Risk</th><th>Confidence</th><th>Carbon Loss</th><th>Status</th></tr></thead>
      <tbody>{alerts_rows}</tbody>
    </table>""" if alerts else     '<p style="color:#94a3b8;padding:1rem 0">No alerts recorded — area appears stable based on satellite analysis.</p>'

    # Carbon display
    carbon_val = aoi.last_carbon_loss
    if carbon_val and carbon_val > 0.01:
        carbon_display = f"{carbon_val:.2f} t CO₂"
        carbon_color   = "#ef4444"
    else:
        carbon_display = "Stable — No significant loss detected"
        carbon_color   = "#10b981"

    scanned_str = aoi.last_scanned.strftime("%Y-%m-%d %H:%M UTC") if aoi.last_scanned else "Not yet scanned"

    # Monitoring status message — from actual scan data only
    if aoi.last_scanned:
        h = sum(1 for a in alerts if a.risk_level == "HIGH"   and not a.resolved)
        m = sum(1 for a in alerts if a.risk_level == "MEDIUM" and not a.resolved)
        if h > 0 or m > 0:
            scan_msg = f"⚠ {h} high-risk and {m} medium-risk alerts are currently active."
            scan_bg  = "#fffbeb"; scan_border = "#fde68a"; scan_text = "#92400e"
        else:
            scan_msg = f"✓ Last satellite scan on {scanned_str} detected no significant deforestation activity."
            scan_bg  = "#f0fdf4"; scan_border = "#bbf7d0"; scan_text = "#166534"
    else:
        scan_msg = "Satellite scan has not been run yet. Start the Huey worker to begin analysis."
        scan_bg  = "#f8fafc"; scan_border = "#e2e8f0"; scan_text = "#64748b"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>ForestGuard Report — {aoi.name}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  * {{ box-sizing:border-box; margin:0; padding:0; }}
  body {{ font-family:'Inter',sans-serif; background:#fff; color:#1e293b; padding:2.5rem; max-width:900px; margin:0 auto; padding-top:4.5rem; }}
  .topbar {{ position:fixed; top:0; left:0; right:0; background:#fff; border-bottom:1px solid #e2e8f0; padding:0.7rem 2.5rem; display:flex; justify-content:flex-end; gap:0.5rem; z-index:99; }}
  .btn-print {{ background:#10b981; color:#fff; border:none; padding:0.45rem 1.1rem; border-radius:6px; font-size:0.82rem; font-weight:600; cursor:pointer; }}
  .btn-close {{ background:#f1f5f9; color:#64748b; border:1px solid #e2e8f0; padding:0.45rem 0.9rem; border-radius:6px; font-size:0.82rem; cursor:pointer; }}
  .header {{ display:flex; justify-content:space-between; align-items:flex-start; border-bottom:3px solid #10b981; padding-bottom:1.25rem; margin-bottom:2rem; }}
  .logo {{ font-size:1.3rem; font-weight:700; color:#10b981; }}
  .report-meta {{ text-align:right; }}
  .report-meta h1 {{ font-size:1rem; font-weight:600; }}
  .report-meta .sub {{ font-size:0.75rem; color:#94a3b8; }}
  .section {{ margin-bottom:2.5rem; }}
  .section-title {{ font-size:0.7rem; font-weight:700; text-transform:uppercase; letter-spacing:0.1em; color:#94a3b8; margin-bottom:0.85rem; padding-bottom:0.4rem; border-bottom:1px solid #e2e8f0; }}
  .grid3 {{ display:grid; grid-template-columns:repeat(3,1fr); gap:1rem; }}
  .card {{ background:#f8fafc; border-radius:8px; padding:1rem 1.1rem; border-left:4px solid #e2e8f0; }}
  .card.accent {{ border-left-color:{risk_color}; }}
  .card.green  {{ border-left-color:#10b981; }}
  .card-label  {{ font-size:0.7rem; color:#94a3b8; font-weight:500; margin-bottom:0.3rem; text-transform:uppercase; letter-spacing:0.05em; }}
  .card-value  {{ font-size:1.15rem; font-weight:700; }}
  table {{ width:100%; border-collapse:collapse; font-size:0.85rem; }}
  th {{ background:#f1f5f9; padding:0.6rem 0.8rem; text-align:left; font-size:0.7rem; font-weight:600; color:#64748b; text-transform:uppercase; letter-spacing:0.05em; }}
  td {{ padding:0.65rem 0.8rem; border-bottom:1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom:none; }}
  .status-box {{ border-radius:8px; padding:0.85rem 1rem; font-size:0.88rem; line-height:1.5; background:{scan_bg}; border:1px solid {scan_border}; color:{scan_text}; }}
  .footer {{ margin-top:3rem; padding-top:1rem; border-top:1px solid #e2e8f0; font-size:0.72rem; color:#94a3b8; display:flex; justify-content:space-between; }}
  @media print {{ .topbar {{ display:none; }} body {{ padding-top:0; }} }}
</style>
</head>
<body>

  <div class="topbar">
    <button class="btn-print" onclick="window.print()">🖨 Print / Save PDF</button>
    <button class="btn-close" onclick="window.close()">✕ Close</button>
  </div>

  <div class="header">
    <div class="logo">🌿 ForestGuard Enterprise</div>
    <div class="report-meta">
      <h1>Deforestation Monitoring Report</h1>
      <div class="sub">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}</div>
      <div class="sub">Satellite data: Landsat 8/9 · 2018–2026</div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Forest Summary</div>
    <div class="grid3">
      <div class="card">
        <div class="card-label">Forest Name</div>
        <div class="card-value" style="font-size:1rem;">{aoi.name}</div>
      </div>
      <div class="card">
        <div class="card-label">Monitoring Since</div>
        <div class="card-value" style="font-size:1rem;">{aoi.created_at.strftime("%Y-%m-%d")}</div>
      </div>
      <div class="card">
        <div class="card-label">Last Scanned</div>
        <div class="card-value" style="font-size:0.9rem;">{scanned_str}</div>
      </div>
      <div class="card accent">
        <div class="card-label">Satellite Risk Level</div>
        <div class="card-value" style="color:{risk_color};">{risk_level}</div>
      </div>
      <div class="card green">
        <div class="card-label">Carbon Assessment</div>
        <div class="card-value" style="font-size:0.88rem;color:{carbon_color};">{carbon_display}</div>
      </div>
      <div class="card">
        <div class="card-label">Total Alerts</div>
        <div class="card-value">{len(alerts)}</div>
      </div>
    </div>
  </div>

  <div class="section">
    <div class="section-title">Monitoring Status</div>
    <div class="status-box">{scan_msg}</div>
  </div>

  <div class="section">
    <div class="section-title">Alert History ({len(alerts)} total)</div>
    {alerts_section}
  </div>

  <div class="footer">
    <span>ForestGuard Enterprise — Intelligent Deforestation Early Warning System</span>
    <span>Powered by Google Earth Engine · NDVI · CUSUM · BFAST · Isolation Forest</span>
  </div>

</body>
</html>"""
    return HTMLResponse(content=html)


# ═══════════════════════════════════════════════════════════════
# DASHBOARD
# ═══════════════════════════════════════════════════════════════

@router.get("/dashboard/stats", response_model=DashboardStats)
def get_dashboard_stats(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return summary stats for the dashboard sidebar."""
    aois = db.query(AOI).filter(AOI.org_id == current_user.org_id).all()
    aoi_ids = [a.id for a in aois]

    alerts = db.query(Alert).filter(Alert.aoi_id.in_(aoi_ids)).all() if aoi_ids else []

    scanned = [a.last_scanned for a in aois if a.last_scanned]
    last_scan = max(scanned) if scanned else None

    return DashboardStats(
        total_aois=len(aois),
        active_aois=sum(1 for a in aois if a.is_active),
        total_alerts=len(alerts),
        unresolved_alerts=sum(1 for a in alerts if not a.resolved),
        high_risk_count=sum(1 for a in alerts if a.risk_level == "HIGH" and not a.resolved),
        medium_risk_count=sum(1 for a in alerts if a.risk_level == "MEDIUM" and not a.resolved),
        low_risk_count=sum(1 for a in alerts if a.risk_level == "LOW" and not a.resolved),
        last_scan_time=last_scan
    )


# ═══════════════════════════════════════════════════════════════
# AOI ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.post("/aois/", response_model=AOIResponse)
def create_aoi(
    aoi: AOICreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst_or_above)
):
    new_aoi = AOI(
        name=aoi.name,
        org_id=current_user.org_id,
        geojson_polygon=json.dumps(aoi.geojson_polygon)
    )
    db.add(new_aoi)
    db.commit()
    db.refresh(new_aoi)

    try:
        scan_aoi_background(new_aoi.id)  # Huey: direct call enqueues
        logger.info(f"Background scan queued for AOI {new_aoi.id}")
    except Exception as e:
        logger.warning(f"Could not queue background scan for AOI {new_aoi.id}: {e}")

    return new_aoi


@router.get("/aois/", response_model=List[AOIResponse])
def get_aois(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    return db.query(AOI).filter(AOI.org_id == current_user.org_id).all()


# ✅ FIXED: Sub-path routes declared BEFORE /{aoi_id} to avoid FastAPI matching
#    "ndvi", "carbon", "report", "scan", "alerts" as the aoi_id parameter

@router.post("/aois/{aoi_id}/scan")
def trigger_manual_scan(
    aoi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst_or_above)
):
    """
    Run a full satellite scan for an AOI synchronously.
    Sends email + SMS alerts automatically if risk is HIGH or MEDIUM.
    Takes 30-90 seconds (GEE processing time).
    """
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")
 
    try:
        # Run scan synchronously — no separate Huey worker needed
        from backend.tasks import run_scan_now
        result = run_scan_now(aoi_id)
 
        if result.get("status") == "error":
            raise HTTPException(
                status_code=500,
                detail=result.get("error", "Scan failed")
            )
 
        return {
            "message":       f"Scan complete for AOI {aoi_id}",
            "risk_level":    result.get("risk_level"),
            "carbon_loss":   result.get("carbon_loss"),
            "confidence":    result.get("confidence"),
            "fire_detected": result.get("fire_detected"),
            "alerts_sent":   True if result.get("risk_level") in ("HIGH", "MEDIUM") else False,
        }
 
    except HTTPException:
        raise
    except Exception as e:
        logger.exception(f"Scan failed for AOI {aoi_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/aois/{aoi_id}/ndvi")
def get_aoi_ndvi(
    aoi_id: int,
    start_year: int = 2018,
    end_year: int = 2026,
    # ✅ FIX: default to landsat (30m) — uses 9× less GEE memory than Sentinel-2 (10m)
    # Use ?source=sentinel2 only for small AOIs (<10,000 ha)
    source: str = "landsat",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return NDVI time-series data for an AOI — powers the frontend chart."""
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")

    try:
        from core.roi import create_polygon_roi
        from core.ndvi import yearly_ndvi_timeseries, SOURCE_SENTINEL2, SOURCE_LANDSAT
        from core.risk_analysis import compute_risk_score

        src = SOURCE_SENTINEL2 if source == "sentinel2" else SOURCE_LANDSAT
        geojson    = json.loads(aoi.geojson_polygon)
        roi        = create_polygon_roi(geojson)
        ndvi_series = yearly_ndvi_timeseries(roi, start_year, end_year, source=src)
        risk       = compute_risk_score(ndvi_series)

        return {
            "aoi_id": aoi_id,
            "aoi_name": aoi.name,
            "start_year": start_year,
            "end_year": end_year,
            "ndvi_timeseries": {str(k): round(v, 4) for k, v in ndvi_series.items()},
            "risk_level": risk.get("risk_level", "UNKNOWN"),
            "slope": round(risk.get("slope", 0), 6),
            "variability": round(risk.get("variability", 0), 6),
        }
    except Exception as e:
        logger.exception(f"NDVI fetch failed for AOI {aoi_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/aois/{aoi_id}/carbon")
def get_aoi_carbon(
    aoi_id: int,
    start_year: int = 2018,
    end_year: int = 2026,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return carbon loss estimation for an AOI."""
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")

    try:
        from core.roi import create_polygon_roi, roi_area_hectares
        from core.ndvi import yearly_ndvi_timeseries, SOURCE_LANDSAT
        from core.carbon_estimation import estimate_carbon_loss, auto_detect_biome

        geojson    = json.loads(aoi.geojson_polygon)
        roi        = create_polygon_roi(geojson)
        area_ha    = roi_area_hectares(roi)
        # ✅ FIX: use Landsat for carbon too — large AOIs exceed Sentinel-2 memory limit
        ndvi_series = yearly_ndvi_timeseries(roi, start_year, end_year, source=SOURCE_LANDSAT)
        carbon     = estimate_carbon_loss(ndvi_series, area_hectares=area_ha)

        return {
            "aoi_id": aoi_id,
            "aoi_name": aoi.name,
            "area_hectares": round(area_ha, 2),
            "carbon_loss_tons": round(carbon.get("carbon_loss_tons", 0), 2),
            "co2_equivalent_tons": round(carbon.get("co2_equivalent_tons", 0), 2),
            "agb_carbon_tons": round(carbon.get("agb_carbon_tons", 0), 2),
            "bgb_carbon_tons": round(carbon.get("bgb_carbon_tons", 0), 2),
            "affected_area_ha": round(carbon.get("affected_area_ha", 0), 2),
            "annual_co2_loss": round(carbon.get("annual_co2_loss", 0), 2),
            "biome": carbon.get("biome", "default"),
            "methodology": carbon.get("methodology", "IPCC Tier 1"),
            "status": carbon.get("status", "Unknown"),
            "formula": "Carbon = Biomass × 0.47 (IPCC Tier 1)"
        }
    except Exception as e:
        logger.exception(f"Carbon fetch failed for AOI {aoi_id}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/aois/{aoi_id}/alerts", response_model=List[AlertResponse])
def get_aoi_alerts(
    aoi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get alert timeline for a specific AOI."""
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")
    return db.query(Alert).filter(
        Alert.aoi_id == aoi_id
    ).order_by(Alert.created_at.desc()).all()


@router.get("/aois/{aoi_id}/report", response_class=HTMLResponse)
def get_aoi_report(
    aoi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_user_from_query_token)
):
    """Generate a clean HTML report — no auto-print, no hardcoded threats."""
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")

    alerts = db.query(Alert).filter(
        Alert.aoi_id == aoi_id
    ).order_by(Alert.created_at.desc()).all()

    org = db.query(Organization).filter(
        Organization.id == current_user.org_id
    ).first()

    # ── Risk colours ──
    RISK_COLORS = {"HIGH": "#ef4444", "MEDIUM": "#f59e0b", "LOW": "#10b981"}
    risk_level  = aoi.last_risk_level or "PENDING"
    risk_color  = RISK_COLORS.get(risk_level, "#94a3b8")

    # ── Alert rows ──
    alert_rows_html = ""
    for a in alerts:
        ac       = RISK_COLORS.get(a.risk_level, "#94a3b8")
        conf_str = f"{a.confidence_score:.1f}%" if a.confidence_score is not None else "N/A"
        carb_str = f"{a.carbon_loss_tons:.2f} t" if a.carbon_loss_tons  is not None else "N/A"
        res_str  = "Resolved" if a.resolved else "Active"
        res_col  = "#10b981" if a.resolved else "#ef4444"
        alert_rows_html += f"""
        <tr>
          <td>{a.created_at.strftime("%Y-%m-%d %H:%M")}</td>
          <td><span style="color:{ac};font-weight:700;">{a.risk_level}</span></td>
          <td>{conf_str}</td>
          <td>{carb_str}</td>
          <td><span style="color:{res_col};">{res_str}</span></td>
        </tr>"""

    alerts_section = f"""
    <table>
      <thead>
        <tr>
          <th>Date (UTC)</th><th>Risk Level</th>
          <th>Confidence</th><th>Carbon Loss</th><th>Status</th>
        </tr>
      </thead>
      <tbody>{alert_rows_html}</tbody>
    </table>""" if alerts else     '<p style="color:#94a3b8;padding:1rem 0;">No alerts recorded — area appears stable.</p>'

    # ── Carbon display ──
    carbon_val = aoi.last_carbon_loss
    if carbon_val and carbon_val > 0.01:
        carbon_display = f"{carbon_val:.2f} t CO₂"
        carbon_color   = "#ef4444"
    else:
        carbon_display = "Stable — No significant loss"
        carbon_color   = "#10b981"

    # ── Last scanned ──
    scanned_str = aoi.last_scanned.strftime("%Y-%m-%d %H:%M UTC")         if aoi.last_scanned else "Not yet scanned"

    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>ForestGuard Report — {aoi.name}</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;600;700&display=swap');
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{ font-family: 'Inter', sans-serif; background: #fff; color: #1e293b; padding: 2.5rem; max-width: 900px; margin: 0 auto; }}

  .topbar {{ position: fixed; top: 0; right: 0; left: 0; background: #fff; padding: 0.75rem 2.5rem;
             border-bottom: 1px solid #e2e8f0; display: flex; justify-content: flex-end;
             gap: 0.5rem; z-index: 99; }}
  .btn-print {{ background: #10b981; color: #fff; border: none; padding: 0.45rem 1.1rem;
                border-radius: 6px; font-size: 0.82rem; font-weight: 600; cursor: pointer; }}
  .btn-close {{ background: #f1f5f9; color: #64748b; border: 1px solid #e2e8f0;
                padding: 0.45rem 0.9rem; border-radius: 6px; font-size: 0.82rem; cursor: pointer; }}
  body {{ padding-top: 4rem; }}

  .header {{ display: flex; justify-content: space-between; align-items: flex-start;
             border-bottom: 3px solid #10b981; padding-bottom: 1.25rem; margin-bottom: 2rem; }}
  .logo {{ font-size: 1.3rem; font-weight: 700; color: #10b981; display: flex; align-items: center; gap: 0.4rem; }}
  .report-meta {{ text-align: right; }}
  .report-meta h1 {{ font-size: 1rem; font-weight: 600; color: #1e293b; margin-bottom: 0.2rem; }}
  .report-meta .sub {{ font-size: 0.75rem; color: #94a3b8; }}

  .section {{ margin-bottom: 2.5rem; }}
  .section-title {{ font-size: 0.7rem; font-weight: 700; text-transform: uppercase;
                    letter-spacing: 0.1em; color: #94a3b8; margin-bottom: 0.85rem;
                    padding-bottom: 0.4rem; border-bottom: 1px solid #e2e8f0; }}

  .grid3 {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 1rem; }}
  .card {{ background: #f8fafc; border-radius: 8px; padding: 1rem 1.1rem; border-left: 4px solid #e2e8f0; }}
  .card.accent {{ border-left-color: {risk_color}; }}
  .card.green  {{ border-left-color: #10b981; }}
  .card-label  {{ font-size: 0.7rem; color: #94a3b8; font-weight: 500; margin-bottom: 0.3rem; text-transform: uppercase; letter-spacing: 0.05em; }}
  .card-value  {{ font-size: 1.15rem; font-weight: 700; color: #1e293b; }}

  table {{ width: 100%; border-collapse: collapse; font-size: 0.85rem; }}
  th {{ background: #f1f5f9; padding: 0.6rem 0.8rem; text-align: left; font-size: 0.7rem;
        font-weight: 600; color: #64748b; text-transform: uppercase; letter-spacing: 0.05em; }}
  td {{ padding: 0.65rem 0.8rem; border-bottom: 1px solid #f1f5f9; }}
  tr:last-child td {{ border-bottom: none; }}

  .footer {{ margin-top: 3rem; padding-top: 1rem; border-top: 1px solid #e2e8f0;
             font-size: 0.72rem; color: #94a3b8; display: flex; justify-content: space-between; }}

  @media print {{
    .topbar {{ display: none; }}
    body {{ padding-top: 0; }}
  }}
</style>
</head>
<body>

  <!-- Print bar — hidden on actual print -->
  <div class="topbar">
    <button class="btn-print" onclick="window.print()">🖨 Print / Save PDF</button>
    <button class="btn-close" onclick="window.close()">✕ Close</button>
  </div>

  <!-- Header -->
  <div class="header">
    <div class="logo">🌿 ForestGuard Enterprise</div>
    <div class="report-meta">
      <h1>Deforestation Monitoring Report</h1>
      <div class="sub">Generated: {datetime.now().strftime("%Y-%m-%d %H:%M UTC")}</div>
      <div class="sub">By: {current_user.full_name}</div>
    </div>
  </div>

  <!-- AOI Summary -->
  <div class="section">
    <div class="section-title">AOI Summary</div>
    <div class="grid3">
      <div class="card">
        <div class="card-label">Area Name</div>
        <div class="card-value" style="font-size:1rem;">{aoi.name}</div>
      </div>
      <div class="card">
        <div class="card-label">Organization</div>
        <div class="card-value" style="font-size:1rem;">{org.name if org else "N/A"}</div>
      </div>
      <div class="card">
        <div class="card-label">Monitoring Since</div>
        <div class="card-value" style="font-size:1rem;">{aoi.created_at.strftime("%Y-%m-%d")}</div>
      </div>
      <div class="card accent">
        <div class="card-label">Current Risk Level</div>
        <div class="card-value" style="color:{risk_color};">{risk_level}</div>
      </div>
      <div class="card">
        <div class="card-label">Last Scanned</div>
        <div class="card-value" style="font-size:0.9rem;">{scanned_str}</div>
      </div>
      <div class="card green">
        <div class="card-label">Carbon Assessment</div>
        <div class="card-value" style="font-size:0.88rem;color:{carbon_color};">{carbon_display}</div>
      </div>
    </div>
  </div>

  <!-- Alert History -->
  <div class="section">
    <div class="section-title">Alert History ({len(alerts)} total)</div>
    {alerts_section}
  </div>

  <!-- Footer -->
  <div class="footer">
    <span>ForestGuard Enterprise — Intelligent Deforestation Early Warning System</span>
    <span>Satellite analysis: Landsat 8/9 + Sentinel-2 · 2018–2026</span>
  </div>

</body>
</html>"""

    return HTMLResponse(content=html)
@router.delete("/aois/{aoi_id}")
def delete_aoi(
    aoi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Delete an AOI — admin only."""
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")
    db.delete(aoi)
    db.commit()
    return {"message": f"AOI {aoi_id} deleted"}


# ✅ /{aoi_id} catch-all is LAST — all sub-paths declared above
@router.get("/aois/{aoi_id}", response_model=AOIResponse)
def get_aoi(
    aoi_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    aoi = db.query(AOI).filter(
        AOI.id == aoi_id,
        AOI.org_id == current_user.org_id
    ).first()
    if not aoi:
        raise HTTPException(status_code=404, detail="AOI not found")
    return aoi


# ═══════════════════════════════════════════════════════════════
# ALERTS ENDPOINTS
# ═══════════════════════════════════════════════════════════════

@router.get("/alerts/", response_model=List[AlertResponse])
def get_alerts(
    resolved: Optional[bool] = None,
    risk_level: Optional[str] = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Get all alerts for the organization. Supports filtering."""
    aois = db.query(AOI).filter(AOI.org_id == current_user.org_id).all()
    aoi_ids = [a.id for a in aois]
    if not aoi_ids:
        return []

    query = db.query(Alert).filter(Alert.aoi_id.in_(aoi_ids))

    if resolved is not None:
        query = query.filter(Alert.resolved == resolved)
    if risk_level:
        query = query.filter(Alert.risk_level == risk_level.upper())

    return query.order_by(Alert.created_at.desc()).all()


@router.patch("/alerts/{alert_id}/resolve", response_model=AlertResponse)
def resolve_alert(
    alert_id: int,
    body: AlertResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_analyst_or_above)
):
    """Mark an alert as resolved or unresolved."""
    aois = db.query(AOI).filter(AOI.org_id == current_user.org_id).all()
    aoi_ids = [a.id for a in aois]

    alert = db.query(Alert).filter(
        Alert.id == alert_id,
        Alert.aoi_id.in_(aoi_ids)
    ).first()
    if not alert:
        raise HTTPException(status_code=404, detail="Alert not found")

    alert.resolved = body.resolved
    alert.resolved_at = datetime.utcnow() if body.resolved else None
    db.commit()
    db.refresh(alert)
    return alert


# ═══════════════════════════════════════════════════════════════
# ORGANIZATION / WEBHOOK
# ═══════════════════════════════════════════════════════════════

@router.get("/organizations/me")
def get_organization(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """Return current user's organization details."""
    org = db.query(Organization).filter(
        Organization.id == current_user.org_id
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    return {
        "id": org.id,
        "name": org.name,
        "webhook_url": org.webhook_url,
        "created_at": org.created_at
    }


@router.put("/organizations/webhook")
def update_webhook(
    body: WebhookUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Update the organization's webhook URL for alert notifications — admin only."""
    org = db.query(Organization).filter(
        Organization.id == current_user.org_id
    ).first()
    if not org:
        raise HTTPException(status_code=404, detail="Organization not found")
    org.webhook_url = body.webhook_url
    db.commit()
    return {"message": "Webhook URL updated", "webhook_url": org.webhook_url}


# ═══════════════════════════════════════════════════════════════
# USER MANAGEMENT (Admin only)
# ═══════════════════════════════════════════════════════════════

@router.get("/users/", response_model=List[UserResponse])
def list_users(
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """List all users in the organization — admin only."""
    return db.query(User).filter(User.org_id == current_user.org_id).all()


@router.post("/users/", response_model=UserResponse)
def create_user(
    body: UserCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Create a new user in the organization — admin only."""
    if body.role not in ("admin", "analyst", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role. Use: admin, analyst, viewer")

    existing = db.query(User).filter(User.email == body.email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    new_user = User(
        email=body.email,
        hashed_password=get_password_hash(body.password),
        full_name=body.full_name,
        role=body.role,
        org_id=current_user.org_id
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    return new_user


@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    role: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Change a user's role — admin only."""
    if role not in ("admin", "analyst", "viewer"):
        raise HTTPException(status_code=400, detail="Invalid role")

    user = db.query(User).filter(
        User.id == user_id,
        User.org_id == current_user.org_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    user.role = role
    db.commit()
    return {"message": f"User {user.email} role updated to {role}"}


@router.patch("/users/{user_id}/deactivate")
def deactivate_user(
    user_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_admin)
):
    """Deactivate a user account — admin only."""
    user = db.query(User).filter(
        User.id == user_id,
        User.org_id == current_user.org_id
    ).first()
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == current_user.id:
        raise HTTPException(status_code=400, detail="Cannot deactivate yourself")

    user.is_active = False
    db.commit()
    return {"message": f"User {user.email} deactivated"}


# ══════════════════════════════════════════════════════════════════════════════
# AI CHAT — POST /api/chat
# ══════════════════════════════════════════════════════════════════════════════
 
@router.post("/chat", response_model=ChatResponse)
async def ai_chat(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_optional_user),  # Optional auth for testing
):
    """
    ForestGuard AI chatbot.
    Answers questions AND returns action payloads for commands like:
    - "scan this AOI"  → action: scan
    - "show NDVI"      → action: ndvi
    - "compare areas"  → action: compare
    Returns: { answer, session_id, action, action_payload }
    """
    from backend.rag import get_chatbot, parse_action_from_response
    
    # Handle both authenticated and guest users
    user_id = current_user.id if current_user else "guest"
    org_id = current_user.org_id if current_user else "default-org"
 
    bot     = get_chatbot()
    session = f"{user_id}-{req.session_id}"
    raw     = bot.chat(
        message    = req.message,
        session_id = session,
        aoi_context = req.aoi_context,
    )
 
    clean_text, action_info = parse_action_from_response(raw)
 
    response = {
        "answer":         clean_text,
        "session_id":     req.session_id,
        "action":         None,
        "action_payload": None,
    }
 
    if action_info:
        action_type   = action_info["action"]   # scan / ndvi / compare / report / alerts / carbon
        action_target = action_info["target"]   # AOI name or "current" or "all"
 
        response["action"] = action_type
 
        # Resolve AOI ID from name or use current (only if authenticated)
        if current_user:
            if action_target not in ("all", "show", "current") and req.aoi_context:
                aoi_id = req.aoi_context.get("aoi_id")
            elif action_target not in ("all", "show"):
                # Try to find by name
                aoi = db.query(AOI).filter(
                    AOI.org_id   == org_id,
                    AOI.name.ilike(f"%{action_target}%"),
                    AOI.is_active == True,
                ).first()
                aoi_id = aoi.id if aoi else (
                    req.aoi_context.get("aoi_id") if req.aoi_context else None
                )
            else:
                aoi_id = None
        else:
            aoi_id = req.aoi_context.get("aoi_id") if req.aoi_context else None
 
        response["action_payload"] = {
            "aoi_id":   aoi_id,
            "aoi_name": action_target,
        }
 
        # If scan requested — enqueue immediately
        if action_type == "scan" and aoi_id:
            from backend.tasks import scan_aoi_background
            scan_aoi_background(aoi_id)
            aoi_rec = db.query(AOI).filter(AOI.id == aoi_id).first()
            name = aoi_rec.name if aoi_rec else action_target
            response["answer"] = (
                f"Scanning {name} now using Google Earth Engine satellite data. "
                f"The full 8-stage analysis takes about 60–90 seconds. "
                f"I'll update the dashboard when complete."
            )
 
    return response
 
 
# ══════════════════════════════════════════════════════════════════════════════
# CHAT SUGGESTIONS — GET /api/chat/suggestions
# ══════════════════════════════════════════════════════════════════════════════
 
@router.get("/chat/suggestions")
def get_chat_suggestions(
    aoi_id:     Optional[int]   = None,
    risk_level: Optional[str]   = None,
    aoi_name:   Optional[str]   = None,
    db:         Session         = Depends(get_db),
    current_user: User          = Depends(get_optional_user),
):
    """Return context-aware suggestion chips for the chat UI."""
    from backend.rag import get_smart_suggestions
 
    ctx = None
    if aoi_id:
        aoi = db.query(AOI).filter(AOI.id == aoi_id).first()
        if aoi:
            ctx = {
                "aoi_id":     aoi.id,
                "aoi_name":   aoi.name,
                "risk_level": aoi.last_risk_level,
            }
    elif risk_level or aoi_name:
        ctx = {"risk_level": risk_level, "aoi_name": aoi_name or "this area"}
 
    return {"suggestions": get_smart_suggestions(ctx)}
 
 
# ══════════════════════════════════════════════════════════════════════════════
# VOICE COMMAND — POST /api/voice/command
# ══════════════════════════════════════════════════════════════════════════════
 
@router.post("/voice/command", response_model=VoiceCommandResponse)
async def voice_command(
    req:          VoiceCommandRequest,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    """
    Siri-style voice agent.
    Receives speech transcript → determines intent → executes action or answers.
 
    Every response has:
      speech_text   — what the TTS reads aloud
      action        — "scan" / "ndvi" / "compare" / "report" / "alerts" / "explain" / None
      action_payload — { aoi_id, aoi_name } for the frontend to act on
    """
    from backend.rag import get_chatbot, parse_action_from_response
 
    transcript = req.transcript.strip()
    bot        = get_chatbot()
    session    = f"{current_user.id}-voice"
 
    # Get all org AOIs for context
    aois = db.query(AOI).filter(
        AOI.org_id   == current_user.org_id,
        AOI.is_active == True,
    ).all()
    aoi_names = [a.name for a in aois]
 
    # ── Build rich context prompt ──────────────────────────────────────────
    current_aoi_name = None
    current_aoi      = None
    if req.current_aoi_id:
        current_aoi = db.query(AOI).filter(AOI.id == req.current_aoi_id).first()
        if current_aoi:
            current_aoi_name = current_aoi.name
 
    aoi_context_str = ""
    if current_aoi_name:
        aoi_context_str = f"\nCurrently selected AOI: {current_aoi_name}"
    if aoi_names:
        aoi_context_str += f"\nAvailable AOIs: {', '.join(aoi_names)}"
 
    enhanced_transcript = f"{transcript}{aoi_context_str}"
 
    # ── Call AI agent ──────────────────────────────────────────────────────
    raw = bot.chat(
        message    = enhanced_transcript,
        session_id = session,
    )
    speech_text, action_info = parse_action_from_response(raw)
 
    # Trim for TTS (max 300 chars, stop at sentence boundary)
    speech_text = _trim_for_speech(speech_text, 300)
 
    if not action_info:
        return VoiceCommandResponse(
            speech_text   = speech_text,
            action        = "explain",
            action_payload = None,
        )
 
    action_type   = action_info["action"]
    action_target = action_info["target"]
 
    # ── Resolve AOI ────────────────────────────────────────────────────────
    aoi_id   = None
    aoi_name = action_target
 
    if action_target in ("current", current_aoi_name) and req.current_aoi_id:
        aoi_id   = req.current_aoi_id
        aoi_name = current_aoi_name or "this area"
    elif action_target not in ("all", "show"):
        # Match by name (partial)
        matched = db.query(AOI).filter(
            AOI.org_id   == current_user.org_id,
            AOI.name.ilike(f"%{action_target}%"),
            AOI.is_active == True,
        ).first()
        if matched:
            aoi_id   = matched.id
            aoi_name = matched.name
        elif req.current_aoi_id:
            aoi_id   = req.current_aoi_id
            aoi_name = current_aoi_name or "current area"
 
    # ── Execute SCAN immediately ───────────────────────────────────────────
    if action_type == "scan" and aoi_id:
        from backend.tasks import scan_aoi_background
        scan_aoi_background(aoi_id)
        speech_text = (
            f"Starting satellite scan for {aoi_name} right now. "
            f"Google Earth Engine will analyse Landsat and Sentinel imagery. "
            f"Results will be ready in about 60 to 90 seconds."
        )
    elif action_type == "compare":
        speech_text = (
            f"Opening comparison view for all {len(aois)} areas. "
            f"You can see NDVI trends side by side for "
            f"{', '.join(aoi_names[:3])}{'and more' if len(aoi_names) > 3 else ''}."
        )
    elif action_type == "report" and aoi_id:
        speech_text = (
            f"Opening the full analysis report for {aoi_name}. "
            f"It includes NDVI trends, risk score breakdown, carbon pools, and hotspot data."
        )
    elif action_type == "alerts":
        speech_text = "Showing your current deforestation alerts. Check the alerts tab."
    elif action_type in ("ndvi", "carbon") and aoi_id:
        speech_text = (
            f"Showing {'NDVI analysis' if action_type == 'ndvi' else 'carbon impact'} "
            f"for {aoi_name}. Loading satellite vegetation data now."
        )
 
    return VoiceCommandResponse(
        speech_text    = speech_text,
        action         = action_type,
        action_payload = {"aoi_id": aoi_id, "aoi_name": aoi_name},
    )
 
 
# ══════════════════════════════════════════════════════════════════════════════
# CLEAR CHAT SESSION — DELETE /api/chat/session
# ══════════════════════════════════════════════════════════════════════════════
 
@router.delete("/chat/session")
def clear_chat_session(
    session_id:   str  = "default",
    current_user: User = Depends(get_current_user),
):
    from backend.rag import get_chatbot
    get_chatbot().clear_session(f"{current_user.id}-{session_id}")
    return {"cleared": True}
 
 
# ══════════════════════════════════════════════════════════════════════════════
# OFFICER CONTACTS
# ══════════════════════════════════════════════════════════════════════════════
 
@router.post("/officers/", status_code=201)
def create_officer(
    data:         OfficerContactCreate,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(require_admin),
):
    """Register an officer to receive deforestation/fire alert emails."""
    officer = OfficerContact(
        org_id      = current_user.org_id,
        name        = data.name,
        email       = data.email,
        phone       = data.phone,
        alert_types = ",".join(data.alert_types),
    )
    db.add(officer)
    db.commit()
    db.refresh(officer)
    return {
        "id":          officer.id,
        "name":        officer.name,
        "email":       officer.email,
        "phone":       officer.phone,
        "alert_types": data.alert_types,
    }
 
 
@router.get("/officers/")
def list_officers(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(get_current_user),
):
    officers = db.query(OfficerContact).filter(
        OfficerContact.org_id == current_user.org_id
    ).all()
    return [
        {
            "id":          o.id,
            "name":        o.name,
            "email":       o.email,
            "phone":       o.phone,
            "alert_types": (o.alert_types or "HIGH,MEDIUM").split(","),
            "is_active":   o.is_active,
        }
        for o in officers
    ]
 
 
@router.delete("/officers/{officer_id}")
def delete_officer(
    officer_id:   int,
    db:           Session = Depends(get_db),
    current_user: User    = Depends(require_admin),
):
    officer = db.query(OfficerContact).filter(
        OfficerContact.id     == officer_id,
        OfficerContact.org_id == current_user.org_id,
    ).first()
    if not officer:
        raise HTTPException(status_code=404, detail="Officer not found")
    db.delete(officer)
    db.commit()
    return {"deleted": True}
 
 
@router.post("/officers/test-alert")
def test_alert_notification(
    db:           Session = Depends(get_db),
    current_user: User    = Depends(require_admin),
):
    """
    Send a test email to ALL registered officers.
    Use this to verify your SMTP / Gmail App Password setup.
    """
    from backend.tasks import _send_email
    officers = db.query(OfficerContact).filter(
        OfficerContact.org_id   == current_user.org_id,
        OfficerContact.is_active == True,
    ).all()
 
    if not officers:
        raise HTTPException(status_code=400, detail="No officers registered yet")
 
    org = db.query(Organization).filter(
        Organization.id == current_user.org_id
    ).first()
    org_name = org.name if org else "ForestGuard"
 
    sent = 0
    for o in officers:
        if o.email:
            ok = _send_email(
                to_email      = o.email,
                officer_name  = o.name,
                aoi_name      = "TEST AREA (Verification Email)",
                risk_level    = "MEDIUM",
                carbon_loss   = 1234.5,
                confidence    = 85.0,
                fire_detected = False,
                org_name      = org_name,
            )
            if ok:
                sent += 1
 
    return {
        "message":      f"Test alert sent to {sent} officer(s)",
        "emails_sent":  sent,
        "total_officers": len(officers),
    }
 
 
# ── Helper ─────────────────────────────────────────────────────────────────────
def _trim_for_speech(text: str, max_chars: int = 300) -> str:
    """Trim long text to natural sentence boundary for TTS."""
    text = re.sub(r"[#*`_]", "", text)
    text = re.sub(r"\n+", " ", text).strip()
    if len(text) <= max_chars:
        return text
    truncated = text[:max_chars]
    last = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
    return truncated[:last + 1] if last > 30 else truncated + "..."




"""
ForestGuard — backend/tasks.py

KEY FIXES:
  1. run_scan_now(aoi_id) — synchronous scan that runs INSIDE the FastAPI
     process.  No separate Huey worker needed.  Call this from the API
     endpoint so email is sent immediately after the scan completes.

  2. scan_aoi_background — kept as a Huey task for the scheduled
     auto-scan (every 5 days).  It now calls run_scan_now() internally.

  3. Email — loads .env explicitly so SMTP vars are always available.

  4. SMS via Twilio — sends an SMS alert to a configured phone number.
     Set TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_FROM_NUMBER,
     ALERT_PHONE_NUMBER in .env.  If Twilio creds are missing, SMS is
     silently skipped.
"""

import logging
import os
import json
import smtplib
import urllib.request
from datetime import datetime
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

logger = logging.getLogger("forestguard.tasks")

# ── Load .env into os.environ immediately ────────────────────────────────────
# The Huey worker is a separate process — without this, os.environ is empty.
def _load_env():
    try:
        from dotenv import load_dotenv
        _root = Path(__file__).resolve().parent.parent
        for candidate in [_root / ".env", Path.cwd() / ".env",
                          Path(__file__).resolve().parent / ".env"]:
            if candidate.exists():
                load_dotenv(candidate, override=True)
                logger.info(f"[TASKS] Loaded .env from {candidate}")
                return
        logger.warning("[TASKS] No .env file found")
    except ImportError:
        pass

_load_env()
# ─────────────────────────────────────────────────────────────────────────────

from huey import SqliteHuey, crontab
from sqlalchemy.orm import Session

from backend.database import SessionLocal
from backend.models import AOI, Alert, OfficerContact, Organization
from core.auth import initialize_gee

logger = logging.getLogger("forestguard.tasks")

huey = SqliteHuey("forestguard_tasks", filename="forestguard_queue.db")

try:
    initialize_gee()
    logger.info("GEE initialized in tasks module")
except Exception as e:
    logger.error(f"GEE init failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  EMAIL
# ══════════════════════════════════════════════════════════════════════════════

def _send_email(to_email: str, officer_name: str, aoi_name: str,
                risk_level: str, carbon_loss: float, confidence: float,
                fire_detected: bool, org_name: str) -> bool:
    """Send HTML alert email. Returns True on success."""
    _load_env()  # re-read in case worker env was stale

    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "").strip()
    smtp_pass = os.environ.get("SMTP_PASSWORD", "").strip()
    smtp_from = os.environ.get("SMTP_FROM", smtp_user).strip() or smtp_user

    logger.info(f"[EMAIL] To: {to_email} | SMTP: {smtp_host}:{smtp_port}")
    logger.info(f"[EMAIL] User set: {bool(smtp_user)} | Pass set: {bool(smtp_pass)}")

    if not smtp_user:
        logger.error("[EMAIL] SMTP_USER empty — add to .env: SMTP_USER=your@gmail.com")
        return False
    if not smtp_pass:
        logger.error(
            "[EMAIL] SMTP_PASSWORD empty.\n"
            "  Gmail requires an App Password (NOT your normal password).\n"
            "  Steps: myaccount.google.com → Security → 2-Step Verification\n"
            "         → App passwords → ForestGuard → Generate\n"
            "  Add to .env:  SMTP_PASSWORD=xxxx xxxx xxxx xxxx"
        )
        return False

    risk_color = {"HIGH": "#dc2626", "MEDIUM": "#d97706", "LOW": "#16a34a"}.get(
        risk_level, "#6b7280"
    )
    ts = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fire_note = "🔥 Active fire detected. " if fire_detected else ""
    fire_li = "<li>🔥 MODIS FIRMS active fire confirmed in area</li>" if fire_detected else ""
    alert_type = "FIRE ALERT" if fire_detected else "DEFORESTATION ALERT"

    html = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:#161b22;border-radius:12px;border:1px solid #30363d;overflow:hidden;">

  <tr><td style="background:#0f4c2a;padding:24px 32px;">
    <p style="color:#10b981;font-size:22px;font-weight:700;margin:0;letter-spacing:1px;">
      🌿 FORESTGUARD ENTERPRISE
    </p>
    <p style="color:#6ee7b7;font-size:12px;margin:6px 0 0;">
      Intelligent Deforestation Monitoring · Telangana, India
    </p>
  </td></tr>

  <tr><td style="background:{risk_color};padding:14px 32px;">
    <p style="color:white;font-size:18px;font-weight:700;margin:0;">
      ⚠️ {risk_level} RISK {alert_type}
    </p>
    <p style="color:rgba(255,255,255,.85);font-size:12px;margin:4px 0 0;">
      {fire_note}Organisation: {org_name} · {ts}
    </p>
  </td></tr>

  <tr><td style="padding:28px 32px;">
    <p style="color:#c9d1d9;font-size:15px;margin:0 0 6px;">Dear {officer_name},</p>
    <p style="color:#8b949e;font-size:13px;line-height:1.7;margin:0 0 22px;">
      ForestGuard's satellite monitoring has detected a
      <strong style="color:{risk_color}">{risk_level} RISK</strong>
      event in the area shown below. Immediate field verification is recommended.
    </p>

    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:22px;">
      <tr>
        <td width="50%" style="padding-right:6px;">
          <div style="background:#1c2431;border-radius:8px;padding:14px;border-left:3px solid {risk_color};">
            <p style="color:#8b949e;font-size:10px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">Area of Interest</p>
            <p style="color:#f1f5f9;font-size:16px;font-weight:600;margin:0;">{aoi_name}</p>
          </div>
        </td>
        <td width="50%" style="padding-left:6px;">
          <div style="background:#1c2431;border-radius:8px;padding:14px;border-left:3px solid #06b6d4;">
            <p style="color:#8b949e;font-size:10px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">Risk Level</p>
            <p style="color:{risk_color};font-size:16px;font-weight:700;margin:0;">{risk_level}</p>
          </div>
        </td>
      </tr>
      <tr><td colspan="2" style="height:10px;"></td></tr>
      <tr>
        <td width="50%" style="padding-right:6px;">
          <div style="background:#1c2431;border-radius:8px;padding:14px;border-left:3px solid #10b981;">
            <p style="color:#8b949e;font-size:10px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">Estimated CO₂ Loss</p>
            <p style="color:#f1f5f9;font-size:16px;font-weight:600;margin:0;">
              {carbon_loss:,.1f} <span style="font-size:11px;color:#8b949e;">t CO₂eq</span>
            </p>
          </div>
        </td>
        <td width="50%" style="padding-left:6px;">
          <div style="background:#1c2431;border-radius:8px;padding:14px;border-left:3px solid #8b5cf6;">
            <p style="color:#8b949e;font-size:10px;letter-spacing:1px;text-transform:uppercase;margin:0 0 4px;">Confidence</p>
            <p style="color:#f1f5f9;font-size:16px;font-weight:600;margin:0;">
              {confidence:.0f}<span style="font-size:11px;color:#8b949e;">/100</span>
            </p>
          </div>
        </td>
      </tr>
    </table>

    <div style="background:#1c2431;border-radius:8px;padding:14px;margin-bottom:22px;">
      <p style="color:#10b981;font-size:13px;font-weight:600;margin:0 0 8px;">📡 Detection methods:</p>
      <ul style="color:#8b949e;font-size:13px;line-height:1.9;margin:0;padding-left:18px;">
        <li>Landsat 8/9 + Sentinel-2 NDVI time-series decline (2018–present)</li>
        <li>CUSUM + BFAST structural breakpoint detected</li>
        <li>Ensemble ML risk model (Isolation Forest + slope + acceleration)</li>
        <li>Forest Health Score (NDVI+EVI+SAVI+NBR) declining</li>
        {fire_li}
      </ul>
    </div>

    <div style="text-align:center;margin-bottom:22px;">
      <a href="http://127.0.0.1:8000"
         style="display:inline-block;background:#10b981;color:white;
                text-decoration:none;padding:12px 30px;border-radius:8px;
                font-weight:600;font-size:14px;">
        View Full Dashboard →
      </a>
    </div>

    <p style="color:#484f58;font-size:11px;line-height:1.6;margin:0;">
      Auto-generated by ForestGuard Enterprise.
      Carbon: IPCC Tier 1 methodology. Satellite: Google Earth Engine.
    </p>
  </td></tr>

  <tr><td style="background:#0d1117;padding:14px 32px;border-top:1px solid #21262d;">
    <p style="color:#484f58;font-size:11px;margin:0;text-align:center;">
      ForestGuard Enterprise · Google Earth Engine + OpenRouter AI
    </p>
  </td></tr>
</table>
</td></tr>
</table>
</body></html>"""

    plain = (
        f"ForestGuard — {risk_level} RISK {alert_type}\n\n"
        f"Dear {officer_name},\n\n"
        f"Area:       {aoi_name}\n"
        f"Risk:       {risk_level}\n"
        f"CO₂ Loss:   {carbon_loss:,.1f} t CO₂eq\n"
        f"Confidence: {confidence:.0f}/100\n"
        + ("FIRE DETECTED.\n" if fire_detected else "")
        + f"Time:       {ts}\n\n"
        "Login: http://127.0.0.1:8000\n\n— ForestGuard Enterprise"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌿 ForestGuard {risk_level} {alert_type}: {aoi_name}"
    msg["From"] = smtp_from
    msg["To"] = to_email
    msg.attach(MIMEText(plain, "plain"))
    msg.attach(MIMEText(html, "html"))

    try:
        logger.info(f"[EMAIL] Connecting {smtp_host}:{smtp_port} ...")
        with smtplib.SMTP(smtp_host, smtp_port, timeout=20) as s:
            s.ehlo()
            s.starttls()
            s.ehlo()
            s.login(smtp_user, smtp_pass)
            s.sendmail(smtp_from, to_email, msg.as_string())
        logger.info(f"[EMAIL] ✅ Sent to {to_email} | {aoi_name} | {risk_level}")
        return True

    except smtplib.SMTPAuthenticationError:
        logger.error(
            f"[EMAIL] ❌ Auth failed for {smtp_user}.\n"
            "  Use a Gmail App Password, not your regular password.\n"
            "  myaccount.google.com → Security → App passwords"
        )
        return False
    except Exception as e:
        logger.error(f"[EMAIL] ❌ {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  SMS via Twilio
# ══════════════════════════════════════════════════════════════════════════════

def _send_sms(to_phone: str, aoi_name: str, risk_level: str,
              carbon_loss: float, fire_detected: bool) -> bool:
    """
    Send SMS via Twilio.  All 4 env vars must be set:
      TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN,
      TWILIO_FROM_NUMBER (+1415…),  ALERT_PHONE_NUMBER (+91…)
    """
    account_sid  = os.environ.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token   = os.environ.get("TWILIO_AUTH_TOKEN", "").strip()
    from_number  = os.environ.get("TWILIO_FROM_NUMBER", "").strip()

    if not all([account_sid, auth_token, from_number, to_phone]):
        logger.info("[SMS] Twilio not configured — skipping SMS")
        return False

    fire_note = " 🔥 FIRE DETECTED." if fire_detected else ""
    body = (
        f"🌿 FORESTGUARD ALERT\n"
        f"Area: {aoi_name}\n"
        f"Risk: {risk_level}{fire_note}\n"
        f"CO₂: {carbon_loss:,.0f} t\n"
        f"Login: http://127.0.0.1:8000"
    )

    try:
        from twilio.rest import Client
        Client(account_sid, auth_token).messages.create(
            body=body, from_=from_number, to=to_phone
        )
        logger.info(f"[SMS] ✅ Sent to {to_phone} | {risk_level} | {aoi_name}")
        return True
    except ImportError:
        logger.warning("[SMS] twilio not installed — run: pip install twilio")
        return False
    except Exception as e:
        logger.error(f"[SMS] ❌ {type(e).__name__}: {e}")
        return False


# ══════════════════════════════════════════════════════════════════════════════
#  CONFIDENCE
# ══════════════════════════════════════════════════════════════════════════════

def _compute_confidence(risk_level: str, ndvi_series: dict) -> float:
    values = list(ndvi_series.values()) if ndvi_series else []
    n = len(values)
    if n < 2:
        return 30.0
    qty = min(n * 8, 60)
    declines = sum(1 for i in range(1, n) if values[i] < values[i - 1])
    trend = (declines / (n - 1)) * 40 if risk_level in ("HIGH", "MEDIUM") else 0
    return round(min(qty + trend, 100), 1)


# ══════════════════════════════════════════════════════════════════════════════
#  WEBHOOK
# ══════════════════════════════════════════════════════════════════════════════

def _dispatch_webhook(url: str, payload: dict):
    try:
        data = json.dumps(payload).encode()
        req = urllib.request.Request(
            url, data=data,
            headers={"Content-Type": "application/json"},
            method="POST"
        )
        with urllib.request.urlopen(req, timeout=5):
            pass
        logger.info(f"Webhook dispatched → {url}")
    except Exception as e:
        logger.warning(f"Webhook failed: {e}")


# ══════════════════════════════════════════════════════════════════════════════
#  CORE SCAN LOGIC  —  runs synchronously, no Huey needed
# ══════════════════════════════════════════════════════════════════════════════

def run_scan_now(aoi_id: int) -> dict:
    """
    Run the full 8-stage GEE analysis synchronously inside the calling process.
    Saves results to DB, creates Alert if HIGH/MEDIUM, sends email + SMS.
    Returns a result dict so the API endpoint can return it to the frontend.

    Call this directly from the /scan endpoint — no Huey worker required.
    """
    _load_env()

    db: Session = SessionLocal()
    result = {"aoi_id": aoi_id, "status": "error", "risk_level": None}

    try:
        aoi = db.query(AOI).filter(AOI.id == aoi_id).first()
        if not aoi:
            logger.error(f"run_scan_now: AOI {aoi_id} not found")
            result["error"] = "AOI not found"
            return result

        logger.info(f"[SCAN] Starting AOI {aoi_id}: '{aoi.name}'")

        from core.roi import create_polygon_roi
        from core.pipeline import run_analysis

        geojson = (
            json.loads(aoi.geojson_polygon)
            if isinstance(aoi.geojson_polygon, str)
            else aoi.geojson_polygon
        )
        roi = create_polygon_roi(geojson)
        current_year = datetime.utcnow().year
        analysis = run_analysis(roi, 2018, current_year, source="landsat")

        risk_level    = analysis.get("risk", {}).get("risk_level", "LOW")
        ndvi_series   = analysis.get("ndvi_timeseries", {})
        carbon_loss   = analysis.get("carbon_impact", {}).get("co2_equivalent_tons", 0.0) or 0.0
        fire_detected = analysis.get("fire_detection", {}).get("fire_detected", False)
        confidence    = _compute_confidence(risk_level, ndvi_series)

        # ── Persist to DB ────────────────────────────────────────────────
        aoi.last_scanned     = datetime.utcnow()
        aoi.last_risk_level  = risk_level
        aoi.last_carbon_loss = carbon_loss

        if risk_level in ("HIGH", "MEDIUM"):
            alert = Alert(
                aoi_id           = aoi_id,
                risk_level       = risk_level,
                details          = json.dumps(analysis),
                created_at       = datetime.utcnow(),
                resolved         = False,
                confidence_score = confidence,
                carbon_loss_tons = carbon_loss,
            )
            db.add(alert)
            logger.info(f"[SCAN] Alert created: {risk_level} for '{aoi.name}'")

            org = db.query(Organization).filter(Organization.id == aoi.org_id).first()

            # Webhook
            if org and org.webhook_url:
                _dispatch_webhook(org.webhook_url, {
                    "aoi_id": aoi_id, "aoi_name": aoi.name,
                    "risk_level": risk_level, "confidence_score": confidence,
                    "carbon_loss_tons": carbon_loss, "fire_detected": fire_detected,
                    "timestamp": datetime.utcnow().isoformat(),
                })

            org_name = org.name if org else "ForestGuard"

            # ── Get officers + global alert number ───────────────────────
            officers = db.query(OfficerContact).filter(
                OfficerContact.org_id    == aoi.org_id,
                OfficerContact.is_active == True,
            ).all()

            # Also always notify the global ALERT_EMAIL / ALERT_PHONE from .env
            global_email = os.environ.get("ALERT_EMAIL", "").strip()
            global_phone = os.environ.get("ALERT_PHONE_NUMBER", "").strip()

            emails_sent = 0
            sms_sent = 0

            # ── Notify each registered officer ───────────────────────────
            for officer in officers:
                wants = (officer.alert_types or "HIGH,MEDIUM").split(",")
                if risk_level not in [w.strip() for w in wants]:
                    continue
                if officer.email:
                    ok = _send_email(
                        to_email=officer.email, officer_name=officer.name,
                        aoi_name=aoi.name, risk_level=risk_level,
                        carbon_loss=carbon_loss, confidence=confidence,
                        fire_detected=fire_detected, org_name=org_name,
                    )
                    if ok:
                        emails_sent += 1
                if officer.phone:
                    ok = _send_sms(
                        to_phone=officer.phone, aoi_name=aoi.name,
                        risk_level=risk_level, carbon_loss=carbon_loss,
                        fire_detected=fire_detected,
                    )
                    if ok:
                        sms_sent += 1

            # ── Notify global email/phone (prototype fallback) ────────────
            # If no officers are registered OR you just want alerts on your
            # own email regardless, set ALERT_EMAIL in .env
            if global_email:
                already_notified = any(
                    o.email == global_email for o in officers if o.email
                )
                if not already_notified:
                    ok = _send_email(
                        to_email=global_email, officer_name="Forest Officer",
                        aoi_name=aoi.name, risk_level=risk_level,
                        carbon_loss=carbon_loss, confidence=confidence,
                        fire_detected=fire_detected, org_name=org_name,
                    )
                    if ok:
                        emails_sent += 1

            if global_phone:
                ok = _send_sms(
                    to_phone=global_phone, aoi_name=aoi.name,
                    risk_level=risk_level, carbon_loss=carbon_loss,
                    fire_detected=fire_detected,
                )
                if ok:
                    sms_sent += 1

            logger.info(
                f"[SCAN] Notifications: {emails_sent} email(s), {sms_sent} SMS "
                f"| '{aoi.name}' [{risk_level}]"
            )
        else:
            logger.info(f"[SCAN] '{aoi.name}' → {risk_level} — no alert needed")

        db.commit()

        result.update({
            "status":        "complete",
            "risk_level":    risk_level,
            "carbon_loss":   round(carbon_loss, 2),
            "confidence":    confidence,
            "fire_detected": fire_detected,
        })
        logger.info(
            f"[SCAN] Done: '{aoi.name}' → {risk_level} | "
            f"CO₂ {carbon_loss:.1f}t | conf {confidence}"
        )
        return result

    except Exception as e:
        db.rollback()
        logger.exception(f"run_scan_now failed for AOI {aoi_id}: {e}")
        result["error"] = str(e)
        return result
    finally:
        db.close()


# ══════════════════════════════════════════════════════════════════════════════
#  HUEY TASK  —  wraps run_scan_now for scheduled/background use
# ══════════════════════════════════════════════════════════════════════════════

@huey.task()
def scan_aoi_background(aoi_id: int):
    """Huey background task — calls run_scan_now() internally."""
    run_scan_now(aoi_id)


# ══════════════════════════════════════════════════════════════════════════════
#  SCHEDULER  —  auto-scan every 5 days
# ══════════════════════════════════════════════════════════════════════════════

@huey.periodic_task(crontab(day="*/5"))
def schedule_aoi_scans():
    db = SessionLocal()
    try:
        aois = db.query(AOI).filter(AOI.is_active == True).all()
        for aoi in aois:
            scan_aoi_background(aoi.id)
        logger.info(f"Scheduled scan enqueued {len(aois)} AOIs")
    finally:
        db.close()


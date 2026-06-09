"""
ForestGuard — Alert Notifications via Email (SMTP) + SMS (Twilio optional)
File: backend/notifications.py

No extra paid libraries needed for email — uses Python's built-in smtplib.
SMS is optional (Twilio). Skip SMS if you only want email.
"""

import smtplib
import logging
import os
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from datetime import datetime

logger = logging.getLogger("forestguard.notifications")


# ─────────────────────────────────────────────────────────────────────────────
# EMAIL ALERT  (uses Python built-in smtplib — no pip install needed)
# ─────────────────────────────────────────────────────────────────────────────

def send_email_alert(
    to_email: str,
    officer_name: str,
    aoi_name: str,
    risk_level: str,
    carbon_loss_tons: float,
    confidence_score: float,
    fire_detected: bool,
    aoi_id: int,
    org_name: str,
    base_url: str = "http://127.0.0.1:8000",
) -> bool:
    """Send a deforestation alert email. Returns True on success."""

    # Read SMTP config from .env (loaded into os.environ by config.py)
    smtp_host = os.environ.get("SMTP_HOST", "smtp.gmail.com")
    smtp_port = int(os.environ.get("SMTP_PORT", "587"))
    smtp_user = os.environ.get("SMTP_USER", "")
    smtp_pass = os.environ.get("SMTP_PASSWORD", "")
    smtp_from = os.environ.get("SMTP_FROM", smtp_user)

    if not smtp_user or not smtp_pass:
        logger.warning("SMTP credentials missing in .env — email not sent")
        return False

    risk_color = {
        "HIGH":   "#dc2626",
        "MEDIUM": "#d97706",
        "LOW":    "#16a34a",
    }.get(risk_level, "#6b7280")

    fire_note  = "🔥 Active fire detected in this area. " if fire_detected else ""
    timestamp  = datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    fire_li    = ("<li>🔥 MODIS FIRMS thermal anomaly — active fire confirmed</li>"
                  if fire_detected else "")

    html_body = f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8"></head>
<body style="margin:0;padding:0;background:#0d1117;font-family:'Segoe UI',Arial,sans-serif;">
<table width="100%" cellpadding="0" cellspacing="0" style="background:#0d1117;padding:20px 0;">
<tr><td align="center">
<table width="600" cellpadding="0" cellspacing="0"
       style="background:#161b22;border-radius:12px;overflow:hidden;border:1px solid #30363d;">

  <!-- Header -->
  <tr><td style="background:#0f4c2a;padding:28px 32px;">
    <span style="color:#10b981;font-size:22px;font-weight:700;letter-spacing:1px;">
      🌿 FORESTGUARD ENTERPRISE
    </span>
    <p style="color:#6ee7b7;margin:6px 0 0;font-size:13px;">
      Intelligent Deforestation Monitoring Platform
    </p>
  </td></tr>

  <!-- Risk Banner -->
  <tr><td style="background:{risk_color};padding:16px 32px;">
    <p style="color:white;margin:0;font-size:18px;font-weight:700;">
      ⚠️ {risk_level} RISK DEFORESTATION ALERT
    </p>
    <p style="color:rgba(255,255,255,0.85);margin:4px 0 0;font-size:13px;">
      {fire_note}Organisation: {org_name} · {timestamp}
    </p>
  </td></tr>

  <!-- Body -->
  <tr><td style="padding:28px 32px;">
    <p style="color:#c9d1d9;font-size:15px;margin:0 0 8px;">Dear {officer_name},</p>
    <p style="color:#8b949e;font-size:14px;line-height:1.6;margin:0 0 24px;">
      ForestGuard's satellite monitoring has detected a
      <strong style="color:{risk_color}">{risk_level} RISK</strong>
      deforestation event. Immediate review is recommended.
    </p>

    <!-- Stats Grid -->
    <table width="100%" cellpadding="0" cellspacing="0" style="margin-bottom:24px;">
      <tr>
        <td width="50%" style="padding-right:8px;">
          <div style="background:#1c2431;border-radius:8px;padding:16px;border-left:3px solid {risk_color};">
            <p style="color:#8b949e;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Area of Interest</p>
            <p style="color:#f1f5f9;font-size:17px;font-weight:600;margin:0;">{aoi_name}</p>
          </div>
        </td>
        <td width="50%" style="padding-left:8px;">
          <div style="background:#1c2431;border-radius:8px;padding:16px;border-left:3px solid #06b6d4;">
            <p style="color:#8b949e;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Risk Level</p>
            <p style="color:{risk_color};font-size:17px;font-weight:700;margin:0;">{risk_level}</p>
          </div>
        </td>
      </tr>
      <tr style="height:12px;"><td colspan="2"></td></tr>
      <tr>
        <td width="50%" style="padding-right:8px;">
          <div style="background:#1c2431;border-radius:8px;padding:16px;border-left:3px solid #10b981;">
            <p style="color:#8b949e;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Estimated CO₂ Loss</p>
            <p style="color:#f1f5f9;font-size:17px;font-weight:600;margin:0;">
              {carbon_loss_tons:,.1f} <span style="font-size:12px;color:#8b949e;">tonnes CO₂eq</span>
            </p>
          </div>
        </td>
        <td width="50%" style="padding-left:8px;">
          <div style="background:#1c2431;border-radius:8px;padding:16px;border-left:3px solid #8b5cf6;">
            <p style="color:#8b949e;font-size:11px;margin:0 0 4px;text-transform:uppercase;letter-spacing:1px;">Confidence Score</p>
            <p style="color:#f1f5f9;font-size:17px;font-weight:600;margin:0;">
              {confidence_score:.0f}<span style="font-size:12px;color:#8b949e;">/100</span>
            </p>
          </div>
        </td>
      </tr>
    </table>

    <!-- Detection Details -->
    <div style="background:#1c2431;border-radius:8px;padding:16px;margin-bottom:24px;">
      <p style="color:#10b981;font-size:13px;font-weight:600;margin:0 0 8px;">📊 Detection methods used:</p>
      <ul style="color:#8b949e;font-size:13px;line-height:1.8;margin:0;padding-left:20px;">
        <li>Landsat 8/9 + Sentinel-2 satellite imagery (2018–present)</li>
        <li>NDVI, EVI, SAVI, NBR, NDWI vegetation indices</li>
        <li>CUSUM + BFAST structural breakpoint change detection</li>
        <li>Isolation Forest ML anomaly detection</li>
        {fire_li}
      </ul>
    </div>

    <!-- Button -->
    <div style="text-align:center;margin-bottom:24px;">
      <a href="{base_url}"
         style="display:inline-block;background:#10b981;color:white;
                text-decoration:none;padding:13px 32px;border-radius:8px;
                font-weight:600;font-size:15px;">
        View Full Analysis Dashboard →
      </a>
    </div>

    <p style="color:#6b7280;font-size:12px;line-height:1.6;margin:0;">
      Generated automatically by ForestGuard Enterprise using IPCC Tier 1
      carbon methodology and Google Earth Engine satellite data.
    </p>
  </td></tr>

  <!-- Footer -->
  <tr><td style="background:#0d1117;padding:16px 32px;border-top:1px solid #21262d;">
    <p style="color:#484f58;font-size:12px;margin:0;text-align:center;">
      ForestGuard Enterprise · Powered by Google Earth Engine + Gemini AI
    </p>
  </td></tr>

</table>
</td></tr>
</table>
</body></html>"""

    fire_line = "FIRE DETECTED.\n" if fire_detected else ""

    plain_body = (
        f"ForestGuard — {risk_level} RISK ALERT\n\n"
        f"Dear {officer_name},\n\n"
        f"Area: {aoi_name}\nRisk: {risk_level}\n"
        f"CO₂ Loss: {carbon_loss_tons:,.1f} tonnes\n"
        f"Confidence: {confidence_score:.0f}/100\n"
      f"{fire_line}"
        f"Time: {timestamp}\n\n"
        f"Dashboard: {base_url}\n— ForestGuard Enterprise"
    )

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"🌿 ForestGuard {risk_level} Alert: {aoi_name}"
    msg["From"]    = smtp_from
    msg["To"]      = to_email
    msg.attach(MIMEText(plain_body, "plain"))
    msg.attach(MIMEText(html_body, "html"))

    try:
        with smtplib.SMTP(smtp_host, smtp_port, timeout=15) as smtp:
            smtp.ehlo()
            smtp.starttls()
            smtp.login(smtp_user, smtp_pass)
            smtp.sendmail(smtp_from, to_email, msg.as_string())
        logger.info(f"Email sent → {to_email} | AOI: {aoi_name} | {risk_level}")
        return True
    except smtplib.SMTPAuthenticationError:
        logger.error(
            "SMTP auth failed — if using Gmail, use an App Password, "
            "not your regular password. See SETUP_GUIDE.md Step 3."
        )
        return False
    except Exception as e:
        logger.error(f"Email failed → {to_email}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# SMS ALERT via Twilio (optional — skip if you only need email)
# ─────────────────────────────────────────────────────────────────────────────

def send_sms_alert(
    to_phone: str,       # E.164 format: "+911234567890"
    aoi_name: str,
    risk_level: str,
    carbon_loss_tons: float,
    fire_detected: bool,
) -> bool:
    """Send SMS via Twilio. Requires: pip install twilio"""
    account_sid = os.environ.get("TWILIO_ACCOUNT_SID", "")
    auth_token  = os.environ.get("TWILIO_AUTH_TOKEN", "")
    from_number = os.environ.get("TWILIO_FROM_NUMBER", "")

    if not all([account_sid, auth_token, from_number]):
        logger.warning("Twilio env vars not set — SMS skipped")
        return False

    fire_txt = " 🔥 FIRE DETECTED." if fire_detected else ""
    body = (
        f"🌿 FORESTGUARD ALERT\n"
        f"Area: {aoi_name}\n"
        f"Risk: {risk_level}{fire_txt}\n"
        f"CO₂: {carbon_loss_tons:,.0f} t CO₂eq\n"
        f"Login to ForestGuard for full report."
    )

    try:
        from twilio.rest import Client
        Client(account_sid, auth_token).messages.create(
            body=body, from_=from_number, to=to_phone
        )
        logger.info(f"SMS sent → {to_phone}")
        return True
    except ImportError:
        logger.warning("Twilio not installed. Run: pip install twilio")
        return False
    except Exception as e:
        logger.error(f"SMS failed → {to_phone}: {e}")
        return False


# ─────────────────────────────────────────────────────────────────────────────
# UNIFIED DISPATCHER — called from tasks.py after a scan
# ─────────────────────────────────────────────────────────────────────────────

def send_alert_notifications(
    officers: list[dict],
    aoi_name: str,
    risk_level: str,
    carbon_loss_tons: float,
    confidence_score: float,
    fire_detected: bool,
    aoi_id: int,
    org_name: str,
    base_url: str = "http://127.0.0.1:8000",
) -> dict:
    """
    Dispatch alerts to all officers subscribed to this risk level.
    Returns {"emails_sent": N, "sms_sent": M}.

    officers format:
      [{"name":"Ravi","email":"ravi@gov.in","phone":"+910000","alert_types":["HIGH","MEDIUM"]}]
    """
    emails_sent = 0
    sms_sent    = 0

    for officer in officers:
        # Check if this officer subscribed to this risk level
        if risk_level not in officer.get("alert_types", ["HIGH", "MEDIUM"]):
            continue

        if officer.get("email"):
            ok = send_email_alert(
                to_email=officer["email"],
                officer_name=officer.get("name", "Officer"),
                aoi_name=aoi_name,
                risk_level=risk_level,
                carbon_loss_tons=carbon_loss_tons,
                confidence_score=confidence_score,
                fire_detected=fire_detected,
                aoi_id=aoi_id,
                org_name=org_name,
                base_url=base_url,
            )
            if ok:
                emails_sent += 1

        if officer.get("phone"):
            ok = send_sms_alert(
                to_phone=officer["phone"],
                aoi_name=aoi_name,
                risk_level=risk_level,
                carbon_loss_tons=carbon_loss_tons or 0,
                fire_detected=fire_detected,
            )
            if ok:
                sms_sent += 1

    logger.info(
        f"Notifications done: {emails_sent} emails + {sms_sent} SMS "
        f"| AOI={aoi_name} | {risk_level}"
    )
    return {"emails_sent": emails_sent, "sms_sent": sms_sent}

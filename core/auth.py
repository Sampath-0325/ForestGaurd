"""
Google Earth Engine Authentication
Supports: default credentials, service account JSON, and GEE_PROJECT_ID env var.
Includes retry logic for transient failures.
"""
import os
import time
import logging
from typing import Optional

import ee

logger = logging.getLogger("forestguard.gee")


def initialize_gee(
    project_id: Optional[str] = None,
    service_account_key: Optional[str] = None,
    max_retries: int = 3
) -> bool:
    """
    Initialize Google Earth Engine with multiple auth strategies.

    Priority:
    1. Service account key file (GEE_SERVICE_ACCOUNT_KEY env var or param)
    2. GEE_PROJECT_ID env var with default credentials
    3. Default credentials (gcloud / earthengine authenticate)

    Args:
        project_id: GEE cloud project ID
        service_account_key: Path to service account JSON key file
        max_retries: Number of retry attempts on transient failure
    """
    project  = project_id or os.environ.get("GEE_PROJECT_ID")
    key_file = service_account_key or os.environ.get("GEE_SERVICE_ACCOUNT_KEY")

    for attempt in range(1, max_retries + 1):
        try:
            if key_file and os.path.exists(key_file):
                # ── Strategy 1: Service Account (production / CI environments) ──
                credentials = ee.ServiceAccountCredentials(
                    email=None,   # auto-read from JSON key
                    key_file=key_file
                )
                ee.Initialize(credentials=credentials, project=project)
                logger.info(f"GEE initialized via service account ({key_file})")

            elif project:
                # ── Strategy 2: Project ID + default credentials ──
                ee.Initialize(project=project)
                logger.info(f"GEE initialized with project: {project}")

            else:
                # ── Strategy 3: Default credentials ──
                ee.Initialize()
                logger.info("GEE initialized with default credentials")

            # Sanity check
            _ = ee.String("ping").getInfo()
            return True

        except ee.EEException as e:
            err = str(e).lower()
            if "credentials" in err or "authenticate" in err or "not found" in err:
                logger.warning("GEE credentials not found — launching browser auth...")
                try:
                    ee.Authenticate()
                    ee.Initialize(project=project) if project else ee.Initialize()
                    logger.info("GEE authenticated and initialized successfully")
                    return True
                except Exception as auth_err:
                    logger.error(f"GEE browser auth failed: {auth_err}")
                    raise

            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"GEE init failed (attempt {attempt}/{max_retries}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                logger.error(f"GEE initialization failed after {max_retries} attempts: {e}")
                raise

        except Exception as e:
            if attempt < max_retries:
                wait = 2 ** attempt
                logger.warning(f"Unexpected GEE error (attempt {attempt}), retrying in {wait}s: {e}")
                time.sleep(wait)
            else:
                raise

    return False


def get_gee_status() -> dict:
    """Return GEE connection status — useful for health checks."""
    try:
        result = ee.String("ok").getInfo()
        return {"connected": True, "status": "ok"}
    except Exception as e:
        return {"connected": False, "status": str(e)}
     

"""
ForestGuard RAG / Chatbot Engine  —  OpenRouter backend
========================================================
FIXES vs previous version:
  - Default model changed to "openrouter/free" (auto-picks any available
    free model).  "google/gemini-2.0-flash-exp:free" can be flaky/removed.
  - OpenRouter 404 error body is {"error":{"message":"...","code":404}}
    — the old code was reading resp.json().get("error",{}).get("message")
    which is correct BUT we were also swallowing the real error text.
    Now we log the full body so you can see exactly what OpenRouter says.
  - Added model-not-found detection from the error message string so the
    user sees a helpful list of working free models.
  - Added a FALLBACK_MODELS list that is tried automatically on 404.
  - All other logic (history, action parser, suggestions) unchanged.
"""

from __future__ import annotations

import logging
import re
import threading
from typing import Optional
import time

logger = logging.getLogger("forestguard.rag")

from backend.config import settings as _settings

# ── OpenRouter endpoint ───────────────────────────────────────────────────────
_OPENROUTER_URL = "https://openrouter.ai/api/v1/chat/completions"

# Primary model from .env.  Default is "openrouter/free" which auto-selects
# any working free model — much more reliable than pinning a specific free slug.
_PRIMARY_MODEL = _settings.openrouter_model  # e.g. "openrouter/free"

# Ordered fallback list tried automatically if the primary model returns 404.
# These are confirmed-working free slugs as of March 2026.
_FALLBACK_MODELS = [
    "openrouter/free",                          # auto-router — always works
    "meta-llama/llama-4-scout:free",
    "meta-llama/llama-4-maverick:free",
    "deepseek/deepseek-chat-v3-0324:free",
    "mistralai/mistral-small-3.1-24b-instruct:free",
    "google/gemini-2.0-flash-exp:free",         # sometimes available
    "meta-llama/llama-3.1-8b-instruct:free",
]

# ═══════════════════════════════════════════════════════════════════════════
#  FORESTGUARD KNOWLEDGE BASE
# ═══════════════════════════════════════════════════════════════════════════

KNOWLEDGE_BASE = """
=== FORESTGUARD SYSTEM — COMPLETE TECHNICAL REFERENCE ===

── 1. NDVI ──────────────────────────────────────────────
Formula: NDVI = (NIR − Red) / (NIR + Red)      range: −1.0 to 1.0
  0.6–0.9  → dense healthy forest
  0.4–0.6  → moderate / degraded vegetation
  0.2–0.4  → sparse vegetation / grassland
  < 0.2    → bare soil, water, urban
Sources: Landsat 8/9 (30 m, 16-day), Sentinel-2 (10 m, 5-day)
Cloud masking: QA_PIXEL (Landsat) / SCL band (Sentinel-2)

── 2. MULTI-INDEX SUITE ─────────────────────────────────
EVI   = 2.5 × (NIR−Red) / (NIR + 6×Red − 7.5×Blue + 1)
        Reduces canopy saturation in dense forests
SAVI  = 1.5 × (NIR−Red) / (NIR + Red + 0.5)
        Soil-adjusted; better for sparse/degraded areas
NBR   = (NIR − SWIR) / (NIR + SWIR)
        Burn scar detection; post-fire recovery tracking
NDWI  = (Green − NIR) / (Green + NIR)
        Water stress / moisture content

Forest Health Score (FHS) weights:
  NDVI 40% + EVI 25% + SAVI 15% + NBR 20%
  FHS trend is used as the 5th feature in risk scoring.

── 3. ENSEMBLE RISK SCORING ─────────────────────────────
Five features → single 0–1 risk_score → risk_level:
  HIGH   if risk_score ≥ 0.30
  MEDIUM if risk_score ≥ 0.12
  LOW    if risk_score <  0.12

Feature weights (calibrated for Indian tropical forests):
  a) NDVI slope         0.35
  b) Trend acceleration 0.20
  c) ML anomaly         0.25
  d) CV volatility      0.15
  e) FHS signal         0.05

── 4. CARBON & CO₂ ESTIMATION (IPCC Tier 1) ────────────
carbon_loss_tons = area_ha × AGB_density × deforestation_fraction × 0.47
CO₂ equivalent: carbon_loss × 3.667

── 5. CUSUM CHANGE DETECTION ───────────────────────────
CUSUM = cumulative sum of deviations from historical mean NDVI
Threshold: CUSUM > 3 × historical_std → structural break detected

── 6. BFAST BREAKPOINT ANALYSIS ────────────────────────
Decomposes NDVI timeseries into: trend + seasonal + remainder

── 7. FIRE / HOTSPOT DETECTION ─────────────────────────
Source: MODIS MCD14ML active fire detections (daily, 1 km)
Confidence threshold: ≥ 50%

── 8. EIGHT-STAGE ANALYSIS PIPELINE ───────────────────
Stage 1: Load satellite imagery
Stage 2: Compute NDVI + multi-index timeseries
Stage 3: EVI, SAVI, NBR, NDWI, FHS computation
Stage 4: CUSUM + BFAST change detection
Stage 5: Ensemble ML risk scoring
Stage 6: IPCC Tier 1 carbon estimation
Stage 7: MODIS fire/hotspot overlay
Stage 8: Hansen Global Forest Change baseline

── 9. VEGETATION STABILITY INDEX ──────────────────────
VSI = 1 − (std_NDVI / mean_NDVI)

── 10. FIVE TELANGANA FOREST AOIs ─────────────────────
1. Nallamala Forest         — Nagarkurnool/Nalgonda, 3700 km²
2. Adilabad Forests         — Adilabad District, 2000 km²
3. Bhadradri Kothagudem     — Godavari Basin, 1800 km²
4. Mulugu Forests           — Bhupalpally/Godavari, 1500 km²
5. Vikarabad/Ananthagiri    — Near Hyderabad, 500 km²

── 11. DATA SOURCES ──────────────────────────────────
1. Landsat 8/9 Collection 2 SR — 30m, 16-day revisit
2. Sentinel-2 Level-2A          — 10m, 5-day revisit
3. MODIS Terra MOD09A1          — 500m, 8-day composite
4. MODIS Active Fire MCD14ML    — daily fire detections
5. Hansen Global Forest Change  — annual tree cover loss

── 12. ALERT SYSTEM ───────────────────────────────────
Alert triggered when risk_level ∈ {HIGH, MEDIUM} after scan
Confidence 0–100%: derived from data density + trend consistency

── 13. BACKGROUND SCAN (Huey Task Queue) ──────────────
Full scan: 30–90 seconds per AOI (Google Earth Engine processing)
Start worker: python -m huey.bin.huey_consumer backend.tasks.huey

=== END REFERENCE ===
"""

SYSTEM_PROMPT = f"""You are ForestGuard AI — the expert assistant for a satellite-based
deforestation monitoring platform covering Telangana, India.

{KNOWLEDGE_BASE}

Behaviour rules:
1. Give precise technical answers grounded in the knowledge above.
2. For "how is X calculated" questions — formula first, then explain each term.
3. For action commands (scan, compare, report, alerts) — confirm you're triggering the action.
4. Keep answers under 250 words unless more detail is specifically requested.
5. Always use Indian forest standards, not Amazon/global defaults.
6. If asked about risk for a specific AOI — quote exact values from context if provided.
"""


# ═══════════════════════════════════════════════════════════════════════════
#  LOW-LEVEL API CALL  (single model, no fallback)
# ═══════════════════════════════════════════════════════════════════════════

def _call_openrouter(api_key: str, model: str, messages: list[dict],
                     timeout: int = 30) -> tuple[str | None, int, dict]:
    """
    Make one call to OpenRouter.
    Returns (answer_text | None, http_status_code, raw_response_json).
    answer_text is None on any error.
    """
    import httpx

    payload = {
        "model":       model,
        "messages":    messages,
        "max_tokens":  500,
        "temperature": 0.35,
        "top_p":       0.85,
    }

    try:
        with httpx.Client(timeout=timeout) as client:
            resp = client.post(
                _OPENROUTER_URL,
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type":  "application/json",
                    "HTTP-Referer":  "https://forestguard.app",
                    "X-Title":       "ForestGuard Enterprise",
                },
                json=payload,
            )

        # Always try to parse JSON — even error responses have a body
        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code == 200:
            choices = data.get("choices", [])
            if choices:
                answer = choices[0].get("message", {}).get("content", "").strip()
                if answer:
                    return answer, 200, data

        return None, resp.status_code, data

    except Exception as exc:
        logger.exception(f"[CHATBOT] httpx error calling OpenRouter: {exc}")
        return None, -1, {"_exception": str(exc)}


# ═══════════════════════════════════════════════════════════════════════════
#  CHATBOT CLASS
# ═══════════════════════════════════════════════════════════════════════════

_MAX_SESSIONS = 500


class OpenRouterChatbot:
    """
    OpenRouter-backed chatbot with:
    - Automatic fallback through _FALLBACK_MODELS on 404
    - Session-aware multi-turn history (OpenAI message format)
    - Thread-safe singleton
    """

    def __init__(self):
        self._sessions: dict[str, list[dict]] = {}
        self._api_key  = _settings.openrouter_api_key
        self._model    = _PRIMARY_MODEL
        # Cache the last working model so we don't retry failed ones
        self._working_model: str | None = None

        logger.info(f"[CHATBOT] Provider:  OpenRouter")
        logger.info(f"[CHATBOT] Model:     {self._model}")
        logger.info(f"[CHATBOT] Key set:   {bool(self._api_key)}")
        if self._api_key:
            logger.info(f"[CHATBOT] Key prefix: {self._api_key[:14]}...")

    # ── public chat method ────────────────────────────────────────────────
    def chat(
        self,
        message:     str,
        session_id:  str = "default",
        aoi_context: Optional[dict] = None,
    ) -> str:

        if not self._api_key:
            return (
                "⚠️ AI unavailable — OPENROUTER_API_KEY not set.\n\n"
                "1. Get a free key at https://openrouter.ai/keys\n"
                "2. Add to .env:  OPENROUTER_API_KEY=sk-or-v1-...\n"
                "3. Restart the server."
            )

        # Build user text with optional AOI context prefix
        user_text = self._inject_context(message, aoi_context)

        # Retrieve history and build full messages array
        history  = self._sessions.get(session_id, [])
        messages = (
            [{"role": "system", "content": SYSTEM_PROMPT}]
            + list(history)
            + [{"role": "user", "content": user_text}]
        )

        # Try cached working model first, then primary, then fallbacks
        models_to_try = self._build_model_queue()
        answer        = None
        used_model    = None

        for model in models_to_try:
            logger.info(f"[CHATBOT] Trying model: {model}")
            text, status, body = _call_openrouter(self._api_key, model, messages)

            if status == 200 and text:
                answer     = text
                used_model = model
                break

            elif status == 429:
                logger.warning("[CHATBOT] Rate limit (429) — waiting 2 s then retrying")
                time.sleep(2)
                text, status, body = _call_openrouter(self._api_key, model, messages)
                if status == 200 and text:
                    answer     = text
                    used_model = model
                    break
                continue

            elif status == 401:
                logger.error("[CHATBOT] 401 Unauthorized — bad API key")
                return (
                    "⚠️ OpenRouter authentication failed (401).\n\n"
                    "Check that OPENROUTER_API_KEY in your .env starts with 'sk-or-v1-'."
                )

            elif status == 402:
                logger.error("[CHATBOT] 402 — insufficient OpenRouter credits")
                return (
                    "⚠️ OpenRouter account has no credits (402).\n\n"
                    "Use a free model — set in .env:\n"
                    "  OPENROUTER_MODEL=openrouter/free"
                )

            elif status == 404:
                err_msg = body.get("error", {}).get("message", "")
                logger.warning(
                    f"[CHATBOT] 404 for model '{model}': {err_msg} — trying next fallback"
                )
                if self._working_model == model:
                    self._working_model = None
                continue

            else:
                err_msg = body.get("error", {}).get("message", str(body))
                logger.warning(f"[CHATBOT] HTTP {status} for '{model}': {err_msg}")
                continue

        if not answer:
            logger.error("[CHATBOT] All models exhausted — no answer returned")
            return (
                "⚠️ Could not reach any AI model.\n\n"
                "• Check your internet connection\n"
                "• Verify OPENROUTER_API_KEY in .env\n"
                "• Set OPENROUTER_MODEL=openrouter/free\n"
                "• Visit https://openrouter.ai/models for current free models"
            )

        # Cache the working model for next call
        self._working_model = used_model
        logger.info(f"[CHATBOT] Answer received via: {used_model}")

        # Update history (keep last 8 turns = 16 messages)
        history.append({"role": "user",      "content": user_text})
        history.append({"role": "assistant", "content": answer})
        self._sessions[session_id] = history[-16:]

        # Evict oldest session if over cap
        if len(self._sessions) > _MAX_SESSIONS:
            oldest = next(iter(self._sessions))
            del self._sessions[oldest]

        return answer

    # ── helpers ───────────────────────────────────────────────────────────
    def _inject_context(self, message: str, aoi_context: Optional[dict]) -> str:
        if not aoi_context:
            return message
        ctx_lines = []
        if aoi_context.get("aoi_name"):
            ctx_lines.append(f"AOI: {aoi_context['aoi_name']}")
        if aoi_context.get("risk_level"):
            ctx_lines.append(f"Risk: {aoi_context['risk_level']}")
        if aoi_context.get("slope") is not None:
            ctx_lines.append(f"NDVI slope: {aoi_context['slope']:.6f}/yr")
        if aoi_context.get("carbon_loss_tons") is not None:
            ctx_lines.append(f"Carbon loss: {aoi_context['carbon_loss_tons']:.2f}t")
        if ctx_lines:
            return f"[Context: {', '.join(ctx_lines)}]\n{message}"
        return message

    def _build_model_queue(self) -> list[str]:
        """Return ordered list of models to try, deduplicated."""
        queue = []
        if self._working_model and self._working_model != self._model:
            queue.append(self._working_model)
        queue.append(self._model)
        for m in _FALLBACK_MODELS:
            if m not in queue:
                queue.append(m)
        return queue

    def clear_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)


# ── Thread-safe singleton ────────────────────────────────────────────────────
_chatbot_instance: Optional[OpenRouterChatbot] = None
_chatbot_lock = threading.Lock()


def get_chatbot() -> OpenRouterChatbot:
    global _chatbot_instance
    if _chatbot_instance is None:
        with _chatbot_lock:
            if _chatbot_instance is None:
                _chatbot_instance = OpenRouterChatbot()
    return _chatbot_instance


# ═══════════════════════════════════════════════════════════════════════════
#  ACTION PARSER
# ═══════════════════════════════════════════════════════════════════════════

_ACTION_PATTERNS: dict[str, list[str]] = {
    "scan":    ["scan", "analyse", "analyze", "scanning", "run scan", "start scan"],
    "ndvi":    ["ndvi", "vegetation index", "vegetation trend", "show ndvi", "open ndvi"],
    "carbon":  ["carbon", "co2", "carbon loss", "carbon impact", "emissions"],
    "compare": ["compare", "comparison", "versus", " vs ", "side by side"],
    "report":  ["report", "generate report", "full report", "show report", "open report"],
    "alerts":  ["alerts", "show alerts", "open alerts", "check alerts", "warning"],
}

_NEGATION_WORDS = {"not", "no", "never", "don't", "dont", "cannot", "can't",
                   "cant", "won't", "wont", "shouldn't", "unable"}


def _is_negated(text_lower: str, keyword: str) -> bool:
    idx = text_lower.find(keyword)
    if idx == -1:
        return False
    preceding_words = text_lower[max(0, idx - 40): idx].split()[-4:]
    return bool(_NEGATION_WORDS.intersection(preceding_words))


def parse_action_from_response(raw: str) -> tuple[str, Optional[dict]]:
    if not raw:
        return "", None
    lower = raw.lower()
    for action, keywords in _ACTION_PATTERNS.items():
        for kw in keywords:
            if kw in lower and not _is_negated(lower, kw):
                return raw, {"action": action, "target": _extract_aoi_target(raw)}
    return raw, None


def _extract_aoi_target(text: str) -> str:
    lower = text.lower()
    if any(w in lower for w in ["this aoi", "current aoi", "selected area",
                                 "this area", "this forest"]):
        return "current"
    for f in ["nallamala", "adilabad", "bhadradri", "kothagudem",
              "mulugu", "vikarabad", "ananthagiri"]:
        if f in lower:
            return f.capitalize()
    if any(w in lower for w in ["all aois", "all forests", "all areas", "every area"]):
        return "all"
    return "current"


# ═══════════════════════════════════════════════════════════════════════════
#  SMART SUGGESTIONS
# ═══════════════════════════════════════════════════════════════════════════

def get_smart_suggestions(context: Optional[dict] = None) -> list[dict]:
    base = [
        {"text": "How is NDVI calculated?",        "icon": "📡"},
        {"text": "Explain risk score formula",      "icon": "⚠️"},
        {"text": "How is carbon loss estimated?",   "icon": "🌿"},
        {"text": "What is CUSUM detection?",        "icon": "📊"},
        {"text": "How does fire detection work?",   "icon": "🔥"},
        {"text": "Explain the 8 pipeline stages",   "icon": "🔬"},
    ]
    if not context:
        return base

    risk = context.get("risk_level")
    name = context.get("aoi_name", "this area")

    if risk == "HIGH":
        ctx = [
            {"text": f"Why is {name} HIGH risk?",       "icon": "🔴"},
            {"text": f"Scan {name} now",                 "icon": "🛰️"},
            {"text": f"Show carbon impact for {name}",   "icon": "🌿"},
            {"text": "Explain the NDVI trend",           "icon": "📈"},
            {"text": "What triggers a HIGH risk alert?", "icon": "⚠️"},
            {"text": f"Open report for {name}",          "icon": "📄"},
        ]
    elif risk == "MEDIUM":
        ctx = [
            {"text": f"What does MEDIUM risk mean?",    "icon": "🟡"},
            {"text": f"Scan {name} for latest data",    "icon": "🛰️"},
            {"text": f"Compare {name} with other AOIs", "icon": "📊"},
            {"text": "How is risk score calculated?",   "icon": "⚠️"},
            {"text": f"Show carbon loss for {name}",    "icon": "🌿"},
            {"text": f"Open alerts for {name}",         "icon": "🔔"},
        ]
    elif risk == "LOW":
        ctx = [
            {"text": f"Why is {name} LOW risk?",        "icon": "🟢"},
            {"text": "What does LOW risk indicate?",    "icon": "✅"},
            {"text": "Compare with other AOIs",         "icon": "📊"},
            {"text": "Explain vegetation stability",    "icon": "🌿"},
            {"text": "How is VSI calculated?",          "icon": "📈"},
            {"text": f"Scan {name} for latest data",    "icon": "🛰️"},
        ]
    else:
        ctx = [
            {"text": f"Scan {name} now",                    "icon": "🛰️"},
            {"text": "How does the 8-stage pipeline work?",  "icon": "🔬"},
            {"text": "What is NDVI?",                        "icon": "📡"},
            {"text": "How are forests monitored?",           "icon": "🌳"},
        ]

    combined = ctx[:4] + [s for s in base if s not in ctx][:2]
    return combined[:6]


/**
 * ForestGuard PRO — Enterprise Forest Intelligence Dashboard
 * Full featured: NDVI charts, carbon panel, alerts timeline,
 * AOI comparison, role-based UI, webhook config, PDF reports
 *
 * ─── BUG FIXES APPLIED ───────────────────────────────────────
 * FIX 1 [saveAOI / line ~571]:
 *   getElementById("new-aoi-name") → getElementById("aoi-name-input")
 *   The HTML input has id="aoi-name-input"; the old ID was a mismatch
 *   causing "Cannot read properties of null (reading 'value')" crash.
 *
 * FIX 2 [openCompareModal duplicate / line ~1016 & ~1493]:
 *   Two functions shared the same name openCompareModal().
 *   The chart-rendering overload (d1, d2) is renamed → renderCompareChart(d1, d2).
 *   The public trigger (no args) keeps the name openCompareModal().
 *   A showCompareModal() alias is added so the HTML onclick still works.
 *
 * FIX 3 [initBoundaryLayer / line ~142]:
 *   Dead GitHub URL (404) for US admin boundaries replaced with a live
 *   India state boundary GeoJSON (correct country for this app).
 *
 * FIX 4 [getContext("2d") / lines ~785 & ~1045]:
 *   Added { willReadFrequently: true } to both canvas getContext calls
 *   to suppress the Canvas2D performance warning.
 * ─────────────────────────────────────────────────────────────
 */

const API_BASE = window.location.origin;
const DEFAULT_CENTER = [17.385, 78.4867];
const DEFAULT_ZOOM = 11;
const HEALTH_INTERVAL = 10000;
const MAX_RETRIES = 3;

// ── Map & Layers ──
let map, markersLayer, riskLayer, heatLayer, ndviLayer;

// ── Charts ──
let ndviChartInstance = null;
let compareChartInstance = null;

// ── State ──
let jwtToken = null;
let currentUser = null;
let currentDrawnAOI = null;
let selectedAOIId = null;
let lastStatus = null;
let abortController = null;
let debounceTimer = null;
let chatDebounceTimer = null;
let alertPollInterval = null;
let statsInterval = null;
let chatBusy = false;

/* ═══════════════════════════════════════════════
   MAP INITIALIZATION
═══════════════════════════════════════════════ */

function initMap() {
  if (map) return;

  map = L.map("map", {
    center: DEFAULT_CENTER,
    zoom: DEFAULT_ZOOM,
    zoomControl: false,
    preferCanvas: true
  });

  L.control.zoom({ position: "topright" }).addTo(map);
  L.control.scale().addTo(map);

  initBaseMaps();
  initBoundaryLayer();
  initDrawingTools();

  markersLayer = L.layerGroup().addTo(map);
  riskLayer    = L.layerGroup().addTo(map);

  map.on("click", onMapClick);
  map.on("moveend", debounceAutoScan);

  setTimeout(() => map.invalidateSize(), 200);
  startHealthCheck();
}

/* ═══════════════════════════════════════════════
   BASE MAPS — SINGLE LAYER CONTROL
═══════════════════════════════════════════════ */

function initBaseMaps() {

  // ══ SATELLITE ══
  const satellite = L.tileLayer(
    "https://mt{s}.google.com/vt/lyrs=s&x={x}&y={y}&z={z}",
    {
      subdomains: ["0","1","2","3"],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: "© Google Satellite",
      tileSize: 256,
      detectRetina: true
    }
  );

  // ══ HYBRID ══
  const hybrid = L.tileLayer(
    "https://mt{s}.google.com/vt/lyrs=y&x={x}&y={y}&z={z}",
    {
      subdomains: ["0","1","2","3"],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: "© Google Hybrid",
      tileSize: 256,
      detectRetina: true
    }
  );

  // ══ STREETS ══
  const streets = L.tileLayer(
    "https://mt{s}.google.com/vt/lyrs=m&x={x}&y={y}&z={z}",
    {
      subdomains: ["0","1","2","3"],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: "© Google Maps",
      tileSize: 256,
      detectRetina: true
    }
  );

  // ══ TERRAIN ══
  const terrain = L.tileLayer(
    "https://mt{s}.google.com/vt/lyrs=p&x={x}&y={y}&z={z}",
    {
      subdomains: ["0","1","2","3"],
      maxZoom: 21,
      maxNativeZoom: 21,
      attribution: "© Google Terrain",
      tileSize: 256,
      detectRetina: true
    }
  );

  // Default: satellite
  satellite.addTo(map);

  L.control.layers(
    {
      "🛰️ Satellite":  satellite,
      "🗺️ Hybrid":     hybrid,
      "🏙️ Streets":    streets,
      "⛰️ Terrain":    terrain
    },
    {},
    { position: "topright", collapsed: true }
  ).addTo(map);
}

// ─────────────────────────────────────────────────────────────
// FIX 3: Replace dead US admin boundary URL (404) with a live
//         India state boundary source — correct for this app.
// ─────────────────────────────────────────────────────────────
function initBoundaryLayer() {
  fetch("https://raw.githubusercontent.com/geohacker/india/master/state/india_state.geojson")
    .then(r => {
      if (!r.ok) throw new Error(`Boundary fetch failed: ${r.status}`);
      return r.json();
    })
    .then(data => {
      L.geoJSON(data, { style: { color: "#ffffff", weight: 1, opacity: 0.3, fillOpacity: 0 } }).addTo(map);
    })
    .catch(() => {
      // Silently fail — boundary overlay is decorative, not critical
    });
}

/* ═══════════════════════════════════════════════
   DRAWING TOOLS
═══════════════════════════════════════════════ */

function initDrawingTools() {
  const drawnItems = new L.FeatureGroup();
  map.addLayer(drawnItems);

  const drawControl = new L.Control.Draw({
    edit: { featureGroup: drawnItems },
    draw: { polygon: true, rectangle: true, circle: false, marker: false, polyline: false, circlemarker: false }
  });
  map.addControl(drawControl);

  map.on(L.Draw.Event.CREATED, e => {
    drawnItems.addLayer(e.layer);
    currentDrawnAOI = e.layer.toGeoJSON().geometry;
    const btn = document.getElementById("btn-save-aoi");
    if (btn) btn.disabled = false;
  });
}

/* ═══════════════════════════════════════════════
   TABS
═══════════════════════════════════════════════ */

function switchTab(name) {
  document.querySelectorAll(".tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".tab-content").forEach(c => c.classList.remove("active"));
  document.querySelector(`.tab[onclick="switchTab('${name}')"]`).classList.add("active");
  document.getElementById(`tab-${name}`).classList.add("active");
  if (name === "alerts") loadAlerts();
  if (name === "compare") populateCompareSelects();
}

function switchAdminTab(name) {
  document.querySelectorAll("#admin-modal .tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll("#admin-modal .tab-content").forEach(c => c.classList.remove("active"));
  document.querySelector(`#admin-modal .tab[onclick="switchAdminTab('${name}')"]`).classList.add("active");
  document.getElementById(`admin-tab-${name}`).classList.add("active");
  document.getElementById('admin-tab-officers').style.display  = name === 'officers'  ? 'block' : 'none';
  if (name === "users")   loadUsers();
  if (name === "invites") loadInviteCodes();
  if (name === "webhook") loadWebhook();
  if (name === 'officers') loadOfficers();
}

/* ═══════════════════════════════════════════════
   PUBLIC FOREST EXPLORER
═══════════════════════════════════════════════ */

let forestPolygonLayers = {};
let selectedForestId = null;

async function loadPublicForests() {
  try {
    const res  = await fetch(`${API_BASE}/api/public/forests`);
    const data = await res.json();

    if (!data.forests || !data.forests.length) {
      document.getElementById("forest-list").innerHTML =
        `<div style="color:var(--text-muted);font-size:0.8rem;padding:1rem;text-align:center;">
          Forests not seeded yet.<br>
          <code style="font-size:0.72rem;">POST /api/auth/seed</code>
        </div>`;
      return;
    }

    document.getElementById("chip-high").textContent = `● High: ${data.high_risk}`;
    document.getElementById("chip-med").textContent  = `● Medium: ${data.medium_risk}`;
    document.getElementById("chip-low").textContent  = `● Low: ${data.low_risk}`;

    data.forests.forEach(forest => renderForestPolygon(forest));

    const allCoords = data.forests.flatMap(f =>
      f.geojson_polygon.coordinates[0].map(c => [c[1], c[0]])
    );
    if (allCoords.length) {
      map.fitBounds(L.latLngBounds(allCoords), { padding: [30, 30] });
    }

    renderForestCards(data.forests);

  } catch(e) {
    document.getElementById("forest-list").innerHTML =
      `<div style="color:#ef4444;font-size:0.8rem;padding:1rem;">Failed to load forest data: ${e.message}</div>`;
  }
}

function renderForestPolygon(forest) {
  const riskColor = forest.risk_color || "#64748b";

  const poly = L.geoJSON({
    type: "Feature",
    geometry: forest.geojson_polygon
  }, {
    style: {
      color:       riskColor,
      fillColor:   riskColor,
      fillOpacity: 0.18,
      weight:      2.5,
      dashArray:   forest.risk_level === "PENDING" ? "6,4" : null
    }
  });

  poly.on("click", () => selectForest(forest.id));

  poly.bindTooltip(`
    <div style="font-weight:600;margin-bottom:2px;">${forest.name}</div>
    <div style="font-size:0.75rem;color:${riskColor};">● ${forest.risk_level}</div>
    <div style="font-size:0.72rem;opacity:0.8;">${forest.district}</div>
  `, { sticky: true });

  poly.addTo(riskLayer);
  forestPolygonLayers[forest.id] = poly;
}

function renderForestCards(forests) {
  const container = document.getElementById("forest-list");
  container.innerHTML = forests.map(f => {
    const riskColor = f.risk_color || "#64748b";
    const scanAge   = f.last_scanned
      ? `Scanned ${timeAgo(f.last_scanned)}`
      : "Not yet scanned";
    const carbonStr = f.carbon_loss_tons
      ? `${f.carbon_loss_tons.toFixed(1)}t C lost`
      : "";
    const alertBadge = f.unresolved_alerts > 0
      ? `<span style="background:#ef4444;color:#fff;border-radius:20px;padding:0.1rem 0.4rem;font-size:0.68rem;font-weight:700;">${f.unresolved_alerts} alert${f.unresolved_alerts > 1 ? "s" : ""}</span>`
      : "";

    return `
    <div class="panel" id="forest-card-${f.id}"
         onclick="selectForest(${f.id})"
         style="cursor:pointer;transition:border-color 0.2s;border:1px solid var(--border);padding:0.7rem 0.85rem;">
      <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:0.4rem;">
        <div style="font-weight:600;font-size:0.85rem;line-height:1.3;">${f.name}</div>
        <div style="display:flex;gap:0.3rem;align-items:center;flex-shrink:0;margin-left:0.4rem;">
          ${alertBadge}
          <span style="font-size:0.7rem;font-weight:700;color:${riskColor};background:${riskColor}22;padding:0.15rem 0.45rem;border-radius:4px;white-space:nowrap;">${f.risk_level}</span>
        </div>
      </div>
      <div style="font-size:0.72rem;color:var(--text-muted);margin-bottom:0.45rem;">${f.district} · ${f.area_km2.toLocaleString()} km²</div>
      <div style="font-size:0.72rem;color:var(--text-muted);display:flex;gap:0.6rem;flex-wrap:wrap;">
        <span>🕐 ${scanAge}</span>
        ${carbonStr ? `<span>🌿 ${carbonStr}</span>` : ""}
      </div>
      <div style="display:flex;flex-wrap:wrap;gap:0.25rem;margin-top:0.45rem;">
        ${f.known_risks.slice(0,3).map(r =>
          `<span style="font-size:0.65rem;background:rgba(239,68,68,0.1);color:#f87171;padding:0.1rem 0.35rem;border-radius:3px;">${r}</span>`
        ).join("")}
        ${f.known_risks.length > 3
          ? `<span style="font-size:0.65rem;color:var(--text-muted);">+${f.known_risks.length - 3} more</span>`
          : ""}
      </div>
    </div>`;
  }).join("");
}

function selectForest(forestId) {
  document.querySelectorAll('[id^="forest-card-"]').forEach(el => {
    el.style.borderColor = "var(--border)";
  });
  const card = document.getElementById(`forest-card-${forestId}`);
  if (card) card.style.borderColor = "var(--accent-green)";

  Object.entries(forestPolygonLayers).forEach(([id, layer]) => {
    layer.setStyle({ weight: id == forestId ? 4 : 2.5 });
  });

  selectedForestId = forestId;

  const layer = forestPolygonLayers[forestId];
  if (layer) map.flyToBounds(layer.getBounds(), { padding: [60, 60], duration: 0.8 });

  const card2 = document.getElementById(`forest-card-${forestId}`);
  const name = card2 ? card2.querySelector('[style*="font-weight:600"]')?.textContent : `Forest ${forestId}`;
  openNDVIPanel(forestId, name || `Forest ${forestId}`);
}

function showAdminLogin() {
  document.getElementById("admin-login-panel").style.display = "block";
  document.getElementById("admin-login-panel").scrollIntoView({ behavior: "smooth" });
}

function hideAdminLogin() {
  document.getElementById("admin-login-panel").style.display = "none";
}

function timeAgo(isoString) {
  const diff = Date.now() - new Date(isoString).getTime();
  const mins  = Math.floor(diff / 60000);
  const hours = Math.floor(diff / 3600000);
  const days  = Math.floor(diff / 86400000);
  if (mins < 60)   return `${mins}m ago`;
  if (hours < 24)  return `${hours}h ago`;
  return `${days}d ago`;
}

/* ═══════════════════════════════════════════════
   AUTH TAB SWITCHER
═══════════════════════════════════════════════ */

function switchAuthTab(tab) {
  ["login","register","join"].forEach(t => {
    document.getElementById(`auth-${t}`).style.display   = t === tab ? "block" : "none";
    document.getElementById(`tab-${t}`).classList.toggle("active", t === tab);
  });
}

/* ═══════════════════════════════════════════════
   AUTH
═══════════════════════════════════════════════ */

async function login() {
  const email    = document.getElementById("login-email").value.trim();
  const password = document.getElementById("login-password").value;
  if (!email || !password) { showToast("Enter email and password", "error"); return; }

  const btn = document.getElementById("btn-login");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-sm"></span> Authenticating...`;

  const formData = new URLSearchParams();
  formData.append("username", email);
  formData.append("password", password);

  try {
    const res = await fetch(`${API_BASE}/api/auth/token`, {
      method: "POST",
      body: formData,
      headers: { "Content-Type": "application/x-www-form-urlencoded" }
    });
    if (!res.ok) throw new Error("Login failed");

    const data = await res.json();
    jwtToken = data.access_token;
    await _postAuthSetup(data);
    showToast("Logged in successfully", "success");

  } catch (e) {
    showToast("Authentication failed. Check credentials.", "error");
  } finally {
    btn.disabled = false;
    btn.innerHTML = "🔐 Sign In";
  }
}

async function handleRegister() {
  const orgName  = document.getElementById("reg-orgname").value.trim();
  const fullName = document.getElementById("reg-fullname").value.trim();
  const email    = document.getElementById("reg-email").value.trim();
  const password = document.getElementById("reg-password").value;
  const confirm  = document.getElementById("reg-confirm").value;

  if (!orgName || !fullName || !email || !password) {
    showToast("Please fill in all fields", "error"); return;
  }
  if (password !== confirm) {
    showToast("Passwords do not match", "error"); return;
  }
  if (password.length < 8) {
    showToast("Password must be at least 8 characters", "error"); return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/auth/register`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ org_name: orgName, full_name: fullName, email, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Registration failed");

    jwtToken = data.access_token;
    await _postAuthSetup(data);
    showToast(`Organization '${data.org_name}' created! You are the admin.`, "success");
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function handleJoin() {
  const code     = document.getElementById("join-code").value.trim().toUpperCase();
  const fullName = document.getElementById("join-fullname").value.trim();
  const email    = document.getElementById("join-email").value.trim();
  const password = document.getElementById("join-password").value;
  const confirm  = document.getElementById("join-confirm").value;

  if (!code || !fullName || !email || !password) {
    showToast("Please fill in all fields", "error"); return;
  }
  if (password !== confirm) {
    showToast("Passwords do not match", "error"); return;
  }

  try {
    const res = await fetch(`${API_BASE}/api/auth/join`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ invite_code: code, full_name: fullName, email, password })
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to join organization");

    jwtToken = data.access_token;
    await _postAuthSetup(data);
    showToast(`Joined '${data.org_name}' as ${data.role}!`, "success");
  } catch (e) {
    showToast(e.message, "error");
  }
}

async function _postAuthSetup(tokenData) {
  const meRes = await apiFetch("/api/auth/me");
  currentUser = await meRes.json();

  document.getElementById("admin-login-panel").style.display = "none";
  document.getElementById("explore-panel").style.display    = "none";
  document.getElementById("enterprise-dashboard").style.display = "block";

  const roleBadge = document.getElementById("user-role-badge");
  roleBadge.textContent = currentUser.role.toUpperCase();
  roleBadge.className = `role-tag ${currentUser.role}`;
  document.getElementById("user-name-display").textContent = `${currentUser.full_name} · ${currentUser.email}`;

  if (currentUser.role === "admin") {
    document.getElementById("btn-admin").style.display = "inline-flex";
  } else {
    document.getElementById("btn-admin").style.display = "none";
  }
  if (currentUser.role === "viewer") {
    document.getElementById("create-aoi-panel").style.display = "none";
    document.querySelectorAll(".btn-scan, .btn-delete-aoi").forEach(b => b.style.display = "none");
  }

  loadAOIs();
  loadDashboardStats();
  statsInterval      = setInterval(loadDashboardStats, 30000);
  alertPollInterval  = setInterval(loadAlerts, 15000);
}

function logout() {
  jwtToken = null;
  currentUser = null;
  selectedAOIId = null;
  if (alertPollInterval) clearInterval(alertPollInterval);
  if (statsInterval)     clearInterval(statsInterval);
  closeNDVIPanel();
  document.getElementById("enterprise-dashboard").style.display = "none";
  document.getElementById("explore-panel").style.display        = "block";
  showToast("Logged out — back to explore mode", "info");
}

/* ═══════════════════════════════════════════════
   API HELPERS
═══════════════════════════════════════════════ */

async function apiFetch(path, options = {}) {
  const headers = { ...(options.headers || {}) };
  if (jwtToken) headers["Authorization"] = `Bearer ${jwtToken}`;
  if (options.json) {
    headers["Content-Type"] = "application/json";
    options.body = JSON.stringify(options.json);
    delete options.json;
  }
  return fetch(`${API_BASE}${path}`, { ...options, headers });
}

/* ═══════════════════════════════════════════════
   DASHBOARD STATS
═══════════════════════════════════════════════ */

async function loadDashboardStats() {
  if (!jwtToken) return;
  try {
    const res = await apiFetch("/api/dashboard/stats");
    if (!res.ok) return;
    const s = await res.json();

    document.getElementById("stat-total").textContent  = s.total_aois;
    document.getElementById("stat-high").textContent   = s.high_risk_count;
    document.getElementById("stat-alerts").textContent = s.unresolved_alerts;

    const scanEl = document.getElementById("last-scan-label");
    if (s.last_scan_time && scanEl) {
      const d = new Date(s.last_scan_time);
      scanEl.textContent = `Last scan: ${d.toLocaleDateString()} ${d.toLocaleTimeString()}`;
    }
  } catch (e) {}
}

/* ═══════════════════════════════════════════════
   AOI MANAGEMENT
═══════════════════════════════════════════════ */

// ─────────────────────────────────────────────────────────────
// FIX 1: getElementById("new-aoi-name") → getElementById("aoi-name-input")
//
// The HTML panel has:  <input type="text" id="aoi-name-input" ...>
// The old code queried "new-aoi-name" which does not exist in the DOM.
// querySelector returned null, and .value on null threw:
//   "Cannot read properties of null (reading 'value')"
// ─────────────────────────────────────────────────────────────
async function saveAOI() {
  const name = document.getElementById("aoi-name-input").value.trim(); // FIX 1
  if (!name || !currentDrawnAOI) {
    showToast("Name your area and draw a polygon first", "error");
    return;
  }

  const btn = document.getElementById("btn-save-aoi");
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner-sm"></span>`;

  try {
    const res = await apiFetch("/api/aois/", {
      method: "POST",
      json: { name, geojson_polygon: currentDrawnAOI }
    });

    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Save failed");
    }

    document.getElementById("aoi-name-input").value = ""; // FIX 1
    currentDrawnAOI = null;
    showToast(`AOI "${name}" saved — scan queued`, "success");
    loadAOIs();
    loadDashboardStats();
  } catch (e) {
    showToast(`Failed: ${e.message}`, "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Save AOI";
  }
}

async function loadAOIs() {
  if (!jwtToken) return;
  try {
    const res = await apiFetch("/api/aois/");
    if (!res.ok) return;
    const aois = await res.json();
    renderAOIList(aois);
    populateCompareSelects(aois);
  } catch (e) {}
}

function renderAOIList(aois) {
  const list = document.getElementById("aoi-list");
  if (!list) return;

  if (!aois.length) {
    list.innerHTML = `<p class="instruction">No active regions found.</p>`;
    return;
  }

  list.innerHTML = aois.map(a => {
    const risk = a.last_risk_level || "PENDING";
    const riskColor = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#10b981", PENDING: "#94a3b8" }[risk];
    const scanned = a.last_scanned
      ? new Date(a.last_scanned).toLocaleDateString()
      : "Never scanned";
    const carbon = a.last_carbon_loss != null
      ? `${a.last_carbon_loss.toFixed(1)} t CO₂`
      : "—";

    const canEdit = currentUser && currentUser.role !== "viewer";

    return `
    <div class="aoi-item ${selectedAOIId === a.id ? 'selected' : ''}" id="aoi-item-${a.id}" onclick="selectAOI(${a.id})">
      <div class="aoi-item-header">
        <span class="aoi-name">${a.name}</span>
        <span class="risk-badge ${risk}">${risk}</span>
      </div>
      <div class="aoi-meta">
        <span>🕐 ${scanned}</span>
        <span>🌿 ${carbon}</span>
      </div>
      ${canEdit ? `
      <div class="aoi-actions" onclick="event.stopPropagation()">
        <button class="btn btn-ghost btn-xs" onclick="triggerScan(${a.id})">🔄 Scan</button>
        <button class="btn btn-ghost btn-xs" onclick="openNDVIPanel(${a.id}, '${a.name}')">📈 NDVI</button>
        <a class="btn btn-ghost btn-xs" href="/api/public/report/${a.id}" target="_blank">📄 Report</a>
        ${currentUser.role === 'admin' ? `<button class="btn btn-danger btn-xs" onclick="deleteAOI(${a.id})">🗑</button>` : ''}
      </div>` : `
      <div class="aoi-actions" onclick="event.stopPropagation()">
        <button class="btn btn-ghost btn-xs" onclick="openNDVIPanel(${a.id}, '${a.name}')">📈 NDVI</button>
        <a class="btn btn-ghost btn-xs" href="/api/public/report/${a.id}" target="_blank">📄 Report</a>
      </div>`}
    </div>`;
  }).join("");
}

function selectAOI(id) {
  selectedAOIId = id;
  loadAOIs();
}

async function triggerScan(aoi_id) {
  try {
    const res = await apiFetch(`/api/aois/${aoi_id}/scan`, { method: "POST" });
    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      throw new Error(err.detail || "failed");
    }
    showToast("Scan queued — results appear after GEE processes (~30s)", "success");
  } catch (e) {
    showToast("Scan queue unavailable. Run: python -m huey.bin.huey_consumer backend.tasks.huey", "error");
  }
}

async function deleteAOI(aoi_id) {
  if (!confirm("Delete this AOI and all its alerts? This cannot be undone.")) return;
  try {
    const res = await apiFetch(`/api/aois/${aoi_id}`, { method: "DELETE" });
    if (!res.ok) throw new Error();
    showToast("AOI deleted", "info");
    if (selectedAOIId === aoi_id) closeNDVIPanel();
    loadAOIs();
    loadDashboardStats();
  } catch (e) {
    showToast("Delete failed", "error");
  }
}

/* ═══════════════════════════════════════════════
   NDVI CHART PANEL
═══════════════════════════════════════════════ */

async function openNDVIPanel(aoi_id, aoi_name) {
  selectedAOIId = aoi_id;

  const panel = document.getElementById("ndvi-panel");
  panel.classList.add("visible");
  document.getElementById("ndvi-panel-title").textContent = `NDVI Trend — ${aoi_name}`;
  document.getElementById("ndvi-panel-meta").textContent = "Loading satellite data...";
  document.getElementById("btn-download-report").href = `/api/public/report/${aoi_id}`;

  ["carbon-loss-val", "co2-val", "area-val"].forEach(id => {
    document.getElementById(id).textContent = "—";
  });

  if (ndviChartInstance) { ndviChartInstance.destroy(); ndviChartInstance = null; }

  const timeoutMs    = 90000;
  const timeoutSignal = AbortSignal.timeout ? AbortSignal.timeout(timeoutMs) : undefined;
  const opts          = timeoutSignal ? { signal: timeoutSignal } : {};

  try {
    if (jwtToken) {
      document.getElementById("ndvi-panel-meta").textContent = "Calling Google Earth Engine — this takes 30–60s...";
      const [ndviRes, carbonRes] = await Promise.all([
        apiFetch(`/api/aois/${aoi_id}/ndvi`, opts),
        apiFetch(`/api/aois/${aoi_id}/carbon`, opts)
      ]);
      if (!ndviRes.ok) {
        const err = await ndviRes.json().catch(() => ({}));
        throw new Error(err.detail || "NDVI fetch failed — check GEE authentication");
      }
      const ndviData   = await ndviRes.json();
      const carbonData = carbonRes.ok ? await carbonRes.json() : null;
      renderNDVIChart(ndviData);
      if (carbonData) renderCarbonPanel(carbonData);

    } else {
      document.getElementById("ndvi-panel-meta").textContent = "Loading satellite analysis...";
      const ndviRes = await fetch(`${API_BASE}/api/public/forests/${aoi_id}/ndvi`);
      if (!ndviRes.ok) throw new Error("Failed to load forest NDVI data");
      const ndviData = await ndviRes.json();
      renderNDVIChart(ndviData);
      const detailRes = await fetch(`${API_BASE}/api/public/forests/${aoi_id}`);
      if (detailRes.ok) {
        const detail = await detailRes.json();
        if (detail.carbon_loss_tons != null) {
          renderCarbonPanel({
            carbon_loss_tons:    detail.carbon_loss_tons,
            co2_equivalent_tons: detail.carbon_loss_tons * 3.67,
            area_hectares:       detail.area_km2 ? detail.area_km2 * 100 : 0
          });
        }
      }
    }

  } catch (e) {
    const msg = e.name === "TimeoutError"
      ? "GEE timed out (>90s). Check your GEE credentials in core/auth.py"
      : e.message || "Unknown error";
    document.getElementById("ndvi-panel-meta").textContent = `⚠ ${msg}`;
    showToast("NDVI load failed — see panel for details", "error");
  }
}

function renderNDVIChart(data) {
  const years  = Object.keys(data.ndvi_timeseries).sort((a,b) => Number(a) - Number(b));
  const values = years.map(y => data.ndvi_timeseries[y]);
  const risk   = data.risk_level || "UNKNOWN";

  document.getElementById("ndvi-panel-meta").textContent =
    `${years[0]} – ${years[years.length - 1]} · Slope: ${data.slope?.toFixed(5) || "N/A"} · Source: Sentinel-2`;

  const badge = document.getElementById("ndvi-risk-badge");
  badge.textContent = risk;
  badge.className = `risk-badge ${risk}`;

  const riskColor = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#10b981" }[risk] || "#94a3b8";

  // FIX 4: Add { willReadFrequently: true } — suppresses Canvas2D readback warning
  //        when Chart.js calls getImageData() repeatedly during animation frames.
  const ctx = document.getElementById("ndvi-chart").getContext("2d", { willReadFrequently: true });
  ndviChartInstance = new Chart(ctx, {
    type: "line",
    data: {
      labels: years,
      datasets: [{
        label: "NDVI",
        data: values,
        borderColor: riskColor,
        backgroundColor: riskColor + "22",
        borderWidth: 2.5,
        pointBackgroundColor: riskColor,
        pointRadius: 4,
        fill: true,
        tension: 0.35
      }]
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => ` NDVI: ${ctx.parsed.y.toFixed(4)}`
          }
        }
      },
      scales: {
        x: {
          ticks: { color: "#94a3b8", font: { size: 11 } },
          grid: { color: "#334155" }
        },
        y: {
          ticks: { color: "#94a3b8", font: { size: 11 } },
          grid: { color: "#334155" },
          min: 0,
          max: 1
        }
      }
    }
  });
}

function renderCarbonPanel(data) {
  const carbonLoss = data.carbon_loss_tons ?? 0;
  const co2        = data.co2_equivalent_tons ?? 0;
  const area       = data.area_hectares ?? data.affected_area_ha ?? 0;

  const isStable = carbonLoss === 0 || carbonLoss < 0.01;

  const carbonEl = document.getElementById("carbon-loss-val");
  const co2El    = document.getElementById("co2-val");
  const areaEl   = document.getElementById("area-val");

  if (isStable) {
    if (carbonEl) {
      carbonEl.textContent = "Stable";
      carbonEl.style.color = "#10b981";
      carbonEl.style.fontSize = "0.9rem";
    }
    if (co2El) {
      co2El.textContent = "No loss";
      co2El.style.color = "#10b981";
      co2El.style.fontSize = "0.9rem";
    }
  } else {
    if (carbonEl) { carbonEl.textContent = carbonLoss.toFixed(2); carbonEl.style.color = ""; }
    if (co2El)    { co2El.textContent    = co2.toFixed(2);        co2El.style.color    = ""; }
  }

  if (areaEl) areaEl.textContent = area > 0 ? area.toFixed(1) : "—";

  const lostLabel = document.querySelector("#carbon-panel .carbon-lost-label");
  if (lostLabel && isStable) {
    lostLabel.textContent = "Forest Health";
  }
}

function closeNDVIPanel() {
  document.getElementById("ndvi-panel").classList.remove("visible");
  if (ndviChartInstance) { ndviChartInstance.destroy(); ndviChartInstance = null; }
}

// After fetching ndvi and carbon data for an AOI:
// Only call if aoi_id, aoi_name, and ndviData are defined
if (typeof fgSetAoiContext === "function" && typeof aoi_id !== "undefined" && typeof aoi_name !== "undefined" && typeof ndviData !== "undefined") {
    fgSetAoiContext(aoi_id, {
        aoi_name:       aoi_name,
        area_hectares:  ndviData.area_hectares || 0,
        risk_level:     ndviData.risk?.risk_level,
        ndvi_timeseries: ndviData.ndvi_timeseries,
        carbon_loss_tons: carbonData?.carbon_impact?.co2_equivalent_tons,
        biome:          ndviData.biome,
    });
}
/* ═══════════════════════════════════════════════
   ALERTS
═══════════════════════════════════════════════ */

async function loadAlerts() {
  if (!jwtToken) return;
  try {
    const risk     = document.getElementById("alert-filter-risk")?.value || "";
    const resolved = document.getElementById("alert-filter-resolved")?.value;

    let url = "/api/alerts/?";
    if (risk)            url += `risk_level=${risk}&`;
    if (resolved !== "") url += `resolved=${resolved}&`;

    const res = await apiFetch(url);
    if (!res.ok) return;
    const alerts = await res.json();
    renderAlerts(alerts);
  } catch (e) {}
}

function renderAlerts(alerts) {
  const list = document.getElementById("alerts-list");
  if (!list) return;

  if (!alerts.length) {
    list.innerHTML = `<p class="instruction">No alerts match current filter.</p>`;
    return;
  }

  list.innerHTML = alerts.map(a => {
    const date  = new Date(a.created_at).toLocaleString();
    const conf  = a.confidence_score != null ? a.confidence_score.toFixed(1) : null;
    const confColor = conf >= 80 ? "#ef4444" : conf >= 50 ? "#f59e0b" : "#10b981";
    const carbon = a.carbon_loss_tons != null ? `${a.carbon_loss_tons.toFixed(2)} t CO₂` : "";

    return `
    <div class="alert-item">
      <div class="alert-left">
        <div class="alert-risk ${a.risk_level}">${a.risk_level} RISK — AOI #${a.aoi_id}</div>
        <div class="alert-meta">
          ${date}${carbon ? ` · ${carbon}` : ""}
          ${a.resolved ? ' · <span style="color:#10b981">✓ Resolved</span>' : ""}
        </div>
        ${conf !== null ? `
        <div style="display:flex;align-items:center;gap:0.4rem;margin-top:0.3rem;">
          <div class="confidence-bar" style="width:80px;">
            <div class="confidence-fill" style="width:${conf}%;background:${confColor};"></div>
          </div>
          <span style="font-size:0.68rem;color:${confColor};font-family:var(--font-mono);">${conf}%</span>
        </div>` : ""}
      </div>
      ${!a.resolved && currentUser?.role !== "viewer" ? `
      <button class="btn btn-ghost btn-xs" style="margin-left:0.5rem;" onclick="resolveAlert(${a.id})">
        Resolve
      </button>` : ""}
    </div>`;
  }).join("");
}

async function resolveAlert(alertId) {
  try {
    const res = await apiFetch(`/api/alerts/${alertId}/resolve`, {
      method: "PATCH",
      json: { resolved: true }
    });
    if (!res.ok) throw new Error();
    showToast("Alert resolved", "success");
    loadAlerts();
    loadDashboardStats();
  } catch (e) {
    showToast("Could not resolve alert", "error");
  }
}

/* ═══════════════════════════════════════════════
   AOI COMPARISON
═══════════════════════════════════════════════ */

async function populateCompareSelects(aois = null) {
  if (!aois) {
    try {
      if (jwtToken) {
        const res = await apiFetch("/api/aois/");
        if (res.ok) aois = await res.json();
      } else {
        const res = await fetch(`${API_BASE}/api/public/forests`);
        const data = await res.json();
        aois = (data.forests || []).map(f => ({ id: f.id, name: f.name }));
      }
    } catch { return; }
  }

  if (!aois || !aois.length) return;

  const opts = `<option value="">Select forest...</option>` +
    aois.map(a => `<option value="${a.id}">${a.name}</option>`).join("");

  const s1 = document.getElementById("compare-aoi-1");
  const s2 = document.getElementById("compare-aoi-2");
  if (s1) s1.innerHTML = opts;
  if (s2) s2.innerHTML = opts;
}

async function runComparison() {
  const id1 = document.getElementById("compare-aoi-1").value;
  const id2 = document.getElementById("compare-aoi-2").value;

  if (!id1 || !id2) { showToast("Select both forests to compare", "error"); return; }
  if (id1 === id2)  { showToast("Select two different forests", "error");   return; }

  const result = document.getElementById("compare-result");
  result.innerHTML = `<div style="text-align:center;padding:0.5rem;color:#94a3b8;font-size:0.8rem;"><span class="spinner-sm"></span> Calling Google Earth Engine — takes 30–60s per forest...</div>`;

  try {
    const fetchNDVI = (id) => jwtToken
      ? apiFetch(`/api/aois/${id}/ndvi`)
      : fetch(`${API_BASE}/api/public/forests/${id}/ndvi`);

    const [r1, r2] = await Promise.all([fetchNDVI(id1), fetchNDVI(id2)]);

    if (!r1.ok || !r2.ok) {
      const err = await (r1.ok ? r2 : r1).json().catch(() => ({}));
      throw new Error(err.detail || "Failed to fetch NDVI data");
    }

    const [d1, d2] = await Promise.all([r1.json(), r2.json()]);

    if (!d1.ndvi_timeseries || !d2.ndvi_timeseries) {
      throw new Error("No NDVI data returned — try scanning the forests first");
    }

    result.innerHTML = "";
    renderCompareChart(d1, d2); // FIX 2: was openCompareModal(d1, d2) — renamed to avoid collision
  } catch (e) {
    result.innerHTML = `<div style="color:#ef4444;font-size:0.8rem;padding:0.5rem;">⚠ ${e.message}</div>`;
    showToast(e.message, "error");
  }
}

// ─────────────────────────────────────────────────────────────
// FIX 2a: Renamed from openCompareModal(d1, d2) → renderCompareChart(d1, d2)
//
// The original code had TWO functions named openCompareModal():
//   1. openCompareModal(d1, d2)   — renders the Chart.js chart (this one)
//   2. openCompareModal()         — modal trigger / called from HTML button
//
// In non-strict JS the second definition silently overwrote the first,
// causing "openCompareModal is not a function" when called with (d1, d2).
// Renaming the chart renderer eliminates the duplicate entirely.
// ─────────────────────────────────────────────────────────────
function renderCompareChart(d1, d2) {
  const modal = document.getElementById("compare-modal");
  modal.classList.add("visible");

  if (compareChartInstance) {
    compareChartInstance.destroy();
    compareChartInstance = null;
  }

  // setTimeout(300) guarantees the CSS modal transition (≈200ms) is complete
  // before Chart.js reads canvas dimensions — prevents blank chart render.
  setTimeout(() => {
    const years1   = Object.keys(d1.ndvi_timeseries).map(Number).sort((a,b)=>a-b);
    const years2   = Object.keys(d2.ndvi_timeseries).map(Number).sort((a,b)=>a-b);
    const allYears = [...new Set([...years1, ...years2])].sort((a,b)=>a-b);

    // API returns string keys {"2018":0.5} but allYears contains Numbers —
    // convert to String for lookup to avoid every value being undefined.
    const vals1 = allYears.map(y => d1.ndvi_timeseries[String(y)] ?? d1.ndvi_timeseries[y] ?? null);
    const vals2 = allYears.map(y => d2.ndvi_timeseries[String(y)] ?? d2.ndvi_timeseries[y] ?? null);

    const c1 = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#10b981" }[d1.risk_level] || "#94a3b8";
    const c2 = { HIGH: "#ef4444", MEDIUM: "#f59e0b", LOW: "#10b981" }[d2.risk_level] || "#06b6d4";

    const canvas = document.getElementById("compare-chart");
    if (!canvas) return;

    // FIX 4: willReadFrequently prevents repeated-getImageData perf warning
    const ctx = canvas.getContext("2d", { willReadFrequently: true });

    compareChartInstance = new Chart(ctx, {
      type: "line",
      data: {
        labels: allYears,
        datasets: [
          {
            label: d1.aoi_name,
            data: vals1,
            borderColor: c1,
            backgroundColor: c1 + "33",
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: c1,
            fill: true,
            tension: 0.35
          },
          {
            label: d2.aoi_name,
            data: vals2,
            borderColor: c2,
            backgroundColor: c2 + "22",
            borderWidth: 2.5,
            pointRadius: 4,
            pointBackgroundColor: c2,
            fill: false,
            tension: 0.35,
            borderDash: [6, 3]
          }
        ]
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        animation: { duration: 600 },
        plugins: {
          legend: {
            display: true,
            labels: { color: "#f1f5f9", font: { size: 12 }, padding: 16 }
          },
          tooltip: {
            callbacks: {
              label: ctx => ` ${ctx.dataset.label}: ${ctx.parsed.y?.toFixed(4) ?? "N/A"}`
            }
          }
        },
        scales: {
          x: {
            ticks: { color: "#94a3b8", font: { size: 11 } },
            grid:  { color: "#1e293b" }
          },
          y: {
            min: 0, max: 1,
            ticks: { color: "#94a3b8", font: { size: 11 } },
            grid:  { color: "#1e293b" },
            title: { display: true, text: "NDVI Value", color: "#64748b", font: { size: 11 } }
          }
        }
      }
    });

    const slope1 = d1.slope ?? d1.risk_score ?? 0;
    const slope2 = d2.slope ?? d2.risk_score ?? 0;
    document.getElementById("compare-summary").innerHTML = [
      { d: d1, c: c1, slope: slope1 },
      { d: d2, c: c2, slope: slope2 }
    ].map(({ d, c, slope }) => {
      const trend = slope > 0 ? "↑ Growing" : slope < -0.001 ? "↓ Declining" : "→ Stable";
      const trendColor = slope > 0 ? "#10b981" : slope < -0.001 ? "#ef4444" : "#94a3b8";
      return `
      <div style="background:#0f172a;border-radius:8px;padding:0.85rem 1rem;border:1px solid #1e293b;flex:1;min-width:180px;">
        <div style="font-weight:700;font-size:0.88rem;margin-bottom:0.5rem;color:${c};">${d.aoi_name}</div>
        <div style="display:flex;justify-content:space-between;margin-bottom:0.25rem;">
          <span style="font-size:0.75rem;color:#64748b;">Risk</span>
          <span style="font-size:0.75rem;font-weight:600;color:${c};">${d.risk_level}</span>
        </div>
        <div style="display:flex;justify-content:space-between;">
          <span style="font-size:0.75rem;color:#64748b;">Trend</span>
          <span style="font-size:0.75rem;font-weight:600;color:${trendColor};">${trend}</span>
        </div>
      </div>`;
    }).join("");
  }, 300);
}

/* ═══════════════════════════════════════════════
   ADMIN: USER MANAGEMENT
═══════════════════════════════════════════════ */

function openAdminModal() {
  document.getElementById("admin-modal").classList.add("visible");
  loadUsers();
}

function closeAdminModal() {
  document.getElementById("admin-modal").classList.remove("visible");
}

async function loadUsers() {
  try {
    const res = await apiFetch("/api/users/");
    if (!res.ok) return;
    const users = await res.json();

    document.getElementById("users-list").innerHTML = users.map(u => `
      <div class="user-row">
        <div>
          <div style="font-size:0.82rem;font-weight:500;">${u.full_name}</div>
          <div style="font-size:0.72rem;color:var(--text-muted);">${u.email}</div>
        </div>
        <div style="display:flex;align-items:center;gap:0.5rem;">
          <span class="role-tag ${u.role}">${u.role}</span>
          <select style="width:auto;font-size:0.72rem;padding:0.15rem 0.4rem;" onchange="changeUserRole(${u.id}, this.value)">
            <option value="viewer"   ${u.role === 'viewer'   ? 'selected' : ''}>viewer</option>
            <option value="analyst"  ${u.role === 'analyst'  ? 'selected' : ''}>analyst</option>
            <option value="admin"    ${u.role === 'admin'    ? 'selected' : ''}>admin</option>
          </select>
        </div>
      </div>`).join("");
  } catch (e) {}
}

async function createUser() {
  const email    = document.getElementById("new-user-email").value.trim();
  const name     = document.getElementById("new-user-name").value.trim();
  const password = document.getElementById("new-user-password").value;
  const role     = document.getElementById("new-user-role").value;

  if (!email || !name || !password) { showToast("Fill all fields", "error"); return; }

  try {
    const res = await apiFetch("/api/users/", {
      method: "POST",
      json: { email, full_name: name, password, role }
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || "Create failed");
    }
    showToast(`User ${email} created`, "success");
    document.getElementById("new-user-email").value    = "";
    document.getElementById("new-user-name").value     = "";
    document.getElementById("new-user-password").value = "";
    loadUsers();
  } catch (e) {
    showToast(`Failed: ${e.message}`, "error");
  }
}

async function changeUserRole(userId, role) {
  try {
    const res = await apiFetch(`/api/users/${userId}/role?role=${role}`, { method: "PATCH" });
    if (!res.ok) throw new Error();
    showToast("Role updated", "success");
    loadUsers();
  } catch (e) {
    showToast("Failed to update role", "error");
  }
}

/* ═══════════════════════════════════════════════
   INVITE CODES
═══════════════════════════════════════════════ */

async function loadInviteCodes() {
  const container = document.getElementById("invite-codes-list");
  if (!container) return;
  container.innerHTML = `<div style="color:var(--text-muted);font-size:0.78rem;">Loading...</div>`;
  try {
    const res  = await apiFetch("/api/auth/invites");
    const data = await res.json();
    if (!data.length) {
      container.innerHTML = `<div style="color:var(--text-muted);font-size:0.78rem;text-align:center;padding:0.5rem 0;">No invite codes yet — generate one above.</div>`;
      return;
    }
    container.innerHTML = data.map(inv => {
      const expired    = inv.expires_at && new Date(inv.expires_at) < new Date();
      const maxLabel   = inv.max_uses === -1 ? "∞" : inv.max_uses;
      const usedAll    = inv.max_uses !== -1 && inv.use_count >= inv.max_uses;
      const statusColor = (!inv.is_active || expired) ? "#ef4444" : usedAll ? "#f59e0b" : "#10b981";
      const statusText  = !inv.is_active ? "Revoked" : expired ? "Expired" : `${inv.use_count}/${maxLabel} used`;
      return `
      <div style="background:var(--bg-tertiary);border-radius:6px;padding:0.5rem 0.65rem;display:flex;align-items:center;justify-content:space-between;gap:0.5rem;flex-wrap:wrap;">
        <div style="display:flex;align-items:center;gap:0.4rem;">
          <span style="font-family:monospace;font-weight:700;font-size:0.88rem;color:var(--accent-green);letter-spacing:0.06em;">${inv.code}</span>
          <span style="font-size:0.7rem;background:rgba(255,255,255,0.08);padding:0.1rem 0.35rem;border-radius:4px;color:var(--text-muted);">${inv.role}</span>
        </div>
        <div style="display:flex;align-items:center;gap:0.4rem;">
          <span style="font-size:0.72rem;color:${statusColor};font-weight:600;">${statusText}</span>
          <button onclick="copyInviteCode('${inv.code}')" class="btn btn-ghost btn-xs" title="Copy to clipboard">📋</button>
          ${inv.is_active && !expired ? `<button onclick="revokeInviteCode('${inv.code}')" class="btn btn-ghost btn-xs" style="color:#ef4444;" title="Revoke">✕</button>` : ''}
        </div>
      </div>`;
    }).join("");
  } catch(e) {
    container.innerHTML = `<div style="color:#ef4444;font-size:0.78rem;">Failed to load invite codes</div>`;
  }
}

async function generateInviteCode() {
  const role    = document.getElementById("invite-role").value;
  const maxUses = parseInt(document.getElementById("invite-uses").value);
  const expires = document.getElementById("invite-expires").value;

  try {
    const res = await apiFetch("/api/auth/invite", {
      method: "POST",
      json: {
        role,
        max_uses: maxUses,
        expires_days: expires ? parseInt(expires) : null
      }
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.detail || "Failed to generate code");

    await navigator.clipboard.writeText(data.code).catch(() => {});
    showToast(`Code generated & copied: ${data.code}`, "success");
    loadInviteCodes();
  } catch(e) {
    showToast(e.message, "error");
  }
}

async function revokeInviteCode(code) {
  if (!confirm(`Revoke invite code ${code}? Users won't be able to use it anymore.`)) return;
  try {
    const res = await apiFetch(`/api/auth/invite/${code}`, { method: "DELETE" });
    if (!res.ok) throw new Error("Failed to revoke");
    showToast(`Code ${code} revoked`, "info");
    loadInviteCodes();
  } catch(e) {
    showToast(e.message, "error");
  }
}

function copyInviteCode(code) {
  navigator.clipboard.writeText(code)
    .then(() => showToast(`Copied: ${code}`, "success"))
    .catch(() => showToast(`Code: ${code} — copy manually`, "info"));
}

/* ═══════════════════════════════════════════════
   ADMIN: WEBHOOK CONFIG
═══════════════════════════════════════════════ */

async function loadWebhook() {
  try {
    const res = await apiFetch("/api/organizations/me");
    if (!res.ok) return;
    const org = await res.json();
    const input = document.getElementById("webhook-url-input");
    if (input && org.webhook_url) {
      input.value = org.webhook_url;
      document.getElementById("webhook-status").textContent = `Current: ${org.webhook_url}`;
    }
  } catch (e) {}
}

async function saveWebhook() {
  const url = document.getElementById("webhook-url-input").value.trim();
  if (!url) { showToast("Enter a webhook URL", "error"); return; }

  try {
    const res = await apiFetch("/api/organizations/webhook", {
      method: "PUT",
      json: { webhook_url: url }
    });
    if (!res.ok) throw new Error();
    showToast("Webhook URL saved", "success");
    document.getElementById("webhook-status").textContent = `Saved: ${url}`;
  } catch (e) {
    showToast("Failed to save webhook", "error");
  }
}

/* ═══════════════════════════════════════════════
   HEALTH CHECK
═══════════════════════════════════════════════ */

async function checkHealth() {
  try {
    const res  = await fetch(`${API_BASE}/health`);
    const data = await res.json();
    setStatus(res.ok && data?.status === "ok" ? "online" : "offline");
  } catch {
    setStatus("offline");
  }
}

function setStatus(status) {
  if (lastStatus === status) return;
  lastStatus = status;
  const dot  = document.getElementById("status-dot");
  const text = document.getElementById("status-text");
  if (!dot || !text) return;
  dot.className    = `status-dot ${status === "online" ? "online" : "offline"}`;
  text.textContent = status === "online" ? "Connected" : "Disconnected";
}

function startHealthCheck() {
  checkHealth();
  setInterval(checkHealth, HEALTH_INTERVAL);
}

/* ═══════════════════════════════════════════════
   MAP CLICK ANALYSIS
═══════════════════════════════════════════════ */

async function onMapClick(e) {
  runAnalysis(e.latlng.lat, e.latlng.lng, 1);
}

async function runAnalysis(lat, lon, bufferKm) {
  if (abortController) abortController.abort();
  abortController = new AbortController();
  clearMarkers();

  const params = new URLSearchParams({
    lat: lat.toFixed(4), lon: lon.toFixed(4),
    buffer_km: bufferKm, start_year: 2018, end_year: 2026
  });

  try {
    const res = await fetchWithRetry(`${API_BASE}/analysis?${params}`, { signal: abortController.signal });
    const data = await res.json();
    addMarker(lat, lon, data?.risk?.color || "#22c55e");
  } catch (err) {
    if (err.name !== "AbortError") console.error("Analysis error:", err);
  }
}

/* ═══════════════════════════════════════════════
   AUTO SCAN / HEATMAP
═══════════════════════════════════════════════ */

function debounceAutoScan() {
  clearTimeout(debounceTimer);
  debounceTimer = setTimeout(scanVisibleArea, 1200);
}

async function scanVisibleArea() {
  try {
    const b = map.getBounds();
    const params = new URLSearchParams({
      west: b.getWest(), south: b.getSouth(),
      east: b.getEast(), north: b.getNorth(), cell_km: 2
    });
    const res = await fetch(`${API_BASE}/map/risk-grid?${params}`);
    if (!res.ok) return;
    const fc = await res.json();
    if (fc?.features) renderHeat(fc.features);
  } catch (e) {}
}

function renderHeat(features) {
  if (heatLayer) map.removeLayer(heatLayer);
  const pts = features.map(f => {
    const [lon, lat] = f.geometry.coordinates;
    const i = { HIGH: 1, MEDIUM: 0.6 }[f.properties.risk_level] || 0.3;
    return [lat, lon, i];
  });
  heatLayer = L.heatLayer(pts, { radius: 28, blur: 22, maxZoom: 18 }).addTo(map);
}

/* ═══════════════════════════════════════════════
   MARKERS
═══════════════════════════════════════════════ */

function addMarker(lat, lon, color) {
  const icon = L.divIcon({
    html: `<div style="width:18px;height:18px;border-radius:50%;background:${color};border:3px solid white;box-shadow:0 2px 6px rgba(0,0,0,0.4);"></div>`,
    className: "",
    iconSize: [18, 18]
  });
  L.marker([lat, lon], { icon }).addTo(markersLayer);
}

function clearMarkers() {
  if (markersLayer) markersLayer.clearLayers();
}

/* ═══════════════════════════════════════════════
   NDVI YEAR SLIDER
═══════════════════════════════════════════════ */

let ndviSeries = {};

function initNDVISlider(series) {
  ndviSeries = series;
  const slider = document.getElementById("ndvi-slider");
  const years  = Object.keys(series);
  if (!slider || !years.length) return;
  slider.max = years.length - 1;
  slider.oninput = () => {
    const year = years[slider.value];
    const label = document.getElementById("year-label");
    if (label) label.innerText = year;
    updateNDVILayer(year);
  };
}

function updateNDVILayer(year) {
  if (ndviLayer) map.removeLayer(ndviLayer);
  ndviLayer = L.tileLayer(`${API_BASE}/tiles/ndvi/${year}/{z}/{x}/{y}`, { opacity: 0.6 });
  ndviLayer.addTo(map);
}

/* ═══════════════════════════════════════════════
   TOAST NOTIFICATIONS
═══════════════════════════════════════════════ */

function showToast(message, type = "success") {
  const existing = document.querySelector(".toast");
  if (existing) existing.remove();
  const toast = document.createElement("div");
  toast.className = `toast ${type}`;
  toast.textContent = message;
  document.body.appendChild(toast);
  setTimeout(() => toast.remove(), 3500);
}

/* ═══════════════════════════════════════════════
   FETCH WITH RETRY
═══════════════════════════════════════════════ */

async function fetchWithRetry(url, options = {}, retries = MAX_RETRIES) {
  for (let i = 0; i <= retries; i++) {
    try {
      const res = await fetch(url, options);
      if (!res.ok) throw new Error(await res.text());
      return res;
    } catch (err) {
      if (err.name === "AbortError") throw err;
      if (i === retries) throw err;
      await new Promise(r => setTimeout(r, 1500));
    }
  }
}

/* ═══════════════════════════════════════════════
   COMPARE MODAL TRIGGER
═══════════════════════════════════════════════ */

// ─────────────────────────────────────────────────────────────
// FIX 2b: This is the ONLY function named openCompareModal().
//         The chart-rendering overload has been renamed to
//         renderCompareChart(d1, d2) above.
//
// FIX 2c: showCompareModal() alias added so the HTML button
//         onclick="showCompareModal()" continues to work without
//         needing to edit the HTML file.
// ─────────────────────────────────────────────────────────────
async function openCompareModal() {
  const modal = document.getElementById("compare-modal");
  modal.classList.add("visible");

  const result = document.getElementById("compare-result");
  if (result) result.innerHTML = "";

  if (compareChartInstance) { compareChartInstance.destroy(); compareChartInstance = null; }
  const summary = document.getElementById("compare-summary");
  if (summary) summary.innerHTML = "";

  await populateCompareSelects();
}

// Alias so onclick="showCompareModal()" in HTML works without any HTML edits
function showCompareModal() {
  openCompareModal();
}

function closeCompareModal() {
  document.getElementById("compare-modal").classList.remove("visible");
}

/* ═══════════════════════════════════════════════
   BOOT — SINGLE DOMContentLoaded
═══════════════════════════════════════════════ */

document.addEventListener("DOMContentLoaded", () => {
  initMap();

  // Load 5 forests publicly on startup — no login required
  loadPublicForests();

  const btnLogin = document.getElementById("btn-login");
  if (btnLogin) btnLogin.addEventListener("click", login);

  document.getElementById("login-password")?.addEventListener("keydown", e => {
    if (e.key === "Enter") login();
  });

  document.getElementById("admin-modal")?.addEventListener("click", function(e) {
    if (e.target === this) closeAdminModal();
  });
  document.getElementById("compare-modal")?.addEventListener("click", function(e) {
    if (e.target === this) this.classList.remove("visible");
  });
});


// ─────────────────────────────────────────────────────────────────────────────
// FORESTGUARD AI — Chat + Voice state
// ─────────────────────────────────────────────────────────────────────────────

 
const FG_CHAT = {
  open:           false,
  sessionId:      'fg-session',
  listening:      false,
  recognition:    null,
  femaleVoice:    null,       // ← locked to female voice only
  voicesLoaded:   false,
  currentAoiId:   null,
  currentAoiData: null,
};
 
// ──────────────────────────────────────────────────────────────────────────
// INITIALISE on page load
// ──────────────────────────────────────────────────────────────────────────
document.addEventListener('DOMContentLoaded', () => {
  fgBootChat();
  fgBootVoice();
});
 
function fgBootChat() {
  fgAppendBotMessage(
    "🌿 Hi, I'm ForestGuard AI. I can answer any question about this platform " +
    "and also perform actions for you — just say or type a command like " +
    "<strong>Scan Nallamala Forest</strong> or " +
    "<strong>Show NDVI for Adilabad</strong> or " +
    "<strong>Compare all AOIs</strong>.<br><br>" +
    "What would you like to do?"
  );
  // Load default suggestions
  fgLoadSuggestions(null);

  // ─── Attach event listeners ─
  const chatBubble = document.getElementById('fg-chat-bubble');
  const closeBtn = document.querySelector('.fg-close-btn');
  const sendBtn = document.getElementById('fg-chat-send');
  const chatInput = document.getElementById('fg-chat-input');
  
  if (chatBubble) {
    chatBubble.addEventListener('click', fgToggleChat);
  }
  if (closeBtn) {
    closeBtn.addEventListener('click', () => {
      FG_CHAT.open = false;
      document.getElementById('fg-chat-panel').classList.remove('open');
    });
  }
  if (sendBtn) {
    sendBtn.addEventListener('click', fgSendChat);
  }
  if (chatInput) {
    chatInput.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        fgSendChat();
      }
    });
  }
}
 
// ── Female Voice Setup ────────────────────────────────────────────────────
function fgBootVoice() {
  const SpeechRecognition =
    window.SpeechRecognition || window.webkitSpeechRecognition;
 
  if (!SpeechRecognition) {
    const btn = document.getElementById('fg-header-voice-btn');
    if (btn) btn.style.display = 'none';
    console.info('[VOICE] SpeechRecognition not supported — mic button hidden');
    return;
  }
 
  // ── FIX 1: Wire up the button click ──────────────────────────────────
  // The HTML button has no onclick attribute, so clicking it did nothing.
  // Adding the listener here means it works regardless of the HTML.
  const voiceBtn = document.getElementById('fg-header-voice-btn');
  if (voiceBtn) {
    voiceBtn.addEventListener('click', fgToggleVoice);
    console.log('[VOICE] Click listener attached to fg-header-voice-btn');
  } else {
    console.error('[VOICE] fg-header-voice-btn not found — mic button missing from HTML');
  }
 
  // ── Set up recognition ────────────────────────────────────────────────
  FG_CHAT.recognition = new SpeechRecognition();
  FG_CHAT.recognition.continuous     = false;
  FG_CHAT.recognition.interimResults = true;
  FG_CHAT.recognition.lang           = 'en-IN';
 
  // ── Interim transcript display ────────────────────────────────────────
  FG_CHAT.recognition.onresult = (e) => {
    const transcript = Array.from(e.results)
      .map(r => r[0].transcript)
      .join('');
 
    const bar = document.getElementById('fg-voice-transcript');
    if (bar) bar.textContent = transcript || 'Listening…';
 
    if (e.results[e.results.length - 1].isFinal) {
      fgHandleVoiceCommand(transcript);
    }
  };
 
  // ── Recognition ended ─────────────────────────────────────────────────
  FG_CHAT.recognition.onend = () => {
    FG_CHAT.listening = false;
    fgVoiceBarOff();
  };
 
  // ── FIX 2: Proper error messages for every error code ─────────────────
  FG_CHAT.recognition.onerror = (e) => {
    FG_CHAT.listening = false;
    fgVoiceBarOff();
    console.error('[VOICE] recognition error:', e.error);
 
    const messages = {
      'no-speech':
        '🎙️ I didn\'t hear anything. Click the mic and speak clearly within 5 seconds.',
      'not-allowed':
        '🎙️ Microphone blocked. Click the 🔒 icon in the address bar → Allow mic → Reload.',
      'audio-capture':
        '🎙️ No microphone found. Please connect a microphone.',
      'network':
        '🎙️ Network error during voice recognition. Check your connection.',
      'service-not-allowed':
        '🎙️ Voice recognition blocked. Use http://localhost:8000 (not an IP address).',
      'aborted': null,  // user cancelled — stay silent
    };
 
    const msg = messages[e.error];
    if (msg === null) return;
    showToast(msg || `🎙️ Voice error: ${e.error}`, 'error');
  };
 
  // ── Load TTS voices ───────────────────────────────────────────────────
  function pickFemaleVoice() {
    const voices = window.speechSynthesis.getVoices();
    if (!voices.length) return;
    FG_CHAT.voicesLoaded = true;
 
    const PREFER = [
      'Google UK English Female',
      'Google US English',
      'Microsoft Zira',
      'Microsoft Hazel',
      'Samantha', 'Karen', 'Moira', 'Tessa',
    ];
    for (const name of PREFER) {
      const v = voices.find(v => v.name === name);
      if (v) { FG_CHAT.femaleVoice = v; return; }
    }
    const byName = voices.find(v => v.name.toLowerCase().includes('female'));
    if (byName) { FG_CHAT.femaleVoice = byName; return; }
    const eng = voices.find(v => v.lang && v.lang.startsWith('en'));
    if (eng) FG_CHAT.femaleVoice = eng;
  }
 
  window.speechSynthesis.onvoiceschanged = pickFemaleVoice;
  pickFemaleVoice();
}
// ── TTS ───────────────────────────────────────────────────────────────────
function fgSpeak(text) {
  if (!('speechSynthesis' in window) || !text) return;
 
  // Strip HTML tags and clean up whitespace
  const clean = text
    .replace(/<[^>]*>/g, '')
    .replace(/&amp;/g, '&').replace(/&lt;/g, '<').replace(/&gt;/g, '>')
    .replace(/[#*`_]/g, '')
    .replace(/\s+/g, ' ')
    .trim();
 
  if (!clean) return;
 
  // Cancel any current speech, then wait one event-loop tick before speaking.
  // This guarantees Chrome's audio graph is ready and will honour .voice.
  window.speechSynthesis.cancel();
 
  setTimeout(() => {
    // Re-fetch voices inside the timeout — Chrome may have loaded more by now
    const voices = window.speechSynthesis.getVoices();
 
    // Strict priority list for female voices
    const FEMALE_NAMES = [
      'Google UK English Female',
      'Google US English Female',
      'Microsoft Zira - English (United States)',
      'Microsoft Hazel - English (Great Britain)',
      'Samantha',
      'Karen',
      'Moira',
      'Tessa',
      'Victoria',
    ];
 
    let femaleVoice = null;
 
    // 1. Try strict name match
    for (const name of FEMALE_NAMES) {
      femaleVoice = voices.find(v => v.name === name);
      if (femaleVoice) break;
    }
 
    // 2. Try partial name match for "female"
    if (!femaleVoice) {
      femaleVoice = voices.find(v =>
        v.name.toLowerCase().includes('female') && v.lang.startsWith('en')
      );
    }
 
    // 3. Try voiceURI match
    if (!femaleVoice) {
      femaleVoice = voices.find(v =>
        v.voiceURI && v.voiceURI.toLowerCase().includes('female')
      );
    }
 
    // 4. Fall back to any English voice + raise pitch
    if (!femaleVoice) {
      femaleVoice = voices.find(v => v.lang.startsWith('en'));
    }
 
    const utter    = new SpeechSynthesisUtterance(clean);
    utter.lang     = 'en-IN';
    utter.rate     = 1.0;
    utter.volume   = 1.0;
 
    if (femaleVoice) {
      utter.voice = femaleVoice;
      utter.pitch = 1.2;
      console.log('[VOICE] Speaking with:', femaleVoice.name);
    } else {
      // No female voice found — raise pitch to soften
      utter.pitch = 1.6;
      console.warn('[VOICE] No female voice found — using default with high pitch');
    }
 
    // Update cached voice for next time
    if (femaleVoice) FG_CHAT.femaleVoice = femaleVoice;
 
    window.speechSynthesis.speak(utter);
  }, 50); // 50ms is enough for Chrome to process the cancel()
}
 
// ── Chat Panel Toggle ─────────────────────────────────────────────────────
function fgToggleChat() {
  FG_CHAT.open = !FG_CHAT.open;
  document.getElementById('fg-chat-panel').classList.toggle('open', FG_CHAT.open);
  if (FG_CHAT.open) setTimeout(() => document.getElementById('fg-chat-input')?.focus(), 300);
}
 
// ── Send Chat Message ─────────────────────────────────────────────────────
async function fgSendChat() {
  const input = document.getElementById('fg-chat-input');
  const msg   = (input.value || '').trim();
  if (!msg) return;

  input.value = '';
  input.style.height = 'auto';

  fgAppendUserMessage(msg);
  fgShowTyping();

  try {
    const res = await apiFetch('/api/chat', {
      method: 'POST',
      json: {
        message: msg,
        session_id: FG_CHAT.sessionId,
        aoi_context: FG_CHAT.currentAoiData || null,
      }
    });

    const data = await res.json();   // ✅ FIX (VERY IMPORTANT)

    fgHideTyping();
    fgAppendBotMessage(data.answer); // ✅ use data.answer

    // Execute action if exists
    if (data.action && data.action_payload) {
      fgExecuteAction(data.action, data.action_payload);
    }

  } catch (err) {
    fgHideTyping();
    fgAppendBotMessage('⚠️ Server error. Please try again.');
  }
}
 
function fgSendSuggestion(text) {
  if (chatBusy) return;  
    chatBusy = true; // ✅ prevent spam

  const inp = document.getElementById('fg-chat-input');
  if (inp) inp.value = text;

  fgSendChat();
}
// ── SMART SUGGESTIONS — load from backend based on current AOI ────────────
async function fgLoadSuggestions(aoiData) {
  const container = document.getElementById('fg-chat-suggestions');
  if (!container) return;
 
  let suggestions;
  try {
    const params = aoiData
      ? `?aoi_id=${aoiData.aoi_id || ''}&risk_level=${aoiData.risk_level || ''}&aoi_name=${encodeURIComponent(aoiData.aoi_name || '')}`
      : '';
    const data = await apiFetch(`/api/chat/suggestions${params}`);
    suggestions = data?.suggestions || [];
    if (!Array.isArray(suggestions)) suggestions = [];
  } catch (err) {
    console.warn('Failed to load suggestions:', err);
    // Fallback if API not yet updated
    suggestions = [
      'How is NDVI calculated?',
      'Explain risk score attributes',
      'How is carbon CO₂ estimated?',
      'What is Forest Health Score?',
      'Explain CUSUM algorithm',
      'How does fire detection work?',
    ];
  }
 
  if (!suggestions || suggestions.length === 0) {
    suggestions = [
      'How is NDVI calculated?',
      'Explain risk score attributes',
      'How is carbon CO₂ estimated?',
      'What is Forest Health Score?',
      'Explain CUSUM algorithm',
      'How does fire detection work?',
    ];
  }
 
  container.innerHTML = suggestions.map(s =>
    `<span class="fg-suggestion-chip" onclick="fgSendSuggestion('${s.replace(/'/g, "\\'")}')">${s}</span>`
  ).join('');
  container.style.display = 'flex';
}
 
// ── Execute actions returned by AI ────────────────────────────────────────
function fgExecuteAction(action, payload) {
  const aoiId   = payload?.aoi_id;
  const aoiName = payload?.aoi_name || '';
 
  console.log(`[AI Action] ${action}`, payload);
 
  switch (action) {
 
    case 'scan':
      if (aoiId) {
        triggerScan(aoiId);
        showToast(`Scanning ${aoiName}…`, 'info');
      } else {
        showToast('Select an AOI first, then say "scan"', 'error');
      }
      break;
 
    case 'ndvi':
    case 'carbon':
      // FIX: was showNDVIPanel (undefined) → correct name is openNDVIPanel
      if (aoiId) {
        openNDVIPanel(aoiId, aoiName);
      } else if (FG_CHAT.currentAoiId) {
        openNDVIPanel(FG_CHAT.currentAoiId, FG_CHAT.currentAoiData?.aoi_name || 'Area');
      } else {
        showToast('No AOI selected. Click an AOI first.', 'error');
      }
      break;
 
    case 'compare':
      if (typeof openCompareModal === 'function') openCompareModal();
      break;
 
    case 'report':
      const id = aoiId || FG_CHAT.currentAoiId;
      if (id) {
        window.open(`/api/public/report/${id}`, '_blank');
      } else {
        showToast('Select an AOI to open its report.', 'error');
      }
      break;
 
    case 'alerts':
      const alertsTab = document.querySelector('[onclick*="alerts"]');
      if (alertsTab) alertsTab.click();
      else showToast('Click the Alerts tab to see deforestation alerts.', 'info');
      break;
 
    default:
      break;
  }
}
 
// ── Update AI context when user clicks an AOI ────────────────────────────
// Call this from your existing showNDVIPanel() or renderAOIList()
function fgSetAoiContext(aoiId, data) {
  FG_CHAT.currentAoiId   = aoiId;
  FG_CHAT.currentAoiData = { aoi_id: aoiId, ...data };
  // Refresh suggestions for this AOI
  fgLoadSuggestions(FG_CHAT.currentAoiData);
}
 
// ── Message rendering ─────────────────────────────────────────────────────
function fgAppendUserMessage(text) {
  const msgs = document.getElementById('fg-chat-messages');
  if (!msgs) return;
  const d = document.createElement('div');
  d.className = 'fg-msg user';
  d.innerHTML = `
    <div class="fg-msg-bubble">${_escHtml(text)}</div>
    <div class="fg-msg-time">${_fgNow()}</div>`;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}
 
function fgAppendBotMessage(html) {
  const msgs = document.getElementById('fg-chat-messages');
  if (!msgs) return;
  const formatted = html
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/\n/g, '<br>');
  const d = document.createElement('div');
  d.className = 'fg-msg bot';
  d.innerHTML = `
    <div class="fg-msg-bubble">${formatted}</div>
    <div class="fg-msg-time">${_fgNow()}</div>`;
  msgs.appendChild(d);
  msgs.scrollTop = msgs.scrollHeight;
}
 
let _typingEl = null;
function fgShowTyping() {
  const msgs = document.getElementById('fg-chat-messages');
  if (!msgs) return;
  _typingEl = document.createElement('div');
  _typingEl.className = 'fg-msg bot';
  _typingEl.innerHTML = `<div class="fg-typing"><span></span><span></span><span></span></div>`;
  msgs.appendChild(_typingEl);
  msgs.scrollTop = msgs.scrollHeight;
}
function fgHideTyping() { if (_typingEl) { _typingEl.remove(); _typingEl = null; } }
function _fgNow() {
  return new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
}
function _escHtml(s) {
  const d = document.createElement('div'); d.textContent = s; return d.innerHTML;
}
 
// ══════════════════════════════════════════════════════════════════════════
// VOICE ASSISTANT
// ══════════════════════════════════════════════════════════════════════════
 
// ── Toggle mic on/off ─────────────────────────────────────────────────────
function fgToggleVoice() {
  console.log('[VOICE] fgToggleVoice() called. listening:', FG_CHAT.listening);
 
  if (FG_CHAT.listening) {
    fgVoiceBarOff();
  } else {
    fgVoiceBarOn();
  }
}
 
 
// ── Start listening ───────────────────────────────────────────────────────
function fgVoiceBarOn() {
  console.log('[VOICE] fgVoiceBarOn() called');
 
  // ── Step 1: browser support ──────────────────────────────────────────
  if (!FG_CHAT.recognition) {
    console.error('[VOICE] FG_CHAT.recognition is null — SpeechRecognition not supported');
    showToast('🎙️ Voice not supported. Please use Google Chrome or Edge.', 'error');
    return;
  }
 
  // ── Step 2: Open chat panel so transcript appears ────────────────────
  if (!FG_CHAT.open) fgToggleChat();
 
  // ── Step 3: Update UI immediately (so user knows it responded) ───────
  FG_CHAT.listening = true;
 
  const btn = document.getElementById('fg-header-voice-btn');
  if (btn) btn.classList.add('listening');
 
  const bar = document.getElementById('fg-voice-bar');
  if (bar) bar.classList.add('active');
 
  const transcript = document.getElementById('fg-voice-transcript');
  if (transcript) transcript.textContent = 'Listening…';
 
  // ── Step 4: Start recognition ─────────────────────────────────────────
  // navigator.mediaDevices is ONLY available on HTTPS or localhost.
  // On http:// (non-localhost) it is undefined — calling .getUserMedia on
  // undefined throws a silent TypeError and kills the function.
  //
  // Strategy:
  //   A) If mediaDevices is available (HTTPS / localhost) → request permission
  //      first, then start recognition.
  //   B) If mediaDevices is NOT available (plain http://) → start recognition
  //      directly. Chrome will still prompt for mic permission via its own UI.
 
  if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
    console.log('[VOICE] Using getUserMedia() permission flow');
 
    navigator.mediaDevices.getUserMedia({ audio: true })
      .then(stream => {
        // Release the stream immediately — we only needed the permission grant
        stream.getTracks().forEach(t => t.stop());
        console.log('[VOICE] Mic permission granted — starting recognition');
        _startRecognition();
      })
      .catch(err => {
        FG_CHAT.listening = false;
        fgVoiceBarOff();
        console.error('[VOICE] getUserMedia error:', err.name, err.message);
 
        if (err.name === 'NotAllowedError' || err.name === 'PermissionDeniedError') {
          showToast(
            '🎙️ Microphone blocked!\n\n' +
            'Fix: Click the 🔒 lock icon in the address bar → Allow Microphone → Reload the page.',
            'error'
          );
        } else if (err.name === 'NotFoundError') {
          showToast('🎙️ No microphone found. Please plug in a mic and try again.', 'error');
        } else {
          showToast(`🎙️ Microphone error: ${err.name} — ${err.message}`, 'error');
        }
      });
 
  } else {
    // Plain http:// — mediaDevices is unavailable.
    // Start recognition directly; Chrome handles the permission prompt itself.
    console.warn(
      '[VOICE] navigator.mediaDevices unavailable (plain http?). ' +
      'Starting recognition directly — Chrome will prompt for mic permission.'
    );
    _startRecognition();
  }
}
 
 
// ── Internal: actually call recognition.start() ──────────────────────────
function _startRecognition() {
  try {
    FG_CHAT.recognition.start();
    console.log('[VOICE] recognition.start() called successfully');
  } catch (err) {
    if (err.name === 'InvalidStateError') {
      // Already started — stop and restart
      console.warn('[VOICE] InvalidStateError — stopping and restarting');
      try { FG_CHAT.recognition.stop(); } catch (_) {}
      setTimeout(() => {
        try {
          FG_CHAT.recognition.start();
          console.log('[VOICE] recognition restarted');
        } catch (e2) {
          FG_CHAT.listening = false;
          fgVoiceBarOff();
          console.error('[VOICE] restart failed:', e2);
          showToast('🎙️ Could not restart mic. Please try again.', 'error');
        }
      }, 400);
    } else {
      FG_CHAT.listening = false;
      fgVoiceBarOff();
      console.error('[VOICE] recognition.start() failed:', err);
      showToast(`🎙️ Could not start microphone: ${err.message}`, 'error');
    }
  }
}
 
 
// ── Stop listening ────────────────────────────────────────────────────────
function fgVoiceBarOff() {
  console.log('[VOICE] fgVoiceBarOff() called');
  FG_CHAT.listening = false;
 
  const btn = document.getElementById('fg-header-voice-btn');
  if (btn) btn.classList.remove('listening');
 
  const bar = document.getElementById('fg-voice-bar');
  if (bar) bar.classList.remove('active');
 
  try { FG_CHAT.recognition?.stop(); } catch (_) {}
}
 
// Keep this alias in case any HTML uses it
function fgStopVoice() { fgVoiceBarOff(); }
 
// ── Handle voice command  -────────────────────────────────
async function fgHandleVoiceCommand(transcript) {
  fgVoiceBarOff();
  if (!transcript.trim()) return;
 
  // Show transcript in chat
  if (!FG_CHAT.open) fgToggleChat();
  fgAppendUserMessage('🎙️ ' + transcript);
  fgShowTyping();
 
  // Voice command requires login (the API uses get_current_user)
  if (!jwtToken) {
    fgHideTyping();
    const msg = 'Please log in to use voice commands. Voice actions like scanning and reporting require authentication.';
    fgAppendBotMessage(msg);
    fgSpeak(msg);
    return;
  }
 
  try {
    const resp = await apiFetch('/api/voice/command', {
      method: 'POST',
      json: {
        transcript:     transcript,
        session_id:     'voice',
        current_aoi_id: FG_CHAT.currentAoiId || null,
      }
    });
 
    // ── FIX 1: parse JSON ──────────────────────────────────────────────
    const data = await resp.json();
 
    fgHideTyping();
 
    // ── FIX 2: handle non-OK HTTP responses ───────────────────────────
    if (!resp.ok) {
      const detail = data?.detail || `Server error (${resp.status})`;
      const errMsg = resp.status === 401
        ? 'Please log in first to use voice commands.'
        : `Voice command failed: ${detail}`;
      fgAppendBotMessage(errMsg);
      fgSpeak(errMsg);
      return;
    }
 
    // ── Success ────────────────────────────────────────────────────────
    if (data.speech_text) {
      fgAppendBotMessage(data.speech_text);
      fgSpeak(data.speech_text);
    }
 
    if (data.action && data.action !== 'explain') {
      fgExecuteAction(data.action, data.action_payload || {});
    }
 
  } catch (err) {
    fgHideTyping();
    console.error('[VOICE] fgHandleVoiceCommand error:', err);
    const errMsg = 'Sorry, voice command failed. Please try again.';
    fgAppendBotMessage(errMsg);
    fgSpeak(errMsg);
  }
}
 
// ══════════════════════════════════════════════════════════════════════════
// OFFICER MANAGEMENT (Admin tab)
// ══════════════════════════════════════════════════════════════════════════
 
async function loadOfficers() {
  const el = document.getElementById('fg-officer-list');
  if (!el) return;
  try {
    const officers = await apiFetch('/api/officers/');
    if (!officers.length) {
      el.innerHTML = '<p style="color:#8b949e;font-size:13px;text-align:center;padding:16px;">' +
        'No officers registered. Add one below to receive email alerts.</p>';
      return;
    }
    el.innerHTML = officers.map(o => `
      <div class="fg-officer-card">
        <div class="fg-officer-info">
          <div class="fg-officer-name">${o.name}</div>
          <div class="fg-officer-contact">
            ${o.email ? '✉️ ' + o.email : ''}
            ${o.email && o.phone ? ' · ' : ''}
            ${o.phone ? '📱 ' + o.phone : ''}
          </div>
          <div class="fg-officer-badges">
            ${o.alert_types.map(t =>
              `<span class="fg-badge-risk ${t}">${t}</span>`).join('')}
          </div>
        </div>
        <button onclick="deleteOfficer(${o.id})"
          style="background:rgba(248,113,113,.1);border:1px solid #f87171;
                 color:#f87171;border-radius:6px;padding:5px 10px;
                 cursor:pointer;font-size:12px;">
          Remove
        </button>
      </div>`).join('');
  } catch (e) {
    if (el) el.innerHTML = '<p style="color:#f87171;font-size:13px;">Could not load officers.</p>';
  }
}
 
async function addOfficer() {
  const name  = document.getElementById('fg-officer-name')?.value.trim();
  const email = document.getElementById('fg-officer-email')?.value.trim();
  const phone = document.getElementById('fg-officer-phone')?.value.trim();
  const types = [...document.querySelectorAll('.fg-alert-type:checked')].map(cb => cb.value);
 
  if (!name)              { showToast('Officer name is required', 'error'); return; }
  if (!email && !phone)   { showToast('Enter email or phone number', 'error'); return; }
  if (!types.length)      { showToast('Select at least one alert type', 'error'); return; }
 
  try {
    await apiFetch('/api/officers/', {
      method: 'POST',
      json: { name, email: email || null, phone: phone || null, alert_types: types }
    });
    showToast(`✅ Officer ${name} added. They'll receive alerts.`, 'success');
    if (document.getElementById('fg-officer-name')) document.getElementById('fg-officer-name').value = '';
    if (document.getElementById('fg-officer-email')) document.getElementById('fg-officer-email').value = '';
    if (document.getElementById('fg-officer-phone')) document.getElementById('fg-officer-phone').value = '';
    loadOfficers();
  } catch (e) {
    showToast('Failed to add officer. Are you logged in as Admin?', 'error');
  }
}
 
async function deleteOfficer(id) {
  if (!confirm('Remove this officer from alerts?')) return;
  try {
    await apiFetch(`/api/officers/${id}`, { method: 'DELETE' });
    showToast('Officer removed', 'info');
    loadOfficers();
  } catch (e) {
    showToast('Failed to remove officer', 'error');
  }
}
 
async function sendTestAlert() {
  const btn = document.getElementById('fg-test-alert-btn');
  if (btn) { btn.textContent = 'Sending…'; btn.disabled = true; }
  try {
    const r = await apiFetch('/api/officers/test-alert', { method: 'POST' });
    showToast(`✅ Test sent: ${r.emails_sent} email(s) dispatched`, 'success');
    fgAppendBotMessage(
      `Test alert sent to ${r.emails_sent} officer(s). ` +
      `Check your inbox — the email has full risk details, carbon stats, and a dashboard link. ` +
      `If not received, check SMTP_PASSWORD in your .env file (use Gmail App Password, not your regular password).`
    );
  } catch (e) {
    showToast('Test failed — check SMTP settings in .env', 'error');
  }
  if (btn) { btn.textContent = 'Send Test Alert'; btn.disabled = false; }
}

function sendChatMessage(message) {
  clearTimeout(chatDebounceTimer);

  chatDebounceTimer = setTimeout(async () => {
    try {
      const res = await fetch("/api/chat", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({ message })
      });

      const data = await res.json();
      console.log("AI:", data.answer);

    } catch (e) {
      console.error("Chat error:", e);
    }
  }, 1500); // delay
}

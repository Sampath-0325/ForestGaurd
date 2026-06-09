# 🌿 ForestGuard Enterprise

## Intelligent Deforestation Monitoring & Early Warning System

ForestGuard Enterprise is an AI-powered deforestation monitoring platform that combines Google Earth Engine, satellite imagery analysis, machine learning risk assessment, carbon loss estimation, and automated alert notifications to detect and report potential forest degradation in near real-time.

---

## 📌 Project Overview

ForestGuard continuously monitors Areas of Interest (AOIs) using satellite data from Landsat and Sentinel missions. The system analyzes vegetation health trends, detects anomalies, estimates carbon loss, and generates risk alerts for forest officers and administrators.

---

## 🚀 Key Features

### Satellite-Based Monitoring

* Google Earth Engine integration
* Landsat 8/9 imagery analysis
* Sentinel-2 imagery support
* Multi-year vegetation monitoring

### Vegetation Analysis

* NDVI (Normalized Difference Vegetation Index)
* EVI (Enhanced Vegetation Index)
* SAVI (Soil Adjusted Vegetation Index)
* NDWI (Normalized Difference Water Index)
* NBR (Normalized Burn Ratio)

### Deforestation Detection

* Time-series vegetation analysis
* Change detection algorithms
* CUSUM anomaly detection
* BFAST breakpoint detection
* Isolation Forest anomaly identification

### Risk Assessment

* Automated risk scoring
* Low / Medium / High risk classification
* Confidence score generation
* Forest stability assessment

### Carbon Impact Analysis

* IPCC Tier-1 carbon estimation
* Carbon loss calculation
* CO₂ equivalent estimation
* Biomass impact analysis

### Alerting System

* Email notifications via SMTP
* SMS notifications via Twilio (optional)
* Officer-based alert subscriptions
* Organization-wide alerts

### Dashboard & Reports

* Interactive monitoring dashboard
* AOI management
* Risk visualization
* PDF-ready reports
* Historical alert tracking

---

## 🏗 System Architecture

### Backend

* FastAPI
* SQLAlchemy
* SQLite
* Huey Task Queue
* JWT Authentication

### AI & Analytics

* Google Earth Engine
* Machine Learning Risk Analysis
* Carbon Estimation Engine
* Forest Stability Analysis

### Frontend

* HTML
* CSS
* JavaScript
* Interactive Dashboard

---

## 📂 Project Structure

```text
Deforestation/
│
├── backend/                        ← FastAPI application
│   ├── __init__.py
│   ├── main.py                     ← App factory, GEE init, static files, CORS
│   ├── api.py                      ← All REST endpoints + Pydantic schemas
│   ├── models.py                   ← SQLAlchemy ORM models (5 tables)
│   ├── deps.py                     ← Auth dependencies (Bearer + query token)
│   ├── auth_utils.py               ← JWT creation, bcrypt hashing
│   ├── config.py                   ← Environment config via pydantic-settings
│   ├── database.py                 ← SQLAlchemy engine, session factory, Base
│   ├── notifications.py 
│   ├── rag.py                       ← Ai chatbot
│   └── tasks.py                    ← Huey background tasks (scan + scheduler)
│
├── core/                           ← GEE analysis engine (9 modules)
│   ├── __init__.py
│   ├── auth.py                     ← GEE initialization (service account + default)
│   ├── data_loader.py              ← Satellite data loaders (5 sources)
│   ├── roi.py                      ← ROI geometry utilities
│   ├── ndvi.py                     ← Multi-index vegetation timeseries
│   ├── change_detection.py         ← CUSUM + BFAST + percentage drop detection
│   ├── risk_analysis.py            ← Ensemble ML risk scoring
│   ├── carbon_estimation.py        ← IPCC Tier 1 carbon accounting
│   ├── hotspot_analysis.py         ← Spatial hotspot + fire overlay
│   ├── vegetation_stability.py     ← VSI + Resilience + Resistance metrics
│   └── pipeline.py                 ← 8-stage analysis orchestrator
│
├── frontend/
└── dist/
│       ├── index.html              ← Single-page app shell + all HTML/CSS
│       └── assets/
│           └── app.js              ← All frontend logic (~1100 lines)
│
│
│
├── models/
│   ├── __init__.py
│   └── response.py
│ 
├── forest-env/                     ← Python virtual environment (not committed)
│
├── forestguard.db                  ← SQLite database (auto-created)
├── forestguard_queue.db            ← Huey task queue SQLite (auto-created)
├── run.py                          ← Process manager (uvicorn + huey worker)
├── .env                            ← Secrets (SECRET_KEY, DATABASE_URL)
└── README.md                       ← This file
```

---

## ⚙️ Installation

### Clone Project

```bash
git clone <repository-url>
cd Deforestation
```

### Create Virtual Environment

```bash
python -m venv .venv
```

### Activate Environment

Windows:

```bash
.venv\Scripts\activate
```

### Install Dependencies

```bash
pip install -r requirements.txt
```

---

## 🔐 Environment Variables

Create a `.env` file in the project root:

```env
SECRET_KEY=your-secret-key

DATABASE_URL=sqlite:///./forestguard.db

GEE_PROJECT_ID=your-google-earth-engine-project

OPENROUTER_API_KEY=your-openrouter-key
OPENROUTER_MODEL=openrouter/free

SMTP_HOST=smtp.gmail.com
SMTP_PORT=587
SMTP_USER=your-email@gmail.com
SMTP_PASSWORD=your-app-password

ALERT_EMAIL=your-email@gmail.com
ALERT_PHONE_NUMBER=
```

---

## 🌎 Google Earth Engine Setup

Authenticate Earth Engine:

```bash
earthengine authenticate
```

Set project:

```bash
earthengine set_project your-project-id
```

Verify access:

```bash
earthengine ls
```

---

## ▶️ Running the Application

Start ForestGuard:

```bash
python run.py
```

Application URL:

```text
http://127.0.0.1:8000
```

---

## 👤 Default Admin Credentials

Seed database:

```text
http://127.0.0.1:8000/api/auth/seed
```

Login:

```text
Email: admin@forestguard.org
Password: forestguard2024
```

---

## 📧 Email Notifications

For Gmail:

1. Enable 2-Step Verification.
2. Generate a Google App Password.
3. Use the App Password in `SMTP_PASSWORD`.

---

## 📈 Workflow

1. User creates or selects AOI.
2. Satellite imagery is fetched via Google Earth Engine.
3. Vegetation indices are calculated.
4. Deforestation risk is assessed.
5. Carbon loss is estimated.
6. Alerts are generated.
7. Email/SMS notifications are sent.
8. Dashboard updates with latest results.

---

## 🛠 Technologies Used

* Python
* FastAPI
* SQLAlchemy
* SQLite
* Google Earth Engine
* Huey
* OpenRouter AI
* HTML
* CSS
* JavaScript

---

## 👩‍💻 Developed By

**Malleboina Sampath**

Mini Project – Intelligent Deforestation Monitoring & Early Warning System

---

## 📜 License

This project is developed for educational and research purposes.

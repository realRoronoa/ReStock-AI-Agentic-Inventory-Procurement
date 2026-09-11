# ReStock AI Deployment Guide

This document is the authoritative, step-by-step deployment guide for **ReStock AI** to production on **Microsoft Azure** (compatible with Azure for Students / GitHub Student Developer Pack credits).

---

## 1. Architecture Overview

ReStock AI consists of three main decoupled tiers:

```
┌────────────────────────────────────────────────────────┐
│               Frontend: Azure Static Web Apps          │
│               - React 19 + TypeScript + Vite           │
│               - Static CDN hosting (Free Tier)         │
└───────────────────────────┬────────────────────────────┘
                            │ HTTPS (REST API calls)
                            ▼
┌────────────────────────────────────────────────────────┐
│            Backend: Azure App Service (Linux)          │
│            - Containerized FastAPI (Python 3.11)       │
│            - Non-root user, Uvicorn ASGI               │
│            - Spend guardrails & Human approval gate    │
│            - Server-side Gemini/OpenAI & RazorpayX     │
└──────────────┬───────────────────────────┬─────────────┘
               │ PostgreSQL connection     │ Webhooks (HMAC-SHA256)
               ▼                           ▲
┌──────────────────────────────┐ ┌─────────┴─────────────┐
│ Azure Database for PostgreSQL│ │  RazorpayX Payouts    │
│ - Flexible Server (B1ms)     │ │  (Test / Sandbox)     │
│ - Managed persistence        │ └───────────────────────┘
└──────────────────────────────┘
```

---

## 2. Prerequisites

1. **Azure CLI (`az`)**: Installed and logged in (`az login`).
2. **Docker**: Docker CLI or Docker Desktop for building/pushing container images (or use Azure Container Registry cloud builds).
3. **Node.js 22+ & npm**: For local frontend build verification.
4. **Python 3.11+ & venv**: For local backend testing and migration execution.
5. **External API Keys**:
   - Google Gemini API Key (`GEMINI_API_KEY`)
   - RazorpayX Test API Key & Secret (`RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, `RAZORPAY_ACCOUNT_NUMBER`, `RAZORPAY_WEBHOOK_SECRET`)

---

## 3. Environment Variables Reference

### Backend Environment Variables (`backend/.env` / Azure App Service Configuration)

| Variable | Required | Secret? | Default / Example | Purpose & Notes |
|---|---|---|---|---|
| `APP_NAME` | No | No | `ReStock AI` | Application name. |
| `ENVIRONMENT` | Yes | No | `production` | Runtime mode (`production`, `development`, `test`). |
| `LOG_LEVEL` | No | No | `INFO` | Logging level (`DEBUG`, `INFO`, `WARNING`, `ERROR`). |
| `PORT` | Yes (in Cloud) | No | `8000` | Port for Uvicorn ASGI web server. |
| `ALLOWED_ORIGINS` | Yes (in Prod) | No | `https://<frontend-app>.azurestaticapps.net` | Comma-separated allowed frontend domains for CORS. |
| `DATABASE_URL` | Yes | Yes | `postgresql+psycopg://user:pass@host:5432/restock_ai?sslmode=require` | PostgreSQL connection string using `psycopg` driver. |
| `LLM_PROVIDER` | Yes | No | `gemini` | AI provider choice: `gemini` or `openai`. |
| `GEMINI_API_KEY` | Yes (if Gemini) | **YES** | `AIzaSy...` | Google Gemini API Key. |
| `GEMINI_MODEL` | No | No | `gemini-2.5-flash` | Gemini model name. |
| `GEMINI_BASE_URL` | No | No | `https://generativelanguage.googleapis.com/v1beta` | Gemini API Base URL. |
| `OPENAI_API_KEY` | Yes (if OpenAI) | **YES** | `sk-...` | OpenAI API Key (if `LLM_PROVIDER=openai`). |
| `OPENAI_MODEL` | No | No | `gpt-4o-mini` | OpenAI model name. |
| `RAZORPAY_KEY_ID` | Yes (for Payouts)| **YES** | `rzp_test_...` | RazorpayX Key ID (Test Mode). |
| `RAZORPAY_KEY_SECRET` | Yes (for Payouts)| **YES** | `...` | RazorpayX Key Secret (Test Mode). |
| `RAZORPAY_ACCOUNT_NUMBER` | Yes (for Payouts)| **YES** | `7878780080333333` | RazorpayX Source Account Number. |
| `RAZORPAY_WEBHOOK_SECRET` | Yes (for Webhooks)| **YES** | `...` | HMAC-SHA256 Webhook Secret. |
| `RAZORPAY_BASE_URL` | No | No | `https://api.razorpay.com/v1` | Razorpay API Base URL. |
| `RAZORPAY_PAYOUT_MODE` | No | No | `IMPS` | Payout settlement mode (`IMPS`, `NEFT`, `RTGS`). |
| `RAZORPAY_PAYOUT_PURPOSE` | No | No | `vendor bill` | Purpose string recognized by RazorpayX. |
| `MAX_ORDER_SPEND_INR` | No | No | `10000` | Hard spending ceiling per individual order (INR). |
| `MAX_DAILY_SPEND_INR` | No | No | `25000` | Cumulative daily spend limit (INR). |
| `MAX_REORDER_QUANTITY`| No | No | `500` | Maximum units per reorder proposal. |
| `SPEND_DAY_TIMEZONE` | No | No | `Asia/Kolkata` | Timezone for rolling daily spend reset. |
| `FORECAST_HISTORY_DAYS`| No | No | `28` | Days of historical sales data provided to forecast agent. |

### Frontend Environment Variables (`frontend/.env.production` / Build-time)

| Variable | Required | Secret? | Example | Purpose & Notes |
|---|---|---|---|---|
| `VITE_API_BASE_URL` | Yes (in Prod) | **NO (Public)** | `https://restock-backend.azurewebsites.net` | Backend API URL. Baked into JS bundle at build time. |

> [!CAUTION]
> NEVER put backend secrets (`GEMINI_API_KEY`, `RAZORPAY_KEY_SECRET`, `DATABASE_URL`) into `VITE_*` variables. Any variable prefixed with `VITE_` is compiled into the browser bundle and publicly visible to all users.

---

## 4. Local Production Verification

Run the automated test suite and production build locally before deploying:

```bash
# 1. Backend tests (398 tests)
cd backend
python -m pytest

# 2. Frontend typecheck and build
cd ../frontend
npm run typecheck
npm run build
```

---

## 5. Azure Step-by-Step Deployment

### Step 5.1: Create Resource Group

```bash
az group create --name restock-ai-rg --location centralindia
```

### Step 5.2: Create Azure Database for PostgreSQL (Flexible Server)

```bash
az postgres flexible-server create \
  --resource-group restock-ai-rg \
  --name restock-ai-pg \
  --location centralindia \
  --admin-user restockadmin \
  --admin-password "<STRONG_PASSWORD>" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 16 \
  --public-access 0.0.0.0

# Create the application database
az postgres flexible-server db create \
  --resource-group restock-ai-rg \
  --server-name restock-ai-pg \
  --database-name restock_ai
```

### Step 5.3: Run Database Migrations

From your local machine or an Azure Cloud Shell with network access to the PostgreSQL server:

```bash
export DATABASE_URL="postgresql+psycopg://restockadmin:<STRONG_PASSWORD>@restock-ai-pg.postgres.database.azure.com:5432/restock_ai?sslmode=require"

cd backend
alembic upgrade head

# (Optional) Seed the initial catalogue and historical sales data:
python -m app.seed.seed
```

### Step 5.4: Deploy Backend Container to Azure App Service

#### Option A: Using Azure Container Registry (ACR)

```bash
# 1. Create ACR
az acr create --resource-group restock-ai-rg --name restockacr --sku Basic --admin-enabled true

# 2. Build and push backend image
az acr build --registry restockacr --image restock-backend:latest ./backend

# 3. Create App Service Plan (Linux B1)
az appservice plan create \
  --name restock-plan \
  --resource-group restock-ai-rg \
  --is-linux \
  --sku B1 \
  --location centralindia

# 4. Create Web App for Container
az webapp create \
  --resource-group restock-ai-rg \
  --plan restock-plan \
  --name restock-backend-api \
  --deployment-container-image-name restockacr.azurecr.io/restock-backend:latest

# 5. Configure App Settings (Environment Variables)
az webapp config appsettings set --resource-group restock-ai-rg --name restock-backend-api --settings \
  PORT=8000 \
  WEBSITES_PORT=8000 \
  ENVIRONMENT=production \
  LOG_LEVEL=INFO \
  ALLOWED_ORIGINS="https://restock-frontend.azurestaticapps.net,http://localhost:5173" \
  DATABASE_URL="postgresql+psycopg://restockadmin:<STRONG_PASSWORD>@restock-ai-pg.postgres.database.azure.com:5432/restock_ai?sslmode=require" \
  LLM_PROVIDER=gemini \
  GEMINI_API_KEY="<YOUR_GEMINI_API_KEY>" \
  GEMINI_MODEL="gemini-2.5-flash" \
  RAZORPAY_KEY_ID="<YOUR_RAZORPAY_KEY_ID>" \
  RAZORPAY_KEY_SECRET="<YOUR_RAZORPAY_KEY_SECRET>" \
  RAZORPAY_ACCOUNT_NUMBER="<YOUR_RAZORPAY_ACCOUNT_NUMBER>" \
  RAZORPAY_WEBHOOK_SECRET="<YOUR_RAZORPAY_WEBHOOK_SECRET>" \
  MAX_ORDER_SPEND_INR=10000 \
  MAX_DAILY_SPEND_INR=25000 \
  MAX_REORDER_QUANTITY=500 \
  SPEND_DAY_TIMEZONE="Asia/Kolkata"
```

### Step 5.5: Deploy Frontend to Azure Static Web Apps

```bash
# Create Static Web App
az staticwebapp create \
  --name restock-frontend \
  --resource-group restock-ai-rg \
  --location "eastasia" \
  --source https://github.com/<YOUR_GITHUB_USER>/ReStock-AI-Agentic-Inventory-Procurement \
  --branch main \
  --app-location "/frontend" \
  --output-location "dist" \
  --login-with-github
```

In your GitHub repository settings or Azure Static Web App configuration, set the build environment variable:
- `VITE_API_BASE_URL`: `https://restock-backend-api.azurewebsites.net`

Update the backend `ALLOWED_ORIGINS` with the newly assigned Static Web App default hostname:
```bash
az webapp config appsettings set --resource-group restock-ai-rg --name restock-backend-api --settings \
  ALLOWED_ORIGINS="https://<YOUR_STATIC_WEB_APP_URL>.azurestaticapps.net"
```

---

## 6. Health Check Verification

Once deployed, verify backend liveness and database reachability:

```bash
curl -fsS https://restock-backend-api.azurewebsites.net/health
```

Expected response payload (`200 OK`):
```json
{
  "status": "ok",
  "app": "ReStock AI",
  "environment": "production",
  "version": "1.0.0",
  "database": "up",
  "integrations": {
    "razorpayx_payouts_configured": true,
    "razorpayx_webhooks_configured": true,
    "llm_configured": true
  }
}
```

---

## 7. Webhook Configuration (RazorpayX)

1. Log in to the [RazorpayX Dashboard](https://x.razorpay.com/) (ensure **Test Mode** toggle is ON).
2. Navigate to **Settings** > **Webhooks** > **Add New Webhook**.
3. Set **Webhook URL**: `https://restock-backend-api.azurewebsites.net/api/webhooks/razorpayx`
4. Set **Secret**: Enter the exact secret string assigned to `RAZORPAY_WEBHOOK_SECRET`.
5. Select the following events:
   - `payout.processed` (triggers stock addition & settles order to `paid`)
   - `payout.failed` (marks order as `failed`, leaves stock unchanged)
   - `payout.reversed` (marks order as `reversed`, leaves stock unchanged for manual audit)
   - `payout.queued`, `payout.initiated`, `payout.pending` (intermediate tracking)
6. Save the webhook.

---

## 8. Troubleshooting

| Issue | Root Cause | Solution |
|---|---|---|
| `503 Service Unavailable` on `/health` | Database connection failed | Verify `DATABASE_URL`, ensure PostgreSQL firewall allows Azure IPs (`public-access 0.0.0.0` or Azure services enabled), and SSL mode is configured (`sslmode=require`). |
| CORS error in browser console | `ALLOWED_ORIGINS` does not match frontend origin | Update `ALLOWED_ORIGINS` in backend App Service to match exact frontend URL (including `https://` protocol, no trailing slash). |
| Proposal generation returns `503 AgentNotConfigured` | Missing `GEMINI_API_KEY` or `OPENAI_API_KEY` | Set `GEMINI_API_KEY` in App Service configuration and verify `LLM_PROVIDER=gemini`. |
| Payout approval returns `503 PaymentNotConfiguredError` | Missing RazorpayX credentials | Configure `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET`, and `RAZORPAY_ACCOUNT_NUMBER`. |
| Webhook returns `401 Unauthorized` | Invalid HMAC signature | Verify `RAZORPAY_WEBHOOK_SECRET` matches the secret registered in RazorpayX dashboard. |
| Frontend route refresh gives `404` | SPA fallback missing in static host | Azure Static Web Apps handles SPA routes automatically via `staticwebapp.config.json` or default routing fallback to `index.html`. |

---

## 9. Security Notes & Known MVP Limitations

- **Authentication**: Frontend includes interactive merchant profiles in `AuthContext.tsx`. Backend API endpoints are unauthenticated at the HTTP boundary. For the MVP demo, deploy on private/restricted URLs or use Azure App Service Easy Auth / basic gateway authentication before production Clerk integration.
- **Single Workspace**: ReStock AI operates as a single store / single workspace per database. Multi-tenant workspace partitioning is marked as a future production requirement.
- **Test Mode**: RazorpayX credentials must only be configured in **Test Mode** until live KYC and business verification are completed.
- **No Automatic Stock Updates**: Stock never increases on payout creation or user action; it increases exclusively upon cryptographic verification of `payout.processed` webhooks.

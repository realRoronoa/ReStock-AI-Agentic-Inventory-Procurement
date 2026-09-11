# Deployment Readiness Audit — ReStock AI

**Audit Date:** 2026-08-27  
**Repository State:** Clean, 387/387 backend tests passing, frontend TypeScript & Vite build passing.

---

### READY

1. **Backend Application Core & Tests**
   - FastAPI application starts cleanly (`app.main:app`).
   - 387 automated tests passing with 100% pass rate (`pytest`).
   - Unified error handling envelope (`{"error": {"code", "message", "details"}}`) with `X-Request-ID` request tracing.
   - Clean separation of domain logic, guardrails, and HTTP routing.

2. **Database & Migrations**
   - Fully decoupled ORM using SQLAlchemy 2.0.
   - Alembic migrations in place and current (`482dbc30816d (head)`).
   - Dual database compatibility: works on SQLite (development) and PostgreSQL (production via `psycopg`).
   - Idempotent seed script (`python -m app.seed.seed`).

3. **Frontend Production Build**
   - React 19 + TypeScript build succeeds cleanly (`tsc -b && vite build`) into `dist/`.
   - API client uses dynamic base URL (`VITE_API_BASE_URL` or relative path `/api`).

4. **Health Check & Observability**
   - Live health endpoint at `GET /health` executing a database round-trip check and returning integration status booleans without leaking credentials.

5. **Secrets & Security Hygiene**
   - `.gitignore` properly excludes `.env`, `backend/.env`, `frontend/.env`, SQLite `.db` files, and credentials.
   - Zero hardcoded API keys, tokens, or passwords committed to the repository.
   - Strict spending guardrails (`MAX_ORDER_SPEND_INR`, `MAX_DAILY_SPEND_INR`, `MAX_REORDER_QUANTITY`) enforced deterministically in Python.

---

### BLOCKERS

1. **Missing CORS Configuration in Backend (`CRITICAL for split deployment`)**
   - FastAPI in `backend/app/main.py` currently has no `CORSMiddleware` registered.
   - If the frontend is deployed to a separate domain (e.g. Vercel, Netlify, Cloudflare Pages) and backend to a different domain (Render, Railway, Fly.io, ECS), **all frontend API requests will be blocked by the browser's CORS policy**.

2. **Frontend Docker Nginx Configuration Lacks SPA Fallback Routing**
   - `frontend/Dockerfile` copies build assets to `nginx:alpine` without a custom `nginx.conf`.
   - The default Nginx configuration does not have `try_files $uri $uri/ /index.html;`. Any page refresh or direct navigation to `/inventory`, `/orders`, `/recommendations`, etc. will return `404 Not Found`.

3. **Docker Compose Missing Frontend Service**
   - `docker-compose.yml` configures `db`, `migrate`, `backend`, `seed`, and `inventory-check`, but does not define a `frontend` service for a complete full-stack local/containerized launch.

4. **Production Web Server Concurrency Configuration**
   - `backend/Dockerfile` CMD runs `uvicorn app.main:app --host 0.0.0.0 --port 8000`. For production, worker process configuration (or Gunicorn/Uvicorn worker count) should be parameterized for multi-core scaling and port customization via `$PORT`.

---

### REQUIRED ENVIRONMENT VARIABLES

| Variable | Classification | Default / Example | Purpose |
|---|---|---|---|
| `DATABASE_URL` | **Required for Startup** | `postgresql+psycopg://user:pass@host:5432/restock_ai` | Primary database connection string (PostgreSQL for prod). |
| `ENVIRONMENT` | **Required for Startup** | `production` | Environment mode (`production`, `development`, `test`). |
| `LOG_LEVEL` | **Optional** | `INFO` | Application log level (`INFO`, `DEBUG`, `WARNING`, `ERROR`). |
| `ALLOWED_ORIGINS` | **Required for Multi-Origin Prod** | `https://restock.example.com` | Comma-separated list of allowed frontend origins for CORS (or `*`). |
| `PORT` | **Optional (Host-dependent)** | `8000` | Port for the backend web server. |
| `LLM_PROVIDER` | **Required for AI** | `gemini` or `openai` | AI model provider selection. |
| `GEMINI_API_KEY` | **Required when LLM_PROVIDER=gemini** | `AIzaSy...` | API key for Google Gemini provider. |
| `GEMINI_MODEL` | **Optional (AI)** | `gemini-2.5-flash` | Gemini model name. |
| `OPENAI_API_KEY` | **Required when LLM_PROVIDER=openai** | `sk-...` | API key for OpenAI provider. |
| `OPENAI_MODEL` | **Optional (AI)** | `gpt-4o-mini` | OpenAI model name. |
| `OPENAI_BASE_URL` | **Optional (AI)** | `https://api.openai.com/v1` | OpenAI API compatible endpoint URL. |
| `RAZORPAY_KEY_ID` | **Required for RazorpayX** | `rzp_test_...` | RazorpayX API Key ID. |
| `RAZORPAY_KEY_SECRET` | **Required for RazorpayX** | `...` | RazorpayX API Key Secret. |
| `RAZORPAY_ACCOUNT_NUMBER` | **Required for RazorpayX** | `7878780080...` | RazorpayX Source Account Number for payouts. |
| `RAZORPAY_WEBHOOK_SECRET` | **Required for RazorpayX** | `...` | Secret for HMAC-SHA256 signature verification. |
| `MAX_ORDER_SPEND_INR` | **Optional (Guardrail)** | `10000` | Maximum spend limit per single order (in INR). |
| `MAX_DAILY_SPEND_INR` | **Optional (Guardrail)** | `25000` | Maximum cumulative daily spend limit (in INR). |
| `SPEND_DAY_TIMEZONE` | **Optional (Guardrail)** | `Asia/Kolkata` | Timezone for rolling daily spend reset. |
| `VITE_API_BASE_URL` | **Required for Frontend Build** | `https://api.restock.example.com` | Backend API URL baked into frontend build (empty if same origin). |

---

### DATABASE

- **Current Database**: SQLite (`backend/restock_ai.db`) for local development; PostgreSQL supported natively via `psycopg`.
- **Persistence**: SQLite files inside container layers are ephemeral unless volume-mounted. In production, a managed PostgreSQL instance is strongly recommended.
- **Migration Command**:
  ```bash
  alembic upgrade head
  ```
- **Seed Command**:
  ```bash
  python -m app.seed.seed
  ```
- **Recommendation**: **PostgreSQL is strongly recommended** for production to enable concurrent transactions, persistent storage across redeployments, and robust connection pooling.

---

### FRONTEND

- **Build Command**:
  ```bash
  npm run build
  ```
  *(runs `tsc -b && vite build`)*
- **Output Directory**: `frontend/dist/`
- **API URL Configuration**:
  - Controlled by `VITE_API_BASE_URL`.
  - If unset, requests use relative paths (`/api/...`), ideal for reverse-proxy setups (single origin).
  - If set to an external backend URL (e.g., `https://api.restock.example.com`), requests target that URL directly.

---

### BACKEND

- **Start Command**:
  ```bash
  uvicorn app.main:app --host 0.0.0.0 --port 8000
  ```
- **Port**: Defaults to `8000` (can bind to dynamic environment `$PORT`).
- **Health Endpoint**:
  `GET /health` — Returns `200 OK` (or `503 Service Unavailable` if database is down), with JSON payload checking DB connectivity and integration readiness.

---

### DOCKER

- **Backend Dockerfile**: Valid multi-stage image based on `python:3.11-slim` with non-root user `restock` and health check.
- **Frontend Dockerfile**: Valid multi-stage build (`node:22-alpine` -> `nginx:alpine`), but requires an SPA-friendly `nginx.conf` (`try_files $uri $uri/ /index.html;`) so route refreshes do not return 404.
- **docker-compose.yml**: Valid syntax; orchestrates Postgres, migrations, and backend service. Can be enhanced with frontend service for complete 1-command local deployment.

---

### SECURITY

- **Secrets**: No secrets are stored in Git. All secrets ingress through environment variables in `app.core.config.Settings`.
- **CORS**: Currently disabled (needs `CORSMiddleware` with configurable allowed origins).
- **Authentication Status**: Frontend demo auth is present; backend API endpoints are currently open / public (ready for single-tenant internal deployment or upstream gateway/reverse proxy auth).
- **Exposed Endpoints**:
  - `/health` (public, safe, no secrets leaked)
  - `/docs` / `/openapi.json` (Swagger documentation)
  - `/api/*` (Domain CRUD and actions)
  - `/api/webhooks/razorpay` (HMAC-SHA256 verified)
- **Production Risks**:
  - Running without CORS configured on split hosting will break API communication.
  - Razorpay payouts must only be used in Test/Sandbox mode until merchant credentials and KYC are finalized.

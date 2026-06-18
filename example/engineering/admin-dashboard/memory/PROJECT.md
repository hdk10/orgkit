# admin-dashboard — Project Memory

_Last updated: 2026-06-18_

## What it is

Internal ops dashboard: job status monitoring, client health metrics, API token management, and a partner-report viewer. Used exclusively by the internal team; not client-facing.

## Stack

- FastAPI (backend) + React 18 + Vite + TypeScript (frontend)
- Tailwind CSS (npm, not CDN — full-site build)
- Zustand (auth/session state) + React Query (server state)
- SQLite for local token store
- Docker (same container as api-service in prod)

## Directory layout

```
backend/
  main.py           # FastAPI app
  apps/
    job_monitor/    # Job status + health metrics app
    token_mgr/      # Client token management app
    _template/      # Copy this to add a new app
  shared/
    auth.py         # Platform IAM middleware — DO NOT MODIFY
    discovery.py    # Auto-mounts all apps in backend/apps/
frontend/
  src/
    apps/
      job-monitor/  # React app (hyphen slug, matches URL)
      token-mgr/
    shared/         # Platform UI — DO NOT MODIFY
```

## Adding a new app

1. Copy `backend/apps/_template/` → `backend/apps/<underscore_slug>/`; edit `app.yaml`.
2. Copy `frontend/src/apps/<hyphen-slug>/`; create `index.tsx` + `App.tsx`.
3. Register route in `frontend/src/App.tsx`.
4. Routes auto-mount at `/api/<slug>/` via `shared/discovery.py`.

## Auth notes

- `DEV_AUTH_BYPASS=1` in local `.env` skips IAM. **Never** add `CONTAINER_` prefix to this var or it will reach prod.
- For any app needing third-party OAuth (ad platforms etc.): exempt only the public `/oauth/callback` via `_is_public_api()`; the signed HMAC state pattern (see engineering ROLE.md) handles the rest.

## Running locally

```bash
# Backend
cd backend && python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
uvicorn main:app --reload

# Frontend (separate terminal)
cd frontend && npm install && npm run dev
```

## Known issues / open

- Mobile perf not benchmarked. Bundle size is heavy (anime.js + fonts). TBT likely > 500 ms on mobile.
- Lookalike audience lists in Google Ads are read-only via API — cannot remove programmatically. Clients must delete from the Google Ads UI; surface clear copy in the dashboard UI.

---

## Lessons captured inline

[LESSON]: Backend app dirs use **underscores** (`job_monitor`), frontend app dirs and URL slugs use **hyphens** (`job-monitor`). Mixing them breaks auto-discovery and route mounting. Enforce at PR review.

[GOTCHA]: Do NOT modify `backend/shared/` or `frontend/src/shared/`. These are platform code. App logic goes in `backend/apps/<slug>/` only. A previous edit to `shared/auth.py` to add an app-specific check caused a regression across all apps.

[PATTERN]: Self-contained offline demo HTML — train the model, embed records + Plotly chart inline, write `demo.html`. AirDrop to a tablet; no server required. See `generate_demo.py` for the pattern.

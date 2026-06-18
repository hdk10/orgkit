# api-service — Project Memory

_Last updated: 2026-06-18_

## What it is

Core platform API: accepts batches of hashed user IDs from clients, routes them through partner enrichment adapters, and returns scored segments. Multi-tenant; each client has an isolated token and device binding.

## Stack

- FastAPI + uvicorn (ASGI)
- PostgreSQL (primary store) + SQLite (per-job manifest)
- httpx AsyncClient for all outbound partner calls
- Playwright (optional, for any partner requiring browser-based auth)
- Docker (containerized for prod deployment)

## Architecture

```
POST /v1/enrich
  → auth middleware (Bearer + X-Device-ID)
  → job_manager.py  (creates job row, returns job_id)
  → worker.py       (async; fans out to partner adapters)
  → adapters/<partner>_adapter.py  (normalize response → canonical schema)
  → scorer.py       (loads joblib model, returns score + decile)
  → result stored in PostgreSQL, polled by client
```

## Key files

| File | Purpose |
|------|---------|
| `main.py` | FastAPI app, lifespan context (DB pool, HTTP client singleton) |
| `auth.py` | Bearer + X-Device-ID validation; JWT TTL ~1 hr |
| `worker.py` | Async fan-out to adapters; `asyncio.gather` pattern |
| `adapters/` | One file per data partner; normalize to canonical column names |
| `scorer.py` | Loads `.joblib` wrapper from data-science; calls `.predict(df)` |
| `manifest.py` | SQLite job manifest; records status, row counts, timings |
| `dashboard.py` | `localhost:8770` live dashboard for job monitoring |
| `enrich_batch.py` | CLI script for one-off batch enrichment; tqdm + ThreadPoolExecutor |

## Running locally

```bash
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
set -a; source .env; set +a
uvicorn main:app --reload
```

## Deployment

Docker image built via `Makefile`. Prod vars use `CONTAINER_` prefix — deploy pipeline strips the prefix and writes to `/data/.env.apps` on the server. Never give `DEV_AUTH_BYPASS=1` the `CONTAINER_` prefix.

## Known issues / open

- JWT refresh loop not built. Scripts do a fresh login per run. For jobs > 1 hr, the token will expire mid-run — add refresh logic before any overnight batch.
- DataVendor2 adapter assumes 24-hr token TTL but actual TTL is 12 hrs; will silently return stale data after half a day. Needs expiry check.

---

## Lessons captured inline

[GOTCHA]: `X-Device-ID` header is required alongside Bearer token on ALL internal API calls. Missing it returns `400 MISSING_DEVICE_ID`. Older helper scripts may be missing this header — patch before use.

[PATTERN]: One `httpx.AsyncClient` per process lifetime, not per request. Creating a new client per call wastes a full TCP+TLS handshake (~65 ms each). Singleton in `lifespan()` context gives keep-alive at ~33 ms p50.

[LESSON]: Always pass the full canonical seed as `--universe` to partner transforms. Without it, absent users are silently dropped instead of flagged. This was caught after a run that under-reported coverage by ~5%.

[GOTCHA]: Never name a utility script `inspect.py` — collides with Python stdlib `inspect` module. Use descriptive names: `probe_api.py`, `debug_log.py`.

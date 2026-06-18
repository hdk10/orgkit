# engineering Role Memory

_Auto-injected into Claude Code sessions inside `engineering/`. Last bootstrapped: 2026-06-01. Last reconciled: 2026-06-18._

## Mission

Engineering workbench for Acme — builds and maintains the API service, internal tooling, CRM apps, scrapers, and client-facing integrations. The focus is working software fast: CLI scripts, FastAPI services, and static HTML rather than over-engineered frameworks.

---

## Projects in this role

### Services

| Project | What it does | Stack |
|---|---|---|
| `api-service` | Core platform API: hashed-ID ingestion, match routing, enriched-score responses; multi-tenant auth | FastAPI, PostgreSQL, httpx, Docker |
| `admin-dashboard` | Internal ops dashboard: job status, client health, token management | FastAPI, React, Vite, TypeScript, Tailwind |
| `data-pipeline` | Nightly ETL that pulls raw partner feeds, normalizes columns, writes parquet for DS | Python, pandas, pyarrow, cron |
| `integrations` | Thin adapters for each data partner API (auth, pagination, rate-limit handling) | Python, httpx, per-partner `.env` |

---

## Best practices

- **`httpx` over `urllib`/`requests` for any HTTP-heavy script** — `urllib` pays ~65 ms TCP+TLS per call; `httpx.Client(http2=True)` with keep-alive drops to ~33 ms p50 (roughly 3× RPS). Use `httpx.AsyncClient` + `asyncio.gather` for concurrent fan-out.
- **Shared HTTP client singleton — never one-per-call** — Creating a new client per row/batch is the #1 performance bug. One client per script lifetime. In FastAPI: one browser/client per process, lock-guarded.
- **Per-project `venv`** for every Python project — `python3 -m venv venv && source venv/bin/activate`. Never install into system Python.
- **Path anchoring** — `Path(__file__).parent` for all file references. Never hardcode absolute paths. Venvs and symlinks break on any folder move.
- **SQLite for local state** — one `.db` file per project at the project root. No Postgres for single-user or offline tooling.
- **`tqdm` on every loop** — any script iterating over rows, batches, or pages must show a progress bar. No silent scripts.
- **Credentials via env vars** — `.env` at project root (gitignored), load with `set -a; source .env; set +a`. Never hardcode tokens or API keys.
- **`debug_log.py` layer for browser scrapers** — schema drift detection, raw response capture, screenshots on parse failure. Mandatory for any scraper that hits an undocumented internal API, because those endpoints rotate.
- **Single-file scripts preferred for CLIs** — one `scrape.py` / `enrich.py` is better than a package until the project graduates to a service.
- **FastAPI duality** — CLI script (`scrape.py`) + service wrapper (`service.py`) in the same project. Service mirrors standard API envelope shape: `{data, includes, meta}`.
- **Gitignore data files** — `*.csv`, `*.db`, `*.parquet`, `*.xlsx` stay out of the repo. Credential files (`.env`, `tokens.json`) always gitignored.
- **Backend app dirs use underscores, frontend slugs use hyphens** — `backend/apps/data_export/` vs URL slug `data-export`. Mixing them breaks auto-discovery.
- **Kill + relaunch after every change for hot-reload-free apps** — `pkill -f "python app.py"` then relaunch on the relevant page before testing.
- **`google-ads` lib is sync; wrap with `run_in_threadpool`** — the official Python lib has no async support. Call via `await run_in_threadpool(fn, ...)` inside FastAPI async routes to avoid blocking the event loop.
- **Signed HMAC state in OAuth callbacks** — encode `(user_id, email)` in the OAuth `state` parameter using HMAC. The public callback verifies and attributes the token to the right user without trusting the URL.

---

## Patterns

### httpx async gather for concurrent batch jobs

```python
async with httpx.AsyncClient(http2=True) as client:
    results = await asyncio.gather(*[client.post(url, json=row) for row in batch])
```

`AsyncClient.post` fanned out via `asyncio.gather` beats `ThreadPoolExecutor` + sync httpx (~3× RPS). Threads + sync waste a connection per worker.

### /tmp progress file + stdlib live dashboard

`/tmp/<project>_progress.txt` written by workers + zero-dep `http.server.ThreadingHTTPServer` renders live dashboard on localhost. No frameworks required.

### FastAPI service scaffold

```
service.py          # FastAPI app, lifespan manages shared client/browser
cli.py              # CLI + importable parse/process functions
debug_log.py        # Rotating logger, raw response capture, schema-drift ops
<name>.db           # SQLite, schema created at startup (SCHEMA constant)
requirements.txt    # Pin versions
venv/               # Always local, never committed
```

### CLI script with tqdm + ThreadPoolExecutor

```python
from tqdm import tqdm
from concurrent.futures import ThreadPoolExecutor

WORKERS = 10
BATCH_SIZE = 100

with ThreadPoolExecutor(max_workers=WORKERS) as pool:
    for result in tqdm(pool.map(fn, batches), total=len(batches)):
        ...
```

### Two-layer auth (platform IAM + third-party OAuth)

Platform middleware gates all routes. For apps needing third-party OAuth (Meta, Google):
1. Exempt only the public `/oauth/callback` via `_is_public_api()`.
2. The `/connect` endpoint (authenticated) generates a signed state + returns the auth URL.
3. Public callback verifies state, exchanges code, stores token in app-specific SQLite table keyed by `user_id`.

Pattern used identically for every partner integration.

### Static demo generation

Generate once → self-contained HTML with all data + JS embedded. Useful for tablet offline demos: `generate_demo.py` trains the model, embeds sample records + chart inline, writes `demo.html`. No server needed.

---

## Anti-patterns

- **Never auto-commit or auto-push** — batch changes, surface what's ready, wait for explicit approval.
- **Never hardcode absolute paths** — they break on any other machine. Use `Path(__file__).parent`.
- **Don't skip the debug layer for browser scrapers** — the first endpoint rotation causes silent data loss with no artifacts to diagnose. Always capture raw responses.
- **Don't share a single service Playwright context across threads** — use a lock (`asyncio.Lock()`) to serialize. Without it, concurrent requests corrupt the context state.
- **Don't mock DB in tests** — use real SQLite; mock the HTTP layer instead. Real schema tests catch column-name drift early.
- **Don't put app-specific docs in the repo root** — they go in `backend/apps/<slug>/`. Only platform-wide docs belong in `docs/` or root.
- **Don't copy/paste long OAuth auth URLs from chat** — chat line-wraps can inject whitespace into query params (`redirect_uri` breaks silently). Always open from the shell directly.

---

## Gotchas

- `[api-service]` — JWT TTL is ~1 hour. Scripts do a fresh login per run. On long batch jobs (> 1 hr) the token will expire mid-run; add refresh logic before running overnight batches.
- `[api-service]` — `X-Device-ID` header is required alongside Bearer token on all internal API calls. Missing it returns `400 MISSING_DEVICE_ID`. Older helper scripts may be missing this; patch before use.
- `[admin-dashboard]` — Backend app dirs use **underscores** (`partner_reports`), frontend app dirs and URL slugs use **hyphens** (`partner-reports`). Mixing them breaks auto-discovery and route mounting.
- `[integrations]` — `DEV_AUTH_BYPASS=1` skips platform IAM locally. Never give it the `CONTAINER_` prefix or it reaches prod. All prod secrets must use the `CONTAINER_` prefix — the deploy pipeline strips it and writes to `/data/.env.apps` on the server.
- `[integrations]` — Partner API tokens for DataVendor1 expire after 12 hours, not 24. The integration script assumes 24 h and will silently return stale data after half a day. Add expiry check.
- `[integrations]` — Never name a script `inspect.py` — collides with Python stdlib `inspect` module. Use descriptive names (`probe_api.py`, `debug_log.py`).
- `[admin-dashboard]` — `google-ads` lib `membership_life_span` for Customer Match lists is capped at 540 days. Values > 540 return a silent API error in testing.

---

## Tools / stacks

### Python

- **FastAPI** — API services
- **Playwright** (sync + async) — browser automation for any scraper targeting a JS-heavy site
- **httpx** — preferred HTTP client; `http2=True` for keep-alive; `AsyncClient` + `asyncio.gather` for concurrent async batch jobs
- **uvicorn** — ASGI server for FastAPI services
- **SQLite** (`sqlite3` stdlib) — local state for scrapers and batch tools
- **tqdm** — progress bars on every script with loops; mandatory
- **concurrent.futures.ThreadPoolExecutor** — multi-threaded batch enrichment
- **pywebview** — desktop GUI shell for internal tools
- **pandas** — data wrangling in enrichment scripts and demo generation

### JavaScript / TypeScript

- **React 18 + Vite** — frontend for `admin-dashboard`
- **TypeScript** — used in `admin-dashboard` frontend
- **Tailwind CSS** — utility-first CSS; CDN for static prototypes; npm for production
- **Zustand** — client-side state in dashboard apps
- **React Query** — server state in dashboard apps

### Infrastructure

- **Docker** — containerization for API service and admin dashboard
- **Playwright Chromium** — installed via `python -m playwright install chromium` per project
- **Tailwind CLI** — for full-site builds; `tailwind.config.js` → `tailwind.output.css`

---

## Vocabulary / glossary

| Term | Meaning |
|---|---|
| **CDP** | Chrome DevTools Protocol — connects Playwright to a running browser instance; preserves fingerprint |
| **schema drift** | When a third-party internal API changes its response shape, breaking the parser; caught by `debug_log.py` |
| **debug layer** | `debug_log.py` — captures raw responses, logs parse errors, detects new/missing fields; mandatory for scrapers |
| **CONTAINER_ prefix** | Convention in the deploy pipeline — secrets with this prefix are stripped and written to `/data/.env.apps` on the server; only `CONTAINER_`-prefixed vars reach production |
| **persistent context** | Playwright browser context that persists cookies/storage to disk; avoids re-login on every run |
| **slug** | URL-safe app identifier; hyphenated in frontend + URLs, underscored in backend Python dirs |
| **AirDrop deploy** | Deployment model for offline demos — single self-contained HTML file sent to tablets; no server required |
| **pywebview** | Python library wrapping a native OS webview; JS ↔ Python bridge via `js_api` / `evaluate_js` |

---

## Open questions

- **api-service** — Token refresh loop not yet built. Fresh login per script run works for short jobs; needs refresh for overnight batches.
- **integrations** — DataVendor2 pagination is undocumented. Current implementation walks pages until empty; unclear if there is an authoritative last-page signal.
- **admin-dashboard** — Mobile perf not benchmarked. TBT likely high due to bundle size.

---

## Solved problems index

| If you need to... | Look at | Why |
|---|---|---|
| Build a concurrent batch enrichment job with progress | `engineering/api-service/enrich_batch.py` | `tqdm` + `ThreadPoolExecutor`, correct auth headers, progress-file pattern |
| Wrap a scraper in a FastAPI service with shared browser | `engineering/api-service/service.py` | Lock-guarded async browser, lifespan management |
| Store scraped data in SQLite with schema-at-startup | `engineering/api-service/db.py` | `SCHEMA` constant, `CREATE TABLE IF NOT EXISTS`, single `.db` file |
| Generate a self-contained offline demo HTML | `engineering/admin-dashboard/generate_demo.py` | Trains model, embeds records + chart inline; no server at runtime |
| Add a new app to the platform | `engineering/admin-dashboard/backend/apps/_template/` | Copy template, edit `app.yaml`; routes auto-mount via `discovery.py` |

---

## How to contribute

- Write inline `[LESSON]: <text>` / `[PATTERN]: <text>` / `[GOTCHA]: <text>` / `[TOOL]: <text>` in any `memory/PROJECT.md` or commit message — the Stop hook scrapes these automatically.
- Deeper insights are auto-reconciled into this file by `/role-promote engineering` (fired automatically when the brain is > 7 days stale with pending activity).

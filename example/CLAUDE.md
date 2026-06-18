# Global Brain — Acme SaaS

_This is the GLOBAL brain. It is always injected, regardless of which role folder the session opens in. Keep it concise — role-specific detail lives in `<role>/memory/ROLE.md`._

---

## Who we are

Acme builds a B2B SaaS platform that helps mid-market companies activate their customer data — matching first-party lists against third-party enrichment sources to power targeted outreach, fraud prevention, and churn prediction. Small founding team; everyone wears multiple hats.

## Product

- **Acme Platform** — cloud-hosted. Customers upload hashed user IDs; we match, enrich, and return scored segments.
- **Acme Enclave** — on-prem variant for customers who cannot send data off-site (financial services, healthcare). Docker / K8s deploy; sensitive data stays inside the client's environment; we receive only pseudonymized IDs.

## Tech north star

Working software fast. CLI scripts and FastAPI services over heavy frameworks. `httpx` over `requests`. SQLite for local state. `tqdm` on every loop. Single-file scripts until complexity earns a package.

## How we work

- Every project lives under `<role>/<project>/`, NEVER at the repo root.
- `.org/roles.json` is the canonical role → folder map. `sync_org.py` regenerates `ORG.md` on every SessionStart and Stop.
- New project → ask which role, create under that role, add `memory/PROJECT.md`.
- Git: never auto-commit. Batch changes, surface what's ready, wait for explicit approval.
- Never hardcode absolute paths in scripts. Anchor with `Path(__file__).parent`.
- Sub-agents: default model = Sonnet. Parallelize freely; keep main thread lean.

## Roles

| Role | What it does |
|------|-------------|
| `engineering` | Backend services, internal tools, client integrations |
| `data-science` | ML models, experiments, feature pipelines |
| `design` | Pitch decks, PDF reports, document generation |
| `growth` | GTM assets, partner decks, audience tooling |

## Brand tokens (design)

- Primary green: `#1A9E78`
- Dark background: `rgb(12, 20, 38)`
- Heading font: Kulim Park Bold
- Body font: Lato Regular / Bold
- Code chips: JetBrains Mono

## Credentials / secrets

All credentials live in per-project `.env` files (gitignored). Never commit tokens, keys, or passwords. Load with `set -a; source .env; set +a`.

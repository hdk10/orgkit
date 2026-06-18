# orgkit as a Claude Code plugin

Two install paths. Both give you the same 9 slash commands and hook behavior.

## Path A — Plugin install (recommended to try first)

From inside Claude Code:

```text
/plugin marketplace add hdk10/orgkit
/plugin install orgkit@orgkit
```

Commands are namespaced as `/orgkit:*`. Then run `/orgkit:orgkit-init` once per repo to scaffold the `.org/` memory structure.

To test from a local checkout instead:

```bash
claude --plugin-dir ./orgkit
```

## Path B — Clone + installer (full control)

```bash
git clone https://github.com/hdk10/orgkit.git
cd orgkit
python3 setup.py            # interactive: fresh scaffold or migrate existing repo
```

Useful flags:

```bash
python3 setup.py --target ~/work --fresh     # scaffold into ~/work
python3 setup.py --target ~/work --migrate   # migrate an existing repo
python3 setup.py --install-cron --weekly     # also install periodic reconcile job
```

---

## The 9 commands

| Command | What it does |
|---------|-------------|
| `orgkit-init` | Onboard the current repo — runs the interactive installer |
| `orgkit-analyze` | Read-only preview of what role mapping orgkit would create |
| `orgkit-migrate` | Reorganize an existing repo under roles (dry-run first) |
| `capture` | Distill session learnings into the current role's queue |
| `role-promote` | Reconcile a role's memory — merge, dedupe, prune |
| `new-project` | Scaffold a new project under a role |
| `org-status` | Roles, drift, stale/pending brains at a glance |
| `org-map` | Render a shareable org-chart of the repo |
| `orgkit-doctor` | Health-check engine, config, brains, and hooks |

---

## How the two paths differ

| | Plugin (Path A) | Clone (Path B) |
|---|---|---|
| Slash commands | `/orgkit:*` from plugin | `.claude/commands/` in the target repo |
| Engine location | Plugin cache (auto-updated) | Vendored into the repo's `.org/` |
| Hooks source | Plugin `hooks/hooks.json` | Registered in settings pointing to `.org/` |
| Onboarding | Run `/orgkit:orgkit-init` once per repo | `setup.py` does it interactively |
| Updates | `/plugin update orgkit@orgkit` | `git pull` + re-run `setup.py` |

<details>
<summary>Plugin layout</summary>

```text
orgkit/
├── .claude-plugin/
│   ├── plugin.json          # manifest
│   └── marketplace.json     # single-plugin marketplace
├── commands/                # 9 slash commands → /orgkit:<name>
├── hooks/
│   └── hooks.json           # SessionStart / UserPromptSubmit / Stop
├── orgkit/                  # engine (sync_org, role_inject, role_digest, setup core)
└── setup.py                 # interactive installer
```

Hooks fire:
- `SessionStart` → `sync_org.py` + `role_inject.py` (inject role/project memory)
- `UserPromptSubmit` → `role_inject.py`
- `Stop` → `sync_org.py` + `role_digest.py scrape` (auto-promote `[LESSON]`/`[PATTERN]`/`[GOTCHA]`/`[TOOL]` tags)

</details>

Built against the [Claude Code plugin spec](https://code.claude.com/docs/en/plugins-reference). Validated with `claude plugin validate .`.

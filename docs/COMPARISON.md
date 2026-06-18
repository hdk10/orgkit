# orgkit vs the alternatives

Honest comparison. orgkit isn't always the right pick.

## Feature table

| | orgkit | One big `CLAUDE.md` | Cursor rules | mem0 / Letta | ChatGPT / Claude built-in memory |
|---|---|---|---|---|---|
| **Context scoping** | Per-role + per-project | One bucket, always | Per-repo only | Semantic — retrieves by similarity | Vendor-managed single bucket |
| **Auto-injects relevant context** | Yes — global + current role only | No — you manage the file | No — attached per-repo, not per-task | Yes — vector search | No — vendor decides what surfaces |
| **Self-maintaining** | Yes — capture on stop, periodic reconcile/dedupe | No — you edit it or it rots | No — manual | Yes — embeddings auto-update | Partial — vendor appends; you can't curate |
| **Captures lessons, not just rules** | Yes — `[LESSON]`/`[GOTCHA]`/`[PATTERN]` auto-promoted | Possible, manual | No — rules only | Yes | Partial |
| **Your files, your git repo** | Yes | Yes | Yes | No — lives in a vector DB | No — lives on vendor servers |
| **Versioned with git** | Yes | Yes | Yes | No | No |
| **Needs API key / external service** | No — uses your session model | No | No | Yes — API key + vector DB | Yes — tied to subscription |
| **Works in Claude Code** | Yes, built for it | Yes | No — Cursor format | No native integration | Partial — no per-role or per-project scope |
| **Semantic search across all memory** | No — structured, not vector | No | No | Yes — its superpower | Partial |
| **Easy to inspect / undo** | Yes — `--rollback`, `MIGRATION.md`, `.bak` | Yes | Yes | No | No |
| **Install complexity** | `python3 setup.py` — stdlib only | None | None | Docker + DB + SDK + API key | None |

---

## Where others genuinely win

**One big `CLAUDE.md`** — perfectly fine if you have one kind of work and a small, stable set of facts. Orgkit is overkill for 20 items. The cost shows at scale: six roles × 2k tokens each loads 12k tokens of context you mostly don't need.

**Cursor rules** — excellent for repo-level coding conventions. Not memory; standing instructions. Don't capture learnings, don't reconcile, don't travel across repos. Right tool for "give this repo persistent style rules."

**mem0 / Letta** — strongest alternative for semantic search across a large, heterogeneous memory corpus. If you need "retrieve anything related to X" across thousands of facts, vector search wins. Trade-off: vector DB, API key, running service, memory lives outside git. Worth it at scale; overhead usually isn't justified for solo operators.

**ChatGPT / Claude built-in memory** — zero setup. Limits: vendor-defined opacity, no per-role scoping, no git versioning, not tied to your codebase. Fine for casual use ("remember my preferences"). Falls short for multi-role operations where you want auditable, scoped memory you own.

---

## One-line summary

- **One big file** — simplest; scales linearly with roles (and that's a problem).
- **Cursor rules** — conventions per repo, not memory, not Claude Code.
- **mem0 / Letta** — powerful semantic recall, but it's infrastructure; orgkit is files + git.
- **Built-in memory** — zero-setup, vendor-owned, one bucket, opaque.

orgkit is for structured, role-scoped, self-maintaining memory that lives in your repo, travels with your code, and needs no service running.

→ [README](../README.md) · [How it works](HOW-IT-WORKS.md)

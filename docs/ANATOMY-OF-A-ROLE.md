# Anatomy of a role

A `ROLE.md` is the brain for one function — engineering, growth, design, whatever you do. It loads **only when you're working in that role**, so it can be rich without taxing every session.

---

## Shape of a `ROLE.md`

These are the sections `setup.py` scaffolds (`/orgkit-init`). Names are a convention, not a hard schema — adapt as the role matures.

```markdown
# Engineering — Role Memory

## Context
What this role covers and how it operates.

## Best practices
Standing rules and conventions for this kind of work.

## Patterns
Reusable "when X, do Y" approaches proven across projects.

## Gotchas
Traps — surprises that bit you once and must never bite again.

## Tools / stacks
The stack, commands, and configs this role relies on.
```

`/role-promote` may also add an **Index** section — one line per project under this role — as it reconciles.

---

## Section guide

| Section | What belongs here |
|---|---|
| **Context** | Scope and operating mode of this role |
| **Best practices** | Standing rules; the constitution of the role |
| **Patterns** | "When X, do Y" — proven approaches across projects |
| **Gotchas** | The painful surprises; never repeat them |
| **Tools / stacks** | Build/deploy commands, linter config, key env vars |
| **Index** *(reconcile adds)* | One line per project: what it is + path to its `PROJECT.md` |

**Where does a fact live?** At the highest level where it's still always true. Cross-project truths belong in `ROLE.md`; one-off quirks in `PROJECT.md`; identity in `CLAUDE.md`.

---

## Inline tag convention

Drop tags as you work — in conversation or in files. On session stop, `role_digest.py` scrapes them and appends bullets **directly into the matching `ROLE.md` section**. No manual editing required.

| Tag | Use it for | Lands in |
|---|---|---|
| `[LESSON]` | Something learned the hard way | `## Best practices` |
| `[PATTERN]` | A reusable "when X, do Y" approach | `## Patterns` |
| `[GOTCHA]` | A trap or sharp edge to avoid | `## Gotchas` |
| `[TOOL]` | A tool, command, or config worth remembering | `## Tools / stacks` |

**Example:**

```text
[GOTCHA]: staging DB resets every Sunday 02:00 UTC — never demo on Mondays.
[PATTERN]: for any new worker, add a /tmp progress file so the main thread can render a bar.
[TOOL]: `make deploy-staging` handles migrations; the raw deploy command does not.
```

After appending to `ROLE.md`, the Stop hook also writes a brief "files touched" stub + transcript pointer to `_pending.md`. This is a pointer list for `/role-promote` to find the full context — it is not the insight store itself.

---

## How `/role-promote` reconciles

Runs through your **session model** (no API key). Reads `ROLE.md` + `_pending.md` + changed files + the session conversation, then:

- **Merge** — new notes about an existing topic are folded in, not stacked as duplicates.
- **De-duplicate** — two notes saying nearly the same thing become one.
- **Declutter** — notes a newer one supersedes are pruned.
- **Rebuild index** — the project index is updated as projects come and go.
- **Shrink-guard + `.bak`** — saves `.bak` before rewriting; rejects any reconcile that would shrink the file past 60% of its previous size.

Triggered manually (`/role-promote ‹role›`) or by a `SessionStart` nudge when the role is >7 days stale with pending activity.

---

→ Back to [README](../README.md) · See also [How it works](HOW-IT-WORKS.md)

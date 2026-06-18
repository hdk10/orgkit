---
description: Safely migrate existing folders into role directories — dry-run preview first, auto-fix literal path strings, then the model handles import statements and relative paths it flags. Never claims success silently.
argument-hint: --role-map '{"folder":"role",...}'  [--target <repo>]
---

You are the **orgkit migration assistant**. Your job is to move project folders under role directories safely — showing the user a complete plan before touching anything, deterministically fixing the easy references, and using your own judgment for the hard ones (import statements, relative paths) that regex can't handle safely.

The orgkit engine is installed in the target repo at `.org/`. The target repo root is `${CLAUDE_PROJECT_DIR}` (fall back to `$PWD`).

Resolve engine path at each step — prefer the installed copy, fall back to the plugin:

```bash
MIGRATE_PY="${CLAUDE_PROJECT_DIR:-$PWD}/.org/migrate.py"
if [ ! -f "$MIGRATE_PY" ]; then
  MIGRATE_PY="${CLAUDE_PLUGIN_ROOT}/orgkit/migrate.py"
fi
```

---

## Step 1 — Run the dry-run and show the full preview

Parse the role map from `$ARGUMENTS` yourself (e.g. `--role-map '{"folder":"role",...}'`). Do NOT splice `$ARGUMENTS_ROLE_MAP` — that variable does not exist; only `$ARGUMENTS` is available.

If `$ARGUMENTS` is empty or contains no role map, first run the scan to discover unmapped folders:

```bash
python3 "$MIGRATE_PY" \
  --target "${CLAUDE_PROJECT_DIR:-$PWD}" \
  --scan
```

Then use **AskUserQuestion** to ask the user how they want to assign each unmapped folder to a role (e.g. `{"api-thing": "engineering", "pitch-deck": "design"}`). Never guess role assignments. Store the resulting JSON in your reasoning — you will pass it as a CLI argument in subsequent steps.

Once you have the role map (as a JSON string, e.g. `'{"api-thing":"engineering"}'`), re-run with `--dry-run` and present the output in a clear table:

```bash
python3 "$MIGRATE_PY" \
  --target "${CLAUDE_PROJECT_DIR:-$PWD}" \
  --role-map '<role_map_json>' \
  --dry-run
```

| Folder | New location | Action |
|--------|-------------|--------|
| `api-thing/` | `engineering/api-thing/` | will move |

And a references table:

| File | Line | Category | Will be… |
|------|------|----------|----------|
| `README.md` | 12 | `literal_path` | auto-fixed |
| `src/utils.py` | 3 | `import` | **model review needed** |
| `scripts/run.sh` | 7 | `relative_path` | **model review needed** |

---

## Step 2 — Confirm with the user

Use **AskUserQuestion**: "Here's the full migration plan above. Shall I proceed? (yes / no / let me adjust the role map)"

Do NOT proceed without an explicit yes. If the user wants to adjust, go back to Step 1.

---

## Step 3 — Apply the move + auto-fix literal path strings

```bash
python3 "$MIGRATE_PY" \
  --target "${CLAUDE_PROJECT_DIR:-$PWD}" \
  --role-map '<role_map_json>'
```

(No `--dry-run` flag this time — this is the live run.)

Report what moved and how many literal-path strings were rewritten.

---

## Step 4 — Handle the needs_review list (model judgment)

Read `MIGRATION.md` at the repo root. Find the section **"⚠ References needing manual/AI review"**.

For **each flagged hit**:

1. Open the file at the listed line.
2. Read enough surrounding context (±10 lines) to understand the import or path.
3. Compute the correct new path/import after the move.
4. Show the user a diff:

   ```
   File: src/utils.py  line 3
   - from oldproj.helpers import foo
   + from engineering.oldproj.helpers import foo
   ```

5. Use **AskUserQuestion** to confirm: "Apply this fix? (yes / skip / let me edit manually)"

   - If yes: apply the edit using your Edit tool.
   - If skip: note it as unresolved in your final report.
   - If manual: note it as deferred.

Handle all flagged hits before proceeding to Step 5.

---

## Step 5 — Verify zero DANGLING references

After applying all confirmed fixes, run the migration tool's read-only `--verify` mode. This greps for references that still point at each folder's **old pre-move location**:

1. Prefixed old-form refs — `reponame/<folder>` or `/abs/repo/<folder>` NOT followed by the role segment — in **all** file types.
2. Bare repo-rooted refs — `<folder>/...` at the start of a path token (e.g. `python3 apiproj/main.py`) — in **shell / Makefile / yaml / py files only**.

It deliberately does NOT re-plan moves and does NOT flag the new `reponame/<role>/<folder>` paths or the moved folder's own internal files. Call the CLI directly — do NOT use an inline heredoc (single-quoted heredocs suppress `${}` expansion and pass literal strings to Python):

```bash
python3 "$MIGRATE_PY" \
  --target "${CLAUDE_PROJECT_DIR:-$PWD}" \
  --role-map '<role_map_json>' \
  --verify
```

`--verify` exits `0` when it finds no dangling refs, and exits `1` while listing each dangling hit (`file:line  [category]  text`) otherwise.

**A zero result is necessary but NOT sufficient — it does not prove the migration is clean.** `--verify` only catches (a) prefixed `abs`/`reponame` old-form refs and (b) bare `<folder>/...` refs in shell/Makefile/yaml/py files. It does **not** catch bare `<folder>/...` refs in other file types (e.g. `.md`, `.txt`, `.json`) or non-path mentions of the folder name. Those surface in `MIGRATION.md` under the `other` needs_review category — do not skip Step 4 / the needs_review review just because `--verify` is green. Report `--verify`'s zero as "no dangling refs of the forms verify checks", not as "migration proven clean".

If `--verify` is not supported by the installed engine (older copy), grep directly for the OLD path forms only — never the bare folder name, which false-positives on the new location and the folder's own files:

```bash
# For each "<folder>": "<role>" pair, search for old-form refs only.
# reponame/<folder> not followed by the role dir, and abs /repo/<folder> likewise.
grep -rnE "(^|[^A-Za-z0-9_/-])(<reponame>|/abs/path/to/<reponame>)/<folder>([/\"'[:space:])]|$)" \
  --exclude-dir=.git --exclude-dir=node_modules \
  --exclude=MIGRATION.md --exclude=FIX_LIST.md \
  "${CLAUDE_PROJECT_DIR:-$PWD}"
```

Exclude anything already living under `<role>/<folder>/` and any hit where the segment after the prefix is `<role>/`. Report the output verbatim — do not interpret it as success until it explicitly shows zero remaining dangling references.

---

## Step 6 — Final report

Present a clean summary:

- Folders moved: N
- Literal path strings auto-fixed: N (across N files)
- Import / relative-path / other references resolved by model: N
- Unresolved references (skipped or deferred): N (list them explicitly)
- Dangling references after re-scan: N (list them if any)

If **any** references are unresolved or dangling, say so explicitly — never claim migration is complete if work remains.

---

## Constraints

- **Never** claim all references are fixed without completing the re-scan in Step 5.
- **Never** apply edits to files the user said to skip.
- **Never** move folders without the user confirming the dry-run plan.
- All paths are repo-relative. Do not hardcode `/Users/…` or any absolute path in outputs.
- If `migrate.py` errors, report the exact error. Do not silently continue.
- This is a write operation. Treat it with care.

---
description: Scaffold a new project under an existing role — creates the folder, memory/PROJECT.md, updates roles.json, and regenerates ORG.md.
argument-hint: <role/project-name>  (or leave blank to be prompted)
---

You are scaffolding a new project into the org memory system. Every piece of work belongs under a role — never at the repo root.

## Steps

1. **Determine the project name.** If `$ARGUMENTS` was passed in the form `<role>/<project>`, parse it. Otherwise use the **AskUserQuestion** tool to ask: "What should the project folder be named? (lowercase-hyphenated, e.g. `my-new-thing`)"

2. **Determine the role.** If role was parsed from `$ARGUMENTS`, use it. Otherwise:
   - Read `.org/roles.json` and list all keys under `roles` with their `desc`.
   - Use **AskUserQuestion** to present them as a numbered list plus a final option: "Create a new role". Wait for selection.
   - If "Create a new role": ask for the role name (lowercase, e.g. `analytics`) and a one-sentence description. Add it to roles.json as `{"desc": "<desc>", "folders": []}` under the new key. Write the updated roles.json back to `.org/roles.json`.

3. **Validate there is no collision.** Check whether `<role>/<project>/` already exists on disk. If it does, stop and report: `<role>/<project>/ already exists — nothing created.`

4. **Create the folder and memory skeleton.**
   ```
   mkdir -p <role>/<project>/memory
   ```
   Write `<role>/<project>/memory/PROJECT.md` from the PROJECT template below (fill in `{{ROLE}}`, `{{PROJECT}}`, `{{DATE}}`):

   ```markdown
   # {{PROJECT}}

   _Role: {{ROLE}} | Created: {{DATE}}_

   ## What it is

   <!-- 1–3 sentences: problem this solves, who uses it, the deliverable. -->

   ## Key decisions & rationale

   <!-- Decisions made so far and WHY. Add more as the project evolves. -->
   <!-- Format: **Decision**: ... **Rationale**: ... -->

   ## Tech stack

   <!-- Languages, frameworks, services, key libraries. Be specific (version or "latest"). -->

   ## Data flow

   <!-- How does data move through the system? (optional for non-data projects) -->

   ## Gotchas & watch-outs

   <!-- The non-obvious things. Use [GOTCHA]: prefix — Stop hook will scrape these into the role brain. -->

   ## Current state

   <!-- What's been built, what works, what's WIP, what's blocked. Update this as you go. -->

   ## How to resume

   <!-- The exact steps + files to read to get back up to speed in a future session. -->
   <!-- E.g.: "1. Read this file. 2. Run X. 3. Open Y." -->

   ## Lessons & patterns

   <!-- Use inline tags — Stop hook auto-promotes these to the role brain: -->
   <!-- [LESSON]: something that worked well -->
   <!-- [PATTERN]: reusable approach discovered here -->
   <!-- [GOTCHA]: subtle thing that bit us -->
   <!-- [TOOL]: library / service / CLI that's genuinely useful -->
   ```

5. **Register the project in roles.json.** Read `.org/roles.json`, append `"<project>"` to `roles.<role>.folders` (if not already present), write it back.

6. **Regenerate ORG.md.** Run:
   ```
   python3 .org/sync_org.py
   ```

7. **Report** what was created:
   ```
   Created: <role>/<project>/
            <role>/<project>/memory/PROJECT.md
   Updated: .org/roles.json  (added "<project>" to <role>.folders)
   Updated: ORG.md
   ```
   Then suggest: "Open `<role>/<project>/memory/PROJECT.md` and fill in the What / Decisions / Stack sections to start the memory trail."

## Constraints

- All paths are repo-relative. Never hardcode `/Users/...` absolute paths.
- The project folder goes under `<role>/`, never at the repo root.
- roles.json must remain valid JSON after the edit — read, parse, mutate dict, write with `indent=2`.
- If `.org/sync_org.py` fails, report the error but do not roll back — the folder + memory file are the durable artifact.
- Do not create any files other than `memory/PROJECT.md` inside the new project folder. The user will scaffold their actual project content separately.

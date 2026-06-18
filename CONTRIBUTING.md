# Contributing to orgkit

Thanks for wanting to improve orgkit. This doc covers how to set up, the test suite, coding conventions, and how to propose a change.

---

## Getting started

**Requirements:** Python 3.8+, stdlib only — nothing to `pip install`.

```bash
git clone https://github.com/hdk10/orgkit.git
cd orgkit

# Smoke test — run setup in dry-run mode against the bundled example
python3 setup.py --target example/ --analyze
```

That's it. No virtual environment needed.

---

## Running the test suite

> `tests/` is being added in parallel by a separate contributor. Once merged, run:

```bash
python3 -m pytest tests/
```

Until then, the canonical sanity check is running the installer against `example/` and confirming the output looks right. Every PR should pass that manually before submitting.

---

## Coding conventions

**The three rules that matter most:**

1. **Stdlib only.** No third-party packages — ever. orgkit runs wherever Python 3 runs. If you need something that looks like it needs a dependency, find the stdlib equivalent or write it yourself.

2. **No hardcoded paths.** Every path that touches the user's system must go through the `--target` resolution chain or a config parameter. Never assume a path like `~/work` or `/Users/...`.

3. **Idempotent.** Every operation that touches files or settings must be safe to run twice. Running `setup.py` on an already-set-up repo should produce the same result as running it once — no duplicates, no corruption, no error. Check before you write.

**Other conventions:**

- Keep functions small and named for what they do, not how.
- Prefer explicit over clever — this code will be read and modified by people who aren't familiar with it.
- Every flag that touches files must dry-run first by default (or accept `--yes` to skip the confirmation).
- Error messages should tell the user what to do next, not just what went wrong.

---

## How to propose a change

1. **Open an issue first** for anything non-trivial. Describe what you're trying to do and why. This saves you from writing code for something that won't be merged.

2. **Fork and branch** — one branch per change, named descriptively (`feat/windows-cron`, `fix/analyze-drift-check`, `docs/comparison`).

3. **Keep PRs tight.** One change per PR. If you find two things to fix, open two PRs.

4. **Update docs alongside code.** If you change a flag, update the README table. If you change the reconcile logic, update `docs/HOW-IT-WORKS.md`. Docs PRs are welcome too.

5. **Fill in the PR template.** It'll ask for a short description, how to test it, and whether it's a breaking change.

---

## What makes a good contribution

- **Bug fixes** — especially on edge cases in the migrate or reconcile flows. Always include a reproduction case.
- **Portability fixes** — Windows, unusual Python 3 minor versions, non-ASCII paths.
- **New migration heuristics** — smarter folder-to-role grouping proposals.
- **Docs and examples** — clearer explanations, better sample orgs in `example/`.
- **Test coverage** — the `tests/` suite is nascent; more cases are welcome.

---

## What to avoid

- External dependencies. If it needs `pip install`, it's out of scope.
- Features that assume a specific OS without a fallback.
- Changes to the memory format that break existing `ROLE.md` files silently.

---

## Questions?

Open an issue with the label `question`. We'll answer there so the answer is findable by others.

#!/usr/bin/env python3
"""orgkit end-to-end smoke test.

Exercises every deterministic workflow path in an isolated sandbox (HOME and
target repo are both temp dirs, so your real ~/.claude/settings.json is never
touched). Prints one PASS/FAIL line per scenario and exits non-zero if anything
failed. This is the simulator the review-loop gates on.

Run:  python3 tests/smoke.py
"""
from __future__ import annotations

# pyright: reportMissingImports=false
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

KIT = Path(__file__).resolve().parent.parent          # dev/orgkit/
SETUP = KIT / "setup.py"
ORGKIT = KIT / "orgkit"

results: list[tuple[bool, str, str]] = []


def check(cond: bool, name: str, detail: str = "") -> bool:
    results.append((bool(cond), name, detail))
    return bool(cond)


def run(cmd, env=None, cwd=None, inp=None):
    """Run a command, return (rc, stdout, stderr)."""
    p = subprocess.run(cmd, env=env, cwd=cwd, input=inp,
                       capture_output=True, text=True)
    return p.returncode, p.stdout, p.stderr


def py(*args, env=None, cwd=None, inp=None):
    return run([sys.executable, *map(str, args)], env=env, cwd=cwd, inp=inp)


def sandbox_env():
    home = Path(tempfile.mkdtemp())
    (home / ".claude").mkdir(parents=True)
    (home / ".claude" / "settings.json").write_text("{}")
    env = dict(os.environ)
    env["HOME"] = str(home)
    return home, env


def new_repo(with_remote=True):
    repo = Path(tempfile.mkdtemp()) / "repo"
    repo.mkdir(parents=True)
    run(["git", "init", "-q"], cwd=repo)
    if with_remote:
        run(["git", "remote", "add", "origin",
             "https://github.com/someuser/theirrepo.git"], cwd=repo)
    return repo


# ---------------------------------------------------------------------------

def t_compile():
    rc, _, err = py("-m", "py_compile", *[str(p) for p in ORGKIT.glob("*.py")], SETUP)
    check(rc == 0, "compile all engine + setup", err.strip()[:200])


def t_fresh_install():
    home, env = sandbox_env()
    repo = new_repo()
    try:
        rc, out, err = py(SETUP, "--target", repo, "--fresh",
                          "--roles", "eng:Build,growth:Grow", "--yes", env=env)
        check(rc == 0, "fresh install exits 0", err.strip()[:200])
        engine = repo / ".org"
        for f in ("scan.py", "transcript.py", "plan.py", "doctor.py",
                  "migrate.py", "role_inject.py", "role_digest.py", "sync_org.py",
                  "install_hooks.py", "install_cron.py", "orgmap.py", "analyze.py",
                  "core.py", "roles.json"):
            check((engine / f).exists(), f"engine ships {f}")
        cmds = repo / ".claude" / "commands"
        shipped = {p.name for p in cmds.glob("*.md")} if cmds.is_dir() else set()
        check(len(shipped) >= 9, "ships >=9 slash commands", f"got {len(shipped)}: {sorted(shipped)}")
        check((repo / "CLAUDE.md").exists(), "global CLAUDE.md created")
        check((repo / "eng" / "memory" / "ROLE.md").exists(), "role ROLE.md created")
        check((repo / "ORG.md").exists(), "ORG.md generated")
        check((repo / "ORG_PLAN.md").exists(), "ORG_PLAN.md generated")
        settings = json.loads((home / ".claude" / "settings.json").read_text())
        n_hooks = sum(len(g.get("hooks", []))
                      for ev in settings.get("hooks", {}).values() for g in ev)
        check(n_hooks >= 3, "hooks registered in settings", f"{n_hooks} hooks")
        plan = (repo / "ORG_PLAN.md").read_text()
        check("[x] hooks registered" in plan.replace("- ", ""),
              "plan: hooks-registered checkbox ticks",
              "checkbox not ticked")
        return repo, home, env
    except Exception as e:  # noqa
        check(False, "fresh install no exception", str(e))
        return None, None, None


def t_doctor(repo, env):
    if not repo:
        return
    rc, out, err = py(SETUP, "--doctor", "--target", repo, "--dry-run", env=env)
    check("0 FAIL" in out, "doctor: 0 FAIL on fresh install", out[-300:])
    check("Engine ↔ hooks" in out and "OK" in out, "doctor: engine<->hooks present", out[-300:])
    # break it
    (repo / ".org" / "roles.json").write_text("NOT JSON {{")
    rc, out, err = py(SETUP, "--doctor", "--target", repo, "--yes", env=env)
    check("roles.json" in out and ("FAIL" in out or "Malformed" in out or "fix" in out.lower()),
          "doctor: detects corrupt roles.json", out[-300:])
    # repair should restore valid json
    try:
        json.loads((repo / ".org" / "roles.json").read_text())
        check(True, "doctor: repaired roles.json is valid JSON")
    except Exception:
        check(False, "doctor: repaired roles.json is valid JSON")


def t_map(repo, env):
    if not repo:
        return
    rc, out, err = py(SETUP, "--map", "--target", repo, env=env)
    svg = repo / "ORG_MAP.svg"
    check(svg.exists(), "map: ORG_MAP.svg created", err[-200:])
    if svg.exists():
        txt = svg.read_text()
        check("someuser/theirrepo" in txt,
              "map: watermark uses USER git remote (not author)",
              "watermark wrong")


def t_scan():
    home, env = sandbox_env()
    repo = new_repo()
    (repo / "web-app").mkdir(); (repo / "web-app" / "package.json").write_text("{}")
    (repo / "ml-thing").mkdir(); (repo / "ml-thing" / "train.py").write_text("x=1")
    rc, out, err = py(ORGKIT / "scan.py", "--target", repo, "--json", env=env)
    ok = False
    try:
        d = json.loads(out)
        ok = isinstance(d.get("folders"), list) and d.get("scanned", 0) >= 2
    except Exception:
        ok = False
    check(ok, "scan.py emits valid JSON with folders", out[:200])


def t_transcript():
    tmp = Path(tempfile.mkdtemp()) / "s.jsonl"
    lines = []
    lines.append(json.dumps({"type": "user", "message": {"role": "user", "content": "hello world"}}))
    lines.append(json.dumps({"type": "assistant", "message": {"role": "assistant",
                "content": [{"type": "thinking", "thinking": "noise"},
                            {"type": "text", "text": "a real answer"},
                            {"type": "tool_use", "name": "x"}]}}))
    tmp.write_text("\n".join(lines) + "\n")
    rc, out, err = py(ORGKIT / "transcript.py", tmp)
    check(rc == 0 and "USER: hello world" in out and "ASSISTANT: a real answer" in out
          and "noise" not in out,
          "transcript: clean dialogue, tool/thinking stripped", out[:200])


def t_role_digest_tag_inside_section():
    home, env = sandbox_env()
    repo = new_repo()
    py(SETUP, "--target", repo, "--fresh", "--roles", "eng:Build", "--yes", env=env)
    role_md = repo / "eng" / "memory" / "ROLE.md"
    # ensure a Gotchas section exists with the --- separator style
    md = role_md.read_text()
    check("## Gotchas" in md, "ROLE.md template has Gotchas section")
    # write a changed file with a tag, backdate .last_digest
    (repo / "eng" / "work.py").write_text("# [GOTCHA]: the rate limiter resets at UTC midnight\nx=1\n")
    import time
    (repo / "eng" / "memory" / ".last_digest").write_text(json.dumps({"ts": time.time() - 99999}))
    env2 = dict(env); env2["CLAUDE_PROJECT_DIR"] = str(repo)
    py(repo / ".org" / "role_digest.py", "scrape", env=env2)
    md2 = role_md.read_text()
    if "rate limiter resets at UTC" in md2:
        # find the inserted bullet position vs the Gotchas section and the next ---/##
        g = md2.index("## Gotchas")
        bullet = md2.index("rate limiter resets at UTC")
        # the next section boundary after Gotchas
        after = md2[g + 1:]
        nb = after.find("\n---")
        nh = after.find("\n## ")
        bounds = [x for x in (nb, nh) if x != -1]
        boundary = (g + 1 + min(bounds)) if bounds else len(md2)
        check(g < bullet < boundary,
              "role_digest: tag bullet lands INSIDE Gotchas section",
              f"g={g} bullet={bullet} boundary={boundary}")
    else:
        check(False, "role_digest: tag scraped into ROLE.md", "tag not found")


def t_role_digest_transcript_pointer():
    home, env = sandbox_env()
    repo = new_repo()
    py(SETUP, "--target", repo, "--fresh", "--roles", "eng:Build", "--yes", env=env)
    import time
    (repo / "eng" / "memory" / ".last_digest").write_text(json.dumps({"ts": time.time() - 99999}))
    (repo / "eng" / "work2.py").write_text("y=2\n")
    env2 = dict(env); env2["CLAUDE_PROJECT_DIR"] = str(repo)
    payload = json.dumps({"transcript_path": "/tmp/sess.jsonl", "cwd": str(repo / "eng")})
    py(repo / ".org" / "role_digest.py", "scrape", env=env2, inp=payload)
    pend = (repo / "eng" / "memory" / "_pending.md").read_text()
    check("session transcript at /tmp/sess.jsonl" in pend,
          "role_digest: records transcript pointer from stdin", pend[-200:])


def t_role_inject():
    home, env = sandbox_env()
    repo = new_repo()
    py(SETUP, "--target", repo, "--fresh", "--roles", "eng:Build", "--yes", env=env)
    env2 = dict(env); env2["CLAUDE_PROJECT_DIR"] = str(repo)
    # SessionStart inside role dir
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s1",
                          "cwd": str(repo / "eng")})
    rc, out, err = py(repo / ".org" / "role_inject.py", env=env2, inp=payload)
    ok = False
    try:
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        ok = "eng" in ctx
    except Exception:
        ok = False
    check(ok, "role_inject: SessionStart injects role brain", out[:200])


def t_no_fresh_nag():
    """A freshly-installed role must NOT trigger the reconcile nag on first session."""
    home, env = sandbox_env()
    repo = new_repo()
    py(SETUP, "--target", repo, "--fresh", "--roles", "eng:Build", "--yes", env=env)
    env2 = dict(env); env2["CLAUDE_PROJECT_DIR"] = str(repo)
    payload = json.dumps({"hook_event_name": "SessionStart", "session_id": "s9",
                          "cwd": str(repo / "eng")})
    rc, out, err = py(repo / ".org" / "role_inject.py", env=env2, inp=payload)
    nagged = False
    try:
        ctx = json.loads(out)["hookSpecificOutput"]["additionalContext"]
        nagged = "Auto-reconcile due" in ctx
    except Exception:
        nagged = False
    check(not nagged, "fresh install does NOT nag /role-promote on first session",
          "spurious reconcile nag fired")


def t_migrate_classify():
    sys.path.insert(0, str(ORGKIT))
    try:
        from migrate import _classify_line
    except Exception as e:
        check(False, "migrate: import _classify_line", str(e)); return
    import inspect
    sig = inspect.signature(_classify_line)
    # call with prefixes if required
    def classify(line, folder):
        kw = {}
        if "abs_prefix" in sig.parameters:
            kw["abs_prefix"] = "/abs/repo"
        if "repo_prefix" in sig.parameters:
            kw["repo_prefix"] = "repo"
        try:
            return _classify_line(line, folder, **kw)
        except TypeError:
            return _classify_line(line, folder)
    cases = [
        ("from oldproj.utils import X", "oldproj", "import"),
        ("see ../oldproj/main.py", "oldproj", "relative_path"),
        ("# note unrelated to oldproj", "oldproj", "other"),
        ("cd /tmp/oldproj && go", "oldproj", "other"),   # NOT literal_path (won't be rewritten)
    ]
    allok = True
    bad = []
    for line, f, exp in cases:
        got = classify(line, f)
        if got != exp:
            allok = False; bad.append(f"{line!r}->{got}!={exp}")
    check(allok, "migrate: classification correct (incl cd /tmp/x not literal)", "; ".join(bad))


def t_migrate_scaffolds_brains():
    """Migrate must complete in ONE step: move folders AND seed ROLE.md + CLAUDE.md."""
    home, env = sandbox_env()
    repo = new_repo()
    (repo / "old-api").mkdir(); (repo / "old-api" / "run.py").write_text("x=1")
    (repo / "old-ml").mkdir(); (repo / "old-ml" / "train.py").write_text("y=2")
    rc, out, err = py(SETUP, "--target", repo, "--migrate",
                      "--role-map", "old-api:engineering,old-ml:data-science",
                      "--yes", env=env)
    check((repo / "engineering" / "old-api").is_dir(), "migrate: folder moved under role")
    check((repo / "engineering" / "memory" / "ROLE.md").is_file()
          and (repo / "data-science" / "memory" / "ROLE.md").is_file(),
          "migrate: seeds ROLE.md per role (one-step)", out[-200:])
    check((repo / "CLAUDE.md").is_file(), "migrate: seeds global CLAUDE.md")
    check((repo / "engineering" / "memory" / ".last_promote").is_file(),
          "migrate: stamps .last_promote (no day-one nag)")



def t_scrape_skips_nested_git():
    """role_digest must NOT scrape tags from a nested git repo (vendored/standalone)."""
    home, env = sandbox_env()
    repo = new_repo()
    py(SETUP, "--target", repo, "--fresh", "--roles", "eng:Build", "--yes", env=env)
    import time as _t
    # a nested git repo under the role with a doc that contains a [GOTCHA] example
    nested = repo / "eng" / "vendored-tool"
    (nested / ".git").mkdir(parents=True)
    (nested / "README.md").write_text("Example: write [GOTCHA]: do not scrape me\n")
    (repo / "eng" / "memory" / ".last_digest").write_text(json.dumps({"ts": _t.time() - 99999}))
    env2 = dict(env); env2["CLAUDE_PROJECT_DIR"] = str(repo)
    py(repo / ".org" / "role_digest.py", "scrape", env=env2)
    role_md = (repo / "eng" / "memory" / "ROLE.md").read_text()
    check("do not scrape me" not in role_md,
          "role_digest skips nested git repos (no vendored-doc pollution)")


def t_install_cron_honest():
    sys.path.insert(0, str(ORGKIT))
    src = (ORGKIT / "install_cron.py").read_text()
    # must NOT run headless model reconcile as the mechanism
    check("claude -p" not in src or "does not execute" in src.lower() or "does NOT" in src,
          "install_cron: no fake headless claude -p reconcile", "still claims headless")


def t_uninstall(repo, env):
    if not repo:
        return
    # re-install hooks first (doctor test may have left state); just run uninstall
    rc, out, err = py(SETUP, "--uninstall", "--target", repo, "--yes", env=env)
    settings = json.loads((Path(env["HOME"]) / ".claude" / "settings.json").read_text())
    repo_hooks = sum(1 for ev in settings.get("hooks", {}).values() for g in ev
                     for h in g.get("hooks", []) if str(repo) in h.get("command", ""))
    check(repo_hooks == 0, "uninstall: removes this repo's hooks", f"{repo_hooks} left")
    check((repo / "eng" / "memory" / "ROLE.md").exists() or (repo / "growth" / "memory" / "ROLE.md").exists(),
          "uninstall: keeps memory (ROLE.md)")


def main() -> int:
    t_compile()
    repo, home, env = t_fresh_install()
    t_doctor(repo, env)
    t_map(repo, env)
    t_scan()
    t_transcript()
    t_role_digest_tag_inside_section()
    t_role_digest_transcript_pointer()
    t_role_inject()
    t_no_fresh_nag()
    t_migrate_classify()
    t_migrate_scaffolds_brains()
    t_scrape_skips_nested_git()
    t_install_cron_honest()
    t_uninstall(repo, env)

    print("\n" + "=" * 64)
    n_fail = 0
    for ok, name, detail in results:
        mark = "PASS" if ok else "FAIL"
        if not ok:
            n_fail += 1
        line = f"  [{mark}] {name}"
        if not ok and detail:
            line += f"\n         ↳ {detail}"
        print(line)
    print("=" * 64)
    print(f"  {len(results) - n_fail}/{len(results)} passed, {n_fail} failed")
    return 1 if n_fail else 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""PreToolUse hook (Bash): refuse whole-index git operations while another live
session shares this checkout.

The git index is shared state across every session in one working copy. On
2026-09-01 a broad `git add` in one session absorbed six files another
session had staged; the second session's `git commit` then reported "no
changes added to commit", which reads as a staging mistake, not a collision
(memory: concurrent-session-git-collisions). Same-repo concurrency peaks at
10 sessions (measured 2026-09-21). The memory file is recall-only and recall
did not stop it; this is the check.

Shapes refused, and only when a peer shares the checkout:
  git add -A | --all | -u | --update | . | :/ | *   stages everyone's changes
  git commit -a | -am | --all                       commits everyone's changes
  git stash [push|save] with no pathspec            removes everyone's changes

Peer = another entry in ~/.claude/sessions/*.json whose pid is alive and whose
cwd resolves to the same `git rev-parse --show-toplevel`. A worktree has its
own index and its own toplevel, so it never counts. `cd X && git ...` and
`git -C X ...` are checked against X. No peers: allow, silently, so a solo
session keeps its habits.

Exit 0 with no output = allow. Exit 2 = block; stderr goes back to the model.
Fails open on any error in the check itself: a guard that breaks git for
everyone costs more than the collision it prevents. Test override:
GIT_GUARD_SESSIONS_DIR.
"""
import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

BROAD_ADD = {"-A", "--all", "-u", "--update", "--no-ignore-removal", ".", "./", ":/", "*"}
SHORT_CLUSTER = re.compile(r"^-[a-zA-Z]+$")
SPLIT = re.compile(r"\s*(?:&&|\|\||;|\||\n)\s*")
GIT_GLOBAL_WITH_ARG = {"-C", "-c", "--git-dir", "--work-tree", "--namespace"}


def sessions_dir():
    return Path(os.environ.get("GIT_GUARD_SESSIONS_DIR") or Path.home() / ".claude" / "sessions")


def toplevel(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
    except Exception:
        return None
    return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else None


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:  # PermissionError = alive, different user
        return True


def peers_in(top, own_session_id):
    """Names of other live sessions whose checkout is exactly this toplevel."""
    names = []
    for f in sessions_dir().glob("*.json"):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        if j.get("sessionId") == own_session_id or not j.get("pid") or not j.get("cwd"):
            continue
        cwd = os.path.realpath(os.path.expanduser(j["cwd"]))
        if cwd != top and not cwd.startswith(top + os.sep):
            continue  # cheap prefix test first; git only for candidates
        if not alive(j["pid"]):
            continue
        if toplevel(cwd) != top:
            continue  # nested repo or worktree: its own index
        names.append(j.get("name") or f.stem)
    return names


def broad_git_ops(command, cwd):
    """Yield (effective_cwd, description) for each whole-index op in the command."""
    effective = cwd
    for seg in SPLIT.split(command):
        try:
            toks = shlex.split(seg)
        except ValueError:
            toks = seg.split()
        if not toks:
            continue
        if toks[0] == "cd":
            if len(toks) > 1 and not toks[1].startswith("-"):
                effective = os.path.normpath(os.path.join(effective, os.path.expanduser(toks[1])))
            continue
        if toks[0] != "git":
            continue
        i, git_cwd = 1, effective
        while i < len(toks) and toks[i].startswith("-"):
            if toks[i] in GIT_GLOBAL_WITH_ARG and i + 1 < len(toks):
                if toks[i] == "-C":
                    git_cwd = os.path.normpath(os.path.join(git_cwd, os.path.expanduser(toks[i + 1])))
                i += 2
            else:
                i += 1
        if i >= len(toks):
            continue
        sub, rest = toks[i], toks[i + 1:]
        if sub == "add":
            if any(t in BROAD_ADD or (SHORT_CLUSTER.match(t) and ("A" in t or "u" in t)) for t in rest):
                yield git_cwd, "git add " + " ".join(rest)
        elif sub == "commit":
            if any(t == "--all" or (SHORT_CLUSTER.match(t) and "a" in t) for t in rest):
                yield git_cwd, "git commit " + " ".join(rest)
        elif sub == "stash":
            action = rest[0] if rest and not rest[0].startswith("-") else "push"
            if action in ("push", "save") and "--" not in rest and "-p" not in rest and "--patch" not in rest:
                yield git_cwd, "git stash " + " ".join(rest)


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0
    if payload.get("tool_name") != "Bash":
        return 0
    command = (payload.get("tool_input") or {}).get("command") or ""
    if "git" not in command:
        return 0
    cwd = payload.get("cwd") or os.getcwd()
    for git_cwd, what in broad_git_ops(command, cwd):
        top = toplevel(git_cwd)
        if not top:
            continue
        peers = peers_in(top, payload.get("session_id"))
        if not peers:
            continue
        sys.stderr.write(
            f"Refused: `{what}` while {len(peers)} other live session(s) share this checkout "
            f"({', '.join(peers)}).\n"
            "The git index is shared; a whole-index operation absorbs or removes their work "
            "(2026-09-01: six files another session had staged landed in this session's commit; "
            "the only symptom was \"no changes added to commit\"). Name the paths this session "
            "changed instead: git add <path>...; git commit -m (no -a); git stash push -- <path>.\n"
        )
        return 2
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        sys.exit(0)

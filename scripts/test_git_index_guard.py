#!/usr/bin/env python3
"""Fixture test for git_index_guard.py. Run: python3 scripts/hooks/test_git_index_guard.py

Builds two real git repos and a worktree in a temp dir plus a fake session
registry, then drives the hook through stdin exactly as Claude Code does.
Liveness uses this test's own pid for a live peer and a dead pid for a dead one.
"""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

HOOK = Path(__file__).with_name("git_index_guard.py")
ME = "S-me"


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def run(sessions, command, cwd, tool="Bash"):
    payload = json.dumps({"session_id": ME, "tool_name": tool, "cwd": str(cwd),
                          "tool_input": {"command": command}, "hook_event_name": "PreToolUse"})
    p = subprocess.run([sys.executable, str(HOOK)], input=payload, text=True, capture_output=True,
                       env={**os.environ, "GIT_GUARD_SESSIONS_DIR": str(sessions)}, timeout=15)
    return p.returncode, p.stderr


def dead_pid():
    pid = 99990
    while True:
        try:
            os.kill(pid, 0)
            pid -= 1
        except ProcessLookupError:
            return pid
        except PermissionError:
            pid -= 1


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(os.path.realpath(tmp))
        repo, other = tmp / "repo", tmp / "other"
        for r in (repo, other):
            r.mkdir(); git("init", "-q", cwd=r); (r / "f").write_text("x")
            git("add", "f", cwd=r); git("commit", "-qm", "init", cwd=r)
        (repo / "sub").mkdir()
        wt = tmp / "repo-wt"
        git("worktree", "add", "-q", str(wt), "-b", "wt", cwd=repo)
        sessions = tmp / "sessions"; sessions.mkdir()

        def registry(*entries):
            for f in sessions.glob("*.json"):
                f.unlink()
            for i, (sid, pid, cwd, name) in enumerate(entries):
                (sessions / f"{i}.json").write_text(json.dumps(
                    {"sessionId": sid, "pid": pid, "cwd": str(cwd), "name": name}))

        live, dead = os.getpid(), dead_pid()

        # 1. peer in the same checkout: broad shapes refused, named paths allowed
        registry((ME, live, repo, "me"), ("S-peer", live, repo / "sub", "repo-peer"))
        for cmd in ("git add -A", "git add .", "git add --all", "git add -u", "git add -- .",
                    "git commit -a -m x", "git commit -am 'msg -a'", "git commit --all -m x",
                    "git stash", "git stash push", "git stash -u", "git stash save wip"):
            rc, err = run(sessions, cmd, repo)
            assert rc == 2 and "repo-peer" in err, (cmd, rc, err)
        for cmd in ("git add f", "git add f g/h", "git commit -m 'add -A later'", "git commit -qm x",
                    "git stash push -- f", "git stash -p", "git stash list", "git stash pop",
                    "git status", "git log -1", "echo git add -A"):
            rc, err = run(sessions, cmd, repo)
            assert rc == 0 and err == "", (cmd, rc, err)
        print("1 peer in same checkout: 12 broad shapes refused, 11 narrow/other allowed  OK")

        # 2. no peers: everything allowed
        registry((ME, live, repo, "me"))
        assert run(sessions, "git add -A", repo)[0] == 0
        print("2 solo session: broad add allowed  OK")

        # 3. peer in another repo, a worktree, or dead: allowed
        registry((ME, live, repo, "me"), ("S-o", live, other, "other"),
                 ("S-w", live, wt, "wt"), ("S-d", dead, repo, "dead"))
        rc, err = run(sessions, "git add -A", repo)
        assert rc == 0, err
        print("3 peers in another repo / a worktree / dead pid: allowed  OK")

        # 4. cd and -C resolve the checkout the command actually touches
        registry((ME, live, other, "me"), ("S-peer", live, repo, "repo-peer"))
        rc, err = run(sessions, f"cd {repo} && git commit -am x", other)
        assert rc == 2 and "repo-peer" in err, err
        rc, err = run(sessions, f"git -C {repo} add -A", other)
        assert rc == 2, err
        rc, err = run(sessions, "git add -A", other)
        assert rc == 0, err
        print("4 cd X && / git -C X: checked against X, not the session cwd  OK")

        # 5. peer in the same repo via a subdirectory cwd counts
        registry((ME, live, repo, "me"), ("S-peer", live, repo / "sub", "sub-peer"))
        rc, err = run(sessions, "git add -A", repo)
        assert rc == 2 and "sub-peer" in err, err
        print("5 peer cwd in a subdirectory of the checkout: counts  OK")

        # 6. non-Bash tool, non-git command, non-repo cwd, malformed stdin: allowed
        assert run(sessions, "git add -A", repo, tool="Read")[0] == 0
        assert run(sessions, "ls", repo)[0] == 0
        assert run(sessions, "git add -A", tmp)[0] == 0
        p = subprocess.run([sys.executable, str(HOOK)], input="nope", text=True, capture_output=True,
                           env={**os.environ, "GIT_GUARD_SESSIONS_DIR": str(sessions)})
        assert p.returncode == 0 and p.stderr == ""
        print("6 other tool / no git / not a repo / malformed stdin: allowed, silent  OK")
    print("all OK")


if __name__ == "__main__":
    main()

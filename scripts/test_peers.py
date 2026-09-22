#!/usr/bin/env python3
"""Fixture test for peers.py. Run: python3 scripts/test_peers.py

Fake registry, fake history.jsonl, fake transcripts in the real line shapes,
two real git repos. Live pid = this test's; dead pid = one nothing owns.
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PEERS = Path(__file__).with_name("peers.py")
sys.path.insert(0, str(PEERS.parent))
import peers  # noqa: E402  — the slug rule is the CLI's, not a copy


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def run(env, *args, stdin=None):
    p = subprocess.run([sys.executable, str(PEERS), *args], input=stdin, text=True,
                       capture_output=True, env={**os.environ, **env}, timeout=20)
    assert p.returncode == 0, p.stderr
    return p.stdout


def dead_pid():
    pid = 99990
    while True:
        try:
            os.kill(pid, 0); pid -= 1
        except ProcessLookupError:
            return pid
        except PermissionError:
            pid -= 1


def tool_line(name, path, ts="2026-09-21T20:30:00.000Z"):
    return json.dumps({"type": "assistant", "timestamp": ts, "message": {"content": [
        {"type": "tool_use", "name": name, "input": {"file_path": path, "content": "x"}}]}}, separators=(",", ":"))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(os.path.realpath(tmp))
        repo, other = tmp / "repo", tmp / "other"
        for r in (repo, other):
            r.mkdir(); git("init", "-q", cwd=r)
        (repo / "sub").mkdir()
        sessions, projects, history = tmp / "sessions", tmp / "projects", tmp / "history.jsonl"
        sessions.mkdir(); projects.mkdir()
        env = {"PEERS_SESSIONS_DIR": str(sessions), "PEERS_PROJECTS_DIR": str(projects), "PEERS_HISTORY": str(history)}
        live, dead = os.getpid(), dead_pid()
        now_ms = int(time.time() * 1000)
        hist = []

        def session(sid, pid, cwd, name, prompts, transcript=(), status="idle", ended_ago_s=0, ran_s=None):
            """pid=None: a finished session — history + transcript only, no registry entry."""
            if pid is not None:
                ended_ago_s = ended_ago_s or 60  # a live session's last prompt was a minute ago
                (sessions / f"{sid}.json").write_text(json.dumps(
                    {"sessionId": sid, "pid": pid, "cwd": str(cwd), "name": name, "status": status,
                     "startedAt": now_ms - 3600_000, "updatedAt": now_ms}))
            end = now_ms - ended_ago_s * 1000
            step = (ran_s * 1000 // max(len(prompts) - 1, 1)) if ran_s else 60_000
            for i, text in enumerate(prompts):
                hist.append(json.dumps({"display": text, "timestamp": end - (len(prompts) - 1 - i) * step,
                                        "project": str(cwd), "sessionId": sid}))
            d = projects / peers.slug(str(cwd)); d.mkdir(exist_ok=True)
            (d / f"{sid}.jsonl").write_text("\n".join(transcript) + "\n")

        big = json.dumps({"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "content": "x" * 300000}]}})
        session("S-me", live, repo, "repo-me", ["my own opener that is long enough to count"])
        session("S-a", live, repo / "sub", "repo-a", status="busy", prompts=[
            "sunday standup",                                   # 2 words: ack, not an opener
            "let us fix the ledger triage bug today",           # opener
            "go",                                               # ack
            "!gh repo rename x -R y --yes",                     # shell
            "/compact",                                         # slash
            "<command-name>/model</command-name>",              # wrapper
            "This session is being continued from a previous conversation that ran out of context.",
            "actually the real problem is the friday triage never runs",   # before
            "ok",
            "now rewrite the horizon email to lead with one question",     # now
            "proceed please",                                   # ack: counted in total, not shown
        ], transcript=[
            tool_line("Read", str(repo / "sub" / "never-edited.md")),
            tool_line("Write", str(repo / "sub" / "old-edit.py"), ts="2026-09-21T19:00:00.000Z"),
            big, big,
            tool_line("Edit", str(repo / "sub" / "a.py")),
            tool_line("Write", str(Path.home() / "outside.md")),
            tool_line("Edit", str(repo / "sub" / "a.py")),      # duplicate collapses
            tool_line("Write", "/etc/elsewhere.conf"),
            tool_line("Write", "/private/tmp/claude-501/-x/abc/scratchpad/notes.py"),  # never shown
        ])
        session("S-b", live, repo, "repo-b", ["one substantive prompt that is the whole session"])
        session("S-o", live, other, "other-o", ["elsewhere entirely, five words here"])
        # a different portfolio root = a different parent directory, so it needs its own temp dir
        walled = Path(os.path.realpath(tempfile.mkdtemp())) / "repo3"; walled.mkdir(); git("init", "-q", cwd=walled)
        session("S-z", live, walled, "walled-z", ["a session in a different portfolio root entirely"])
        session("S-d", dead, repo, "repo-dead", ["dead session prompt with enough words"], ended_ago_s=3 * 86400)
        # finished sessions: the newest one in repo is the handoff; older / other-repo ones are not
        session("S-x", None, repo, None, ["yesterday I started the pikafish wasm build",
                                          "publish script is failing on the net download",
                                          "commit it and write the release notes"],
                transcript=[tool_line("Write", str(repo / "publish.sh")), tool_line("Edit", str(repo / "RELEASE.md"))],
                ended_ago_s=3 * 3600, ran_s=5 * 3600)
        session("S-x2", None, repo / "sub", None, ["an earlier finished session in the same checkout"],
                ended_ago_s=2 * 86400)
        session("S-old", None, repo, None, ["a finished session from ten days ago that no longer counts"],
                ended_ago_s=10 * 86400)
        session("S-y", None, other, None, ["a finished session in the other repo, five words"], ended_ago_s=600)
        history.write_text("\n".join(hist) + "\n")

        out = run(env, "--here", "--cwd", str(repo), "--self", "S-me")
        assert "Other live sessions in this checkout (repo) — 2:" in out, out
        assert "repo-a  busy · up 60m · 11 prompts · last 1m ago" in out, out
        assert 'opened:  "let us fix the ledger triage bug today"' in out, out
        assert 'before:  "actually the real problem is the friday triage never runs"' in out, out
        assert 'now:     "now rewrite the horizon email to lead with one question"' in out, out
        for junk in ("sunday standup", "!gh", "/compact", "command-name", "continued from", '"go"', "proceed"):
            assert junk not in out, (junk, out)
        assert "editing: /etc/elsewhere.conf, a.py, ~/outside.md, old-edit.py (" in out, out
        assert "never-edited" not in out and "scratchpad" not in out, out
        assert 'now:     "one substantive prompt that is the whole session"' in out and "opened:" in out, out
        assert out.count("opened:") == 1, out  # repo-b has one prompt: only "now"
        assert "repo-me" not in out and "other-o" not in out and "repo-dead" not in out, out
        assert "Last session" not in out and "pikafish" not in out, out
        print("1 --here: opener/before/now from history with acks, shell, slash, wrapper, continuation skipped; "
              "editing list newest-first, distinct, through 600KB of tool output; self/other repo/dead excluded; no --last block  OK")

        out = run(env, "--cwd", str(repo), "--self", "S-me")
        assert "Other live sessions — 3:" in out and "other-o" in out and "walled-z" not in out, out
        out = run(env, "--all", "--cwd", str(repo), "--self", "S-me")
        assert "Other live sessions — 4:" in out and "walled-z" in out, out
        out = run(env, "--hook", stdin=json.dumps({"session_id": "S-z", "cwd": str(walled)}))
        assert "repo-a" not in out and "other-o" not in out, out
        print("2 default: every live session under this root; a session in another root needs --all; the hook never crosses  OK")

        out = run(env, "--here", "--cwd", str(other), "--self", "S-o")
        assert out.strip() == "No other live sessions in this checkout.", out
        print("3 nothing to show: says so  OK")

        out = run(env, "--last", "--cwd", str(repo), "--self", "S-me")
        assert out.startswith("Last session in this checkout (repo) ended 3h ago · ran 5h · 3 prompts:"), out
        assert 'opened:  "yesterday I started the pikafish wasm build"' in out, out
        assert 'now:     "commit it and write the release notes"' in out, out
        assert "editing: RELEASE.md, publish.sh (" in out, out
        assert "S-x2" not in out and "earlier finished" not in out and "ten days" not in out and "other repo" not in out, out
        assert "handoff:" not in out, out
        print("4 --last: newest finished session in this checkout; older, >7d and other-repo ones ignored  OK")

        hdir = projects / peers.slug(str(repo)) / "memory"; hdir.mkdir()
        (hdir / "HANDOFF.md").write_text("# Handoff\n**Next:** ship it\n")
        out = run(env, "--last", "--cwd", str(repo), "--self", "S-me")
        assert "handoff: memory/HANDOFF.md written 0s ago" in out and "stale" not in out, out
        old = time.time() - 4 * 3600
        os.utime(hdir / "HANDOFF.md", (old, old))
        out = run(env, "--last", "--cwd", str(repo), "--self", "S-me")
        assert "handoff: memory/HANDOFF.md written 4h ago — older than that session" in out, out
        print("5 --last: an authored HANDOFF.md is pointed at, and flagged when older than the last session  OK")

        stdin = json.dumps({"session_id": "S-me", "cwd": str(repo), "hook_event_name": "SessionStart", "source": "startup"})
        out = run(env, "--hook", stdin=stdin)
        assert out.startswith("Last session in this checkout (repo)"), out
        assert "\n\nOther live sessions in this checkout (repo) — 2:" in out and "SendMessage" in out, out
        out = run(env, "--hook", stdin=json.dumps({"session_id": "S-o", "cwd": str(other)}))
        assert out.startswith("Last session in this checkout (other) ended 10m ago") and "Other live" not in out, out
        assert run(env, "--hook", stdin="garbage") is not None
        print("6 --hook: last-session block then peers block; last-session alone when no peers; survives bad stdin  OK")

        for f in list(sessions.glob("*.json")):
            f.unlink()
        history.write_text("")
        assert run(env, "--hook", stdin=stdin) == "", "hook must print nothing with no peers and no finished session"
        assert run(env, "--last", "--cwd", str(repo), "--self", "S-me").strip() == "No finished session in this checkout in the last 7 days."
        print("7 nothing at all: hook silent, --last says so  OK")
    print("all OK")


if __name__ == "__main__":
    main()

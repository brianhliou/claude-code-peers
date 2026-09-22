#!/usr/bin/env python3
"""Fixture test for memory_delta.py. Run: python3 scripts/hooks/test_memory_delta.py

Builds a fake session registry, memory dir and transcript in a temp dir, then
drives the hook through stdin exactly as Claude Code does. No network, no
real state touched (both state dirs are overridden through env).
"""
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

HOOK = Path(__file__).with_name("memory_delta.py")


def run(env, session_id, transcript):
    payload = json.dumps({"session_id": session_id, "transcript_path": str(transcript),
                          "cwd": "/tmp", "hook_event_name": "UserPromptSubmit"})
    p = subprocess.run([sys.executable, str(HOOK)], input=payload, text=True,
                       capture_output=True, env={**os.environ, **env}, timeout=10)
    assert p.returncode == 0, p.stderr
    return p.stdout


def write(path, text, mtime):
    path.write_text(text)
    os.utime(path, (mtime, mtime))


def main():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        sessions, state = tmp / "sessions", tmp / "state"
        sessions.mkdir(); state.mkdir()
        proj = tmp / "projects" / "-x-repo"; proj.mkdir(parents=True)
        memory = proj / "memory"; memory.mkdir()
        env = {"MEMORY_DELTA_SESSIONS_DIR": str(sessions), "MEMORY_DELTA_STATE_DIR": str(state)}

        T = time.time() - 600  # this session started 10 minutes ago
        (sessions / "1.json").write_text(json.dumps({"sessionId": "S1", "startedAt": int(T * 1000), "name": "repo-me"}))
        (sessions / "2.json").write_text(json.dumps({"sessionId": "S2", "startedAt": int((T - 5000) * 1000), "name": "repo-peer"}))
        transcript = proj / "S1.jsonl"
        own_path = memory / "own-by-transcript.md"
        compact = dict(separators=(",", ":"))  # real transcripts are compact JSON
        transcript.write_text(
            json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": str(own_path), "content": "x"}}]}}, **compact) + "\n"
            + json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Read", "input": {"file_path": str(memory / "read-not-written.md")}}]}}) + "\n")

        fm = "---\nname: {n}\ndescription: {d}\nmetadata:\n  originSessionId: {o}\n---\nbody\n"
        write(memory / "old.md", fm.format(n="old", d="before start", o="S2"), T - 100)
        write(memory / "peer.md", fm.format(n="peer", d="peer wrote this", o="S2"), T + 10)
        write(memory / "own-by-frontmatter.md", fm.format(n="own", d="mine", o="S1"), T + 20)
        write(own_path, "---\nname: own2\ndescription: mine via Write tool\n---\n", T + 30)
        # created by a peer long ago (foreign originSessionId), edited by this session now
        peer_created = memory / "peer-created-own-edit.md"
        write(peer_created, fm.format(n="pc", d="peer created it, I edited it", o="S2"), T + 35)
        with open(transcript, "a") as fh:
            fh.write(json.dumps({"type": "assistant", "message": {"content": [
                {"type": "tool_use", "name": "Edit", "input": {"file_path": str(peer_created), "old_string": "a", "new_string": "b"}}]}}, **compact) + "\n")
        write(memory / "read-not-written.md", "---\nname: r\ndescription: I only read it, peer changed it\n---\n", T + 40)
        write(memory / "no-frontmatter.md", "# Handoff — repo — today\n\n**Next:** ship it\n", T + 50)
        write(memory / "MEMORY.md", "- index line\n", T + 60)

        out = run(env, "S1", transcript)
        assert "peer.md" in out and "peer wrote this" in out and "repo-peer" in out, out
        assert "read-not-written.md" in out, out
        assert "no-frontmatter.md" in out and "Handoff" in out, out
        assert "old.md" not in out, out
        assert "own-by-frontmatter.md" not in out, out
        assert "own-by-transcript.md" not in out, out
        assert "peer-created-own-edit.md" not in out, out
        assert "MEMORY.md" not in out, out
        print("1 first run: shows peer/read/no-frontmatter, hides old/own/index/peer-created-but-own-edit  OK")

        out = run(env, "S1", transcript)
        assert out == "", repr(out)
        print("2 second run: nothing repeated  OK")

        # real writes carry the current time, never a future one
        write(memory / "later.md", fm.format(n="later", d="written after last check", o="S2"), time.time())
        out = run(env, "S1", transcript)
        assert "later.md" in out and "peer.md" not in out, out
        print("3 new file after the stamp: shown once, earlier ones not again  OK")

        for i in range(8):
            write(memory / f"bulk{i}.md", fm.format(n=f"b{i}", d=f"bulk {i}", o="S2"), time.time())
        out = run(env, "S1", transcript)
        assert out.count("\n- ") == 6 and "+3 more" in out, out
        print("4 cap: 5 lines plus a +N more line  OK")

        # unknown session id: falls back to transcript birthtime, must not crash
        (state / "S9").unlink(missing_ok=True)
        out = run(env, "S9", transcript)
        assert isinstance(out, str)
        print("5 unknown session: fallback path, no crash  OK")

        p = subprocess.run([sys.executable, str(HOOK)], input="not json", text=True,
                           capture_output=True, env={**os.environ, **env})
        assert p.returncode == 0 and p.stdout == ""
        print("6 malformed stdin: silent exit 0  OK")
    print("all OK")


if __name__ == "__main__":
    main()

#!/usr/bin/env python3
"""UserPromptSubmit hook: show memory that other sessions wrote since this one started.

Auto memory is loaded once, at session start. This machine runs a median of 8
concurrent sessions (peak 12, measured 2026-09-21) with a p90 lifetime of 39
hours, so a memory file a peer writes at noon reaches a session started at
08:00 only when that session restarts — the same correction gets re-learned in
parallel. This closes the gap the cheap way: on each prompt, list the files in
this project's memory dir modified after this session started and not yet
shown, drop the ones this session wrote itself, print one line each.

Why a hook and not a watcher: UserPromptSubmit already runs per prompt, the
check is one directory stat, and the output lands exactly where the session
reads. Fails silent by design — a broken notice must never block a prompt.

Own writes are excluded two ways: frontmatter `originSessionId` (Claude Code's
memory flow writes it) equal to this session, or the file's path appearing in
a write-shaped tool_use line (Write/Edit/Bash) of this session's transcript.
The transcript scan runs only when a candidate exists, so the common case
costs nothing. `originSessionId` names the creator, not the last editor, so a
foreign origin never short-circuits the transcript check (first live false
positive, 2026-09-22: a 09-09 file this session had just edited).

State: ~/.claude/cache/memory-delta/<session_id> holds the epoch of the last
run, so each file is shown once per session. Test overrides:
MEMORY_DELTA_SESSIONS_DIR, MEMORY_DELTA_STATE_DIR.
"""
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path

MAX_LINES = 5
STAMP_TTL_DAYS = 30
# transcripts are compact JSON, but tolerate a space after the colon
WRITE_CALL = re.compile(r'"name":\s*"(Write|Edit|MultiEdit|NotebookEdit|Bash)"')
HOME = Path.home()


def sessions_dir():
    return Path(os.environ.get("MEMORY_DELTA_SESSIONS_DIR")
                or HOME / ".claude" / "sessions")


def state_dir():
    return Path(os.environ.get("MEMORY_DELTA_STATE_DIR")
                or HOME / ".claude" / "cache" / "memory-delta")


def live_sessions():
    """{sessionId: {startedAt_s, name}} from the session registry."""
    out = {}
    for f in sessions_dir().glob("*.json"):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        sid = j.get("sessionId")
        if sid and j.get("startedAt"):
            out[sid] = {"start": j["startedAt"] / 1000.0, "name": j.get("name")}
    return out


def session_start(session_id, transcript, sessions):
    if session_id in sessions:
        return sessions[session_id]["start"]
    try:  # fallback: when the transcript was created (birthtime on macOS)
        st = transcript.stat()
        return getattr(st, "st_birthtime", st.st_mtime)
    except Exception:
        return time.time()


def frontmatter(path):
    """description + originSessionId from the first lines; cheap, tolerant."""
    desc, origin, first_text = None, None, None
    try:
        with open(path, errors="ignore") as fh:
            for i, line in enumerate(fh):
                if i > 30:
                    break
                s = line.strip()
                if s.startswith("description:"):
                    desc = s[len("description:"):].strip().strip('"').strip("'")
                elif s.startswith("originSessionId:"):
                    origin = s.split(":", 1)[1].strip()
                elif first_text is None and s and s != "---" and ":" not in s[:20]:
                    first_text = s.lstrip("#").strip()
    except Exception:
        pass
    return desc or first_text or "", origin


def written_here(path, transcript):
    """True if this session's transcript shows a write-shaped tool call naming the file."""
    needles = (str(path), path.name)
    try:
        with open(transcript, errors="ignore") as fh:
            for line in fh:
                if '"tool_use"' not in line:
                    continue
                if not any(n in line for n in needles):
                    continue
                if WRITE_CALL.search(line):
                    return True
    except Exception:
        return False
    return False


def fmt_time(ts, now):
    d = datetime.fromtimestamp(ts)
    return d.strftime("%H:%M") if now - ts < 20 * 3600 else d.strftime("%m-%d %H:%M")


def main():
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return
    session_id = payload.get("session_id")
    transcript = Path(payload.get("transcript_path") or "")
    if not session_id or not transcript.name:
        return
    memory = transcript.parent / "memory"
    if not memory.is_dir():
        return

    sessions = live_sessions()
    start = session_start(session_id, transcript, sessions)
    sdir = state_dir()
    sdir.mkdir(parents=True, exist_ok=True)
    stamp = sdir / session_id
    try:
        last = float(stamp.read_text().strip())
    except Exception:
        last = 0.0
    threshold = max(start, last)
    now = time.time()

    candidates = []
    for f in memory.glob("*.md"):
        if f.name == "MEMORY.md":
            continue
        try:
            mtime = f.stat().st_mtime
        except Exception:
            continue
        if mtime > threshold:
            candidates.append((mtime, f))
    candidates.sort()

    shown = []
    for mtime, f in candidates:
        desc, origin = frontmatter(f)
        # originSessionId names the file's creator, not its last editor: a
        # peer-created file this session just edited still needs the transcript check
        if origin == session_id or written_here(f, transcript):
            continue
        who = (sessions.get(origin) or {}).get("name") or "another session"
        shown.append(f"- {f.name} ({fmt_time(mtime, now)}, {who}): {desc[:160]}")

    # stamp every run so a file is shown once, and own writes are scanned once
    try:
        stamp.write_text(f"{now:.3f}")
        for old in sdir.iterdir():
            if now - old.stat().st_mtime > STAMP_TTL_DAYS * 86400:
                old.unlink()
    except Exception:
        pass

    if not shown:
        return
    print("Memory written by other sessions since this one started — "
          "read the file before acting on it:")
    for line in shown[:MAX_LINES]:
        print(line)
    if len(shown) > MAX_LINES:
        print(f"- +{len(shown) - MAX_LINES} more in {memory}")


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)

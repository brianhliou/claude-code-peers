#!/usr/bin/env python3
"""What the other live Claude Code sessions are on, and what the last one here did. Read-only; no state.

    scripts/peers.py            every live session under this checkout's parent directory
    scripts/peers.py --all      every live session on the machine, any root
    scripts/peers.py --here     only the ones sharing this checkout
    scripts/peers.py --last     the most recent finished session in this checkout
    scripts/peers.py --hook     SessionStart hook: --last block, then --here block; nothing if neither

Why: on 2026-09-21 the roster held 5 sessions with derived names
(`myrepo-a9`), and 30 days of transcripts showed `ListAgents` called in 118
sessions but `SendMessage` in 7. The roster says who exists, not what they
are doing, so nobody had a reason to talk. The name printed here is the
address `SendMessage` takes. Same month: 302 sessions ended and 2 wrote a
HANDOFF.md, so the next session in a checkout started blind; `--last` is the
handoff nobody writes, built from what the last session left on disk.

Two signals per session, both already on disk, neither maintained by anyone:

  prompts   ~/.claude/history.jsonl holds every typed prompt with its
            session id. Acknowledgements ("go", "ok", "what else") were 35%
            of the last 30 days' prompts and say nothing, so a prompt counts
            only at MIN_WORDS words or more and not a shell (!), slash (/)
            or wrapper (<) line. Shown: the opener, the one before the
            latest, and the latest — intent drifts over a session, and the
            last two show the drift.
  editing   the files the session's latest Write/Edit calls touched, read
            backward from its transcript in bounded chunks. Prose drifts;
            the file list is what another session would collide on.

Self is found by walking this process's ancestors to a registered pid, or
given with --self <session_id> (the hook takes it from stdin). Dead pids are
skipped. Test overrides: PEERS_SESSIONS_DIR, PEERS_PROJECTS_DIR, PEERS_HISTORY.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

HOME = Path.home()
MIN_WORDS = 5
CHUNK = 262144                # one backward read of the transcript
TAIL_MAX = 8 * CHUNK          # give up after 2 MB of tool output
MAX_FILES = 5
WIDTH = 100
LAST_DAYS = 7                 # a finished session older than this is not a handoff
HISTORY_DAYS = 14             # how far back the history pass reads (longest live session seen: 6 days)
CONTINUATION = "This session is being continued from a previous conversation"
EDIT_NAMES = {"Write", "Edit", "MultiEdit"}
EDIT_CALL = re.compile(r'"name":\s*"(Write|Edit|MultiEdit)"')
TS_FIELD = re.compile(r'"timestamp":\s*(\d+)')


def sessions_dir():
    return Path(os.environ.get("PEERS_SESSIONS_DIR") or HOME / ".claude" / "sessions")


def projects_dir():
    return Path(os.environ.get("PEERS_PROJECTS_DIR") or HOME / ".claude" / "projects")


def history_path():
    return Path(os.environ.get("PEERS_HISTORY") or HOME / ".claude" / "history.jsonl")


def alive(pid):
    try:
        os.kill(int(pid), 0)
        return True
    except ProcessLookupError:
        return False
    except Exception:
        return True


def toplevel(cwd):
    try:
        r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                           capture_output=True, text=True, timeout=3)
        return os.path.realpath(r.stdout.strip()) if r.returncode == 0 and r.stdout.strip() else os.path.realpath(cwd)
    except Exception:
        return os.path.realpath(cwd)


def ancestor_pids():
    pids, pid = [], os.getpid()
    for _ in range(12):
        try:
            pid = int(subprocess.run(["ps", "-o", "ppid=", "-p", str(pid)],
                                     capture_output=True, text=True, timeout=2).stdout.strip())
        except Exception:
            break
        if pid <= 1:
            break
        pids.append(pid)
    return pids


def slug(cwd):
    """Claude Code's project-dir name: every non-alphanumeric byte becomes '-'
    (`/Users/x/projects/.archive/y` -> `-Users-x-projects--archive-y`)."""
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


def transcript_for(session):
    return projects_dir() / slug(session["cwd"]) / f"{session['sessionId']}.jsonl"


def substantive(text):
    t = (text or "").strip()
    if not t or t[0] in "!/<" or t.startswith(CONTINUATION):
        return None
    t = re.sub(r"\s+", " ", t)
    return t if len(t.split()) >= MIN_WORDS else None


def read_history(since):
    """{sessionId: {"cwd": str, "prompts": [(ts_seconds, text_or_None), ...]}} for prompts after `since`.
    history.jsonl is append-only and chronological; the timestamp is read with a regex so only
    rows inside the window are JSON-parsed."""
    out = {}
    since_ms = int(since * 1000)
    try:
        with open(history_path(), errors="ignore") as fh:
            for line in fh:
                m = TS_FIELD.search(line)
                if not m or int(m.group(1)) < since_ms:
                    continue
                try:
                    j = json.loads(line)
                except Exception:
                    continue
                sid = j.get("sessionId")
                if not sid:
                    continue
                rec = out.setdefault(sid, {"cwd": j.get("project") or "", "prompts": []})
                rec["prompts"].append((int(m.group(1)) / 1000, substantive(j.get("display"))))
    except Exception:
        pass
    return out


def recent_edits(path):
    """Distinct files from the latest Write/Edit calls, newest first, plus the newest call's timestamp."""
    files, newest = [], None
    try:
        size = path.stat().st_size
        end = size
        with open(path, "rb") as fh:
            while end > 0 and size - end < TAIL_MAX and len(files) < MAX_FILES:
                start = max(0, end - CHUNK)
                fh.seek(start)
                lines = fh.read(end - start).decode("utf-8", errors="ignore").splitlines()
                if start > 0:
                    lines = lines[1:]  # cut mid-line; the next chunk holds it whole
                for line in reversed(lines):
                    if '"tool_use"' not in line or not EDIT_CALL.search(line):
                        continue
                    try:
                        j = json.loads(line)
                    except Exception:
                        continue
                    for block in (j.get("message") or {}).get("content") or []:
                        if block.get("type") == "tool_use" and block.get("name") in EDIT_NAMES:
                            p = (block.get("input") or {}).get("file_path")
                            if p and p not in files and not scratch(p):
                                files.append(p)
                            newest = newest or j.get("timestamp")
                    if len(files) >= MAX_FILES:
                        break
                end = start
    except Exception:
        pass
    return files, newest


def scratch(path):
    """Session scratchpads and temp files: nobody collides on them."""
    return "/scratchpad/" in path or path.startswith(("/private/tmp/", "/tmp/"))


def short(path, cwd):
    if path.startswith(cwd + os.sep):
        return path[len(cwd) + 1:]
    home = str(HOME)
    return "~" + path[len(home):] if path.startswith(home + os.sep) else path


def age(seconds):
    if seconds < 60:
        return f"{int(max(seconds, 0))}s"
    if seconds < 5400:
        return f"{int(seconds // 60)}m"
    if seconds < 172800:
        return f"{seconds / 3600:.0f}h"
    return f"{seconds / 86400:.0f}d"


def iso_age(ts, now):
    try:
        from datetime import datetime
        return age(now - datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp())
    except Exception:
        return "?"


def live_sessions():
    out = []
    for f in sessions_dir().glob("*.json"):
        try:
            j = json.loads(f.read_text())
        except Exception:
            continue
        if j.get("sessionId") and j.get("pid") and j.get("cwd") and alive(j["pid"]):
            out.append(j)
    return out


def body_lines(session, prompts, now):
    """opened / before / now / editing lines shared by live peers and the last finished session."""
    lines = []
    said = [t for _, t in prompts if t]
    if said:
        latest = said[-1]
        before = said[-2] if len(said) > 1 else None
        opener = said[0] if said[0] not in (latest, before) else None
        if opener:
            lines.append(f'    opened:  "{opener[:WIDTH]}"')
        if before:
            lines.append(f'    before:  "{before[:WIDTH]}"')
        lines.append(f'    now:     "{latest[:WIDTH]}"')
    files, newest = recent_edits(transcript_for(session))
    if files:
        when = f" ({iso_age(newest, now)} ago)" if newest else ""
        lines.append("    editing: " + ", ".join(short(p, session["cwd"]) for p in files) + when)
    return lines


def describe(session, prompts, now):
    up = age(now - session.get("startedAt", now * 1000) / 1000)
    head = f"- {session.get('name') or session['sessionId'][:8]}  {session.get('status', '?')} · up {up}"
    if prompts:
        head += f" · {len(prompts)} prompts · last {age(now - prompts[-1][0])} ago"
    return "\n".join([head] + body_lines(session, prompts, now))


def render_peers(others, hist, top, now):
    if not others:
        return ""
    scope = f" in this checkout ({os.path.basename(top)})" if top else ""
    others.sort(key=lambda s: -s.get("updatedAt", 0))
    body = "\n".join(describe(s, hist.get(s["sessionId"], {}).get("prompts", []), now) for s in others)
    return f"Other live sessions{scope} — {len(others)}:\n{body}"


def render_last(top, live_ids, hist, now):
    """The most recent finished session whose cwd is inside this checkout, within LAST_DAYS."""
    best = None
    for sid, rec in hist.items():
        if sid in live_ids or not rec["prompts"] or not rec["cwd"]:
            continue
        cwd = os.path.realpath(rec["cwd"])
        if cwd != top and not cwd.startswith(top + os.sep):
            continue
        ended = rec["prompts"][-1][0]
        if now - ended > LAST_DAYS * 86400:
            continue
        if best is None or ended > best[1]:
            best = (sid, ended, rec)
    if not best:
        return ""
    sid, ended, rec = best
    started = rec["prompts"][0][0]
    session = {"sessionId": sid, "cwd": rec["cwd"]}
    lines = [f"Last session in this checkout ({os.path.basename(top)}) ended {age(now - ended)} ago "
             f"· ran {age(ended - started)} · {len(rec['prompts'])} prompts:"]
    handoff = projects_dir() / slug(top) / "memory" / "HANDOFF.md"
    try:
        if handoff.exists():
            h_age = now - handoff.stat().st_mtime
            stale = " — older than that session, so it may be stale" if handoff.stat().st_mtime < ended else ""
            lines.append(f"    handoff: memory/HANDOFF.md written {age(h_age)} ago{stale}")
    except Exception:
        pass
    lines += body_lines(session, rec["prompts"], now)
    return "\n".join(lines)


def portfolio_root(cwd):
    """The directory that holds this checkout: the default scope of a listing."""
    return os.path.dirname(toplevel(cwd))


def render(here, last, self_id, cwd, everything=False):
    now = time.time()
    sessions = live_sessions()
    if self_id is None:
        anc = set(ancestor_pids())
        self_id = next((s["sessionId"] for s in sessions if int(s["pid"]) in anc), None)
    others = [s for s in sessions if s["sessionId"] != self_id]
    top = toplevel(cwd) if (here or last) else None
    if here:
        others = [s for s in others if toplevel(s["cwd"]) == top]
    elif not everything:
        # a listing stays inside its own portfolio root: two roots on one machine
        # may be walled from each other, and a wall is only as good as its default
        root = portfolio_root(cwd)
        others = [s for s in others if os.path.realpath(s["cwd"]).startswith(root + os.sep)]
    hist = read_history(now - HISTORY_DAYS * 86400)
    blocks = []
    if last:
        blocks.append(render_last(top, {s["sessionId"] for s in sessions}, hist, now))
    if here or not last:
        blocks.append(render_peers(others, hist, top, now))
    return "\n\n".join(b for b in blocks if b)


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--here", action="store_true", help="only sessions sharing this checkout")
    ap.add_argument("--all", action="store_true", help="every root on the machine (default: the checkout's own parent directory)")
    ap.add_argument("--last", action="store_true", help="the most recent finished session in this checkout")
    ap.add_argument("--hook", action="store_true", help="SessionStart hook mode: read stdin, print --last and --here blocks, or nothing")
    ap.add_argument("--self", dest="self_id", help="this session's id (excluded from the list)")
    ap.add_argument("--cwd", default=os.getcwd())
    a = ap.parse_args()
    if a.hook:
        try:
            payload = json.load(sys.stdin)
        except Exception:
            payload = {}
        out = render(True, True, payload.get("session_id") or a.self_id, payload.get("cwd") or a.cwd)
        if out:
            print(out)
            print("Before editing a file a live peer is on, or committing while one is busy, tell it (SendMessage to the name). "
                  f"Refresh any time: python3 {Path(__file__).resolve()} --here --last")
        return
    out = render(a.here, a.last, a.self_id, a.cwd, everything=a.all)
    if out:
        print(out)
    elif a.last and not a.here:
        print("No finished session in this checkout in the last 7 days.")
    else:
        print("No other live sessions" + (" in this checkout." if a.here else (" anywhere." if a.all else " under this root.")))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass  # a peer listing must never block a session start
    sys.exit(0)

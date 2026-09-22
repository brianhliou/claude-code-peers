#!/usr/bin/env python3
"""How many Claude Code sessions run at once, and how long they live, from transcripts.

    scripts/session_concurrency.py [--days 30]

Each transcript under ~/.claude/projects/<slug>/<session>.jsonl carries a
timestamp per line; a session is live between its first and last one.
Sampled hourly: peak concurrent sessions per day (any checkout, and the
most in one checkout), then lifetime percentiles. Subagent transcripts sit
one level deeper and are not counted.

Measured 2026-09-21 for the multi-session coordination build: median daily
peak 8, max 12, up to 10 in one checkout; lifetime median 5 h, p90 39 h.
"""
import argparse
import datetime as dt
import glob
import json
import os
from collections import defaultdict

ROOT = os.path.expanduser("~/.claude/projects")


def span(path):
    first = last = None
    with open(path, errors="ignore") as fh:
        for line in fh:
            if '"timestamp"' not in line:
                continue
            try:
                ts = json.loads(line).get("timestamp")
            except Exception:
                continue
            if not ts:
                continue
            t = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            first = first or t
            last = t
    return first, last


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--days", type=int, default=30)
    a = ap.parse_args()
    now = dt.datetime.now(dt.timezone.utc)
    cutoff = now - dt.timedelta(days=a.days)

    spans = []
    for f in glob.glob(f"{ROOT}/*/*.jsonl"):
        if dt.datetime.fromtimestamp(os.path.getmtime(f), dt.timezone.utc) < cutoff:
            continue
        first, last = span(f)
        if first and last and last > cutoff:
            spans.append((os.path.basename(os.path.dirname(f)), first, last))

    peak, same = defaultdict(int), defaultdict(int)
    t = cutoff.replace(minute=0, second=0, microsecond=0)
    while t < now:
        live = [p for p, a_, b in spans if a_ <= t <= b]
        day = t.astimezone().date().isoformat()
        peak[day] = max(peak[day], len(live))
        per = defaultdict(int)
        for p in live:
            per[p] += 1
        same[day] = max(same[day], max(per.values(), default=0))
        t += dt.timedelta(hours=1)

    print(f"sessions live in the last {a.days}d: {len(spans)}")
    print("peak concurrent per day (any checkout | most in one checkout):")
    for day in sorted(peak):
        print(f"  {day}  {peak[day]:2d} | {same[day]}")
    v = sorted(peak.values())
    print(f"median daily peak {v[len(v) // 2]}, max {v[-1]}")
    life = sorted((b - a_).total_seconds() / 3600 for _, a_, b in spans)
    print(f"lifetime hours: median {life[len(life) // 2]:.1f}, p90 {life[int(len(life) * .9)]:.1f}, max {life[-1]:.0f}")


if __name__ == "__main__":
    main()

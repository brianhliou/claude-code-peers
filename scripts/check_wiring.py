#!/usr/bin/env python3
"""Is every hook registered, present, importable, and passing its test?

The behaviour of each hook is covered by the fixture test beside it
(test_<name>.py). What those cannot see is the wiring: the entry in
~/.claude/settings.json, the path it names, and whether the script still
compiles. A hook that is registered but broken fails silent by design, so
nothing else would notice. Run:

    python3 scripts/check_wiring.py          # exit 1 on any problem

Checks, per registered command that points into this repo:
  1. the script exists and compiles
  2. its sibling test_<name>.py, if any, passes
and that each hook this repo ships is registered at all.
"""
import json
import os
import py_compile
import re
import subprocess
import sys
from pathlib import Path

HOME = Path.home()
REPO = Path(__file__).resolve().parents[1]  # scripts/ -> repo root
SETTINGS = HOME / ".claude" / "settings.json"
EXPECTED = {  # event -> script basenames this repo expects to be registered
    "SessionStart": {"peers.py"},
    "UserPromptSubmit": {"memory_delta.py"},
    "PreToolUse": {"git_index_guard.py"},
}


def registered():
    """{event: [script path, ...]} for every hook command in settings.json."""
    out = {}
    hooks = json.loads(SETTINGS.read_text()).get("hooks", {})
    for event, groups in hooks.items():
        for g in groups:
            for h in g.get("hooks", []):
                m = re.search(r"python3\s+(\S+)", h.get("command", ""))
                if m:
                    out.setdefault(event, []).append(Path(os.path.expanduser(m.group(1))))
    return out


def main():
    problems = []
    reg = registered()
    for event, names in EXPECTED.items():
        have = {p.name for p in reg.get(event, [])}
        for missing in names - have:
            problems.append(f"{event}: {missing} is not registered in settings.json")
    for event, paths in reg.items():
        for p in paths:
            if REPO not in p.resolve().parents:
                continue
            if not p.exists():
                problems.append(f"{event}: {p} does not exist")
                continue
            try:
                py_compile.compile(str(p), doraise=True)
            except py_compile.PyCompileError as exc:
                problems.append(f"{event}: {p.name} does not compile: {exc}")
                continue
            test = p.with_name(f"test_{p.name}")
            if test.exists():
                r = subprocess.run([sys.executable, str(test)], capture_output=True, text=True, timeout=300)
                if r.returncode != 0:
                    problems.append(f"{event}: {test.name} failed:\n{(r.stderr or r.stdout).strip()[-600:]}")

    n = sum(len(v) for v in reg.values())
    if problems:
        print(f"hook wiring: {len(problems)} problem(s) across {n} registered hooks")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(f"hook wiring OK — {n} registered hooks exist and compile, sibling tests pass")
    return 0


if __name__ == "__main__":
    sys.exit(main())

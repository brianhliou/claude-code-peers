#!/usr/bin/env python3
"""Is every hook registered, present, importable, and passing its test?

The behaviour of each hook is covered by the fixture test beside it
(test_<name>.py). What those cannot see is the wiring: the entry in
~/.claude/settings.json or the installed plugin, the path it names, and
whether the script still compiles. A hook that is registered but broken fails
silent by design, so nothing else would notice. Run:

    python3 scripts/check_wiring.py          # exit 1 on any problem

A hook counts as registered from either install path:
  settings.json   a hook command in ~/.claude/settings.json naming a script
  plugin          a claude-code-peers@<marketplace> entry in
                  ~/.claude/plugins/installed_plugins.json, not set to false
                  under enabledPlugins; its hooks come from
                  <installPath>/hooks/hooks.json with ${CLAUDE_PLUGIN_ROOT}
                  replaced by installPath

Checks, per registered command that points into this repo or the plugin:
  1. the script exists and compiles
  2. its sibling test_<name>.py, if any, passes
that each hook this repo ships is registered at all, and that none is
registered twice. Claude Code does not merge a plugin hook with the same
command in settings.json, so both installs at once run every hook twice.

CLAUDE_CONFIG_DIR moves ~/.claude and CLAUDE_CODE_PLUGIN_CACHE_DIR moves
~/.claude/plugins, as they do for Claude Code itself.
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
CONFIG = Path(os.environ.get("CLAUDE_CONFIG_DIR") or HOME / ".claude")
SETTINGS = CONFIG / "settings.json"
PLUGINS = Path(os.environ.get("CLAUDE_CODE_PLUGIN_CACHE_DIR") or CONFIG / "plugins")
PLUGIN = "claude-code-peers"
SCRIPT = re.compile(r'python3\s+"?([^"\s]+)"?')
EXPECTED = {  # event -> script basenames this repo expects to be registered
    "SessionStart": {"peers.py"},
    "UserPromptSubmit": {"memory_delta.py"},
    "PreToolUse": {"git_index_guard.py"},
}


def read_json(path):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return {}


def scripts_in(hooks, root=None):
    """[(event, script path)] for every python3 command in a hooks object."""
    out = []
    for event, groups in (hooks or {}).items():
        for g in groups:
            for h in g.get("hooks", []):
                cmd = h.get("command", "")
                if root is not None:
                    cmd = cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(root))
                m = SCRIPT.search(cmd)
                if m:
                    out.append((event, Path(os.path.expanduser(m.group(1)))))
    return out


def plugin_installs(settings):
    """[(plugin id, install path)] for every enabled install of this plugin."""
    enabled = settings.get("enabledPlugins") or {}
    out = []
    for pid, records in (read_json(PLUGINS / "installed_plugins.json").get("plugins") or {}).items():
        if pid.split("@")[0] != PLUGIN or enabled.get(pid) is False:
            continue
        for rec in records if isinstance(records, list) else [records]:
            if rec.get("installPath"):
                out.append((pid, Path(rec["installPath"])))
    return out


def registered():
    """{event: [(script path, where it was registered), ...]} across settings.json and the plugin."""
    out = {}
    settings = read_json(SETTINGS)
    for event, p in scripts_in(settings.get("hooks")):
        out.setdefault(event, []).append((p, "settings.json"))
    for pid, root in plugin_installs(settings):
        for event, p in scripts_in(read_json(root / "hooks" / "hooks.json").get("hooks"), root):
            out.setdefault(event, []).append((p, f"plugin {pid}"))
    return out


def main():
    problems = []
    reg = registered()
    for event, names in EXPECTED.items():
        for name in sorted(names):
            where = [src for p, src in reg.get(event, []) if p.name == name]
            if not where:
                problems.append(f"{event}: {name} is not registered in settings.json or as the {PLUGIN} plugin")
            elif len(where) > 1:
                problems.append(f"{event}: {name} is registered {len(where)} times ({', '.join(where)}), "
                                "so it runs once per registration; keep one install path")
    for event, entries in reg.items():
        for p, src in entries:
            if src == "settings.json" and REPO not in p.resolve().parents:
                continue
            if not p.exists():
                problems.append(f"{event}: {p} does not exist ({src})")
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
                    problems.append(f"{event}: {test.name} failed ({src}):\n{(r.stderr or r.stdout).strip()[-600:]}")

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

#!/usr/bin/env python3
"""Fixture test for the plugin packaging. Run: python3 scripts/test_plugin.py

Checks the three files the plugin install reads (.claude-plugin/plugin.json,
.claude-plugin/marketplace.json, hooks/hooks.json) against each other and
against the manual settings.json block in the README, so the two install
paths register the same hooks with the same matchers and timeouts. Then runs
each hooks.json command through `sh -c` with CLAUDE_PLUGIN_ROOT set, the way
Claude Code runs a shell-form plugin hook, and drives check_wiring.py against
a fake config dir. Nothing in ~/.claude is read or written: every state
directory the hooks and check_wiring.py read is overridden through env.

`claude plugin validate --strict .` is the authoritative schema check; this
test covers what the validator does not know: which scripts should be wired,
and that the plugin and the README agree.
"""
import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
PLUGIN_JSON = REPO / ".claude-plugin" / "plugin.json"
MARKETPLACE_JSON = REPO / ".claude-plugin" / "marketplace.json"
HOOKS_JSON = REPO / "hooks" / "hooks.json"
README = REPO / "README.md"
CHECK_WIRING = REPO / "scripts" / "check_wiring.py"
KEBAB = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
# from the marketplace reference; a name in this list, or one imitating it, is refused at add time
RESERVED = {"claude-code-marketplace", "claude-code-plugins", "claude-plugins-official", "anthropic-marketplace",
            "anthropic-plugins", "agent-skills", "anthropic-agent-skills", "claude-community",
            "claude-plugins-community", "inline", "builtin", "skills-dir", "synced", "claude-plugin-test",
            "npm", "pip", "uv", "cargo", "github", "gh"}
WIRED = {  # event -> (matcher, timeout, script and args): the README's table, restated
    "SessionStart": ("startup|resume", 10, "peers.py --hook"),
    "UserPromptSubmit": (None, 5, "memory_delta.py"),
    "PreToolUse": ("Bash", 5, "git_index_guard.py"),
}


def load(path):
    return json.loads(path.read_text())


def wiring(hooks):
    """{event: [(matcher, timeout, 'script.py args')]} from a hooks object, command paths stripped."""
    out = {}
    for event, groups in hooks.items():
        for g in groups:
            for h in g["hooks"]:
                assert h.get("type") == "command", (event, h)
                words = shlex.split(h["command"])
                assert words[0] == "python3", (event, h["command"])
                tail = " ".join([Path(words[1]).name] + words[2:])
                out.setdefault(event, []).append((g.get("matcher"), h.get("timeout"), tail))
    return out


def readme_hooks():
    """The hooks object in the README's manual-install settings.json block."""
    for block in re.findall(r"```json\n(.*?)```", README.read_text(), re.S):
        j = json.loads(block)
        if "hooks" in j:
            return j["hooks"]
    raise AssertionError("README has no ```json block with a hooks object")


def env_for(tmp, **extra):
    env = {k: v for k, v in os.environ.items() if k not in ("CLAUDE_CONFIG_DIR", "CLAUDE_CODE_PLUGIN_CACHE_DIR")}
    empty = tmp / "empty"
    empty.mkdir(exist_ok=True)
    env.update({
        "CLAUDE_PLUGIN_ROOT": str(REPO),
        "PEERS_SESSIONS_DIR": str(empty), "PEERS_PROJECTS_DIR": str(empty),
        "PEERS_HISTORY": str(tmp / "history.jsonl"),
        "MEMORY_DELTA_SESSIONS_DIR": str(empty), "MEMORY_DELTA_STATE_DIR": str(tmp / "md-state"),
        "GIT_GUARD_SESSIONS_DIR": str(empty),
    })
    env.update(extra)
    return env


def git(*args, cwd):
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def main():
    plugin, market, hooks_file = load(PLUGIN_JSON), load(MARKETPLACE_JSON), load(HOOKS_JSON)

    # 1. plugin.json: the fields the validator warns on, plus a name that is the install id
    assert KEBAB.match(plugin["name"]), plugin["name"]
    for key in ("version", "description", "license"):
        assert isinstance(plugin.get(key), str) and plugin[key], key
    assert plugin["author"]["name"], plugin["author"]
    assert "hooks" not in plugin, "hooks/hooks.json loads by default; naming it again in plugin.json double-loads it"
    assert set(os.listdir(REPO / ".claude-plugin")) <= {"plugin.json", "marketplace.json"}, \
        "only manifests go in .claude-plugin/"
    print("1 plugin.json: kebab name, version, description, author, license; hooks left to the default path  OK")

    # 2. marketplace.json: required fields, one entry pointing at this repo's root
    assert KEBAB.match(market["name"]) and market["name"] not in RESERVED, market["name"]
    assert market["owner"]["name"], market["owner"]
    assert market.get("description"), "validate warns without a description"
    entries = market["plugins"]
    assert len(entries) == 1, entries
    entry = entries[0]
    assert entry["name"] == plugin["name"], "entry name and manifest name must match or install-by-name fails"
    assert entry["source"] in ("./", "."), entry["source"]
    assert (REPO / entry["source"] / ".claude-plugin" / "plugin.json").resolve() == PLUGIN_JSON
    assert "version" not in entry or entry["version"] == plugin["version"], "plugin.json version wins; keep one"
    assert "hooks" not in entry, "entry hooks would replace hooks/hooks.json per event"
    print(f"2 marketplace.json: '{market['name']}' lists {entry['name']} at source {entry['source']}  OK")

    # 3. hooks.json: every command runs a script that exists under the plugin root, quoted
    hooks = hooks_file["hooks"]
    for event, groups in hooks.items():
        for g in groups:
            for h in g["hooks"]:
                cmd = h["command"]
                assert '"${CLAUDE_PLUGIN_ROOT}/' in cmd, f"unquoted or missing plugin root: {cmd}"
                path = Path(shlex.split(cmd.replace("${CLAUDE_PLUGIN_ROOT}", str(REPO)))[1]).resolve()
                assert REPO in path.parents and path.is_file(), (event, path)
    print(f"3 hooks.json: {sum(len(g['hooks']) for v in hooks.values() for g in v)} commands, "
          "each a quoted ${CLAUDE_PLUGIN_ROOT} path to an existing script  OK")

    # 4. the plugin and the README's manual block register the same hooks, and both match the table
    plugin_w, readme_w = wiring(hooks), wiring(readme_hooks())
    assert plugin_w == readme_w, (plugin_w, readme_w)
    assert plugin_w == {e: [w] for e, w in WIRED.items()}, plugin_w
    print("4 hooks.json == README settings.json block == table: events, matchers, timeouts, args  OK")

    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(os.path.realpath(tmp))
        repo = tmp / "repo"
        repo.mkdir()
        git("init", "-q", cwd=repo)
        (repo / "f").write_text("x")
        git("add", "f", cwd=repo)
        git("commit", "-qm", "init", cwd=repo)

        # 5. each command, run as Claude Code runs a shell-form plugin hook, exits 0 on an empty machine
        def fire(event, env, payload):
            h = hooks[event][0]["hooks"][0]
            return subprocess.run(["sh", "-c", h["command"]], input=json.dumps(payload), text=True,
                                  capture_output=True, env=env, cwd=repo, timeout=h["timeout"] + 10)

        env = env_for(tmp)
        base = {"session_id": "S-me", "cwd": str(repo), "transcript_path": str(tmp / "t.jsonl")}
        for event, payload in (("SessionStart", {**base, "hook_event_name": "SessionStart", "source": "startup"}),
                               ("UserPromptSubmit", {**base, "hook_event_name": "UserPromptSubmit", "prompt": "hi"}),
                               ("PreToolUse", {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash",
                                               "tool_input": {"command": "git add -A"}})):
            p = fire(event, env, payload)
            assert p.returncode == 0 and p.stderr == "", (event, p.returncode, p.stderr)
        print("5 all three commands run through sh -c with CLAUDE_PLUGIN_ROOT set: exit 0, silent when alone  OK")

        # 6. the wired guard refuses a broad add once a live peer shares the checkout
        sessions = tmp / "sessions"
        sessions.mkdir()
        for i, sid in enumerate(("S-me", "S-peer")):
            (sessions / f"{i}.json").write_text(json.dumps(
                {"sessionId": sid, "pid": os.getpid(), "cwd": str(repo), "name": sid.lower()}))
        p = fire("PreToolUse", env_for(tmp, GIT_GUARD_SESSIONS_DIR=str(sessions)),
                 {**base, "hook_event_name": "PreToolUse", "tool_name": "Bash", "tool_input": {"command": "git add -A"}})
        assert p.returncode == 2 and "s-peer" in p.stderr, (p.returncode, p.stderr)
        print("6 guard through the plugin command: git add -A refused with a live peer (exit 2)  OK")

        # 7. check_wiring.py sees a plugin install, and flags the plugin plus settings.json as a double install
        config = tmp / "config"
        (config / "plugins").mkdir(parents=True)
        pid = f"{plugin['name']}@{market['name']}"
        (config / "plugins" / "installed_plugins.json").write_text(json.dumps(
            {"version": 2, "plugins": {pid: [{"scope": "user", "installPath": str(REPO), "version": plugin["version"]}]}}))

        def check(settings):
            (config / "settings.json").write_text(json.dumps(settings))
            return subprocess.run([sys.executable, str(CHECK_WIRING)], capture_output=True, text=True, timeout=300,
                                  env={**env_for(tmp), "CLAUDE_CONFIG_DIR": str(config)})

        p = check({"enabledPlugins": {pid: True}})
        assert p.returncode == 0 and "3 registered hooks" in p.stdout, p.stdout + p.stderr
        manual = json.loads(json.dumps(readme_hooks()).replace("/path/to/claude-code-peers", str(REPO)))
        p = check({"enabledPlugins": {pid: True}, "hooks": manual})
        assert p.returncode == 1 and p.stdout.count("registered 2 times") == 3, p.stdout
        p = check({"enabledPlugins": {pid: False}})
        assert p.returncode == 1 and p.stdout.count("is not registered") == 3, p.stdout
        p = check({"enabledPlugins": {pid: False}, "hooks": manual})
        assert p.returncode == 0, p.stdout
        print("7 check_wiring: plugin alone OK, plugin + settings.json flagged twice-registered, "
              "disabled plugin not counted, manual alone OK  OK")
    print("all OK")


if __name__ == "__main__":
    main()

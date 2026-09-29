# claude-code-peers

Three hooks and a measurement script that let concurrent Claude Code sessions on one machine see each other. Measured over thirty days on the machine they were built for: a median of eight sessions alive at once, up to ten in one checkout, and only seven of 297 that ever sent a peer a message. Every piece reads state Claude Code already writes (the session registry, `history.jsonl`, the transcripts) and none adds a field a session has to maintain.

**Write-up:** [Eight Claude Code Sessions, One Checkout](https://brianhliou.com/posts/eight-sessions-one-checkout/)

## What each piece does

| piece | hook | what it does |
|---|---|---|
| `scripts/peers.py --hook` | `SessionStart` | prints the last session that finished in this checkout (when, how long, what it was on, what it edited) and the live peers sharing it, each with its opening, previous and latest substantive prompt and the files it is editing |
| `scripts/memory_delta.py` | `UserPromptSubmit` | lists auto-memory files a peer wrote since this session started, once each; own writes are excluded via the transcript |
| `scripts/git_index_guard.py` | `PreToolUse` (Bash) | refuses `git add -A`, `add .`, `commit -a` and a pathless `stash` while another live session shares the checkout; worktrees don't count; alone, the command runs untouched |
| `scripts/session_concurrency.py` | none | peak concurrent sessions per day and lifetime percentiles, from transcripts |
| `scripts/check_wiring.py` | none | asserts the three hooks are registered once (plugin or `settings.json`), exist, compile, and pass their tests |

`peers.py` also runs by hand: no flags lists every live session under this checkout's parent directory, `--here` only this checkout, `--last` the most recent finished session here, `--all` every root on the machine.

## Install

Python 3.10+ and git; no other dependencies. Pick one of the two paths below. Claude Code runs a plugin's hook and an identical hook in `settings.json` as two separate hooks, so installing both runs every hook twice; `check_wiring.py` reports that case.

### As a plugin

In a session:

```
/plugin marketplace add brianhliou/claude-code-peers
/plugin install claude-code-peers@claude-code-peers
```

Or from a shell, `claude plugin marketplace add brianhliou/claude-code-peers` and then `claude plugin install claude-code-peers@claude-code-peers`. The repository is its own marketplace (`.claude-plugin/marketplace.json`) and its own plugin (`.claude-plugin/plugin.json`), and `hooks/hooks.json` registers the same three hooks as the block below, with the same matchers and timeouts, pointing at `scripts/` through `${CLAUDE_PLUGIN_ROOT}`. New sessions load it; `/reload-plugins` loads it into a running one. `claude plugin uninstall claude-code-peers@claude-code-peers` removes it.

### By hand, in settings.json

Clone anywhere, then register the three hooks in `~/.claude/settings.json` (paths absolute to where you cloned):

```json
{
  "hooks": {
    "SessionStart": [{
      "matcher": "startup|resume",
      "hooks": [{ "type": "command", "timeout": 10,
                  "command": "python3 /path/to/claude-code-peers/scripts/peers.py --hook" }]
    }],
    "UserPromptSubmit": [{
      "hooks": [{ "type": "command", "timeout": 5,
                  "command": "python3 /path/to/claude-code-peers/scripts/memory_delta.py" }]
    }],
    "PreToolUse": [{
      "matcher": "Bash",
      "hooks": [{ "type": "command", "timeout": 5,
                  "command": "python3 /path/to/claude-code-peers/scripts/git_index_guard.py" }]
    }]
  }
}
```

A `SessionStart` hook reaches sessions started after registration; the other two were observed reaching a running session as well.

## Check it without trusting the author

```bash
python3 scripts/test_peers.py
python3 scripts/test_memory_delta.py
python3 scripts/test_git_index_guard.py
python3 scripts/test_plugin.py
claude plugin validate --strict .   # Claude Code's own manifest and hooks.json check
python3 scripts/check_wiring.py     # after installing, either way
python3 scripts/session_concurrency.py --days 30
```

The tests build real git repositories and a worktree in a temp directory, fake the session registry, history and transcripts in the shapes Claude Code writes, and drive each hook through stdin exactly as the harness does. `test_plugin.py` checks that `hooks/hooks.json` and the `settings.json` block above register the same scripts with the same matchers and timeouts, runs each plugin command through `sh -c` the way Claude Code runs it, and points `check_wiring.py` at a fake config directory. Nothing in `~/.claude` is touched.

## How this differs

Claude Code ships its own [cross-session messaging](https://code.claude.com/docs/en/cross-session-messaging): `ListAgents` lists your other live sessions by name, and `SendMessage` delivers a message to one of them over a local socket. The name `peers.py` prints is the address `SendMessage` takes, so the two work together. Messaging carries what one session decides to tell another, and nothing moves until a session sends. These hooks read what every session already leaves on disk (its prompts, the files it wrote, the memory it saved) and put it in front of the others with nobody sending anything. On this machine that gap was the whole problem: in thirty days, 118 sessions listed their peers and seven sent a message. The git index guard is the other difference. Messaging can warn a peer before a broad `git add`; the guard refuses the command.

| tool | what it does | the difference |
|---|---|---|
| [inter-session](https://github.com/yilunzhang/claude-code-inter-session) | a plugin that joins each session to a local WebSocket bus; sessions address each other by name and incoming messages arrive as prompts | messaging, like the built-in tools; a session sees a peer when the peer sends |
| [session-bridge](https://github.com/PatilShreyas/claude-code-session-bridge) | one session runs `/bridge listen` and polls a directory under `~/.claude/session-bridge/`; another asks it questions with `/bridge ask` | built for sessions in different projects querying each other; both sides start it by hand |
| [huddle](https://github.com/timothyfroehlich/huddle) | hooks write session updates as comments on beads (`bd`) issues in the repository and inject recent peer posts at start and during the session; announces `gh pr create` | the nearest design; it keeps its own store and needs beads plus a registry file per repository, where these hooks read Claude Code's own files and keep none |
| [GitButler](https://blog.gitbutler.com/parallel-claude-code) | Claude Code hooks tell GitButler which session edited what; each session gets its own branch in one working directory and its changes are committed there when a chat ends | sorts concurrent edits into branches after they happen; the guard here stops the one staging command that mixes them, and leaves branching to you |

## What it reads

| file | what it holds | who reads it |
|---|---|---|
| `~/.claude/sessions/<pid>.json` | one file per live session: id, cwd, name, status, start time; removed on exit | all three hooks |
| `~/.claude/history.jsonl` | every typed prompt with its session id | `peers.py` |
| `~/.claude/projects/<slug>/<session>.jsonl` | the transcript: every tool call, including the path of every file written | `peers.py`, `memory_delta.py` |
| `~/.claude/projects/<slug>/memory/` | auto memory, one directory per repository | `memory_delta.py` |

## Limits

The guard refuses; it does not lock. A peer's `git reset --hard` still reverts your uncommitted edits. `memory_delta` covers the memory directory and not `CLAUDE.md`. A peer whose last edit is buried under two megabytes of tool output shows no edited files rather than stale ones. Everything assumes one machine: the registry and transcripts are local, so cloud sessions are invisible.

## Status

Built 2026-09-21/22. The claim under test is that showing intent makes sessions talk; baseline is seven sessions sending a message in thirty days, re-measured at the end of October 2026 in the write-up.

## License

MIT.

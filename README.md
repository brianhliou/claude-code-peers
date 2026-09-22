# claude-code-peers

Three hooks and a measurement script that let concurrent Claude Code sessions on one machine see each other. Measured over thirty days on the machine they were built for: a median of eight sessions alive at once, up to ten in one checkout, and only seven of 297 that ever sent a peer a message. Every piece reads state Claude Code already writes (the session registry, `history.jsonl`, the transcripts) and none adds a field a session has to maintain.

**Write-up:** [Eight Sessions, One Checkout](https://brianhliou.com/posts/eight-sessions-one-checkout/)

## What each piece does

| piece | hook | what it does |
|---|---|---|
| `scripts/peers.py --hook` | `SessionStart` | prints the last session that finished in this checkout (when, how long, what it was on, what it edited) and the live peers sharing it, each with its opening, previous and latest substantive prompt and the files it is editing |
| `scripts/memory_delta.py` | `UserPromptSubmit` | lists auto-memory files a peer wrote since this session started, once each; own writes are excluded via the transcript |
| `scripts/git_index_guard.py` | `PreToolUse` (Bash) | refuses `git add -A`, `add .`, `commit -a` and a pathless `stash` while another live session shares the checkout; worktrees don't count; alone, the command runs untouched |
| `scripts/session_concurrency.py` | none | peak concurrent sessions per day and lifetime percentiles, from transcripts |
| `scripts/check_wiring.py` | none | asserts the three hooks are registered, exist, compile, and pass their tests |

`peers.py` also runs by hand: no flags lists every live session under this checkout's parent directory, `--here` only this checkout, `--last` the most recent finished session here, `--all` every root on the machine.

## Install

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

Python 3.10+ and git; no other dependencies. A `SessionStart` hook reaches sessions started after registration; the other two were observed reaching a running session as well.

## Check it without trusting the author

```bash
python3 scripts/test_peers.py
python3 scripts/test_memory_delta.py
python3 scripts/test_git_index_guard.py
python3 scripts/check_wiring.py     # after registering the hooks
python3 scripts/session_concurrency.py --days 30
```

The tests build real git repositories and a worktree in a temp directory, fake the session registry, history and transcripts in the shapes Claude Code writes, and drive each hook through stdin exactly as the harness does. Nothing in `~/.claude` is touched.

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

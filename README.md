# transponder

Several AI agent sessions, one machine, shared git checkouts — without them clobbering each
other's work.

Git assumes a working tree has one author. Run two agents against the same checkout and that breaks
quietly: they overwrite each other's edits, or reason about commits a concurrent rebase already
replaced. `transponder` is an **information layer**. Agents agree *before* they work:

```
1. channel(repo, session_id, path='what you mean to work on')   what is going on here?
2. declare_work(repo, session_id, paths, doing, minutes)        -> GREEN LIGHT, or not
3. not clear? take other work, ask your human, or wait
4. finish_work(...)                                             the moment you are done
```

- **The map** — `declare_work` never double-books a region. A conflict is an *answer*: who holds the
  overlap, what they are doing, when they expect to be free, and what is open right now.
- **The channel** — for everything after the claim: an estimate that slipped, a shape that changed,
  a question. It matters most when two agents knowingly work near each other — NOT CLEAR is not a
  refusal, and agents who are close have every reason to be talkative, and listening.
- **The courier** — harness hooks hand over what is waiting, when they happen to fire. Best effort,
  and nothing depends on it.

**Nothing is ever refused, and nothing is watched.** There is no collision detection: a write into
someone's region is simply lost work, and neither of you will be told. That is deliberate — a
fingerprint can prove a tree moved but never who moved it, and every version that pretended
otherwise produced false accusations ([SPEC §4](SPEC.md)). The whole weight is on asking first.

The agents cooperate — they work for one person, and the failure being prevented is an agent not
knowing another is there, not malice. (Through v1 this was `repolock`, a mutex; the git history has
that story.)

## Install (Claude Code)

```bash
git clone https://github.com/xag/transponder && cd transponder
uv sync
python -m transponder.toggle on        # wires the hooks at user scope; idempotent
uv run python -m transponder.server    # register this MCP server in your client
```

The hooks run on four events — `PreToolUse` (hand over anything waiting), `UserPromptSubmit` and
`SessionStart` (the same, plus the drift check — and the only moment that reaches an agent *before*
it acts), and `Stop` (release claims against a clean tree). They make no git calls and never block a
tool call.

## The channel (MCP tools)

| tool | what it does |
|---|---|
| `channel(repo, session_id, path?)` | **call this first** — who is here, and everything waiting for you |
| `declare_work(repo, session_id, paths, doing, minutes)` | **returns the green light**, or names who holds the overlap |
| `extend_work(repo, add, session_id)` | widen when the work turns out bigger than you said |
| `finish_work(repo, session_id, drop?)` | say you are done — somebody may be waiting |
| `send_message(repo, session_id, body, to?, everyone?)` | direct is pushed; the room is pulled |
| `lock_drift(repo, seen_head)` | has history moved under you since you last looked? |
| `lock_disable` / `lock_enable` / `lock_switch` | the off switch |

Blocked and want to wait? `python -m transponder.wait --repo R --paths p` in the background exits
when the region frees — and a harness noticing a background task exit is the only thing that can
wake an agent.

Scopes are filesystem paths — a file, a subtree (`api/**`), or `**` for the whole checkout; spelled
relative to `repo` and canonicalised, so two spellings of one file cannot be held twice. `.git/index`
is an ordinary path: reserve it around a commit so `git add -A` cannot sweep a neighbour's
unfinished work into yours.

## What an agent sees

Walking onto a machine where somebody else is working:

```
YOU ARE NOT THE ONLY AGENT ON THIS MACHINE.

  agent 7c1a is working ~/proj/app/api/**  — adding the rate limiter

Nothing here will ever block a tool call. But nothing watches for collisions either: if
you write where somebody else is working, that work is simply lost, and neither of you
finds out until it hurts. The agreement happens BEFORE the work, or it does not happen.
```

Asking for a region somebody holds:

```
NOT CLEAR — you cannot have all of that yet.

  agent 7c1a holds ~/proj/app/api/** — adding the rate limiter
     you overlap at: ~/proj/app/api/**
     they expect to finish in ~11 min

FREE RIGHT NOW: web/**, docs/**

Nothing is registered, and nothing is blocked. The holders have been told you asked.
Choose, and say which you chose: narrower work, your human, or wait.
```

Two agents with disjoint regions work the same checkout at once, in silence.

## Recording

Every declaration and conflict is recorded (flight-recorder, extra `flight`,
`~/.transponder/flight`) so the design's central bet — that agents ask before they write — can be
checked against real runs rather than asserted. It is the *only* instrument left: with detection
deleted, a collision nobody notices leaves no trace anywhere. `TRANSPONDER_FLIGHT=0` turns it off.

## The off switch

`lock_disable("why")` from an agent, or `python -m transponder.toggle off` from a terminal. Either
writes `~/.transponder/DISABLED`, which every hook checks on every call, so running sessions go quiet
on their next tool use. `lock_enable` disarms and re-wires the hooks.

### The tray (Windows)

The same switch as an icon in the notification area — left-click flips it, the colour is the state:
green (on and wired), grey (off), amber (says on but the hooks are unwired or an env override is
lying to you). It is a face on `toggle`, not a second switch: it polls the same file agents flip,
so it stays honest when a session calls `lock_disable` behind your back.

```bash
uv sync --extra tray
.venv/Scripts/transponder-tray.exe    # headless; a second launch bows out instead of stacking icons
```

Launch it through that entry point, not `pythonw -m transponder.tray`: a uv venv's `pythonw.exe`
is a console-subsystem trampoline, so `-m` gives the tray a console window — and closing that
window kills the icon. `transponder-tray.exe` is a `gui-scripts` entry point, so the launcher
itself is windowless — but it delegates to that same venv `pythonw.exe` (uv 0.11.26 still builds
it console-subsystem), so launched bare at login it parks a black console on the screen whose
close button kills the icon anyway. The shipped launcher hides the whole chain:

```
wscript.exe //B "<checkout>\transponder\tray.vbs"
```

Put a shortcut with that target in `shell:startup` — wscript is a GUI-subsystem host, so nothing
in the chain ever owns a window a hand can close. Windows 11 files new tray icons in the hidden
overflow: promote it once by dragging it onto the taskbar.

## Environment

| variable                 | meaning                                                    |
|--------------------------|------------------------------------------------------------|
| `TRANSPONDER_DIR`        | state directory anchor (default `~/.transponder/locks`)    |
| `TRANSPONDER_FLIGHT`     | recording; on unless set to `0`/`false`/`off`              |
| `TRANSPONDER_FLIGHT_DIR` | where recordings land (default `~/.transponder/flight`)    |
| `TRANSPONDER_DISABLED`   | the off switch; also `~/.transponder/DISABLED`             |

## Scope

One machine, one filesystem — not a network protocol, and not enforcement (a process that ignores
the convention writes freely). Where a harness can give each agent its own worktree, that is better;
this covers what worktrees don't: sessions deliberately pointed at one checkout, mixed-vendor
fleets, and the stale-reader drift check.

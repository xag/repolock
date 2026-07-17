# transponder

Several AI agent sessions, one machine, shared git checkouts — without them clobbering each
other's work.

Git assumes a working tree has one author. Run two agents against the same checkout and that breaks
quietly: they overwrite each other's edits, or reason about commits a concurrent rebase already
replaced. `transponder` is an **information layer** that prevents this by visibility rather than
locking:

- **The map** — an agent declares the files or subtrees it is about to write (`declare_scope`). The
  map never double-books a region; a conflicting declaration is answered with who holds the overlap
  and what is free.
- **The courier** — harness hooks tell an agent what it cannot see from inside its own context: that
  the checkout is shared, that the file it is about to edit sits in another agent's declared region,
  or that history moved under it.
- **The witness** — a fingerprint before and after each shell or MCP call. A write that lands in
  another agent's region is reported to the agent that made it, with the recovery attached.

**Nothing is ever refused.** The agents cooperate — they work for one person, and the failure being
prevented is an agent not knowing another is there, not malice. The design and its rationale are in
[SPEC.md](SPEC.md). (Through v1 this was `repolock`, a mutex; the git history has that story.)

## Install (Claude Code)

```bash
git clone https://github.com/xag/transponder && cd transponder
uv sync
python -m transponder.toggle on        # wires the hooks at user scope; idempotent
uv run python -m transponder.server    # register this MCP server in your client
```

The hooks run on four events — `PreToolUse` and `PostToolUse` (the courier and the witness; matchers
cover both shells and `mcp__.*`), `Stop`, and `SessionStart` (the drift check). None of them ever
blocks a tool call.

## The channel (MCP tools)

| tool | what it does |
|---|---|
| `scopes(repo)` | who is working this checkout and where — call it before you plan |
| `declare_scope(repo, scope, session_id, intent)` | put your region on the map |
| `extend_scope(repo, add, session_id)` | widen when you find you need more |
| `release_scope(repo, session_id, drop?)` | come off the map when a region stops being yours |
| `lock_drift(repo, seen_head)` | has history moved under you since you last looked? |
| `lock_disable` / `lock_enable` / `lock_switch` | the off switch |

Scopes are filesystem paths — a file, a subtree (`api/**`), or `**` for the whole checkout; spelled
relative to `repo` and canonicalised, so two spellings of one file cannot be held twice. `.git/index`
is an ordinary path: reserve it around a commit so `git add -A` cannot sweep a neighbour's
unfinished work into yours.

## What an agent sees

Walking into a shared checkout:

```
THIS CHECKOUT IS SHARED — you are not alone in ~/proj/app:
  agent 7c1a is working ~/proj/app/api/**  — adding the rate limiter

Nothing is blocked. Stay out of their region, and say where you will write:
    declare_scope(repo, ['web/**'], intent='what you are doing')
```

A write that lands in another agent's region is named after the fact, with the fix:

```
SCOPE VIOLATION — you wrote inside another agent's reserved region.
  web/page.js  belongs to agent 7c1a (~/proj/app/web/**)
  ...
  You committed their work. Recover it:
      git reset --soft HEAD~1
      git restore --staged <their paths>   # then stage yours by name, never -A
```

Two agents with disjoint scopes work the same checkout at once, in silence.

## Recording

Every declaration, conflict and witnessed write is recorded (flight-recorder, extra `flight`,
`~/.transponder/flight`) so the design's central bet — that agents contain their work when they can
see each other — can be checked against real runs rather than asserted. `TRANSPONDER_FLIGHT=0` turns
it off.

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
pythonw -m transponder.tray    # headless; a second launch bows out instead of stacking icons
```

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

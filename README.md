# repolock

One developer, several AI agent sessions, one checkout.

> ### ⚠️ Experimental, and it has already bitten
>
> This is a young protocol with a young implementation, and it sits on the write path of every
> session on your machine. That is an unforgiving place for a bug.
>
> It has broken a working fleet once, in exactly the way you would least want: a change to the
> write-detection gate made *reading* commands take the write lease
> ([#4](https://github.com/xag/repolock/issues/4)), so sessions doing nothing but reading locked
> out sessions that wanted to work. The blocked sessions could not inspect the repo, could not
> wait out the lease, and could not file the bug — every escape route was itself a shell command,
> and the shell was what was blocked. The gate is fixed and the four commands that caused it are
> regression tests, but the shape of that failure is intrinsic to what this tool is: **when
> repolock is wrong, it is wrong in the direction of stopping your work.**
>
> Known-open, and worth reading before you install: a mutating command nobody listed still writes
> unguarded ([#2](https://github.com/xag/repolock/issues/2)); writes through MCP tools are not
> gated at all ([#3](https://github.com/xag/repolock/issues/3)); and a blocked session has no
> good way to wait ([#5](https://github.com/xag/repolock/issues/5)).
>
> If it does get in your way, the off switch is one edit — delete the `hooks` block from
> `~/.claude/settings.json` (or `~/.cursor/hooks.json`) and everything returns to normal
> immediately. Nothing is left behind in your repos; the lock records live outside them.

Git assumes the working tree has one author. Run two agent sessions against the same clone and
that assumption silently fails: sessions overwrite each other's edits, and — subtler — a session
goes on *reasoning* about commits that a concurrent rebase has already destroyed. Nothing is
corrupted, so nothing complains.

repolock is **a protocol plus a reference implementation, not a service**:

- **[SPEC.md](SPEC.md)** — a lockfile convention (think `.git/index.lock`, or EditorConfig):
  where the lockfile lives, its JSON schema, and its semantics — short leases renewed by
  activity, commit-anchored handoff, release refused on a dirty tree, idle-clean releases,
  idle-dirty lapses, and the read-side drift check.
- **`repolock/`** — the reference library. Pure stdlib, zero dependencies:
  `acquire` / `renew` / `release` / `go_idle` / `status` / `drift`.
- **`repolock/hooks/`** — the adapters that make the lock *binding*: `claude_code.py`
  (PreToolUse/Stop/SessionStart) and `cursor.py` (preToolUse/beforeShellExecution/stop).
  Enforcement cannot be an MCP tool — a tool is something the model chooses to call, and the
  offending session never chooses to. A lock taken through one vendor's hook holds out a
  session arriving through the other's; the test suite executes exactly that.
- **`repolock/server.py`** *(extra `mcp`)* — a read-mostly stdio MCP server for visibility and
  the deliberate human override: `lock_status`, `lock_drift`, `lock_debug`, `force_unlock`.

The point is cross-tool: every agent on the machine — Claude Code, Cursor, Codex CLI, whatever
comes next — honoring the same lockfile. A mixed fleet is exactly the scenario the lock guards.

## Install (Claude Code)

```bash
git clone https://github.com/xag/repolock && cd repolock
uv sync            # or: pip install .
```

Wire the hook at **user scope** (`~/.claude/settings.json`), so every repo on the machine is
guarded — use an absolute path to a python that can import `repolock`:

```json
{
  "hooks": {
    "PreToolUse": [{
      "matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
      "hooks": [{"type": "command", "timeout": 20,
                 "command": "\"<python>\" \"<checkout>/repolock/hooks/claude_code.py\""}]
    }],
    "Stop":         [{"hooks": [{"type": "command", "timeout": 20, "command": "<same>"}]}],
    "SessionStart": [{"hooks": [{"type": "command", "timeout": 20, "command": "<same>"}]}]
  }
}
```

Both shells belong in the matcher. The matcher is a list of *names*, and a name that is missing is
a tool the hook never sees: on Windows `PowerShell` is the shell that actually runs, so a matcher
without it leaves every write on that platform unguarded, silently and for as long as nobody looks.

Optionally register the MCP server for visibility (`uv run python -m repolock.server`).

## Install (Cursor)

Same lockfile, Cursor's wire format — `~/.cursor/hooks.json`:

```json
{
  "version": 1,
  "hooks": {
    "preToolUse":           [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
    "beforeShellExecution": [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
    "stop":                 [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
    "sessionEnd":           [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
    "sessionStart":         [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}],
    "beforeSubmitPrompt":   [{"command": "<python> <checkout>/repolock/hooks/cursor.py"}]
  }
}
```

Run both harnesses against the same clone and each one's sessions are held out of the other's
mid-change tree — which is the point.

## What it feels like

A session that tries to write into a checkout another live session is mid-change on gets the
tool call blocked, with the holder, its intent, and when the lease frees. A session that stops
to ask its human something releases on a clean tree, or holds until its lease lapses on a dirty
one — and the next writer inherits a **handoff**: what landed while the previous holder was
away, what it left uncommitted, and whether history was rewritten under everyone's feet.

## Recording (on by default)

A lock bug is a heisenbug — a clock, a PID, and an interleaving you cannot re-stage. With the
`flight` extra, every lock operation is recorded at its nondeterminism boundary and replayed
deterministically, and `repolock/invariants.py` holds the claims (two live sessions never hold
the same copy; a dirty tree is never handed over; …) that condemn a bad trajectory on any tape.

**Recording is on by default, and that default was bought the hard way.** It used to be opt-in
behind `REPOLOCK_FLIGHT`, to spare the hook's hot path a heavyweight import. Then the write gate
starved a two-session fleet ([#4](https://github.com/xag/repolock/issues/4)) and there was no
tape: the incident had to be reconstructed from the harness's own transcripts, which happened to
exist and were never designed to answer the question. An opt-in recorder is off exactly when you
need it, because nobody knows in advance which hour is the interesting one.

Recordings land in `~/.repolock/flight` — absolute, and outside every repo, for the same reason
the lockfile is: the hook runs with cwd set to the session's checkout, so a relative default
would drop a recording directory into every repo on the machine. Set `REPOLOCK_FLIGHT=0` to turn
recording off and buy back the zero-import path.

## Honest niche

Claude Code ships worktree isolation for background sessions — the vendor's answer to the same
problem, by not sharing the checkout at all. Where worktrees fit, use them. repolock's durable
niche is what they don't reach:

- **interactive sessions deliberately pointed at the same checkout** (one clone, one venv, one
  dev server);
- **cross-tool fleets** — no vendor's isolation covers another vendor's agent;
- **the stale-reader drift check** — a worktree doesn't stop another session rewriting the
  branch you're reasoning about.

Success looks like planned obsolescence: harnesses absorb the convention, and this repo remains
as the reference.

## Environment

| variable              | meaning                                              |
|-----------------------|------------------------------------------------------|
| `REPOLOCK_DIR`        | lockfile directory (default `~/.repolock/locks`)      |
| `REPOLOCK_FLIGHT`     | recording; **on** unless set to `0`/`false`/`off`     |
| `REPOLOCK_FLIGHT_DIR` | where recordings land (default `~/.repolock/flight`)  |

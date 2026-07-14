# repolock

One developer, several AI agent sessions, one checkout.

> ### ⚠️ Experimental, and it has already bitten
>
> This is a young protocol with a young implementation, and it sits on the write path of every
> session on your machine. That is an unforgiving place for a bug.
>
> It has broken a working fleet twice, both times through the same door. v0.1 decided whether a
> shell command was a write **by reading it** — word lists of mutating commands, a redirect regex.
> That gate locked *readers* out of their own repos ([#4](https://github.com/xag/repolock/issues/4)),
> and then, after being fixed, did it again: a `>` inside a string
> ([#7](https://github.com/xag/repolock/issues/7)) made `print("a -> b")` a redirect into a file,
> so a session that was only reading took the lock — and could be refused one — on a repo it wasn't
> even touching.
>
> **v1 deletes the classifier.** It cannot be made correct: `npm install` and `./deploy.sh` write and
> name nothing a list can hold, while `git log --format='%h -> %s'` is a read under a POSIX shell and
> a *write* under `cmd.exe`. Instead repolock now **looks at the repo** — a fingerprint before a tool
> runs and after — and a write is a fingerprint that moved. That is an observation, not a prediction,
> and it is right about `make`, `./deploy.sh` and every other command nobody listed.
>
> A shell still takes the lock *before* it runs — not because anyone thinks it writes, but because
> acquiring is how you find out safely. If the fingerprint proves it only read, the lock is handed
> straight back in the same breath. The price is stated rather than hidden: two sessions cannot run
> shell commands against one checkout at the same instant. That is not a defect in a mutex, it is a
> mutex — and `Read`/`Grep`/`Glob` are never gated, so a refused session can always still inspect
> the repo and see why. See SPEC §7b.
>
> A refused session is no longer left at a locked door with no sign on it. It is told what the
> holder is doing, what it has touched, and what is still open — and it can **subscribe**: the gate
> hands it a background waiter that dies when the lock frees, so its harness wakes it and it can go
> and do other work in the meantime ([#5](https://github.com/xag/repolock/issues/5)). Waiting used
> to be impossible: `sleep` is a shell command, and the shell is exactly what was refused
> ([#4](https://github.com/xag/repolock/issues/4)).
>
> Still open: writes through MCP tools are not seen at all
> ([#3](https://github.com/xag/repolock/issues/3)).
>
> **The off switch, when it gets in your way.** When this thing misfires it takes away your *shell*
> — that is what a refusal is — so the off switch is deliberately not a shell command:
>
> - **from an agent session** (including one that is currently wedged): call the MCP tool
>   **`lock_disable(reason)`**. The hook does not gate MCP tools, so it always gets through.
>   `lock_enable()` puts it back; `lock_switch()` says whether it is on, wired, and what it holds.
> - **from a terminal**: `python -m repolock.toggle off` (`on`, `status`).
>
> Either way it writes `~/.repolock/DISABLED`, which every hook checks on every call — so sessions
> **already running** are freed on their next tool use. That matters because a harness snapshots its
> hooks at startup: editing `settings.json` cannot reach the sessions that are actually stuck.
> Nothing is left behind in your repos; the lock records live outside them.

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
- **`repolock/server.py`** *(extra `mcp`)* — a read-mostly stdio MCP server for visibility, the
  deliberate human override, and the off switch: `lock_status`, `lock_wait`, `lock_drift`,
  `lock_debug`, `force_unlock`, and `lock_disable` / `lock_enable` / `lock_switch`. MCP is where
  these belong precisely because the hook does not gate MCP tools — so they still work from inside
  a session the lock has refused, which is the only moment anyone reaches for them.

The point is cross-tool: every agent on the machine — Claude Code, Cursor, Codex CLI, whatever
comes next — honoring the same lockfile. A mixed fleet is exactly the scenario the lock guards.

## Install (Claude Code)

```bash
git clone https://github.com/xag/repolock && cd repolock
uv sync            # or: pip install .
```

Then wire the hooks at **user scope** (`~/.claude/settings.json`), so every repo on the machine is
guarded:

```bash
python -m repolock.toggle on        # writes the hooks block; idempotent; leaves your other hooks alone
python -m repolock.toggle status    # is it on? is it wired? what is it holding?
python -m repolock.toggle off       # ...and off again, instantly, everywhere
```

This used to be a JSON stanza you pasted in by hand, and that is precisely how you got the failure
mode the library then had to detect at runtime: a `PostToolUse` that never made it across, and a lock
that was therefore never handed back. **An install that can be got wrong by hand should be done by a
program.** Four events are wired, and each one is load-bearing:

| event | matcher | why |
|---|---|---|
| `PreToolUse`   | `Edit\|Write\|MultiEdit\|NotebookEdit\|Bash\|PowerShell` | acquire before a declared write; hold on speculation before a shell |
| `PostToolUse`  | `Bash\|PowerShell` | where a shell write is *discovered* — the first moment anyone honestly can |
| `Stop`         | — | the handback: commit/ignore/stash, then the lock frees itself |
| `SessionStart` | — | the read-side drift check |

`PostToolUse` is not optional. Without it, `PreToolUse` still guards the file-editing tools — which
declare what they will write — but every shell runs unobserved, *and* the speculative lock a shell
takes is never given back, so every `cat` holds the repo for a full lease. That is
[#4](https://github.com/xag/repolock/issues/4), reintroduced by a config typo.

Both shells belong in the matcher. The matcher is a list of *names*, and a name that is missing is
a tool the hook never sees: on Windows `PowerShell` is the shell that actually runs, so a matcher
without it leaves every write on that platform unguarded, silently and for as long as nobody looks.

**Register the MCP server too — it is not optional.** `uv run python -m repolock.server`.

It carries `lock_wait`, and that is how a blocked session waits. It cannot wait by itself: waiting
means running `sleep`, `sleep` is a shell command, and the shell is exactly what the lock just
refused it. The hook does not gate MCP tools, so that is the one channel that still works from
inside a refusal — it is also the only reason [#4](https://github.com/xag/repolock/issues/4) could
be reported at all, by a session that could not run a shell. Install the hooks without the server
and a refused session has nothing to do but spin.

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

A session that tries to write into a checkout another live session is mid-change on gets the tool
call blocked — and, more to the point, gets told enough to *do something about it*:

```
REPO LOCKED — another agent session is part-way through changing this working copy.
  refused : Edit rhythm.py
  holder  : session 8663de9b
  doing   : Bash: uv run pytest -q tests/test_today.py
  since   : 41s ago, still moving
  frees in: ~598s — but activity renews the lease, so it may be longer
  touched : 2 uncommitted change(s) in the tree —
             M rhythm.py
             M server.py
```

Then: what is still open (`Read`/`Grep`/`Glob` are never gated; every other repo is free), and two
ways to wait — because **a blocked session cannot wait by itself**. `sleep` is a shell command, and
the shell is exactly what was refused.

- **Subscribe**, and get on with something else. The refusal hands over a one-time command; run it
  in the background and your harness wakes you the moment the lock frees. That command is minted by
  the gate and allowed by byte-equality against the string it wrote itself — a capability, not a
  guess. Change one character and it is blocked like any other command.
- **Block**, if you have nothing else to do: the `lock_wait` MCP tool returns the instant it frees.

A session that stops to ask its human something releases on a clean tree, or holds until its lease
lapses on a dirty one — and the next writer inherits a **handoff**: what landed while the previous
holder was away, what it left uncommitted, and whether history was rewritten under everyone's feet.

A gate that stops you without saying what it is waiting for, or offering a way to wait, leaves an
agent doing what a person does at a locked door with no sign on it: rattling the handle. That is
what v0.1 did, and it is the other half of why this release exists.

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
| `REPOLOCK_DISABLED`   | the panic switch; also `~/.repolock/DISABLED` (below) |

**The panic switch.** `lock_disable("why")` from any agent session, or `python -m repolock.toggle
off` from a terminal. Either writes `~/.repolock/DISABLED`, and every hook becomes a no-op
immediately.

Two properties, and both of them are forced by *when* this gets used — which is always the worst
possible moment:

**It is a file, checked on every hook call**, not an install-time setting. A harness snapshots its
hooks when a session *starts*, so editing `settings.json` cannot reach the sessions already running
— which are precisely the ones you need to free. We learned that the expensive way: the hooks were
uninstalled, a live session kept its old snapshot, and it was still being blocked minutes later.
Uninstalling should never require restarting the work it is holding up.

**It is an MCP tool, not a shell command.** When the lock misfires it refuses your *shell*. An off
switch you have to type into a terminal is therefore unreachable at exactly the moment you need it —
which is a funny thing to discover about a panic button. The hook does not gate MCP tools, so
`lock_disable` always gets through; the CLI is a convenience for a human, never the mechanism.

And `lock_enable` re-wires the hooks as well as clearing the switch, because *on* has to mean on: a
repolock that reports itself enabled while its hooks are missing from `settings.json` guards nothing
and gets trusted anyway, which is the worst of the three states.

# The repolock convention, v1

One developer, several AI agent sessions, one checkout. Git assumes the working tree has one
author; agent harnesses pretend that is still true. This convention is the missing lock: a
lockfile any agent on the machine can honor, regardless of vendor — the way `.git/index.lock`
or EditorConfig work, as a *convention*, not a service.

This document is normative. The Python package in this repository is a reference
implementation, not the definition. **MUST/SHOULD/MAY** as in RFC 2119.

## Scope: two failures, two mechanisms

1. **Two writers colliding.** Mutual exclusion, via the lockfile (§2–§5).
2. **A stale reader.** A session that only *read* a repo keeps reasoning from a picture of it;
   a concurrent rebase silently invalidates that picture without corrupting anything. Detection,
   via the commit anchor and the drift check (§6). No lock is involved.

Enforcement is out of scope, deliberately: a lock a model must *choose* to take is a
suggestion, because the offending session never chooses to. Each harness binds the convention
in its own hook mechanism (§7).

## 1. Where the lockfile lives

- Directory: `$REPOLOCK_DIR` if set, else `~/.repolock/locks/`. Implementations MUST create it.
- The directory is deliberately **outside every repo**: a lock must work for a checkout that
  is not a git repo yet, and must never appear in `git status` as an edit of its own.
- One file per working copy. The working copy's identity is its **canonical path**: absolute,
  symlinks resolved, case-normalized per platform (`realpath` + `normcase`).
- Filename: `<basename>-<key>.json`, where `key` is the first 16 hex chars of the SHA-256 of
  the canonical path (UTF-8). The basename is only for human readability of the directory;
  the hash is the identity.

## 2. The record

A single JSON object. Writers MUST write it **atomically** (write a temp file in the same
directory, fsync, rename) — a torn read is a lock held by nobody.

| field           | type            | meaning                                             |
|-----------------|-----------------|-----------------------------------------------------|
| `repo`          | string          | canonical working-copy path (§1)                    |
| `session`       | string          | opaque id of the holder (harness session id)        |
| `pid`           | int             | holder's process id, or `0` when none exists (§4)   |
| `intent`        | string          | free text: what the holder is doing                 |
| `acquired_at`   | float, epoch s  | when the lock was first taken                       |
| `renewed_at`    | float, epoch s  | last activity (§3)                                  |
| `expires_at`    | float, epoch s  | end of the current lease                            |
| `lease_seconds` | float           | the declared lease length                           |
| `base_commit`   | string \| null  | HEAD when the lock was taken — the handoff anchor   |
| `idle_since`    | float \| null   | set when the holder went idle with a dirty tree (§5)|
| `dirty_at_idle` | list of string  | `git status --porcelain` lines at that moment       |

Readers MUST treat an unparsable record as **no lock** (garbage to be overwritten), and MUST
ignore unknown fields.

## 3. Leases, renewed by activity

- A lock is **short-lived**, taken immediately before a write. The acquirer declares its lease
  (reference default 900 s; hooks use 600 s; implementations MUST cap at 4 h).
- The lease is renewed **by activity**: a harness hook renews on every tool call. A tool call
  IS the activity — there is no daemon to supervise, and an idle session lets go on its own
  because nothing renews for it.
- Acquire is **reentrant**: the holder acquiring again is a renewal, so a session never
  deadlocks against itself.

## 4. Liveness: who still holds a lock

A lock **binds** while its lease is unexpired **AND** its holder still exists:

- lease alone would let a crashed session block others until it ran out;
- PID liveness alone would let an idle session hold the repo for hours;
- each covers the other's blind spot.

`pid <= 0` means "no PID was recorded" and MUST read as *cannot disprove liveness*: locks taken
by a hook have no usable PID (the hook process exits immediately), and treating them as dead
would get every hook-taken lock stolen on sight. A PID-less lock therefore degrades to
**lease-only** liveness — which is why leases are short.

An acquire against a **binding** lock MUST be refused (verdict `held`). An acquire against a
lapsed or dead lock MUST succeed as a **takeover, with a handoff** (§6). Lock operations MUST
return verdicts, never raise: an exception in a hook is a session that cannot edit anything.

## 5. Release, and the idle boundary

- Release MUST be refused while the working tree is dirty (verdict `dirty`), unless forced.
  "Commit fast" as a rule, not an aspiration: handing over a tree with someone's half-finished
  edits is strictly worse than making the next session wait.
- Release MUST be idempotent: releasing an unheld lock is success, so hooks, tools and
  crash-recovery can all release without coordinating.
- Only the holder releases; anyone else's release MUST be refused (`denied`) unless forced.
  Force is the human's deliberate override, never the routine path.
- When the holder goes **idle** (hands control back to its human):
  - clean tree → release; holding would be pure obstruction;
  - dirty tree → the handback itself MUST be refused, where the harness allows it (see §7c). The
    holder is told to **commit** its work, **ignore** the artifact, or **stash** the scrap; the
    lock then releases itself against the clean tree, and no idle-dirty lock is ever created.
  - dirty tree, and the holder declined when asked → record `idle_since` and `dirty_at_idle` and
    let the lease lapse on its own schedule. The takeover handoff then says exactly what was left
    behind. This is the fallback, not the rule: a gate that will not let a session hand back to
    its human is worse than any lock it could be protecting.

### 5a. Why the handback is refused rather than absorbed

The rule here used to stop at "dirty tree → hold, and let the lease lapse", on the grounds that
handing over half-finished edits is worse than making the next session wait. Both halves of that
are true and the conclusion was still wrong: it accepted the dirty handback as a given, and then
charged everybody else for it.

In production (xag/repolock#11) that meant a session parked on a checkout whose only "dirt" was one
untracked artifact directory — `?? .devdata/`, not work at all — and the next session was refused
`ls && git log`, a pure read, for ten minutes. An untracked artifact directory makes a tree dirty
*permanently*, so every session that ever stopped in that repo parked a full-lease lock on it. A
livelock generator, in the shape of a safety feature.

An implementation MUST therefore offer all three routes, not just "commit": an artifact must be
**ignored** (committing it is a bug, and stashing it may break a process that is using it), work
must be **committed**, and a scrap should be **stashed**. A gate that gives the wrong instruction
for two of the three things actually in a dirty tree is a gate that gets ignored.

## 6. The anchor, the handoff, and the drift check

- Every lock taken in a git checkout MUST record `base_commit` = HEAD at acquisition.
- A takeover (§4) MUST hand the next writer: the previous session and intent, why its claim
  ended, the base commit, current HEAD, the commits between them, whether **history was
  rewritten** (the base commit no longer exists in the object graph), and any uncommitted files.
- The **drift check** is read-side and lock-free: given the HEAD a session last saw, report
  `current`, `moved` (with the commits between), or `rewritten` (the seen commit no longer
  exists — everything the session remembers about this repo is suspect). Harnesses SHOULD run
  it at session start and on each return of control, remembering HEAD per (session, repo).

## 7. Adapter obligations

A harness adapter MUST:

1. **acquire before a write it is told about.** A file-editing tool names the file it will write.
   That is ground truth: the adapter MUST acquire-or-renew before it runs, on the repo that owns
   **that path** — never on the session's cwd, which is a different repo often enough to matter,
   and is no repo at all when the target is a scratch file;
2. **never guess about a write it is not told about.** For a shell — or any tool whose effect is not
   declared — an adapter MUST NOT decide from the command text whether it writes (§7a). It MUST
   instead observe the working copy before and after, and treat a **moved fingerprint** as the write
   (§7b);
3. block when the verdict is `held`, and say so in the terms obligation 8 sets out;
4. surface the handoff verbatim on takeover;
5. **refuse the dirty handback** when the session returns control to the human (§5, §5a), where the
   harness gives it the means to. A clean tree releases. A dirty one MUST be refused, with the three
   routes spelled out (commit / ignore / stash), so the lock is never *parked* on a mess. An adapter
   MUST ask exactly **once** — harnesses cap or override a stop-hook that blocks repeatedly, and a
   session that cannot hand back to its human is a worse failure than any lock — and then fall back
   to hold-and-lapse. Where a harness's stop event cannot refuse (it is a notification, not a gate),
   the adapter MUST fall back to hold-and-lapse and MUST say so, rather than claim a guarantee it
   cannot deliver;
6. run the drift check when a session starts or resumes;
7. cover **every shell the harness exposes**, not the one its authors use. A harness that offers both
   `bash` and `powershell` and watches only the first is unguarded on the platform where the second
   is the default;
8. **make the refusal actionable.** A block MUST tell the refused session: what of ITS work was
   refused; who holds the checkout and **what that holder is doing right now** (an intent refreshed
   on every renewal — a stale one misleads the session reading it to decide whether to wait);
   what the holder has already touched; when the lease frees, and that activity extends it; what is
   **still permitted** (reading tools, every other repo); and **how to wait**. "Session 8663de9b
   (Bash)" is an ID and a tool name, not information, and a session given only that will spin;
9. **provide a way to wait through a channel it does not gate — and a way to wait that does not mean
   waiting around.** This is not a nicety, it is a hole the lock itself opens: a refused session
   cannot wait by itself, because waiting means running `sleep`, `sleep` is a shell, and the shell
   is exactly what was refused. (This is xag/repolock#4's cruellest detail — "`sleep` was also a
   write" — arriving through the new door.) An adapter that refuses a session and then refuses to
   let it wait has not built a lock, it has built a wall. Two forms, and both are owed:

   - **block**, for a session with nothing else to do: the reference implementation exposes
     `lock_wait` as an MCP tool, because the hook does not gate MCP tools — which is also the only
     reason #4 could be reported at all, by sessions that could not run a shell;
   - **subscribe**, for a session that has other work: an agent turn cannot be interrupted and
     nothing can push into it, so the ONLY thing that can wake one is its harness noticing that a
     background task it launched has exited. The adapter MUST therefore let a blocked session launch
     a waiter that sleeps on the lock and dies when the lock frees. Since that waiter is itself a
     shell — in the very repo the session is blocked from — the gate MUST issue it as a **one-time
     ticket**: a command the gate mints, and then allows by **byte equality against the string it
     wrote itself**. That is a capability, not a classification: append one character and it is a
     different string, matches nothing, and is gated like any other command. Recognising your own
     token is not the same act as understanding someone else's command, and the distinction is what
     keeps this from being #7 again.

     The ticket MUST be **executable by the shell it is handed to**, and an implementation MUST have
     a test that runs it in a real one. This is not pedantry; it is xag/repolock#10. The first
     implementation minted a single string with the interpreter path unquoted and spelled with
     Windows backslashes; bash ate every backslash as an escape (`command not found`, exit 127) and
     the waiter had never run once in the library's history. Because the refusal tells the session to
     launch it in the *background*, dying instantly was indistinguishable from waking because the
     lock freed: the escape hatch failed by reporting success. Where a harness may pick more than one
     shell, no single string suffices (a quoted path is inert in PowerShell without `&`; `&` is a
     syntax error in bash), so the gate MUST mint one spelling per shell, label them, and accept all
     of them — each is still a string it wrote itself;
10. **fail open, loudly**: a crashing adapter must never wedge the machine — an unguarded write is
   bad, a laptop where nobody can edit anything is worse, and silent is worst;
11. offer a **kill switch** that reaches sessions already running, **and that does not need a shell.**
   Both halves follow from *when* it gets used, which is always the worst possible moment:
   - it must reach a running session. A harness snapshots its hooks when a session starts, so
     uninstalling by editing config cannot free the sessions that are stuck — which are precisely the
     ones you are trying to free. A file the adapter checks on every call can.
   - it must not be a shell command. When the lock misfires it **refuses your shell** — that is what
     a refusal *is* — so an off switch spelled as a terminal command is unreachable exactly when it is
     needed. The hook does not gate MCP tools, so that is where the switch belongs; a CLI is a
     convenience for a human, never the mechanism.

   And **"on" must mean on**: an implementation that re-enables the lock without checking that its
   hooks are still wired reports itself enabled while guarding nothing, which is worse than being
   off, because it will be relied upon.

## 7a. Write detection is not decidable, and MUST NOT be attempted

An adapter MUST NOT classify a shell command as a write or a read by reading it. **v0.1 required
exactly that, and it was wrong in both directions — not by accident, but by construction.**

*It called writes reads.* `npm install`, `make`, `uv run ruff --fix .`, `python scripts/codegen.py`,
`./deploy.sh` all mutate the working copy and name nothing a list can hold. Deciding whether an
arbitrary program writes to a tree requires running it. No list closes this; the tail is the whole
space of programs.

*It called reads writes.* `print("a -> b")` was read as a redirect into a file named `b")`, so a
session doing nothing but reading took the lock, and could be refused one (#7). A quoting-aware
parser does not save it either: `git log --format='%h -> %s'` is a read under a POSIX shell and a
**write** under `cmd.exe`, where single quotes do not quote and the `>` redirects. The same text has
opposite effects in two shells, so no parser can be correct about it without knowing which shell will
run it and how that shell quotes — at which point it is an interpreter, not a gate.

And a false positive is not free. That was the most expensive sentence this document ever contained.
It costs the *lease*, which is the only resource in the protocol: a reader that takes the lock holds
the working copy against a session that wants to change it, and a fleet where every session reads
first is a fleet that locks itself out (#4).

## 7b. What replaces it: observe the repo, do not predict the command

The right question is not *"was this a write?"* but *"did the repo change?"* — and that one is
answered exactly, by looking.

A **fingerprint** of a working copy is: its HEAD, plus its porcelain status, plus the size and mtime
of every path the porcelain names. (The stat is load-bearing: a file that is already ` M` stays ` M`
when it is edited again — the status line does not move, but the bytes do. Files git ignores are
deliberately not seen; the lock protects what git tracks.)

An adapter MUST:

- **take the lock before running a tool whose effect it was not told** — a shell — without forming
  any opinion about what the command does. Not because it is believed to write: because acquiring is
  how you find out safely. A live holder in the way means `held`, and the tool is refused;
- take the **fingerprint** at that moment, and again once the tool returns;
- *unmoved* => it read, whatever it looked like. **Release the lock immediately.** A reader holds the
  working copy for the duration of its own command and not one second longer;
- *moved* => it wrote. Keep the lock, and hold it while the session stays active — exactly as a
  declared write does. Nobody had to recognise `./deploy.sh` for this to be true.

### A backgrounded tool MUST NOT be settled by observation

A task the harness runs in the background **returns immediately**: the tool call hands back a task
id, the "after" hook fires at *launch*, and the fingerprint has not moved — because the command has
not done anything yet. An adapter that settles on that picture will release the lock and let the
process write the working copy unguarded for as long as it runs (`npm run dev`, a test watcher, a
build). There is nothing to observe, and observing anyway is worse than not looking: it produces a
confident wrong answer.

So: when the harness **declares** a task backgrounded (a field in the tool input — a fact, not a
command to be read), the adapter MUST hold the lock and MUST NOT settle it. The lease and the
session's own activity carry it, and the idle boundary (§5) decides at the end. An adapter that
cannot see the end of a thing it started must not pretend it has.

The release of an unneeded lock MUST NOT be refused by a dirty tree. The dirty-tree refusal (§5)
guards the *idle boundary*; here the fingerprint proves this session changed nothing, and holding a
checkout hostage over someone else's uncommitted work, having written nothing, is #4 with a hat on.

### What this costs, and what it does not

Two sessions cannot run shell commands against one checkout at the same instant. That is not a
defect in a mutex; it is a mutex. The cost is bounded by the length of the command they collided
with, and it is emphatically **not** #4: #4's disease was a reader minting a ten-minute *lease* and
holding the repo while it did nothing. Reading tools that are not shells (`Read`, `Grep`, `Glob`)
are not gated at all, so a refused session can always still inspect the repo and diagnose the
refusal — the escape route #4's victims did not have.

An earlier draft of v1 did NOT hold through the unknown: it detected shell writes only afterwards
and *reported* a collision it had failed to prevent. That leaves a window, reachable by
construction, in which two sessions write one checkout — and a known-reachable hole in a mutex is
not a residual risk, it is the absence of the mutex on that path. It was rejected. An adapter MUST
NOT implement it.

## Non-goals

- **Not a network lock.** One machine, one filesystem. Advisory across NFS is out of scope.
- **Not sandboxing.** A process that ignores the convention can still write; the convention is
  for cooperating harnesses, and enforcement strength comes from their hooks.
- **Not a replacement for worktree isolation.** Where a harness can give each session its own
  worktree, that is the better answer; this convention covers what worktrees don't reach —
  interactive sessions deliberately pointed at the same checkout, mixed-vendor fleets, and the
  stale-reader drift check.
- **Not a lock between a session and its own subagents, and this is deliberate.** A subagent's tool
  calls reach the hook carrying the **parent's** session id (verified against a real run, not
  assumed). Acquire is reentrant, so a parent that holds the lock *renews* when its child writes.

  It has to be this way. If a subagent had an id of its own, a parent holding the lock would refuse
  its own child **while blocking on that child's result** — not contention but a deadlock, and one
  that neither a wait nor a ticket can break, because the holder is the very thing being waited for.

  The price: a session and all its subagents are **one holder**, so the convention does not
  arbitrate between two subagents of the same session running in parallel against one checkout.
  That is the harness's problem, and the harness has the better answer for it (give each agent its
  own worktree). A lock cannot fix intra-session concurrency without deadlocking on it.

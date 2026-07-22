"""The design ledger: every non-obvious decision, live hypothesis and carried debt, as a tree a
rule can go red on.

Not a changelog. Each decision carries its rationale and the alternatives it rejected; each
hypothesis carries the observation that would kill it; each debt carries the condition under which
it is discharged, and a **gate** that stays red while it is not.

**This file used to author its own vocabulary, and that was the bug behind a bug.** It defined
`decision`, `rejected-alternative`, `hypothesis`, `falsifier` — and nothing else. So when v1's first
draft left a known hole open (a shell write detected only *after* it landed, through which two
sessions could write one checkout), there was nowhere in this vocabulary to put a known-unsound
thing. It went in as a *hypothesis*, with a "falsifier" that would fire the first time the hole cost
somebody their work.

That is not a hypothesis. A hypothesis is a belief whose outcome is genuinely unknown, killed by an
observation that costs nothing to make. A hole you have already proved reachable is a **debt**, and
a debt whose only kill-criterion is the damage it causes is not being carried — it is being hidden.
`quern.ledger` models this correctly and always did: `debt` (known-unsound, carried on purpose),
`discharge` (what clears it, and who is competent to), and `gate` (the point past which unsound
things must not travel, with `nothing-unsound-passes-a-gate` to enforce it). Re-authoring a poorer
copy of that vocabulary is what left the lie somewhere to live. So: pin the package, do not restate
it. (The hole itself is now closed in code — see `hold-the-lock-through-the-unknown`.)

Authored against `quern`, which is a private library: it is NOT a dependency of this public package.
The ledger check skips where quern is not installed, and runs on machines that have it.
"""

from __future__ import annotations

import os
from pathlib import Path

import quern.grounding  # noqa: F401 -- the natives; the package itself arrives by pin
from quern import Quern, Node, Quantity
from quern.library import consume

_ROOT = Path(__file__).resolve().parents[1]

DECISIONS = [
    Node(id="protocol-not-process", kind="decision", name="Ship a protocol, not a service",
         payload={"rationale":
                  "The enforcement half of a lock cannot be an MCP tool: a tool is something the "
                  "model chooses to call, and the offending session never chooses to. Binding "
                  "enforcement is vendor-specific (each harness's hook mechanism); what is portable "
                  "is the lockfile convention. So the deliverable is SPEC.md plus a reference "
                  "library plus per-harness adapters."},
         children=[
             Node(id="alt-mcp-only", kind="alternative", name="MCP tool only",
                  payload={"why": "advisory by construction — the session that needs stopping "
                                  "never calls it"}),
             Node(id="alt-daemon", kind="alternative", name="A lock daemon/service",
                  payload={"why": "something to install, supervise and keep alive on every "
                                  "machine; the hook renews on activity so no process needs to "
                                  "outlive the call"}),
         ]),

    Node(id="core-zero-deps", kind="decision", name="Core is pure stdlib; extras for the rest",
         payload={"rationale":
                  "A lock convention with a dependency tree is an adoption blocker. "
                  "flight-recorder and mcp are optional extras, imported only at the moment they "
                  "are used; recording off means zero non-stdlib imports, enforced by "
                  "test_the_core_imports_no_optional_dependency."},
         children=[
             Node(id="alt-hard-dep", kind="alternative", name="Hard dependency on flight-recorder",
                  payload={"why": "a personal lib on the install path of every adopter; also a "
                                  "per-write import tax in the hook's hot path"}),
         ]),

    Node(id="liveness-lease-and-pid", kind="decision",
         name="[SUPERSEDED] Liveness = unexpired lease AND live holder; pid<=0 degrades to lease-only",
         payload={"rationale":
                  "Lease alone lets a crashed session block others until it runs out; PID alone "
                  "lets an idle session hold the repo for hours. Hook-taken locks have no usable "
                  "PID (the hook process exits immediately), so pid<=0 must read as 'cannot "
                  "disprove liveness' or every hook lock would be stolen on sight."},
         children=[
             Node(id="alt-lease-only", kind="alternative", name="Lease-only",
                  payload={"why": "a crashed holder blocks for the whole lease even when the OS "
                                  "could disprove it in one call"}),
             Node(id="alt-pid-only", kind="alternative", name="PID-only",
                  payload={"why": "an idle-at-lunch session holds forever; and hook locks have no "
                                  "PID at all"}),
         ]),

    Node(id="lockdir-outside-repo", kind="decision",
         name="Lockfiles live outside every repo (~/.repolock/locks)",
         payload={"rationale":
                  "A lock must work for a checkout that is not a git repo yet, and must never "
                  "appear in git status as an edit of its own. Path identity is canonical "
                  "(realpath+normcase) and hashed into the filename."},
         children=[
             Node(id="alt-inrepo", kind="alternative",
                  name="A lockfile inside the repo (like .git/index.lock)",
                  payload={"why": "shows up as a working-tree change, needs the repo to exist, "
                                  "and dies with the checkout it guards"}),
         ]),

    Node(id="client-consumes-via-config", kind="decision",
         name="Clients consume the lock via machine config, not imports",
         payload={"rationale":
                  "The extraction's client kept no lock imports — its 'dependency' is the hooks "
                  "block and MCP registration on the machine. The lib knows nothing of any client, "
                  "per the lib/client rule."},
         children=[
             Node(id="alt-lib-import", kind="alternative",
                  name="Make the old host import the lib anyway",
                  payload={"why": "an unused import kept only to satisfy a sentence in an issue; "
                                  "the honest wiring is config"}),
         ]),

    # SUPERSEDED 2026-07-13 by observe-do-not-predict, one day after it was written. Both of its
    # alternatives were right to reject, and the decision itself was STILL wrong: every option on
    # the table, the taken one included, was a way of PREDICTING mutation from a command line, and
    # that is not a thing that can be done. Kept, not deleted — the sequence (fail closed -> fail
    # open -> stop guessing) is the finding.
    Node(id="write-detection-names-the-write", kind="decision",
         name="[SUPERSEDED] A command is a write only when we can point at the write",
         payload={"status": "superseded",
                  "superseded_by": "observe-do-not-predict",
                  "rationale":
                  "Reached by being wrong first, in production. The fail-closed design (enumerate "
                  "the READERS, treat the unknown as a write) was shipped and broke a two-session "
                  "fleet within the hour (#4): `cd repo && cat file` was a write because `cd` was "
                  "not on the reader list. So the lists were inverted — name the writers, and the "
                  "unknown is a read. That was less wrong and still wrong.",
                  "killed_by":
                  "xag/repolock#7 — `print(\"a -> b\")` was judged a redirect into a file named "
                  "`b\")`, so a session that only read took the lock and could be refused one, on "
                  "a repo it was not touching. A quoting-aware parser was written, went green, and "
                  "was then killed by the test suite's own counterexample: `git log --format='%h "
                  "-> %s'` is a READ under a POSIX shell and a WRITE under cmd.exe, where single "
                  "quotes do not quote. Same text, opposite effects — a parser would have to know "
                  "which shell will run it and how that shell quotes, which makes it an "
                  "interpreter, not a gate."},
         links={"supersedes": []},
         children=[
             Node(id="alt-readers-allowlist", kind="alternative",
                  name="Enumerate the readers; fail closed on the unknown",
                  payload={"why": "tried, shipped, reverted the same day. The cost of a miss is "
                                  "not a redundant lock, it is a starved fleet: xag/repolock#4"}),
             Node(id="alt-lock-every-shell", kind="alternative",
                  name="Lock on every shell command, reads included, for the whole lease",
                  payload={"why": "what the allowlist DEGRADED into once the misses piled up. "
                                  "Distinct from the pessimistic hold now taken (see "
                                  "hold-the-lock-through-the-unknown), which keeps the lock only "
                                  "for the duration of the call and hands it back the instant the "
                                  "fingerprint proves the command read"}),
         ]),

    Node(id="observe-do-not-predict", kind="decision",
         name="Observe the working copy; never classify a command",
         payload={"rationale":
                  "Write detection from command text is undecidable, and both directions of the "
                  "error were paid for in production. It called writes reads: `npm install`, "
                  "`make`, `uv run ruff --fix`, `python codegen.py`, `./deploy.sh` all mutate the "
                  "tree and name nothing a list can hold — deciding whether an arbitrary program "
                  "writes means running it (#2). It called reads writes: a `>` inside a string, so "
                  "a reading session took the lock and was refused one (#7, and #4 before it). So "
                  "the adapter stops having opinions about commands. Where the harness declares the "
                  "target (Edit/Write/NotebookEdit carry a file_path) the lock is taken before the "
                  "write, on the repo that owns THAT PATH — not the session's cwd, which was a "
                  "second bug (#8). Where it does not, the repo itself is the witness: a "
                  "fingerprint (HEAD + porcelain + the stat of every dirty path) before the tool "
                  "and after, and a write is a fingerprint that MOVED. An observation cannot be "
                  "wrong about what a command did."},
         children=[
             Node(id="alt-quoting-aware-parser", kind="alternative",
                  name="Keep the classifier, make it quoting-aware",
                  payload={"why": "written, and green, before it was thrown away. It fixes the #7 "
                                  "family and no other: being right requires knowing which shell "
                                  "will run the text and how that shell quotes. A gate that must "
                                  "interpret the shell is a shell"}),
             Node(id="alt-widen-the-lists", kind="alternative",
                  name="Widen WRITING_GIT / WRITING_SHELL until they cover it",
                  payload={"why": "the tail is the space of all programs. `./deploy.sh` is on no "
                                  "list that can exist"}),
             Node(id="alt-drift-only", kind="alternative",
                  name="Scrap enforcement; keep only the drift check",
                  payload={"why": "seriously considered, and it is the sound core — drift needs no "
                                  "classification and catches the founding incident. Rejected "
                                  "because it abandons mutual exclusion entirely, and two sessions "
                                  "committing over each other is the failure that started this"}),
         ]),

    Node(id="hold-the-lock-through-the-unknown", kind="decision",
         name="[SUPERSEDED] A shell takes the lock BEFORE it runs, and gives it back if it turns out to have read",
         payload={"rationale":
                  "The first draft of v1 detected shell writes only afterwards (PostToolUse), which "
                  "left a window: inside one tool call, two sessions could both write one checkout. "
                  "The write was reported as a collision rather than prevented.\n\n"
                  "That window was written into this ledger as a HYPOTHESIS — 'detecting one call "
                  "late is good enough' — with a falsifier that would fire the first time a session "
                  "committed over another's work. The user's objection ended the design: a falsifier "
                  "you already know is reachable is not a test, and not seeing it fire is luck, not "
                  "evidence. Worse, its firing condition was the damage itself. A hole you can name "
                  "is a hole you close, or a debt you gate — never a hypothesis.\n\n"
                  "So it is closed. The lock is taken before a shell runs — not because we think it "
                  "writes (we have no opinion, and forming one is the mistake above) but because "
                  "taking it is how you find out safely. PostToolUse fingerprints: moved, and the "
                  "session keeps the lock as the writer it has proved to be; unmoved, and the lock "
                  "is handed straight back. A reader holds the repo for the duration of its own "
                  "command and not one second more.\n\n"
                  "The cost is stated: two sessions cannot run shell commands in one checkout at "
                  "the same instant. That is not a bug in a mutex — it is a mutex. See "
                  "hyp-serialising-shells-does-not-starve, which is a real hypothesis: its "
                  "falsifier fires on annoyance, cheaply, on a tape, long before anything is lost."},
         children=[
             Node(id="alt-optimistic-detect-after", kind="alternative",
                  name="Detect the shell write afterwards; report the collision",
                  payload={"why": "shipped for an hour, and it is what this decision reverses. It "
                                  "leaves a window that is reachable BY CONSTRUCTION, and a "
                                  "known-reachable hole in a mutex is not a residual risk, it is "
                                  "the absence of the mutex on that path"}),
             Node(id="alt-block-dirty-holders-only", kind="alternative",
                  name="Before a shell, refuse only a live holder whose tree is dirty",
                  payload={"why": "a fact-based block, and still not exclusion: two sessions can "
                                  "both run a writing shell against a CLEAN tree and land on each "
                                  "other. It narrows the window; it does not close it"}),
         ]),

    Node(id="a-refusal-must-be-actionable", kind="decision",
         name="[SUPERSEDED] A refused session is told what is happening, what it may still do, and how to wait",
         payload={"rationale":
                  "The pessimistic hold (above) closed the write window and opened a worse hole in "
                  "the same stroke: a refused session could not WAIT. Waiting means running `sleep`; "
                  "`sleep` is a shell command; the shell is exactly what was refused. So the one "
                  "thing a blocked session most needs to do was the one thing it was blocked from "
                  "doing — which is #4's cruellest detail ('`sleep` was also a write') arriving "
                  "through the new door, and it was not noticed until the user asked what the "
                  "blocked process was supposed to DO.\n\n"
                  "And the refusal itself said `session <id> (Bash)`. An ID and a tool name. "
                  "Nothing a session could act on: not what the holder is doing, not what it has "
                  "already touched, not what is still permitted, not how long to wait. An agent "
                  "given that will rattle the handle, which is precisely what was observed.\n\n"
                  "So a refusal now carries: what of YOUR work was refused; who holds it and what "
                  "they are doing RIGHT NOW (the intent is refreshed on every renewal — a stale one "
                  "actively misleads the session reading it to decide whether to wait); the files "
                  "they have already touched; when the lease frees and that activity extends it; "
                  "what is still open (Read/Grep/Glob, every other repo); and `lock_wait`, an MCP "
                  "tool, because the hook does not gate MCP tools. That channel is load-bearing, "
                  "not a convenience: it is how #4 was reported at all, from sessions that could "
                  "not run a shell."},
         children=[
             Node(id="alt-let-them-sleep", kind="alternative",
                  name="Let the blocked session wait with `sleep`",
                  payload={"why": "it cannot. `sleep` is a shell command and the shell is what was "
                                  "refused. Special-casing it would mean reading the command to "
                                  "decide it is harmless — the exact thing that is forbidden, and "
                                  "for the exact reason (`sleep 440; rm -rf x` is one segment away)"}),
             Node(id="alt-just-a-shorter-lease", kind="alternative",
                  name="Make the lease short enough that waiting does not matter",
                  payload={"why": "a lease short enough to spin on is a lease that lapses under a "
                                  "session mid-work, which is the failure hyp-renewal-on-activity "
                                  "exists to prevent. It trades a blocked session for a corrupted "
                                  "one"}),
         ]),

    Node(id="waiting-is-a-subscription", kind="decision",
         name="[SUPERSEDED] A blocked session subscribes and gets on with something else; the gate "
              "mints the waiter as a one-time ticket",
         payload={"rationale":
                  "`lock_wait` (MCP) unblocked the session but blocked its TURN: it sits there. An "
                  "agent turn cannot be interrupted and nothing can push into it, so the only thing "
                  "in existence that can WAKE a session is its harness noticing that a background "
                  "task it launched has exited. That is the entire mechanism available, so the "
                  "waiter has to be a background process the session launches itself.\n\n"
                  "Which lands on the joke that runs through this whole library: the waiter is a "
                  "SHELL, and the shell is exactly what the refused session cannot run — in the one "
                  "repo it needs to run it in. So the gate issues the command. `ticket_for(session, "
                  "repo)` mints a deterministic string, the refusal prints it, and the hook allows "
                  "that string by BYTE EQUALITY against what it wrote itself.\n\n"
                  "That is a capability, not a classification, and the difference is the whole of "
                  "why it is allowed to exist here. Nothing reads the command to judge what it "
                  "does; the hook recognises its own token. Append one character — `&& rm -rf src` "
                  "— and it is a different string, matches nothing, and is gated like anything "
                  "else. Recognising your own token is not the same act as understanding someone "
                  "else's command, and if that line ever blurs we are back in #7."},
         children=[
             Node(id="alt-blocking-wait-only", kind="alternative",
                  name="Only offer the blocking MCP wait",
                  payload={"why": "it frees the session from spinning but not from waiting: the "
                                  "turn is spent sitting on a lock instead of doing the other half "
                                  "of the work, which is usually available"}),
             Node(id="alt-exempt-all-background", kind="alternative",
                  name="Just exempt every backgrounded shell from the gate",
                  payload={"why": "it would have made the waiter work, and opened a hole the size "
                                  "of `npm install &`. Backgrounded is not a synonym for harmless — "
                                  "see the-background-task-cannot-be-observed, which goes the other "
                                  "way for exactly the same reason"}),
         ]),

    Node(id="the-background-task-cannot-be-observed", kind="decision",
         name="[SUPERSEDED] A backgrounded task holds the lock; it is never settled by a fingerprint",
         payload={"rationale":
                  "Found while building the subscription, and it would otherwise have shipped as a "
                  "silent hole. A backgrounded tool call RETURNS IMMEDIATELY — the harness hands "
                  "back a task id, PostToolUse fires at LAUNCH, and the fingerprint has of course "
                  "not moved, because the command has not run yet. Settling on that picture "
                  "releases the lock and lets `npm run dev` write the working copy unguarded for "
                  "the next hour, while the lock cheerfully reports the repo as free.\n\n"
                  "This is the failure mode of observation, and it is worth naming precisely: "
                  "observing at the wrong MOMENT is not safer than not observing. It produces a "
                  "confident wrong answer, which is worse than an admitted unknown. The whole "
                  "design rests on the after-picture being taken after the thing happened, and for "
                  "a background task there is no such moment available to a hook.\n\n"
                  "So we do not pretend. The harness DECLARES the task backgrounded ("
                  "`run_in_background` in the tool input — a fact it gives us, not a command we "
                  "read), and on that declaration the lock is HELD and never settled. The lease and "
                  "the session's activity carry it; the idle boundary decides at the end."},
         children=[
             Node(id="alt-settle-background-anyway", kind="alternative",
                  name="Settle it like any other shell",
                  payload={"why": "the fingerprint is taken before the command has done anything, "
                                  "so it always says 'read', so the lock is always released, so "
                                  "every background writer runs unguarded. The observation is not "
                                  "wrong — it is just of the wrong moment, which is worse"}),
             Node(id="alt-wait-for-the-task-to-exit", kind="alternative",
                  name="Have the hook wait for the background task and settle then",
                  payload={"why": "that un-backgrounds it. The session launched it precisely so it "
                                  "would not have to wait"}),
         ]),

    Node(id="the-ticket-must-survive-the-shell", kind="decision",
         name="[SUPERSEDED] The waiter's ticket is minted once per shell, and a test drives a real one",
         payload={"rationale":
                  "The gate mints the one command a blocked session may run — the background waiter "
                  "that lets it go and do something else (waiting-is-a-subscription). It minted a "
                  "single string, with `sys.executable` UNQUOTED and spelled the way Windows spells "
                  "it, and bash ate every backslash as an escape:\n\n"
                  "    C:UserstransProjectsrepolock.venvScriptspython.exe: command not found  (127)\n\n"
                  "So the waiter had never run. Not 'ran badly' — never ran, not once, in the "
                  "library's entire history: `wait_until_free` appears in ZERO of the 4528 recorded "
                  "sessions on this machine. And because the refusal instructs the session to launch "
                  "it with run_in_background, exiting 127 on the spot made the harness report the "
                  "task as COMPLETED — which is the same signal it sends when the waiter exits "
                  "because the lock freed. The escape hatch did not merely fail; it failed by "
                  "reporting success, and the blocked session was told to go back to work.\n\n"
                  "No single string survives both shells a harness may choose. Bash needs the path "
                  "quoted (a space would split it) and forward-slashed (a backslash is an escape); "
                  "PowerShell will not EXECUTE a quoted path without the call operator `&`, and `&` "
                  "is a syntax error in bash. So mint both, label them by shell, and allow both. It "
                  "remains a capability and not a classification: every member of the accepted set "
                  "is a string this gate wrote itself, and byte equality is still the whole test.\n\n"
                  "The rule that follows is the general one, and it is the point: TWO tests covered "
                  "this ticket. One asserted the gate RECOGNISES the string; the other called "
                  "`waitfor.main([repo])` in-process. Neither ever handed the string to a shell — "
                  "the only boundary that could fail, and the one that did. An uninstrumented fake "
                  "is relocated guessing, and two green tests around an unexercised boundary are "
                  "worse than no tests, because they are believed."},
         children=[
             Node(id="alt-one-string-for-all-shells", kind="alternative",
                  name="Keep one ticket string and make it shell-neutral",
                  payload={"why": "there is no such string. A quoted path is inert in PowerShell "
                                  "without `&`; `&` is a syntax error in bash. Unquoted forward "
                                  "slashes work in both ONLY while no path contains a space — a "
                                  "'works on my machine' that breaks on `C:/Users/John Smith/`"}),
             Node(id="alt-ship-a-launcher-script", kind="alternative",
                  name="Write a .cmd/.sh launcher and put its path in the ticket",
                  payload={"why": "moves the quoting problem rather than solving it (the launcher's "
                                  "own path can contain a space), and adds a generated file on disk "
                                  "that must be kept in step with the interpreter it wraps"}),
         ]),

    Node(id="refuse-the-dirty-handback", kind="decision",
         name="A session may not go home holding a lock on a dirty tree — commit, ignore or stash",
         payload={"rationale":
                  "The old rule: dirty tree at the Stop boundary => KEEP the lock and let the lease "
                  "run out, because 'handing over a checkout full of half-finished edits is worse "
                  "than making the next session wait'. Both halves of that sentence are true. The "
                  "conclusion was still wrong, because it took the dirty handback as a GIVEN and "
                  "then made every other session pay for it.\n\n"
                  "What it produced (#11): a session parked on `chores` with one untracked "
                  "directory in the tree — `?? .devdata/`, an artifact directory, not work at all — "
                  "and the next session was refused `ls && git log`, a pure read, for ten minutes. "
                  "And it was not a one-off: an untracked artifact dir makes a tree dirty FOREVER, "
                  "so EVERY session that ever stopped in that repo parked a ten-minute lock on it. "
                  "A livelock generator, installed by accident, in the shape of a safety feature.\n\n"
                  "So refuse the premise. Claude Code's Stop hook can block the handback (exit 2) "
                  "and hand the reason back to the model, so the session is told to commit its work, "
                  "gitignore the artifact, or stash the scrap — and the lock then releases itself "
                  "against the clean tree. The dirty handoff never happens, so there is nothing left "
                  "to protect anybody from, and the idle-dirty lock simply ceases to exist.\n\n"
                  "Three routes, not one, because 'commit your work' is the WRONG instruction for "
                  "two of the three things actually in a dirty tree at that moment — an artifact "
                  "must be ignored (committing it is a bug, stashing it may break a running "
                  "process), and a scrap should be stashed. A gate that gives wrong instructions is "
                  "a gate that gets ignored.\n\n"
                  "We ask ONCE (`stop_hook_active`), then get out of the way and fall back to the "
                  "old hold-and-lapse. A gate that will not let a session hand back to its human is "
                  "a worse failure than any lock it could be protecting."},
         children=[
             Node(id="alt-idle-blocks-writes-observes-shells", kind="alternative",
                  name="Let an idle holder block declared writes, but observe shells",
                  payload={"why": "would have unblocked the read, and left a shell free to write "
                                  "into a parked session's half-finished tree — trading a liveness "
                                  "bug for a correctness one. It also still ACCEPTS the dirty "
                                  "handback; it only makes it cheaper for everyone else"}),
             Node(id="alt-untracked-dirt-releases", kind="alternative",
                  name="Hold at idle only for TRACKED modifications; untracked-only dirt releases",
                  payload={"why": "kills the `.devdata/` class exactly, and mis-handles the case "
                                  "next door: `?? newfeature.py` is untracked AND is real "
                                  "work-in-progress. It sorts dirt by git's bookkeeping rather than "
                                  "by what it IS, which is the same category error as reading a "
                                  "command to guess what it does"}),
             Node(id="alt-short-idle-lease", kind="alternative",
                  name="Park the lock as before, but cut the idle lease to ~60s",
                  payload={"why": "makes the wound smaller without closing it: everyone still waits, "
                                  "still for nothing, and a session's half-finished edits are still "
                                  "abandoned in a shared checkout — now with a 60s fuse on them. "
                                  "Kept as the fallback for the session that declines to clean up, "
                                  "which is the only place it is the honest answer"}),
         ]),

    Node(id="mcp-is-watched-never-gated", kind="decision",
         name="[SUPERSEDED, by generalisation] The MCP channel is watched and never gated — the escape hatches all live on it",
         payload={"rationale":
                  "xag/repolock#3: an MCP tool that writes the working copy takes no lease. The "
                  "issue's own proposed fix — add `mcp__.*` to the matcher, or fail closed on "
                  "unrecognised tools — turns out to be the one thing that must never be done, and "
                  "the code was ALREADY one line of config away from doing it: the hook's "
                  "fallthrough treats any tool that is not a declared write as a shell, so the "
                  "moment `mcp__.*` reached the matcher, MCP calls would have been sent through "
                  "`hold_unknown` and REFUSED against a live holder. Verified, not assumed: with "
                  "the matcher widened and nothing else changed, `lock_disable` against a held repo "
                  "exits 2.\n\n"
                  "Which is a trap with the safety catch filed off. Three of this library's "
                  "guarantees are MCP calls, and every one of them is needed precisely when the "
                  "lock is misbehaving: `lock_wait`, the only way a refused session can wait (the "
                  "shell is what it was refused); `lock_disable`, the off switch, which "
                  "the-off-switch-cannot-need-a-shell says in as many words must not be a terminal "
                  "command; and 'file an issue and move on', the third route the refusal itself "
                  "offers — and the route by which #4 was reported, from sessions that could run no "
                  "shell at all. A gate on MCP stands in front of all three. The tool that turns "
                  "the lock off cannot be reachable only when the lock is off.\n\n"
                  "So MCP is WATCHED instead: fingerprint before, fingerprint after, and no verdict "
                  "in between. Unmoved (a mail search; almost every call) costs nothing and takes "
                  "no lock. Moved means that tool wrote, as a fact, and the session holds the lock "
                  "from then on exactly as a shell that wrote does. Moved against a live holder is a "
                  "COLLISION, and the one thing still available is to say so loudly to the session "
                  "that caused it.\n\n"
                  "Our own lock tools are exempt from even the watching. `lock_wait` sits there ON "
                  "PURPOSE while the holder works, so the tree moves under it by design — observing "
                  "across it would take the holder's honest work, attribute it to the session that "
                  "was politely waiting, and hand that session a collision report about a file it "
                  "never touched.\n\n"
                  "The cost is real and is NOT hidden here: on this path there is no mutex, only "
                  "detection one call late. See mcp-writes-settle-late, which is a debt in front of "
                  "the gate — not a hypothesis, because nothing about it is uncertain."},
         children=[
             Node(id="alt-gate-mcp-like-a-shell", kind="alternative",
                  name="Add mcp__.* to the matcher and hold the lock through it, exactly as a shell",
                  payload={"why": "a true mutex on the MCP path, and it strands the protocol. The "
                                  "off switch, the blocking wait and 'file an issue and move on' "
                                  "are all MCP calls, so a blocked session would be refused every "
                                  "way out of a lock that is misfiring — and a lock whose off "
                                  "switch is behind its own gate is not a safer lock, it is an "
                                  "unrecoverable one. It would also refuse a session's mail while "
                                  "it waits for a repo, which is a blast radius no part of this "
                                  "protocol asked for"}),
             Node(id="alt-matcher-lists-writing-mcp-tools", kind="alternative",
                  name="Enumerate the MCP tools that can write, and gate only those",
                  payload={"why": "observe-do-not-predict, wearing a new hat. MCP tool names are "
                                  "open-ended and vendor-supplied: the list is `mcp__ide__"
                                  "executeCode` today and something nobody has written yet "
                                  "tomorrow. This is `alt-widen-the-lists` for a namespace that "
                                  "grows faster than the shell's did"}),
             Node(id="alt-document-the-boundary-only", kind="alternative",
                  name="State the boundary in SPEC.md and ship no code",
                  payload={"why": "honest, and it was the issue's own minimum. Rejected because the "
                                  "write stays INVISIBLE: a cell that writes the tree from a "
                                  "notebook kernel leaves the next session to discover it as a "
                                  "mangled rebase. Watching costs one fingerprint and turns a silent "
                                  "corruption into a named collision, which is the whole difference"}),
         ]),

    # ITS PREMISE WAS FALSE WHEN IT WAS WRITTEN, and the text below is left standing so that stays
    # visible. "The hook prints into every agent's context on every tool call" was the sentence the
    # mutex was deleted on; hook stdout went to a debug log, and no agent had ever received a note.
    # The conclusion survives — a two-session run on 2026-07-22 confirmed agents cooperate when told
    # — but it was reached on a mechanism that did not exist and was only made real that day. The
    # correction, and the rule that follows from it, are in the-courier-speaks-where-it-can-be-heard.
    Node(id="information-not-exclusion", kind="decision",
         meta={"amended": "9b985b966e26 tightened under the 600-word budget; every claim kept"},
         name="Scratch the mutex entirely: nothing is ever refused, and information is the model",
         payload={"rationale":
                  "The user's sentence, which the first trial build implemented only by half: 'we "
                  "are not blocking agents, just giving them a channel to negotiate and "
                  "collaboratively prevent breaking each other's work.' The build kept four kinds "
                  "of refusal anyway — the v1 mutex for undeclared sessions, a teaching-refusal, a "
                  "scope gate on declared writes, a v1-lock check on scoped agents — arguing "
                  "silence must stay safe for agents who never heard of the protocol. "
                  "That argument had a flaw the user exposed by hitting it: THE COURIER REACHES "
                  "NON-PARTICIPANTS TOO. The hook prints into every agent's context on every tool "
                  "call, so an undeclared agent can be TOLD another is mid-change without "
                  "being REFUSED anything. Making the cooperation bet for declared agents while "
                  "refusing it for undeclared ones was incoherent — same bet, make it everywhere. "
                  "And the race that interrupted the trial's first pilot declaration was the "
                  "kept-mutex biting, on cue.\n\n"
                  "v1 is deleted, not superseded-in-place: the lease-lock, the pessimistic hold, "
                  "the tickets and the waiter, the takeover handoff, the Cursor adapter, and every "
                  "test that pinned refusal behaviour. What remains never refuses: the claims map "
                  "(scope.py — a conflicting declare is not RECORDED, the map stays coherent; no "
                  "call is blocked), the witness (witness.py — what happened, violations "
                  "named to their author, remedy attached), the courier (hooks — the "
                  "shared-checkout intro, the pre-write heads-up), and the drift check. Deadlock "
                  "ceases by construction — nothing blocks, nothing cycles — and §5's "
                  "extend-deadlock apparatus evaporates.\n\n"
                  "ONE deliberate exception, flagged and accepted: the Stop boundary may block a "
                  "DEPARTING session's handback, once, to ask commit/ignore/stash of a dirty "
                  "tree. It refuses no other agent anything; demoted to a note it would be prose "
                  "that cannot fire.\n\n"
                  "Knowingly given up: exclusion. Two agents CAN now write one region; the "
                  "witness names it after the fact. Every failure in this library's recorded "
                  "history was an agent that did not know another agent was there — ignorance, not "
                  "malice — and information cures ignorance at a fraction of the price of a mutex "
                  "that took the machine down four times (#4, #7, #10, #11) and whose only two "
                  "genuine collisions-avoided were between sessions in DIFFERENT directories. "
                  "Deterrence is NOT the mechanism (no memory across "
                  "sessions, no reputation, no future to lose): visibility plus a witness is."},
         links={"supersedes": ["hold-the-lock-through-the-unknown", "a-refusal-must-be-actionable",
                               "waiting-is-a-subscription", "the-ticket-must-survive-the-shell",
                               "mcp-is-watched-never-gated", "liveness-lease-and-pid",
                               "the-background-task-cannot-be-observed"]},
         children=[
             Node(id="alt-keep-v1-as-degenerate-case", kind="alternative",
                  meta={"amended": "bd0a0693f6c1 tightened with its entry; claim kept"},
                  name="Keep the mutex as the default for undeclared sessions (silence is `**`)",
                  payload={"why": "the first trial build. Defensible on paper — non-participants "
                                  "keep yesterday's protection — and incoherent: the bet made "
                                  "only for agents who opted in, while the courier could inform "
                                  "the others just as well. And it kept the lease/liveness "
                                  "machine alive for a default — the machine that raced the "
                                  "trial's first pilot"}),
             Node(id="alt-gate-only-declared-writes", kind="alternative",
                  name="Hybrid: never gate shells or MCP, but still refuse an Edit into another's region",
                  payload={"why": "the write is knowable there, so prevention is free of guessing — "
                                  "but it splits the model down the middle: the same collision is "
                                  "refused through one tool and witnessed through another, agents "
                                  "learn the boundary is negotiable via the shell, and every "
                                  "refusal message must explain the asymmetry. A heads-up BEFORE "
                                  "the declared write keeps the information value of that moment "
                                  "without the cage"}),
             Node(id="alt-demote-stop-ask-to-a-note", kind="alternative",
                  name="Make even the Stop dirty-tree ask a note, for purity",
                  payload={"why": "a note at the moment a session is ALREADY LEAVING is prose that "
                                  "cannot fire — the session is gone before anyone reads it. The "
                                  "ask-once blocks nobody else, ever, and #11's lesson (three "
                                  "routes: commit / ignore / stash) survives only if it can insist "
                                  "on an answer exactly once"}),
         ]),

    Node(id="filesystem-is-the-namespace", kind="decision",
         name="A scope resource is a canonical filesystem path — one namespace, one overlap relation",
         payload={"rationale":
                  "SPEC-v2's first draft let a scope hold resources from several namespaces: paths, "
                  "plus opaque names (`git:index`, `port:3000`) overlapping by string equality. The "
                  "user's question killed it: if scope is free-form, whom do we inform, without "
                  "broadcasting? Routing was never the problem — the claim store is the routing "
                  "table and overlap computes the addressees — but ALIASING was: two spellings of "
                  "one real resource (`port:3000` vs `dev-server`) read as disjoint, both granted, "
                  "and the collision lands in the world with the system reporting calm. The failure "
                  "mode of free-form is not noise, it is silence.\n\n"
                  "So the namespace is the local filesystem, whole: a resource is a canonical "
                  "absolute path (realpath+normcase — the same canonicalisation the v1 lockfile has "
                  "always used), one file or a subtree. Aliasing dies by construction: case, "
                  "symlinks, junctions, `..` all resolve to one string. Overlap is the prefix "
                  "relation, so a conflict names the exact INTERSECTION and 'come back narrower' is "
                  "computed rather than guessed.\n\n"
                  "And the special resources were never special: `git:index` IS `<repo>/.git/index`, "
                  "`git:HEAD` IS `<repo>/.git/HEAD`. The founding incident's cure — reserve the "
                  "index before you commit — needs no second kind of resource, no second overlap "
                  "relation to get wrong, and the witness already sees the sweep through the "
                  "porcelain."},
         children=[
             Node(id="alt-per-namespace-overlap", kind="alternative",
                  name="Several namespaces, each with its own overlap relation",
                  payload={"why": "the first draft. Routing works, but every opaque namespace is a "
                                  "contract without a witness AND an aliasing surface: equality "
                                  "cannot see that two names mean one resource. Each added "
                                  "namespace is a second overlap relation to get subtly wrong, in "
                                  "the one function whose failure is silent"}),
             Node(id="alt-freeform-with-registry", kind="alternative",
                  name="Free-form names, converging by visibility (scopes() as discovery)",
                  payload={"why": "convention-over-ontology does converge for a handful of agents, "
                                  "but 'converges' is a behavioural bet stacked on top of the "
                                  "behavioural bet the trial already carries. The filesystem gives "
                                  "the same expressiveness for everything the trial actually "
                                  "protects, with zero bets"}),
             Node(id="alt-ports-and-services", kind="alternative",
                  name="Keep port:/service: for the things a filesystem cannot name",
                  payload={"why": "a port is a name no fingerprint can witness — a violation "
                                  "surfaces as two dev servers fighting, never as a line on the "
                                  "tape. Dropped rather than carried: a contract nobody can check "
                                  "is not a contract, and it can earn its way in later WITH a "
                                  "witness"}),
         ]),

    Node(id="the-off-switch-cannot-need-a-shell", kind="decision",
         name="The off switch is an MCP tool and a file — never a command in a terminal",
         payload={"rationale":
                  "This lock sits on the write path of every agent session on the machine, and it "
                  "has now taken the machine down four times (#4, #7, #10, #11). The switch that turns "
                  "it off is therefore load-bearing, and it has exactly two hard requirements — both "
                  "of which are consequences of WHEN it gets used, which is always the worst "
                  "possible moment.\n\n"
                  "It must not need a shell. When the lock misfires it REFUSES YOUR SHELL — that is "
                  "what a refusal is — so an off switch spelled as a terminal command is unreachable "
                  "at exactly the moment it is needed. The hook does not gate MCP tools, so the real "
                  "surface is `lock_disable`/`lock_enable`; the CLI is a convenience for a human, "
                  "not the mechanism.\n\n"
                  "It must reach sessions that are ALREADY RUNNING. A harness snapshots its hooks at "
                  "session start, so removing them from settings.json does nothing for the sessions "
                  "currently wedged — which are precisely the ones you are trying to free. Only a "
                  "file, read on every hook call, gets through (`~/.repolock/DISABLED`). That is why "
                  "the switch is a file and not a config edit, and why it is checked on every call "
                  "rather than at install.\n\n"
                  "`lock_enable` also RE-WIRES the hooks, because 'on' has to mean on: a repolock "
                  "that reports itself enabled while its hooks are missing from settings.json guards "
                  "nothing and is trusted anyway, which is the worst of the three states."},
         children=[
             Node(id="alt-uninstall-the-hooks", kind="alternative",
                  name="Turn it off by removing the hooks from settings.json",
                  payload={"why": "cannot reach a running session — it snapshotted its hooks at "
                                  "startup — so it frees everyone EXCEPT the sessions that are "
                                  "actually stuck. Kept as a belt-and-braces flag (`off --unwire`), "
                                  "never as the mechanism"}),
             Node(id="alt-env-var-only", kind="alternative",
                  name="REPOLOCK_DISABLED as the only switch",
                  payload={"why": "a session cannot be handed an env var it was not launched with. "
                                  "Kept as an override (it beats the file in BOTH directions, so a "
                                  "test can re-enable the lock without deleting the machine's panic "
                                  "file), and `toggle status` reports it precisely because it wins"}),
         ]),

    Node(id="tray-is-a-face-not-an-authority", kind="decision",
         name="The tray icon is a third face on the one switch, never a second switch",
         payload={"rationale":
                  "A human at the desktop should be able to see whether the transponder is on and "
                  "flip it without opening a terminal — the-off-switch-cannot-need-a-shell, extended "
                  "to a human whose hands are on a mouse. So `transponder.tray` (extra `tray`: "
                  "pystray + Pillow, keeping core-zero-deps) draws an icon in the notification "
                  "area.\n\n"
                  "The decision is what the tray is NOT: it holds no state and knows no second "
                  "path. It reads `toggle.state()` and flips through `toggle.enable`/`disable`, so "
                  "a click and an agent's `lock_disable` are indistinguishable to the rest of the "
                  "system. And it POLLS rather than remembering what it last did, because the "
                  "switch it displays is shared — an icon showing ON over a machine an agent just "
                  "switched off would be the map lying, which is this project's cardinal sin. The "
                  "third state (amber: claims ON while unwired or env-overridden) is rendered, not "
                  "rounded to green, for the same reason toggle.render() warns about it."},
         children=[
             Node(id="alt-tray-own-flag", kind="alternative",
                  name="A tray app with its own on/off flag or cached state",
                  payload={"why": "two authorities over one switch drift, and the icon becomes a "
                                  "confident liar the first time an agent flips the file under it"}),
             Node(id="alt-shell-shortcut", kind="alternative",
                  name="A pinned pair of .lnk shortcuts running `toggle on`/`toggle off`",
                  payload={"why": "write-only — shows nothing. The value of the icon is the STATE "
                                  "being visible at a glance, ON/OFF/lying, before any click"}),
         ]),

    Node(id="never-hand-a-child-our-stdin", kind="decision",
         name="A subprocess never inherits this process's stdin — inside the MCP server, stdin is "
              "the client's pipe",
         payload={"rationale":
                  "Found by using the thing as intended: two agents, one checkout, one file. Neither "
                  "agent ever reached the file. Both spent thirty minutes failing to complete their "
                  "FIRST `scopes()` call and were killed still trying — obeying the protocol to the "
                  "end, which is the only reason this was visible as a channel failure rather than "
                  "as two agents clobbering each other.\n\n"
                  "`env._run` ran git with `capture_output=True` and INHERITED STDIN. Inside the MCP "
                  "server this process's stdin IS the client's stdio pipe; a child that inherits it "
                  "does not exit until that pipe does, so `communicate()` blocks joining its reader "
                  "threads until the 30s timeout. `_fmt_scopes` makes two git calls, so `scopes()` "
                  "cost 60.06s — measured against the real server driven over raw stdio, not "
                  "inferred. And the blocking call runs ON THE EVENT-LOOP THREAD, so FastMCP "
                  "serialises every request and the server is deaf for the whole 60s: with three "
                  "actors calling it, one request waited 1800s and was aborted without ever being "
                  "served. A 60s bug became a dead channel.\n\n"
                  "The latency is not the finding. THE TIMEOUT RETURNS None, and `_run`'s contract "
                  "cannot tell 'git failed' from 'git never answered' — so `git_head` reported `?` "
                  "and `git_dirty` reported a CLEAN TREE. The map told every reader the checkout was "
                  "clean while it was dirty, `release_scope` saw nothing to object to, and the drift "
                  "check lost its baseline. A witness that fails OPEN and SILENT is the one failure "
                  "this library cannot have, and it had it on every MCP call for as long as the "
                  "server has existed.\n\n"
                  "`stdin=subprocess.DEVNULL`: 60.06s -> 0.08s, and the server now answers `tree "
                  "DIRTY, 1 change(s), head 2be1c29055fc` where it had answered `tree clean, head "
                  "?`. The hooks were never affected — they run git in short-lived processes with no "
                  "stdio transport — which is exactly why the courier and the witness worked "
                  "throughout while the negotiation channel was dead, and why nothing in the test "
                  "suite noticed.\n\n"
                  "RESIDUAL, stated rather than fixed: `_run` still collapses timeout into None, so "
                  "any future git call slow enough to time out still reads as a clean tree. The "
                  "stdin bug made that reachable on every call; closing it makes it rare, not "
                  "impossible."},
         children=[
             Node(id="alt-raise-the-git-timeout", kind="alternative",
                  name="Raise (or lower) the 30s timeout",
                  payload={"why": "reads the symptom as slowness. Longer makes each hang longer; "
                                  "shorter makes the silently-wrong clean-tree answer arrive sooner. "
                                  "Neither touches the reason the child never exits, and the "
                                  "dangerous half of the bug is the None, not the wait"}),
             Node(id="alt-run-git-off-the-event-loop", kind="alternative",
                  name="Run the git calls in a worker thread so the server stays responsive",
                  payload={"why": "true, and insufficient. It cures the DEAFNESS — other calls would "
                                  "be served during the stall — and leaves every git answer wrong by "
                                  "timeout, which is the part that corrupts the map. Worth doing on "
                                  "its own merits; it is not this bug"}),
             Node(id="alt-cache-the-tree-state", kind="alternative",
                  name="Cache HEAD and the porcelain instead of shelling out per call",
                  payload={"why": "trades a wrong answer for a stale one, on the single question "
                                  "`scopes()` exists to answer truthfully. A map that is confidently "
                                  "out of date is the same cardinal sin as a map that is confidently "
                                  "wrong — see tray-is-a-face-not-an-authority"}),
         ]),

    Node(id="the-courier-speaks-where-it-can-be-heard", kind="decision",
         name="Notes go out as hookSpecificOutput.additionalContext — and a channel is verified at "
              "the RECEIVING end or not at all",
         payload={"rationale":
                  "`_say()` was `print(msg)` for the whole life of v2. Plain stdout from "
                  "PreToolUse/PostToolUse/Stop goes to Claude Code's DEBUG LOG — not the model, not "
                  "the transcript. So no agent on this machine had ever received the "
                  "shared-checkout intro, a heads-up, or a violation report. THE COURIER HAD NEVER "
                  "SPOKEN TO ANYONE.\n\n"
                  "Nothing internal could tell. The map was accurate, the witness was watching, the "
                  "tapes were faithful, every test was green — and the tape recorded each note "
                  "being PRODUCED, which is not the same fact as delivered. It took two sessions "
                  "deliberately colliding to notice, because the failure is invisible from inside "
                  "one session, which is the exact blindness this project exists to cure, turned on "
                  "itself.\n\n"
                  "The fix is small: emit one JSON document carrying "
                  "`hookSpecificOutput.additionalContext`, which reaches the model without refusing "
                  "the call. Buffered, because two JSON objects on stdout do not parse as one and "
                  "several notes can fire on a call.\n\n"
                  "The rule worth keeping is larger, and it generalises every failure found the "
                  "same day: A CHANNEL IS VERIFIED AT THE RECEIVING END. Producing a message, "
                  "recording it, and asserting on it in a test all pass while nobody is listening. "
                  "This is the flight-recorder corollary about uninstrumented fakes, one level up — "
                  "the tape was real and it was recording the wrong side of the boundary.\n\n"
                  "It also corrects the ground under information-not-exclusion, which deleted the "
                  "v1 mutex on the sentence 'the hook prints into every agent's context on every "
                  "tool call'. That was false when it was written. The conclusion survives — today's "
                  "two-session run confirmed cooperation works — but it was reached on a mechanism "
                  "that did not exist, and the entry stands unedited so that stays visible."},
         children=[
             Node(id="alt-keep-printing-to-stdout", kind="alternative",
                  name="Leave `print()` alone; the notes are for the human in the transcript",
                  payload={"why": "they are not in the transcript either — the debug log is the "
                                  "whole audience. And the notes are addressed to an agent: 'stay "
                                  "out of their region' is not advice a human can act on"}),
             Node(id="alt-exit-2-to-reach-the-model", kind="alternative",
                  name="Use exit 2, whose stderr does reach the model",
                  payload={"why": "exit 2 BLOCKS the call. It is the one thing this library will "
                                  "not do, and reaching the agent by refusing it is the mutex "
                                  "returning through the informer's door"}),
             Node(id="alt-permission-decision-reason", kind="alternative",
                  name="Allow the call with a `permissionDecisionReason`",
                  payload={"why": "checked rather than assumed: the reason goes to the transcript "
                                  "and the permission UI, not into the model's context. It would "
                                  "have looked like a fix and changed nothing — the same shape as "
                                  "the bug"}),
         ]),

    Node(id="there-is-no-warning-before-a-write", kind="decision",
         name="heads_up() is deleted: a hook reaches an agent before its tool only by refusing it. "
              "What arrives in time is PULLED, not pushed",
         payload={"rationale":
                  "Context attached to PreToolUse is delivered beside the TOOL RESULT — after the "
                  "write. The only thing that reaches a model before its tool runs is exit 2, which "
                  "refuses the call. So the pre-write warning was a warning delivered after the "
                  "thing it warned about, still worded 'if you write it anyway', addressed to an "
                  "agent that already had.\n\n"
                  "Deleted rather than reworded. Rewording leaves two paths saying the same thing "
                  "at the same moment in different words, and a docstring promising a guarantee the "
                  "harness cannot give. Edit/Write now settle exactly like a shell — snapshot "
                  "before, reported after, remedy attached — which is what observe-do-not-predict "
                  "said all along.\n\n"
                  "It closed a real hole in the same stroke. post_tool_use used to return early on "
                  "a declared write, on the assumption the warning had done the job. It had not, "
                  "and could not: so an Edit into another agent's region got an undeliverable "
                  "warning and NO after-the-fact report — the one write in the system the witness "
                  "never saw, on either side.\n\n"
                  "WHAT DOES ARRIVE IN TIME IS PULLED. The two-session run settled this: the other "
                  "agent called scopes(), saw the holder, and DECLINED TO WRITE — unprompted. It "
                  "wrote only when its human overrode it. The pre-write moment is not reachable by "
                  "pushing information at an agent; it is reachable by the agent asking, and the "
                  "MCP reply is synchronous. The courier's job is to make sure it knows to ask, and "
                  "to tell it afterwards when it did not.\n\n"
                  "The one push that does arrive before an agent acts is UserPromptSubmit, whose "
                  "output the harness puts in front of the model. HANDLERS had routed it since v2, "
                  "commented 'the same read-side check, on the way back in' — and EVENTS never "
                  "wired it, so it never ran once. The drift check on the way back in never "
                  "happened either. Wired now, and the test asserts the WIRING: the handler was "
                  "always fine, and a test of the handler passes either way."},
         children=[
             Node(id="alt-reword-the-heads-up", kind="alternative",
                  name="Keep heads_up() and reword it as an after-the-fact report",
                  payload={"why": "then it IS settle(), said twice, in two code paths, at the same "
                                  "moment, in different words. Two things that fire together and "
                                  "disagree in wording are how a reader learns to trust neither"}),
             Node(id="alt-block-just-this-one-case", kind="alternative",
                  name="Use exit 2 for a write into another agent's declared region — the one case "
                       "where blocking is obviously right",
                  payload={"why": "it is a mutex on the path where prevention looks cheapest, which "
                                  "is exactly how v1 began. information-not-exclusion gave up "
                                  "exclusion knowingly; taking it back for the case that feels "
                                  "deserving is taking it back"}),
         ]),

    Node(id="tell-both-parties", kind="decision",
         name="The agent whose region was written is told too — and the offender is not asked to "
              "repair what it never saw",
         payload={"rationale":
                  "`victim` existed in exactly two places: computed in scope.violations, and "
                  "rendered into the OFFENDER's message. The agent whose half-finished work had "
                  "just been overwritten was addressed by nothing.\n\n"
                  "So the remedy asked the wrong party. 'Put back what was theirs' asks an agent to "
                  "reconstruct bytes it never saw — the work may never have been committed, so "
                  "nothing readable says what was there. That is a guess, in a library whose whole "
                  "design is the refusal to guess (observe-do-not-predict). And obeying it means "
                  "writing into a region that is still not yours: a real session followed the "
                  "remedy, reverted, and collected a SECOND violation for complying. An alarm that "
                  "fires when you do as you are told is an alarm that gets ignored.\n\n"
                  "Both parties are now addressed, and told DIFFERENT things. The offender: stop, "
                  "leave it, they have been told. The victim, on its next tool call or its human's "
                  "next prompt: someone wrote here, LOOK before you carry on — your picture of "
                  "these files predates their write, so an edit against what you remember "
                  "overwrites it again, this time by you. Keep, merge or restore is a judgement "
                  "only the victim can make. The single thing the offender is still told to undo is "
                  "a commit that swept their work: that one is recoverable and genuinely its own.\n\n"
                  "KNOWN LIMIT, accepted rather than hidden: the victim is not woken. Nothing can "
                  "push into a running turn (waiting-is-a-subscription), so the note waits for its "
                  "next hook. The window is bounded and cheap — a working victim fires a hook within "
                  "seconds, and a parked one is reached at UserPromptSubmit BEFORE it acts. The one "
                  "uncovered case is a victim whose very next call writes that same file, and that "
                  "write goes into its own region: the one write nobody needs protecting from.\n\n"
                  "The inbox also exposed an older bug it did not cause: _memo_path hashed whatever "
                  "repo spelling it was handed, and the callers disagree (repo_root gives git's "
                  "C:/..., repo_of a backslashed abspath). One checkout, several keys — so 'the "
                  "intro, once' was only as good as the key it was remembered under. Keyed on "
                  "env.canonical() now, which is filesystem-is-the-namespace applying to a second "
                  "store nobody had noticed was a namespace."},
         children=[
             Node(id="alt-offender-restores-it", kind="alternative",
                  name="Keep asking the offender to put the work back",
                  payload={"why": "it cannot. Uncommitted work leaves no readable trace of what it "
                                  "was, so restoring is guessing, and the attempt is a second write "
                                  "into someone else's region — observed firing the alarm at an "
                                  "agent that was obeying the alarm"}),
             Node(id="alt-mirror-one-message-to-both", kind="alternative",
                  name="Send both parties the same violation report",
                  payload={"why": "cheaper, and wrong in the only way that matters: the two need "
                                  "OPPOSITE instructions. The offender must stop touching it; the "
                                  "victim must go and look at it. One text cannot say both"}),
             Node(id="alt-wake-the-victim", kind="alternative",
                  name="Wake the victim with a background waiter, as v1 did for blocked sessions",
                  payload={"why": "v1 built exactly this and it was the worst thing in the repo — "
                                  "the two-shell ticket, the quoting bug, and `wait_until_free` in "
                                  "ZERO of 4528 recorded sessions. Reintroducing it to save the few "
                                  "seconds measured here is the mutex's ghost walking back in"}),
         ]),

    Node(id="a-channel-they-can-repurpose", kind="decision",
         name="An addressed message substrate — direct pushes, broadcast and channel are pull-only; "
              "a transponder, not a chatter phone",
         payload={"rationale":
                  "The map answers WHERE, plus one line of why. It cannot carry what an agent is "
                  "actually doing, and that is the information the other agent needs most — two "
                  "sessions in one checkout are not rivals, they are building one app. Knowing that "
                  "a rewrite of the auth middleware is COMING changes how you write the module "
                  "beside it: you make it future-proof rather than merely correct. Nothing in this "
                  "library could say that. A conflict ended with 'file an issue for their part', "
                  "which is what you write when there is no channel — it routes a live negotiation "
                  "through a human and a day.\n\n"
                  "The user's framing decided the shape: an item left in the cabin, the way ground "
                  "control solves a problem with what is aboard. A general channel can be "
                  "repurposed by intelligent agents for the case nobody anticipated; a "
                  "special-purpose protocol only ever solves the case we imagined. So: free-form "
                  "messages, addressed three ways — broadcast (this machine), channel (one "
                  "checkout), direct (one agent).\n\n"
                  "IT STAYS A TRANSPONDER. Direct messages push into the recipient; broadcast and "
                  "channel are PULL-ONLY. This is not squeamishness about volume, it is that chat "
                  "traffic and the scope-violation alarm share one delivery path, and an agent "
                  "taught to skim the channel skims the alarm with it. The library's oldest lesson "
                  "is that a note printed forever is a note nobody reads. An agent that wants the "
                  "room asks for it — and one that has been hit once will ask.\n\n"
                  "Storage is an append-only log per address with a per-reader cursor, which is why "
                  "reading is NOT destructive: it marks the message read for you and leaves it "
                  "standing for everyone else. A queue that deletes on read cannot serve two "
                  "addressees, and cannot be re-read by an agent that wants to check what it was "
                  "told.\n\n"
                  "STATED LIMIT, because it will otherwise be discovered as a hang: there is no "
                  "wake-up. Nothing can push into a running turn, so a reply lands when the other "
                  "side next fires a hook, or never, if it has finished. This is letters, not "
                  "conversation. It carries 'I will need api/** when you are done' perfectly well "
                  "and cannot carry a handshake. The system stamps sender identity and the sender's "
                  "current claims onto every message, so an assertion about the map can always be "
                  "checked against the map rather than believed."},
         children=[
             Node(id="alt-no-channel-file-an-issue", kind="alternative",
                  name="Keep the status quo: a conflict tells the asker to take a narrower scope "
                       "and file an issue",
                  payload={"why": "what exists, and it is the absence of a channel wearing a "
                                  "procedure. It answers a live question — 'can I have this, and "
                                  "when?' — by sending one agent away to write a ticket for a human "
                                  "to read tomorrow, while the other agent is right there"}),
             Node(id="alt-push-everything", kind="alternative",
                  name="Deliver all traffic — broadcast and channel included — into every agent's "
                       "context on every hook call",
                  payload={"why": "the simplest thing, and it destroys two channels at once. It "
                                  "makes the chatter useless (nobody reads a feed) AND degrades the "
                                  "violation report, which arrives by the same route and depends on "
                                  "being rare to be read at all"}),
             Node(id="alt-structured-negotiation-protocol", kind="alternative",
                  name="A typed handshake — request / grant / release — instead of free-form text",
                  payload={"why": "solves exactly the case we thought of and nothing else, which is "
                                  "the opposite of the reason for building it. It also presumes a "
                                  "REPLY, and the no-wake-up limit means a protocol that waits for "
                                  "one is a protocol that hangs"}),
         ]),

    Node(id="inform-peers-never-police-an-agents-internals", kind="decision",
         name="The map addresses PEERS who cannot see each other; what happens inside one agent's "
              "delegation is not its business",
         links={"rests_on": ["hyp-a-parents-fan-out-is-its-own-coordination"]},
         payload={"rationale":
                  "Raised by the observation that a session's subagents all fire hooks under the "
                  "parent's session_id, so the map cannot tell them apart. The first instinct was to "
                  "treat that as a hole and go get an actor id. The user's objection is the "
                  "decision: it is not this library's job to make sure an agent does its own job "
                  "well.\n\n"
                  "The failure this whole project exists to prevent is AN AGENT NOT KNOWING ANOTHER "
                  "AGENT IS THERE. That is a statement about peers — two sessions, started by "
                  "different hands, with nothing above them that knows about both. A parent and its "
                  "subagents are not peers: the parent chose both tasks and knows exactly what it "
                  "dispatched. There is already a coordinator, and it is better informed than the "
                  "map could ever be, because it holds the intent and the map only ever sees paths. "
                  "The harness enforces the same partition from its own side — a subagent launch "
                  "tells the parent, unprompted, to avoid working the same files as its sibling.\n\n"
                  "So the cost of intervening is real and the benefit is speculative. To see inside "
                  "a delegation the courier would have to model what a subagent IS — a vendor's "
                  "internal strategy, undocumented, unobservable from a hook payload, and free to "
                  "change in any release. Checks built on that model would fire on arrangements that "
                  "are none of our business, perturb agents that were coordinating fine, and go "
                  "quietly wrong the first time a harness reorganises how it fans out. The courier's "
                  "value is that it speaks rarely and truly; every spurious notice is a withdrawal "
                  "from that account.\n\n"
                  "The boundary, so the next reader need not re-derive it: transponder informs an "
                  "agent of what it CANNOT see — another session's region, a write that landed in "
                  "one, history that moved underneath it. It does not inform an agent about its own "
                  "conduct, and it models nobody's internals. Where those collide, the map stays "
                  "silent and the tape stays honest.\n\n"
                  "Knowingly given up, and named in the hypothesis this rests on: if a parent fans "
                  "out two agents that collide in a file it never considered, nothing here catches "
                  "it. That is the same bet information-not-exclusion already made one level up."},
         children=[
             Node(id="alt-actor-ids-for-subagents", kind="alternative",
                  name="Key the map and the witness on a per-ACTOR id instead of the session id",
                  payload={"why": "the shape the problem first suggested, and it was written down as "
                                  "a discharge before being rejected on its merits (see "
                                  "retract-the-subagent-debt). Half of it is free — `declare_scope` "
                                  "already takes an id the agent supplies, so a subagent could name "
                                  "itself. The other half cannot be bought: the COURIER AND THE "
                                  "WITNESS learn identity only from the hook payload, which carries "
                                  "a session and nothing else. Shipping the free half alone makes "
                                  "the map MORE wrong — regions correctly split between two actors, "
                                  "every violation of them still attributed to whichever declared "
                                  "first. And buying the hard half means tracking a vendor's "
                                  "delegation model from outside it"}),
             Node(id="alt-warn-the-parent-on-fan-out", kind="alternative",
                  name="Have the courier warn a session when it spawns agents into its own region",
                  payload={"why": "noise aimed at the one party that already knows. It would fire on "
                                  "every ordinary fan-out — the overwhelmingly common, entirely "
                                  "correct case — to catch a collision nobody has yet observed "
                                  "happening by accident. Teaching agents to skim past the courier "
                                  "is the most expensive thing this library can do to itself"}),
         ]),
]

HYPOTHESES = [
    # KILLED 2026-07-13, the day it was written. Kept, not deleted: a ledger that quietly drops its
    # dead hypotheses is a ledger that cannot show you were wrong. The falsifier below asked for a
    # flight recording and got none — recording was never switched on — so the kill came from
    # harness transcripts instead. That the tape was missing at the one moment it was needed is
    # itself the finding, and it is why REPOLOCK_FLIGHT is now on by default.
    Node(id="hyp-readers-not-starved", kind="hypothesis",
         name="[FALSIFIED] Failing closed does not starve the reading session",
         payload={"claim": "Sessions that mostly read take the lock only when they run something "
                           "unrecognized, so a fail-closed gate does not turn a reading session "
                           "into a blocker in practice.",
                  "status": "falsified",
                  "killed_by": "xag/repolock#4 — two sessions, each refused by the other, on "
                               "`cd && cat`, `cd && git status`, `gh issue view` and `sleep`."},
         children=[
             Node(id="kill-readers-starved", kind="falsification",
                  payload={"claim": "A session is denied the lock by a holder whose whole turn "
                                    "wrote nothing.",
                           "fired_on": "2026-07-13"}),
         ]),

    Node(id="hyp-the-fingerprint-sees-every-write", kind="hypothesis",
         name="A fingerprint of HEAD + porcelain + the stat of each dirty path sees every write "
              "that matters",
         payload={"claim": "The whole design rests on this one observation being complete. HEAD "
                           "catches commits and rebases; the porcelain catches added, removed and "
                           "staged paths; the stat catches a re-edit of a file that was already "
                           "dirty (its status line does not move, but its bytes do). Writes to "
                           "git-ignored files are deliberately not seen — the lock protects what "
                           "git tracks. If this is wrong, the lock silently does not hold, which "
                           "is the worst failure available to it.",
                  "cadence": "every hook call"},
         children=[
             Node(id="kill-fingerprint-blind", kind="falsification",
                  payload={"claim":
                           "A tape shows a session's PostToolUse fingerprint UNCHANGED across a "
                           "tool call after which `git status` differs from what it was before — "
                           "i.e. the repo moved and the fingerprint did not see it. Cheap, "
                           "mechanical, and observable on any recording without anyone being "
                           "harmed first: this is what a falsifier is supposed to look like."}),
         ]),

    # HOLED 2026-07-14 by xag/repolock#11. Not falsified — AMENDED, and the amendment is the lesson.
    # The claim below reasons entirely about an ACTIVE holder, and on that ground it still stands.
    # But it ends with the words "rather than a ten-minute lease (#4)", and a ten-minute lease is
    # precisely what #11 cost a session that only wanted to run `ls && git log`. The holder was not
    # active at all: it had gone home, and `go_idle` had PARKED its lock on a dirty tree for the
    # remainder of the lease. The hypothesis never considered the idle path, so its claim was true
    # of every case it had thought about and false in production.
    #
    # Worse, and this is the finding worth keeping: `kill-shell-starvation` COULD NOT FIRE on it.
    # It was written to catch #4's shape — a non-writing holder refusing many consecutive calls —
    # and #11 arrived as ONE refusal from a holder that HAD written. A falsifier calibrated to the
    # last war is a falsifier that watches the wrong door. The kill below is the widened one.
    # MOOT 2026-07-15: information-not-exclusion deleted the hold this hypothesis was about.
    # Nothing serialises any more, so neither starvation nor its absence can be observed. Kept: its
    # amendment history (the #11 hole, the falsifier watching the wrong door) is the best record in
    # this file of why falsifiers must be recalibrated when the design moves.
    Node(id="hyp-serialising-shells-does-not-starve", kind="hypothesis",
         name="[MOOT] Holding the lock through a shell call does not starve a second session",
         payload={"claim": "A shell takes the lock for the duration of its own call and hands it "
                           "back if it read. So contention costs a second session only the length "
                           "of a command it happened to collide with — seconds, and a retry — "
                           "rather than a ten-minute lease (#4). Reads through Read/Grep/Glob are "
                           "not gated at all, so a blocked session can always still inspect and "
                           "diagnose, which is the escape route #4's victims did not have.",
                  "status": "holds for an ACTIVE holder; the IDLE holder was the hole (#11), and is "
                            "now closed by refuse-the-dirty-handback",
                  "cadence": "every hook call"},
         children=[
             Node(id="kill-shell-starvation", kind="falsification",
                  payload={"claim":
                           "A tape shows a session refused the lock on the same repo across more "
                           "than 3 consecutive tool calls by a holder that never wrote (a holder "
                           "whose fingerprint never moved) — the #4 shape returning through the "
                           "pessimistic hold. Fires on ANNOYANCE, before anything is lost, and it "
                           "is a query over a recording rather than a bug report from a human."}),
             Node(id="kill-refused-by-a-holder-who-went-home", kind="falsification",
                  payload={"claim":
                           "A tape shows `acquire` returning `held` where the holder's record "
                           "carries a non-null `idle_since` — a session refused by a holder that "
                           "has gone back to its human and is running nothing at all.\n\n"
                           "This is the door #11 came through, and the old falsifier was not "
                           "watching it: ONE refusal, by a holder that HAD written, whose lock was "
                           "parked rather than working. After refuse-the-dirty-handback an idle "
                           "holder should not be holding a lock at all — the only survivor is the "
                           "session that was asked to commit/ignore/stash and declined, which is "
                           "deliberate and rare. So this firing more than occasionally means the "
                           "handback refusal is not working, and it fires on the ANNOYANCE, "
                           "with nothing lost, straight off any recording.",
                           "fired_on": "2026-07-14 (retrospectively, on the #11 tape: "
                                       "a recorded tape)"}),
         ]),

    Node(id="hyp-the-dirty-handback-can-be-refused", kind="hypothesis",
         name="A session told to commit, ignore or stash will actually do it",
         payload={"claim": "refuse-the-dirty-handback rests on this and nothing else: that a Stop "
                           "hook which refuses the handback and explains the three routes (commit "
                           "your work / gitignore the artifact / stash the scrap) gets a CLEAN tree "
                           "on the second stop, in the ordinary case. If a session routinely "
                           "declines — or cannot, because the dirt is not its to resolve — then all "
                           "we have added is one wasted turn before parking the same lock we parked "
                           "before, and the honest move would be to shorten the idle lease instead.",
                  "cadence": "every Stop with a dirty tree"},
         children=[
             Node(id="kill-the-model-will-not-clean-up", kind="falsification",
                  payload={"claim":
                           "A tape shows `go_idle` returning `idle_dirty` (the asked-once-and-"
                           "declined path) on more than 1 in 4 of the Stops where the tree was "
                           "dirty — i.e. the refusal is being ignored rather than acted on. "
                           "Mechanical, needs no human to complain, and distinguishes the two "
                           "outcomes that matter: a session that cleans up (the lock frees itself) "
                           "from one that shrugs (the lock parks anyway, and this was theatre)."}),
         ]),

    # RE-VERIFIED 2026-07-22, and the verification is bad news now. The claim still holds exactly as
    # written — two subagents launched against one checkout fired every hook call under the PARENT's
    # session_id, and `kill-subagent-has-own-id` did not fire. What changed is the SIGN of the fact.
    # This hypothesis was written under v1, where a shared id was the thing standing between a parent
    # and a deadlock against its own child, so confirming it was a relief. `information-not-exclusion`
    # deleted the mutex, and under a MAP the same fact means N actors wear one identity and the map
    # cannot tell them apart. Carried on as hyp-a-parents-fan-out-is-its-own-coordination — which
    # holds that this costs nothing, because a parent is not a peer of its children.
    #
    # This is the second time in this file a falsifier has been found watching the wrong door (see
    # hyp-serialising-shells-does-not-starve). The lesson is not 'recalibrate the falsifier' — this
    # one is still correctly calibrated, and correctly silent. It is that a hypothesis carries an
    # unstated premise about WHY the answer matters, and a design change can invert that premise
    # while leaving the claim, the falsifier and the verdict all untouched and all still true.
    Node(id="hyp-subagents-share-the-session-id", kind="hypothesis",
         meta={"amended": "9eda8d6a89d8 re-verified 2026-07-22 and the status line says so; the "
                          "claim and its falsifier are untouched. What moved is what the answer "
                          "MEANS — carried on as hyp-a-parents-fan-out-is-its-own-coordination"},
         name="A subagent's tool calls carry its PARENT's session id, so the lock is reentrant "
              "across the whole agent tree",
         payload={"claim": "A session spawns subagents; a subagent writes the same checkout its "
                           "parent holds. The lock survives that only because the child's tool "
                           "calls reach the hook with the PARENT's session_id, and acquire() is "
                           "reentrant for the same session — so the parent RENEWS instead of "
                           "refusing itself.\n\n"
                           "This was never checked, and it is the difference between a lock and a "
                           "hang. If a subagent had an id of its own, a parent holding the lock "
                           "would refuse its own child WHILE BLOCKING ON THAT CHILD'S RESULT: a "
                           "deadlock that neither lock_wait nor the ticket can break, because the "
                           "holder is the very thing being waited for.\n\n"
                           "VERIFIED 2026-07-13 against a real headless run with a logging hook: "
                           "the parent's own Bash and its subagent's Bash arrived with the same "
                           "session_id. One level of nesting was exercised; deeper nesting is "
                           "inferred from the id being a property of the SESSION, not of the agent, "
                           "and that inference is what the falsifier below watches.",
                  "status": "verified 2026-07-13; re-verified 2026-07-22 with two subagents on one "
                            "checkout, still true, and no longer the relief it was written as — see "
                            "hyp-a-parents-fan-out-is-its-own-coordination",
                  "cadence": "every hook call"},
         children=[
             Node(id="kill-subagent-has-own-id", kind="falsification",
                  payload={"claim":
                           "A tape shows two DIFFERENT session_ids issuing hook calls against one "
                           "repo within a single conversation — i.e. an agent tree whose children "
                           "do not inherit the parent's id. Fires on the id itself, cheaply, on "
                           "any recording, LONG BEFORE anyone deadlocks: it does not wait for the "
                           "hang to prove the hang is possible."}),
         ]),

    Node(id="hyp-a-parents-fan-out-is-its-own-coordination", kind="hypothesis",
         name="A parent's own knowledge of what it dispatched is sufficient coordination for its "
              "subagents, so the map's blindness to them costs nothing",
         payload={"claim":
                  "Every subagent of a session fires its hooks under the PARENT's session_id "
                  "(hyp-subagents-share-the-session-id), so N actors wear one identity and the map "
                  "cannot tell them apart. Proved, on the tape, 2026-07-22: two subagents aimed at "
                  "one file were one agent to the map, and the conflict between them was "
                  "structurally unreportable.\n\n"
                  "The claim is that this costs nothing, because the arrangement it fails on is one "
                  "that does not arise unattended. A parent is not a peer of its children — it chose "
                  "both tasks — so the coordinator the map exists to substitute for is already "
                  "present, and better informed. The two subagents that collided did so because "
                  "they were INSTRUCTED to; that proves reachability, not occurrence.\n\n"
                  "What is genuinely open, and the only part worth watching, is UNINTENDED overlap: "
                  "a parent partitions by TASK, and two tasks can meet in a FILE it never considered "
                  "— 'fix the auth bug' and 'add rate limiting' landing in one middleware module. If "
                  "that turns out to be common, this belief is wrong and "
                  "inform-peers-never-police-an-agents-internals loses its ground.",
                  "status": "believed, not yet observed either way; the falsifier below has not been "
                            "run over the transcript history that could answer it",
                  "cadence": "any fan-out into a shared checkout"},
         children=[
             Node(id="kill-subagents-collide-unintended", kind="falsification",
                  payload={"claim":
                           "A harness transcript shows two concurrent subagents of ONE session "
                           "writing the same path, where the parent's dispatch did not aim them "
                           "there — collision by accident rather than by instruction.\n\n"
                           "Note WHERE this must be observed, because it is the one falsifier in "
                           "this file that cannot be run against our own tapes: subagents are "
                           "invisible in them, and that invisibility is the very fact under test. "
                           "It is a query over harness transcripts, which do record each subagent's "
                           "tool calls with their file paths. Retrospective, mechanical, needs "
                           "nobody to lose work first — it fires on a near-miss in history that has "
                           "already happened."}),
         ]),

    Node(id="hyp-renewal-on-activity", kind="hypothesis",
         name="Renew-on-tool-call keeps live CLAIMS from lapsing mid-work",
         payload={"claim": "The claim lease (scope.LEASE_SECONDS) outlasts the longest single tool "
                           "call, so an active participant never falls off the map between calls — "
                           "the lock died, but leases survive as the decay rate of information, and "
                           "a participant that silently vanished from the map mid-task misleads "
                           "everyone reading it.",
                  "cadence": "every hook call"},
         children=[
             Node(id="kill-renewal", kind="falsification",
                  payload={"claim": "A flight recording shows a lease lapsing between two tool "
                                    "calls of one still-active session (a single call longer than "
                                    "the lease)."}),
         ]),
]

DEBTS = [
    # RESOLVED 2026-07-15 — both, by the same stroke, and neither was PAID: the claims they deviated
    # from ceased to exist. `information-not-exclusion` deleted the mutex, so there is no exclusion
    # for `mcp-writes-settle-late` to be a hole in — a debt is a deviation from the system's own
    # claims, and the system no longer claims exclusion on any path. And the Cursor adapter — the
    # hand-rolled wire format nobody had ever watched a real client emit, the textbook
    # 'uninstrumented fake' — was DELETED rather than verified: the unsound thing was removed, which
    # discharges the debt as honestly as paying it would have. Both kept, params grounded to record
    # the resolution, because a ledger that quietly drops its history cannot show it was ever wrong.
    Node(
        id="cursor-settles-late",
        kind="debt",
        name="[RESOLVED: the adapter was deleted] The Cursor adapter held a read's lock until its "
             "next hook call",
        payload={
            "note":
                "Adapter #1 settles the speculative lock at PostToolUse — the instant the command "
                "returns. Cursor's post-tool event has NOT been verified against the real client, "
                "and hand-rolling a vendor's wire format from memory is exactly the 'uninstrumented "
                "fake' failure: a guess wearing the costume of an integration. So the Cursor adapter "
                "settles LAZILY, at the start of its next hook call.\n\n"
                "Between a read-only shell and whatever Cursor does next, that session therefore "
                "holds a lock it does not need. It is bounded by the lease and by the next event, "
                "but it is a live #4 risk that adapter #1 does not carry, and it is unsound. It is a "
                "debt, not a hypothesis: nothing about it is uncertain — I know it is wrong, I know "
                "why, and I know what would fix it.",
        },
        params={
            "settles_at_post_tool": Quantity(
                value=0, unit="adapter", provenance="resolved by deletion, 2026-07-15", grounded=True,
                source="repolock/hooks/cursor.py no longer exists — the adapter was a wire format "
                       "hand-rolled from memory and never once checked against a running client. "
                       "Removing the unsound thing discharges the debt; a future Cursor adapter "
                       "starts from OBSERVING Cursor, per verify-cursors-post-event"),
        },
        children=[
            Node(id="verify-cursors-post-event", kind="discharge",
                 name="Run a real Cursor session against the hook, capture the events it actually "
                      "fires, and settle the speculative lock in the true post-tool event",
                 payload={"competence": "has Cursor installed and can read the events it emits; "
                                        "the recorder is already on, so the tape is the evidence",
                          "note": "Discharged by OBSERVING Cursor, not by reading its docs and "
                                  "believing them — which is the same rule the rest of this "
                                  "library now lives by."}),
        ],
    ),

    Node(
        id="mcp-writes-settle-late",
        kind="debt",
        meta={"amended": "5bc3134bbb35 tightened under the 600-word budget; every claim kept"},
        name="[RESOLVED: the mutex was deleted everywhere] On the MCP path there was no mutex — a "
             "write was detected one call after it landed",
        payload={
            "note":
                "A shell is HELD through (hold-the-lock-through-the-unknown), so two sessions "
                "cannot write one checkout through it: the second is refused before it runs. An "
                "MCP tool is not held through, because it MUST NOT be "
                "(mcp-is-watched-never-gated) — the off switch, the blocking wait and the blocked "
                "session's 'file an issue and move on' are all MCP calls, and a gate would stand in "
                "front of all three. So the window that §7b refuses to accept for a shell is "
                "accepted here, knowingly: between the fingerprint before an MCP call and the one "
                "after it, a second session can write the same repo, and all the adapter can do is "
                "prove it happened and shout.\n\n"
                "This is a DEBT and not a hypothesis, and the distinction is the one this ledger "
                "was rebuilt to keep honest. Nothing about it is uncertain. The hole is reachable "
                "by construction, it is reachable today, and I know exactly what would close it — "
                "filing it as a belief awaiting evidence, a falsifier firing the first time "
                "somebody loses work, would hide it behind the shape of an experiment: the "
                "mistake hold-the-lock-through-the-unknown was written to reverse.\n\n"
                "It is narrower than it sounds — an MCP tool must be able to write the "
                "filesystem at all to reach it. On this machine exactly one can "
                "(`mcp__ide__executeCode`, code in a kernel); the rest are remote calls that "
                "cannot touch a working copy.",
        },
        params={
            "prevents_the_collision": Quantity(
                value=0, unit="mcp-call", provenance="resolved by design change, 2026-07-15",
                grounded=True,
                source="information-not-exclusion deleted the mutex from EVERY path, so detection "
                       "one call late stopped being a deviation and became the stated design. A "
                       "debt is a gap between the system and its own claims; the claim is gone, and "
                       "SPEC.md now says detection-not-prevention out loud where §7b used to forbid "
                       "it"),
        },
        children=[
            Node(id="gate-mcp-on-a-declared-target", kind="discharge",
                 name="Gate an MCP tool the moment its harness declares what it will write — a path "
                      "in the tool input, the way Edit carries one — and it joins obligation 1",
                 payload={"competence": "whoever owns the harness's MCP tool schema",
                          "note": "The debt exists because the call arrives with no declared target, "
                                  "so there is nothing to lock ON before it runs. Given one, the "
                                  "tool stops being unknown, is gated before it runs like any Edit, "
                                  "and the window closes with no guessing anywhere — WITHOUT gating "
                                  "the calls that carry no target, which is what keeps the off "
                                  "switch reachable. Discharged by that fact arriving, never by "
                                  "deciding the risk feels small."}),

            # This discharge was written as: "whoever owns the harness's MCP tool schema; nothing in
            # this library can grant itself the fact it is missing." That sentence was FALSE, and
            # believing it is what made the debt look permanent — which is the most dangerous thing
            # a debt can look, because nobody pays a debt they have been told cannot be paid.
            Node(id="scope-declared-by-the-agent", kind="discharge",
                 name="Have the AGENT declare the scope it will write, and reserve it before any "
                      "tool runs (xag/repolock#14)",
                 payload={"competence": "this library, plus an agent that declares a scope — no "
                                        "vendor has to change anything, because the declaration "
                                        "arrives over MCP, which is ungated by construction (§7c)",
                          "note": "The missing fact is not a property of the harness's tool schema. "
                                  "It is a property of the AGENT'S INTENT, and the agent can be made "
                                  "to say it out loud before it acts.\n\n"
                                  "It also moves WHEN the collision is prevented, which is the whole "
                                  "trick. Today we prevent at the WRITE, which is why MCP is a hole: "
                                  "the call declares nothing, so there is nothing to gate on short of "
                                  "gating the channel — and the channel carries the off switch. Under "
                                  "a reservation we prevent at the SCOPE: a write landing inside my "
                                  "region cannot collide with anyone, because nobody else is "
                                  "permitted there. The ungated channel stays ungated and the hole "
                                  "still closes.\n\n"
                                  "NOT free, and #14 states the price: it trades hard exclusion for a "
                                  "contract on the shell path, which is the exact trade §7b refuses. "
                                  "So it discharges this debt only as a decision that SUPERSEDES that "
                                  "one, argued in the open — never by quietly widening what counts as "
                                  "sound."}),
        ],
    ),

]

RETRACTIONS = [
    Node(
        id="retract-the-subagent-debt",
        kind="retraction",
        name="`subagent-writes-are-unattributable` was a mood filed as a debt",
        payload={
            "why":
                "Filed 2026-07-22 and buried the same day, before it was ever committed to as a "
                "debt. The observation behind it is sound and survives as "
                "hyp-a-parents-fan-out-is-its-own-coordination: two subagents on one checkout fire "
                "every hook under the parent's session_id, so the map cannot tell them apart. What "
                "was wrong was the KIND, and this vocabulary names the error in as many words — a "
                "retraction is for 'a positioning statement filed as a hypothesis, A MOOD FILED AS "
                "A DEBT'.\n\n"
                "A debt is known-unsound with nothing uncertain. Half of this was certain: IF two "
                "subagents compete for a region, the map is blind to it — proved, on the tape. The "
                "other half was assumed: that they WILL. And they were only observed doing so "
                "because the test instructed them to. A constructed collision proves reachability, "
                "which is not occurrence, and dressing an untested behavioural belief as a proved "
                "hole is the same error as the one this ledger was rebuilt to stop — running the "
                "other way. The 2026-07-13 mistake filed a proved hole as a hypothesis; this filed "
                "an open question as a debt. Both launder the distinction the gate depends on, and "
                "a gate that goes red on a guess teaches its reader to discount it.\n\n"
                "It also carried a discharge — 'key the map on an actor id' — that named work this "
                "project has now decided it should NOT do. That is not a debt waiting to be paid. "
                "It is a rejected alternative, and it lives as one under "
                "inform-peers-never-police-an-agents-internals.",
        },
        children=[
            Node(id="tomb-subagent-debt", kind="tombstone",
                 payload={"was": "subagent-writes-are-unattributable",
                          "why": "never valid: an open behavioural question filed as a "
                                 "known-unsound thing. Carried on, as the hypothesis it always "
                                 "was, by hyp-a-parents-fan-out-is-its-own-coordination"}),
            Node(id="tomb-subagent-discharge", kind="tombstone",
                 payload={"was": "subagent-writes-are-unattributable/identify-the-actor-not-the-"
                                 "session",
                          "why": "a discharge for a debt that was never a debt, naming work that "
                                 "has since been rejected on its merits. It stands as "
                                 "alt-actor-ids-for-subagents under "
                                 "inform-peers-never-police-an-agents-internals"}),
        ],
    ),
]

# --- the gate ------------------------------------------------------------------------

GATE = Node(
    id="release",
    kind="gate",
    meta={"amended": "17082cb69446 the note records a red that should not have happened and what it "
                     "teaches; `admits` is back to what it was, and nothing admitted was changed"},
    name="What is allowed onto the write path of every session on a machine",
    payload={
        "note":
            "This library runs a hook on every tool call of every agent session on the machine. "
            "That is an unforgiving place for an unsound thing, so this gate exists to stop one "
            "travelling there quietly.\n\n"
            "It is GREEN, for the first time, and it is worth recording HOW — because neither debt "
            "was paid. Both dissolved when information-not-exclusion changed what the system "
            "claims: mcp-writes-settle-late was a hole in a mutex, and there is no mutex left "
            "anywhere for it to be a hole in; cursor-settles-late lived in an adapter that was "
            "deleted rather than verified. A debt is a deviation from the system's own claims. "
            "Changing the claims, out loud, in the spec, with the user driving, is a legitimate "
            "way for a debt to die — quietly weakening the claims to launder a debt is not, and "
            "the difference is whether the change is written where the next reader must see it. "
            "It is: SPEC.md says detection-not-prevention on every path, as the design.\n\n"
            "It went RED for a few minutes on 2026-07-22 and should not have. A debt was filed for "
            "the map's blindness to subagents, and the blindness is real — but whether anything "
            "unsound TRAVELS through it is an open behavioural question, not a known defect. See "
            "retract-the-subagent-debt. The near-miss is worth keeping in front of this gate: the "
            "temptation was to admit a proved MECHANISM rather than a proved HARM, and a gate that "
            "reddens on a mechanism nobody has seen fire is a gate its reader learns to wave "
            "through. What guards that question now is a hypothesis with a falsifier, which is "
            "where an open question belongs.",
    },
    links={"admits": ["cursor-settles-late", "mcp-writes-settle-late"]},
)


def build() -> Quern:
    """The ledger, with ledger@0.1.0's semantics staged beneath it.

    The vocabulary is PINNED, not restated. An earlier version of this file authored its own kinds
    and left out `debt`, `discharge` and `gate` — so a known-unsound shortcut had nowhere to live
    and was filed as a hypothesis instead, with a falsifier that would only fire once it had cost
    someone their work. The package that models this properly already existed.
    """
    # The channel of quern#19 landed and this function lost its tempdir, as promised. This
    # repo is public and quern's registry is not, so the synced cache (.quern/library) is
    # COMMITTED, not ignored: the package travels as data in the repo itself, verified
    # against quern.lock's digests at every load, and the check still simply skips where
    # quern (the Python) is not installed. A registry, when reachable, only refreshes it.
    lib, refs = consume(_ROOT, os.environ.get("QUERN_REGISTRY", _ROOT.parent / "quern-registry"))

    quern = Quern(packages=[next(r for r in refs if r.name == "ledger")])
    quern = lib.effective(quern)

    quern.root.children = [*DECISIONS, *HYPOTHESES, *DEBTS, *RETRACTIONS, GATE]
    return quern


LEDGER = build()

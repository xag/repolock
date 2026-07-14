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
`bom.ledger` models this correctly and always did: `debt` (known-unsound, carried on purpose),
`discharge` (what clears it, and who is competent to), and `gate` (the point past which unsound
things must not travel, with `nothing-unsound-passes-a-gate` to enforce it). Re-authoring a poorer
copy of that vocabulary is what left the lie somewhere to live. So: pin the package, do not restate
it. (The hole itself is now closed in code — see `hold-the-lock-through-the-unknown`.)

Authored against `bom`, which is a private library: it is NOT a dependency of this public package.
The ledger check skips where bom is not installed, and runs on machines that have it.
"""

from __future__ import annotations

import os
from pathlib import Path

import bom.grounding  # noqa: F401 -- the natives; the package itself arrives by pin
from bom import Bom, Node, Quantity
from bom.library import consume

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
         name="Liveness = unexpired lease AND live holder; pid<=0 degrades to lease-only",
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
         name="A shell takes the lock BEFORE it runs, and gives it back if it turns out to have read",
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
         name="A refused session is told what is happening, what it may still do, and how to wait",
         payload={"rationale":
                  "The pessimistic hold (above) closed the write window and opened a worse hole in "
                  "the same stroke: a refused session could not WAIT. Waiting means running `sleep`; "
                  "`sleep` is a shell command; the shell is exactly what was refused. So the one "
                  "thing a blocked session most needs to do was the one thing it was blocked from "
                  "doing — which is #4's cruellest detail ('`sleep` was also a write') arriving "
                  "through the new door, and it was not noticed until the user asked what the "
                  "blocked process was supposed to DO.\n\n"
                  "And the refusal itself said `session 8663de9b (Bash)`. An ID and a tool name. "
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
         name="A blocked session subscribes and gets on with something else; the gate mints the "
              "waiter as a one-time ticket",
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
         name="A backgrounded task holds the lock; it is never settled by a fingerprint",
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
         name="The waiter's ticket is minted once per shell, and a test drives a real one",
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
    Node(id="hyp-serialising-shells-does-not-starve", kind="hypothesis",
         name="Holding the lock through a shell call does not starve a second session",
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
                                       "flight-20260714-110251-13056.jsonl)"}),
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

    Node(id="hyp-subagents-share-the-session-id", kind="hypothesis",
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
                  "status": "verified once, watched continuously",
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

    Node(id="hyp-renewal-on-activity", kind="hypothesis",
         name="Renew-on-tool-call keeps live sessions from lapsing mid-work",
         payload={"claim": "A 600s hook lease outlasts the longest single tool call, so an active "
                           "session never loses its lock between calls.",
                  "cadence": "every hook call"},
         children=[
             Node(id="kill-renewal", kind="falsification",
                  payload={"claim": "A flight recording shows a lease lapsing between two tool "
                                    "calls of one still-active session (a single call longer than "
                                    "the lease)."}),
         ]),
]

DEBTS = [
    Node(
        id="cursor-settles-late",
        kind="debt",
        name="The Cursor adapter holds a read's lock until its next hook call",
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
                value=0, unit="adapter", provenance="not verified", grounded=False,
                source="repolock/hooks/cursor.py::_catch_up — settles on the NEXT event, because "
                       "Cursor's post-tool event name and payload were never checked against a "
                       "running client"),
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
]

# --- the gate ------------------------------------------------------------------------

GATE = Node(
    id="release",
    kind="gate",
    name="What is allowed onto the write path of every session on a machine",
    payload={
        "note":
            "repolock sits on the write path of every agent session on the machine. That is an "
            "unforgiving place for an unsound thing, so this gate exists to stop one travelling "
            "there quietly.\n\n"
            "It is RED, and it names why: the Cursor adapter settles its speculative lock late, "
            "because nobody has ever watched Cursor emit an event. Discharge it by doing the work "
            "the discharge names — running the real client and reading the tape. Never by editing "
            "this file.",
    },
    links={"admits": ["cursor-settles-late"]},
)


def build() -> Bom:
    """The ledger, with ledger@0.1.0's semantics staged beneath it.

    The vocabulary is PINNED, not restated. An earlier version of this file authored its own kinds
    and left out `debt`, `discharge` and `gate` — so a known-unsound shortcut had nowhere to live
    and was filed as a hypothesis instead, with a falsifier that would only fire once it had cost
    someone their work. The package that models this properly already existed.
    """
    # The channel of bom#19 landed and this function lost its tempdir, as promised. This
    # repo is public and bom's registry is not, so the synced cache (.bom/library) is
    # COMMITTED, not ignored: the package travels as data in the repo itself, verified
    # against bom.lock's digests at every load, and the check still simply skips where
    # bom (the Python) is not installed. A registry, when reachable, only refreshes it.
    lib, refs = consume(_ROOT, os.environ.get("BOM_REGISTRY", _ROOT.parent / "bom-registry"))

    bom = Bom(packages=[next(r for r in refs if r.name == "ledger")])
    bom = lib.effective(bom)

    bom.root.children = [*DECISIONS, *HYPOTHESES, *DEBTS, GATE]
    return bom


LEDGER = build()

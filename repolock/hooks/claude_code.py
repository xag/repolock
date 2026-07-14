"""The Claude Code hook that makes the repo lock binding instead of advisory — adapter #1.

An MCP tool alone would be a suggestion. The session that rebases `main` underneath another session
would never have called `lock_repo` — nobody had told it to. What makes a lock mean something is a
gate the model cannot forget to walk through, so the enforcement lives here, in the harness, and the
model never has to remember anything.

Four events, and the split between the first two is the whole of v1 (see hooks/common.py):

  PreToolUse   Edit/Write/NotebookEdit say WHICH FILE they will write. That is ground truth, so the
               lock is taken before the write, on the repo that owns that file. Held by someone
               live => exit 2, which blocks the tool and hands the reason back to the model.

               A shell says nothing we can trust. We do not read its command text — that guess was
               wrong in both directions (#4, #7) and is not fixable. We refuse it only against a
               live holder with a dirty tree, take the before-fingerprint, and let it run.

  PostToolUse  the after-fingerprint. Moved => that tool wrote, as a fact; take the lock (and if
               someone else holds it, we have just collided — say so). Unmoved => it was a read,
               whatever it looked like, and it is charged nothing.

  Stop         the model is handing control back to the human. Clean tree => release (a session
               waiting on someone at lunch must not starve every other session). Dirty tree => hold,
               mark it idle, let the declared lease run out; handing over a checkout full of
               half-finished edits is worse than making the next session wait.

  SessionStart the read-side check: compare the HEAD this session last saw against the HEAD that is
               there now, and say so if history moved. No lock involved.

Install: a `hooks` block in ~/.claude/settings.json (user scope, so every repo on the machine is
guarded, not just one) wiring PreToolUse and PostToolUse (matcher Edit|Write|MultiEdit|NotebookEdit|
Bash|PowerShell — the shells BOTH have to be there; on Windows PowerShell is the one that gets
used), Stop, and SessionStart to run this script via a python that can import `repolock`, by
absolute path — at user scope $CLAUDE_PROJECT_DIR points at whatever project the session is in.

Kill switch: `~/.repolock/DISABLED` (see env.disabled) makes every event a no-op, including in
sessions that already snapshotted the hooks and cannot be reached by editing settings.json.
"""

from __future__ import annotations

import json
import os
import sys

try:
    from repolock import env, lock
    from repolock.hooks import common
except ImportError:                               # run straight from a checkout, uninstalled
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    from repolock import env, lock
    from repolock.hooks import common

# The tools that tell us what they will write. The ONLY place a write is known in advance — and the
# input carries the path, so the lock target is a fact too. Everything else is "unknown", including
# every shell, and unknown is no longer a synonym for either "read" or "write".
WRITING_TOOLS = {"Edit", "Write", "MultiEdit", "NotebookEdit"}


def _deny(reason: str) -> None:
    """Exit 2 blocks the tool call and feeds stderr back to the model as the reason."""
    print(reason, file=sys.stderr)
    sys.exit(2)


def _say(msg: str) -> None:
    print(msg)


def _record() -> None:
    """Arm the recorder immediately before the first call into `lock` — never at the top of main().
    The boundary being recorded IS the lock; a hook call that touches no lock has nothing to record,
    and installing eagerly taxed every read with a ~110ms flight-recorder import.

    A MISSING recorder must never cost the lock. `flight-recorder` is an optional extra and
    recording is on by default, so a plain `pip install .` has no recorder — and until this
    try/except existed, the ImportError travelled up into main()'s fail-open handler and every hook
    call no-oped. The lock looked installed, printed a line to stderr nobody reads, and guarded
    nothing. An optional dependency that silently disables the whole tool when it is absent is not
    optional; it is a hard dependency with a bug.
    """
    if not env.recording():
        return
    try:
        from repolock import flight
    except ImportError:
        return                            # no recorder installed: run without a tape, not without a lock
    flight.install()


def _intent(payload: dict) -> str:
    """What this session is about to do, in words the NEXT session can act on.

    The refusal used to read `session 8663de9b (Bash)`, which tells a blocked session nothing at all
    — it cannot judge whether to wait 10 seconds or go and do something else. So the intent carries
    the target: the file being edited, or the command being run.

    Note carefully what this is NOT. The command text is recorded to be *shown to a human or another
    agent*. Nothing branches on it. The moment anything in this library decides something by reading
    a command, we are back to #7 and #4 — see hooks/common.py.
    """
    tool = payload.get("tool_name") or "tool"
    ti = payload.get("tool_input") or {}
    path = ti.get("file_path") or ti.get("notebook_path")
    if path:
        return f"{tool} {os.path.basename(path)}"
    command = ti.get("command")
    if isinstance(command, str) and command.strip():
        one = " ".join(command.split())
        return f"{tool}: {one[:70]}" + ("…" if len(one) > 70 else "")
    return tool


def _target(payload: dict) -> tuple[str | None, bool]:
    """(repo, known_write). The repo a tool is about to act on, and whether we KNOW it writes.

    For a file-editing tool the repo comes from its own file_path — not from cwd, which is a
    different repo often enough to matter (#8). For anything else it is the repo the session sits
    in, and all we can say is that we do not know.
    """
    tool = payload.get("tool_name") or ""
    tool_input = payload.get("tool_input") or {}
    if tool in WRITING_TOOLS:
        path = tool_input.get("file_path") or tool_input.get("notebook_path") or ""
        return common.repo_of(path), True
    return common.repo_root(payload.get("cwd") or os.getcwd()), False


def pre_tool_use(payload: dict) -> None:
    repo, known_write = _target(payload)
    if not repo:
        return                       # not a git checkout — nothing to protect, nothing to lock

    _record()
    session = payload.get("session_id") or "unknown"
    intent = _intent(payload)

    if known_write:
        denial, notes = common.gate(repo, session, intent)
        if denial:
            _deny(denial)
        for note in notes:
            _say(note)
        return

    tool_input = payload.get("tool_input") or {}

    # The one command a refused session is allowed to run here: the background waiter this gate
    # itself minted. Byte-for-byte equality against a string we wrote — recognising our own token,
    # not reading someone else's command. Without it, a blocked session cannot even wait, because
    # waiting is a shell and the shell is what it was refused.
    if common.is_ticket(session, repo, tool_input.get("command") or ""):
        return

    # Unknown effect: take the lock on speculation rather than form an opinion about the command.
    # PostToolUse hands it straight back if the repo did not move — unless the task was BACKGROUNDED,
    # in which case there is nothing to observe yet and the lock is held instead of guessed at.
    denial, notes = common.hold_unknown(
        repo, session, intent, background=bool(tool_input.get("run_in_background")))
    if denial:
        _deny(denial)
    for note in notes:
        _say(note)


def post_tool_use(payload: dict) -> None:
    """Where the speculation is settled: the lock is kept if the repo moved, released if it did not.

    This is the half that makes the pessimistic hold affordable. Without it, every `cat` would keep
    a ten-minute lease and we would be back in #4 within the hour.
    """
    repo, known_write = _target(payload)
    if not repo or known_write:
        return                       # a declared write took the lock before it ran, and keeps it

    _record()
    session = payload.get("session_id") or "unknown"
    for note in common.settle_unknown(repo, session):
        _say(note)


def stop(payload: dict) -> None:
    """The handback, and the one event that is allowed to say NO to the model itself.

    Exit 2 here does not block a tool — it blocks the *stop*, and hands the reason back so the
    session keeps working. That is what lets us refuse the dirty handback outright (see
    common.hand_back) instead of holding a lock on the mess it leaves behind.

    `stop_hook_active` is the harness telling us we already blocked this stop once. We ask exactly
    once and then get out of the way: Claude Code overrides a Stop hook that blocks eight times
    running, and a session that cannot hand back to its human is a worse failure than any lock.
    """
    repo = common.repo_root(payload.get("cwd") or os.getcwd())
    if not repo:
        return
    _record()
    session = payload.get("session_id") or "unknown"
    block, notes = common.hand_back(
        repo, session, already_asked=bool(payload.get("stop_hook_active")))
    if block:
        _deny(block)
    for note in notes:
        _say(note)

def session_start(payload: dict) -> None:
    repo = common.repo_root(payload.get("cwd") or os.getcwd())
    if not repo:
        return
    _record()
    session = payload.get("session_id") or "unknown"
    if note := common.drift_note(session, repo):
        _say(note)


HANDLERS = {
    "PreToolUse": pre_tool_use,
    "PostToolUse": post_tool_use,
    "Stop": stop,
    "SessionStart": session_start,
    "UserPromptSubmit": session_start,   # same read-side check, on the way back in
}


# --- wiring this adapter into (and out of) the harness ------------------------------------------
#
# The hook block was, until now, a stanza in the README that a human pasted into settings.json by
# hand. That is how you get the DEGRADED case common.py has to detect at runtime: a PostToolUse that
# never made it across, and a lock that is then never handed back. An install that can be got wrong
# by hand should be done by a program.

# Matchers per event. The shells BOTH have to be in the PreToolUse/PostToolUse matchers — on Windows
# PowerShell is the one that actually gets used, and a missing PostToolUse is bug #4 wearing a hat.
EVENTS = {
    "PreToolUse": "Edit|Write|MultiEdit|NotebookEdit|Bash|PowerShell",
    "PostToolUse": "Bash|PowerShell",
    "Stop": None,
    "SessionStart": None,
}


def settings_path() -> str:
    """User scope, not project scope: the lock guards every checkout on the machine, and at user
    scope $CLAUDE_PROJECT_DIR points at whatever project the session happens to be in."""
    return os.getenv("REPOLOCK_CLAUDE_SETTINGS") or os.path.join(
        os.path.expanduser("~"), ".claude", "settings.json")


def hook_command() -> str:
    """The command the harness will run — quoted, and spelled with forward slashes.

    The same lesson as the waiter's ticket (#10), and it is not a coincidence: this string is also
    handed to a shell. Quoted, so a space in the path survives; forward slashes, so there is no
    backslash for a POSIX shell to eat. Windows accepts them everywhere.
    """
    py = sys.executable.replace("\\", "/")
    script = os.path.abspath(__file__).replace("\\", "/")
    return f'"{py}" "{script}"'


# The adapter script's own directory, which is what makes a hook entry OURS. Specific on purpose:
# the first version of this tested for the bare substring `repolock`, and a foreign hook whose
# command merely CONTAINED the word (`echo not-repolock`) was identified as ours and deleted by
# uninstall. A substring is not an identity. This fragment names the file we actually install, and
# is stable across checkouts (any path ending .../repolock/hooks/claude_code.py).
MARKER = "repolock/hooks/"


def _ours(entry: dict) -> bool:
    """Is this hook entry one of ours? Uninstalling repolock must not uninstall anything else."""
    return any(MARKER in (h.get("command") or "").replace("\\", "/").lower()
               for h in (entry.get("hooks") or []))


def hook_block() -> dict:
    cmd = hook_command()
    block: dict[str, list] = {}
    for event, matcher in EVENTS.items():
        hook = {"type": "command", "timeout": 20, "command": cmd}
        if event == "PreToolUse":
            hook["statusMessage"] = "checking the repo lock"
        entry: dict = {"hooks": [hook]}
        if matcher:
            entry["matcher"] = matcher
        block[event] = [entry]
    return block


def _load_settings(path: str) -> dict:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return {}


def _save_settings(path: str, data: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
        f.write("\n")
    os.replace(tmp, path)                 # atomic: a torn settings.json is a harness that won't start


def wired(path: str | None = None) -> bool:
    """Are ALL four of our events wired? Three out of four is the degraded install, not an install."""
    hooks = _load_settings(path or settings_path()).get("hooks") or {}
    return all(any(_ours(e) for e in (hooks.get(ev) or [])) for ev in EVENTS)


def install(path: str | None = None) -> bool:
    """Wire the four events. Idempotent, and it preserves any hooks that are not ours."""
    path = path or settings_path()
    data = _load_settings(path)
    hooks = data.setdefault("hooks", {})
    for event, entries in hook_block().items():
        keep = [e for e in (hooks.get(event) or []) if not _ours(e)]
        hooks[event] = keep + entries
    _save_settings(path, data)
    return True


def uninstall(path: str | None = None) -> bool:
    """Remove only our entries, and drop an event key only if nothing else was using it."""
    path = path or settings_path()
    data = _load_settings(path)
    hooks = data.get("hooks") or {}
    if not hooks:
        return False
    for event in list(hooks):
        keep = [e for e in (hooks.get(event) or []) if not _ours(e)]
        if keep:
            hooks[event] = keep
        else:
            hooks.pop(event, None)
    if not hooks:
        data.pop("hooks", None)
    _save_settings(path, data)
    return True


def _utf8() -> None:
    """Say what we mean, in the encoding the harness actually reads.

    Python on Windows writes stderr in the ANSI code page, and the harness decodes it as UTF-8. So
    every em-dash in a refusal — and this module's refusals are made of them — arrived at the model
    as U+FFFD (seen on the #10 tape: `REPO LOCKED � another agent session...`). The text a gate
    uses to explain itself is the only thing it has; delivering it as mojibake is not cosmetic.
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, OSError):
            pass                          # not a real console stream; nothing to fix


def main() -> None:
    _utf8()
    if env.disabled():
        sys.exit(0)                       # the panic switch — see env.disabled()

    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        sys.exit(0)                       # a hook that cannot parse its input must not block work

    handler = HANDLERS.get(payload.get("hook_event_name") or "")
    if not handler:
        sys.exit(0)

    try:
        handler(payload)
    except SystemExit:
        raise
    except Exception as e:                # noqa: BLE001
        # A crashing hook must never wedge the session. Fail OPEN, loudly: an unguarded write is
        # bad, but a laptop where nobody can edit anything is worse — and silent is worst.
        print(f"repo-lock hook error ({type(e).__name__}: {e}) — proceeding unguarded",
              file=sys.stderr)
        sys.exit(0)


if __name__ == "__main__":
    main()

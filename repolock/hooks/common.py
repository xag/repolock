"""What every harness adapter shares: the parts of an adapter that are not the harness.

An adapter is a thin translation — harness event in, SPEC.md obligation out. The obligations
themselves (which commands write, what a refusal must say, how a session remembers the HEAD it
last saw) are identical across harnesses, so they live here and each adapter keeps only its
vendor's wire format.
"""

from __future__ import annotations

import hashlib
import os
import subprocess

from repolock import env, lock

# git subcommands that mutate the working copy or rewrite history. `rebase` is on this list
# because a rebase is a *sequence* of commits, and the lock must span the whole sequence.
WRITING_GIT = ("commit", "rebase", "merge", "reset", "checkout", "switch", "restore",
               "cherry-pick", "revert", "apply", "am", "stash", "push", "pull", "clean", "mv",
               "rm", "add")

LEASE_SECONDS = 600          # renewed on every tool call; must outlast the longest single call
SEEN_DIR = "seen"


def repo_root(cwd: str) -> str | None:
    try:
        res = subprocess.run(["git", "rev-parse", "--show-toplevel"], cwd=cwd,
                             capture_output=True, text=True, timeout=15)
    except (OSError, subprocess.SubprocessError):
        return None
    return res.stdout.strip() or None if res.returncode == 0 else None


def git_writes(command: str) -> bool:
    """Does this shell command mutate a working copy or its history?

    Cheap and deliberately over-inclusive: a false positive costs a lock we'd have taken
    anyway; a false negative is an unguarded write, which is the bug.
    """
    for part in (command or "").strip().split("&&"):
        toks = part.split()
        if len(toks) >= 2 and toks[0] == "git" and toks[1] in WRITING_GIT:
            return True
    return False


# --- the per-(session, repo) memory of the last-seen HEAD ----------------------

def _seen_path(session: str, repo: str) -> str:
    d = os.path.join(os.path.dirname(env.record_path(repo)), SEEN_DIR)
    os.makedirs(d, exist_ok=True)
    key = hashlib.sha256(f"{session}:{repo}".encode()).hexdigest()[:16]
    return os.path.join(d, f"{key}.txt")


def remember_head(session: str, repo: str, head: str | None) -> None:
    if not head:
        return
    try:
        with open(_seen_path(session, repo), "w", encoding="utf-8") as f:
            f.write(head)
    except OSError:
        pass


def last_seen_head(session: str, repo: str) -> str | None:
    try:
        with open(_seen_path(session, repo), encoding="utf-8") as f:
            return f.read().strip() or None
    except OSError:
        return None


def drift_note(session: str, repo: str) -> str | None:
    """The read-side check, packaged: report a move/rewrite since this session last looked,
    and remember where the repo stands now. Returns None when there is nothing to say."""
    verdict = lock.drift(repo, last_seen_head(session, repo))
    remember_head(session, repo, verdict.get("head_commit"))
    if verdict["status"] in ("moved", "rewritten"):
        return verdict["message"]
    return None


# --- the words a refusal and a takeover owe the next session -------------------

def format_held(verdict: dict) -> str:
    lk = verdict["lock"]
    return (
        f"REPO LOCKED — another agent session is writing to this working copy.\n"
        f"  repo    : {verdict['repo']}\n"
        f"  holder  : session {lk['session']}"
        f"{' (' + lk['intent'] + ')' if lk.get('intent') else ''}\n"
        f"  frees in: ~{int(verdict['expires_in'])}s\n"
        f"  base    : {(lk.get('base_commit') or '?')[:12]}\n\n"
        f"Do not force your way in — you would be editing a tree someone else is "
        f"mid-change on.\nIf this work is blocking, wait for the lease to lapse and retry. "
        f"If it is not, file an issue with what you were about to do and move on."
    )


def format_handoff(verdict: dict) -> str:
    h = verdict["handoff"]
    note = [f"Took over the lock on {verdict['repo']} ({h['reason']})."]
    if h.get("history_rewritten"):
        note.append(f"WARNING: history was REWRITTEN — the previous holder's base commit "
                    f"{(h.get('base_commit') or '?')[:12]} no longer exists. Re-read before "
                    f"you act on anything you remember about this repo.")
    elif h.get("commits_since"):
        note.append(f"{len(h['commits_since'])} commit(s) landed since they started:")
        note += [f"  {c}" for c in h["commits_since"][:10]]
    if h.get("uncommitted"):
        note.append(f"They left {len(h['uncommitted'])} uncommitted change(s) in the tree — "
                    f"review before writing:")
        note += [f"  {c}" for c in h["uncommitted"][:10]]
    return "\n".join(note)


def gate(repo: str, session: str, intent: str) -> tuple[str | None, list[str]]:
    """Acquire-or-renew ahead of a write: SPEC.md §7.1, harness-independent.

    Returns (denial, notes): `denial` is the refusal text when a live holder is in the way
    (the adapter turns it into its harness's block), else None; `notes` are the messages the
    session should see anyway — the takeover handoff and the commit warning.
    """
    verdict = lock.acquire(repo, session, pid=0, lease_seconds=LEASE_SECONDS, intent=intent)
    if verdict["status"] == "held":
        return format_held(verdict), []

    notes = []
    if verdict["status"] == "acquired" and verdict.get("handoff"):
        notes.append(format_handoff(verdict))
    warn = lock.needs_commit_warning(repo, session)
    if warn:
        notes.append(warn["message"])
    remember_head(session, repo, env.git_head(repo))
    return None, notes

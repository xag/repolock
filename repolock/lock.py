"""A mutex over a local working copy, for the several agent sessions that share one.

Git assumes one working tree per developer. On a machine running parallel AI agent sessions
that assumption is false: several sessions edit the same checkout, and nothing in git or its
hosting cares. This is the missing lock. The on-disk convention it implements is SPEC.md, in
this repository — this module is the reference implementation, not the definition.

The shape, in one breath: a lock is **short-lived** and taken right before writing; it carries a
**lease** whose duration the acquirer declares; the lease is renewed by *activity* (a harness
hook renews on every tool call — a tool call IS the activity, so no daemon exists to supervise);
it is **anchored to the commit** it was taken at, so the next writer inherits a diff rather than a
surprise; and it is **released explicitly, only against a clean tree**.

Two distinct failures are in scope, and they want different halves of this:
  - two writers colliding      → the lock (mutual exclusion)
  - a *stale reader*           → the commit anchor + `drift()`  (the incident that started
                                 this, 2026-07-12: history was rebased under a session that
                                 held no lock and was merely reasoning about commits that had
                                 moved)

Everything nondeterministic — clock, PID liveness, lockfile, git — is in repolock/env.py. This
module decides; that module touches the world.
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass, field

from repolock import env

DEFAULT_LEASE_SECONDS = 900        # 15 min: must comfortably outlast the longest single tool call
MAX_LEASE_SECONDS = 4 * 3600
WARN_BEFORE_SECONDS = 120          # "your lease is nearly up and you still haven't committed"


@dataclass
class Lock:
    """The lock record, as it sits on disk. Also the handoff note to whoever comes next."""
    repo: str
    session: str
    pid: int
    intent: str
    acquired_at: float
    renewed_at: float
    expires_at: float
    lease_seconds: float
    base_commit: str | None = None
    idle_since: float | None = None       # set when the holder went idle with a dirty tree
    dirty_at_idle: list[str] = field(default_factory=list)

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, sort_keys=True)

    @staticmethod
    def from_json(text: str) -> "Lock | None":
        try:
            data = json.loads(text)
            return Lock(**{k: v for k, v in data.items() if k in Lock.__dataclass_fields__})
        except (json.JSONDecodeError, TypeError, ValueError):
            return None   # a corrupt lockfile is not a held lock; it's garbage to be overwritten


# --- reading the world --------------------------------------------------------

def _load(repo: str) -> Lock | None:
    text = env.read_record(repo)
    return Lock.from_json(text) if text else None


def _lapsed(lock: Lock, now: float) -> bool:
    return now >= lock.expires_at


def _live(lock: Lock, now: float) -> bool:
    """A lock binds only while its lease is unexpired AND its holder still exists.

    Both, deliberately. The lease alone would let a crashed session block others until it ran
    out; PID liveness alone would let an idle session (one that stopped to ask its human a
    question and went to lunch) hold the repo for hours. Each covers the other's blind spot.
    """
    alive = env.pid_alive(lock.pid)      # bound, not short-circuited: the tape must carry it
    return not _lapsed(lock, now) and alive


def _may_release(dirty: list[str], force: bool) -> bool:
    """"Commit fast", as a rule rather than an aspiration: you may only hand back a tree the next
    session can safely walk into. Force is the deliberate, asked-for override."""
    return bool(force) or not dirty


def _why_dead(lock: Lock, now: float) -> str:
    if _lapsed(lock, now):
        return "lease lapsed"
    return "holder process is gone"


# --- the handoff --------------------------------------------------------------

def _handoff(repo: str, prev: Lock, now: float) -> dict:
    """What the next writer needs to know about the tree it is inheriting.

    This is the half of the design that addresses the stale reader. The predecessor's base
    commit is the anchor: if it no longer exists, history was rewritten (rebase, amend,
    force-push) and the incoming session must not trust anything it remembers about this repo.
    """
    head = env.git_head(repo)
    base = prev.base_commit
    dirty = env.git_dirty(repo)
    rewritten = bool(base) and not env.git_commit_exists(repo, base)
    return {
        "previous_session": prev.session,
        "previous_intent": prev.intent,
        "reason": _why_dead(prev, now),
        "idle_since": prev.idle_since,
        "base_commit": base,
        "head_commit": head,
        "history_rewritten": rewritten,
        "commits_since": [] if rewritten else env.git_log_between(repo, base or "", head or ""),
        "uncommitted": dirty,
    }


# --- the operations -----------------------------------------------------------

def acquire(repo: str, session: str, pid: int, lease_seconds: float = DEFAULT_LEASE_SECONDS,
            intent: str = "") -> dict:
    """Take the lock, or explain who has it.

    Outcomes: `acquired` (it was free, or the previous holder was dead/lapsed — then `handoff`
    describes what changed), `renewed` (we already held it; acquire is reentrant so a session
    never deadlocks against itself), or `held` (someone live has it — the caller decides
    whether to wait).
    """
    repo = env.canonical(repo)
    lease = max(1.0, min(float(lease_seconds), MAX_LEASE_SECONDS))
    now = env.now()
    cur = _load(repo)

    # Bound explicitly, not inlined into the branches: these two are the entire exclusion
    # decision, and the invariants in repolock/invariants.py read them straight out of the
    # trace. A claim you cannot check from the tape is a claim you are only hoping is true.
    prior_session = cur.session if cur else None
    prior_live = _live(cur, now) if cur else False

    if cur and prior_session == session and prior_live:
        return renew(repo, session, lease, intent=intent)

    if cur and prior_live:
        return {"status": "held", "repo": repo, "lock": asdict(cur),
                "expires_in": round(cur.expires_at - now, 1),
                "message": (f"{repo} is locked by session {cur.session} (pid {cur.pid})"
                            f"{f' — {cur.intent}' if cur.intent else ''}. "
                            f"Lease expires in {round(cur.expires_at - now)}s.")}

    handoff = _handoff(repo, cur, now) if cur else None
    head = env.git_head(repo)                # bound: the anchor invariant reads it off the tape
    lock = Lock(repo=repo, session=session, pid=pid, intent=intent,
                acquired_at=now, renewed_at=now, expires_at=now + lease, lease_seconds=lease,
                base_commit=head)
    wrote_path = env.write_record(repo, lock.to_json())   # bound: the grant, on the tape
    return {"status": "acquired", "repo": repo, "lock": asdict(lock), "handoff": handoff,
            "lockfile": wrote_path}


def renew(repo: str, session: str, lease_seconds: float = DEFAULT_LEASE_SECONDS,
          intent: str = "") -> dict:
    """Extend the lease. Called on every tool call by the harness hook — that is what makes
    renewal key on *activity* rather than on mere process liveness, and it is why an idle session
    lets go on its own without anything having to notice that it went idle.

    It also refreshes the INTENT, which is not cosmetic. The intent is what a refused session reads
    to decide whether to wait ten seconds or go and do something else, and a stale one actively
    misleads: a holder that took the lock to edit a file an hour ago, and is now half way through a
    test run, would otherwise still be advertised as editing that file.
    """
    repo = env.canonical(repo)
    now = env.now()
    cur = _load(repo)
    if not cur:
        return {"status": "unlocked", "repo": repo, "message": "nothing to renew"}
    if cur.session != session:
        return {"status": "held", "repo": repo, "lock": asdict(cur),
                "message": f"not yours to renew — session {cur.session} holds {repo}"}

    lease = max(1.0, min(float(lease_seconds), MAX_LEASE_SECONDS))
    cur.renewed_at = now
    cur.expires_at = now + lease
    cur.lease_seconds = lease
    if intent:
        cur.intent = intent
    cur.idle_since = None            # renewing IS activity; whatever idleness we noted is over
    cur.dirty_at_idle = []
    wrote_path = env.write_record(repo, cur.to_json())
    return {"status": "renewed", "repo": repo, "lock": asdict(cur),
            "expires_in": round(cur.expires_at - now, 1), "lockfile": wrote_path}


def release(repo: str, session: str, force: bool = False) -> dict:
    """Give the lock back. Refuses a dirty tree.

    That refusal is the whole of "commit fast", made checkable: releasing with uncommitted edits
    would hand the next session a checkout full of someone else's half-finished work, which is
    strictly worse than making it wait. Commit (or stash), then release.

    Releasing a lock nobody holds is success, not an error — release is idempotent so that a
    hook, a tool call and a crash-recovery path can all call it without coordinating.
    """
    repo = env.canonical(repo)
    cur = _load(repo)
    if not cur:
        return {"status": "released", "repo": repo, "message": "was not locked"}

    now = env.now()
    if cur.session != session and not force:
        if _live(cur, now):
            return {"status": "denied", "repo": repo, "lock": asdict(cur),
                    "message": f"{repo} is held by session {cur.session}, not you"}
        # A dead holder's lock is not ours to tidy away silently: acquire() steals it *with* a
        # handoff, which is the path that shows the next writer what it is inheriting.
        return {"status": "denied", "repo": repo, "lock": asdict(cur),
                "message": (f"{repo} is held by session {cur.session} ({_why_dead(cur, now)}). "
                            "Call acquire to take it over and see the handoff, or force.")}

    dirty = env.git_dirty(repo)
    if not _may_release(dirty, force):
        return {"status": "dirty", "repo": repo, "uncommitted": dirty,
                "message": (f"Refusing to release {repo} with {len(dirty)} uncommitted "
                            "change(s) — commit or stash first, then release. "
                            "(Handing over a dirty tree is worse than making the next "
                            "session wait.)")}

    removed = env.remove_record(repo)             # bound: the release, on the tape
    return {"status": "released", "repo": repo, "base_commit": cur.base_commit,
            "head_commit": env.git_head(repo), "forced": bool(force), "freed": removed}


def go_idle(repo: str, session: str) -> dict:
    """The holder is handing control back to its human (the harness's stop event). Decide what
    that means.

    Clean tree → release. Nothing is in flight, so holding would be pure obstruction, and a
    session waiting on a human at lunch must not starve every other session.

    Dirty tree → do NOT release; there are half-finished edits in the checkout and the next
    writer would walk straight into them. Mark the lock idle and let the declared lease run out
    on its own schedule. When it lapses, acquire()'s handoff tells the next session exactly what
    it is inheriting — *lapsed, owner idle since T, these files uncommitted* — instead of letting
    it discover that the hard way.
    """
    repo = env.canonical(repo)
    cur = _load(repo)
    if not cur or cur.session != session:
        return {"status": "noop", "repo": repo}

    dirty = env.git_dirty(repo)
    if not dirty:
        removed = env.remove_record(repo)
        return {"status": "released", "repo": repo, "reason": "idle with a clean tree",
                "freed": removed}

    now = env.now()
    cur.idle_since = now
    cur.dirty_at_idle = dirty
    wrote_path = env.write_record(repo, cur.to_json())
    return {"status": "idle_dirty", "repo": repo, "uncommitted": dirty, "lockfile": wrote_path,
            "expires_in": round(cur.expires_at - now, 1),
            "message": (f"Went idle with {len(dirty)} uncommitted change(s); holding {repo} "
                        f"until the lease lapses in {round(cur.expires_at - now)}s. "
                        "Don't go idle dirty — commit before asking.")}


def status(repo: str) -> dict:
    """Who holds this repo, since when, on what base, and for how much longer.

    Answers "should I wait?" *without blocking*: a tool that slept until the lock freed would be
    killed by an MCP idle timeout (we watched one die at exactly 300s), so the wait happens
    client-side and this returns the bounded facts needed to decide whether waiting is even the
    right move.
    """
    repo = env.canonical(repo)
    now = env.now()
    cur = _load(repo)
    head = env.git_head(repo)
    if not cur:
        return {"status": "unlocked", "repo": repo, "head_commit": head,
                "dirty": env.git_dirty(repo)}

    live = _live(cur, now)
    return {
        "status": "locked" if live else "lapsed",
        "repo": repo,
        "lock": asdict(cur),
        "holder_alive": env.pid_alive(cur.pid),
        "expires_in": round(cur.expires_at - now, 1),
        "idle": cur.idle_since is not None,
        "head_commit": head,
        "dirty": env.git_dirty(repo),
        "takeable": not live,
        "reason": None if live else _why_dead(cur, now),
    }


def needs_commit_warning(repo: str, session: str) -> dict | None:
    """The repurposed wake-up call: not "renew or lose the lock" (an agent four minutes into a
    tool call cannot answer a knock — it is turn-based, not an event loop), but "your lease is
    nearly up and you still haven't committed", fired early enough that the idle boundary can't
    strand the work. Returns None when there is nothing to say."""
    repo = env.canonical(repo)
    cur = _load(repo)
    if not cur or cur.session != session:
        return None
    now = env.now()
    left = cur.expires_at - now
    if left > WARN_BEFORE_SECONDS:
        return None
    dirty = env.git_dirty(repo)
    if not dirty:
        return None
    return {"repo": repo, "expires_in": round(left, 1), "uncommitted": dirty,
            "message": (f"Lease on {repo} expires in {round(left)}s and {len(dirty)} change(s) "
                        "are still uncommitted. Commit now, or renew if you need longer.")}


MCP_MAX_WAIT_SECONDS = 240         # under MCP's hard idle timeout, which killed a call at exactly
                                   # 300s. It is a fact about the TRANSPORT, so it is enforced by the
                                   # MCP tool and not here: a background waiter (repolock.waitfor)
                                   # has no such ceiling and may legitimately sleep for hours.


def wait_until_free(repo: str, timeout_seconds: float, poll_seconds: float = 2.0) -> dict:
    """Block until this repo is takeable, or until `timeout_seconds` runs out. Returns either way.

    This exists because of a hole the pessimistic hold opened, and it is not a nicety. A refused
    session cannot wait by itself: waiting means running `sleep`, `sleep` is a shell, and a shell
    takes the lock — so the one thing a blocked session most needs to do is the one thing it is
    blocked from doing. That is xag/repolock#4's cruellest detail ("`sleep` was also a write")
    returning through the new door, and a lock that refuses you and then refuses to let you wait is
    not a lock, it is a wall.

    An MCP tool is the escape, because the hook does not gate MCP tools — it is how #4 was reported
    at all, from a session that could not run a shell. The timeout is capped below MCP's idle
    timeout so this returns an answer rather than dying silently and looking like a server hang.
    """
    repo = env.canonical(repo)
    deadline = env.now() + max(0.0, float(timeout_seconds))
    while True:
        cur = _load(repo)
        now = env.now()
        if not cur or not _live(cur, now):
            return {"status": "free", "repo": repo, "waited": True,
                    "message": f"{repo} is free — the next write takes it, with a handoff."}

        left = deadline - now
        if left <= 0:
            return {"status": "still_held", "repo": repo, "lock": asdict(cur),
                    "expires_in": round(cur.expires_at - now, 1),
                    "message": (f"Still held by session {cur.session} after the wait; its lease "
                                f"has {round(cur.expires_at - now)}s left (activity renews it). "
                                f"Wait again, or do something else and come back.")}

        # Sleep to the lease's own expiry when that is sooner than the next poll: there is nothing
        # to learn in between, and a lock that lapses is takeable the instant it does.
        env.sleep(min(poll_seconds, left, max(0.1, cur.expires_at - now + 0.2)))


def fingerprint(repo: str) -> str:
    """What the working copy looks like right now, in one string.

    This is the question that replaced the guess. v0.1 asked "does this command look like a write?"
    — of a shell command, whose effect is not decidable from its text: `python codegen.py` writes,
    `python check.py` does not, and nothing short of running them can say which. Both directions
    failed in production (#4 blocked readers, #7 blocked a `print("a -> b")`, and `npm install`
    was never seen at all).

    So: no adapter classifies anything. It takes this before a tool runs and after, and a write is
    a fingerprint that MOVED. That is an observation, not a prediction, and it cannot be wrong
    about what a command did.

    HEAD catches commits and rebases; the porcelain catches added, removed and staged files; the
    stat of each dirty path catches a re-edit of a file that was already dirty (its status line does
    not move, but its bytes do).

    What it deliberately does not see: files git ignores. `node_modules` churning is not a change to
    the working copy in any sense the next session cares about, and the lock exists to protect what
    git tracks.
    """
    repo = env.canonical(repo)
    parts = [env.git_head(repo) or "-"]
    for line in env.git_dirty(repo):
        parts.append(line)
        path = line[3:].strip().strip('"').split(" -> ")[-1]   # porcelain: XY <path>[ -> <new>]
        parts.append(str(env.file_stat(os.path.join(repo, path))))
    return hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()


def drift(repo: str, seen_head: str | None) -> dict:
    """The read-side check, and the cheapest thing in this file.

    No lock is involved. A session that only *read* a repo still holds a picture of it, and that
    picture goes stale the moment another session rebases — which is exactly the damage in the
    founding incident: not a corrupted file, but a session confidently reasoning about commits
    that no longer existed. Compare the HEAD a session last saw against the HEAD that is there
    now.
    """
    repo = env.canonical(repo)
    head = env.git_head(repo)
    if not seen_head or not head or seen_head == head:
        return {"status": "current", "repo": repo, "head_commit": head}

    gone = not env.git_commit_exists(repo, seen_head)
    return {
        "status": "rewritten" if gone else "moved",
        "repo": repo,
        "seen_head": seen_head,
        "head_commit": head,
        "commits_since": [] if gone else env.git_log_between(repo, seen_head, head),
        "message": (
            f"History was REWRITTEN under you: the commit you last saw ({seen_head[:7]}) no "
            f"longer exists; HEAD is now {head[:7]}. Anything you remember about this repo's "
            "commits is suspect — re-read before acting."
            if gone else
            f"{repo} moved from {seen_head[:7]} to {head[:7]} since you last looked."),
    }

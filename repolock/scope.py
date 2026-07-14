"""Negotiated scopes — SPEC-v2. Pure logic over `env`; every effect is on the boundary and on tape.

**This is a TRIAL (SPEC-v2 §11).** The claim it tests is not about code, it is about behaviour:
*agents will contain their work once containment is visible and rewarded.* It cannot be settled by
argument, and it cannot be settled from v1's tapes either — those record agents who were never
offered the deal, and reasoning from them is the Lucas critique. So it is run, with the recorder on,
and §11b's falsifiers decide.

The one property that makes it safe to run: **silence is `**`**. A session that never declares holds
everything, conflicts with everyone, and therefore behaves *exactly* as it does under v1. Nothing
that ignores this file can be hurt by it, and the way back is one line of config.

The turn v2 makes, in a sentence: v1 prevents a collision AT THE WRITE, which is why MCP is a hole
(the call declares nothing, and the channel carries the off switch, so it cannot be gated — §7c). v2
prevents it AT THE RESERVATION: a write inside my scope cannot collide with anyone, because nobody
else is permitted there. The ungated channel stays ungated and the hole still closes.
"""

from __future__ import annotations

import json

from repolock import env

LEASE_SECONDS = 900
EVERYTHING = "**"


# --- resources and overlap (SPEC-v2 §1) ---------------------------------------------------------
#
# Deliberately a SMALL grammar. General glob-vs-glob intersection is a research problem, and a scope
# system whose overlap test is subtly wrong is worse than no scope system at all: it would hand two
# agents the same region and tell them both they were alone. So the resources are the ones whose
# overlap is decidable by inspection, and anything else is rejected at declare() rather than guessed
# at here.
#
#   **              everything, and the DEFAULT. Overlaps every resource, including named ones.
#   src/api/**      a directory subtree (prefix)
#   src/api/x.py    one file
#   git:index       a named resource — the staging area. See §1a: the agent that commits must take
#                   this, and it is what stops `git add -A` sweeping another agent's half-done work
#                   into your commit. That failure is the founding incident of this library, and
#                   path scopes ALONE hand it straight back.
#   git:HEAD        commits, rebases, checkouts
#   port:3000       a dev server, a debugger — anything a second agent would fight over


def _norm(resource: str) -> str:
    r = (resource or "").strip().replace("\\", "/").lstrip("./")
    while "//" in r:
        r = r.replace("//", "/")
    return r


def is_named(resource: str) -> bool:
    """`git:index`, `port:3000` — a resource that is not a path. Overlap is equality."""
    return ":" in resource.split("/")[0]


def _prefix(resource: str) -> str | None:
    """The directory a subtree resource covers, or None if it is not a subtree."""
    if resource.endswith("/**"):
        return resource[:-3].rstrip("/") + "/"
    if resource.endswith("/"):
        return resource
    return None


def bad_resource(resource: str) -> str | None:
    """Why this resource cannot be used, or None if it is fine. Rejecting is the honest move: a
    resource whose overlap we cannot compute is a region we would hand to two agents at once."""
    r = _norm(resource)
    if not r:
        return "empty"
    if r == EVERYTHING or is_named(r) or _prefix(r):
        return None
    if "*" in r or "?" in r or "[" in r:
        return (f"{resource!r}: only `dir/**`, an exact file path, `**`, or a named resource "
                f"(git:index, port:3000) can be reserved — a general glob has no decidable overlap, "
                f"and a scope system that is unsure whether two regions touch is worse than none")
    return None


def overlaps(a: str, b: str) -> bool:
    """Do these two resources touch? The whole protocol rests on this function being right."""
    a, b = _norm(a), _norm(b)
    if a == EVERYTHING or b == EVERYTHING:
        return True                            # silence is `**`, and `**` is v1
    if a == b:
        return True
    if is_named(a) or is_named(b):
        return False                           # named resources are equal-or-disjoint, nothing else
    pa, pb = _prefix(a), _prefix(b)
    if pa and pb:
        return pa.startswith(pb) or pb.startswith(pa)      # nested subtrees touch
    if pa:
        return b.startswith(pa)                            # a file inside a subtree
    if pb:
        return a.startswith(pb)
    return False                                           # two different files


def conflicts(mine: list[str], theirs: list[str]) -> list[tuple[str, str]]:
    return [(m, t) for m in mine for t in theirs if overlaps(m, t)]


def covers(scope: list[str], path: str) -> bool:
    """Is this working-copy path inside this scope? Used to gate a DECLARED write (§6)."""
    return any(overlaps(r, _norm(path)) for r in scope)


# --- the claims ---------------------------------------------------------------------------------

def _live(repo: str, now: float) -> list[dict]:
    # `records` is bound to a local ON PURPOSE, and it is not a style choice. The oracle judges from
    # the boundary's own answer, and it reads that answer off the tape as a LOCAL BINDING
    # (invariants._touches / scopes_never_overlap, via t.trace.values). Inline this into the `for`
    # and the claims that were actually on disk never reach the tape — and the invariant that is
    # supposed to catch two agents being handed the same region goes vacuously green, which is the
    # single most dangerous state this library can be in.
    records = env.read_claims(repo)
    out = []
    for text in records:
        try:
            claim = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            continue                           # a torn claim is no claim (SPEC §2)
        if claim.get("expires_at", 0) > now and claim.get("scope"):
            out.append(claim)
    return out


def live(repo: str) -> list[dict]:
    """Every claim that still binds. A lapsed claim is nobody's: leases are the backstop here for
    the same reason as in v1 — a crashed agent must not hold a region for ever."""
    return _live(env.canonical(repo), env.now())


def mine(repo: str, session: str) -> dict | None:
    return next((c for c in live(repo) if c["session"] == session), None)


def scope_of(repo: str, session: str) -> list[str]:
    """**The default is EVERYTHING, and that is the load-bearing line in this file.** An agent that
    has not declared holds the whole checkout, conflicts with every other scope, and is therefore
    excluded from a repo someone else is working — which is v1, exactly. v1 is the degenerate case of
    v2 (§4), so a harness that has never heard of scopes loses nothing and breaks nothing."""
    claim = mine(repo, session)
    return claim["scope"] if claim else [EVERYTHING]


def declared(repo: str, session: str) -> bool:
    return mine(repo, session) is not None


def _write(repo: str, session: str, scope: list[str], intent: str, now: float,
           acquired_at: float | None = None) -> dict:
    claim = {
        "repo": repo, "session": session, "scope": sorted(set(scope)), "intent": intent,
        "acquired_at": acquired_at or now, "renewed_at": now,
        "expires_at": now + LEASE_SECONDS, "lease_seconds": LEASE_SECONDS,
        "base_commit": env.git_head(repo),
    }
    env.write_claim(repo, session, json.dumps(claim, indent=2, sort_keys=True))
    return claim


def declare(repo: str, session: str, scope: list[str], intent: str = "") -> dict:
    """Reserve a scope. **All-or-nothing** — granted entire, or not at all (§3).

    Not a preference: it is conservative two-phase locking, and it is what keeps the HAPPY path free
    of deadlock. Grant it piecemeal and you have incremental acquisition, which is where the cycle
    lives (A holds api/**, wants git:index; B holds git:index, wants api/**). `extend` is the one
    place incremental acquisition survives, and it never blocks — see §5.
    """
    repo = env.canonical(repo)
    now = env.now()

    for r in scope:
        if why := bad_resource(r):
            return {"status": "rejected", "repo": repo, "reason": why}

    scope = [_norm(r) for r in scope]
    others = [c for c in _live(repo, now) if c["session"] != session]
    clash = [c for c in others if conflicts(scope, c["scope"])]
    if clash:
        return {"status": "conflict", "repo": repo, "scope": scope,
                "conflicts": [{"session": c["session"], "scope": c["scope"],
                               "intent": c.get("intent") or "",
                               "held_for": int(now - c.get("acquired_at", now))} for c in clash],
                "free_hint": _free_hint(repo, others)}

    was = mine(repo, session)
    claim = _write(repo, session, scope, intent, now,
                   acquired_at=was["acquired_at"] if was else None)
    return {"status": "granted", "repo": repo, "claim": claim}


def extend(repo: str, session: str, add: list[str], intent: str = "") -> dict:
    """Widen a scope you already hold. **The genuinely hard operation** (§5).

    An agent discovers mid-task that it must touch one more module. It cannot release and re-declare
    — it is holding uncommitted work, and releasing a dirty tree is refused (v1 §5). So this IS
    incremental acquisition, and incremental acquisition is where deadlock lives.

    v2 does not answer that with a wait-for graph. It answers it by **never blocking**: this returns
    `granted` or `conflict`, immediately, and the agent negotiates (`please_narrow`) or commits and
    re-declares from a clean tree. An agent that SPINS here, waiting for the other to yield while
    holding what the other wants, is the one shape of deadlock v2 admits — so it must not.
    """
    claim = mine(repo, session)
    if not claim:
        return declare(repo, session, add, intent)          # nothing held yet: this is a declare
    return declare(repo, session, list(claim["scope"]) + list(add), intent or claim.get("intent", ""))


def release(repo: str, session: str, drop: list[str] | None = None) -> dict:
    """Let go — of the whole scope, or of part of it (narrowing, which is what `please_narrow` asks
    for). Dropping everything removes the claim, and the agent falls back to the `**` default."""
    repo = env.canonical(repo)
    claim = mine(repo, session)
    if not claim:
        return {"status": "ok", "repo": repo, "scope": []}

    keep = [r for r in claim["scope"] if r not in {_norm(d) for d in (drop or [])}] if drop else []
    if not keep:
        env.remove_claim(repo, session)
        return {"status": "ok", "repo": repo, "scope": []}

    return {"status": "ok", "repo": repo,
            "scope": _write(repo, session, keep, claim.get("intent", ""), env.now(),
                            claim["acquired_at"])["scope"]}


def renew(repo: str, session: str) -> None:
    """Activity renews the lease, exactly as in v1 §3 — a tool call IS the activity, and an agent
    that has gone home stops renewing and lets go on its own."""
    claim = mine(repo, session)
    if claim:
        _write(env.canonical(repo), session, claim["scope"], claim.get("intent", ""), env.now(),
               claim["acquired_at"])


def _free_hint(repo: str, others: list[dict]) -> list[str]:
    """Top-level directories nobody has claimed. A conflict must be an ANSWER, not a refusal (§2):
    'that region is taken' leaves an agent stuck; 'that region is taken, these are free' does not."""
    taken = [r for c in others for r in c["scope"]]
    if any(_norm(r) == EVERYTHING for r in taken):
        return []
    free = []
    for entry in sorted(env.git_tracked_dirs(repo)):
        if not any(overlaps(f"{entry}/**", t) for t in taken):
            free.append(f"{entry}/**")
    return free[:12]


# --- the witness (SPEC-v2 §7) -------------------------------------------------------------------

def violations(repo: str, session: str, written: list[str]) -> list[dict]:
    """Paths this agent wrote OUTSIDE its own scope, and whose region another agent had reserved.

    This is what a shell or an MCP call gets instead of a gate: the target of those is not declared
    and v1 §7a is the standing proof it cannot be recovered from the text, so the write is WITNESSED
    rather than prevented. §7a is explicit that this is a real loss, and it is the trade the trial is
    testing.

    A write outside your scope that lands in NOBODY's region is untidy, not dangerous — it is
    reported to you (you evidently meant to declare it) but it is not a violation against anyone.
    """
    repo = env.canonical(repo)
    my_scope = scope_of(repo, session)
    others = [c for c in live(repo) if c["session"] != session]

    out = []
    for path in written:
        if covers(my_scope, path):
            continue
        for c in others:
            if covers(c["scope"], path):
                out.append({"path": path, "victim": c["session"], "scope": c["scope"],
                            "intent": c.get("intent") or ""})
                break
    return out


def stray(repo: str, session: str, written: list[str]) -> list[str]:
    """Wrote outside your own scope, into nobody's region. Not a violation — a missing declaration."""
    repo = env.canonical(repo)
    my_scope = scope_of(repo, session)
    others = [c for c in live(repo) if c["session"] != session]
    return [p for p in written
            if not covers(my_scope, p) and not any(covers(c["scope"], p) for c in others)]

"""The channel the agents can repurpose (ledger: a-channel-they-can-repurpose).

A claim says where an agent is working and what it is doing. This is for everything after that: an
estimate that slipped, a shape that changed, a question, an answer. Two sessions in one checkout are
not rivals — they are building one app — and knowing that a rewrite of the auth middleware is COMING
changes how you write the module next to it.

It earns its keep in the case the protocol does not prevent. NOT CLEAR is not a refusal: an agent
can weigh it and declare overlapping work anyway, and sometimes that is the right call. Two agents
who know they are close have every reason to be talkative, and to be listening. Before this, a
conflict ended with "file an issue for their part" — which is what you write when there is no
channel.

Three addresses, because the list of addressees has to be unambiguous:

    direct       one agent, by session id. The only kind that is PUSHED into a recipient.
    channel      one checkout. Everybody working that repo can read it; nobody is served it.
    broadcast    this machine. Same.

IT STAYS A TRANSPONDER, NOT A CHATTER PHONE. Chat traffic and the scope-violation alarm share one
delivery path, and an agent taught to skim the channel skims the alarm with it. So broadcast and
channel are PULL-ONLY: an agent that wants the room asks for it, and one that has been hit once
will ask.

Reading is not destructive. Each message is a file; each reader keeps a set of the ids it has seen.
So a message addressed to several agents is cleared for the one that read it and stands for the
rest — which a delete-on-read queue cannot do, and which also lets an agent re-read what it was told
(`mark=False`).

NO WAKE-UP, and it is stated here because it will otherwise be discovered as a hang: nothing can
push into a running turn. A reply lands when the other side next fires a hook, or never, if it has
finished. This carries "I will need api/** when you are done" perfectly well and cannot carry a
handshake.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid

from transponder import env, scope

TTL_SECONDS = 24 * 3600      # a letter nobody collected in a day is noise, not news
BROADCAST = "broadcast"


def _root() -> str:
    return os.path.join(os.path.dirname(env.lock_dir()), "mail")


def _key(address: str) -> str:
    return hashlib.sha256(address.encode()).hexdigest()[:16]


def _box(address: str) -> str:
    d = os.path.join(_root(), _key(address))
    os.makedirs(d, exist_ok=True)
    return d


def _seen_path(reader: str) -> str:
    d = os.path.join(_root(), "seen")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, f"{_key(reader)}.json")


def address_for(kind: str, repo: str = "", to: str = "") -> str:
    """One namespace for addresses, canonical like every other name here: a channel IS its
    checkout's canonical path, so two spellings of one repo cannot be two rooms."""
    if kind == "direct":
        return f"direct:{to}"
    if kind == "channel":
        return f"channel:{env.canonical(repo)}"
    return BROADCAST


def send(sender: str, body: str, kind: str = "channel", repo: str = "", to: str = "") -> dict:
    """Post a message. The SYSTEM stamps who sent it and what they hold — an assertion about the
    map ('I have released api/**') can then be checked against the map instead of believed."""
    if not body.strip():
        return {"status": "empty"}
    address = address_for(kind, repo, to)
    msg = {"id": uuid.uuid4().hex[:12], "at": env.now(), "from": sender, "kind": kind,
           "address": address, "from_scope": scope.scope_of(sender), "body": body.strip()}
    path = os.path.join(_box(address), f"{msg['id']}.json")
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(msg, f)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)                    # atomic, so a reader never sees half a letter
    except OSError:
        return {"status": "undeliverable"}
    return {"status": "sent", "id": msg["id"], "address": address}


def _load(address: str) -> list[dict]:
    box = _box(address)
    out = []
    for name in os.listdir(box):
        if not name.endswith(".json"):
            continue
        path = os.path.join(box, name)
        try:
            with open(path, encoding="utf-8") as f:
                msg = json.load(f)
        except (OSError, json.JSONDecodeError, ValueError):
            continue
        if env.now() - msg.get("at", 0) > TTL_SECONDS:
            try:
                os.remove(path)                  # retention, swept by whoever passes by
            except OSError:
                pass
            continue
        out.append(msg)
    return sorted(out, key=lambda m: m.get("at", 0))


def _seen(reader: str) -> set[str]:
    try:
        with open(_seen_path(reader), encoding="utf-8") as f:
            return set(json.load(f))
    except (OSError, json.JSONDecodeError, ValueError):
        return set()


def _mark(reader: str, ids: set[str]) -> None:
    keep = (_seen(reader) | ids)
    path = _seen_path(reader)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(sorted(keep), f)
        os.replace(tmp, path)
    except OSError:
        pass


def unread(reader: str, repo: str = "", kinds: tuple[str, ...] = ("direct",),
           mark: bool = True) -> list[dict]:
    """What this reader has not seen, oldest first.

    `kinds` is the whole difference between the courier and a feed. The hooks ask for ("direct",)
    only — those are addressed to this agent and are pushed. A pull asks for all three.
    """
    seen = _seen(reader)
    out = []
    for kind in kinds:
        if kind == "channel" and not repo:
            continue
        for msg in _load(address_for(kind, repo, reader)):
            if msg["id"] in seen or msg.get("from") == reader:
                continue                          # never serve an agent its own letter back
            out.append(msg)
    out.sort(key=lambda m: m.get("at", 0))
    if mark and out:
        _mark(reader, {m["id"] for m in out})
    return out


def render(msg: dict) -> str:
    """The `they hold:` clause is the anti-hearsay device: an agent can assert anything about the
    map in prose, so the map's own answer travels beside it. It is omitted for the system's own
    letters — transponder holds no scope, and printing "they hold: nothing" of the thing that KEEPS
    the map reads as a fault in the map."""
    ago = max(0, int(env.now() - msg.get("at", 0)))
    where = {"direct": "to you", "channel": "on this checkout", BROADCAST: "to everyone"}.get(
        msg.get("kind", ""), msg.get("kind", ""))
    sender = msg.get("from")
    if sender == "transponder":
        who = "from the transponder"
    else:
        held = ", ".join(msg.get("from_scope") or []) or "nothing on the map"
        who = f"from agent {sender} (they hold: {held})"
    return f"MESSAGE {where} {who} — {ago}s ago\n  {msg.get('body', '')}"

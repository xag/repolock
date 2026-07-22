# What actually happens, in order

Sequence diagrams for every path an agent can take through the transponder.

They are short now. An earlier version of this file had six diagrams, three of which drew a witness
that watched every write and named collisions after the fact — that is deleted, and why is
[SPEC §4](SPEC.md). What is left is a protocol you can hold in your head: **ask, declare, wait for
the green light, finish.**

---

## 1. The whole protocol

```mermaid
sequenceDiagram
    autonumber
    participant A as agent
    participant T as transponder
    A->>T: channel(repo, session_id, path='api/**')
    T-->>A: nobody is working api/** · 1 message waiting
    A->>T: declare_work(paths=['api/**'], doing='rewriting the rate limiter', minutes=15)
    T-->>A: GREEN LIGHT — api/** is yours. Go ahead.
    A-->>A: does the work
    A->>T: finish_work()
    T-->>A: off the map — somebody may have been waiting
```

`doing` and `minutes` carry what the map alone cannot: what is *coming*, and when to come back.

---

## 2. Somebody is already there

```mermaid
sequenceDiagram
    autonumber
    participant H as agent A (holder)
    participant T as transponder
    participant B as agent B
    B->>T: channel(repo, session_id, path='api/**')
    T-->>B: api/** OVERLAPS agent A — "rewriting the rate limiter" (finishes in ~11 min)
    B->>T: declare_work(paths=['api/**'], doing='a hotfix')
    T-->>B: NOT CLEAR · overlap named · FREE RIGHT NOW: web/**, docs/**
    T->>H: (direct) SOMEONE WANTS YOUR REGION — B asked
    Note over B: choose, and say which:
    alt take other work
        B->>T: declare_work(paths=['web/**'], doing='the hotfix, elsewhere')
        T-->>B: GREEN LIGHT
    else ask the human
        B-->>B: "A is mid-rebuild on api/** — wait, or go ahead anyway?"
    else wait
        B-->>B: launches `python -m transponder.wait --repo R --paths api/**` in the background
        H->>T: finish_work()
        Note over B: the waiter exits — a background task ending is the ONLY<br/>thing that can wake an agent
        B->>T: declare_work(...) again
    end
```

Nothing was blocked at any point. The conflicting claim is simply **not registered** — the map
never double-books — and B keeps working.

---

## 3. Talking while you work

`declare_work` already carries `doing`, so the map says what each agent is up to. The channel is for
everything that happens *after* that: an estimate that slipped, a shape that changed, a question.

It matters most in the case the protocol does not prevent. NOT CLEAR is not a refusal — an agent may
read it, weigh it, and declare overlapping work anyway, and sometimes that is the right call. When
two agents are knowingly working near each other, being talkative and listening is the whole of what
keeps it from going wrong.

```mermaid
sequenceDiagram
    autonumber
    participant A as agent A
    participant T as transponder
    participant B as agent B
    A->>T: send_message('replacing the auth middleware return type this hour')
    Note over T: posted to the checkout's channel — PULLED, never pushed
    B->>T: channel(repo, session_id)
    T-->>B: A: "replacing the auth middleware return type this hour"
    Note over B: writes its caller once, for the shape it is about to have,<br/>instead of writing it twice
    B->>T: send_message(to='A', 'noted — I will take web/** instead')
    Note over A: delivered when a hook next fires for A, or when A asks.<br/>No wake-up: write letters, not handshakes.
```

Direct messages are pushed; the room is pulled. Chat traffic and everything else share one delivery
path, and an agent trained to skim the channel skims everything with it.

---

## 4. Delivery, and what it does not promise

```mermaid
sequenceDiagram
    autonumber
    participant U as human
    participant M as the model
    participant H as hooks
    U->>M: a prompt
    H->>M: UserPromptSubmit — arrives BEFORE the model acts ✅
    M->>H: PreToolUse
    M-->>M: the tool runs
    H-->>M: anything waiting, beside the tool result
    Note over M,H: best effort. A hook fires when it fires, and no part<br/>of the protocol may depend on a note arriving.
```

This is why the protocol is **pull-first**: anything an agent must know before it acts, it has to
ask for. The one thing that reliably reaches a model ahead of its tools is the prompt-submit
channel, and the answer to a question it asked itself.

---

## 5. Going home

```mermaid
sequenceDiagram
    autonumber
    participant A as agent
    participant H as hooks (Stop)
    participant T as transponder
    A->>H: Stop — the turn is over
    alt tree is clean
        H->>T: release A's claims in every checkout it declared
        T-->>A: off the map
    else dirty, and A declared here
        H-->>A: exit 2, ONCE — commit / gitignore the artifact / stash the scrap
        Note over A,H: the one refusal in the library, and it blocks<br/>no other agent, ever
    end
```

Asked once and declined, the claims stay until the lease lapses: the work really is still there.

---

## Who is told what

| what happened | the agent is told | anyone else is told |
|---|---|---|
| walked onto a shared machine | the introduction and the four steps, once | — |
| asked the channel | who is here, and everything waiting | — |
| declared free ground | GREEN LIGHT | — |
| asked for a held region | who, what, when free, what is open | the holder: someone wants your region |
| said something on the channel | — | only when they call `channel()` |
| history moved underneath | the drift note | — |
| **wrote into someone's region** | **nothing** | **nothing** |

That last row is the design, stated plainly. There is no detection. It is the price of never
inventing an author the system cannot see, and the whole weight rests on the first four rows.

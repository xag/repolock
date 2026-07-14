# repolock v2 — negotiated scopes

> **Status: PROPOSAL.** Not implemented. [SPEC.md](SPEC.md) is what ships today and remains
> normative until this replaces it. Discussion: [#14](https://github.com/xag/repolock/issues/14).
>
> This document changes what the project *is*, so it is written to be argued with rather than
> merely read. Where it takes something away that v1 promised, it says so under that heading.

## 0. The turn

v1 is a **gate**. It decides, for each tool call, whether an agent may write a checkout, and it
refuses the ones that would collide. Everything hard about it follows from that: the undecidability
of reading a command (§7a), the pessimistic hold, the tickets a refused session needs in order to
wait at all, the off switch that must live where the gate cannot reach it.

v2 is a **channel**. Agents declare what they intend to write, see what everyone else has declared,
and stay out of each other's way. Nothing needs to be guessed, because the agent *says*. Nothing
needs to be refused, because there is nothing to collide with.

The failure this library exists to prevent was never malice, and reading the incidents back it was
never even carelessness. **It was ignorance.** Every one of them is an agent that did not know
another agent was there. You do not deter ignorance. You inform it — and a channel informs where a
gate can only obstruct.

### What that does NOT mean

It does not mean trusting agents and hoping. Two things keep this honest, and both are load-bearing:

- **Silence still costs everything (§4).** An agent that declares nothing is given scope `**`, which
  conflicts with every other scope — *exactly v1's whole-checkout mutex*. Concurrency is granted only
  to agents that opted in by declaring, and those are, by construction, the cooperating ones. **The
  guarantee v2 "gives up" was only ever held over agents who would have honoured the channel anyway.**
- **The witness stays (§7).** Every write is still observed, by fingerprint, exactly as in v1 §7b. A
  contract nobody checks is a wish. The hook stops being the gate and becomes the **witness** and the
  **courier**; it does not stop existing.

## 1. Scope: the namespace is the local filesystem

A **scope** is a set of **resources** an agent reserves before it works — and a resource is a
**canonical absolute filesystem path**: `realpath` + `normcase`, the same canonicalisation the v1
lockfile has always applied to repos. Exactly two forms exist:

| resource                     | reserves                                              |
|------------------------------|-------------------------------------------------------|
| `<canonical-path>`           | one file                                              |
| `<canonical-path>/**`        | a subtree                                             |

Agents spell paths relative to the checkout they name (`api/**` for `<repo>/api/**`; `**` alone is
the whole checkout — the degenerate case, spelt out), and the implementation canonicalises before
storing. Overlap is the **prefix relation** — decidable always — so a conflict MUST name the exact
**intersection**, and "come back narrower" is *computed*, never guessed.

One namespace, deliberately, and each exclusion is an argument, not an omission:

- **general globs** (`src/*.py`) have no decidable overlap. A scope system that is unsure whether
  two regions touch hands one region to two agents and tells each it is alone. Rejected at declare.
- **opaque names** (`port:3000`, `branch:main`) are contracts no witness can check — their
  violations surface as two dev servers fighting, never as a line on the tape. Not reservable until
  one earns its way in *with* a witness.
- **aliasing is dead by construction.** Case, symlinks, junctions, `..`, drive spellings all
  canonicalise to one string — so "whom do we inform?" is always answerable, point-to-point, from
  the claim store. The claim store is the routing table; overlap computes the addressees; nothing
  is ever broadcast.

Paths that **git ignores are not reservable and are never observed** — the same rule as v1's
fingerprint. Without it, `node_modules/`, `dist/` and `.pytest_cache/` make every scope conflict with
every other scope, every time, and the protocol dies of false positives in an afternoon.

### 1a. The index is a file, and that is the reason this works

Take source-path scopes alone. Agent A holds `api/**`, agent B holds `web/**`, and they edit
concurrently — which is the entire point. Then A runs `git add -A && git commit`.

It sweeps B's half-finished edits into A's commit. **That is the founding incident of this library**,
and source-path scopes alone hand it straight back: in v1 it is prevented only as a side effect of B
never being able to edit at all while A holds the checkout.

It cannot be prevented by inspection — knowing that a command is a whole-tree stage means reading the
command, and v1 §7a is the proof that this is not decidable.

The filesystem namespace dissolves it without adding anything: **the staging area was never anything
but a file.** `git:index` is `<repo>/.git/index`; `git:HEAD` is `<repo>/.git/HEAD`. **An agent that
intends to commit MUST reserve `<repo>/.git/index`** — an ordinary path, in the ordinary namespace,
with the ordinary overlap. Commits serialise. Nobody parsed a command line, and nobody had to invent
a second kind of resource to get there.

### 1b. …and the index MUST be held briefly, or v2 collapses back into v1

**Every writing session in the tapes moved HEAD — 13 out of 13 (§10.1).** Every writer commits. So
`.git/index` is not an occasional resource, it is one that *every* writer needs.

Which sets a hard constraint the obvious design would have got wrong: **the index MUST be reserved
immediately before a commit and released immediately after it.** It MUST NOT be held for the life of
a scope. If a session reserves the index when it declares, then every writer conflicts with every
other writer for its whole lifetime — which is the v1 whole-checkout mutex, rebuilt at greater cost
and with a worse name.

So a scope has resources of two lifetimes: **held** (the paths you are working) and **taken briefly**
(the index, at the moment you commit). An implementation that cannot express that difference cannot
express v2.

## 2. The protocol

Every step below is an MCP call. MCP is ungated by construction (v1 §7c), so **an agent can always
reach the channel, including one that is currently blocked, wedged, or being asked to give something
up.** That property is not a convenience here; it is what makes the channel a channel.

```
declare(repo, scope, intent)   -> granted | conflict(with: [{agent, scope, intent, since,
                                                             intersection}])
extend(repo, add)              -> granted | conflict(...)          # widening: see §5
release(repo, drop)            -> ok                               # narrowing, or letting go
scopes(repo)                   -> who holds what, and why
```

`intersection` is not decoration: it is the exact overlapping region, computable because the
namespace is canonical paths (§1), and it is what turns "denied" into "subtract exactly this and
carry on".

**`declare` is all-or-nothing.** An agent states its whole scope up front and is granted all of it or
none of it. This is conservative two-phase locking, and it is *meant* to be the reason v2 has no
deadlock: no incremental acquisition, therefore no cycle, therefore no wait-for graph, no detection,
no victim selection, and nothing to debug at 2am on a wedged laptop.

> **`extend` must be a first-class path, not an escape hatch (§5).** Under v1, sessions are wide and
> spread: a scope inferred from the first write is wrong for 92% of them (§10.1). That number is
> **evidence about v1, not v2** — agents spread because containment bought them nothing — so it must
> NOT be read as "agents cannot declare". But discovery is intrinsic to the work: even a disciplined
> agent, deliberately containing itself, will sometimes find it must touch one more module. So
> extension happens, incremental acquisition survives, and the deadlock-freedom above is a property
> of the *happy* path only. **Whether `extend` is the exception or the main road is the thing the
> trial (§11) has to measure** — and it decides how much of §5 has to be real.

**A conflict is an answer, not a refusal.** v1 tells a blocked agent *why* it is blocked and hands it
a way to wait. v2 tells it **who holds what**, so it can come back with a scope that fits and proceed
*immediately*:

```
CONFLICT — app/server/** is held by agent 8663de9b (adding the rate limiter, 4m).
Free right now: app/web/**, tests/**, docs/**.
Take a narrower scope and carry on, or ask them to narrow theirs (see §3).
```

Waiting becomes the fallback. In v1 it is the outcome.

## 3. The channel is duplex

An agent turn **cannot be interrupted**, and nothing can push into it. That is not a limitation of
this design; it is a fact about agent harnesses, and every delivery mechanism must be built out of
what remains. Two things remain, and between them they cover both states an agent can be in:

- **A working agent** is making tool calls, and the hook prints to stdout on every one of them. An
  inbound request rides its **next tool call**. No daemon, no push, no new machinery.
- **A parked or blocked agent** is making no tool calls, so there is nothing to ride. It launches a
  **listener** — the v1 waiter (#5, #10), generalised from *"wait until this lock frees"* to *"wait
  until something is addressed to me"*. The listener **exits** on a message, because **exiting is the
  only signal that wakes a harness**. The agent is woken, reads the message, and relaunches it.

So an agent that listens is reachable *promptly*, not merely eventually — it does not have to make a
tool call to hear you.

The listener is a shell command, and a blocked agent cannot run shells. So, exactly as in v1 §7.9,
**the channel MUST mint the listener as a one-time ticket**, allowed by byte-equality against a string
it wrote itself, one spelling per shell, with a test that runs it in a real one. Every word of
xag/repolock#10 applies here unchanged, and it will be re-learned the hard way by anyone who skips it.

### What may be pushed

```
please_narrow(repo, agent, scope, why)   # "I need web/**; you hold ** and haven't touched it"
```

The holder MAY comply, MAY counter-offer, MAY decline. **It is a request, not a preemption.** An
agent mid-edit in a region cannot be made to drop it — that would hand its half-finished tree to
someone else, which is the thing v1 §5 refuses and v2 has no reason to start doing.

## 4. Silence, and what it costs

An agent that has declared nothing holds `**` — every resource in the checkout.

That single rule is what makes v2 safe to adopt:

- an agent that has never heard of scopes behaves **exactly as it does under v1**, and is excluded
  from a checkout another agent is working, exactly as under v1;
- a legacy or third-party harness costs nothing and breaks nothing;
- **v1 is the degenerate case of v2** — the state in which every agent asks for everything.

And the equivalence MUST hold **in both directions**, because the two regimes coexist on one
machine. A live v1 lock on a checkout reads to scoped agents as a claim on `<checkout>/**`: it
refuses a `declare` over it, and it blocks a scoped agent's work inside it, exactly as a claim
would. The session that took the whole-checkout lock was *promised* the whole checkout, and a
reservation is not a priesthood the mutex stops applying to. (The first trial build implemented the
equivalence in one direction only — undeclared sessions were held out by claims, but scoped agents
sailed over v1 locks — and that is the founding incident with a reservation as the weapon.)

Migration is therefore not a flag day. It is agents learning to ask for less.

MCP is never gated for the undeclared either. §7c does not have a scope-shaped exception: the
teaching refusal an undeclared session receives applies to its declared writes and shells, never to
its MCP calls — **`declare_scope` is itself an MCP call**, and a refusal that names the way in must
not stand in front of the door. (Also shipped wrong in the first trial build, which is why it is
spelt out here.)

## 5. Widening, which is the one genuinely hard operation

`declare` is all-or-nothing and up front. Real work is not: an agent halfway through a task discovers
it must touch one more module.

It cannot simply release and re-declare — it is holding uncommitted work, and v1 §5 (which stands)
forbids releasing a dirty tree. So `extend` **is** incremental acquisition, and incremental
acquisition is where deadlock lives.

v2 does not solve this with a wait-for graph. It solves it with the channel:

- `extend` **never blocks**. It returns `granted`, or `conflict` naming the holder.
- On `conflict` the agent MAY `please_narrow` the holder and get on with something else, or commit
  what it has and re-`declare` from a clean tree.
- An agent MUST NOT sit and spin on `extend`. Two agents each blocked in `extend` on the other's
  region is a deadlock, and it is the only shape of deadlock v2 admits — so this is the rule that
  keeps the design's central claim true, and it MUST be tested rather than asserted.

## 6. What is still enforced

Not everything becomes advisory. The hook still knows, for free and without guessing, the target of
every **declared** write:

- `Edit` / `Write` / `NotebookEdit` carry a `file_path`. A write to a path **outside the writing
  agent's own scope MUST be refused**, before it happens, with the conflict message from §2. This is
  v1 §7.1 unchanged, with the scope taking the place of the checkout.
- The refusal is *useful* now, which it never was in v1: the agent is not told to wait, it is told to
  declare the region it evidently meant to write.

## 7. The witness, and what it cannot do

For a **shell** or an **MCP call**, the target is not declared, and v1 §7a is the standing proof that
it cannot be recovered from the text. So the fingerprint (v1 §7b) stays, and its job changes: it no
longer decides who takes a lock, it **witnesses who wrote what**.

- writes inside the writer's own scope: expected, correct, silent;
- writes **outside** it, into a region another agent reserved: a **violation**. It is detected, it is
  named (culprit, victim, paths, commit), it is loud, and it is **NOT PREVENTED**.

### 7a. This is a real loss, and v1 explicitly forbids it

v1 §7b, on detection-instead-of-exclusion for shells:

> *"a known-reachable hole in a mutex is not a residual risk, it is the absence of the mutex on that
> path. It was rejected. An adapter MUST NOT implement it."*

**v2 reverses that sentence for the shell path, and must own it.** Under v1 two agents cannot run
shells against one checkout at the same instant. Under v2, two agents with disjoint scopes can — and
nothing stops one of them writing into the other's region.

The case for reversing it:

- the hole is bounded to **out-of-scope** writes, where v1's MCP hole (§7c) is *every* MCP write, so
  the unsound surface **shrinks** rather than grows;
- a violation has a culprit, a victim, an alarm and a diff, where a v1 collision had none of those;
- the exclusion being traded away was **never machine-wide**. It binds only agents whose harness runs
  the hook — the very population that would honour a channel. A human in an editor, a `cron` job, a
  harness with no adapter: all of these already write freely, and v1's own non-goals admit it
  ("*Not sandboxing… the convention is for cooperating harnesses*").

The case against, which must not be waved through: it is a **weaker guarantee than the one we have**,
and this library's entire history is of weaker guarantees being discovered in production.

### 7b. What a violation MUST do

Detection with no consequence is a log line. On observing an out-of-scope write, an implementation
MUST:

1. **tell the violator, immediately**, on its next hook call: what it wrote, whose region it landed
   in, and that it must stop;
2. **tell the victim**, through the channel — this is what the duplex channel is *for*;
3. **refuse that agent's further declared writes** until it re-declares a scope that covers what it
   is evidently doing, or reverts. It cannot unwrite the bytes; it can be stopped from continuing;
4. where the violation is a **commit** (HEAD moved with out-of-scope paths in it), say so with
   the sha — a commit is the one violation that is cleanly **recoverable**, and the message MUST say
   `git revert`/`git reset` rather than leaving the agent to work it out.

## 8. The cooperation assumption, stated plainly so it can be attacked

**v2 assumes agents cooperate.** The assumption is not "agents are good". It is:

- they are driven by **one human**, who wants all of their work to survive;
- the failure mode observed in every incident in this repository is **ignorance, not defection** — an
  agent that did not know another was there;
- **there is no adversary to model.** An agent that stomps a scope is not defecting in a repeated
  game: it has no memory across sessions, no reputation, and no future to lose. Which is exactly why
  **deterrence cannot be the mechanism** — "they risk being stomped back" is not an argument, it is a
  hope, and it MUST NOT appear in the reasoning for this design.

What replaces deterrence is **information plus a witness**: an agent that can see the other scopes has
no reason to collide, and one that collides anyway is named immediately rather than discovered a week
later in a mangled rebase.

**If this assumption is wrong, v2 is wrong** — and the honest kill condition is §7b's alarm firing
regularly in tapes, from agents that had declared a scope and wrote outside it anyway. That is
mechanical, it is cheap, and it fires long before anyone loses a day's work.

## 9. What v1 keeps

Unchanged, and not up for renegotiation: the lockfile record and its atomicity; leases renewed by
activity; liveness (lease **and** holder); the commit anchor, the takeover handoff and the drift check
(§6 — it needs no scopes and catches what no scope can); the dirty-tree release refusal and the
handback (§5, §5a); fail-open-loudly; the off switch (§7 obligation 11); **and the ungated MCP channel
(§7c), on which the whole of v2's negotiation now rides.**

## 10. Open, and not to be closed by assertion

1. ~~**Scope granularity in practice.**~~ **ANSWERED FROM THE TAPES — and it moved the design.**

   Every session ever recorded on this machine (26 on real repos, 13 of them writers) was replayed
   and asked: what scope would you have had to declare, and would you have stayed inside it?

   | | |
   |---|---|
   | paths written per writing session | **median 20**, max 129 |
   | top-level dirs it had to reserve | **median 3**, max 15 |
   | sessions that wrote outside a scope inferred from their FIRST write | **92%** (308 out-of-scope writes) |
   | writing sessions that moved HEAD | **13 / 13 — every one** |
   | moments where two sessions were alive on one checkout at once | **2** |
   | …of those, genuinely disjoint (perfect foresight, no shared file or dir) | **2 — both of them** |

   **Most of this table cannot be used to judge v2, and saying otherwise was an error.** It measures
   agents *under v1* — agents with no way to see each other, no reason to contain themselves, and no
   cost for spreading. v2 changes the information they act on, so it changes the behaviour being
   measured. Predicting a new regime with a behavioural relationship estimated under the old one is
   the **Lucas critique**, and it is exactly what the first draft of this section did.

   **Dies with the regime — evidence about v1, not about v2:**

   - **the 92%.** Agents spread because narrowness bought them nothing and they could not see whom it
     would cost. An agent that can *see* what is free has a reason to stay inside it, and to file an
     issue splitting off the rest instead of wandering into it. v2's whole claim is that it removes
     the cause of this number.
   - **"contention is rare".** The tape records a world where a whole-checkout lock punishes
     concurrency. One-session-at-a-time is precisely the habit v1 trains. This is v1's *output*, not
     evidence about demand.
   - **"sessions are wide".** Width is what you get when narrowness is unrewarded.

   **Survives, because it is not about behaviour:**

   - **13 of 13 writing sessions committed.** Agents will still commit under v2 — this is a fact
     about working with git, not about the lock. `.git/index` is therefore needed by *every* writer,
     which is what forces §1b: take it briefly, never hold it for the life of a scope.
   - **both observed collisions were genuinely disjoint** — different *directories*, not merely
     different files. That is an observation of what happened, not a prediction: twice out of twice,
     v1 refused work it had no reason to refuse.

   **So the study yields one design constraint (§1b) and no verdict.** The question it was built to
   settle — *will agents contain their work when containment is rewarded and visible?* — is not
   answerable from tapes of agents who were never offered the deal. **It has to be tried.** §11.

   Reproduce: `studies/scope_study.py` / `scope_study2.py` against `~/.repolock/flight`.
2. **Who is an agent?** v1 keys on the harness session id, and subagents share their parent's
   (`hyp-subagents-share-the-session-id`). Scopes make that reentrancy sharper, not softer: two
   subagents of one session with disjoint scopes are two writers with one identity.
3. **Does the shell still take anything at all?** §7 says no. The alternative — a shell keeps taking
   the whole checkout, and only editors and MCP use scopes — preserves v1's hard exclusion and buys
   almost no concurrency, since agents run shells constantly. It should be written down as the
   rejected alternative it is, with this sentence as the reason.
4. **`please_narrow` and starvation.** A holder that always declines is a holder that starves
   everyone else, and v2 has no preemption by design (§3). Is the lease the only backstop?

## 11. The trial: v2 cannot be argued into existence, only run

The central claim — **agents will contain their work when containment is visible and rewarded** — is
not a fact about code. It is a fact about how agents behave *once they can see each other*, and no
amount of staring at v1's tapes will settle it (§10.1). It is also not a matter of taste: it is a
**hypothesis**, it is cheap to test, and it fails loudly if it is false.

So it gets tried, on this machine, with the recorder on.

### 11a. What makes the trial safe to run

- **Silence is `**` (§4).** Any session that does not declare behaves exactly as under v1. The trial
  therefore cannot be *worse* than today for anything that does not opt in.
- **The witness (§7) is what produces the evidence**, and it is already built: the fingerprint records
  every write on every hook call, and recording is on by default.
- **The off switch (v1 §7 obligation 11) is untouched.** A failing experiment on the write path of
  every session on the machine must be stoppable in one MCP call, from inside a wedged session.

### 11b. What is measured — and these are the falsifiers, not a dashboard

| the claim | what kills it, on a tape |
|---|---|
| **agents contain themselves** once they can see what is free | a session's writes land outside its declared scope at a rate comparable to v1's 92% — i.e. declaration changed nothing, and §7b's alarm becomes noise nobody reads |
| **declaration is roughly right up front** | `extend` is called on most tasks — in which case incremental acquisition is the norm, deadlock is the main surface (§3, §5), and conservative locking was the wrong foundation |
| **scopes buy real concurrency** | sessions that overlap in time still conflict on scope with the same frequency as they would have collided under v1 — the work genuinely overlaps, and no protocol can fix that |
| **negotiation converges** | `please_narrow` is declined, or ignored, in the ordinary case — the channel is then a suggestion box, and starvation (§10.4) is the real behaviour |
| **the exclusion trade (§7a) was worth it** | any out-of-scope write destroys work that was not recoverable by `git revert`. **One** of these is enough. It is the thing v1 never allowed, and the only outcome that says the trade itself was wrong rather than merely expensive |

The last row is the one that matters. Every other failure costs annoyance and can be measured for a
week. That one costs a human their work, and it is the reason the trial runs **on one machine, with
one human, who knows it is running** — not shipped to anyone else first.

### 11c. What "it worked" looks like

Not "no violations". Violations are *expected* — discovery is intrinsic, agents will misjudge, and
§7b exists precisely for that. It worked if:

- containment is the norm and violations are **rare and recovered**, rather than routine and ignored;
- sessions that could have run in parallel **did**, where v1 would have refused them;
- and no one lost work.

If it does not work, the way back is one line of config, because **v1 is the degenerate case of v2**
(§4). That is the property that makes this worth trying at all.

# The shared-checkout convention, v2

One developer, several AI agent sessions, one machine of shared checkouts. Git assumes a working
tree has one author; agent harnesses pretend that is still true. This convention is the missing
**information layer**: a claims map any agent on the machine can read and write, a channel they can
talk on, and a protocol for asking before writing — regardless of vendor. It has no enforcement and,
since v2.1, no detection either: the agreement happens before the work (§4).

This document is normative. The Python package in this repository is a reference implementation,
not the definition. **MUST/SHOULD/MAY** as in RFC 2119.

> **This replaces v1, which was a lock** — a mutex that refused tool calls it judged unsafe. It was
> removed because refusal cost more than it saved: genuine collisions were rare, while refusing
> blocked far more work than it protected, and a command's effect cannot be judged before it runs
> anyway. This version informs instead of refusing; §0 is the argument for that trade.

## 0. The model, in four sentences

An agent **asks** what is going on, **declares** where it will write and what it is doing, and
**waits for the green light that declaring returns**. The declarations form a map, and the map never
double-books a region. The **courier** delivers, opportunistically and without any guarantee, what
an agent cannot see from inside its own context. **Nothing is ever refused** — the failure this
convention prevents was never malice, it was an agent that did not know another agent was there.

**The agreement happens before the work, or it does not happen.** There is no detection: nothing
observes writes, and a collision is neither prevented nor reported. That is not an omission, it is
§4.

### 0a. Why cooperation is a sound foundation, not a hope

Every agent on the machine works for the same human, who wants all of their work to survive. There
is no adversary to model — and **deterrence is explicitly not the mechanism** (an agent has no
memory across sessions, no reputation, no future to lose; "they risk being stomped back" is a hope,
not an argument). The mechanism is *visibility before the fact*: an agent that
can see the other scopes has no reason to collide, and one that asks before it writes will not.

If this assumption is wrong, the convention is wrong, and there is no longer a backstop that will
tell you — see §4. The kill condition is now the one thing still observable: agents that were shown
the map and declared nothing.

## 1. The namespace is the local filesystem

A **scope** is a set of **resources**; a resource is a **canonical absolute path** — `realpath` +
`normcase`, forward slashes — in exactly two forms:

| resource               | reserves   |
|------------------------|------------|
| `<canonical-path>`     | one file   |
| `<canonical-path>/**`  | a subtree  |

Agents spell paths relative to the checkout they name (`api/**` for `<repo>/api/**`; `**` alone is
the whole checkout), and the implementation canonicalises before storing. **Overlap is the prefix
relation** — decidable always — so a conflict MUST name the exact **intersection**: "come back
narrower" is computed, never guessed.

One namespace, deliberately:

- **general globs** (`src/*.py`) have no decidable overlap and MUST be rejected at declaration. A
  scope system unsure whether two regions touch hands one region to two agents and tells each it
  is alone.
- **opaque names** (`port:3000`) name nothing the overlap relation can reason about, and MUST be
  rejected. Two spellings of one resource would read as disjoint, both be granted, and the
  collision would land in the world with the map reporting calm — and since §4 there is nothing
  downstream that would ever notice.
- **aliasing is dead by construction**: case, symlinks, junctions, `..` all canonicalise to one
  string, so "whom do we inform?" is always answerable point-to-point from the map. Nothing is
  ever broadcast.
- paths git ignores are not reservable and are never observed — without this, `node_modules/`
  makes every scope conflict with every other, and the convention dies of false positives.

### 1a. The index is a file, and that is why commits are safe

Agent A works `api/**`, agent B works `web/**`, both mid-edit — the whole point. A runs
`git add -A && git commit` and sweeps B's half-finished work into A's commit. **That is the
founding incident of this convention**, it cannot be prevented by inspection (knowing a command
stages the whole tree means reading the command, which is undecidable — the one v1 lesson nothing
here revisits), and source-path scopes alone hand it straight back.

The filesystem namespace dissolves it without adding anything: **the staging area was never
anything but a file.** An agent that intends to commit SHOULD reserve `<repo>/.git/index` — an
ordinary path with the ordinary overlap — immediately before the commit, and release it
immediately after. Held for the life of a scope it makes every writer conflict with every other,
which is the old mutex rebuilt; taken briefly, commits serialise by consent and nobody parsed
anything. Nothing backstops the agent that doesn't: a `git add -A` that sweeps a neighbour's
half-finished work into your commit is the founding incident of this convention, and it is now
prevented only by reserving the index — not detected afterwards (§4).

## 2. The claims map

- Claims live in one machine-global store (`$TRANSPONDER_DIR`'s parent, `claims/`), **one file per
  agent**, written atomically. Never a shared list: a list is read-modify-write, and a store that
  loses a claim under contention loses it exactly when it matters.
- A claim carries: `session` (the harness session id — the same one the hooks see), `scope`
  (canonical resources), `intent` (free text, refreshed on renewal — a stale intent misleads the
  agent reading the map to decide what to do), `acquired_at` / `renewed_at` / `expires_at` /
  `lease_seconds`.
- **Leases are the decay rate of information, not a hold.** Activity renews (a tool call IS the
  activity; nothing needs a daemon); an agent that crashed or wandered off simply fades from the
  map. Readers MUST treat an unparsable claim as no claim, and MUST ignore unknown fields.
- The map MUST NOT double-book: a `declare` or `extend` whose scope overlaps a live claim is not
  recorded, and the answer names the holder, the intent, the exact intersection, and what is free.
  **This refuses a map entry, never a tool call** — the agent's work is not blocked by anything,
  anywhere.
- `declare` is all-or-nothing (a scope granted entire or not at all); `extend` widens and never
  blocks; `release` narrows or clears. Since nothing ever waits on anything, deadlock does not
  exist in this convention — there is nothing to cycle on.

## 3. Asking, and the courier

**The protocol is pull-first, and that is the whole design.** An agent turn cannot be interrupted
and nothing can reliably push into it, so anything an agent must know before it acts, it has to ask
for. Four steps, and an adapter MUST teach all four:

1. **ask** — `channel(repo, session_id, path?)`: who is working here, narrowed to the path you mean
   to touch, plus everything waiting for you. Nothing is pushed reliably; asking is how you find out;
2. **declare** — `declare_work(repo, session_id, paths, doing, minutes)`: where you will write, what
   you are doing, roughly how long. **It returns a green light, or it does not**, and an agent waits
   for it before editing a shared checkout;
3. **not clear** — take different work, go back to the human, or wait. An agent SHOULD say which it
   chose, and waiting SHOULD be a background process that exits when the region frees, because a
   harness noticing a background task exit is the only thing that can wake an agent;
4. **finish** — `finish_work()` the moment the region stops being yours. A map that says you are
   working where you are not makes the next agent wait for nothing.

`doing` and `minutes` carry what the map alone cannot: what is *coming*, and when to come back.
`minutes` is advisory — liveness remains the lease, so an optimistic estimate cannot squat a region
past the point where anyone can tell whether the agent is alive.

The **courier** is what is left of push, and it promises nothing. When a hook happens to fire, it
hands over: the **introduction**, once per session — who is on this machine, and the four steps
above — and the **drift note** (§6). Delivery is best-effort by construction, and no part of this
convention may depend on a note arriving.

The courier MUST NOT promise a warning *before* a write. That was required here once, and built,
and the moment does not exist: a hook reaches an agent ahead of its tool only by refusing the call,
which §7 forbids, and context attached to a pre-tool hook is delivered beside the TOOL RESULT. The
only text that reliably reaches an agent before it acts is what it asked for, and the harness's
own prompt-submit channel.

## 4. There is no witness

Earlier drafts of this spec required one: fingerprint the checkout before a tool call and after,
and report anything that moved inside another agent's region. It is deleted, and the reason is not
cost.

**A fingerprint proves the tree MOVED. It cannot prove who moved it.** With one agent that
distinction never surfaces. With two — the only case this convention exists for — it is the normal
case: an agent appending to its own declared file, and a passer-by whose tool call merely lasted
longer than the gap between two of those appends, produce *the same picture from outside*. Built
and run, it named a reader as the author of writes it never made, four times in one afternoon, and
told the holder its work had been trampled by an agent that never wrote a byte.

Two attempts to bridge the gap were made and both were guesses wearing an observation's clothes:
grading by whether the region's owner had recently renewed ("they were awake, so probably them"),
and its complement ("nobody else was awake, so probably you"). Renewal proves an agent was awake.
It says nothing about who touched a file.

So an adapter MUST NOT report authorship it cannot establish. The only writes with an author are
those a harness *declares* — a tool whose input names the file it will write — and a convention
built on that alone is a convention that reports almost nothing. It is therefore built on the other
side of the moment instead: **the agreement, before the work.**

What replaces detection:

- an agent that suspects something changed under it **asks** (§3) and can message whoever it finds;
- the drift check (§6), which asks about the *reader's own* stale picture and never about anyone
  else's writes — the one observation here that was never wrong;
- the recording (flight-recorder), for the human afterwards.

**Knowingly given up:** a write into a declared region is lost work that neither party is told
about. The whole weight now rests on agents asking first, which is a behavioural bet stated in §0a
and no longer hedged by anything.

## 6. The drift check

Read-side, lock-free, and the one part of v1 that was never wrong: given the HEAD a session last
saw, report `current`, `moved` (with the commits between) or `rewritten` (the seen commit no
longer exists — everything the session remembers about this repo is suspect). Adapters SHOULD run
it at session start and on each return of control.

## 7. Adapter obligations

1. **never refuse a tool call.** The one permitted exit-2 is the Stop boundary (obligation 6);
2. **teach the four steps of §3** in the introduction, and expose them as tools an agent can call.
   The instruction is the mechanism now: there is no detection behind it to catch what it fails to
   convey;
3. deliver the courier's notes through the harness's own channel into the agent's context — and
   claim nothing about delivery. Best-effort is the contract;
4. key everything on facts, **never on the session's cwd**: a tool's own declared file path, or the
   checkouts on the map. An adapter MUST NOT infer which checkout a call is about from where the
   session happens to be sitting — that is a prediction of the same family as reading a command to
   guess what it writes (§7a), and it failed the same way, silently, for as long as it existed;
5. **report no authorship it cannot establish** (§4). An adapter MUST NOT tell an agent it wrote
   something, or tell a third party who wrote it, on the strength of a tree that changed while a
   call was running;
6. at the Stop boundary: release the session's claims, in every checkout it declared, against a clean tree; if
   the tree is dirty and the session was a participant there, it MAY block the stop **exactly
   once** to ask for commit / ignore / stash — three routes, because "commit your work" is the
   wrong instruction for an artifact and for a scrap. Asked once and declined, the claims stay on
   the map until the lease lapses: the work is still there, and the map should say so;
7. **fail open, silently for the flow, loudly for the eye**: a crashing adapter must never block
   work — losing a note is an inconvenience; blocking would be the lock's disease in the
   informer's coat. This MUST hold when the hook **command itself cannot start** — a moved,
   deleted, or renamed script — not only when the code inside it throws. A harness that treats a
   launcher failure as a block (many read a non-zero exit as "deny") will wedge every gated tool
   the instant the script goes missing, and it will do so *fail-closed*, which is the opposite of
   this obligation. Learned the hard way: renaming the package pulled the script out from under its
   own wired path and blocked Bash, Edit, Write and every MCP tool at once — including the kill
   switch. So the wired command MUST degrade to a no-op it cannot fail to run: either a tiny
   wrapper that exits 0 when the real script is absent, or an install that re-points the moment its
   target moves. **A gate you cannot reach to turn off is the one thing worse than no gate**, and
   that is doubly true here, where there is no gate to justify the risk;
8. offer a **kill switch** that reaches sessions already running (a file checked on every call,
   `~/.transponder/DISABLED`) and that does not need a terminal (an MCP tool). An informer cannot
   wedge the machine, but it can be wrong, noisy or slow, and off must mean off, everywhere,
   instantly. **The switch MUST be reachable even when the adapter script is gone** (obligation 7):
   a file checked by a wrapper, or a harness that fails a missing hook open, so "off" does not
   depend on the very script that broke. "On" must re-wire the hooks as well as disarm — reporting
   on while feeding nothing is the worst of the three states.

## 8. What kills this design

Falsifiable, off the tape, and each one cheap:

| the claim | what kills it |
|---|---|
| agents ask before they write, once asking is cheap and taught | a checkout shared for days with zero declarations — the instruction is being ignored, and this is decoration |
| a green light is worth waiting for | agents that declare, are told NOT CLEAR, and write anyway. Observable only from the tape and only for declared writes, which is a weaker instrument than v2 had, and that is the price of §4 |
| information suffices — the loss of exclusion was affordable | an out-of-scope write destroying work that `git revert` could not bring back. **One** is enough |
| **and this one has no instrument at all** | with detection deleted, a collision that nobody notices leaves no trace anywhere. If this design fails, it will fail silently — which is why §4 states the bet rather than burying it |

## Non-goals

- **Not enforcement.** A process that ignores the convention writes freely; the convention is for
  cooperating harnesses, and §0a is the argument for why that is enough. If it is not, §8 will say
  so before anyone loses much.
- **Not a network protocol.** One machine, one filesystem.
- **Not a replacement for worktree isolation.** Where a harness can give each agent its own
  worktree, that is strictly better; this covers what worktrees don't reach — sessions
  deliberately pointed at one checkout, mixed-vendor fleets, and the drift check.
- **Not arbitration between subagents.** A subagent's tool calls carry its parent's session id
  (verified against a real run), so one session and its subagents are one participant on the map.

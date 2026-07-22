"""The channel and the witness, driven over the real hook wire format.

Three claims, in the order they matter:

  1. THE MAP IS COHERENT — a granted region belongs to exactly one agent, overlap is decidable,
     and a conflict names the exact intersection. A double-booked map is worse than no map,
     because it is believed.
  2. THE COURIER INFORMS AND NEVER REFUSES — an agent walking into a shared checkout gets
     introduced, once, and every call proceeds. It does NOT warn before a write: that was
     specified, built, and undeliverable, because a hook reaches an agent ahead of its tool only
     by refusing the call. The failure this project prevents was never malice, it was an agent
     that did not know another agent was there.
  3. THE WITNESS NAMES WHAT HAPPENED — a write into another agent's region is reported, loudly,
     to the agent that did it, with the remedy attached. It is a fact off the tree, not a guess
     about a command.
"""

import os
import subprocess

from transponder import scope

from test_adapters import CLAUDE, claude_edit, claude_shell, claude_write, run_hook


def declare(repo, session, paths, intent="working"):
    return scope.declare(repo, session, paths, intent)


def dirs(repo, *names):
    """Create the directories AND COMMIT them: `git status --porcelain` collapses a wholly
    untracked directory to one `?? api/` line, and every real repo has tracked directories."""
    for name in names:
        os.makedirs(os.path.join(repo, name), exist_ok=True)
        with open(os.path.join(repo, name, ".keep"), "w") as f:
            f.write("")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "scaffold"], cwd=repo, check=True)


# --- 1. the map ------------------------------------------------------------------------------------

def test_overlap_is_decidable_or_it_is_refused(repo):
    """One namespace — canonical filesystem paths — one overlap relation (the prefix), and anything
    else is rejected at declare rather than guessed at. A scope system unsure whether two regions
    touch hands one region to two agents and tells each it is alone."""
    r = "c:/x/repo"
    assert scope.overlaps(f"{r}/**", f"{r}/src/a.py")
    assert scope.overlaps(f"{r}/src/**", f"{r}/src/api/x.py")
    assert scope.overlaps(f"{r}/src/**", f"{r}/src/api/**")
    assert not scope.overlaps(f"{r}/src/**", f"{r}/web/**")
    assert not scope.overlaps(f"{r}/src/a.py", f"{r}/src/b.py")
    assert not scope.overlaps(f"{r}/src/**", f"{r}/srcx/y.py")   # prefix means DIRECTORY, not string

    assert scope.intersection(f"{r}/src/**", f"{r}/src/api/**") == f"{r}/src/api/**"
    assert scope.intersection(f"{r}/src/**", f"{r}/src/a.py") == f"{r}/src/a.py"

    a = scope.resolve("API/../api/server.py", repo)
    b = scope.resolve("api/server.py", repo)
    assert a == b, "two spellings of one file must collapse to one claim"

    assert scope.resolve("src/*.py", repo) is None, "a general glob must be refused"
    assert scope.resolve("git:index", repo) is None, "a named resource is not a path — say .git/index"
    assert scope.resolve(".git/index", repo), "the index IS a file, and reservable as one"
    assert scope.resolve("**", repo) == scope.canon(repo) + "/**"


def test_the_map_never_double_books_and_a_conflict_is_an_answer(repo):
    """A conflicting declare is not registered — and the answer carries who, why, the exact
    intersection to subtract, and what is free right now. Nothing about the agent's WORK is
    refused; what is refused is writing a lie into the map."""
    dirs(repo, "api", "web", "docs")

    assert declare(repo, "A", ["api/**"], intent="the rate limiter")["status"] == "granted"
    v = declare(repo, "B", ["api/handlers/**"])

    assert v["status"] == "conflict"
    assert v["conflicts"][0]["session"] == "A"
    assert "the rate limiter" in v["conflicts"][0]["intent"]
    assert v["conflicts"][0]["intersection"] == [scope.canon(repo) + "/api/handlers/**"], (
        "a conflict must name the exact intersection, so narrowing is computed rather than guessed")
    assert any("web" in f for f in v["free_hint"]), "a conflict that does not say where to go is a wall"
    assert not scope.declared("B"), "the conflicting claim must not have been registered"


def test_a_lapsed_claim_binds_nobody(repo, monkeypatch):
    """Leases are the decay of information: an agent that stopped renewing stops being on the map,
    and its region is free again without anyone having to force anything."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])

    from transponder import env
    real_now = env.now
    monkeypatch.setattr(env, "now", lambda: real_now() + scope.LEASE_SECONDS + 1)

    assert declare(repo, "B", ["api/**"])["status"] == "granted", "a lapsed claim still bound B"


# --- 2. the courier ---------------------------------------------------------------------------------

def test_a_write_into_anothers_region_is_reported_after_it_lands(repo):
    """This used to assert a HEADS UP before the write, and that moment turned out not to exist:
    a hook cannot reach a Claude Code agent before its tool runs without refusing the call. So an
    Edit is witnessed exactly like a shell — the write succeeds, and the truth follows immediately,
    with the remedy attached. Nothing is prevented, which was always the honest description."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"], intent="the rate limiter")
    declare(repo, "B", ["web/**"])

    res = claude_write(repo, "B", path="api/server.py")     # B reaches into A's region

    assert res.returncode == 0, "nothing is ever refused"
    assert "SCOPE VIOLATION" in res.stdout, "an Edit into another's region went unwitnessed"
    assert "agent A" in res.stdout and "the rate limiter" in res.stdout
    assert not res.pre_stdout.strip(), "the pre half spoke about a write it could not stop in time"


def test_the_agent_whose_region_was_written_is_told_on_its_next_call(repo):
    """Both parties, or neither is served.

    `victim` used to appear in exactly two places: computed in scope.violations, and rendered into
    the OFFENDER's message. The agent whose half-finished work had just been overwritten was
    addressed by nothing at all — so the remedy asked the offender to put back bytes it had never
    seen (a guess, in a library that refuses to guess) and made it write into the region a second
    time to do it, firing the alarm again at an agent that was complying."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"], intent="the rate limiter")
    declare(repo, "B", ["web/**"])

    offender = claude_write(repo, "B", path="api/server.py")
    assert "SCOPE VIOLATION" in offender.stdout
    assert "THEY HAVE BEEN TOLD TOO" in offender.stdout
    assert "Put back what was theirs" not in offender.stdout, (
        "the offender is being asked to reconstruct work it never saw")

    mail = claude_shell(repo, "A", "cat a.txt")
    assert "SOMEONE WROTE IN YOUR REGION" in mail.pre_stdout, "the victim was never told"
    assert "api/server.py" in mail.pre_stdout and "agent B" in mail.pre_stdout

    again = claude_shell(repo, "A", "cat a.txt")
    assert "SOMEONE WROTE IN YOUR REGION" not in again.pre_stdout, (
        "redelivered — a note repeated on every call is a note that stops being read")


def test_an_agent_walking_into_a_shared_checkout_is_introduced_once(repo):
    """The fact an agent cannot see from inside its own context: who else is here. Delivered at
    the first moment it matters, not repeated on every call — a note printed forever is a note
    nobody reads. And the way in is taught: declare, don't wait."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    first = claude_shell(repo, "B", "cat a.txt")
    assert "THIS CHECKOUT IS SHARED" in first.pre_stdout
    assert "declare_scope" in first.pre_stdout, "the intro must teach the way in"
    assert "agent A" in first.pre_stdout

    second = claude_shell(repo, "B", "cat a.txt")
    assert "THIS CHECKOUT IS SHARED" not in second.pre_stdout, "introduced twice — that is spam"


def test_a_participant_writing_unclaimed_ground_is_asked_to_declare_it(repo):
    """The map should say what participants are actually touching. It used to be made true FOR the
    agent — the pre-write path silently widened A's claim to cover whatever it was about to edit.
    That went with heads_up(), and the replacement is the treatment a shell has always had: say so,
    and ask. Better on its own merits, not merely what was left: a map must not grow behind the
    back of the agent whose name is on it."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])

    res = claude_write(repo, "A", path="notes.md")

    assert res.returncode == 0
    assert "extend_scope" in res.stdout, "a stray write was not reported to its own author"
    assert not scope.covers(scope.scope_of("A"), scope.canon(os.path.join(repo, "notes.md"))), \
        "the map widened itself without the agent saying so"


def test_a_write_inside_your_own_scope_is_silent(repo):
    """The common case must cost nothing and say nothing, or the whole channel becomes noise."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])
    declare(repo, "B", ["web/**"])

    res = claude_edit(repo, "A", path="api/server.py")
    assert res.returncode == 0
    assert not res.stdout.strip(), "a write inside your own scope produced chatter"


# --- 3. the witness ---------------------------------------------------------------------------------

def test_a_shell_write_into_anothers_region_is_witnessed_and_named(repo):
    """A shell's target is not knowable before it runs — the old §7a proof stands — so this could
    not have been prevented and is not. It is NAMED: the path, the victim, the remedy."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"], intent="the rate limiter")
    declare(repo, "B", ["web/**"])

    res = claude_shell(repo, "B", "echo boom > api/server.py")

    assert res.returncode == 0
    assert "SCOPE VIOLATION" in res.stdout
    assert "api/server.py" in res.stdout
    assert "agent A" in res.stdout


def test_a_commit_that_sweeps_anothers_work_is_the_loudest_thing_said(repo):
    """THE founding incident. A `git add -A` sweeps B's half-finished work into A's commit; the
    witness chases HEAD into the object graph, names the swept file — and because a commit is the
    one violation that is cleanly recoverable, the message carries the remedy, not just the
    accusation."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])
    declare(repo, "B", ["web/**"])

    with open(os.path.join(repo, "web", "page.js"), "w") as f:
        f.write("B's half-finished work")

    res = claude_shell(repo, "A", "echo x > api/server.py && git add -A && git commit -qm sweep")

    assert "SCOPE VIOLATION" in res.stdout
    assert "web/page.js" in res.stdout, "the commit swept B's file and nobody noticed"
    assert "agent B" in res.stdout
    assert "git reset --soft HEAD~1" in res.stdout, "a recoverable violation must carry its remedy"


def test_a_participant_straying_onto_unclaimed_ground_is_told_to_declare(repo):
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])

    res = claude_shell(repo, "A", "echo x > web/page.js")   # nobody holds web/**

    assert res.returncode == 0
    assert "VIOLATION" not in res.stdout, "nobody was hurt — this is not a violation"
    assert "outside your declared scope" in res.stdout
    assert "extend_scope" in res.stdout


def test_a_shell_that_only_reads_says_nothing(repo):
    dirs(repo, "api")
    declare(repo, "A", ["api/**"])
    res = claude_shell(repo, "A", "cat a.txt")
    assert res.returncode == 0
    assert not res.stdout.strip()


def test_two_agents_with_disjoint_scopes_work_concurrently_in_silence(repo):
    """What all of this buys: both of the only two collisions in the recorded history of the old
    lock were two sessions working DIFFERENT DIRECTORIES, refused for no reason. Here they work,
    at the same time, and the channel has nothing to say because there is nothing to say."""
    dirs(repo, "api", "web")
    assert declare(repo, "A", ["api/**"])["status"] == "granted"
    assert declare(repo, "B", ["web/**"])["status"] == "granted"

    a = claude_edit(repo, "A", path="api/server.py")
    b = claude_edit(repo, "B", path="web/page.js")
    assert a.returncode == 0 and b.returncode == 0
    assert not a.stdout.strip() and not b.stdout.strip()

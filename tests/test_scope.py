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

from test_adapters import CLAUDE, claude_edit, claude_shell, run_hook


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


def test_an_anchor_that_is_not_a_checkout_is_refused(repo):
    """The anchor was the last unchecked input, and an unchecked anchor does not fail — it succeeds
    somewhere else. Two agents declared with `repo="myapp"`, a NAME: it was joined to the server's
    own working directory, and both were granted regions under a folder that has never existed, in
    a checkout neither was sitting in. Every part of that answer read as reassurance — no conflicts,
    tree clean, `head ?` — which is what a phantom looks like from the inside.
    """
    phantom = os.path.join(repo, "no-such-project")
    v = declare(phantom, "A", ["api/**"])
    assert v["status"] == "rejected", "a claim was granted under a directory that does not exist"
    assert scope.canon(phantom) in v["reason"], (
        "the refusal must name what the anchor RESOLVED TO — the gap between what the agent typed "
        "and what the filesystem made of it is the entire bug, and it is invisible from inside")
    assert not scope.live(), "the refused claim was written anyway"

    plain = os.path.join(repo, "..", "not-a-repo")
    os.makedirs(plain, exist_ok=True)
    assert declare(plain, "A", ["**"])["status"] == "rejected", (
        "a real directory outside any checkout still is not something to agree about")

    dirs(repo, "api")
    assert scope.why_not_a_checkout(repo) is None, "the real checkout was refused"
    assert scope.why_not_a_checkout(os.path.join(repo, "api")) is None, (
        "a subdirectory of a checkout is a legitimate anchor — relative paths still resolve")


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



def test_the_waiter_runs_through_a_real_shell_and_reports_both_ways(repo):
    """A blocked agent waits by launching this in the background; the harness noticing it exit is
    the only thing that can wake an agent.

    Driven through an actual shell, both ways, because this library has already shipped an escape
    hatch that was tested twice and had never once run: `wait_until_free` appeared in ZERO of 4528
    recorded sessions, because the command string it minted died in bash before Python saw it, and
    exiting 127 reported to the harness as success. Two green tests around a boundary nobody
    exercised are worse than no tests, because they are believed."""
    import sys

    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    argv = [sys.executable, "-m", "transponder.wait", "--repo", repo,
            "--paths", "api/**", "--max-minutes", "0", "--every", "1"]
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    held = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=60)
    assert held.returncode == 1, f"the waiter did not see a live claim: {held.stdout}{held.stderr}"
    assert "STILL HELD" in held.stdout, "giving up must say so, not exit quietly"
    assert "A" in held.stdout, "a waiter that gives up must name who it was waiting for"

    # ...and the way an agent will actually launch it: one string, handed to a shell.
    quoted = " ".join(f'"{a}"' for a in argv)
    viashell = subprocess.run(quoted, shell=True, cwd=root, capture_output=True, text=True, timeout=60)
    assert viashell.returncode == 1, (
        f"the waiter died before Python saw it: rc={viashell.returncode} {viashell.stderr[:200]}")

    scope.release("A")
    freed = subprocess.run(argv, cwd=root, capture_output=True, text=True, timeout=60)
    assert freed.returncode == 0, f"the region freed and the waiter did not notice: {freed.stdout}"
    assert "FREE" in freed.stdout


def test_a_channel_message_is_pulled_and_a_direct_one_is_pushed(repo):
    """The line between a courier and a feed, and the whole reason this stays a transponder: chat
    and the violation alarm share one delivery path, so an agent trained to skim the channel skims
    the alarm with it. Direct is pushed. Channel and broadcast wait to be asked for."""
    from transponder import messages

    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    messages.send(sender="B", body="rewriting the auth middleware return type", kind="channel",
                  repo=repo)
    messages.send(sender="B", body="you asked for api/** — free in ten minutes", kind="direct",
                  repo=repo, to="A")

    pushed = claude_shell(repo, "A", "cat a.txt")
    assert "free in ten minutes" in pushed.pre_stdout, "a direct message was not delivered"
    assert "auth middleware" not in pushed.pre_stdout, (
        "channel traffic was pushed — that is how the alarm becomes wallpaper")

    pulled = messages.unread("A", repo, kinds=("direct", "channel", messages.BROADCAST))
    assert any("auth middleware" in m["body"] for m in pulled), "the channel was not readable at all"


def test_reading_clears_a_message_for_you_and_leaves_it_for_everyone_else(repo):
    """A queue that deletes on read cannot serve two addressees. Per-reader seen-sets are what make
    a pull non-destructive — the user's correction, and it is what lets a channel exist at all."""
    from transponder import messages

    dirs(repo, "api")
    messages.send(sender="C", body="moving the schema at 15:00", kind="channel", repo=repo)

    first = messages.unread("A", repo, kinds=("channel",))
    assert len(first) == 1
    assert not messages.unread("A", repo, kinds=("channel",)), "redelivered to the reader"
    assert len(messages.unread("B", repo, kinds=("channel",))) == 1, (
        "one agent's read deleted the message for everyone else")


def test_the_holder_is_told_that_someone_wanted_its_region(repo):
    """A conflict was computed and answered to the ASKER only, so an agent sitting on a wide scope
    never learned anyone was queued behind it — the same one-sided delivery as the violation report.
    Deduped, because a retrying agent must not become a siren."""
    from transponder import messages, server

    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    for _ in range(3):
        server.declare_work(repo, "B", ["api/handlers/**"], "adding a handler")

    got = messages.unread("A", repo, kinds=("direct",), mark=False)
    wanted = [m for m in got if "SOMEONE WANTS YOUR REGION" in m["body"]]
    assert wanted, "the holder was never told anyone asked for its region"
    assert len(wanted) == 1, f"a retrying asker became a siren: {len(wanted)} notices"
    assert "agent B" in wanted[0]["body"] and "adding a handler" in wanted[0]["body"]


def test_a_session_parked_on_a_question_is_told_on_the_way_back_in(repo):
    """A victim that has stopped to ask its human something makes no tool calls, so mail addressed
    to it waits until it writes again — one call too late. UserPromptSubmit is where it is reached,
    and it is the only event whose output the harness puts in front of the model BEFORE it acts.

    The wiring assertion is the load-bearing one, and it is not paranoia. `HANDLERS` routed
    UserPromptSubmit to session_start for the whole of v2, with a comment saying so, and `EVENTS`
    never wired it — so it never ran once. A test of the handler passes either way; only a test that
    the event is WIRED can tell the difference between a feature and a comment about a feature."""
    from transponder.hooks.claude_code import EVENTS

    assert "UserPromptSubmit" in EVENTS, "the handler exists but nothing will ever call it"

    from transponder import messages

    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"], intent="the rate limiter")
    messages.send(sender="B", body="I need api/x when you are free", kind="direct",
                  repo=repo, to="A")

    back = run_hook(CLAUDE, {"hook_event_name": "UserPromptSubmit", "cwd": repo, "session_id": "A"})
    assert "I need api/x when you are free" in back.stdout, (
        "a session coming back from its human was not handed what was waiting for it")




def test_an_agent_is_introduced_to_the_machine_once(repo):
    """The fact an agent cannot see from inside its own context: who else is here. Delivered at the
    first moment it matters, not repeated on every call — a note printed forever is a note nobody
    reads. And the way in is taught: declare what you will edit, don't wait.

    It introduces the MACHINE, not "this checkout". The old version picked one repo from the
    session's cwd, so an agent was told about its neighbours only when it happened to be sitting in
    the same folder — and an agent editing across checkouts, which is the ordinary case for anyone
    with a lib and its client open, was told nothing at all."""
    dirs(repo, "api")
    declare(repo, "A", ["api/**"], intent="the rate limiter")

    first = claude_shell(repo, "B", "cat a.txt")
    assert "NOT THE ONLY AGENT" in first.pre_stdout
    assert "agent A" in first.pre_stdout
    assert "declare_work" in first.pre_stdout, "the intro must teach the way in"
    assert "WAIT FOR THE GREEN LIGHT" in first.pre_stdout, (
        "the protocol is declare-then-wait; an intro that omits the wait teaches half of it")
    assert "channel(" in first.pre_stdout and "finish_work" in first.pre_stdout, (
        "all four steps or none — an agent that never calls finish_work blocks the next one")

    second = claude_shell(repo, "B", "cat a.txt")
    assert "NOT THE ONLY AGENT" not in second.pre_stdout, "introduced twice — that is spam"



def test_a_write_inside_your_own_scope_is_silent(repo):
    """The common case must cost nothing and say nothing, or the whole channel becomes noise."""
    dirs(repo, "api", "web")
    declare(repo, "A", ["api/**"])
    declare(repo, "B", ["web/**"])

    res = claude_edit(repo, "A", path="api/server.py")
    assert res.returncode == 0
    assert not res.stdout.strip(), "a write inside your own scope produced chatter"


# --- 3. the witness ---------------------------------------------------------------------------------




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

import subprocess

import pytest


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A real git checkout with one commit, and a lock directory of its own."""
    monkeypatch.setenv("REPOLOCK_DIR", str(tmp_path / "locks"))
    monkeypatch.setenv("REPOLOCK_DISABLED", "0")   # the machine's own panic file must not mute the
                                                   # suite — a green run would then mean nothing
    work = tmp_path / "repo"
    work.mkdir()
    for args in (["git", "init", "-q"], ["git", "config", "user.email", "t@t"],
                 ["git", "config", "user.name", "t"]):
        subprocess.run(args, cwd=work, check=True)
    (work / "a.txt").write_text("one")
    subprocess.run(["git", "add", "-A"], cwd=work, check=True)
    subprocess.run(["git", "commit", "-qm", "one"], cwd=work, check=True)
    return str(work)

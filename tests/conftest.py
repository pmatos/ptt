import os
import stat
import subprocess
from pathlib import Path

import pytest

FAKE_BIN = Path(__file__).parent / "fake_bin"


@pytest.fixture(autouse=True)
def tmp_xdg(monkeypatch, tmp_path):
    """Redirect all XDG dirs into a temp dir so tests never touch real config."""
    cfg, state, cache = tmp_path / "config", tmp_path / "state", tmp_path / "cache"
    for p in (cfg, state, cache):
        p.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(cfg))
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return {"config": cfg, "state": state, "cache": cache}


@pytest.fixture
def fake_bin(monkeypatch):
    """Prepend the fake claude/gh onto PATH for the duration of a test."""
    for f in FAKE_BIN.iterdir():
        if f.is_file():
            f.chmod(f.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    monkeypatch.setenv("PATH", f"{FAKE_BIN}{os.pathsep}{os.environ['PATH']}")
    return FAKE_BIN


def _git(work, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(work), *args], check=check, capture_output=True, text=True
    )


def _make_github_repo(work: Path) -> Path:
    """A real local git repo whose origin *looks* like github.com but is wired
    via insteadOf to a local bare repo, so is_github_repo() passes AND
    fetch/worktree actually work offline."""
    bare = work.parent / (work.name + "-remote.git")
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    work.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        ["git", "init", "-b", "main", str(work)], check=True, capture_output=True
    )
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    fake_url = "https://github.com/fake/repo.git"
    _git(work, "remote", "add", "origin", fake_url)
    _git(work, "config", f"url.{bare.as_uri()}/.insteadOf", fake_url)
    (work / "README.md").write_text("# fake\n")
    _git(work, "add", "-A")
    _git(work, "commit", "-m", "init")
    _git(work, "push", "origin", "main")
    return work


@pytest.fixture
def github_repo(tmp_path):
    return _make_github_repo(tmp_path / "work")


@pytest.fixture
def github_repo_factory():
    """Factory to build several github-style repos (e.g. with colliding basenames)."""
    return _make_github_repo

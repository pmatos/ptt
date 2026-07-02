import subprocess

import pytest

from ptt import git_ops


def _git(work, *args):
    return subprocess.run(
        ["git", "-C", str(work), *args], check=True, capture_output=True, text=True
    )


def test_branch_name():
    assert (
        git_ops.branch_name("code-audit", "20260630T050000Z")
        == "ptt/code-audit/20260630T050000Z"
    )


def test_is_github_repo_true(github_repo, tmp_path):
    assert git_ops.is_github_repo(github_repo, tmp_path / "g.log") is True


def test_is_github_repo_false_for_non_github_origin(tmp_path):
    work = tmp_path / "gl"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "remote", "add", "origin", "https://gitlab.com/x/y.git")
    assert git_ops.is_github_repo(work, tmp_path / "g.log") is False


def test_is_github_repo_false_when_no_origin(tmp_path):
    work = tmp_path / "noorigin"
    work.mkdir()
    _git(work, "init", "-b", "main")
    assert git_ops.is_github_repo(work, tmp_path / "g.log") is False


def test_fetch_succeeds(github_repo, tmp_path):
    git_ops.fetch(github_repo, "main", tmp_path / "g.log")  # no raise


def test_fetch_bad_branch_raises(github_repo, tmp_path):
    with pytest.raises(git_ops.GitError):
        git_ops.fetch(github_repo, "does-not-exist", tmp_path / "g.log")


def test_add_and_remove_worktree(github_repo, tmp_path):
    git_ops.fetch(github_repo, "main", tmp_path / "g.log")
    dest = tmp_path / "wt"
    git_ops.add_worktree(github_repo, dest, "ptt/x/1", "main", tmp_path / "g.log")
    assert (dest / "README.md").is_file()
    git_ops.remove_worktree(github_repo, dest, tmp_path / "g.log")
    assert not dest.exists()


def test_remove_worktree_missing_is_best_effort(github_repo, tmp_path):
    # removing a non-existent worktree must not raise
    git_ops.remove_worktree(github_repo, tmp_path / "nope", tmp_path / "g.log")


def _bare_with_commit(tmp_path):
    """A local bare repo with one commit on main, usable as a clone source."""
    bare = tmp_path / "src.git"
    subprocess.run(
        ["git", "init", "--bare", "-b", "main", str(bare)],
        check=True,
        capture_output=True,
    )
    seed = tmp_path / "seed"
    subprocess.run(
        ["git", "init", "-b", "main", str(seed)], check=True, capture_output=True
    )
    _git(seed, "config", "user.email", "t@example.com")
    _git(seed, "config", "user.name", "Tester")
    (seed / "README.md").write_text("# fake\n")
    _git(seed, "add", "-A")
    _git(seed, "commit", "-m", "init")
    _git(seed, "remote", "add", "origin", str(bare))
    _git(seed, "push", "origin", "main")
    return bare


def test_clone_and_create_branch_and_remove(tmp_path):
    bare = _bare_with_commit(tmp_path)
    dest = tmp_path / "clone"
    log = tmp_path / "g.log"
    git_ops.clone(str(bare), dest, "main", log)
    assert (dest / "README.md").is_file()
    git_ops.create_branch(dest, "ptt/x/1", log)
    head = _git(dest, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip()
    assert head == "ptt/x/1"
    git_ops.remove_clone(dest, log)
    assert not dest.exists()


def test_clone_bad_branch_raises(tmp_path):
    bare = _bare_with_commit(tmp_path)
    with pytest.raises(git_ops.GitError):
        git_ops.clone(
            str(bare), tmp_path / "clone2", "does-not-exist", tmp_path / "g.log"
        )


def test_remove_clone_missing_is_best_effort(tmp_path):
    git_ops.remove_clone(tmp_path / "nope", tmp_path / "g.log")

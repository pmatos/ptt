"""Git operations: classify a repo's origin, and create/remove the per-run clone.
ptt runs every project (local or remote) in a fresh throwaway clone of its
github.com remote — never a worktree of a local checkout — so nothing local is
fetched into, branched, or mutated. All commands are tee'd to git.log via proc.run."""

from __future__ import annotations

import shutil
from pathlib import Path

from ptt import proc

REMOTE = "origin"


class GitError(Exception):
    pass


def branch_name(routine: str, run_id: str) -> str:
    return f"ptt/{routine}/{run_id}"


def origin_url(path: Path, log_path: Path) -> str | None:
    # config --get returns the *stored* URL (insteadOf rewrites are not applied),
    # which is what we want to classify the remote and use as the clone source.
    r = proc.run(
        ["git", "-C", str(path), "config", "--get", f"remote.{REMOTE}.url"],
        log_path=log_path,
    )
    return r.stdout.strip() if r.returncode == 0 else None


def is_github_repo(path: Path, log_path: Path) -> bool:
    url = origin_url(path, log_path)
    return url is not None and "github.com" in url


def clone(url: str, dest: Path, base_branch: str, log_path: Path) -> None:
    """Clone a remote repo into a fresh disposable dir (ephemeral per-run clone)."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    r = proc.run(
        ["git", "clone", "--single-branch", "--branch", base_branch, url, str(dest)],
        log_path=log_path,
    )
    if r.returncode != 0:
        raise GitError(f"git clone {url} failed: {r.stderr.strip()}")


def create_branch(repo: Path, branch: str, log_path: Path) -> None:
    r = proc.run(["git", "-C", str(repo), "checkout", "-b", branch], log_path=log_path)
    if r.returncode != 0:
        raise GitError(f"git checkout -b {branch} failed in {repo}: {r.stderr.strip()}")


def reset_worktree(repo: Path, base_branch: str, log_path: Path) -> bool:
    """Discard everything a prior attempt left in the clone — local commits, staged
    and unstaged edits, and untracked/ignored files — returning the worktree to the
    freshly-branched base state. Returns True only if the reset fully succeeds.

    Used between `claude.run_claude` retries so a failed attempt's local side effects
    don't leak into the next one (where they could be mis-reported as `no_action`
    while an unpushed commit is silently dropped with the ephemeral clone). A local
    reset cannot revert an already-pushed branch or an opened PR; those remote side
    effects are caught instead by the runner's before/after gh snapshot."""
    reset = proc.run(
        ["git", "-C", str(repo), "reset", "--hard", f"{REMOTE}/{base_branch}"],
        log_path=log_path,
    )
    if reset.returncode != 0:
        return False
    clean = proc.run(
        ["git", "-C", str(repo), "clean", "-fdx"],
        log_path=log_path,
    )
    return clean.returncode == 0


def remove_clone(dest: Path, log_path: Path) -> None:
    """Best-effort removal of an ephemeral clone; never raises (finally block)."""
    try:
        with log_path.open("a") as fh:
            fh.write(f"$ rm -rf {dest}\n")
    except OSError:
        pass
    shutil.rmtree(dest, ignore_errors=True)

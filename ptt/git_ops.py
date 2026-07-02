"""Git operations: validate a project is a GitHub repo, fetch, and manage the
per-run worktree. All commands are tee'd to the project's git.log via proc.run."""

from __future__ import annotations

from pathlib import Path

from ptt import proc

REMOTE = "origin"


class GitError(Exception):
    pass


def branch_name(routine: str, run_id: str) -> str:
    return f"ptt/{routine}/{run_id}"


def is_github_repo(path: Path, log_path: Path) -> bool:
    # config --get returns the *stored* URL (insteadOf rewrites are not applied),
    # which is what we want to classify the remote.
    r = proc.run(
        ["git", "-C", str(path), "config", "--get", f"remote.{REMOTE}.url"],
        log_path=log_path,
    )
    return r.returncode == 0 and "github.com" in r.stdout


def fetch(path: Path, base_branch: str, log_path: Path) -> None:
    r = proc.run(
        ["git", "-C", str(path), "fetch", REMOTE, base_branch], log_path=log_path
    )
    if r.returncode != 0:
        raise GitError(
            f"git fetch {REMOTE} {base_branch} failed in {path}: {r.stderr.strip()}"
        )


def add_worktree(
    repo: Path, dest: Path, branch: str, base_ref: str, log_path: Path
) -> None:
    r = proc.run(
        [
            "git",
            "-C",
            str(repo),
            "worktree",
            "add",
            str(dest),
            "-b",
            branch,
            f"{REMOTE}/{base_ref}",
        ],
        log_path=log_path,
    )
    if r.returncode != 0:
        raise GitError(f"git worktree add failed for {repo}: {r.stderr.strip()}")


def remove_worktree(repo: Path, dest: Path, log_path: Path) -> None:
    """Best-effort cleanup; never raises (called from a finally block)."""
    proc.run(
        ["git", "-C", str(repo), "worktree", "remove", "--force", str(dest)],
        log_path=log_path,
    )

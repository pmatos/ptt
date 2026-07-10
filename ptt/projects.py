"""Classify a routine's project entry as either a local checkout or a remote
GitHub repo to be cloned ephemerally. Syntactic and side-effect free for ordinary
entries (no network access) — a `gh:owner/repo` marker or a git URL is remote;
anything else (including a bare `owner/repo`) is a local path. The `gh:` marker is
required so a relative path like `dev/repo` is never mistaken for a GitHub slug and
silently cloned from a foreign repo (#9). Degenerate entries whose derived directory
name would be empty, `.`, or `..` are sanitized (`_safe_name`) so the run's
log/worktree dirs can't collapse to or escape their parent: a local path resolves
once against the cwd to recover its real basename; a URL falls back to a fixed token."""

from __future__ import annotations

import contextlib
import re
from pathlib import Path

from ptt import models as m

# Explicit opt-in marker for the GitHub `owner/repo` remote shorthand.
_GH_PREFIX = "gh:"
_URL_RE = re.compile(r"^(https?://|ssh://|git@)")


def parse(raw: str) -> m.ProjectSpec:
    s = raw.strip()
    if _URL_RE.match(s):
        return m.ProjectSpec(
            raw=raw, is_remote=True, location=s, name=_safe_name(_name_from_url(s))
        )
    if s.startswith(_GH_PREFIX):
        slug = _strip_git(s[len(_GH_PREFIX) :])
        url = f"https://github.com/{slug}.git"
        return m.ProjectSpec(
            raw=raw,
            is_remote=True,
            location=url,
            name=_safe_name(slug.rsplit("/", 1)[-1]),
        )
    # A bare `~user` that can't be resolved makes expanduser() raise RuntimeError;
    # a malformed project entry must degrade to a (bad) literal path, not crash the run.
    path = Path(s)
    with contextlib.suppress(RuntimeError):
        path = path.expanduser()
    return m.ProjectSpec(
        raw=raw, is_remote=False, location=str(path), name=_safe_name(path.name, path)
    )


def _name_from_url(url: str) -> str:
    return _strip_git(url.rstrip("/").split("/")[-1].split(":")[-1])


def slug_from_url(url: str) -> str | None:
    """The `owner/repo` slug of a remote URL (for `--project` matching), or None.
    Handles `https://…/owner/repo(.git)`, `ssh://…/owner/repo.git`, and the scp
    form `git@github.com:owner/repo.git`."""
    body = _strip_git(re.sub(r"^(https?://|ssh://|git@)", "", url).rstrip("/"))
    parts = [p for p in re.split(r"[/:]", body) if p]
    return "/".join(parts[-2:]) if len(parts) >= 2 else None


def _safe_name(name: str, path: Path | None = None) -> str:
    """A non-empty, non-traversing directory name for the run's log/worktree dirs.
    A degenerate name (``""``/``.``/``..`` — e.g. from `projects = ["."]` or a
    mistyped `https://github.com/org/..`) would make `dest = work_dir/<run_id>/name`
    collapse to or escape its parent and then be `rmtree`d on cleanup. A local path
    resolves once against the cwd to recover its real basename; a URL (no path to
    resolve) falls back to a fixed token."""
    if name and name not in (".", ".."):
        return name
    if path is not None:
        resolved = path.resolve().name
        if resolved and resolved not in (".", ".."):
            return resolved
    return "project"


def _strip_git(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name

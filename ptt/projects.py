"""Classify a routine's project entry as either a local checkout or a remote
GitHub repo to be cloned ephemerally. Syntactic and side-effect free for ordinary
entries (no network access) — a `owner/repo` shorthand or a git URL is remote;
anything else is a local path. The one exception is a degenerate local path (`.`,
`..`, `/`), whose basename is empty or `..`; that case is resolved once against
the cwd to recover a real, non-empty directory name for the run's log/worktree
dirs (see `name` handling below)."""

from __future__ import annotations

import re
from pathlib import Path

from ptt import models as m

# owner/repo — exactly one slash, no leading ~ . / and no scheme.
_SLUG_RE = re.compile(r"^[A-Za-z0-9][\w.-]*/[A-Za-z0-9][\w.-]*$")
_URL_RE = re.compile(r"^(https?://|ssh://|git@)")


def parse(raw: str) -> m.ProjectSpec:
    s = raw.strip()
    if _URL_RE.match(s):
        return m.ProjectSpec(
            raw=raw, is_remote=True, location=s, name=_name_from_url(s)
        )
    if _SLUG_RE.match(s):
        url = f"https://github.com/{s}.git"
        return m.ProjectSpec(
            raw=raw, is_remote=True, location=url, name=_strip_git(s.split("/")[1])
        )
    path = Path(s).expanduser()
    name = path.name
    if name in ("", ".."):
        # `.`/`/` give an empty basename and `..`/`foo/..` give a literal `..`;
        # either would corrupt (or escape) the run's log/worktree dir layout, so
        # resolve to a real basename, falling back to a fixed token for the root.
        name = path.resolve().name or "project"
    return m.ProjectSpec(raw=raw, is_remote=False, location=str(path), name=name)


def _name_from_url(url: str) -> str:
    return _strip_git(url.rstrip("/").split("/")[-1].split(":")[-1])


def _strip_git(name: str) -> str:
    return name[:-4] if name.endswith(".git") else name

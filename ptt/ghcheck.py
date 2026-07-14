"""Preflight: is the gh CLI installed and authenticated?

Every routine run drives `gh` (PR/issue snapshots) and clones the project over
HTTPS, both of which lean on gh's GitHub credentials. When gh is logged out the
run otherwise dies deep in per-project work with a cryptic "gh snapshot failed",
or — interactively — hangs on git's own `Username for 'https://github.com'`
prompt. Checking up front turns that into one clean, actionable message. The
`which`/`run` seams are injected so the check is unit-testable without a real gh."""

from __future__ import annotations

import shutil
from collections.abc import Callable

from ptt import proc

_NOT_FOUND = "gh CLI not found on PATH — install it from https://cli.github.com/"
_LOGGED_OUT = (
    "gh is not authenticated — run `gh auth login` "
    "(answer yes to 'Authenticate Git', or run `gh auth setup-git`)"
)


def gh_problem(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., proc.Completed] = proc.run,
) -> str | None:
    """None if gh is installed and authenticated; otherwise a one-line reason
    (with the fix) suitable for printing to the user. gh's own output is captured
    by proc.run, so nothing leaks to the console on the happy path."""
    if which("gh") is None:
        return _NOT_FOUND
    # Scope to the *active* github.com account: a bare `gh auth status` probes every
    # configured host, and even `--hostname github.com` alone tests every github.com
    # account and fails if any is broken. `--active` checks only the account gh uses
    # for github.com — the one ptt's later `gh pr/issue` calls target — so a stale
    # Enterprise host or an expired inactive account can't wrongly fail this preflight.
    cmd = ["gh", "auth", "status", "--hostname", "github.com", "--active"]
    if run(cmd).returncode != 0:
        return _LOGGED_OUT
    return None

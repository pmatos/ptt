"""Preflight: is the gh CLI installed and authenticated?

Every routine run drives `gh` (PR/issue snapshots) and clones the project over
HTTPS, both of which lean on gh's GitHub credentials. When gh is logged out the
run otherwise dies deep in per-project work with a cryptic "gh snapshot failed",
or — interactively — hangs on git's own `Username for 'https://github.com'`
prompt. Checking up front turns that into one clean, actionable message.

The check is split in two so a *transient* GitHub outage can't masquerade as a
logout. `gh auth token` is a purely local read — a missing token is the one
unambiguous "logged out" and fails fast. `gh auth status --active` instead
validates the token against the GitHub API, so a network blip (e.g. the timer
firing before connectivity is fully up) makes it exit non-zero even for a
perfectly good token — the exact failure that once aborted a scheduled run with
a false "not authenticated". That online check is therefore best-effort: it's
retried briefly, and if it still can't reach GitHub the preflight proceeds
anyway rather than abort. Proceeding never does worse than aborting: if only
GitHub is unreachable the clones fail but the summary email still sends; a full
network outage fails the email too (the same limit `wait-online` has), leaving
the failed timer unit as the signal — but a good token that briefly couldn't be
validated is no longer wrongly killed.

The `which`/`run`/`sleep`/`monotonic` seams are injected so the whole thing is
unit-testable without a real gh or real time."""

from __future__ import annotations

import shutil
import time
from collections.abc import Callable

from ptt import proc

_NOT_FOUND = "gh CLI not found on PATH — install it from https://cli.github.com/"
_LOGGED_OUT = (
    "gh is not authenticated — run `gh auth login` "
    "(answer yes to 'Authenticate Git', or run `gh auth setup-git`)"
)

# How long to keep re-checking online validation before proceeding anyway.
DEFAULT_VALIDATE_TIMEOUT_S = 30.0
DEFAULT_VALIDATE_INTERVAL_S = 3.0


def gh_problem(
    *,
    which: Callable[[str], str | None] = shutil.which,
    run: Callable[..., proc.Completed] = proc.run,
    timeout_s: float = DEFAULT_VALIDATE_TIMEOUT_S,
    interval_s: float = DEFAULT_VALIDATE_INTERVAL_S,
    sleep: Callable[[float], None] = time.sleep,
    monotonic: Callable[[], float] = time.monotonic,
) -> str | None:
    """None if the run may proceed; otherwise a one-line reason (with the fix)
    suitable for printing to the user. gh's own output is captured by proc.run, so
    nothing leaks to the console on the happy path."""
    if which("gh") is None:
        return _NOT_FOUND

    # Hard gate: is a github.com token stored at all? This is a local read, so it
    # stays reliable even when GitHub is unreachable — the one signal that means a
    # genuine logout (and the later gh/git calls would hang on a username prompt).
    if run(["gh", "auth", "token", "--hostname", "github.com"]).returncode != 0:
        return _LOGGED_OUT

    # Best-effort online validation of that token, scoped to the *active* github.com
    # account (the one ptt's later `gh pr/issue` calls target). A transient failure
    # here must not fail the run, so we retry briefly and then proceed regardless.
    status = ["gh", "auth", "status", "--hostname", "github.com", "--active"]
    deadline = monotonic() + timeout_s
    while True:
        # Bound each probe by the time left on the deadline: proc.run passes it to
        # subprocess (returning rc 124 on expiry, which we treat like any failed
        # check), so a hung `gh` — GitHub accepts the connection but never replies —
        # can't block the scheduled run past timeout_s. Without a per-call timeout the
        # deadline would only be reconsidered once run() returned, i.e. never.
        remaining = deadline - monotonic()
        if remaining <= 0:
            return None
        if run(status, timeout=remaining).returncode == 0:
            return None
        # Cap the wait to the budget left, so a probe that used it all (e.g. rc 124
        # right at the deadline) doesn't tack on another interval and push the bound
        # out to timeout_s + interval_s; when nothing's left the loop top returns next.
        wait = min(interval_s, deadline - monotonic())
        if wait > 0:
            sleep(wait)

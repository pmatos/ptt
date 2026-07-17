from ptt import ghcheck
from ptt.proc import Completed

TOKEN_CMD = ["gh", "auth", "token", "--hostname", "github.com"]
STATUS_CMD = ["gh", "auth", "status", "--hostname", "github.com", "--active"]


def _fake_run(*, token_rc=0, status_rcs=(0,)):
    """Return `(run, calls, status_timeouts)`: a fake `run` that returns *token_rc*
    for the local token probe and walks the *status_rcs* sequence on successive
    online-validation probes (repeating the last entry once exhausted), plus the list
    of commands it saw and the `timeout=` passed to each status probe."""
    seq = list(status_rcs)
    calls: list[list[str]] = []
    status_timeouts: list[float | None] = []

    def run(cmd, **kw):
        calls.append(cmd)
        if cmd == TOKEN_CMD:
            return Completed(token_rc, "", "")
        if cmd == STATUS_CMD:
            status_timeouts.append(kw.get("timeout"))
            rc = seq.pop(0) if len(seq) > 1 else seq[0]
            return Completed(rc, "", "")
        raise AssertionError(f"unexpected command: {cmd}")

    return run, calls, status_timeouts


def _never_sleep(_dt):
    raise AssertionError("should not have slept")


def test_none_when_installed_and_authenticated():
    run, calls, _ = _fake_run(token_rc=0, status_rcs=(0,))
    assert (
        ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=run, sleep=_never_sleep)
        is None
    )
    # happy path validated the token online exactly once, no retry
    assert calls.count(STATUS_CMD) == 1


def test_message_when_gh_missing():
    run, _, _ = _fake_run()
    msg = ghcheck.gh_problem(which=lambda _: None, run=run)
    assert msg and "not found" in msg


def test_does_not_probe_when_gh_missing():
    run, calls, _ = _fake_run()
    ghcheck.gh_problem(which=lambda _: None, run=run)
    assert calls == []


def test_logged_out_when_no_token_is_stored():
    # No stored token is the one unambiguous "logged out" — it's a local read, so it
    # stays reliable even when GitHub is unreachable. It must fail fast (no retry) and
    # never bother probing online status.
    run, calls, _ = _fake_run(token_rc=1, status_rcs=(0,))
    msg = ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=run, sleep=_never_sleep)
    assert msg and "gh auth login" in msg
    assert STATUS_CMD not in calls


def test_transient_validation_failure_is_ridden_out():
    # Token is present; online validation blips (as when the network isn't up yet
    # after the timer fires) then recovers. The preflight must retry and pass, not
    # abort on the first failure.
    run, calls, _ = _fake_run(token_rc=0, status_rcs=(1, 1, 0))
    slept = []
    assert (
        ghcheck.gh_problem(
            which=lambda _: "/usr/bin/gh",
            run=run,
            timeout_s=30,
            interval_s=3,
            sleep=slept.append,
            monotonic=lambda: 0.0,
        )
        is None
    )
    assert slept == [3, 3]  # slept between the two failed probes
    assert calls.count(STATUS_CMD) == 3


def test_proceeds_when_validation_never_recovers():
    # The regression this fix exists for: a stored, valid token that simply can't be
    # validated online (transient GitHub/network outage) must NOT be reported as
    # logged out. After exhausting the retry budget the preflight returns None so the
    # run proceeds — clones then fail per-project and an emailed summary is sent,
    # instead of a silent exit-2 with no email.
    run, calls, _ = _fake_run(token_rc=0, status_rcs=(1,))  # never succeeds
    clock = {"t": 0.0}
    slept = []

    def sleep(dt):
        slept.append(dt)
        clock["t"] += dt  # advance the fake clock as we wait

    result = ghcheck.gh_problem(
        which=lambda _: "/usr/bin/gh",
        run=run,
        timeout_s=10,
        interval_s=4,
        sleep=sleep,
        monotonic=lambda: clock["t"],
    )
    assert result is None  # proceeded, did not report _LOGGED_OUT
    # probes at t=0,4,8; the third wait is capped to the 2s left so it never oversleeps
    assert slept == [4, 4, 2]
    assert sum(slept) == 10  # total wait stays within timeout_s
    assert calls.count(STATUS_CMD) == 3


def test_wait_is_capped_so_a_slow_probe_never_overshoots_the_budget():
    # A probe that burns the whole remaining budget (rc 124 right at the deadline) must
    # not be followed by another interval sleep — otherwise the "best-effort timeout_s"
    # bound would blow out to timeout_s + interval_s. The post-probe wait is capped to
    # what's left, which here is nothing, so the loop returns without sleeping.
    clock = {"t": 0.0}
    slept = []

    def run(cmd, **kw):
        if cmd == TOKEN_CMD:
            return Completed(0, "", "")
        clock["t"] += kw[
            "timeout"
        ]  # a hung probe burns its whole timeout, then times out
        return Completed(124, "", "")

    def sleep(dt):
        slept.append(dt)
        clock["t"] += dt

    result = ghcheck.gh_problem(
        which=lambda _: "/usr/bin/gh",
        run=run,
        timeout_s=10,
        interval_s=4,
        sleep=sleep,
        monotonic=lambda: clock["t"],
    )
    assert result is None
    assert slept == []  # nothing left to wait, so no sleep tacked on
    assert clock["t"] == 10  # never overshot timeout_s


def test_each_online_probe_is_bounded_by_the_deadline():
    # A hung `gh auth status` (GitHub accepts the connection but never replies) must
    # not block past the budget: every probe is given the time remaining on the
    # deadline as its subprocess timeout, so the total wait stays bounded by timeout_s
    # even if the deadline check (only reached once run() returns) never would be.
    run, _, status_timeouts = _fake_run(token_rc=0, status_rcs=(1,))  # never succeeds
    clock = {"t": 0.0}

    def sleep(dt):
        clock["t"] += dt

    ghcheck.gh_problem(
        which=lambda _: "/usr/bin/gh",
        run=run,
        timeout_s=10,
        interval_s=4,
        sleep=sleep,
        monotonic=lambda: clock["t"],
    )
    # remaining budget at each probe: 10 (t=0), 6 (t=4), 2 (t=8) — always a positive,
    # shrinking bound, never None (which would be an unbounded subprocess).
    assert status_timeouts == [10, 6, 2]


def test_commands_are_scoped_to_the_active_github_account():
    run, calls, _ = _fake_run(token_rc=0, status_rcs=(0,))
    ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=run, sleep=_never_sleep)
    # token probe is a local, host-scoped read; the online check is pinned to the
    # active github.com account (the one ptt's later gh calls use) so a stale
    # Enterprise host or an expired inactive account can't fail this preflight.
    assert TOKEN_CMD in calls
    assert STATUS_CMD in calls

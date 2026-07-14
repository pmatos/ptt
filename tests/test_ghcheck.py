from ptt import ghcheck
from ptt.proc import Completed


def _ok(cmd, **kw):
    return Completed(0, "", "Logged in to github.com")


def _fail(cmd, **kw):
    return Completed(1, "", "You are not logged into any GitHub hosts.")


def test_none_when_installed_and_authenticated():
    assert ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=_ok) is None


def test_message_when_gh_missing():
    msg = ghcheck.gh_problem(which=lambda _: None, run=_ok)
    assert msg and "not found" in msg


def test_message_when_not_authenticated():
    msg = ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=_fail)
    assert msg and "gh auth login" in msg


def test_does_not_probe_auth_when_gh_missing():
    called = {"n": 0}

    def run(cmd, **kw):
        called["n"] += 1
        return Completed(0, "", "")

    ghcheck.gh_problem(which=lambda _: None, run=run)
    assert called["n"] == 0


def test_auth_check_is_scoped_to_github_com():
    seen = {}

    def run(cmd, **kw):
        seen["cmd"] = cmd
        return Completed(0, "", "Logged in to github.com")

    ghcheck.gh_problem(which=lambda _: "/usr/bin/gh", run=run)
    # A stale non-github.com host — or an expired inactive github.com account — must
    # not fail the preflight, so the probe is pinned to the active github.com account
    # (the one ptt's later gh calls use) rather than the all-hosts/all-accounts default.
    assert seen["cmd"] == [
        "gh",
        "auth",
        "status",
        "--hostname",
        "github.com",
        "--active",
    ]

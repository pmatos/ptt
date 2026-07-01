import json

from ptt import outcomes
from ptt import models as m


def snap(prs=None, issues=None):
    return {"prs": prs or {}, "issues": issues or {}}


def claim(action, status=m.Status.SUCCESS, url="u"):
    return m.Outcome(status=status, action=action, url=url, title="t", summary="s")


PR_POST = snap(prs={1: {"url": "https://github.com/x/pull/1", "headRefName": "ptt/x"}})
ISS_OPEN_POST = snap(
    issues={6: {"url": "https://github.com/x/issues/6", "state": "OPEN"}}
)


def test_confirmed_pr():
    r = outcomes.reconcile(claim(m.Action.PR), snap(), PR_POST, True, True, 0, False)
    assert r["status"] == m.Status.SUCCESS
    assert r["action"] == m.Action.PR
    assert r["verified"] is True
    assert r["source"] == m.Source.CLAUDE


def test_unverified_claim_kept_but_flagged():
    r = outcomes.reconcile(claim(m.Action.PR), snap(), snap(), True, True, 0, False)
    assert r["action"] == m.Action.PR
    assert r["verified"] is False
    assert r["source"] == m.Source.CLAUDE


def test_gh_observed_when_claim_says_none():
    r = outcomes.reconcile(
        claim(m.Action.NONE, status=m.Status.NO_ACTION, url=None),
        snap(),
        PR_POST,
        True,
        True,
        0,
        False,
    )
    assert r["action"] == m.Action.PR
    assert r["source"] == m.Source.GH
    assert r["verified"] is True
    assert r["status"] == m.Status.SUCCESS


def test_gh_observed_when_no_result_file():
    r = outcomes.reconcile(None, snap(), PR_POST, True, True, 0, False)
    assert r["action"] == m.Action.PR
    assert r["source"] == m.Source.GH


def test_missing_result_file_no_delta_is_error():
    r = outcomes.reconcile(None, snap(), snap(), True, True, 0, False)
    assert r["status"] == m.Status.ERROR
    assert "no result file" in r["reason"]


def test_claude_nonzero_no_file_uses_stderr_reason():
    r = outcomes.reconcile(
        None, snap(), snap(), True, True, 3, False, stderr_tail="boom happened"
    )
    assert r["status"] == m.Status.ERROR
    assert "boom" in r["reason"]


def test_timeout_is_error():
    r = outcomes.reconcile(claim(m.Action.PR), snap(), snap(), True, True, 124, True)
    assert r["status"] == m.Status.ERROR
    assert r["reason"] == "timeout"


def test_gh_snapshot_failure_is_error():
    r = outcomes.reconcile(claim(m.Action.PR), snap(), snap(), True, False, 0, False)
    assert r["status"] == m.Status.ERROR
    assert "gh snapshot failed" in r["reason"]


def test_issue_closed_confirmed():
    pre = snap(issues={5: {"url": "u5", "state": "OPEN"}})
    post = snap(issues={5: {"url": "u5", "state": "CLOSED"}})
    r = outcomes.reconcile(
        claim(m.Action.ISSUE_CLOSED), pre, post, True, True, 0, False
    )
    assert r["action"] == m.Action.ISSUE_CLOSED
    assert r["verified"] is True


def test_no_action_claim_kept():
    r = outcomes.reconcile(
        claim(m.Action.NONE, status=m.Status.NO_ACTION, url=None),
        snap(),
        snap(),
        True,
        True,
        0,
        False,
    )
    assert r["status"] == m.Status.NO_ACTION
    assert r["action"] == m.Action.NONE
    assert r["verified"] is True


# --- gh_snapshot / read_result_file ---


def test_gh_snapshot_reads_markers(fake_bin, tmp_path):
    wt = tmp_path / "wt"
    wt.mkdir()
    before, ok1 = outcomes.gh_snapshot(wt, tmp_path / "g.log")
    assert ok1 is True and before["prs"] == {}
    (wt / ".fake-pr").write_text("1")
    after, ok2 = outcomes.gh_snapshot(wt, tmp_path / "g.log")
    assert ok2 is True and 1 in after["prs"]


def test_gh_snapshot_command_failure_sets_ok_false(monkeypatch, tmp_path):
    monkeypatch.setattr(
        outcomes.proc, "run", lambda *a, **k: outcomes.proc.Completed(1, "", "fail")
    )
    snapshot, ok = outcomes.gh_snapshot(tmp_path, tmp_path / "g.log")
    assert ok is False


def test_read_result_file(tmp_path):
    (tmp_path / ".ptt-result.json").write_text(
        json.dumps(
            {
                "status": "success",
                "action": "pr",
                "url": "u",
                "title": "t",
                "summary": "s",
            }
        )
    )
    o = outcomes.read_result_file(tmp_path)
    assert o is not None
    assert o.action == m.Action.PR


def test_read_result_file_missing_or_garbage(tmp_path):
    assert outcomes.read_result_file(tmp_path) is None
    (tmp_path / ".ptt-result.json").write_text("{not json")
    assert outcomes.read_result_file(tmp_path) is None

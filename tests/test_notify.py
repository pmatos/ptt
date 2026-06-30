import json
from pathlib import Path

import pytest

from ptt import notify
from ptt import models as m


def _proj(name, status, action, verified=True, url="https://gh/pull/1",
          reason=None, log_dir="/log/x"):
    return m.ProjectResult(
        name=name, path="/p/" + name, status=status, action=action, url=url,
        title="Title " + name, summary="s", verified=verified, source=m.Source.CLAUDE,
        reason=reason, branch="ptt/x/1", duration_s=1.0, log_dir=log_dir,
    )


def _run(projects, status=m.Status.SUCCESS):
    return m.RunResult(routine="audit", run_id="20260630T050000Z", started_at="s",
                       ended_at="e", overall_status=status, projects=projects,
                       run_dir="/state/runs/audit/20260630T050000Z")


def _email(on):
    return m.EmailConfig(from_addr="ptt@x.com", to_addr="p@x.com", on=on,
                         postmark_token_env="PTT_POSTMARK_TOKEN")


def test_should_send_matrix():
    pr = _run([_proj("a", m.Status.SUCCESS, m.Action.PR)])
    none = _run([_proj("a", m.Status.NO_ACTION, m.Action.NONE)])
    fail = _run([_proj("a", m.Status.ERROR, m.Action.NONE)], status=m.Status.ERROR)
    assert notify.should_send(none, m.EmailOn.ALWAYS) is True
    assert notify.should_send(none, m.EmailOn.CHANGES) is False
    assert notify.should_send(pr, m.EmailOn.CHANGES) is True
    assert notify.should_send(none, m.EmailOn.FAILURES) is False
    assert notify.should_send(fail, m.EmailOn.FAILURES) is True


def test_build_subject_counts():
    run = _run([
        _proj("a", m.Status.SUCCESS, m.Action.PR),
        _proj("b", m.Status.SUCCESS, m.Action.ISSUE_OPENED),
        _proj("c", m.Status.ERROR, m.Action.NONE),
    ], status=m.Status.ERROR)
    s = notify.build_subject(run)
    assert "audit" in s and "1 PR" in s and "1 issue" in s and "1 failed" in s


def test_build_text_marks_actions_unverified_and_failures():
    run = _run([
        _proj("ok", m.Status.SUCCESS, m.Action.PR),
        _proj("maybe", m.Status.SUCCESS, m.Action.PR, verified=False),
        _proj("idle", m.Status.NO_ACTION, m.Action.NONE),
        _proj("bad", m.Status.ERROR, m.Action.NONE, reason="timeout", log_dir="/log/bad"),
    ], status=m.Status.ERROR)
    text = notify.build_text(run)
    assert "https://gh/pull/1" in text
    assert "(unverified)" in text
    assert "nothing to do" in text
    assert "timeout" in text and "/log/bad" in text


def test_send_posts_to_postmark_with_token_header(monkeypatch):
    captured = {}

    class FakeResp:
        status = 200
        def __enter__(self): return self
        def __exit__(self, *a): return False

    def fake_urlopen(req, *a, **k):
        captured["url"] = req.full_url
        captured["headers"] = req.headers
        captured["body"] = json.loads(req.data.decode())
        return FakeResp()

    monkeypatch.setattr(notify.urllib.request, "urlopen", fake_urlopen)
    notify.send("subj", "body", None, _email(m.EmailOn.ALWAYS), "SECRET-TOKEN")
    assert captured["url"] == "https://api.postmarkapp.com/email"
    # urllib title-cases header keys
    assert captured["headers"]["X-postmark-server-token"] == "SECRET-TOKEN"
    assert captured["body"]["From"] == "ptt@x.com"
    assert captured["body"]["Subject"] == "subj"


def test_send_raises_on_http_error(monkeypatch):
    def boom(req, *a, **k):
        raise notify.urllib.error.HTTPError(req.full_url, 422, "bad", {}, None)
    monkeypatch.setattr(notify.urllib.request, "urlopen", boom)
    with pytest.raises(Exception):
        notify.send("s", "b", None, _email(m.EmailOn.ALWAYS), "T")


def test_notify_retries_then_writes_marker(monkeypatch, tmp_path):
    calls = {"n": 0}
    def always_fail(*a, **k):
        calls["n"] += 1
        raise RuntimeError("nope")
    monkeypatch.setattr(notify, "send", always_fail)
    run = _run([_proj("a", m.Status.SUCCESS, m.Action.PR)])
    notify.notify(run, _email(m.EmailOn.ALWAYS), "T", tmp_path)  # must not raise
    assert calls["n"] == 2
    assert (tmp_path / ".email-failed").is_file()


def test_notify_skips_when_policy_not_met(monkeypatch, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(notify, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1))
    run = _run([_proj("a", m.Status.NO_ACTION, m.Action.NONE)])
    notify.notify(run, _email(m.EmailOn.FAILURES), "T", tmp_path)
    assert called["n"] == 0


def test_token_never_in_rendered_message():
    run = _run([_proj("a", m.Status.SUCCESS, m.Action.PR)])
    assert "SECRET" not in notify.build_subject(run)
    assert "SECRET" not in notify.build_text(run)

import pytest

from ptt import models as m
from ptt import notify


def _fake_smtp(record):
    """A stand-in for smtplib.SMTP / SMTP_SSL that records what send() does."""

    class _S:
        def __init__(self, host, port, *a, **k):
            record["host"] = host
            record["port"] = port
            record.setdefault("starttls", 0)
            record.setdefault("login", None)
            record.setdefault("sent", [])

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, *a, **k):
            record["starttls"] += 1

        def login(self, user, password):
            record["login"] = (user, password)

        def send_message(self, msg):
            record["sent"].append(msg)

    return _S


def _proj(
    name,
    status,
    action,
    verified=True,
    url="https://gh/pull/1",
    reason=None,
    log_dir="/log/x",
):
    return m.ProjectResult(
        name=name,
        path="/p/" + name,
        status=status,
        action=action,
        url=url,
        title="Title " + name,
        summary="s",
        verified=verified,
        source=m.Source.CLAUDE,
        reason=reason,
        branch="ptt/x/1",
        duration_s=1.0,
        log_dir=log_dir,
    )


def _run(projects, status=m.Status.SUCCESS):
    return m.RunResult(
        routine="audit",
        run_id="20260630T050000Z",
        started_at="s",
        ended_at="e",
        overall_status=status,
        projects=projects,
        run_dir="/state/runs/audit/20260630T050000Z",
    )


def _email(
    on,
    security=m.SmtpSecurity.STARTTLS,
    username="user",
    host="smtp.example.com",
    port=587,
):
    return m.EmailConfig(
        from_addr="ptt@x.com",
        to_addr="p@x.com",
        on=on,
        smtp_host=host,
        smtp_port=port,
        smtp_security=security,
        smtp_username=username,
        smtp_password_env="PTT_SMTP_PASSWORD",
    )


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
    run = _run(
        [
            _proj("a", m.Status.SUCCESS, m.Action.PR),
            _proj("b", m.Status.SUCCESS, m.Action.ISSUE_OPENED),
            _proj("c", m.Status.ERROR, m.Action.NONE),
        ],
        status=m.Status.ERROR,
    )
    s = notify.build_subject(run)
    assert "audit" in s and "1 PR" in s and "1 issue" in s and "1 failed" in s


def test_build_text_marks_actions_unverified_and_failures():
    run = _run(
        [
            _proj("ok", m.Status.SUCCESS, m.Action.PR),
            _proj("maybe", m.Status.SUCCESS, m.Action.PR, verified=False),
            _proj("idle", m.Status.NO_ACTION, m.Action.NONE),
            _proj(
                "bad",
                m.Status.ERROR,
                m.Action.NONE,
                reason="timeout",
                log_dir="/log/bad",
            ),
        ],
        status=m.Status.ERROR,
    )
    text = notify.build_text(run)
    assert "https://gh/pull/1" in text
    assert "(unverified)" in text
    assert "nothing to do" in text
    assert "timeout" in text and "/log/bad" in text


def test_build_text_error_with_claim_shows_claimed_pr():
    # gh snapshot failed, but Claude's own claim survived reconcile() — the email
    # should surface it instead of leaving the reader with a bare "failed" line.
    run = _run(
        [
            _proj(
                "flaky",
                m.Status.ERROR,
                m.Action.PR,
                reason="gh snapshot failed",
                log_dir="/log/flaky",
            ),
        ],
        status=m.Status.ERROR,
    )
    text = notify.build_text(run)
    assert "gh snapshot failed" in text
    assert "claude claims" in text
    assert "https://gh/pull/1" in text
    assert "Title flaky" in text


def test_send_starttls_logs_in_and_sends(monkeypatch):
    rec = {}
    monkeypatch.setattr(notify.smtplib, "SMTP", _fake_smtp(rec))
    notify.send(
        "subj",
        "body-text",
        "<pre>body-text</pre>",
        _email(m.EmailOn.ALWAYS),
        "SECRET-PW",
    )
    assert rec["host"] == "smtp.example.com"
    assert rec["port"] == 587
    assert rec["starttls"] == 1
    assert rec["login"] == ("user", "SECRET-PW")
    assert len(rec["sent"]) == 1
    msg = rec["sent"][0]
    assert msg["From"] == "ptt@x.com"
    assert msg["To"] == "p@x.com"
    assert msg["Subject"] == "subj"
    raw = msg.as_string()
    assert "body-text" in raw
    assert "SECRET-PW" not in raw  # the password never rides in the message


def test_send_ssl_uses_smtp_ssl_without_starttls(monkeypatch):
    rec = {}
    monkeypatch.setattr(notify.smtplib, "SMTP_SSL", _fake_smtp(rec))
    cfg = _email(m.EmailOn.ALWAYS, security=m.SmtpSecurity.SSL, port=465)
    notify.send("s", "b", None, cfg, "PW")
    assert rec["port"] == 465
    assert rec["starttls"] == 0
    assert rec["login"] == ("user", "PW")
    assert len(rec["sent"]) == 1


def test_send_none_without_auth_skips_starttls_and_login(monkeypatch):
    rec = {}
    monkeypatch.setattr(notify.smtplib, "SMTP", _fake_smtp(rec))
    cfg = _email(
        m.EmailOn.ALWAYS,
        security=m.SmtpSecurity.NONE,
        username=None,
        host="127.0.0.1",
        port=25,
    )
    notify.send("s", "b", None, cfg, None)
    assert rec["starttls"] == 0
    assert rec["login"] is None
    assert len(rec["sent"]) == 1


def test_send_username_but_no_password_skips_login(monkeypatch):
    rec = {}
    monkeypatch.setattr(notify.smtplib, "SMTP", _fake_smtp(rec))
    # username configured but no password available: must not attempt auth
    notify.send("s", "b", None, _email(m.EmailOn.ALWAYS), None)
    assert rec["login"] is None
    assert len(rec["sent"]) == 1


def test_send_raises_on_smtp_error(monkeypatch):
    def boom(*a, **k):
        raise notify.smtplib.SMTPException("nope")

    monkeypatch.setattr(notify.smtplib, "SMTP", boom)
    with pytest.raises(notify.smtplib.SMTPException):
        notify.send("s", "b", None, _email(m.EmailOn.ALWAYS), "PW")


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


def test_notify_missing_password_writes_marker_naming_env(tmp_path):
    run = _run([_proj("a", m.Status.SUCCESS, m.Action.PR)])
    notify.notify(run, _email(m.EmailOn.ALWAYS), None, tmp_path)
    txt = (tmp_path / ".email-failed").read_text()
    assert "PTT_SMTP_PASSWORD" in txt
    assert "postmark" not in txt.lower()


def test_notify_skips_when_policy_not_met(monkeypatch, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(
        notify, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    run = _run([_proj("a", m.Status.NO_ACTION, m.Action.NONE)])
    notify.notify(run, _email(m.EmailOn.FAILURES), "T", tmp_path)
    assert called["n"] == 0


def test_token_never_in_rendered_message():
    run = _run([_proj("a", m.Status.SUCCESS, m.Action.PR)])
    assert "SECRET" not in notify.build_subject(run)
    assert "SECRET" not in notify.build_text(run)


# ---------- command routines ----------


def _cmd_run(status=m.Status.SUCCESS, exit_code=0, stdout_len=10, reason=None):
    return m.CommandRunResult(
        routine="mail-digest",
        run_id="20260716T070000Z",
        started_at="s",
        ended_at="e",
        status=status,
        exit_code=exit_code,
        stdout_len=stdout_len,
        reason=reason,
        command=["mail_digest.py"],
        duration_s=1.0,
        run_dir="/state/runs/mail-digest/20260716T070000Z",
    )


def test_build_command_subject_ok_and_failed():
    assert notify.build_command_subject(_cmd_run()) == "[ptt] mail-digest — ok"
    failed = notify.build_command_subject(
        _cmd_run(status=m.Status.ERROR, exit_code=3, reason="exit 3")
    )
    assert "failed" in failed and "exit 3" in failed
    timed = notify.build_command_subject(
        _cmd_run(status=m.Status.ERROR, exit_code=124, reason="timeout")
    )
    assert "failed" in timed and "timeout" in timed


def test_build_command_body_has_stdout_and_footer():
    run = _cmd_run()
    body = notify.build_command_body(run, "# Digest\nthe body")
    assert "the body" in body
    assert run.run_id in body and run.run_dir in body


def test_should_send_command_matrix():
    ok = _cmd_run(status=m.Status.SUCCESS, stdout_len=5)
    empty = _cmd_run(status=m.Status.SUCCESS, stdout_len=0)
    fail = _cmd_run(status=m.Status.ERROR, exit_code=1, stdout_len=0, reason="exit 1")
    assert notify.should_send_command(empty, m.EmailOn.ALWAYS) is True
    assert notify.should_send_command(ok, m.EmailOn.CHANGES) is True
    assert notify.should_send_command(empty, m.EmailOn.CHANGES) is False
    assert notify.should_send_command(ok, m.EmailOn.FAILURES) is False
    assert notify.should_send_command(fail, m.EmailOn.FAILURES) is True


def test_md_to_html_covers_all_branches():
    md = (
        "# Title\n"
        "## Section\n"
        "### Sub\n"
        "- one\n"
        "- two\n"
        "\n"
        "A **bold** and *italic* line with <script> & ampersand\n"
    )
    html = notify.md_to_html(md)
    assert "<h1>" in html
    assert "<h2" in html and "<h3" in html
    assert "<ul>" in html and "</ul>" in html
    assert html.count("<li>") == 2
    assert "<strong>bold</strong>" in html
    assert "<em>italic</em>" in html
    assert "<p" in html
    # dangerous markup is escaped, never emitted raw
    assert "&lt;script&gt;" in html and "&amp;" in html
    assert "<script>" not in html


def test_build_command_html_text_escapes_html():
    # Text format must not let arbitrary command stdout inject live HTML into the
    # email's HTML alternative (e.g. a copied <img onerror=...> from a mail digest).
    out = notify.build_command_html(
        "<img src=x onerror=alert(1)> & done", m.BodyFormat.TEXT
    )
    assert "<img" not in out
    assert "&lt;img" in out and "&amp;" in out


def test_md_to_html_closes_open_list_before_every_block():
    # An open list is closed exactly once whether the next line is a heading of any
    # level, a paragraph, or the end of input.
    assert notify.md_to_html("- a\n### h").count("</ul>") == 1
    assert notify.md_to_html("- a\n## h").count("</ul>") == 1
    assert notify.md_to_html("- a\n# h").count("</ul>") == 1
    assert notify.md_to_html("- a\nplain para").count("</ul>") == 1
    at_eof = notify.md_to_html("- a\n- b")  # list runs to the end of the input
    assert at_eof.count("<li>") == 2 and at_eof.count("</ul>") == 1


def test_notify_command_suppressed_when_notify_false(monkeypatch, tmp_path):
    called = {"n": 0}
    monkeypatch.setattr(
        notify, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    notify.notify_command(
        _cmd_run(),
        False,  # notify disabled
        m.BodyFormat.TEXT,
        _email(m.EmailOn.ALWAYS),
        "PW",
        tmp_path,
        "body",
    )
    assert called["n"] == 0
    assert not (tmp_path / ".email-failed").exists()


def test_notify_command_missing_password_writes_marker(tmp_path):
    notify.notify_command(
        _cmd_run(),
        True,
        m.BodyFormat.TEXT,
        _email(m.EmailOn.ALWAYS),
        None,  # no password
        tmp_path,
        "body",
    )
    txt = (tmp_path / ".email-failed").read_text()
    assert "PTT_SMTP_PASSWORD" in txt


def test_notify_command_retries_then_marker(monkeypatch, tmp_path):
    calls = {"n": 0}

    def always_fail(*a, **k):
        calls["n"] += 1
        raise RuntimeError("nope")

    monkeypatch.setattr(notify, "send", always_fail)
    notify.notify_command(
        _cmd_run(),
        True,
        m.BodyFormat.TEXT,
        _email(m.EmailOn.ALWAYS),
        "PW",
        tmp_path,
        "b",
    )
    assert calls["n"] == 2
    assert (tmp_path / ".email-failed").is_file()


def test_notify_command_markdown_renders_html(monkeypatch, tmp_path):
    captured = {}
    monkeypatch.setattr(
        notify,
        "send",
        lambda subj, text, html, cfg, pw: captured.update(text=text, html=html),
    )
    notify.notify_command(
        _cmd_run(),
        True,
        m.BodyFormat.MARKDOWN,
        _email(m.EmailOn.ALWAYS),
        "PW",
        tmp_path,
        "## Heading\nbody",
    )
    assert "<h2" in captured["html"]  # markdown rendered, not <pre>-wrapped
    assert "## Heading" in captured["text"]  # plain-text alternative keeps the source

import json
import os
import sys

from ptt import cli, ghcheck, netcheck, notify, runner


def write_config(cfg_home, github_repo, tmp_path, name="audit", enabled=True):
    d = cfg_home / "ptt"
    (d / "routines").mkdir(parents=True, exist_ok=True)
    (d / "config.toml").write_text(
        '[email]\nfrom="a@x.com"\nto="b@x.com"\non="always"\n'
        'smtp_host="smtp.example.com"\nsmtp_username="user"\n'
    )
    prompt = tmp_path / "p.md"
    prompt.write_text("Do the audit.")
    (d / "routines" / f"{name}.toml").write_text(
        f'name="{name}"\nenabled={"true" if enabled else "false"}\n'
        f'prompt="{prompt}"\nschedule="Mon..Fri 05:00"\nprojects=["{github_repo}"]\n'
    )


def write_command_config(cfg_home, tmp_path, argv, name="mail-digest", notify=True):
    d = cfg_home / "ptt"
    (d / "routines").mkdir(parents=True, exist_ok=True)
    (d / "config.toml").write_text(
        '[email]\nfrom="a@x.com"\nto="b@x.com"\non="always"\n'
        'smtp_host="smtp.example.com"\nsmtp_username="user"\n'
    )
    argv_toml = ", ".join(json.dumps(a) for a in argv)
    (d / "routines" / f"{name}.toml").write_text(
        f'name="{name}"\nschedule="*-*-* 07:00:00"\n'
        f"command=[{argv_toml}]\nnotify={'true' if notify else 'false'}\n"
    )


def test_run_dispatches_command_routine(tmp_xdg, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    probed = {"n": 0}
    monkeypatch.setattr(
        ghcheck,
        "gh_problem",
        lambda: probed.__setitem__("n", probed["n"] + 1) or "gh bad",
    )
    write_command_config(
        tmp_xdg["config"], tmp_path, [sys.executable, "-c", "print('HELLO DIGEST')"]
    )
    rc = cli.main(["run", "mail-digest"])
    assert rc == 0
    out = capsys.readouterr().out
    assert "[ptt] mail-digest — ok" in out
    assert "HELLO DIGEST" in out
    assert probed["n"] == 0  # gh preflight skipped for command routines


def test_run_command_routine_rejects_project_flag(tmp_xdg, tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        runner,
        "run_command_routine",
        lambda *a, **k: called.__setitem__("n", called["n"] + 1),
    )
    write_command_config(
        tmp_xdg["config"], tmp_path, [sys.executable, "-c", "print('x')"]
    )
    assert cli.main(["run", "mail-digest", "--project", "foo"]) == 2
    assert called["n"] == 0


def test_logs_shows_command_output(tmp_xdg, capsys):
    from ptt import config

    rd = config.state_home() / "runs" / "mail-digest" / "20260716T070000Z"
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(
        json.dumps({"routine": "mail-digest", "status": "success"})
    )
    (rd / "command.txt").write_text("mail_digest.py --day yesterday\n")
    (rd / "command.stdout.log").write_text("THE DIGEST BODY\n")
    (rd / "command.stderr.log").write_text("")
    assert cli.main(["logs", "mail-digest"]) == 0
    out = capsys.readouterr().out
    assert "THE DIGEST BODY" in out
    assert "command.txt" in out


def test_logs_command_output_tolerates_non_utf8(tmp_xdg, capsys):
    from ptt import config

    rd = config.state_home() / "runs" / "mail-digest" / "20260716T110000Z"
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(
        json.dumps({"routine": "mail-digest", "status": "success"})
    )
    (rd / "command.txt").write_text("mail_digest.py\n")
    (rd / "command.stdout.log").write_bytes(b"\xff\xfe digest body\n")
    (rd / "command.stderr.log").write_bytes(b"")
    # non-UTF-8 bytes must not crash `ptt logs` with a UnicodeDecodeError
    assert cli.main(["logs", "mail-digest"]) == 0
    assert "digest body" in capsys.readouterr().out


def test_run_command_end_to_end(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PTT_FAKE_MODE", "none")
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    rc = cli.main(["run", "audit"])
    assert rc == 0
    assert "[ptt] audit" in capsys.readouterr().out


def test_run_command_merges_baked_ptt_path(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch
):
    # `ptt run` merges the baked PTT_PATH into PATH so claude/git/gh resolve even when
    # the unit's PATH was left stale/sparse by an env-file PATH= override (issue #15).
    monkeypatch.setenv("PTT_FAKE_MODE", "none")
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_PATH", "/baked/sentinel/bin")
    assert cli.main(["run", "audit"]) == 0
    assert os.environ["PATH"].split(os.pathsep)[0] == "/baked/sentinel/bin"


def test_run_aborts_cleanly_when_gh_not_authenticated(
    github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setattr(
        ghcheck, "gh_problem", lambda: "gh is not authenticated — run `gh auth login`"
    )
    called = {"n": 0}
    monkeypatch.setattr(
        runner, "run_routine", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    assert cli.main(["run", "audit"]) == 2
    assert called["n"] == 0
    assert "gh is not authenticated" in capsys.readouterr().err


def test_run_skips_gh_preflight_for_disabled_routine(
    github_repo, tmp_xdg, tmp_path, monkeypatch
):
    # A paused routine does no gh work (run_routine exits 0), so the preflight must
    # not run — otherwise a logged-out gh would wrongly fail the timer-fired run.
    write_config(
        tmp_xdg["config"], github_repo, tmp_path, name="cleanup", enabled=False
    )
    probed = {"n": 0}

    def fake_problem():
        probed["n"] += 1
        return "gh is not authenticated — run `gh auth login`"

    monkeypatch.setattr(ghcheck, "gh_problem", fake_problem)
    assert cli.main(["run", "cleanup"]) == 0
    assert probed["n"] == 0


def test_run_force_preflights_disabled_routine(
    github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    # --force makes a disabled routine actually run, so the preflight applies again.
    write_config(
        tmp_xdg["config"], github_repo, tmp_path, name="cleanup", enabled=False
    )
    monkeypatch.setattr(
        ghcheck, "gh_problem", lambda: "gh is not authenticated — run `gh auth login`"
    )
    called = {"n": 0}
    monkeypatch.setattr(
        runner, "run_routine", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    assert cli.main(["run", "cleanup", "--force"]) == 2
    assert called["n"] == 0
    assert "gh is not authenticated" in capsys.readouterr().err


def test_doctor_ok(fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    assert cli.main(["doctor"]) == 0


def test_doctor_command_only_passes_without_git_tools(tmp_xdg, tmp_path, monkeypatch):
    # A command-only install needs no claude/git/gh; doctor must not hard-fail on them.
    write_command_config(
        tmp_xdg["config"], tmp_path, [sys.executable, "-c", "print('x')"]
    )
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("PATH", "")  # nothing resolvable by bare name
    assert cli.main(["doctor"]) == 0


def test_doctor_flags_missing_command_exe(tmp_xdg, tmp_path, monkeypatch):
    write_command_config(tmp_xdg["config"], tmp_path, ["/nonexistent/mail_digest.py"])
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("PATH", "")
    assert cli.main(["doctor"]) != 0


def test_doctor_flags_non_executable_command(tmp_xdg, tmp_path, monkeypatch):
    # A configured command path that exists but isn't executable would fail at
    # Popen; doctor must catch it (os.access X_OK), not pass it on Path.is_file().
    script = tmp_path / "not_exec.py"
    script.write_text("print('hi')\n")
    script.chmod(0o644)
    write_command_config(tmp_xdg["config"], tmp_path, [str(script)])
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("PATH", "")
    assert cli.main(["doctor"]) != 0


def test_doctor_project_routine_still_requires_git_tools(
    github_repo, tmp_xdg, tmp_path, monkeypatch
):
    # With a project routine configured, missing claude/git/gh must still fail doctor.
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    monkeypatch.setenv("PATH", "")
    assert cli.main(["doctor"]) != 0


def test_doctor_missing_password_fails(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.delenv("PTT_SMTP_PASSWORD", raising=False)
    assert cli.main(["doctor"]) != 0


def test_doctor_reports_smtp_password_line(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "smtp password ($PTT_SMTP_PASSWORD)" in out
    assert "postmark" not in out.lower()


def test_doctor_hints_to_load_env_file_when_password_missing(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    env_file = tmp_xdg["config"] / "ptt" / "env"
    env_file.write_text("PTT_SMTP_PASSWORD=pw\n")
    monkeypatch.delenv("PTT_SMTP_PASSWORD", raising=False)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert str(env_file) in out
    assert f"source {env_file}" in out


def test_doctor_no_env_hint_when_env_file_absent(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.delenv("PTT_SMTP_PASSWORD", raising=False)
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "source" not in out


def test_doctor_no_env_hint_when_password_present(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    (tmp_xdg["config"] / "ptt" / "env").write_text("PTT_SMTP_PASSWORD=pw\n")
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    cli.main(["doctor"])
    out = capsys.readouterr().out
    assert "source" not in out


def test_logs_prints_latest_run(tmp_xdg, capsys):
    from ptt import config

    rd = config.state_home() / "runs" / "audit" / "20260630T050000Z"
    rd.mkdir(parents=True)
    (rd / "run.json").write_text(
        json.dumps({"routine": "audit", "overall_status": "success"})
    )
    rc = cli.main(["logs", "audit"])
    assert rc == 0
    assert "success" in capsys.readouterr().out


def test_logs_no_runs_returns_error(tmp_xdg):
    assert cli.main(["logs", "ghost"]) != 0


def test_test_email_fails_fast_without_password(
    tmp_xdg, github_repo, tmp_path, monkeypatch
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.delenv("PTT_SMTP_PASSWORD", raising=False)
    sent = {"n": 0}
    monkeypatch.setattr(
        notify, "send", lambda *a, **k: sent.__setitem__("n", sent["n"] + 1)
    )
    assert cli.main(["test-email"]) != 0
    assert sent["n"] == 0


def test_test_email_sends_provider_neutral_body(
    tmp_xdg, github_repo, tmp_path, monkeypatch
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    captured = {}

    def fake_send(subject, text, html, email_cfg, password):
        captured["text"] = text
        captured["password"] = password

    monkeypatch.setattr(notify, "send", fake_send)
    assert cli.main(["test-email"]) == 0
    assert "postmark" not in captured["text"].lower()
    assert captured["password"] == "pw"


def test_wait_online_returns_zero_when_host_resolves(monkeypatch):
    monkeypatch.setattr(netcheck, "wait_online", lambda host, timeout: True)
    assert cli.main(["wait-online"]) == 0


def test_wait_online_returns_one_and_warns_on_give_up(monkeypatch, capsys):
    monkeypatch.setattr(netcheck, "wait_online", lambda host, timeout: False)
    assert cli.main(["wait-online", "--host", "git.example", "--timeout", "5"]) == 1
    assert "did not resolve" in capsys.readouterr().err


def test_wait_online_passes_host_and_timeout_through(monkeypatch):
    seen = {}

    def fake(host, timeout):
        seen["host"] = host
        seen["timeout"] = timeout
        return True

    monkeypatch.setattr(netcheck, "wait_online", fake)
    cli.main(["wait-online", "--host", "git.example", "--timeout", "7"])
    assert seen == {"host": "git.example", "timeout": 7.0}


def test_list_shows_routines(tmp_xdg, github_repo, tmp_path, capsys):
    write_config(tmp_xdg["config"], github_repo, tmp_path, name="audit")
    write_config(
        tmp_xdg["config"], github_repo, tmp_path, name="cleanup", enabled=False
    )
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "audit" in out and "cleanup" in out

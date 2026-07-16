import sys
from pathlib import Path

from ptt import command, logstore, notify, runner
from ptt import models as m
from tests.test_runner import make_global


def make_cmd_routine(
    tmp_path,
    argv,
    *,
    name="mail-digest",
    enabled=True,
    notify=True,
    body_format=m.BodyFormat.TEXT,
    timeout=30,
):
    return m.CommandRoutine(
        name=name,
        description="",
        enabled=enabled,
        schedule="*-*-* 07:00:00",
        command=argv,
        work_dir=tmp_path / "cmd-work",
        timeout_minutes=timeout,
        body_format=body_format,
        notify=notify,
    )


def test_success_writes_logs_and_emails(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    captured = {}
    monkeypatch.setattr(
        notify, "send", lambda subj, text, html, cfg, pw: captured.update(text=text)
    )
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", "print('DIGEST LINE')"])
    run = runner.run_command_routine(r, make_global())

    assert run.status == m.Status.SUCCESS
    assert run.exit_code == 0
    assert runner.command_exit_code(run) == 0
    rd = Path(run.run_dir)
    assert "DIGEST LINE" in logstore.command_stdout_path(rd).read_text()
    assert (rd / "command.txt").is_file()
    assert (rd / "run.json").is_file()
    assert "DIGEST LINE" in captured["text"]
    # per-run scratch cwd is cleaned up
    assert not (r.work_dir / run.run_id).exists()


def test_nonzero_exit_is_error(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", "import sys; sys.exit(3)"])
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.ERROR
    assert run.exit_code == 3
    assert run.reason == "exit 3"
    assert runner.command_exit_code(run) == 1


def test_disabled_is_noop(tmp_xdg, tmp_path, monkeypatch):
    called = {"n": 0}
    monkeypatch.setattr(
        notify, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", "print('x')"], enabled=False)
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.SUCCESS
    assert run.run_id == ""
    assert run.run_dir == ""
    assert called["n"] == 0
    # no run dir created under the state home
    assert not (tmp_xdg["state"] / "ptt" / "runs" / "mail-digest").exists()


def test_disabled_with_force_runs(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    r = make_cmd_routine(
        tmp_path, [sys.executable, "-c", "print('forced')"], enabled=False
    )
    run = runner.run_command_routine(r, make_global(), force=True)
    assert run.status == m.Status.SUCCESS
    assert run.run_id != ""


def test_timeout_is_error(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)

    def fake_run_command(argv, cwd, stdout_path, stderr_path, timeout_s, env=None):
        Path(stdout_path).write_text("partial output\n")
        return 124, True

    monkeypatch.setattr(command, "run_command", fake_run_command)
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", "pass"])
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.ERROR
    assert run.reason == "timeout"
    assert runner.command_exit_code(run) == 1


def test_notify_false_skips_email(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    called = {"n": 0}
    monkeypatch.setattr(
        notify, "send", lambda *a, **k: called.__setitem__("n", called["n"] + 1)
    )
    r = make_cmd_routine(
        tmp_path, [sys.executable, "-c", "print('quiet')"], notify=False
    )
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.SUCCESS
    assert called["n"] == 0  # ptt stayed silent
    # but the run was still recorded
    assert "quiet" in logstore.command_stdout_path(Path(run.run_dir)).read_text()


def test_unlaunchable_command_is_clean_error(tmp_xdg, tmp_path, monkeypatch):
    # A command that can't even start (missing/typo'd exe) must become a clean ERROR
    # outcome with run.json written — never a raw traceback out of the run.
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    r = make_cmd_routine(tmp_path, ["/nonexistent/mail_digest_xyz"])
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.ERROR
    assert runner.command_exit_code(run) == 1
    assert "could not run command" in (run.reason or "")
    assert (Path(run.run_dir) / "run.json").is_file()


def test_scratch_dir_removed_even_when_command_writes_to_it(
    tmp_xdg, tmp_path, monkeypatch
):
    # The throwaway cwd must be removed even if the command left files in it, so
    # private digest artifacts don't accumulate under work_dir.
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    code = "open('litter.txt', 'w').close(); print('done')"
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", code])
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.SUCCESS
    assert not (r.work_dir / run.run_id).exists()


def test_non_utf8_stdout_does_not_crash(tmp_xdg, tmp_path, monkeypatch):
    # Non-UTF-8 bytes on stdout must not fail an otherwise successful run.
    monkeypatch.setattr(notify, "send", lambda *a, **k: None)
    code = "import sys; sys.stdout.buffer.write(b'\\xff\\xfe digest'); sys.exit(0)"
    r = make_cmd_routine(tmp_path, [sys.executable, "-c", code])
    run = runner.run_command_routine(r, make_global())
    assert run.status == m.Status.SUCCESS
    assert run.stdout_len > 0

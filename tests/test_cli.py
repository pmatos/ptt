import json

from ptt import cli
from ptt import notify


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


def test_run_command_end_to_end(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv("PTT_FAKE_MODE", "none")
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    rc = cli.main(["run", "audit"])
    assert rc == 0
    assert "[ptt] audit" in capsys.readouterr().out


def test_validate_ok(fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    assert cli.main(["validate"]) == 0


def test_validate_missing_password_fails(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.delenv("PTT_SMTP_PASSWORD", raising=False)
    assert cli.main(["validate"]) != 0


def test_validate_reports_smtp_password_line(
    fake_bin, github_repo, tmp_xdg, tmp_path, monkeypatch, capsys
):
    write_config(tmp_xdg["config"], github_repo, tmp_path)
    monkeypatch.setenv("PTT_SMTP_PASSWORD", "pw")
    cli.main(["validate"])
    out = capsys.readouterr().out
    assert "smtp password ($PTT_SMTP_PASSWORD)" in out
    assert "postmark" not in out.lower()


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


def test_list_shows_routines(tmp_xdg, github_repo, tmp_path, capsys):
    write_config(tmp_xdg["config"], github_repo, tmp_path, name="audit")
    write_config(
        tmp_xdg["config"], github_repo, tmp_path, name="cleanup", enabled=False
    )
    assert cli.main(["list"]) == 0
    out = capsys.readouterr().out
    assert "audit" in out and "cleanup" in out

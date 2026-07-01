from pathlib import Path

from ptt import claude
from ptt import models as m


def _routine(permission_mode=m.PermissionMode.BYPASS, model=None):
    return m.Routine(
        name="audit",
        description="",
        enabled=True,
        prompt=Path("/x/p.md"),
        schedule="Mon..Fri 05:00",
        projects=[Path("/x/a")],
        base_branch="main",
        permission_mode=permission_mode,
        model=model,
        timeout_minutes=30,
        work_dir=Path("/x/work"),
    )


def test_build_prompt_appends_result_footer():
    out = claude.build_prompt("Do the audit.")
    assert "Do the audit." in out
    assert ".ptt-result.json" in out
    for field in ("status", "action", "url", "title", "summary"):
        assert field in out


def test_build_argv_bypass_uses_dangerous_flag():
    argv = claude.build_argv(_routine(m.PermissionMode.BYPASS))
    assert argv[0] == "claude"
    assert "-p" in argv
    assert "--output-format" in argv and "stream-json" in argv
    assert "--dangerously-skip-permissions" in argv
    assert "--permission-mode" not in argv


def test_build_argv_accept_edits_uses_permission_mode():
    argv = claude.build_argv(_routine(m.PermissionMode.ACCEPT_EDITS))
    assert "--permission-mode" in argv
    assert "acceptEdits" in argv
    assert "--dangerously-skip-permissions" not in argv


def test_build_argv_model_present_and_absent():
    assert "--model" not in claude.build_argv(_routine(model=None))
    argv = claude.build_argv(_routine(model="claude-opus-4-8"))
    assert "--model" in argv and "claude-opus-4-8" in argv


def test_run_claude_success_writes_result_and_logs(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    out, err = tmp_path / "o.jsonl", tmp_path / "e.log"
    rc, timed_out = claude.run_claude(_routine(), wt, "prompt", out, err, timeout_s=10)
    assert rc == 0 and timed_out is False
    assert (wt / ".ptt-result.json").is_file()
    assert out.read_text().strip()  # stream-json captured


def test_run_claude_error_returns_exit_code(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "error")
    wt = tmp_path / "wt"
    wt.mkdir()
    rc, timed_out = claude.run_claude(
        _routine(), wt, "prompt", tmp_path / "o", tmp_path / "e", timeout_s=10
    )
    assert rc == 3 and timed_out is False


def test_run_claude_timeout_is_killed(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "timeout")
    wt = tmp_path / "wt"
    wt.mkdir()
    rc, timed_out = claude.run_claude(
        _routine(), wt, "prompt", tmp_path / "o", tmp_path / "e", timeout_s=0.5
    )
    assert rc == 124 and timed_out is True

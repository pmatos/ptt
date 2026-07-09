import json
from pathlib import Path

from ptt import claude
from ptt import models as m


def _routine(permission_mode=m.PermissionMode.BYPASS, model=None, effort=None):
    return m.Routine(
        name="audit",
        description="",
        enabled=True,
        prompt=Path("/x/p.md"),
        schedule="Mon..Fri 05:00",
        projects=[
            m.ProjectSpec(raw="/x/a", is_remote=False, location="/x/a", name="a")
        ],
        base_branch="main",
        permission_mode=permission_mode,
        model=model,
        effort=effort,
        timeout_minutes=30,
        work_dir=Path("/x/work"),
    )


def test_build_prompt_appends_result_footer():
    out = claude.build_prompt("Do the audit.")
    assert "Do the audit." in out
    # The result contract is enforced by --json-schema, so the footer describes the
    # fields but no longer tells the model to hand-write a result file.
    assert ".ptt-result.json" not in out
    for field in ("status", "action", "url", "title", "summary"):
        assert field in out


def test_build_prompt_warns_against_background_deferral():
    out = claude.build_prompt("Do the audit.").lower()
    # The run is one-shot: Claude must not defer to background work expecting a
    # re-invocation that never comes, and must verify synchronously.
    assert "re-invoked" in out
    assert "background" in out
    assert "synchronously" in out


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


def test_build_argv_forces_structured_output_schema():
    argv = claude.build_argv(_routine())
    assert "--json-schema" in argv
    schema = json.loads(argv[argv.index("--json-schema") + 1])
    assert schema["type"] == "object"
    assert set(schema["required"]) == {"status", "action", "url", "title", "summary"}
    assert set(schema["properties"]["status"]["enum"]) == {s.value for s in m.Status}
    assert set(schema["properties"]["action"]["enum"]) == {a.value for a in m.Action}


def test_build_argv_disallows_schedule_and_wait_tools():
    argv = claude.build_argv(_routine())
    assert "--disallowedTools" in argv
    i = argv.index("--disallowedTools")
    # Variadic flag is last, so the tool names run to the end of argv.
    assert argv[i + 1 :] == ["ScheduleWakeup", "Monitor", "CronCreate"]


def test_build_argv_model_present_and_absent():
    assert "--model" not in claude.build_argv(_routine(model=None))
    argv = claude.build_argv(_routine(model="claude-opus-4-8"))
    assert "--model" in argv and "claude-opus-4-8" in argv


def test_build_argv_effort_present_and_absent():
    assert "--effort" not in claude.build_argv(_routine(effort=None))
    argv = claude.build_argv(_routine(effort=m.Effort.HIGH))
    assert "--effort" in argv and "high" in argv


def test_run_claude_success_emits_structured_output_and_logs(
    fake_bin, tmp_path, monkeypatch
):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    out, err = tmp_path / "o.jsonl", tmp_path / "e.log"
    rc, timed_out = claude.run_claude(_routine(), wt, "prompt", out, err, timeout_s=10)
    assert rc == 0 and timed_out is False
    assert not (wt / ".ptt-result.json").exists()  # no hand-written file anymore
    assert "structured_output" in out.read_text()  # result delivered via stream-json


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

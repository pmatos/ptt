import json
from pathlib import Path

from ptt import claude
from ptt import models as m


class _Sleeper:
    """Records backoff delays instead of sleeping, so retry tests stay instant."""

    def __init__(self):
        self.delays = []

    def __call__(self, delay):
        self.delays.append(delay)


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
    assert ".ptt-result.json" in out
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


def test_build_argv_model_present_and_absent():
    assert "--model" not in claude.build_argv(_routine(model=None))
    argv = claude.build_argv(_routine(model="claude-opus-4-8"))
    assert "--model" in argv and "claude-opus-4-8" in argv


def test_build_argv_effort_present_and_absent():
    assert "--effort" not in claude.build_argv(_routine(effort=None))
    argv = claude.build_argv(_routine(effort=m.Effort.HIGH))
    assert "--effort" in argv and "high" in argv


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
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o",
        tmp_path / "e",
        timeout_s=0.5,
        sleep=sleeper,
    )
    assert rc == 124 and timed_out is True
    assert sleeper.delays == []  # a timeout is never retried


def test_backoff_delay_is_exponential_and_capped():
    assert claude.backoff_delay(0, base=10.0, cap=100.0) == 10.0
    assert claude.backoff_delay(1, base=10.0, cap=100.0) == 20.0
    assert claude.backoff_delay(2, base=10.0, cap=100.0) == 40.0
    # grows past the cap → clamped
    assert claude.backoff_delay(10, base=10.0, cap=100.0) == 100.0


def test_last_api_error_status_detects_529(tmp_path):
    log = tmp_path / "o.jsonl"
    log.write_text(
        json.dumps({"type": "system", "subtype": "init"})
        + "\n"
        + json.dumps({"type": "assistant", "error": "server_error"})
        + "\n"
        + json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": True,
                "api_error_status": 529,
            }
        )
        + "\n"
    )
    assert claude.last_api_error_status(log) == 529


def test_last_api_error_status_none_on_success(tmp_path):
    log = tmp_path / "o.jsonl"
    log.write_text(
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "api_error_status": None,
                "num_turns": 5,
            }
        )
        + "\n"
    )
    assert claude.last_api_error_status(log) is None


def test_last_api_error_status_none_on_missing_or_garbage(tmp_path):
    assert claude.last_api_error_status(tmp_path / "does-not-exist") is None
    log = tmp_path / "garbage.jsonl"
    log.write_text("not json\n{partial\n\n")
    assert claude.last_api_error_status(log) is None


def test_run_claude_retries_then_gives_up_on_persistent_529(
    fake_bin, tmp_path, monkeypatch
):
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error")
    wt = tmp_path / "wt"
    wt.mkdir()
    out = tmp_path / "o.jsonl"
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        out,
        tmp_path / "e",
        timeout_s=10,
        max_retries=2,
        retry_base_s=0.01,
        retry_cap_s=10,
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    # 2 retries → 3 attempts total → 2 backoff sleeps, exponential.
    assert sleeper.delays == [0.01, 0.02]
    retries_log = out.parent / "claude.retries.log"
    assert retries_log.is_file()
    assert "529" in retries_log.read_text()


def test_run_claude_clears_stale_result_between_retries(
    fake_bin, tmp_path, monkeypatch
):
    # A prior attempt that writes .ptt-result.json then dies non-zero must not
    # leave a stale success claim for a later failed attempt to be reconciled
    # against — otherwise a failed run would be reported as success.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_stale_result")
    wt = tmp_path / "wt"
    wt.mkdir()
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        max_retries=1,
        retry_base_s=0.01,
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert len(sleeper.delays) == 1  # first attempt wrote the result, then retried
    # the stale success result must be gone after the failed final attempt
    assert not (wt / ".ptt-result.json").exists()


def test_run_claude_succeeds_after_transient_529(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_then_pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        retry_base_s=0.01,
        sleep=sleeper,
    )
    assert rc == 0 and timed_out is False
    assert len(sleeper.delays) == 1  # failed once, then succeeded
    assert (wt / ".ptt-result.json").is_file()


def test_run_claude_does_not_retry_non_api_error(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "error")  # exits 3, no api_error_status
    wt = tmp_path / "wt"
    wt.mkdir()
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o",
        tmp_path / "e",
        timeout_s=10,
        sleep=sleeper,
    )
    assert rc == 3 and timed_out is False
    assert sleeper.delays == []

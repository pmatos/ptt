import json
from pathlib import Path

from ptt import claude, outcomes
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


def _invocations(wt):
    """The fake claude's per-invocation log (`fresh` / `resume:<id>` lines)."""
    p = Path(wt) / ".fake-invocations"
    return p.read_text().split() if p.is_file() else []


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


def test_build_prompt_names_background_bash_and_subset_fallback():
    out = claude.build_prompt("Do the audit.").lower()
    # Beyond the schedule-and-wait tools, the footer must name the background Bash
    # path explicitly (that is what backgrounded test262 in #30) and give a concrete
    # fallback for a suite too big to finish synchronously: run a bounded subset.
    assert "run_in_background" in out
    assert "subset" in out


def test_build_env_disables_background_tasks_and_keeps_environ(monkeypatch):
    # The hard guard that closes the background-Bash gap: with this set the CLI
    # ignores run_in_background so every Bash blocks to completion in-turn.
    monkeypatch.setenv("PATH", "/sentinel/bin")
    env = claude.build_env()
    assert env["CLAUDE_CODE_DISABLE_BACKGROUND_TASKS"] == "1"
    # It augments the real environment rather than replacing it, so the subprocess
    # still finds claude/git/gh on PATH.
    assert env["PATH"] == "/sentinel/bin"


def test_run_claude_passes_no_background_env_to_subprocess(
    fake_bin, tmp_path, monkeypatch
):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    wt = tmp_path / "wt"
    wt.mkdir()
    out, err = tmp_path / "o.jsonl", tmp_path / "e.log"
    claude.run_claude(_routine(), wt, "prompt", out, err, timeout_s=10)
    assert (wt / ".fake-env-bgtasks").read_text() == "1"


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


def test_session_id_from_stream_reads_last_seen(tmp_path):
    # A resumed session can fork to a new id; track the last one seen so the next
    # retry resumes the current conversation, not a stale ancestor.
    log = tmp_path / "o.jsonl"
    log.write_text(
        json.dumps({"type": "system", "subtype": "init", "session_id": "sess-a"})
        + "\n"
        + json.dumps({"type": "assistant", "session_id": "sess-a"})
        + "\n"
        + json.dumps({"type": "result", "subtype": "success", "session_id": "sess-b"})
        + "\n"
    )
    assert claude.session_id_from_stream(log) == "sess-b"


def test_session_id_from_stream_none_when_absent_or_missing(tmp_path):
    assert claude.session_id_from_stream(tmp_path / "nope.jsonl") is None
    log = tmp_path / "o.jsonl"
    log.write_text(json.dumps({"type": "result", "subtype": "success"}) + "\n")
    assert claude.session_id_from_stream(log) is None


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


def test_run_claude_stale_result_does_not_survive_retries(
    fake_bin, tmp_path, monkeypatch
):
    # A prior attempt that emits a success structured_output then dies non-zero must
    # not leave a stale success claim for a later failed attempt to be reconciled
    # against — otherwise a failed run would be reported as success. Each attempt
    # truncates the stdout log, so the final failed attempt's log carries no claim.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_stale_result")
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
        max_retries=1,
        retry_base_s=0.01,
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert len(sleeper.delays) == 1  # first attempt emitted a result, then retried
    # the stale success claim must be gone after the failed final attempt
    assert outcomes.read_structured_output(out) is None


def test_run_claude_succeeds_after_transient_529(fake_bin, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_then_pr")
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
        retry_base_s=0.01,
        sleep=sleeper,
    )
    assert rc == 0 and timed_out is False
    assert len(sleeper.delays) == 1  # failed once, then succeeded
    claimed = outcomes.read_structured_output(out)
    assert claimed is not None and claimed.action == m.Action.PR
    # the retry RESUMED the interrupted session rather than starting over
    assert _invocations(wt) == ["fresh", "resume:sess-1"]


def test_run_claude_resumes_between_retries_without_resetting(
    fake_bin, tmp_path, monkeypatch
):
    # A transient API error is a pause, not a reason to start over: each retry must
    # RESUME the interrupted session (keeping the worktree — incl. any pushed branch
    # — intact) instead of rewinding the clone. So reset must NOT be called.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error")  # persistent 529, with a session
    wt = tmp_path / "wt"
    wt.mkdir()
    resets = []
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        max_retries=2,
        retry_base_s=0.01,
        reset=lambda: (resets.append(True), True)[1],
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert resets == []  # resume keeps the worktree; the reset fallback is untouched
    assert sleeper.delays == [0.01, 0.02]
    # fresh first attempt, then each retry resumes the same session
    assert _invocations(wt) == ["fresh", "resume:sess-1", "resume:sess-1"]


def test_run_claude_stays_sticky_when_resumed_attempt_omits_session(
    fake_bin, tmp_path, monkeypatch
):
    # Once a session is established and we're resuming, a later failed attempt that
    # emits no fresh session_id must NOT reset+restart (that would rebuild a divergent
    # commit over an already-pushed branch — the bug this PR fixes); it must keep
    # resuming the existing session. Only a never-established session resets.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_session_then_none")
    wt = tmp_path / "wt"
    wt.mkdir()
    resets = []
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        max_retries=2,
        retry_base_s=0.01,
        reset=lambda: (resets.append(True), True)[1],
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert resets == []  # never reset once a session was established
    # first attempt establishes sess-1; both retries keep resuming it despite no new id
    assert _invocations(wt) == ["fresh", "resume:sess-1", "resume:sess-1"]


def test_run_claude_falls_back_to_reset_when_no_session(
    fake_bin, tmp_path, monkeypatch
):
    # If claude died before a session existed, there's nothing to resume; fall back
    # to the old behaviour — reset the clone, then retry fresh.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_no_session")
    wt = tmp_path / "wt"
    wt.mkdir()
    resets = []
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        max_retries=2,
        retry_base_s=0.01,
        reset=lambda: (resets.append(True), True)[1],
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert len(resets) == 2  # reset before each fresh retry
    assert sleeper.delays == [0.01, 0.02]
    assert _invocations(wt) == ["fresh", "fresh", "fresh"]  # never resumed


def test_run_claude_stops_retrying_when_reset_fails(fake_bin, tmp_path, monkeypatch):
    # On the no-session fallback, if the clone can't be cleaned, retrying into a dirty
    # worktree is unsafe: stop and return the failure so reconciliation decides.
    monkeypatch.setenv("PTT_FAKE_MODE", "api_error_no_session")
    wt = tmp_path / "wt"
    wt.mkdir()
    calls = []
    sleeper = _Sleeper()
    rc, timed_out = claude.run_claude(
        _routine(),
        wt,
        "prompt",
        tmp_path / "o.jsonl",
        tmp_path / "e",
        timeout_s=10,
        max_retries=3,
        retry_base_s=0.01,
        reset=lambda: (calls.append(True), False)[1],
        sleep=sleeper,
    )
    assert rc == 1 and timed_out is False
    assert len(calls) == 1  # attempted reset once after the first failure, then bailed
    assert sleeper.delays == []  # no backoff because we didn't retry
    assert _invocations(wt) == ["fresh"]  # only the first attempt ran


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

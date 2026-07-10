"""Invoke `claude -p` headless against a worktree. Builds the effective prompt
(routine prompt + a fixed result-reporting footer) and the argv, and runs it in
its own process group so a timeout can kill the whole tree.

Two harness-level guards make the one-shot contract robust: `--json-schema` forces
Claude's final message to be a schema-valid result object (surfaced in the
stream-json `result` event's `structured_output`, so ptt no longer depends on the
model remembering to write a file), and `--disallowedTools` removes the
schedule-and-wait tools that tempt the model to background work and end its turn
expecting a re-invocation that never comes.

Separately, `claude` retries transient API failures internally, but during a
sustained overload window it exhausts those retries and exits non-zero with an
`api_error_status` (e.g. 529) in its stream-json output. `run_claude` adds an
outer retry with exponential backoff for exactly those cases, so a single blip in
Anthropic availability doesn't fail an otherwise-fine project. A transient error
is a pause, not a reason to start over: each retry **resumes** the interrupted
conversation (`claude -p --resume <session_id>`, the id read from the failed
attempt's stream), so the worktree — including any commit or branch a prior
attempt already pushed — is kept intact and the model continues from where it left
off rather than redoing (and diverging from) its own work. Only if no session was
ever established (claude died before emitting one) does `run_claude` fall back to
the caller's `reset` callback and a fresh attempt."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

from ptt import models as m

# The exact result contract, enforced by `--json-schema` so the final message
# cannot end the run without conforming. Enum values mirror m.Status / m.Action.
RESULT_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["success", "no_action", "error"]},
        "action": {
            "type": "string",
            "enum": ["pr", "issue_opened", "issue_closed", "commit", "none"],
        },
        "url": {"type": ["string", "null"]},
        "title": {"type": "string"},
        "summary": {"type": "string"},
    },
    "required": ["status", "action", "url", "title", "summary"],
    "additionalProperties": False,
}

# Tools that let Claude background work and end its turn to "be resumed" — which
# never happens in a one-shot headless run, so the work is killed and lost. Denied
# by bare name so they are removed from the toolset entirely. (Agent/subagents are
# left enabled: they run to completion within the turn.)
DISALLOWED_TOOLS = ["ScheduleWakeup", "Monitor", "CronCreate"]

# HTTP statuses worth re-invoking `claude` for: overload/rate-limit/5xx, i.e.
# transient server-side conditions that a later attempt may clear. A 4xx like
# 400/401/403/404 is a config/auth problem that won't fix itself, so it is
# deliberately absent — retrying it would only waste time.
RETRYABLE_API_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

RESULT_FOOTER = """\
---
After completing the task above, you are running unattended via `ptt`. When the
work warrants it, create a branch, commit, push, and open a pull request (or open
/ close an issue) using the `gh` CLI.

This is a single, one-shot run and you will NOT be re-invoked. Run every
verification step (tests, builds, suites) synchronously to completion and read
its result before continuing — never launch long-running work in the background
and end your turn waiting to be resumed, because anything still running is killed
and its result is lost when your turn ends. Push the branch and open the PR
within this turn; an unpushed commit is discarded when the throwaway clone is
removed.

When you end, report the outcome as the structured result ptt requires — even if
you failed or ran out of time (use status "error" with a summary of how far you
got): a single object with these keys:

  {
    "status":  "success" | "no_action" | "error",
    "action":  "pr" | "issue_opened" | "issue_closed" | "commit" | "none",
    "url":     "<url of the PR/issue, or null>",
    "title":   "<short human title>",
    "summary": "<1-3 sentence description of what was done, or why nothing was>"
  }
"""


# Stdin for a resumed attempt after a transient API error. The original task and
# result contract are already in the resumed transcript (and `--json-schema` still
# re-enforces the result), so this only nudges the model to continue — not restart.
RESUME_PROMPT = """\
A transient API error interrupted your previous turn before you finished. This run
resumes that same session: the working tree — including any edits, commits, and any
branch you already pushed — is exactly as you left it. Pick up where you left off and
carry the task to completion. Do not start over or repeat work already done (e.g.
don't re-create a branch or re-open a PR that already exists). When you finish,
report the structured result ptt requires, as instructed earlier.
"""


def build_prompt(prompt_text: str) -> str:
    return f"{prompt_text}\n\n{RESULT_FOOTER}"


def build_argv(
    routine: m.Routine, *, resume_session_id: str | None = None
) -> list[str]:
    argv = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if resume_session_id:
        argv += ["--resume", resume_session_id]
    if routine.permission_mode == m.PermissionMode.BYPASS:
        argv.append("--dangerously-skip-permissions")
    else:
        argv += ["--permission-mode", str(routine.permission_mode)]
    if routine.model:
        argv += ["--model", routine.model]
    if routine.effort:
        argv += ["--effort", str(routine.effort)]
    argv += ["--json-schema", json.dumps(RESULT_SCHEMA)]
    # Variadic flag: keep it last so it consumes only the tool names that follow.
    argv += ["--disallowedTools", *DISALLOWED_TOOLS]
    return argv


def run_claude(
    routine: m.Routine,
    worktree: Path,
    prompt_text: str,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: float,
    *,
    max_retries: int = m.DEFAULT_MAX_API_RETRIES,
    retry_base_s: float = m.DEFAULT_RETRY_BASE_S,
    retry_cap_s: float = m.DEFAULT_RETRY_CAP_S,
    reset: Callable[[], bool] | None = None,
    sleep=time.sleep,
) -> tuple[int, bool]:
    """Invoke `claude` (see _run_once), retrying up to `max_retries` extra times
    with exponential backoff when it exits non-zero because of a transient API
    error (a RETRYABLE_API_STATUSES status in its stream-json output). Returns
    (exit_code, timed_out) for the final attempt. Timeouts and non-API failures
    are never retried. The stdout/stderr logs hold the last attempt; each retry is
    noted in a sibling `claude.retries.log`.

    Each retry **resumes** the interrupted conversation (`--resume <session_id>`,
    read from the just-failed attempt's stream) so the worktree — including any
    commit or branch the prior attempt already pushed — is kept intact and the
    model continues its own work rather than redoing it from scratch (which would
    diverge from an already-pushed branch and be rejected on push). Only if that
    attempt established no session to resume does `run_claude` fall back to the
    `reset` callback (if given) — discarding any local side effects — before a fresh
    attempt; if the reset returns False the retry is abandoned (returning the failed
    result rather than running against a dirty tree) and reconciliation decides."""
    retries = max(0, max_retries)  # always at least one attempt
    resume_session_id: str | None = None
    for attempt in range(retries + 1):
        # Each attempt truncates stdout_path (see _run_once), so the reconciled
        # claim always comes from the last attempt's structured_output — a prior
        # attempt that died non-zero can't leave a stale claim behind. Extract the
        # session id below *before* the next attempt overwrites the log.
        stdin_text = RESUME_PROMPT if resume_session_id else build_prompt(prompt_text)
        rc, timed_out = _run_once(
            routine,
            worktree,
            stdin_text,
            stdout_path,
            stderr_path,
            timeout_s,
            resume_session_id=resume_session_id,
        )
        if rc == 0 or timed_out:
            return rc, timed_out
        status = last_api_error_status(stdout_path)
        if status not in RETRYABLE_API_STATUSES or attempt == retries:
            return rc, timed_out
        session_id = session_id_from_stream(stdout_path)
        if session_id is not None:
            # Resume the same conversation next time; keep the worktree as-is.
            resume_session_id = session_id
        else:
            # No session to resume: discard any local side effects and retry fresh;
            # if the tree can't be cleaned, don't retry into a dirty state.
            if reset is not None and not reset():
                return rc, timed_out
            resume_session_id = None
        delay = backoff_delay(attempt, retry_base_s, retry_cap_s)
        _note_retry(stdout_path, attempt, retries, status, delay)
        sleep(delay)
    return rc, timed_out


def _run_once(
    routine: m.Routine,
    worktree: Path,
    stdin_text: str,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: float,
    *,
    resume_session_id: str | None = None,
) -> tuple[int, bool]:
    """One `claude -p` invocation (resuming `resume_session_id` if given), with
    `stdin_text` piped in. Returns (exit_code, timed_out). On timeout the whole
    process group is terminated and exit_code is 124."""
    argv = build_argv(routine, resume_session_id=resume_session_id)
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        p = subprocess.Popen(
            argv,
            cwd=str(worktree),
            stdin=subprocess.PIPE,
            stdout=out,
            stderr=err,
            text=True,
            start_new_session=True,
        )
        try:
            p.communicate(input=stdin_text, timeout=timeout_s)
            return p.returncode, False
        except subprocess.TimeoutExpired:
            _terminate_group(p)
            return 124, True


def session_id_from_stream(stdout_path: Path) -> str | None:
    """Return the `session_id` carried by the stream-json output (the last one seen,
    so a resumed session that forks to a new id is tracked forward), or None if the
    log is missing or carries no session id. Used to resume the conversation on a
    retry. Tolerant of partial/non-JSON lines so a truncated log never raises."""
    session_id: str | None = None
    try:
        with open(stdout_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                s = obj.get("session_id")
                if isinstance(s, str) and s:
                    session_id = s
    except OSError:
        return None
    return session_id


def last_api_error_status(stdout_path: Path) -> int | None:
    """Return the `api_error_status` from the terminal stream-json `result` record
    if the run ended in an API-level error (e.g. 529 Overloaded), else None. Tolerant
    of partial/non-JSON lines so a truncated log never raises."""
    status: int | None = None
    try:
        with open(stdout_path) as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "result" and obj.get("is_error"):
                    s = obj.get("api_error_status")
                    # bools are ints in Python; guard against a stray True/False.
                    if isinstance(s, int) and not isinstance(s, bool):
                        status = s
    except OSError:
        return None
    return status


def backoff_delay(attempt: int, base: float, cap: float, factor: float = 2.0) -> float:
    """Exponential backoff for a 0-based attempt index, capped at `cap`."""
    return min(cap, base * (factor**attempt))


def _note_retry(
    stdout_path: Path, attempt: int, max_retries: int, status: int, delay: float
) -> None:
    """Best-effort breadcrumb next to the claude logs so a post-mortem shows the
    outer retries (the canonical stdout log only keeps the last attempt)."""
    line = (
        f"attempt {attempt + 1}/{max_retries + 1} failed: claude API error "
        f"{status}; retrying in {delay:.0f}s\n"
    )
    with (
        contextlib.suppress(OSError),
        open(stdout_path.parent / "claude.retries.log", "a") as fh,
    ):
        fh.write(line)


def _terminate_group(p: subprocess.Popen) -> None:
    try:
        pgid = os.getpgid(p.pid)
    except ProcessLookupError:
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        p.wait(timeout=2)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(pgid, signal.SIGKILL)
        p.wait()

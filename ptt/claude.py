"""Invoke `claude -p` headless against a worktree. Builds the effective prompt
(routine prompt + a fixed result-reporting footer) and the argv, and runs it in
its own process group so a timeout can kill the whole tree.

`claude` retries transient API failures internally, but during a sustained
overload window it exhausts those retries and exits non-zero with an
`api_error_status` (e.g. 529) in its stream-json output. `run_claude` adds an
outer retry with exponential backoff for exactly those cases, so a single blip in
Anthropic availability doesn't fail an otherwise-fine project."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
import time
from pathlib import Path

from ptt import models as m

# HTTP statuses worth re-invoking `claude` for: overload/rate-limit/5xx, i.e.
# transient server-side conditions that a later attempt may clear. A 4xx like
# 400/401/403/404 is a config/auth problem that won't fix itself, so it is
# deliberately absent — retrying it would only waste time.
RETRYABLE_API_STATUSES = frozenset({408, 429, 500, 502, 503, 504, 529})

# The file Claude writes to report its outcome; run_claude clears it before each
# attempt so a retry is never reconciled against a stale earlier claim.
RESULT_FILENAME = ".ptt-result.json"

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

Always write a file named `.ptt-result.json` in the repository root before you
end — even if you failed or ran out of time (use status "error" with a summary of
how far you got) — a single JSON object describing what you did, with exactly
these keys:

  {
    "status":  "success" | "no_action" | "error",
    "action":  "pr" | "issue_opened" | "issue_closed" | "commit" | "none",
    "url":     "<url of the PR/issue, or null>",
    "title":   "<short human title>",
    "summary": "<1-3 sentence description of what was done, or why nothing was>"
  }
"""


def build_prompt(prompt_text: str) -> str:
    return f"{prompt_text}\n\n{RESULT_FOOTER}"


def build_argv(routine: m.Routine) -> list[str]:
    argv = ["claude", "-p", "--output-format", "stream-json", "--verbose"]
    if routine.permission_mode == m.PermissionMode.BYPASS:
        argv.append("--dangerously-skip-permissions")
    else:
        argv += ["--permission-mode", str(routine.permission_mode)]
    if routine.model:
        argv += ["--model", routine.model]
    if routine.effort:
        argv += ["--effort", str(routine.effort)]
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
    sleep=time.sleep,
) -> tuple[int, bool]:
    """Invoke `claude` (see _run_once), retrying up to `max_retries` extra times
    with exponential backoff when it exits non-zero because of a transient API
    error (a RETRYABLE_API_STATUSES status in its stream-json output). Returns
    (exit_code, timed_out) for the final attempt. Timeouts and non-API failures
    are never retried. The stdout/stderr logs hold the last attempt; each retry is
    noted in a sibling `claude.retries.log`."""
    retries = max(0, max_retries)  # always at least one attempt
    result_file = worktree / RESULT_FILENAME
    for attempt in range(retries + 1):
        # Judge each attempt on its own result file: a prior attempt that wrote
        # .ptt-result.json and then died non-zero must not leave a stale claim for
        # a later failed attempt to be reconciled against (that would report a
        # failed run as success). See PR #17 review.
        with contextlib.suppress(FileNotFoundError):
            result_file.unlink()
        rc, timed_out = _run_once(
            routine, worktree, prompt_text, stdout_path, stderr_path, timeout_s
        )
        if rc == 0 or timed_out:
            return rc, timed_out
        status = last_api_error_status(stdout_path)
        if status not in RETRYABLE_API_STATUSES or attempt == retries:
            return rc, timed_out
        delay = backoff_delay(attempt, retry_base_s, retry_cap_s)
        _note_retry(stdout_path, attempt, retries, status, delay)
        sleep(delay)
    return rc, timed_out


def _run_once(
    routine: m.Routine,
    worktree: Path,
    prompt_text: str,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: float,
) -> tuple[int, bool]:
    """One `claude -p` invocation. Returns (exit_code, timed_out). On timeout the
    whole process group is terminated and exit_code is 124."""
    argv = build_argv(routine)
    full_prompt = build_prompt(prompt_text)
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
            p.communicate(input=full_prompt, timeout=timeout_s)
            return p.returncode, False
        except subprocess.TimeoutExpired:
            _terminate_group(p)
            return 124, True


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

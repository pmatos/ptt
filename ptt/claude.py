"""Invoke `claude -p` headless against a worktree. Builds the effective prompt
(routine prompt + a fixed result-reporting footer) and the argv, and runs it in
its own process group so a timeout can kill the whole tree.

Two harness-level guards make the one-shot contract robust: `--json-schema` forces
Claude's final message to be a schema-valid result object (surfaced in the
stream-json `result` event's `structured_output`, so ptt no longer depends on the
model remembering to write a file), and `--disallowedTools` removes the
schedule-and-wait tools that tempt the model to background work and end its turn
expecting a re-invocation that never comes."""

from __future__ import annotations

import contextlib
import json
import os
import signal
import subprocess
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
) -> tuple[int, bool]:
    """Returns (exit_code, timed_out). On timeout the whole process group is
    terminated and exit_code is 124."""
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

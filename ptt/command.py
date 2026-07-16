"""Invoke a command routine's local command, capturing its stdout/stderr to files.

ptt stays a pure scheduler here: the command is run as-is (argv, no shell), its
stdout becomes the digest emailed by the runner, and its exit code drives the
outcome. The command runs in its own process group (`start_new_session=True`) so a
wall-clock timeout kills the whole tree, not just the direct child — an unattended
run must not leak forked grandchildren. This mirrors `claude.py`'s process-group
handling but has no stdin/stream-json needs, so it is kept separate and simple."""

from __future__ import annotations

import subprocess
from pathlib import Path

from ptt import proc


def run_command(
    argv: list[str],
    cwd: Path,
    stdout_path: Path,
    stderr_path: Path,
    timeout_s: float,
    env: dict[str, str] | None = None,
) -> tuple[int, bool]:
    """Run `argv` in `cwd`, redirecting stdout/stderr to the given paths. Returns
    (exit_code, timed_out). On timeout the whole process group is killed and the
    exit code is 124."""
    with open(stdout_path, "w") as out, open(stderr_path, "w") as err:
        p = subprocess.Popen(
            argv,
            cwd=str(cwd),
            stdout=out,
            stderr=err,
            env=env,
            text=True,
            start_new_session=True,
        )
        try:
            p.wait(timeout=timeout_s)
            return p.returncode, False
        except subprocess.TimeoutExpired:
            proc.terminate_group(p)
            return 124, True

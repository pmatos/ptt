"""Thin synchronous subprocess wrapper used by git_ops, outcomes, and schedule.
Captures stdout/stderr as text, optionally tees the invocation to a log file,
and turns a timeout into a 124 return code (rather than raising). claude.py does
NOT use this — its streaming/process-group needs are handled separately."""

from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Completed:
    returncode: int
    stdout: str
    stderr: str


def run(
    cmd: list[str],
    *,
    cwd=None,
    timeout: float | None = None,
    env=None,
    input: str | None = None,
    log_path: Path | None = None,
) -> Completed:
    try:
        cp = subprocess.run(
            cmd,
            cwd=cwd,
            env=env,
            input=input,
            timeout=timeout,
            capture_output=True,
            text=True,
        )
        result = Completed(cp.returncode, cp.stdout, cp.stderr)
    except subprocess.TimeoutExpired as e:
        out = _as_text(e.stdout or "")
        err = _as_text(e.stderr or "") + f"\n[ptt] timed out after {timeout}s"
        result = Completed(124, out, err)

    if log_path is not None:
        with Path(log_path).open("a") as fh:
            fh.write(f"$ {shlex.join(cmd)}\n")
            if result.stdout:
                fh.write(result.stdout)
                if not result.stdout.endswith("\n"):
                    fh.write("\n")
            if result.stderr:
                fh.write(result.stderr)
                if not result.stderr.endswith("\n"):
                    fh.write("\n")
            fh.write(f"[exit {result.returncode}]\n")
    return result


def _as_text(v) -> str:
    return v.decode() if isinstance(v, (bytes, bytearray)) else str(v)

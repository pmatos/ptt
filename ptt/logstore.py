"""Run/log directory layout and writers. Owns the on-disk shape under
$XDG_STATE_HOME/ptt/runs/<routine>/<run-id>/ described in the spec (§6)."""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from ptt import config
from ptt import models as m


def new_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _runs_root() -> Path:
    return config.state_home() / "runs"


def make_run_dir(routine: str, run_id: str) -> tuple[str, Path]:
    """Create the run dir, never clobbering an existing one. If two runs share
    the same second, the id gets a -2/-3/... suffix; the final id is returned so
    callers use it for branch names and paths."""
    base = _runs_root() / routine
    candidate = run_id
    n = 2
    while True:
        path = base / candidate
        try:
            path.mkdir(parents=True, exist_ok=False)
            return candidate, path
        except FileExistsError:
            candidate = f"{run_id}-{n}"
            n += 1


def project_dir_name(path: Path, taken: set[str]) -> str:
    name = path.name
    if name in taken:
        digest = hashlib.sha1(str(path).encode()).hexdigest()[:6]
        name = f"{name}-{digest}"
    return name


def project_dir(run_dir: Path, name: str) -> Path:
    d = run_dir / "projects" / name
    d.mkdir(parents=True, exist_ok=True)
    return d


def snapshot_prompt(run_dir: Path, prompt_path: Path) -> Path:
    dest = run_dir / "prompt.md"
    dest.write_text(Path(prompt_path).read_text())
    return dest


def claude_stdout_path(pdir: Path) -> Path:
    return pdir / "claude.stdout.jsonl"


def claude_stderr_path(pdir: Path) -> Path:
    return pdir / "claude.stderr.log"


def result_path(pdir: Path) -> Path:
    return pdir / "result.json"


def git_log_path(pdir: Path) -> Path:
    return pdir / "git.log"


def append_git_log(pdir: Path, text: str) -> None:
    with git_log_path(pdir).open("a") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def write_result_json(pdir: Path, result: m.ProjectResult) -> None:
    result_path(pdir).write_text(json.dumps(result.to_dict(), indent=2))


def write_run_json(run_dir: Path, run: m.RunResult) -> None:
    (run_dir / "run.json").write_text(json.dumps(run.to_dict(), indent=2))

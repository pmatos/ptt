"""Orchestrate a routine run: fan out over projects (sequentially, isolated in a
worktree each), reconcile outcomes, write logs, and email a summary. One project
failing never aborts the others; worktree cleanup is always guaranteed."""

from __future__ import annotations

import os
import time
from datetime import datetime, timezone
from pathlib import Path

from ptt import claude, git_ops, logstore, notify, outcomes
from ptt import models as m


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def exit_code(run: m.RunResult) -> int:
    return 1 if run.overall_status == m.Status.ERROR else 0


def run_routine(
    routine: m.Routine,
    global_cfg: m.GlobalConfig,
    *,
    only_project: str | None = None,
    force: bool = False,
) -> m.RunResult:
    started = _now_iso()
    if not routine.enabled and not force:
        return m.RunResult(
            routine.name, "", started, _now_iso(), m.Status.SUCCESS, [], ""
        )

    run_id, run_dir = logstore.make_run_dir(routine.name, logstore.new_run_id())

    try:
        logstore.snapshot_prompt(run_dir, routine.prompt)
        prompt_text = routine.prompt.read_text()
    except OSError as e:
        return _finish(
            routine,
            run_id,
            run_dir,
            started,
            [_synthetic_error(run_dir, "prompt", f"prompt unreadable: {e}")],
            global_cfg,
        )

    projects = [Path(p).expanduser().resolve() for p in routine.projects]
    if only_project is not None:
        target = Path(only_project).expanduser().resolve()
        projects = [p for p in projects if p == target]
        if not projects:
            return _finish(
                routine,
                run_id,
                run_dir,
                started,
                [
                    _synthetic_error(
                        run_dir, "select", f"--project {only_project!r} matched nothing"
                    )
                ],
                global_cfg,
            )

    taken: set[str] = set()
    results: list[m.ProjectResult] = []
    for repo in projects:
        name = logstore.project_dir_name(repo, taken)
        taken.add(name)
        pdir = logstore.project_dir(run_dir, name)
        try:
            results.append(
                _run_one_project(routine, repo, run_id, pdir, prompt_text, name)
            )
        except Exception as e:  # noqa: BLE001 - isolate per-project failures
            results.append(
                _error_result(
                    name,
                    repo,
                    pdir,
                    f"runner error: {e}",
                    git_ops.branch_name(routine.name, run_id),
                )
            )

    # tidy the now-empty per-run worktree parent (worktrees themselves are
    # already removed); leave it in place if anything failed to clean up.
    try:
        (routine.work_dir / run_id).rmdir()
    except OSError:
        pass

    return _finish(routine, run_id, run_dir, started, results, global_cfg)


def _run_one_project(routine, repo_path, run_id, pdir, prompt_text, name):
    t0 = time.monotonic()
    log = logstore.git_log_path(pdir)
    branch = git_ops.branch_name(routine.name, run_id)

    if not git_ops.is_github_repo(repo_path, log):
        return _error_result(
            name,
            repo_path,
            pdir,
            "not a GitHub repo (origin missing or non-github)",
            branch,
            _dur(t0),
        )

    git_ops.fetch(repo_path, routine.base_branch, log)
    dest = routine.work_dir / run_id / name
    try:
        git_ops.add_worktree(repo_path, dest, branch, routine.base_branch, log)
        pre, pre_ok = outcomes.gh_snapshot(dest, log)
        rc, timed_out = claude.run_claude(
            routine,
            dest,
            prompt_text,
            logstore.claude_stdout_path(pdir),
            logstore.claude_stderr_path(pdir),
            routine.timeout_minutes * 60,
        )
        claimed = outcomes.read_result_file(dest)
        post, post_ok = outcomes.gh_snapshot(dest, log)
        fields = outcomes.reconcile(
            claimed,
            pre,
            post,
            pre_ok,
            post_ok,
            rc,
            timed_out,
            stderr_tail=_tail(logstore.claude_stderr_path(pdir)),
        )
        result = m.ProjectResult(
            name=name,
            path=str(repo_path),
            branch=branch,
            duration_s=_dur(t0),
            log_dir=str(pdir),
            **fields,
        )
        logstore.write_result_json(pdir, result)
        return result
    finally:
        git_ops.remove_worktree(repo_path, dest, log)


def _finish(routine, run_id, run_dir, started, results, global_cfg) -> m.RunResult:
    overall = (
        m.Status.ERROR
        if any(p.status == m.Status.ERROR for p in results)
        else m.Status.SUCCESS
    )
    run = m.RunResult(
        routine.name, run_id, started, _now_iso(), overall, results, str(run_dir)
    )
    logstore.write_run_json(run_dir, run)
    password = os.environ.get(global_cfg.email.smtp_password_env)
    notify.notify(run, global_cfg.email, password, run_dir)
    return run


def _error_result(
    name, repo_path, pdir, reason, branch, duration=0.0
) -> m.ProjectResult:
    result = m.ProjectResult(
        name=name,
        path=str(repo_path),
        status=m.Status.ERROR,
        action=m.Action.NONE,
        url=None,
        title="",
        summary=reason,
        verified=False,
        source=m.Source.CLAUDE,
        reason=reason,
        branch=branch,
        duration_s=duration,
        log_dir=str(pdir),
    )
    logstore.write_result_json(pdir, result)
    return result


def _synthetic_error(run_dir, name, reason) -> m.ProjectResult:
    pdir = logstore.project_dir(run_dir, name)
    return _error_result(name, Path("."), pdir, reason, branch=None)


def _dur(t0: float) -> float:
    return round(time.monotonic() - t0, 3)


def _tail(path: Path, n: int = 20) -> str:
    try:
        return "\n".join(path.read_text().splitlines()[-n:])
    except OSError:
        return ""

"""Orchestrate a routine run: fan out over projects (sequentially, each isolated
in a git worktree for local repos or a throwaway clone for remote ones), reconcile
outcomes, write logs, and email a summary. One project failing never aborts the
others; worktree/clone cleanup is always guaranteed."""

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

    specs = list(routine.projects)
    if only_project is not None:
        specs = [s for s in specs if _project_matches(s, only_project)]
        if not specs:
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
    for spec in specs:
        name = logstore.project_dir_name(spec.name, spec.raw, taken)
        taken.add(name)
        pdir = logstore.project_dir(run_dir, name)
        try:
            results.append(
                _run_one_project(routine, spec, run_id, pdir, prompt_text, name)
            )
        except Exception as e:  # noqa: BLE001 - isolate per-project failures
            results.append(
                _error_result(
                    name,
                    spec.raw,
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


def _project_matches(spec: m.ProjectSpec, sel: str) -> bool:
    if sel in (spec.raw, spec.name, spec.location):
        return True
    if not spec.is_remote:
        try:
            return Path(spec.location).resolve() == Path(sel).expanduser().resolve()
        except OSError:
            return False
    return False


def _run_one_project(routine, spec, run_id, pdir, prompt_text, name):
    t0 = time.monotonic()
    log = logstore.git_log_path(pdir)
    branch = git_ops.branch_name(routine.name, run_id)
    dest = routine.work_dir / run_id / name
    if spec.is_remote:
        return _run_remote(
            routine, spec, pdir, prompt_text, name, branch, dest, log, t0
        )
    return _run_local(routine, spec, pdir, prompt_text, name, branch, dest, log, t0)


def _run_local(routine, spec, pdir, prompt_text, name, branch, dest, log, t0):
    repo_path = Path(spec.location)
    if not git_ops.is_github_repo(repo_path, log):
        return _error_result(
            name,
            spec.location,
            pdir,
            "not a GitHub repo (origin missing or non-github)",
            branch,
            _dur(t0),
        )
    git_ops.fetch(repo_path, routine.base_branch, log)
    try:
        git_ops.add_worktree(repo_path, dest, branch, routine.base_branch, log)
        return _run_claude_and_reconcile(
            routine, dest, pdir, prompt_text, name, str(repo_path), branch, t0
        )
    finally:
        git_ops.remove_worktree(repo_path, dest, log)


def _run_remote(routine, spec, pdir, prompt_text, name, branch, dest, log, t0):
    try:
        git_ops.clone(spec.location, dest, routine.base_branch, log)
        if not git_ops.is_github_repo(dest, log):
            return _error_result(
                name,
                spec.raw,
                pdir,
                "not a GitHub repo (origin missing or non-github)",
                branch,
                _dur(t0),
            )
        git_ops.create_branch(dest, branch, log)
        return _run_claude_and_reconcile(
            routine, dest, pdir, prompt_text, name, spec.raw, branch, t0
        )
    finally:
        git_ops.remove_clone(dest, log)


def _run_claude_and_reconcile(
    routine, dest, pdir, prompt_text, name, path_display, branch, t0
):
    log = logstore.git_log_path(pdir)
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
        path=path_display,
        branch=branch,
        duration_s=_dur(t0),
        log_dir=str(pdir),
        **fields,
    )
    logstore.write_result_json(pdir, result)
    return result


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

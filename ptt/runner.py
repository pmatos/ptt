"""Orchestrate a routine run: fan out over projects (sequentially, each isolated in
a throwaway clone of its github.com remote), reconcile outcomes, write logs, and email
a summary. Local and remote projects are treated the same — a local entry contributes
only its origin URL (read-only); ptt never runs in, fetches into, or branches the local
checkout. One project failing never aborts the others; clone cleanup is always
guaranteed."""

from __future__ import annotations

import contextlib
import os
import shutil
import time
from datetime import UTC, datetime
from pathlib import Path

from ptt import claude, command, git_ops, logstore, notify, outcomes, projects
from ptt import models as m


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def exit_code(run: m.RunResult) -> int:
    return 1 if run.overall_status == m.Status.ERROR else 0


def command_exit_code(run: m.CommandRunResult) -> int:
    return 1 if run.status == m.Status.ERROR else 0


def run_command_routine(
    routine: m.CommandRoutine, global_cfg: m.GlobalConfig, *, force: bool = False
) -> m.CommandRunResult:
    """Run a projectless command routine: execute its command in a throwaway cwd,
    capture stdout/stderr, and email the stdout per policy. No clone, no Claude, no
    gh. One run = one command (no fan-out)."""
    started = _now_iso()
    if not routine.enabled and not force:
        return m.CommandRunResult(
            routine.name,
            "",
            started,
            _now_iso(),
            m.Status.SUCCESS,
            0,
            0,
            None,
            list(routine.command),
            0.0,
            "",
        )

    run_id, run_dir = logstore.make_run_dir(routine.name, logstore.new_run_id())
    logstore.snapshot_command(run_dir, routine.command)
    t0 = time.monotonic()
    cwd = routine.work_dir / run_id
    cwd.mkdir(parents=True, exist_ok=True)
    try:
        rc, timed_out = command.run_command(
            routine.command,
            cwd,
            logstore.command_stdout_path(run_dir),
            logstore.command_stderr_path(run_dir),
            routine.timeout_minutes * 60,
        )
        body = _read_text(logstore.command_stdout_path(run_dir))
    except Exception as e:  # a command that can't even launch is still a clean ERROR
        rc, timed_out, body = 127, False, ""
        status, reason = m.Status.ERROR, f"could not run command: {e}"
    else:
        if timed_out:
            status, reason = m.Status.ERROR, "timeout"
        elif rc != 0:
            status, reason = m.Status.ERROR, f"exit {rc}"
        else:
            status, reason = m.Status.SUCCESS, None
    finally:
        # Recursive best-effort: the command may have written files into its cwd, so
        # a plain rmdir would leave the scratch dir (and private artifacts) behind.
        shutil.rmtree(cwd, ignore_errors=True)

    run = m.CommandRunResult(
        routine=routine.name,
        run_id=run_id,
        started_at=started,
        ended_at=_now_iso(),
        status=status,
        exit_code=rc,
        stdout_len=len(body),
        reason=reason,
        command=list(routine.command),
        duration_s=_dur(t0),
        run_dir=str(run_dir),
    )
    logstore.write_command_run_json(run_dir, run)
    password = os.environ.get(global_cfg.email.smtp_password_env)
    notify.notify_command(
        run,
        routine.notify,
        routine.body_format,
        global_cfg.email,
        password,
        run_dir,
        body,
    )
    return run


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
        except Exception as e:  # isolate per-project failures
            results.append(
                _error_result(
                    name,
                    spec.raw,
                    pdir,
                    f"runner error: {e}",
                    git_ops.branch_name(routine.name, run_id),
                )
            )

    # tidy the now-empty per-run work parent (clones themselves are already
    # removed); leave it in place if anything failed to clean up.
    with contextlib.suppress(OSError):
        (routine.work_dir / run_id).rmdir()

    return _finish(routine, run_id, run_dir, started, results, global_cfg)


def _project_matches(spec: m.ProjectSpec, sel: str) -> bool:
    if sel in (spec.raw, spec.name, spec.location):
        return True
    if spec.is_remote:
        # a URL-configured remote is still targetable by its owner/repo slug,
        # which is what the CLI advertises `--project` accepts.
        return projects.slug_from_url(spec.location) == sel
    try:
        return Path(spec.location).resolve() == Path(sel).expanduser().resolve()
    except OSError:
        return False


def _run_one_project(routine, spec, run_id, pdir, prompt_text, name):
    t0 = time.monotonic()
    log = logstore.git_log_path(pdir)
    branch = git_ops.branch_name(routine.name, run_id)
    dest = routine.work_dir / run_id / name

    # Resolve the github.com clone URL. Remote specs carry it directly; a local
    # spec contributes only its origin (read-only). Either way ptt runs on a fresh
    # clone of the remote — the local checkout is never touched.
    if spec.is_remote:
        url = spec.location
        path_display = spec.raw
    else:
        path_display = spec.location
        url = git_ops.origin_url(Path(spec.location), log)

    if not url or "github.com" not in url:
        return _error_result(
            name,
            path_display,
            pdir,
            "not a GitHub repo (origin missing or non-github)",
            branch,
            _dur(t0),
        )

    try:
        git_ops.clone(url, dest, routine.base_branch, log)
        git_ops.create_branch(dest, branch, log)
        # Every project runs in a throwaway clone that is deleted below, so an
        # unpushed commit-only outcome is a loss for local and remote alike.
        return _run_claude_and_reconcile(
            routine,
            dest,
            pdir,
            prompt_text,
            name,
            path_display,
            branch,
            t0,
            ephemeral=True,
        )
    finally:
        git_ops.remove_clone(dest, log)


def _run_claude_and_reconcile(
    routine, dest, pdir, prompt_text, name, path_display, branch, t0, ephemeral=False
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
        max_retries=routine.api_max_retries,
        retry_base_s=routine.api_retry_base_seconds,
        retry_cap_s=routine.api_retry_cap_seconds,
        reset=lambda: git_ops.reset_worktree(dest, routine.base_branch, log),
    )
    claimed = outcomes.read_structured_output(logstore.claude_stdout_path(pdir))
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
        ephemeral=ephemeral,
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


def _read_text(path: Path) -> str:
    # errors="replace": command stdout is arbitrary bytes; odd encoding must not fail
    # an otherwise-successful run.
    try:
        return path.read_text(errors="replace")
    except OSError:
        return ""

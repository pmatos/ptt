"""ptt command-line interface."""

from __future__ import annotations

import argparse
import contextlib
import os
import shutil
import sys
from pathlib import Path

from ptt import config, ghcheck, git_ops, logstore, netcheck, notify, runner, schedule
from ptt import models as m


def _cmd_run(args) -> int:
    # Under the timer, fold the baked PTT_PATH into PATH before shelling out so
    # claude/git/gh resolve even if the unit's PATH was left stale (issue #15).
    schedule.apply_baked_path()
    cfg = config.load_global_config()
    routine = config.load_routine(args.routine, cfg)
    if isinstance(routine, m.CommandRoutine):
        return _run_command_routine(routine, cfg, args)
    # Fail fast (before any clone or gh call) if gh is missing/logged out, rather
    # than dying deep in the run with "gh snapshot failed" or hanging on git's own
    # `Username for 'https://github.com'` prompt. Only when the run will actually
    # proceed: a disabled routine without --force does no gh work (run_routine
    # exits 0), so preflighting it would wrongly fail a paused routine.
    if routine.enabled or args.force:
        problem = ghcheck.gh_problem()
        if problem is not None:
            print(f"error: {problem}", file=sys.stderr)
            return 2
    run = runner.run_routine(routine, cfg, only_project=args.project, force=args.force)
    print(notify.build_subject(run))
    print(notify.build_text(run))
    return runner.exit_code(run)


def _run_command_routine(routine: m.CommandRoutine, cfg, args) -> int:
    # A command routine does no GitHub work, so `--project` is meaningless and the gh
    # preflight is skipped entirely.
    if args.project:
        print("error: --project is not valid for a command routine", file=sys.stderr)
        return 2
    run = runner.run_command_routine(routine, cfg, force=args.force)
    print(notify.build_command_subject(run))
    if run.run_dir:
        stdout = logstore.command_stdout_path(Path(run.run_dir))
        if stdout.is_file():
            print(stdout.read_text(errors="replace"), end="")
    return runner.command_exit_code(run)


def _cmd_list(args) -> int:
    names = config.list_routine_names()
    if not names:
        print("no routines configured")
        return 0
    cfg = None
    with contextlib.suppress(config.ConfigError):
        cfg = config.load_global_config()
    for name in names:
        state = "?"
        if cfg is not None:
            try:
                state = (
                    "enabled" if config.load_routine(name, cfg).enabled else "disabled"
                )
            except config.ConfigError as e:
                state = f"INVALID ({e})"
        print(f"{name}: {state}")
    try:
        timers = schedule.list_timers()
        if timers.strip():
            print("\n" + timers)
    except schedule.ScheduleError:
        pass
    return 0


def _cmd_logs(args) -> int:
    runs_root = config.state_home() / "runs" / args.routine
    if not runs_root.is_dir():
        print(f"no runs for {args.routine!r}", file=sys.stderr)
        return 1
    if args.run:
        rd = runs_root / args.run
    else:
        runs = sorted(p for p in runs_root.iterdir() if p.is_dir())
        rd = runs[-1] if runs else None
    if rd is None or not rd.is_dir():
        print("run not found", file=sys.stderr)
        return 1
    run_json = rd / "run.json"
    if run_json.is_file():
        print(run_json.read_text())
    # A command run has no projects/ subtree; its output lives beside run.json.
    # Command stdout/stderr are arbitrary bytes, so decode leniently (matching the
    # runner) — a non-UTF-8 log must never make `ptt logs` crash.
    if (rd / "command.stdout.log").is_file():
        for fn in ("command.txt", "command.stdout.log", "command.stderr.log"):
            f = rd / fn
            if f.is_file():
                print(f"\n--- {fn} ---")
                print(f.read_text(errors="replace"))
    elif args.project:
        pdir = rd / "projects" / args.project
        for fn in ("git.log", "claude.stderr.log", "claude.stdout.jsonl"):
            f = pdir / fn
            if f.is_file():
                print(f"\n--- {fn} ---")
                print(f.read_text())
    return 0


def _cmd_install(args) -> int:
    cfg = config.load_global_config()
    routine = config.load_routine(args.routine, cfg)
    note = schedule.install(routine)
    print(f"installed timer for {routine.name!r} ({routine.schedule})")
    print(note)
    return 0


def _cmd_uninstall(args) -> int:
    schedule.uninstall(args.routine)
    print(f"removed timer for {args.routine!r}")
    return 0


def _cmd_wait_online(args) -> int:
    """Block until DNS is up (the systemd ExecStartPre gate). Always exits so a
    give-up never blocks the run — a non-zero code just marks it in the journal."""
    if netcheck.wait_online(args.host, args.timeout):
        return 0
    print(
        f"wait-online: {args.host} did not resolve within {args.timeout:g}s; "
        "proceeding anyway",
        file=sys.stderr,
    )
    return 1


def _cmd_doctor(args) -> int:
    ok = True
    smtp_password_missing = False

    def check(label, passed, detail=""):
        nonlocal ok
        print(f"{'✓' if passed else '✗'} {label}" + (f" — {detail}" if detail else ""))
        ok = ok and passed

    def info(label, passed, detail=""):
        # Reported for context but does not gate the exit code.
        print(f"{'✓' if passed else '·'} {label}" + (f" — {detail}" if detail else ""))

    cfg = None
    cfg_error = None
    routines: dict[str, m.Routine | m.CommandRoutine | config.ConfigError] = {}
    try:
        cfg = config.load_global_config()
    except config.ConfigError as e:
        cfg_error = e
    if cfg is not None:
        for name in config.list_routine_names():
            try:
                routines[name] = config.load_routine(name, cfg)
            except config.ConfigError as e:
                routines[name] = e

    # claude/git/gh are only needed by project routines. If none are configured (a
    # command-only install) they're reported for context but don't fail doctor. When
    # config itself failed to load we can't tell, so require them (conservative).
    needs_git_tools = cfg is None or any(
        isinstance(r, m.Routine) for r in routines.values()
    )
    tool_check = check if needs_git_tools else info
    for tool in ("claude", "git", "gh"):
        tool_check(f"{tool} on PATH", shutil.which(tool) is not None)
    if needs_git_tools:
        gh_authed = (
            bool(shutil.which("gh")) and runner_proc(["gh", "auth", "status"]) == 0
        )
        check("gh authenticated", gh_authed)

    if cfg is None:
        check("global config loads", False, str(cfg_error))
    else:
        check("global config loads", True)
        if cfg.email.smtp_username:
            pw = os.environ.get(cfg.email.smtp_password_env)
            smtp_password_missing = not pw
            check(f"smtp password (${cfg.email.smtp_password_env})", bool(pw))
        for name, r in routines.items():
            if isinstance(r, config.ConfigError):
                check(f"routine {name}", False, str(r))
                continue
            check(f"routine {name}", True)
            if isinstance(r, m.CommandRoutine):
                exe = r.command[0]
                found = shutil.which(exe) is not None or Path(exe).is_file()
                check(
                    f"routine {name} command",
                    found,
                    "" if found else f"{exe} not found",
                )

    if smtp_password_missing:
        env_file = config.env_file_path()
        if env_file.is_file():
            print(
                f"\nhint: manual runs don't auto-load {env_file} (only the systemd "
                f"timer does). Export its secrets into your shell first:\n"
                f"  set -a; source {env_file}; set +a"
            )
    return 0 if ok else 1


def runner_proc(cmd) -> int:
    from ptt import proc

    return proc.run(cmd).returncode


def _cmd_test_email(args) -> int:
    cfg = config.load_global_config()
    password = os.environ.get(cfg.email.smtp_password_env)
    if cfg.email.smtp_username and not password:
        print(f"error: ${cfg.email.smtp_password_env} is not set", file=sys.stderr)
        return 1
    run = m.RunResult(
        routine="test-email",
        run_id=logstore.new_run_id(),
        started_at="now",
        ended_at="now",
        overall_status=m.Status.SUCCESS,
        projects=[
            m.ProjectResult(
                name="example",
                path="/example",
                status=m.Status.SUCCESS,
                action=m.Action.PR,
                url="https://github.com/example/pull/1",
                title="ptt test email",
                summary="If you got this, your SMTP settings work.",
                verified=True,
                source=m.Source.CLAUDE,
                reason=None,
                branch="ptt/test/1",
                duration_s=0.0,
                log_dir="-",
            )
        ],
        run_dir="-",
    )
    text = notify.build_text(run)
    try:
        notify.send(
            notify.build_subject(run),
            text,
            notify.build_html(text),
            cfg.email,
            password,
        )
    except Exception as e:
        print(f"failed to send: {e}", file=sys.stderr)
        return 1
    print(f"test email sent to {cfg.email.to_addr}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="ptt",
        description="prompt-then-that: scheduled Claude runs on git projects",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("run", help="run a routine now")
    r.add_argument("routine")
    r.add_argument("--project", help="run only this project (path or owner/repo)")
    r.add_argument("--force", action="store_true", help="run even if disabled")
    r.set_defaults(fn=_cmd_run)

    sub.add_parser("list", help="list routines").set_defaults(fn=_cmd_list)

    lg = sub.add_parser("logs", help="show logs for a routine's run")
    lg.add_argument("routine")
    lg.add_argument("--run", help="run id (default: latest)")
    lg.add_argument("--project", help="also show this project's logs")
    lg.set_defaults(fn=_cmd_logs)

    ins = sub.add_parser("install", help="install systemd timer")
    ins.add_argument("routine")
    ins.set_defaults(fn=_cmd_install)

    un = sub.add_parser("uninstall", help="remove systemd timer")
    un.add_argument("routine")
    un.set_defaults(fn=_cmd_uninstall)

    wo = sub.add_parser(
        "wait-online", help="block until DNS resolves (used by the systemd timer)"
    )
    wo.add_argument("--host", default=netcheck.DEFAULT_HOST, help="host to resolve")
    wo.add_argument(
        "--timeout",
        type=float,
        default=netcheck.DEFAULT_TIMEOUT_S,
        help="seconds to keep trying before giving up",
    )
    wo.set_defaults(fn=_cmd_wait_online)

    sub.add_parser("doctor", help="check config + tooling").set_defaults(fn=_cmd_doctor)
    sub.add_parser("test-email", help="send a test email").set_defaults(
        fn=_cmd_test_email
    )
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except (config.ConfigError, schedule.ScheduleError, git_ops.GitError) as e:
        print(f"error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())

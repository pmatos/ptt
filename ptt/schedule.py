"""Generate, install, and remove systemd *user* timers for routines (§14).
All systemctl/systemd-analyze calls go through proc.run; if those tools are
absent we raise a clear ScheduleError rather than a raw non-zero."""

from __future__ import annotations

import getpass
import os
import shutil
import sys
from pathlib import Path

from ptt import proc
from ptt import models as m


class ScheduleError(Exception):
    pass


def units_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME")
    root = Path(base) if base else Path.home() / ".config"
    return root / "systemd" / "user"


def ptt_command() -> str:
    found = shutil.which("ptt")
    return found if found else f"{sys.executable} -m ptt"


def render_service(routine_name: str, ptt_cmd: str, path_env: str | None = None) -> str:
    lines = [
        "[Unit]",
        f"Description=ptt routine {routine_name}",
        "",
        "[Service]",
        "Type=oneshot",
        "EnvironmentFile=%h/.config/ptt/env",
    ]
    # Bake the install-time PATH so `claude`/`git`/`gh` resolve the way they do in
    # the user's shell. The systemd user manager's own PATH is sparse and typically
    # omits e.g. ~/.local/bin, which would leave subprocess("claude") unfindable.
    # The value is double-quoted (with C-style escaping) because systemd splits an
    # unquoted Environment= value on whitespace into separate assignments, which
    # would silently truncate a PATH entry that contains a space.
    if path_env:
        escaped = path_env.replace("\\", "\\\\").replace('"', '\\"')
        lines.append(f'Environment="PATH={escaped}"')
    lines.append(f"ExecStart={ptt_cmd} run {routine_name}")
    return "\n".join(lines) + "\n"


def render_timer(routine_name: str, schedule: str) -> str:
    return (
        "[Unit]\n"
        f"Description=ptt routine {routine_name} schedule\n\n"
        "[Timer]\n"
        f"OnCalendar={schedule}\n"
        "Persistent=true\n\n"
        "[Install]\n"
        "WantedBy=timers.target\n"
    )


def linger_note() -> str:
    user = getpass.getuser()
    return (
        f"One-time setup (needs sudo) so timers fire while logged out:\n"
        f"  sudo loginctl enable-linger {user}"
    )


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        raise ScheduleError(f"systemd user instance not available: missing {tool!r}")


def _systemctl(*args: str, check: bool = True) -> proc.Completed:
    r = proc.run(["systemctl", "--user", *args])
    if check and r.returncode != 0:
        raise ScheduleError(
            f"systemctl --user {' '.join(args)} failed: {r.stderr.strip()}"
        )
    return r


def validate_schedule(schedule: str) -> None:
    _require("systemd-analyze")
    r = proc.run(["systemd-analyze", "calendar", schedule])
    if r.returncode != 0:
        raise ScheduleError(f"invalid schedule {schedule!r}: {r.stderr.strip()}")


def install(routine: m.Routine) -> str:
    _require("systemctl")
    validate_schedule(routine.schedule)
    d = units_dir()
    d.mkdir(parents=True, exist_ok=True)
    cmd = ptt_command()
    (d / f"ptt-{routine.name}.service").write_text(
        render_service(routine.name, cmd, os.environ.get("PATH", ""))
    )
    (d / f"ptt-{routine.name}.timer").write_text(
        render_timer(routine.name, routine.schedule)
    )
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", f"ptt-{routine.name}.timer")
    return linger_note()


def uninstall(routine_name: str) -> None:
    _require("systemctl")
    _systemctl("disable", "--now", f"ptt-{routine_name}.timer", check=False)
    d = units_dir()
    for suffix in (".service", ".timer"):
        f = d / f"ptt-{routine_name}{suffix}"
        if f.exists():
            f.unlink()
    _systemctl("daemon-reload", check=False)


def list_timers() -> str:
    _require("systemctl")
    return _systemctl("list-timers", "--all", check=False).stdout

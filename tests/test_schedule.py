from pathlib import Path

import pytest

from ptt import schedule
from ptt import proc
from ptt import models as m


def _routine(name="audit", schedule_str="Mon..Fri 05:00"):
    return m.Routine(
        name=name,
        description="",
        enabled=True,
        prompt=Path("/x/p.md"),
        schedule=schedule_str,
        projects=[
            m.ProjectSpec(raw="/x/a", is_remote=False, location="/x/a", name="a")
        ],
        base_branch="main",
        permission_mode=m.PermissionMode.BYPASS,
        model=None,
        effort=None,
        timeout_minutes=30,
        work_dir=Path("/x/work"),
    )


@pytest.fixture
def all_ok(monkeypatch):
    """systemctl/systemd-analyze present and every command succeeds."""
    monkeypatch.setattr(schedule.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(
        schedule.proc, "run", lambda *a, **k: proc.Completed(0, "ok", "")
    )


def test_render_service_has_exec_envfile_oneshot():
    s = schedule.render_service("audit", "/usr/bin/ptt")
    assert "Type=oneshot" in s
    assert "EnvironmentFile=" in s and "/.config/ptt/env" in s
    assert "ExecStart=/usr/bin/ptt run audit" in s


def test_render_service_bakes_path_when_given():
    s = schedule.render_service("audit", "/usr/bin/ptt", "/home/me/.local/bin:/usr/bin")
    assert "Environment=PATH=/home/me/.local/bin:/usr/bin" in s
    # PATH must be set before ExecStart so the subprocess `claude` lookup can see it.
    assert s.index("Environment=PATH=") < s.index("ExecStart=")


def test_render_service_omits_path_line_when_empty():
    assert "Environment=PATH=" not in schedule.render_service(
        "audit", "/usr/bin/ptt", ""
    )


def test_render_timer_has_calendar_and_persistent():
    s = schedule.render_timer("audit", "Mon..Fri 05:00")
    assert "OnCalendar=Mon..Fri 05:00" in s
    assert "Persistent=true" in s
    assert "WantedBy=timers.target" in s


def test_install_writes_units_and_reloads(all_ok, tmp_xdg, monkeypatch):
    monkeypatch.setenv("PATH", "/home/me/.local/bin:/usr/bin")
    note = schedule.install(_routine())
    d = schedule.units_dir()
    svc = d / "ptt-audit.service"
    assert svc.is_file()
    assert (d / "ptt-audit.timer").is_file()
    # install captures the live PATH so `claude` resolves under systemd's sparse env.
    assert "Environment=PATH=/home/me/.local/bin:/usr/bin" in svc.read_text()
    assert "enable-linger" in note  # surfaces the one-time linger step


def test_validate_schedule_failure_raises(monkeypatch):
    monkeypatch.setattr(schedule.shutil, "which", lambda t: "/usr/bin/" + t)
    monkeypatch.setattr(
        schedule.proc, "run", lambda *a, **k: proc.Completed(1, "", "bad calendar")
    )
    with pytest.raises(schedule.ScheduleError):
        schedule.validate_schedule("not a calendar")


def test_missing_systemctl_raises(monkeypatch, tmp_xdg):
    monkeypatch.setattr(schedule.shutil, "which", lambda t: None)
    with pytest.raises(schedule.ScheduleError):
        schedule.install(_routine())


def test_uninstall_removes_units(all_ok, tmp_xdg):
    schedule.install(_routine())
    d = schedule.units_dir()
    assert (d / "ptt-audit.timer").is_file()
    schedule.uninstall("audit")
    assert not (d / "ptt-audit.timer").exists()
    assert not (d / "ptt-audit.service").exists()

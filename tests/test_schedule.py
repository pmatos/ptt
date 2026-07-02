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
        projects=[Path("/x/a")],
        base_branch="main",
        permission_mode=m.PermissionMode.BYPASS,
        model=None,
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


def test_render_timer_has_calendar_and_persistent():
    s = schedule.render_timer("audit", "Mon..Fri 05:00")
    assert "OnCalendar=Mon..Fri 05:00" in s
    assert "Persistent=true" in s
    assert "WantedBy=timers.target" in s


def test_install_writes_units_and_reloads(all_ok, tmp_xdg):
    note = schedule.install(_routine())
    d = schedule.units_dir()
    assert (d / "ptt-audit.service").is_file()
    assert (d / "ptt-audit.timer").is_file()
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

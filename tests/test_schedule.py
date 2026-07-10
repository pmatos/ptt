import os
from pathlib import Path

import pytest

from ptt import models as m
from ptt import proc, schedule


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


def test_render_service_gates_on_wait_online_before_exec():
    s = schedule.render_service("audit", "/usr/bin/ptt")
    # Non-blocking DNS gate ('-' prefix = ignore failure) placed before the run so
    # a resume-from-suspend race doesn't fail every clone on an unresolved host.
    assert "ExecStartPre=-/usr/bin/ptt wait-online" in s
    assert s.index("ExecStartPre=") < s.index("ExecStart=")


def test_render_service_bakes_path_under_ptt_path_when_given():
    s = schedule.render_service("audit", "/usr/bin/ptt", "/home/me/.local/bin:/usr/bin")
    # Baked under PTT_PATH — a name the secrets-only env file never sets — so an
    # env-file PATH= can't override it (EnvironmentFile= wins over a bare PATH=).
    # `ptt run` merges PTT_PATH into PATH at runtime.
    assert 'Environment="PTT_PATH=/home/me/.local/bin:/usr/bin"' in s
    # Never bake a bare PATH= (see above), so there is nothing for the env file to win.
    assert 'Environment="PATH=' not in s
    # Set before ExecStart so `ptt run` sees PTT_PATH.
    assert s.index('Environment="PTT_PATH=') < s.index("ExecStart=")


def test_render_service_quotes_path_with_spaces():
    # A PATH entry containing a space must stay intact — the whole assignment is
    # wrapped in double quotes rather than split by systemd at the space.
    s = schedule.render_service("audit", "/usr/bin/ptt", "/opt/my tools/bin:/usr/bin")
    assert 'Environment="PTT_PATH=/opt/my tools/bin:/usr/bin"' in s


def test_render_service_escapes_quotes_and_backslashes_in_path():
    s = schedule.render_service("audit", "/usr/bin/ptt", '/a\\b/bin:/q"x/bin')
    assert 'Environment="PTT_PATH=/a\\\\b/bin:/q\\"x/bin"' in s


def test_render_service_omits_path_line_when_empty():
    s = schedule.render_service("audit", "/usr/bin/ptt", "")
    assert 'Environment="PTT_PATH=' not in s
    assert 'Environment="PATH=' not in s


def test_apply_baked_path_prepends_and_dedups(monkeypatch):
    monkeypatch.setenv("PTT_PATH", "/home/me/.local/bin:/usr/bin")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    schedule.apply_baked_path()
    # Baked entries first, then any current entry not already present; no dupes.
    assert os.environ["PATH"] == "/home/me/.local/bin:/usr/bin:/bin"


def test_apply_baked_path_preserves_extra_env_file_dirs(monkeypatch):
    # A dir the env-file's PATH added keeps its place, appended after the baked dirs.
    monkeypatch.setenv("PTT_PATH", "/home/me/.local/bin:/usr/bin")
    monkeypatch.setenv("PATH", "/opt/extra/bin:/usr/bin")
    schedule.apply_baked_path()
    assert os.environ["PATH"] == "/home/me/.local/bin:/usr/bin:/opt/extra/bin"


def test_apply_baked_path_overrides_stale_env_file_path(monkeypatch):
    # Regression for #15: a stale/sparse PATH (what an env-file PATH= leaves under the
    # timer) must not hide the baked bin dir where `claude` actually lives.
    monkeypatch.setenv("PTT_PATH", "/home/me/.local/bin:/usr/bin")
    monkeypatch.setenv("PATH", "/usr/bin")  # omits ~/.local/bin
    schedule.apply_baked_path()
    assert os.environ["PATH"].split(":")[0] == "/home/me/.local/bin"


def test_apply_baked_path_noop_without_ptt_path(monkeypatch):
    monkeypatch.delenv("PTT_PATH", raising=False)
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    schedule.apply_baked_path()
    assert os.environ["PATH"] == "/usr/bin:/bin"


def test_apply_baked_path_noop_when_ptt_path_empty(monkeypatch):
    monkeypatch.setenv("PTT_PATH", "")
    monkeypatch.setenv("PATH", "/usr/bin:/bin")
    schedule.apply_baked_path()
    assert os.environ["PATH"] == "/usr/bin:/bin"


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
    # install captures the live PATH (baked under PTT_PATH) so `claude` resolves
    # under systemd's sparse env once `ptt run` merges it back into PATH.
    assert 'Environment="PTT_PATH=/home/me/.local/bin:/usr/bin"' in svc.read_text()
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

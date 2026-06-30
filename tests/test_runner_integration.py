"""Cross-cutting end-to-end checks that span multiple modules (run-id collision,
colliding project basenames, and the rendered email payload)."""
from pathlib import Path

from ptt import runner, logstore, notify
from ptt import models as m
from tests.test_runner import make_global, make_routine


def test_run_id_collision_makes_distinct_dirs(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "none")
    monkeypatch.setattr(logstore, "new_run_id", lambda: "20260630T050000Z")
    r = make_routine(tmp_path, [github_repo])
    run1 = runner.run_routine(r, make_global())
    run2 = runner.run_routine(r, make_global())
    assert run1.run_id != run2.run_id           # M5: second run gets a suffix
    assert Path(run1.run_dir).is_dir() and Path(run2.run_dir).is_dir()
    assert run1.run_dir != run2.run_dir


def test_two_projects_same_basename_get_distinct_logdirs(
        fake_bin, github_repo_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    a = github_repo_factory(tmp_path / "one" / "repo")
    b = github_repo_factory(tmp_path / "two" / "repo")   # same basename "repo"
    r = make_routine(tmp_path, [a, b])
    run = runner.run_routine(r, make_global())
    assert len(run.projects) == 2
    log_dirs = {p.log_dir for p in run.projects}
    assert len(log_dirs) == 2                    # m3: no collision


def test_project_filter_picks_correct_same_basename_repo(
        fake_bin, github_repo_factory, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    a = github_repo_factory(tmp_path / "one" / "repo")
    b = github_repo_factory(tmp_path / "two" / "repo")
    r = make_routine(tmp_path, [a, b])
    run = runner.run_routine(r, make_global(), only_project=str(b))
    assert len(run.projects) == 1
    assert run.projects[0].path == str(b.resolve())


def test_email_payload_flags_unverified(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "unverified")
    monkeypatch.setenv("PTT_POSTMARK_TOKEN", "tok")
    captured = {}
    monkeypatch.setattr(notify, "send",
                        lambda subj, text, html, cfg, token: captured.update(text=text))
    runner.run_routine(make_routine(tmp_path, [github_repo]), make_global())
    assert "(unverified)" in captured["text"]

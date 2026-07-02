from pathlib import Path

from ptt import projects as proj
from ptt import runner
from ptt import models as m


def _as_spec(p):
    if isinstance(p, m.ProjectSpec):
        return p
    s = str(p)
    return m.ProjectSpec(raw=s, is_remote=False, location=s, name=Path(s).name)


def make_global():
    return m.GlobalConfig(
        email=m.EmailConfig(
            "ptt@x.com",
            "p@x.com",
            m.EmailOn.ALWAYS,
            "smtp.example.com",
            587,
            m.SmtpSecurity.STARTTLS,
            "user",
            "PTT_SMTP_PASSWORD",
        ),
        defaults=m.Defaults(m.PermissionMode.BYPASS, 30, Path("/tmp/w"), "main"),
    )


def make_routine(tmp_path, projects, name="audit", enabled=True, timeout=30):
    prompt = tmp_path / "p.md"
    if not prompt.exists():
        prompt.write_text("Do the audit.")
    return m.Routine(
        name=name,
        description="",
        enabled=enabled,
        prompt=prompt,
        schedule="Mon..Fri 05:00",
        projects=[_as_spec(p) for p in projects],
        base_branch="main",
        permission_mode=m.PermissionMode.BYPASS,
        model=None,
        effort=None,
        timeout_minutes=timeout,
        work_dir=tmp_path / "ptt-work",
    )


def test_run_pr_is_verified_and_cleaned(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo])
    run = runner.run_routine(r, make_global())
    assert run.overall_status == m.Status.SUCCESS
    assert len(run.projects) == 1
    p = run.projects[0]
    assert p.action == m.Action.PR and p.verified is True
    assert runner.exit_code(run) == 0
    # run.json + per-project artifacts written
    assert (Path(run.run_dir) / "run.json").is_file()
    assert (Path(p.log_dir) / "result.json").is_file()
    assert (Path(p.log_dir) / "claude.stdout.jsonl").is_file()
    assert (Path(p.log_dir) / "git.log").is_file()
    # worktree cleaned up, including the now-empty run-id parent dir
    dest = r.work_dir / run.run_id / p.name
    assert not dest.exists()
    assert not (r.work_dir / run.run_id).exists()


def test_run_remote_clone_pr_and_cleaned(
    fake_bin, remote_github_repo, tmp_path, monkeypatch
):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    spec = proj.parse(remote_github_repo)  # "fake/repo" -> ephemeral clone
    assert spec.is_remote is True
    r = make_routine(tmp_path, [spec])
    run = runner.run_routine(r, make_global())
    assert run.overall_status == m.Status.SUCCESS
    p = run.projects[0]
    assert p.action == m.Action.PR and p.verified is True
    assert p.name == "repo"
    assert p.path == "fake/repo"
    # the ephemeral clone (and the now-empty run-id parent) are removed
    dest = r.work_dir / run.run_id / p.name
    assert not dest.exists()
    assert not (r.work_dir / run.run_id).exists()


def test_run_remote_commit_only_is_error_and_cleaned(
    fake_bin, remote_github_repo, tmp_path, monkeypatch
):
    # A remote run that commits but never pushes: the ephemeral clone is deleted,
    # so the commit is lost and the outcome must be an error, not a success.
    monkeypatch.setenv("PTT_FAKE_MODE", "commit")
    r = make_routine(tmp_path, [proj.parse(remote_github_repo)])
    run = runner.run_routine(r, make_global())
    p = run.projects[0]
    assert p.status == m.Status.ERROR
    assert "not pushed" in (p.reason or "")
    assert not (r.work_dir / run.run_id).exists()  # clone still cleaned up


def test_run_local_commit_only_is_success(fake_bin, github_repo, tmp_path, monkeypatch):
    # A local run keeps its worktree branch in the source repo, so a commit-only
    # outcome remains a verified success.
    monkeypatch.setenv("PTT_FAKE_MODE", "commit")
    r = make_routine(tmp_path, [github_repo])
    run = runner.run_routine(r, make_global())
    p = run.projects[0]
    assert p.status == m.Status.SUCCESS
    assert p.action == m.Action.COMMIT


def test_run_error_sets_overall_error_and_cleans(
    fake_bin, github_repo, tmp_path, monkeypatch
):
    monkeypatch.setenv("PTT_FAKE_MODE", "error")
    r = make_routine(tmp_path, [github_repo])
    run = runner.run_routine(r, make_global())
    assert run.overall_status == m.Status.ERROR
    assert runner.exit_code(run) != 0
    dest = r.work_dir / run.run_id / run.projects[0].name
    assert not dest.exists()  # B2: cleaned even on failure


def test_run_unverified(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "unverified")
    run = runner.run_routine(make_routine(tmp_path, [github_repo]), make_global())
    p = run.projects[0]
    assert p.action == m.Action.PR and p.verified is False


def test_run_none_is_no_action(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "none")
    run = runner.run_routine(make_routine(tmp_path, [github_repo]), make_global())
    assert run.projects[0].status == m.Status.NO_ACTION


def test_disabled_routine_skipped(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo], enabled=False)
    run = runner.run_routine(r, make_global())
    assert run.projects == []
    assert run.overall_status == m.Status.SUCCESS


def test_force_runs_disabled(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo], enabled=False)
    run = runner.run_routine(r, make_global(), force=True)
    assert len(run.projects) == 1


def test_project_filter_matches_by_path(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo])
    run = runner.run_routine(r, make_global(), only_project=str(github_repo))
    assert len(run.projects) == 1


def test_project_filter_no_match_is_error(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo])
    run = runner.run_routine(r, make_global(), only_project=str(tmp_path / "nope"))
    assert run.overall_status == m.Status.ERROR


def test_prompt_missing_fast_fail(fake_bin, github_repo, tmp_path, monkeypatch):
    monkeypatch.setenv("PTT_FAKE_MODE", "pr")
    r = make_routine(tmp_path, [github_repo])
    r.prompt.unlink()  # deleted after config load
    run = runner.run_routine(r, make_global())
    assert run.overall_status == m.Status.ERROR
    # fast-fails with a synthetic error before touching any real project
    assert [p.name for p in run.projects] == ["prompt"]
    assert not r.work_dir.exists()  # no worktree was ever created

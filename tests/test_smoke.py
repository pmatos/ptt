import subprocess

import ptt


def test_package_imports_with_version():
    assert ptt.__version__


def test_fake_bins_run(fake_bin, tmp_path):
    # gh auth status (faked) succeeds; proves the fake bins are on PATH + runnable.
    r = subprocess.run(
        ["gh", "auth", "status"], cwd=tmp_path, capture_output=True, text=True
    )
    assert r.returncode == 0


def test_github_repo_fixture_is_github_and_fetchable(github_repo):
    # config --get returns the raw stored URL (insteadOf is NOT applied here),
    # which is how is_github_repo must read it.
    url = subprocess.run(
        ["git", "-C", str(github_repo), "config", "--get", "remote.origin.url"],
        capture_output=True,
        text=True,
    ).stdout.strip()
    assert "github.com" in url
    # fetch resolves via insteadOf to the local bare repo
    r = subprocess.run(
        ["git", "-C", str(github_repo), "fetch", "origin", "main"],
        capture_output=True,
        text=True,
    )
    assert r.returncode == 0, r.stderr

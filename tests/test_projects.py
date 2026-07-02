from pathlib import Path

from ptt import projects


def test_absolute_local_path():
    s = projects.parse("/abs/b")
    assert s.is_remote is False
    assert s.location == "/abs/b"
    assert s.name == "b"


def test_home_local_path_is_expanded(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    s = projects.parse("~/dev/a")
    assert s.is_remote is False
    assert s.location == str(tmp_path / "dev" / "a")
    assert s.name == "a"


def test_owner_repo_shorthand_is_remote():
    s = projects.parse("pmatos/ptt")
    assert s.is_remote is True
    assert s.location == "https://github.com/pmatos/ptt.git"
    assert s.name == "ptt"
    assert s.raw == "pmatos/ptt"


def test_https_url_is_remote_and_strips_dotgit():
    s = projects.parse("https://github.com/pmatos/ptt.git")
    assert s.is_remote is True
    assert s.location == "https://github.com/pmatos/ptt.git"
    assert s.name == "ptt"


def test_https_url_without_dotgit():
    s = projects.parse("https://github.com/pmatos/ptt")
    assert s.is_remote is True
    assert s.name == "ptt"


def test_scp_style_url_is_remote():
    s = projects.parse("git@github.com:pmatos/ptt.git")
    assert s.is_remote is True
    assert s.location == "git@github.com:pmatos/ptt.git"
    assert s.name == "ptt"


def test_ssh_url_is_remote():
    s = projects.parse("ssh://git@github.com/pmatos/ptt.git")
    assert s.is_remote is True
    assert s.name == "ptt"


def test_relative_multi_segment_path_is_local():
    s = projects.parse("./dev/a")
    assert s.is_remote is False
    assert s.name == "a"
    assert s.location == str(Path("./dev/a").expanduser())

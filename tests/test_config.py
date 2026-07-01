from pathlib import Path

import pytest

from ptt import config
from ptt import models as m


def _write_global(cfg_home, body):
    d = cfg_home / "ptt"
    d.mkdir(parents=True, exist_ok=True)
    (d / "config.toml").write_text(body)


def _write_routine(cfg_home, name, body):
    d = cfg_home / "ptt" / "routines"
    d.mkdir(parents=True, exist_ok=True)
    (d / f"{name}.toml").write_text(body)


GOOD_GLOBAL = """
[email]
from = "ptt@x.com"
to = "p@x.com"
on = "changes"
smtp_host = "smtp.example.com"
smtp_username = "user"

[defaults]
permission_mode = "bypass"
timeout_minutes = 20
base_branch = "main"
"""


def test_load_global_config_parses_and_applies_defaults(tmp_xdg):
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    cfg = config.load_global_config()
    assert cfg.email.from_addr == "ptt@x.com"
    assert cfg.email.to_addr == "p@x.com"
    assert cfg.email.on == m.EmailOn.CHANGES
    assert cfg.email.smtp_host == "smtp.example.com"
    assert cfg.email.smtp_username == "user"
    assert cfg.email.smtp_security == m.SmtpSecurity.STARTTLS      # default
    assert cfg.email.smtp_port == 587                             # default by mode
    assert cfg.email.smtp_password_env == "PTT_SMTP_PASSWORD"     # default
    assert cfg.defaults.timeout_minutes == 20
    # work_dir defaulted under the cache dir
    assert cfg.defaults.work_dir == tmp_xdg["cache"] / "ptt" / "work"


def test_load_global_config_ssl_defaults_port_465(tmp_xdg):
    body = GOOD_GLOBAL.replace('smtp_host = "smtp.example.com"',
                               'smtp_host = "smtp.example.com"\nsmtp_security = "ssl"')
    _write_global(tmp_xdg["config"], body)
    cfg = config.load_global_config()
    assert cfg.email.smtp_security == m.SmtpSecurity.SSL
    assert cfg.email.smtp_port == 465


def test_load_global_config_none_defaults_port_25(tmp_xdg):
    body = ('[email]\nfrom = "a@x.com"\nto = "b@x.com"\n'
            'smtp_host = "smtp.example.com"\nsmtp_security = "none"\n')
    _write_global(tmp_xdg["config"], body)
    cfg = config.load_global_config()
    assert cfg.email.smtp_port == 25


def test_load_global_config_explicit_port_overrides_default(tmp_xdg):
    body = GOOD_GLOBAL.replace('smtp_host = "smtp.example.com"',
                               'smtp_host = "smtp.example.com"\nsmtp_port = 2525')
    _write_global(tmp_xdg["config"], body)
    cfg = config.load_global_config()
    assert cfg.email.smtp_port == 2525


def test_load_global_config_bad_smtp_security_raises(tmp_xdg):
    body = GOOD_GLOBAL.replace('smtp_host = "smtp.example.com"',
                               'smtp_host = "smtp.example.com"\nsmtp_security = "tls"')
    _write_global(tmp_xdg["config"], body)
    with pytest.raises(config.ConfigError):
        config.load_global_config()


def test_load_global_config_legacy_postmark_token_env_raises(tmp_xdg):
    body = ('[email]\nfrom = "a@x.com"\nto = "b@x.com"\n'
            'postmark_token_env = "PTT_POSTMARK_TOKEN"\n')
    _write_global(tmp_xdg["config"], body)
    with pytest.raises(config.ConfigError) as ei:
        config.load_global_config()
    assert "smtp_host" in str(ei.value)


def test_load_global_config_missing_smtp_host_raises(tmp_xdg):
    body = '[email]\nfrom = "a@x.com"\nto = "b@x.com"\n'
    _write_global(tmp_xdg["config"], body)
    with pytest.raises(config.ConfigError) as ei:
        config.load_global_config()
    assert "smtp_host" in str(ei.value)


def test_load_global_config_none_with_creds_remote_raises(tmp_xdg):
    body = ('[email]\nfrom = "a@x.com"\nto = "b@x.com"\n'
            'smtp_host = "smtp.example.com"\nsmtp_security = "none"\n'
            'smtp_username = "user"\n')
    _write_global(tmp_xdg["config"], body)
    with pytest.raises(config.ConfigError):
        config.load_global_config()


def test_load_global_config_none_with_creds_loopback_ok(tmp_xdg):
    body = ('[email]\nfrom = "a@x.com"\nto = "b@x.com"\n'
            'smtp_host = "127.0.0.1"\nsmtp_security = "none"\n'
            'smtp_username = "user"\n')
    _write_global(tmp_xdg["config"], body)
    cfg = config.load_global_config()
    assert cfg.email.smtp_host == "127.0.0.1"


def test_load_global_config_missing_email_fields_raises(tmp_xdg):
    _write_global(tmp_xdg["config"], "[email]\nfrom = \"a@x.com\"\n")
    with pytest.raises(config.ConfigError):
        config.load_global_config()


def test_load_global_config_bad_on_enum_raises(tmp_xdg):
    _write_global(tmp_xdg["config"],
                  "[email]\nfrom=\"a@x.com\"\nto=\"b@x.com\"\non=\"sometimes\"\n")
    with pytest.raises(config.ConfigError):
        config.load_global_config()


def _good_routine(prompt_path):
    return f"""
name = "code-audit"
description = "audit"
enabled = true
prompt = "{prompt_path}"
schedule = "Mon..Fri 05:00"
projects = ["~/dev/a", "/abs/b"]
"""


def test_load_routine_merges_defaults_and_expands_paths(tmp_xdg, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    prompt = tmp_path / "p.md"
    prompt.write_text("do the thing")
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    _write_routine(tmp_xdg["config"], "code-audit", _good_routine(prompt))
    cfg = config.load_global_config()
    r = config.load_routine("code-audit", cfg)
    assert r.name == "code-audit"
    assert r.prompt == prompt
    assert r.projects[0] == tmp_path / "dev" / "a"   # ~ expanded
    assert r.projects[1] == Path("/abs/b")
    assert r.permission_mode == m.PermissionMode.BYPASS  # from defaults
    assert r.timeout_minutes == 20                       # from defaults
    assert r.base_branch == "main"
    assert r.model is None


def test_load_routine_name_must_match_filename_stem(tmp_xdg, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("x")
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    _write_routine(tmp_xdg["config"], "audit",
                   _good_routine(prompt).replace('name = "code-audit"', 'name = "code-audit"'))
    cfg = config.load_global_config()
    with pytest.raises(config.ConfigError):
        config.load_routine("audit", cfg)  # file stem 'audit' != name 'code-audit'


def test_load_routine_bad_name_regex_raises(tmp_xdg, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("x")
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    _write_routine(tmp_xdg["config"], "Bad_Name",
                   _good_routine(prompt).replace('"code-audit"', '"Bad_Name"'))
    cfg = config.load_global_config()
    with pytest.raises(config.ConfigError):
        config.load_routine("Bad_Name", cfg)


def test_load_routine_empty_projects_raises(tmp_xdg, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("x")
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    body = _good_routine(prompt).replace('projects = ["~/dev/a", "/abs/b"]', "projects = []")
    _write_routine(tmp_xdg["config"], "code-audit", body)
    cfg = config.load_global_config()
    with pytest.raises(config.ConfigError):
        config.load_routine("code-audit", cfg)


def test_load_routine_missing_prompt_file_raises(tmp_xdg, tmp_path):
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    _write_routine(tmp_xdg["config"], "code-audit",
                   _good_routine(tmp_path / "nope.md"))
    cfg = config.load_global_config()
    with pytest.raises(config.ConfigError):
        config.load_routine("code-audit", cfg)


def test_load_routine_bad_permission_mode_raises(tmp_xdg, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("x")
    _write_global(tmp_xdg["config"], GOOD_GLOBAL)
    body = _good_routine(prompt) + '\npermission_mode = "yolo"\n'
    _write_routine(tmp_xdg["config"], "code-audit", body)
    cfg = config.load_global_config()
    with pytest.raises(config.ConfigError):
        config.load_routine("code-audit", cfg)


def test_list_routine_names_sorted(tmp_xdg, tmp_path):
    prompt = tmp_path / "p.md"
    prompt.write_text("x")
    for n in ("zeta", "alpha"):
        _write_routine(tmp_xdg["config"], n, _good_routine(prompt).replace('"code-audit"', f'"{n}"'))
    assert config.list_routine_names() == ["alpha", "zeta"]

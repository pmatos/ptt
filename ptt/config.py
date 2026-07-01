"""Load and validate ptt configuration: the global config and per-routine TOML
files under the XDG config dir. Repo existence is intentionally NOT checked here
(only at run time) so editing config never requires the projects to be present."""
from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from ptt import models as m

_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")


class ConfigError(Exception):
    pass


def _xdg(env: str, fallback: Path) -> Path:
    v = os.environ.get(env)
    base = Path(v) if v else fallback
    return base / "ptt"


def config_home() -> Path:
    return _xdg("XDG_CONFIG_HOME", Path.home() / ".config")


def state_home() -> Path:
    return _xdg("XDG_STATE_HOME", Path.home() / ".local" / "state")


def cache_home() -> Path:
    return _xdg("XDG_CACHE_HOME", Path.home() / ".cache")


def global_config_path() -> Path:
    return config_home() / "config.toml"


def routines_dir() -> Path:
    return config_home() / "routines"


def env_file_path() -> Path:
    return config_home() / "env"


def _load_toml(path: Path) -> dict:
    if not path.is_file():
        raise ConfigError(f"missing config file: {path}")
    try:
        with path.open("rb") as fh:
            return tomllib.load(fh)
    except tomllib.TOMLDecodeError as e:
        raise ConfigError(f"invalid TOML in {path}: {e}") from e


def _enum(cls, value, field):
    try:
        return cls(value)
    except ValueError:
        allowed = ", ".join(str(e) for e in cls)
        raise ConfigError(f"invalid {field} {value!r}; allowed: {allowed}")


_LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}

_DEFAULT_SMTP_PORT = {
    m.SmtpSecurity.STARTTLS: 587,
    m.SmtpSecurity.SSL: 465,
    m.SmtpSecurity.NONE: 25,
}


_SMTP_MIGRATION_HINT = (
    "ptt now sends over SMTP, not the Postmark API. Configure [email] with "
    "smtp_host, smtp_username and smtp_password_env (optionally smtp_security/"
    "smtp_port). Postmark over SMTP looks like:\n"
    '  smtp_host = "smtp.postmarkapp.com"\n'
    '  smtp_username = "<your-postmark-server-token>"\n'
    '  smtp_password_env = "PTT_SMTP_PASSWORD"   # env var holding that same token'
)


def _email_config(email: dict) -> m.EmailConfig:
    if "from" not in email or "to" not in email:
        raise ConfigError("[email] requires both 'from' and 'to'")
    if "postmark_token_env" in email:
        raise ConfigError(f"[email] 'postmark_token_env' is no longer supported. "
                          f"{_SMTP_MIGRATION_HINT}")
    if "smtp_host" not in email:
        raise ConfigError(f"[email] requires 'smtp_host'. {_SMTP_MIGRATION_HINT}")
    on = _enum(m.EmailOn, email.get("on", "always"), "email.on")
    security = _enum(m.SmtpSecurity, email.get("smtp_security", "starttls"),
                     "email.smtp_security")
    username = email.get("smtp_username")
    if (security == m.SmtpSecurity.NONE and username
            and email["smtp_host"] not in _LOOPBACK_HOSTS):
        raise ConfigError(
            '[email] refuses to send credentials over an unencrypted connection: '
            'smtp_security = "none" with smtp_username set is only allowed when '
            'smtp_host is loopback (localhost/127.0.0.1/::1). Use "starttls" or '
            '"ssl" to reach a remote host.')
    port = int(email.get("smtp_port", _DEFAULT_SMTP_PORT[security]))
    return m.EmailConfig(
        from_addr=email["from"],
        to_addr=email["to"],
        on=on,
        smtp_host=email["smtp_host"],
        smtp_port=port,
        smtp_security=security,
        smtp_username=username,
        smtp_password_env=email.get("smtp_password_env", "PTT_SMTP_PASSWORD"),
    )


def load_global_config() -> m.GlobalConfig:
    data = _load_toml(global_config_path())
    email_cfg = _email_config(data.get("email", {}))
    d = data.get("defaults", {})
    work_dir = (Path(d["work_dir"]).expanduser() if "work_dir" in d
                else cache_home() / "work")
    defaults = m.Defaults(
        permission_mode=_enum(m.PermissionMode,
                              d.get("permission_mode", "bypass"),
                              "defaults.permission_mode"),
        timeout_minutes=int(d.get("timeout_minutes", 30)),
        work_dir=work_dir,
        base_branch=d.get("base_branch", "main"),
    )
    return m.GlobalConfig(email=email_cfg, defaults=defaults)


def load_routine(name: str, global_cfg: m.GlobalConfig) -> m.Routine:
    path = routines_dir() / f"{name}.toml"
    data = _load_toml(path)
    dflt = global_cfg.defaults

    rname = data.get("name")
    if rname != name:
        raise ConfigError(f"routine 'name' ({rname!r}) must equal filename stem ({name!r})")
    if not _NAME_RE.match(rname):
        raise ConfigError(f"routine name {rname!r} must match {_NAME_RE.pattern}")

    if "prompt" not in data:
        raise ConfigError(f"routine {name!r} missing 'prompt'")
    prompt = Path(str(data["prompt"])).expanduser()
    if not prompt.is_file():
        raise ConfigError(f"routine {name!r} prompt not readable: {prompt}")

    projects_raw = data.get("projects", [])
    if not projects_raw:
        raise ConfigError(f"routine {name!r} must list at least one project")
    projects = [Path(str(p)).expanduser() for p in projects_raw]

    schedule = data.get("schedule")
    if not schedule:
        raise ConfigError(f"routine {name!r} missing 'schedule'")

    work_dir = (Path(str(data["work_dir"])).expanduser() if "work_dir" in data
                else dflt.work_dir)

    return m.Routine(
        name=rname,
        description=data.get("description", ""),
        enabled=bool(data.get("enabled", True)),
        prompt=prompt,
        schedule=str(schedule),
        projects=projects,
        base_branch=data.get("base_branch", dflt.base_branch),
        permission_mode=_enum(m.PermissionMode,
                              data.get("permission_mode", str(dflt.permission_mode)),
                              "permission_mode"),
        model=data.get("model"),
        timeout_minutes=int(data.get("timeout_minutes", dflt.timeout_minutes)),
        work_dir=work_dir,
    )


def list_routine_names() -> list[str]:
    d = routines_dir()
    if not d.is_dir():
        return []
    return sorted(p.stem for p in d.glob("*.toml"))

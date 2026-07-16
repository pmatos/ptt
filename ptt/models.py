"""Shared data types for ptt: config + run-result dataclasses and the enums
they use. Kept dependency-free so every other module can import it without
cycles."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

# Defaults for the outer retry `run_claude` does when `claude` exits on a transient
# API error (429/5xx, e.g. 529 Overloaded). Overridable in config via
# [defaults].api_max_retries / api_retry_base_seconds / api_retry_cap_seconds and
# per-routine keys of the same name.
DEFAULT_MAX_API_RETRIES = 3
DEFAULT_RETRY_BASE_S = 15.0
DEFAULT_RETRY_CAP_S = 120.0


class Status(StrEnum):
    SUCCESS = "success"
    NO_ACTION = "no_action"
    ERROR = "error"


class Action(StrEnum):
    PR = "pr"
    ISSUE_OPENED = "issue_opened"
    ISSUE_CLOSED = "issue_closed"
    COMMIT = "commit"
    NONE = "none"


class EmailOn(StrEnum):
    ALWAYS = "always"
    CHANGES = "changes"
    FAILURES = "failures"


class SmtpSecurity(StrEnum):
    STARTTLS = "starttls"
    SSL = "ssl"
    NONE = "none"


class PermissionMode(StrEnum):
    BYPASS = "bypass"
    ACCEPT_EDITS = "acceptEdits"


class Effort(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    XHIGH = "xhigh"
    MAX = "max"


class Source(StrEnum):
    CLAUDE = "claude"
    GH = "gh"


class BodyFormat(StrEnum):
    TEXT = "text"
    MARKDOWN = "markdown"


@dataclass
class EmailConfig:
    from_addr: str
    to_addr: str
    on: EmailOn
    smtp_host: str
    smtp_port: int
    smtp_security: SmtpSecurity
    smtp_username: str | None
    smtp_password_env: str


@dataclass
class Defaults:
    permission_mode: PermissionMode
    timeout_minutes: int
    work_dir: Path
    base_branch: str
    api_max_retries: int = DEFAULT_MAX_API_RETRIES
    api_retry_base_seconds: float = DEFAULT_RETRY_BASE_S
    api_retry_cap_seconds: float = DEFAULT_RETRY_CAP_S


@dataclass
class GlobalConfig:
    email: EmailConfig
    defaults: Defaults


@dataclass
class ProjectSpec:
    """A routine project entry, classified as either a local checkout or a remote
    GitHub repo to be cloned ephemerally. `location` is the filesystem path (local)
    or the clone URL (remote); `name` is the basename used for log dirs / --project."""

    raw: str
    is_remote: bool
    location: str
    name: str


@dataclass
class Routine:
    name: str
    description: str
    enabled: bool
    prompt: Path
    schedule: str
    projects: list[ProjectSpec]
    base_branch: str
    permission_mode: PermissionMode
    model: str | None
    effort: Effort | None
    timeout_minutes: int
    work_dir: Path
    api_max_retries: int = DEFAULT_MAX_API_RETRIES
    api_retry_base_seconds: float = DEFAULT_RETRY_BASE_S
    api_retry_cap_seconds: float = DEFAULT_RETRY_CAP_S


@dataclass
class CommandRoutine:
    """A projectless routine that runs a local command on a schedule and emails its
    stdout. ptt stays a pure scheduler here — no clone, no Claude, no gh. Mutually
    exclusive with the project-based `Routine`; the config discriminates on which of
    `command`/`projects` the routine's TOML carries."""

    name: str
    description: str
    enabled: bool
    schedule: str
    command: list[str]
    work_dir: Path
    timeout_minutes: int
    body_format: BodyFormat = BodyFormat.TEXT
    notify: bool = True


@dataclass
class Outcome:
    """What Claude *claimed* it did (from the stream-json structured_output)."""

    status: Status
    action: Action
    url: str | None
    title: str
    summary: str


@dataclass
class ProjectResult:
    name: str
    path: str
    status: Status
    action: Action
    url: str | None
    title: str
    summary: str
    verified: bool
    source: Source
    reason: str | None
    branch: str | None
    duration_s: float
    log_dir: str

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "path": self.path,
            "status": str(self.status),
            "action": str(self.action),
            "url": self.url,
            "title": self.title,
            "summary": self.summary,
            "verified": self.verified,
            "source": str(self.source),
            "reason": self.reason,
            "branch": self.branch,
            "duration_s": self.duration_s,
            "log_dir": self.log_dir,
        }


@dataclass
class RunResult:
    routine: str
    run_id: str
    started_at: str
    ended_at: str
    overall_status: Status
    projects: list[ProjectResult] = field(default_factory=list)
    run_dir: str = ""

    def to_dict(self) -> dict:
        return {
            "routine": self.routine,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "overall_status": str(self.overall_status),
            "run_dir": self.run_dir,
            "projects": [p.to_dict() for p in self.projects],
        }


@dataclass
class CommandRunResult:
    """The result of one command-routine run. A command run is strictly 1:1 (one run
    = one command, no project fan-out), so this is a single flat record — not a
    RunResult/ProjectResult split. The full stdout is NOT stored here; it lives in
    the run dir's command.stdout.log."""

    routine: str
    run_id: str
    started_at: str
    ended_at: str
    status: Status
    exit_code: int
    stdout_len: int
    reason: str | None
    command: list[str]
    duration_s: float
    run_dir: str

    def to_dict(self) -> dict:
        return {
            "routine": self.routine,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "status": str(self.status),
            "exit_code": self.exit_code,
            "stdout_len": self.stdout_len,
            "reason": self.reason,
            "command": self.command,
            "duration_s": self.duration_s,
            "run_dir": self.run_dir,
        }

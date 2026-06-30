"""Shared data types for ptt: config + run-result dataclasses and the enums
they use. Kept dependency-free so every other module can import it without
cycles."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path


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


class PermissionMode(StrEnum):
    BYPASS = "bypass"
    ACCEPT_EDITS = "acceptEdits"


class Source(StrEnum):
    CLAUDE = "claude"
    GH = "gh"


@dataclass
class EmailConfig:
    from_addr: str
    to_addr: str
    on: EmailOn
    postmark_token_env: str


@dataclass
class Defaults:
    permission_mode: PermissionMode
    timeout_minutes: int
    work_dir: Path
    base_branch: str


@dataclass
class GlobalConfig:
    email: EmailConfig
    defaults: Defaults


@dataclass
class Routine:
    name: str
    description: str
    enabled: bool
    prompt: Path
    schedule: str
    projects: list[Path]
    base_branch: str
    permission_mode: PermissionMode
    model: str | None
    timeout_minutes: int
    work_dir: Path


@dataclass
class Outcome:
    """What Claude *claimed* it did (parsed from .ptt-result.json)."""
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

"""Postmark email notification: decide whether to send (policy), render the
summary, and POST it. The token comes only from the caller and is never written
into the rendered message."""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path

from ptt import models as m

POSTMARK_URL = "https://api.postmarkapp.com/email"


class NotifyError(Exception):
    pass


def should_send(run: m.RunResult, on: m.EmailOn) -> bool:
    if on == m.EmailOn.ALWAYS:
        return True
    if on == m.EmailOn.CHANGES:
        return any(p.action != m.Action.NONE for p in run.projects)
    if on == m.EmailOn.FAILURES:
        return any(p.status == m.Status.ERROR for p in run.projects)
    return True


def build_subject(run: m.RunResult) -> str:
    prs = sum(1 for p in run.projects if p.action == m.Action.PR)
    issues = sum(1 for p in run.projects
                 if p.action in (m.Action.ISSUE_OPENED, m.Action.ISSUE_CLOSED))
    failed = sum(1 for p in run.projects if p.status == m.Status.ERROR)
    return f"[ptt] {run.routine} — {prs} PR, {issues} issue, {failed} failed"


def build_text(run: m.RunResult) -> str:
    lines = []
    for p in run.projects:
        if p.status == m.Status.ERROR:
            lines.append(f"❌ {p.name} — failed ({p.reason or 'error'}) — log: {p.log_dir}")
        elif p.action == m.Action.NONE:
            lines.append(f"⏭️  {p.name} — nothing to do")
        else:
            tag = "" if p.verified else " (unverified)"
            url = f" {p.url}" if p.url else ""
            lines.append(f'✅ {p.name} — {p.action}: "{p.title}"{url}{tag}')
    footer = f"\n— run {run.run_id} · {run.run_dir}"
    return "\n".join(lines) + footer


def build_html(text: str) -> str:
    return f"<pre>{text}</pre>"


def send(subject: str, text: str, html: str | None,
         email_cfg: m.EmailConfig, token: str) -> None:
    payload = {
        "From": email_cfg.from_addr,
        "To": email_cfg.to_addr,
        "Subject": subject,
        "TextBody": text,
    }
    if html:
        payload["HtmlBody"] = html
    req = urllib.request.Request(
        POSTMARK_URL, data=json.dumps(payload).encode(), method="POST",
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Postmark-Server-Token": token,
        },
    )
    with urllib.request.urlopen(req) as resp:
        status = getattr(resp, "status", 200)
        if not (200 <= status < 300):
            raise NotifyError(f"postmark returned {status}")


def notify(run: m.RunResult, email_cfg: m.EmailConfig, token: str | None,
           run_dir) -> None:
    """Send per policy; never raises. On repeated failure (or missing token) drop
    a .email-failed marker in the run dir so the failure is debuggable."""
    if not should_send(run, email_cfg.on):
        return
    marker = Path(run_dir) / ".email-failed"
    if not token:
        marker.write_text("no postmark token in environment")
        return
    subject = build_subject(run)
    text = build_text(run)
    html = build_html(text)
    last = None
    for _ in range(2):
        try:
            send(subject, text, html, email_cfg, token)
            return
        except Exception as e:  # noqa: BLE001 - email must never crash the run
            last = e
    marker.write_text(f"postmark send failed: {last}")

"""SMTP email notification: decide whether to send (policy), render the summary,
and hand it to any SMTP server. The password comes only from the caller and is
never written into the rendered message."""

from __future__ import annotations

import html
import re
import smtplib
from email.message import EmailMessage
from pathlib import Path

from ptt import models as m


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
    issues = sum(
        1
        for p in run.projects
        if p.action in (m.Action.ISSUE_OPENED, m.Action.ISSUE_CLOSED)
    )
    failed = sum(1 for p in run.projects if p.status == m.Status.ERROR)
    return f"[ptt] {run.routine} — {prs} PR, {issues} issue, {failed} failed"


def build_text(run: m.RunResult) -> str:
    lines = []
    for p in run.projects:
        if p.status == m.Status.ERROR:
            claim = (
                f' — claude claims {p.action}: "{p.title}" {p.url}'
                if p.url and p.action != m.Action.NONE
                else ""
            )
            lines.append(
                f"❌ {p.name} — failed ({p.reason or 'error'}){claim} — "
                f"log: {p.log_dir}"
            )
        elif p.action == m.Action.NONE:
            lines.append(f"⏭️  {p.name} — nothing to do")
        else:
            tag = "" if p.verified else " (unverified)"
            url = f" {p.url}" if p.url else ""
            lines.append(f'✅ {p.name} — {p.action}: "{p.title}"{url}{tag}')
    footer = f"\n— run {run.run_id} · {run.run_dir}"
    return "\n".join(lines) + footer


def build_html(text: str) -> str:
    # Escape so arbitrary content (command stdout, titles) can't inject live markup
    # into the HTML alternative — it must render as literal text inside the <pre>.
    return f"<pre>{html.escape(text)}</pre>"


def send(
    subject: str,
    text: str,
    html: str | None,
    email_cfg: m.EmailConfig,
    password: str | None,
) -> None:
    msg = EmailMessage()
    msg["From"] = email_cfg.from_addr
    msg["To"] = email_cfg.to_addr
    msg["Subject"] = subject
    msg.set_content(text)
    if html:
        msg.add_alternative(html, subtype="html")
    if email_cfg.smtp_security == m.SmtpSecurity.SSL:
        client = smtplib.SMTP_SSL(email_cfg.smtp_host, email_cfg.smtp_port)
    else:
        client = smtplib.SMTP(email_cfg.smtp_host, email_cfg.smtp_port)
    with client as s:
        if email_cfg.smtp_security == m.SmtpSecurity.STARTTLS:
            s.starttls()
        if email_cfg.smtp_username and password is not None:
            s.login(email_cfg.smtp_username, password)
        s.send_message(msg)


def notify(
    run: m.RunResult, email_cfg: m.EmailConfig, password: str | None, run_dir
) -> None:
    """Send per policy; never raises. On repeated failure (or a missing password)
    drop a .email-failed marker in the run dir so the failure is debuggable."""
    if not should_send(run, email_cfg.on):
        return
    marker = Path(run_dir) / ".email-failed"
    if email_cfg.smtp_username and not password:
        marker.write_text(f"no SMTP password in ${email_cfg.smtp_password_env}")
        return
    subject = build_subject(run)
    text = build_text(run)
    _send_with_marker(subject, text, build_html(text), email_cfg, password, marker)


def _send_with_marker(subject, text, html_body, email_cfg, password, marker) -> None:
    """Attempt to send (one retry); on repeated failure drop the debuggable marker.
    Shared by the project (`notify`) and command (`notify_command`) send paths."""
    last = None
    for _ in range(2):
        try:
            send(subject, text, html_body, email_cfg, password)
            return
        except Exception as e:  # email must never crash the run
            last = e
    marker.write_text(f"SMTP send failed: {last}")


# ---------- command routines ----------


def md_to_html(md: str) -> str:
    """Render a small Markdown subset (headings, bold, italic, lists, paragraphs)
    to HTML for a command routine's digest email. Deliberately minimal — not a
    general Markdown engine — and always HTML-escapes text so command output can
    never inject raw markup."""

    def inline(t: str) -> str:
        t = html.escape(t)
        t = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", t)
        t = re.sub(r"\*(?!\s)(.+?)\*", r"<em>\1</em>", t)
        return t

    out = [
        '<div style="font-family:system-ui,sans-serif;max-width:720px;line-height:1.5">'
    ]
    inlist = False
    for line in md.splitlines():
        if line.startswith("### "):
            if inlist:
                out.append("</ul>")
                inlist = False
            out.append(f"<h3 style='margin:.8em 0 .2em'>{inline(line[4:])}</h3>")
        elif line.startswith("## "):
            if inlist:
                out.append("</ul>")
                inlist = False
            out.append(
                f"<h2 style='border-bottom:1px solid #ddd;padding-bottom:.2em'>"
                f"{inline(line[3:])}</h2>"
            )
        elif line.startswith("# "):
            if inlist:
                out.append("</ul>")
                inlist = False
            out.append(f"<h1>{inline(line[2:])}</h1>")
        elif line.startswith("- "):
            if not inlist:
                out.append("<ul>")
                inlist = True
            out.append(f"<li>{inline(line[2:])}</li>")
        elif not line.strip():
            if inlist:
                out.append("</ul>")
                inlist = False
        else:
            if inlist:
                out.append("</ul>")
                inlist = False
            out.append(f"<p style='margin:.2em 0'>{inline(line)}</p>")
    if inlist:
        out.append("</ul>")
    out.append("</div>")
    return "\n".join(out)


def should_send_command(run: m.CommandRunResult, on: m.EmailOn) -> bool:
    if on == m.EmailOn.FAILURES:
        return run.status == m.Status.ERROR
    if on == m.EmailOn.CHANGES:
        return run.stdout_len > 0
    return True  # ALWAYS (and the safe default)


def build_command_subject(run: m.CommandRunResult) -> str:
    state = (
        "ok" if run.status != m.Status.ERROR else f"failed ({run.reason or 'error'})"
    )
    return f"[ptt] {run.routine} — {state}"


def build_command_body(run: m.CommandRunResult, stdout_text: str) -> str:
    return f"{stdout_text}\n— run {run.run_id} · {run.run_dir}"


def build_command_html(body: str, fmt: m.BodyFormat) -> str:
    return md_to_html(body) if fmt == m.BodyFormat.MARKDOWN else build_html(body)


def notify_command(
    run: m.CommandRunResult,
    notify_enabled: bool,
    body_format: m.BodyFormat,
    email_cfg: m.EmailConfig,
    password: str | None,
    run_dir,
    stdout_text: str,
) -> None:
    """Email a command routine's stdout per policy; never raises. `notify_enabled`
    is the routine's own master switch (False = the command delivers its own output,
    so ptt stays silent). On repeated failure (or a missing password) drop a
    .email-failed marker in the run dir."""
    if not notify_enabled or not should_send_command(run, email_cfg.on):
        return
    marker = Path(run_dir) / ".email-failed"
    if email_cfg.smtp_username and not password:
        marker.write_text(f"no SMTP password in ${email_cfg.smtp_password_env}")
        return
    subject = build_command_subject(run)
    body = build_command_body(run, stdout_text)
    html_body = build_command_html(body, body_format)
    _send_with_marker(subject, body, html_body, email_cfg, password, marker)

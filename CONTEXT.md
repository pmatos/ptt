# ptt

Runs Markdown prompts through Claude Code against git projects on a schedule,
opens PRs/issues, and emails a per-run summary. This glossary pins the language
we use so provider names don't leak back into the design.

## Language

### Email

**Transport**:
The wire protocol ptt uses to hand a rendered message to an email service. ptt
speaks SMTP, and only SMTP — no provider-specific HTTP APIs.
_Avoid_: backend, driver, gateway.

**Email service**:
The external service that actually accepts and delivers the mail (Postmark,
Amazon SES, Gmail, a self-hosted MTA). ptt is service-agnostic: the user names one
by giving its SMTP connection settings. "Postmark" is one example service, never a
synonym for "the email service".
_Avoid_: provider, backend, Postmark (as a generic term).

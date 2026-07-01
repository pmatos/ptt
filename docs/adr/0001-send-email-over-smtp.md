---
status: accepted
---

# Send email over SMTP, not a provider HTTP API

ptt originally sent run summaries through Postmark's HTTP API, hardwiring one
email service into both the code (`api.postmarkapp.com`, `X-Postmark-Server-Token`)
and the config (`postmark_token_env`). To let anyone use ptt with any email service
without lock-in, ptt now sends **exclusively over SMTP** (stdlib `smtplib` +
`email.message.EmailMessage`), configured with discrete `[email]` fields
(`smtp_host`, `smtp_port`, `smtp_security`, `smtp_username`, `smtp_password_env`).
Postmark becomes just one SMTP endpoint among many (Gmail, Amazon SES, a
self-hosted MTA, …) rather than a name baked into the design.

## Considered options

- **Pluggable transports** — keep the Postmark HTTP backend and add SMTP behind an
  `email.transport` selector (Django `EMAIL_BACKEND` style). Rejected: once SMTP can
  reach Postmark too, the second code path is redundant machinery, working against
  ptt's stdlib-only, single-purpose ethos.
- **Auto-mapping legacy config** — silently translate old Postmark config into
  Postmark-over-SMTP. Rejected: it re-bakes "Postmark" into the code as a special
  case — the exact coupling this change removes. ptt is pre-1.0 and single-user, so
  a clean hard break is cheaper than a compat shim.

## Consequences

- **Breaking config change.** `postmark_token_env` is removed. A config that still
  carries it, or that lacks `smtp_host`, fails loudly at load with a `ConfigError`
  that names the new keys and shows a Postmark-over-SMTP example.
- **Secrets stay out of the file.** The config holds only the *name* of the env var
  (`smtp_password_env`), never the literal password — as `postmark_token_env` did.
- **No cleartext credentials to a remote host.** `smtp_security = "none"` with
  credentials set is refused unless the host is loopback.
- `smtp_security` is an enum (`starttls` | `ssl` | `none`), defaulting to `starttls`;
  `smtp_port` defaults by mode (587 / 465 / 25).

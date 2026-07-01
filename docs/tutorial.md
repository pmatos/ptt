# ptt Tutorial

A hands-on walkthrough: from nothing to a routine that runs a prompt through Claude on
your repos every weekday and emails you the results. It should take ~15 minutes.

If you just want the command reference, see the [README](../README.md). This document is
the step-by-step version and is kept in sync with every feature — start here.

## What you'll build

A routine called `code-audit` that, every weekday at 05:00, runs a refactoring-audit
prompt through Claude against one of your GitHub projects (in an isolated worktree),
lets Claude open a PR when it finds something worth doing, and emails you a summary.

## 0. Prerequisites

- **uv** — <https://docs.astral.sh/uv/> (installs the right Python for you)
- **git** and the **GitHub CLI** (`gh`), authenticated:
  ```bash
  gh auth login
  ```
- **A GitHub project** to point ptt at — a local clone whose `origin` is on github.com.
- **An email account you can send through over SMTP** — any provider works (Postmark,
  Amazon SES, Gmail, Fastmail, a self-hosted MTA). You'll need its SMTP host, a username,
  and a password/token. If the provider verifies senders (Postmark, SES), make sure the
  address you send *from* is verified.

## 1. Get ptt

Clone it and either install the `ptt` command onto your PATH, or run it via `uv run`:

```bash
git clone https://github.com/pmatos/ptt.git
cd ptt

uv tool install .        # installs `ptt` into ~/.local/bin  (recommended)
# — or, to run from the clone without installing:
uv sync                  # creates .venv with everything
uv run ptt --help        # then prefix every command below with `uv run`
```

Verify:

```bash
ptt --help
```

## 2. Write a prompt

The prompt is just a Markdown file — whatever you want Claude to do on each project.
Create `~/prompts/refactor-audit.md`:

```markdown
Audit this codebase for one high-impact refactoring opportunity that reduces
complexity or removes duplication. If you find one, implement it with tests and
open a pull request describing the change. If nothing is worth doing, stop and
report that briefly.
```

> ptt automatically appends instructions telling Claude to open PRs/issues with `gh`
> and to record what it did — you don't need to add that yourself.

## 3. Global config

ptt reads its config from `~/.config/ptt/` (honoring `XDG_CONFIG_HOME`).

Create `~/.config/ptt/config.toml`:

```toml
[email]
from = "ptt@yourdomain.com"     # a sender your SMTP provider accepts
to   = "you@yourdomain.com"
on   = "always"                 # always | changes | failures

# SMTP transport — point these at any provider:
smtp_host     = "smtp.postmarkapp.com"
smtp_username = "your-postmark-server-token"
# smtp_security   = "starttls"          # starttls (default) | ssl | none
# smtp_port       = 587                 # defaults by security: 587 / 465 / 25
# smtp_password_env = "PTT_SMTP_PASSWORD"   # name of the env var holding the password

[defaults]
permission_mode = "bypass"      # see "About permissions" below
timeout_minutes = 30
base_branch = "main"
```

`on` controls when you get email:

| value      | emails when…                                  |
|------------|-----------------------------------------------|
| `always`   | every run completes                           |
| `changes`  | at least one project did something (PR/issue) |
| `failures` | at least one project failed                   |

**ptt speaks SMTP and nothing else**, so any provider works — you just change the three
`smtp_*` values. A few worked examples:

| Provider   | `smtp_host`               | `smtp_username`          | password (in the env var) |
|------------|---------------------------|--------------------------|---------------------------|
| Postmark   | `smtp.postmarkapp.com`    | your Server API token    | the *same* Server API token |
| Amazon SES | `email-smtp.<region>.amazonaws.com` | your SMTP username | your SMTP password        |
| Gmail      | `smtp.gmail.com`          | your full address        | an app password           |

All three use the default `smtp_security = "starttls"` on port 587. Use `ssl` (port 465)
for implicit-TLS providers, or `none` (port 25) **only** for a trusted local relay — ptt
refuses to send a username/password over an unencrypted connection to a remote host.

## 4. Store your SMTP password

The password/token is read from an environment variable (named by `smtp_password_env`,
default `PTT_SMTP_PASSWORD`) — never from the config file. Put it in `~/.config/ptt/env`,
which the scheduled timer loads:

```bash
mkdir -p ~/.config/ptt
printf 'PTT_SMTP_PASSWORD=your-smtp-password-or-token\n' > ~/.config/ptt/env
chmod 600 ~/.config/ptt/env
```

> **Postmark:** use your Server API token here — it doubles as both the SMTP username
> (above) and the SMTP password.

For running by hand in your shell, also export it (or `source` the file):

```bash
export PTT_SMTP_PASSWORD=your-smtp-password-or-token
```

The password is only ever read from the environment — it is never written to logs or email.

## 5. Define the routine

One TOML file per routine, in `~/.config/ptt/routines/`. **The filename stem must equal
`name`.** Create `~/.config/ptt/routines/code-audit.toml`:

```toml
name = "code-audit"
description = "Weekday refactoring audit"
enabled = true

prompt = "~/prompts/refactor-audit.md"
schedule = "Mon..Fri 05:00"          # systemd OnCalendar syntax
projects = ["~/dev/yourproject"]     # one or more local git repos

# optional (fall back to [defaults]):
# base_branch = "main"
# permission_mode = "bypass"
# model = "claude-opus-4-8"
# timeout_minutes = 30
```

The `schedule` uses systemd's `OnCalendar` syntax. A few examples:

| you want…              | schedule                |
|------------------------|-------------------------|
| weekdays at 05:00      | `Mon..Fri 05:00`        |
| every day at 09:30     | `*-*-* 09:30:00`        |
| every Monday           | `Mon *-*-* 08:00:00`    |

Check any expression with `systemd-analyze calendar "Mon..Fri 05:00"`.

## 6. Validate the setup

```bash
ptt validate
```

You want all green:

```
✓ claude on PATH
✓ git on PATH
✓ gh on PATH
✓ gh authenticated
✓ global config loads
✓ smtp password ($PTT_SMTP_PASSWORD)
✓ routine code-audit
```

Any `✗` tells you exactly what to fix before scheduling.

## 7. Send a test email

Confirm your SMTP settings work before relying on them:

```bash
ptt test-email
# -> test email sent to you@yourdomain.com
```

If it fails, the message shows the SMTP error (common causes: wrong password/token, a
`from` address the provider won't accept, or the wrong host/port/security combination).

## 8. Do a manual run first

Never wait for 05:00 to find out it works. Run it now:

```bash
ptt run code-audit
```

You'll see a summary printed, and get the email:

```
[ptt] code-audit — 1 PR, 0 issue, 0 failed
✅ yourproject — pr: "Extract config loader" https://github.com/you/yourproject/pull/42
— run 20260701T090012Z · ~/.local/state/ptt/runs/code-audit/20260701T090012Z
```

Useful variants:

```bash
ptt run code-audit --project ~/dev/yourproject   # just one project
ptt run code-audit --force                        # run even if disabled
```

Each project runs in a throwaway git worktree under `~/.cache/ptt/work` on a fresh
`ptt/code-audit/<run-id>` branch, so your real checkout is never touched. If Claude opens
a PR, the branch is pushed to your remote; the local worktree is cleaned up afterward.

## 9. Inspect what happened

Everything is logged under `~/.local/state/ptt/runs/<routine>/<run-id>/`:

```
run.json                     # overall status + per-project outcomes
prompt.md                    # the exact prompt used
projects/yourproject/
  result.json                # this project's outcome (verified against gh)
  claude.stdout.jsonl        # raw Claude output
  claude.stderr.log
  git.log                    # every git/gh command + output
```

Browse it with:

```bash
ptt logs code-audit                                   # latest run's run.json
ptt logs code-audit --run 20260701T090012Z            # a specific run
ptt logs code-audit --project yourproject             # + that project's raw logs
```

In `result.json`, note `verified`: ptt cross-checks Claude's claim ("I opened a PR")
against a real `gh` before/after diff. A claimed-but-unconfirmed action is reported as
`(unverified)` in the email so you know to look closer.

## 10. Schedule it

Install the systemd **user** timer:

```bash
ptt install code-audit
```

```
installed timer for 'code-audit' (Mon..Fri 05:00)
One-time setup (needs sudo) so timers fire while logged out:
  sudo loginctl enable-linger <you>
```

Run that `enable-linger` line once (the only step needing sudo) so the routine fires even
when you're not logged in. Confirm it's scheduled:

```bash
ptt list                                  # routines + enabled state + next-run times
systemctl --user list-timers | grep ptt   # the raw systemd view
```

## 11. Iterating day to day

- **Change the prompt or projects** — just edit the files; the next run picks them up.
- **Pause without deleting** — set `enabled = false` in the routine (the timer skips it),
  or run manually with `--force`.
- **Stop scheduling** — `ptt uninstall code-audit` removes the timer.

## About permissions

Unattended runs can't answer interactive approval prompts, so the default
`permission_mode = "bypass"` runs Claude with `--dangerously-skip-permissions`. The blast
radius is limited to the throwaway worktree, but `git`/`gh` act with *your* credentials —
that's intentional, it's how PRs get opened. Set `permission_mode = "acceptEdits"` on a
routine if you want a more conservative mode (note: it won't be able to push/open PRs
unattended).

## Troubleshooting

| Symptom                              | Likely cause / fix                                                    |
|--------------------------------------|----------------------------------------------------------------------|
| `✗ gh authenticated`                 | Run `gh auth login`.                                                  |
| `✗ smtp password`                    | Set `PTT_SMTP_PASSWORD` (env + `~/.config/ptt/env`).                  |
| `[email] refuses to send credentials…` | `smtp_security = "none"` with a username only works on a loopback host; use `starttls`/`ssl` for a remote host. |
| Project shows `not a GitHub repo`    | Its `origin` remote must point at github.com.                         |
| No email arrived                     | Check `on` policy; look for a `.email-failed` marker in the run dir.  |
| Action shown as `(unverified)`       | Claude claimed a PR/issue `gh` couldn't confirm — check `git.log`.    |
| Project `failed (timeout)`           | Raise `timeout_minutes` for the routine.                             |
| Timer never fires while logged out   | Run `sudo loginctl enable-linger <you>`.                              |

For deeper debugging, read `claude.stderr.log` and `git.log` in the run's project dir.

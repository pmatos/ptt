# ptt Tutorial

A hands-on walkthrough: from nothing to a routine that runs a prompt through Claude on
your repos every weekday and emails you the results. It should take ~15 minutes.

If you just want the command reference, see the [README](../README.md). This document is
the step-by-step version and is kept in sync with every feature — start here.

## What you'll build

A routine called `code-audit` that, every weekday at 05:00, runs a refactoring-audit
prompt through Claude against one of your GitHub projects (in a throwaway clone of the remote),
lets Claude open a PR when it finds something worth doing, and emails you a summary.

## 0. Prerequisites

- **uv** — <https://docs.astral.sh/uv/> (installs the right Python for you)
- **git** and the **GitHub CLI** (`gh`), authenticated:
  ```bash
  gh auth login
  ```
- **A GitHub project** to point ptt at — either a local clone whose `origin` is on
  github.com, or a remote you name as `gh:owner/repo` / a git URL. Either way ptt clones the
  remote fresh each run and deletes it afterward (a local clone is only read for its
  `origin`); private repos just need credentials `git`/`gh` can already use.
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

# Transient API-error retry (see step 9). Defaults shown; tune or omit:
# api_max_retries        = 3      # extra re-invocations after a 429/5xx (e.g. 529)
# api_retry_base_seconds = 15     # first backoff; doubles each retry
# api_retry_cap_seconds  = 120    # upper bound on any single backoff
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

The scheduled timer loads this file automatically, but **manual runs do not** — for
`ptt run`, `ptt doctor`, or `ptt test-email` from your shell, load it yourself first:

```bash
set -a; source ~/.config/ptt/env; set +a
```

(`set -a` auto-exports every variable the file assigns; `set +a` turns that back off.)
If you skip this, `ptt doctor` reports the password as missing and prints this same
reminder.

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
projects = ["~/dev/yourproject"]     # local paths and/or remote repos (see below)

# optional (fall back to [defaults]):
# base_branch = "main"
# permission_mode = "bypass"
# model = "claude-opus-4-8"
# effort = "high"                    # low | medium | high | xhigh | max
# timeout_minutes = 30
# api_max_retries = 3                # transient-API-error retry; see step 9
# api_retry_base_seconds = 15
# api_retry_cap_seconds = 120
```

**`effort`** picks Claude's reasoning effort for the run (passed through as
`claude --effort`). Higher effort means the model thinks harder (and costs more); leave it
unset to use the model's default. Which levels a model accepts varies — Opus takes
`low`/`medium`/`high`/`max`.

**Remote projects** — a `projects` entry doesn't have to be on disk. Alongside local paths
you can list a GitHub repo directly, and ptt treats it the same as a local one: clone it
fresh, run the prompt, delete the clone:

```toml
projects = [
  "~/dev/yourproject",                    # local checkout (read-only, only for its origin URL)
  "gh:yourname/some-repo",                # owner/repo shorthand → cloned + deleted
  "https://github.com/yourname/other",    # full URL also works (also git@… SSH)
]
```

The `gh:` marker is required for the `owner/repo` shorthand: a bare `yourname/some-repo`
is read as a **relative local path**, so a real local checkout like `dev/repo` is never
mistaken for a GitHub slug and cloned from the wrong repo. Remotes must therefore opt in
with `gh:` or a full URL.

Every entry — local or remote — is run against a fresh clone of its github.com remote under
`work_dir` (default `~/.cache/ptt/work`), removed after each run. A local checkout is only
read (for its `origin` URL); ptt never runs in it, fetches into it, or branches it. Private
repos work as long as `git`/`gh` already have credentials for them. To run just one of the
projects, `ptt run <routine> --project <owner/repo>` selects a remote by its `owner/repo`
slug (the `gh:` form, a full URL, or a local path works too).

The `schedule` uses systemd's `OnCalendar` syntax. A few examples:

| you want…              | schedule                |
|------------------------|-------------------------|
| weekdays at 05:00      | `Mon..Fri 05:00`        |
| every day at 09:30     | `*-*-* 09:30:00`        |
| every Monday           | `Mon *-*-* 08:00:00`    |

Check any expression with `systemd-analyze calendar "Mon..Fri 05:00"`.

## 6. Check the setup

```bash
ptt doctor
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

Every project — local or remote — runs in a **throwaway clone of its github.com remote**
under `~/.cache/ptt/work` on a fresh `ptt/code-audit/<run-id>` branch. A local checkout is
never run in, fetched into, or branched (ptt only reads its `origin` URL). If Claude opens
a PR, the branch is pushed to the remote; the clone is deleted afterward.

Because the clone is deleted the moment Claude's turn ends, each run is **one-shot**: Claude
is not re-invoked, so the prompt footer tells it to verify synchronously and finish within
the turn, and ptt disables the schedule-and-wait tools (`ScheduleWakeup`, `Monitor`,
`CronCreate`) so it can't background work and stall. If Claude still backgrounds a
long-running check (e.g. a full test suite) and ends its turn waiting to be resumed, that
work is killed and any local-only commit is discarded. Claude's final result is forced to a
fixed JSON schema via `claude --json-schema` (it comes back in the stream-json `result`
event's `structured_output`), so the outcome no longer depends on Claude remembering to write
a file; if it somehow ends with no valid result and `gh` shows no new PR/issue, the run is
recorded as an error (`claude produced no structured result`).

## 9. Inspect what happened

Everything is logged under `~/.local/state/ptt/runs/<routine>/<run-id>/`:

```
run.json                     # overall status + per-project outcomes
prompt.md                    # the exact prompt used
projects/yourproject/
  result.json                # this project's outcome (verified against gh)
  claude.stdout.jsonl        # raw Claude output
  claude.stderr.log
  claude.retries.log         # only present if a transient API error forced a retry
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

**Transient API errors are retried.** If `claude` exhausts its own internal retries and
exits because Anthropic's API was overloaded or rate-limited (HTTP 429/5xx, e.g. a `529
Overloaded`), ptt re-invokes it a few times with exponential backoff before giving up on
the project — a single availability blip no longer fails an otherwise-fine run. When that
happens you'll find a `claude.retries.log` in the project's log dir listing each retry;
the canonical `claude.stdout.jsonl` keeps the final attempt. Because every attempt reuses
the same throwaway clone, ptt first resets it (`git reset --hard` + `git clean`) between
attempts, so any edits or an unpushed commit a failed attempt left behind can't leak into
the retry and be mis-reported as "nothing to do". Non-transient failures (a timeout, a
`4xx`, or Claude reporting an error itself) are **not** retried.

Tune it in `[defaults]` (or per routine) — the defaults give backoffs of 15s → 30s → 60s:

| key                      | default | meaning                                             |
|--------------------------|---------|-----------------------------------------------------|
| `api_max_retries`        | `3`     | extra re-invocations after the first attempt fails  |
| `api_retry_base_seconds` | `15`    | first backoff; doubles on each subsequent retry     |
| `api_retry_cap_seconds`  | `120`   | ceiling on any single backoff                       |

Set `api_max_retries = 0` to disable the outer retry entirely (one attempt, as before).

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

Install bakes your **current `PATH`** into the service unit so the scheduled run finds
`claude`, `git`, and `gh` exactly where your shell does. This matters because systemd's
user manager runs with a sparse `PATH` that usually omits `~/.local/bin` (where `claude`
typically lives) — without this you'd see `No such file or directory: 'claude'` in the
run log. It's baked as `Environment="PTT_PATH=…"` rather than `PATH` itself, so even a
`PATH=` line you added to `~/.config/ptt/env` can't shadow it (systemd lets an
`EnvironmentFile=` entry override `Environment=`); `ptt run` folds `PTT_PATH` back into
`PATH` before it shells out. Re-run `ptt install <routine>` if `claude` ever moves to a
different directory.

The timer is installed with `Persistent=true`, so a run missed while the machine was
asleep or off fires as soon as it comes back. Because that can happen the instant the
machine resumes — before the network resolver is back up — the service first runs
`ptt wait-online`, which waits (up to two minutes) for DNS to work before the run starts.
Without it, a run triggered on resume would fail every clone and the summary email with
`Could not resolve host`. The gate is best-effort: if DNS never comes up it lets the run
proceed anyway rather than blocking it, so it can never turn a healthy run into a failure.
You can exercise it by hand with `ptt wait-online` (add `--host` / `--timeout` to tune it).

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
radius is limited to the throwaway clone (your local checkouts are never run in or
modified), but `git`/`gh` act with *your* credentials —
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
| All projects `failed (Could not resolve host)` on a resume-triggered run | Network wasn't up yet when the timer fired; the `wait-online` gate covers this — if it predates this fix, re-run `ptt install <routine>`. |
| Action shown as `(unverified)`       | Claude claimed a PR/issue `gh` couldn't confirm — check `git.log`.    |
| Project `failed (claude exited 1)` with a `529`/overload in `claude.stdout.jsonl` | Anthropic's API was overloaded. ptt already retries these with backoff (see `claude.retries.log`); if it persisted through every retry, just re-run the routine later. |
| Project `failed (timeout)`           | Raise `timeout_minutes` for the routine.                             |
| `No such file or directory: 'claude'` under the timer | The unit's baked `PATH` is stale or predates this fix — re-run `ptt install <routine>` from a shell where `claude` is on `PATH`. (A `PATH=` line in `~/.config/ptt/env` no longer breaks this: the baked value lives under `PTT_PATH` and `ptt run` merges it in, so it wins regardless.) |
| Timer never fires while logged out   | Run `sudo loginctl enable-linger <you>`.                              |

For deeper debugging, read `claude.stderr.log` and `git.log` in the run's project dir.

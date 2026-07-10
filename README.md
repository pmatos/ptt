# ptt — prompt-then-that

[![CI](https://github.com/pmatos/ptt/actions/workflows/ci.yml/badge.svg)](https://github.com/pmatos/ptt/actions/workflows/ci.yml)

Run a Markdown prompt through [Claude Code](https://claude.com/claude-code) against
one or more git projects on a schedule, let Claude open PRs/issues via `gh`, and get
an email summary of what happened. Local, single-user, self-hosted — the DIY analogue
of claude.ai/code Routines.

## How it works

A **routine** is one prompt + a list of projects + a schedule. A project can be a
**local checkout** on disk or a **remote GitHub repo** (`gh:owner/repo` or a git URL); either
way ptt runs against a **fresh clone of its github.com remote** in a throwaway dir and
deletes it when the run finishes. When it runs, for each project ptt:

1. **clones the project's github.com remote** into a throwaway dir and checks out a fresh
   `ptt/<routine>/<run-id>` branch (a local checkout only supplies its `origin` URL — it is
   never run in, fetched into, or branched);
2. runs `claude -p` headless in that clone, with a footer instructing Claude to do
   the task synchronously (it is a one-shot run — no re-invocation, so nothing may
   be deferred to a background task) and open a PR/issue with `gh` when warranted.
   Two harness guards make this stick: `--json-schema` forces Claude's final message
   to be a fixed result object, and `--disallowedTools` removes the schedule-and-wait
   tools so it can't background work and stall;
3. detects the outcome from that structured result, **cross-checked** against a `gh`
   PR/issue diff;
4. deletes the clone; the pushed branch stays on the remote.

Then it emails one summary per run over SMTP (any provider) and writes full logs to disk.

> **New here?** Follow the step-by-step **[tutorial](docs/tutorial.md)** to go from zero
> to a scheduled routine. The sections below are the quick reference.

## Requirements

- [uv](https://docs.astral.sh/uv/) (it provisions Python ≥ 3.11 automatically)
- `claude`, `git`, and `gh` on `PATH`; `gh` authenticated (`gh auth login`)
- Each project resolves to a github.com repo — a local checkout whose `origin` is on
  github.com, or a remote given as `gh:owner/repo` / a git URL. ptt always clones the remote
  fresh and deletes it after (a local checkout is used read-only, never run in)
- An SMTP account for email — any provider (Postmark, SES, Gmail, self-hosted)

## Install

ptt is managed with [uv](https://docs.astral.sh/uv/). To work from a clone:

```bash
uv sync                 # creates .venv and installs ptt + dev deps
uv run ptt --help       # run any command via `uv run`
```

To put `ptt` on your `PATH` as a standalone command (recommended for scheduled use):

```bash
uv tool install .       # installs `ptt` into ~/.local/bin
```

The commands below are written as `ptt …`; if you didn't `uv tool install`, prefix
them with `uv run` (e.g. `uv run ptt doctor`).

## Configure

All config lives under `~/.config/ptt/` (respects `XDG_CONFIG_HOME`).

`~/.config/ptt/config.toml`:

```toml
[email]
from = "ptt@yourdomain.com"
to   = "you@yourdomain.com"
on   = "always"                 # always | changes | failures
smtp_host     = "smtp.postmarkapp.com"     # any SMTP provider
smtp_username = "your-postmark-server-token"
# smtp_security     = "starttls"           # starttls (default) | ssl | none
# smtp_port         = 587                   # defaults by security: 587 / 465 / 25
# smtp_password_env = "PTT_SMTP_PASSWORD"   # name of the env var (default shown)

[defaults]
permission_mode = "bypass"      # bypass | acceptEdits  (see Security)
timeout_minutes = 30
base_branch = "main"
# work_dir = "~/.cache/ptt/work"
# Retry claude on a transient API error (429/5xx, e.g. 529 Overloaded); 0 disables:
# api_max_retries = 3            # extra re-invocations, with exponential backoff
# api_retry_base_seconds = 15    # first backoff (doubles each retry)
# api_retry_cap_seconds = 120    # cap on any single backoff
```

`~/.config/ptt/routines/code-audit.toml` (filename stem must equal `name`):

```toml
name = "code-audit"
description = "Weekday refactoring audit"
enabled = true
prompt = "~/prompts/refactor-audit.md"
schedule = "Mon..Fri 05:00"     # systemd OnCalendar syntax
projects = ["~/dev/rightkey", "gh:pmatos/ptt"]   # local path or gh:owner/repo — both cloned fresh + deleted
# base_branch / permission_mode / model / effort / timeout_minutes override [defaults]
# api_max_retries / api_retry_base_seconds / api_retry_cap_seconds also override [defaults]
# model  = "claude-opus-4-8"
# effort = "high"               # low | medium | high | xhigh | max  (reasoning effort)
```

Each `projects` entry is either a **local path** (a checkout whose `origin` is on
github.com) or a **remote GitHub repo** — `gh:owner/repo`, `https://github.com/owner/repo`, or
a `git@github.com:owner/repo.git` URL. A bare `owner/repo` (no `gh:`, no scheme) is read as a
relative local path, so an on-disk checkout is never mistaken for a foreign GitHub slug. In
both cases ptt clones the github.com remote into
`work_dir`, runs against that clone, and deletes it when the run ends — a local checkout is
only read (for its `origin`), never run in or modified. Private remotes need credentials
`git`/`gh` can already use.

Secrets — `~/.config/ptt/env` (chmod 600), loaded by the systemd timer:

```bash
PTT_SMTP_PASSWORD=your-smtp-password-or-token   # Postmark: your Server API token
```

```bash
chmod 600 ~/.config/ptt/env
```

Check everything is wired up:

```bash
ptt doctor
```

## Use

```bash
ptt run code-audit                 # run now (also what the timer calls)
ptt run code-audit --project ~/dev/rightkey   # just one project
ptt run code-audit --force         # run even if disabled

ptt list                           # routines + enabled state + timers
ptt logs code-audit                # latest run summary
ptt logs code-audit --run 20260630T050000Z --project rightkey   # drill in
ptt test-email                     # verify your SMTP settings work
ptt wait-online                    # block until DNS resolves (the timer's pre-run gate)

ptt install code-audit             # create + enable the systemd user timer
ptt uninstall code-audit
```

### Scheduling (systemd user timers)

`ptt install <routine>` writes `~/.config/systemd/user/ptt-<routine>.{service,timer}`
and enables the timer. The unit's `ExecStart` uses whatever `ptt` resolves to at install
time — `~/.local/bin/ptt` if you ran `uv tool install`, or the project's
`.venv/bin/ptt` if you ran `uv run ptt install` — both stable, absolute paths.
The service also bakes in your **current `PATH`** (via `Environment="PATH=…"`) so that
`claude`, `git`, and `gh` resolve the same way they do in your shell — the systemd user
manager's own `PATH` is sparse and usually omits e.g. `~/.local/bin`, which is where
`claude` typically lives. Run `ptt install <routine>` again if `claude` ever moves.

Timers use `Persistent=true`, so a run missed while the machine was asleep fires on
resume — potentially before the network is up. The service therefore gates `ExecStart`
behind `ExecStartPre=-… wait-online`, which waits (up to two minutes) for DNS before the
run so a resume-triggered run doesn't fail every clone with `Could not resolve host`. The
`-` prefix makes it best-effort: if DNS never comes up the run proceeds anyway.

For timers to fire **while you're logged out**, enable lingering once (the only step
that needs sudo):

```bash
sudo loginctl enable-linger "$USER"
```

## Logs

Everything is kept under `~/.local/state/ptt/runs/<routine>/<run-id>/`:

- `run.json` — run metadata + per-project outcomes
- `prompt.md` — the exact prompt used
- `projects/<name>/` — `claude.stdout.jsonl`, `claude.stderr.log`, `git.log`, `result.json`
  (plus `claude.retries.log` if a transient API error, e.g. `529`, forced an outer retry)

## Security

Unattended runs need Claude to edit files and run `git`/`gh` non-interactively, so the
default `permission_mode = "bypass"` maps to `claude --dangerously-skip-permissions`.
The blast radius is bounded to a throwaway clone under `~/.cache/ptt/work` (your local
checkouts are never run in or modified), but `git`/`gh` run with **your** credentials —
that's intentional (it's how PRs get opened).
The SMTP password is read only from the environment and never logged or emailed.

## Development

```bash
uv sync                 # install deps into .venv
uv run pytest           # full suite (example + Hypothesis); fails under 90% coverage
uv run ty check         # type-check
uv run ruff check       # lint (E/F/I/UP/B/SIM/S/RUF — incl. flake8-bandit security)
uv run ruff format      # format
```

`pytest` enforces a coverage gate (`--cov-fail-under=90`, `pytest-cov`) and includes
[Hypothesis](https://hypothesis.readthedocs.io/) property tests for the pure
parse/normalize/reconcile seams. A committed `.claude/settings.json` runs `ruff` + `ty`
after each edit and `pytest` on stop, mirroring CI so an agent gets the same feedback
in-loop (your personal `.claude/settings.local.json` stays gitignored).

CI (`.github/workflows/ci.yml`) runs the test suite on Python 3.11 and 3.13, the
`ty` / `ruff` checks, and workflow-security linting ([zizmor](https://docs.zizmor.sh/) +
[actionlint](https://github.com/rhysd/actionlint)) on every push to `main` and every pull
request; superseded runs are cancelled. Actions are pinned to their latest major/release
tag and kept current by Dependabot (`.github/dependabot.yml`).

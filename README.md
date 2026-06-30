# ptt — prompt-then-that

Run a Markdown prompt through [Claude Code](https://claude.com/claude-code) against
one or more git projects on a schedule, let Claude open PRs/issues via `gh`, and get
an email summary of what happened. Local, single-user, self-hosted — the DIY analogue
of claude.ai/code Routines.

## How it works

A **routine** is one prompt + a list of projects + a schedule. When it runs, for each
project ptt:

1. fetches `origin/<base_branch>` and creates an **isolated git worktree** on a fresh
   `ptt/<routine>/<run-id>` branch (your real checkout is never touched);
2. runs `claude -p` headless in that worktree, with a footer instructing Claude to do
   the task, open a PR/issue with `gh` when warranted, and write a `.ptt-result.json`;
3. detects the outcome from that file, **cross-checked** against a `gh` PR/issue diff;
4. removes the worktree (the pushed branch stays on the remote).

Then it emails one summary per run via Postmark and writes full logs to disk.

## Requirements

- Python ≥ 3.11
- `claude`, `git`, and `gh` on `PATH`; `gh` authenticated (`gh auth login`)
- Each project must be a git repo whose `origin` is on github.com
- A Postmark server token (for email)

## Install

```bash
pip install -e .        # or: pipx install .
# no install? everything also runs as: python -m ptt ...
```

## Configure

All config lives under `~/.config/ptt/` (respects `XDG_CONFIG_HOME`).

`~/.config/ptt/config.toml`:

```toml
[email]
from = "ptt@yourdomain.com"
to   = "you@yourdomain.com"
on   = "always"                 # always | changes | failures
# postmark_token_env = "PTT_POSTMARK_TOKEN"   # name of the env var (default shown)

[defaults]
permission_mode = "bypass"      # bypass | acceptEdits  (see Security)
timeout_minutes = 30
base_branch = "main"
# work_dir = "~/.cache/ptt/work"
```

`~/.config/ptt/routines/code-audit.toml` (filename stem must equal `name`):

```toml
name = "code-audit"
description = "Weekday refactoring audit"
enabled = true
prompt = "~/prompts/refactor-audit.md"
schedule = "Mon..Fri 05:00"     # systemd OnCalendar syntax
projects = ["~/dev/rightkey", "~/dev/foo"]
# base_branch / permission_mode / model / timeout_minutes override [defaults]
```

Secrets — `~/.config/ptt/env` (chmod 600), loaded by the systemd timer:

```bash
PTT_POSTMARK_TOKEN=your-postmark-server-token
```

```bash
chmod 600 ~/.config/ptt/env
```

Check everything is wired up:

```bash
ptt validate
```

## Use

```bash
ptt run code-audit                 # run now (also what the timer calls)
ptt run code-audit --project ~/dev/rightkey   # just one project
ptt run code-audit --force         # run even if disabled

ptt list                           # routines + enabled state + timers
ptt logs code-audit                # latest run summary
ptt logs code-audit --run 20260630T050000Z --project rightkey   # drill in
ptt test-email                     # verify Postmark works

ptt install code-audit             # create + enable the systemd user timer
ptt uninstall code-audit
```

### Scheduling (systemd user timers)

`ptt install <routine>` writes `~/.config/systemd/user/ptt-<routine>.{service,timer}`
and enables the timer. For timers to fire **while you're logged out**, enable lingering
once (the only step that needs sudo):

```bash
sudo loginctl enable-linger "$USER"
```

## Logs

Everything is kept under `~/.local/state/ptt/runs/<routine>/<run-id>/`:

- `run.json` — run metadata + per-project outcomes
- `prompt.md` — the exact prompt used
- `projects/<name>/` — `claude.stdout.jsonl`, `claude.stderr.log`, `git.log`, `result.json`

## Security

Unattended runs need Claude to edit files and run `git`/`gh` non-interactively, so the
default `permission_mode = "bypass"` maps to `claude --dangerously-skip-permissions`.
The blast radius is bounded to a throwaway worktree under `~/.cache/ptt/work`, but
`git`/`gh` run with **your** credentials — that's intentional (it's how PRs get opened).
The Postmark token is read only from the environment and never logged or emailed.

## Development

```bash
python -m pytest        # full suite; no network, no real Claude/GitHub (all faked)
```

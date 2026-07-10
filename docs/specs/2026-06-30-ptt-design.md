# ptt (prompt-then-that) — Design

**Date:** 2026-06-30
**Status:** Approved (design); spec pending user review
**Author:** Paulo Matos (with Claude Code)

## 1. Purpose

`ptt` ("prompt-then-that") runs a Markdown prompt through Claude Code against one or
more git projects on a schedule, lets Claude open PRs / issues via `gh`, and emails a
summary of what happened via Postmark. It is a local, single-user tool — the
self-hosted analogue of claude.ai/code Routines.

A typical use: *"Run `refactor-audit.md` through Claude on projects X, Y, Z every
weekday at 05:00; email me a summary (PR created / issue opened / issue closed /
nothing / failed); keep full logs for debugging."*

## 2. Scope

### In scope
- Define **routines** (prompt + projects + schedule) in config files.
- Run a routine on demand or via a scheduler.
- Execute Claude in a **fresh per-run clone** of each project's remote, on a fresh branch.
- Let Claude open PRs/issues with `gh`; detect the outcome.
- Email a per-run summary via **Postmark**.
- Persist full logs per run for debugging.
- Install/remove **systemd user timers** from routine definitions.

### Out of scope (YAGNI for v1)
- Multi-user / multi-tenant operation, auth, web UI.
- Parallel execution of projects within a run (sequential in v1).
- Notification channels other than Postmark email (e.g. Slack).
- Non-git or non-GitHub projects (a GitHub `origin` remote is assumed).
- Distributed/remote execution (everything runs on the local machine).

## 3. Concepts & terminology

- **Routine** — one prompt + a list of projects + a schedule. Maps 1:1 to a
  claude.ai "Routine". Defined in one TOML file.
- **Run** — one execution of a routine. Fans out over the routine's projects
  (sequentially) and produces exactly one summary email.
- **Run id** — a sortable UTC timestamp identifying a run, e.g. `20260630T050000Z`.
- **Project** — a GitHub repository, given as a `gh:owner/repo` slug, a git URL, or a local
  checkout whose `origin` is on github.com (github origin required so PRs/issues can be
  created). It is resolved to a clone URL and **cloned fresh** per run; a local checkout,
  if any, is used read-only (to read its `origin`) and never run in.
- **Outcome** — the structured result of running Claude on one project: one of
  `pr`, `issue_opened`, `issue_closed`, `commit`, or `none`; plus a status of
  `success`, `no_action`, or `error`.

## 4. Technology choices

| Concern        | Choice                              | Rationale |
|----------------|-------------------------------------|-----------|
| Language       | Python 3 (stdlib only)              | Light glue work; no build step; easy to read/debug/extend. |
| Config format  | TOML (`tomllib`, Python 3.11+)      | Human-editable; native parser in stdlib. |
| Scheduling     | systemd **user** timers             | Native on Arch; robust `OnCalendar`; logs to journald. |
| Claude         | `claude -p` (headless)              | Non-interactive run against the per-run clone. |
| Git isolation  | per-run `git clone` of the remote   | Full isolation from the local checkout; nothing local is fetched/branched; no local repo required (see ADR-0002). |
| PR/issue I/O   | `gh` CLI                            | Already authenticated; simplest path to PRs/issues. |
| Email          | SMTP via stdlib `smtplib` ¹         | No third-party deps; works with any email service. |

Required external tools on `PATH`: `claude`, `git`, `gh` (authenticated). Python ≥ 3.11
(for `tomllib`). `ptt doctor` checks all of these.

## 5. Repository layout (the tool itself)

Lives in `~/dev/ptt`:

```
ptt/
  __init__.py
  cli.py            # argument parsing, subcommand dispatch
  config.py         # load + validate global config and routines (TOML)
  runner.py         # run a routine: per-project fan-out, aggregation
  claude.py         # build & invoke `claude -p`; capture output
  git_ops.py        # classify origin, per-run clone/remove, branch naming
  outcomes.py       # read structured_output from stream-json + reconcile with gh
  notify.py         # build & send Postmark email
  logstore.py       # run/log directory layout + writers
  schedule.py       # generate/enable/remove systemd units
tests/
  ...               # see §13
docs/specs/2026-06-30-ptt-design.md
pyproject.toml      # console_scripts entry point: `ptt`
README.md
.gitignore
```

Each module has a single clear responsibility and a small interface, so it can be
tested in isolation. `claude.py`, `git_ops.py`, and `notify.py` are the only modules
that touch external processes/network; they are thin wrappers so the rest of the code
is pure and mockable.

## 6. User data layout (XDG; never in the repo)

```
~/.config/ptt/
  config.toml                 # global: email + defaults
  env                         # secrets, chmod 600 (PTT_POSTMARK_TOKEN=...)
  routines/
    <name>.toml               # one file per routine

~/.cache/ptt/work/            # transient per-run clones (created/removed per run)

~/.local/state/ptt/runs/<routine>/<run-id>/
  run.json                    # run metadata + aggregated per-project results
  prompt.md                   # snapshot of the exact prompt used
  projects/<project-name>/
    claude.stdout.jsonl       # raw `claude --output-format stream-json`
    claude.stderr.log
    result.json               # parsed + reconciled outcome for this project
    git.log                   # git/gh commands run for this project + their output
```

`<project-name>` is the repo's directory basename; collisions are disambiguated by
appending a short hash of the full path.

## 7. Configuration

### 7.1 Routine — `~/.config/ptt/routines/<name>.toml`

```toml
name = "code-audit"             # must equal the filename stem
description = "Weekday refactoring audit"
enabled = true

prompt = "~/prompts/refactor-audit.md"   # path to the prompt markdown
schedule = "Mon..Fri 05:00"              # systemd OnCalendar syntax

projects = [
  "~/dev/foo",                 # a local checkout (read-only, for its origin URL)
  "gh:pmatos/rightkey",        # an owner/repo slug (gh: marks it remote)
  "git@github.com:pmatos/bar.git",   # or a git URL
]

# optional, override [defaults]
base_branch = "main"
permission_mode = "bypass"      # "bypass" | "acceptEdits" (see §10)
model = "claude-opus-4-8"       # optional; omit to use Claude Code default
timeout_minutes = 30
```

Validation rules:
- `name` matches the filename stem and `^[a-z0-9][a-z0-9-]*$` (used in unit names,
  branch names, paths).
- `prompt` resolves (after `~` expansion) to a readable file.
- `projects` is non-empty. Each entry is a local path, a `gh:owner/repo` slug, or a git URL;
  it must resolve to a github.com remote (a local path is read for its `origin`). The `gh:`
  marker is required for the slug form so a bare relative path (`dev/repo`) is classified as
  local, not silently cloned from `github.com/dev/repo`. Resolution happens at run time, not
  load time, so editing config never needs the repos present.
- `schedule` is a non-empty string; it is validated by `systemd-analyze calendar`
  during `ptt install` (not parsed by ptt itself).

### 7.2 Global — `~/.config/ptt/config.toml`

```toml
[email]
postmark_token_env = "PTT_POSTMARK_TOKEN"  # name of the env var holding the token
from = "ptt@yourdomain.com"
to   = "you@yourdomain.com"
on   = "always"                            # "always" | "changes" | "failures"

[defaults]
permission_mode = "bypass"
timeout_minutes = 30
work_dir = "~/.cache/ptt/work"
base_branch = "main"
```

`email.on` controls when an email is sent:
- `always` — every completed run.
- `changes` — only when ≥1 project produced an action other than `none`.
- `failures` — only when ≥1 project failed.

### 7.3 Secrets

The Postmark **server token** is read from the environment variable named by
`postmark_token_env`. It is delivered to scheduled runs via
`EnvironmentFile=~/.config/ptt/env` (a `KEY=value` file, `chmod 600`). The token is
never written to config, logs, or emails. `ptt doctor` fails if the variable is
unset.

## 8. Run flow

`ptt run <routine>` (invoked by the timer, or manually):

1. **Load & preflight.** Load global config + routine. Resolve the prompt file. If the
   routine is `enabled = false`, exit 0 with a logged note (the timer may still fire;
   manual `run` ignores `enabled` only when `--force` is passed — otherwise honors it).
2. **Create run dir.** `~/.local/state/ptt/runs/<routine>/<run-id>/`, snapshot the
   prompt to `prompt.md`.
3. **For each project, sequentially:**
   1. **Resolve the entry** to a github.com clone URL (§3/§7.1): a `gh:owner/repo` slug or a
      git URL is used directly; a local path contributes only its `origin` URL (read-only).
   2. `git clone --single-branch --branch <base_branch> <url> <work_dir>/<run-id>/<name>`,
      then `git -C <clone> checkout -b ptt/<routine>/<run-id>`. The clone is standalone
      (`origin` = the github URL), so `gh` and pushes target the real repo. The user's
      local checkout is **never** fetched into, branched, or run in.
   3. Snapshot pre-state: `gh pr list --json number,url,headRefName` and
      `gh issue list --json number,url,state` (run in the clone).
   4. Invoke Claude (see §9) with cwd = clone, prompt on stdin, output streamed to
      `claude.stdout.jsonl` / `claude.stderr.log`, under a wall-clock timeout.
   5. Read the schema-enforced result (`structured_output` from the final stream-json
      `result` event); reconcile with a fresh `gh` snapshot (see §11). Write
      `projects/<name>/result.json`.
   6. Delete the clone dir (`shutil.rmtree`). The branch, if pushed by Claude, remains on
      the remote. On step failure, the clone is still removed but all logs are retained.
   7. Append the outcome to the run's in-memory result list. Any exception here is
      caught, recorded as an `error` outcome for this project, and does **not** abort
      the remaining projects.
4. **Aggregate.** Write `run.json` (start/end UTC, overall status, per-project
   outcomes + log paths).
5. **Notify.** If `email.on` policy matches, send the summary email (§12). An email
   failure is logged + marked but does not fail the run record.
6. **Exit code.** `0` if all projects succeeded (`success` or `no_action`); non-zero if
   any project `error`ed — so systemd records the failure in journald.

## 9. Invoking Claude

`claude.py` builds, roughly:

```
claude -p \
  --output-format stream-json --verbose \
  --permission-mode <mode | dangerously-skip-permissions> \
  [--model <model>] \
  --json-schema '<result schema>' \
  --disallowedTools ScheduleWakeup Monitor CronCreate \
  < <effective-prompt>
```

- `cwd` is the clone, so the whole repo is in scope without an extra `--add-dir`.
- **Effective prompt** = the routine's prompt markdown, followed by a fixed ptt
  instruction footer that tells Claude to:
  1. Perform the task described above.
  2. When work warrants it, create a branch, commit, push, and open a PR (or open/close
     an issue) using `gh`.
  3. **Report the outcome as a structured result** — a single JSON object with this
     schema:
     ```json
     {
       "status": "success | no_action | error",
       "action": "pr | issue_opened | issue_closed | commit | none",
       "url": "https://github.com/... or null",
       "title": "short human title",
       "summary": "1-3 sentence description of what was done or why nothing was"
     }
     ```
- **Structured-output guard:** this schema is passed to `claude --json-schema`, so the
  final message is forced to conform and is surfaced in the stream-json `result` event's
  `structured_output` field. ptt reads it from there — it no longer depends on Claude
  writing a file, which was an unreliable step (a model that ended its turn early left no
  file and the whole run was lost).
- **No-background guard:** `--disallowedTools ScheduleWakeup Monitor CronCreate` removes the
  schedule-and-wait tools, so Claude cannot background long-running work and end its turn
  expecting a re-invocation that never comes in one-shot mode. (Subagents via `Agent` stay
  enabled — they run to completion within the turn.)
- **Permission mode:** for unattended runs the effective flag is
  `--dangerously-skip-permissions` (chosen when `permission_mode = "bypass"`), because
  headless `-p` mode has no one to approve tool prompts. `acceptEdits` is offered for
  routines that should auto-accept edits but is **not** sufficient for autonomous
  push/PR; this is documented in the README. See §10.
- **Timeout:** the process is started in its own process group; on timeout the whole
  group is killed (`SIGTERM` then `SIGKILL`) and the project is marked
  `status=error, action=none` with reason `timeout`.
- **Transient API-error retry:** `claude` retries API failures internally, but a
  sustained overload window exhausts them and it exits non-zero with an
  `api_error_status` (429/5xx, e.g. `529 Overloaded`) in its stream-json output.
  `claude.py` parses that terminal `result` record and, for a retryable status, re-invokes
  `claude` up to `api_max_retries` more times with exponential backoff (`api_retry_base_seconds`,
  doubling each retry, capped at `api_retry_cap_seconds`). These three knobs live in
  `[defaults]` and are per-routine overridable (defaults 3 / 15s / 120s; `api_max_retries = 0`
  disables the outer retry). Each retry is recorded in a sibling `claude.retries.log`; the
  canonical `claude.stdout.jsonl`/`.stderr.log` keep the final attempt. Timeouts and
  non-API failures are never retried.

## 10. Security considerations

- Autonomous PR/issue creation requires Claude to edit files and run `git`/`gh`
  non-interactively. The v1 default `permission_mode = "bypass"` maps to
  `--dangerously-skip-permissions`.
- **Blast radius is bounded** to a throwaway clone under `~/.cache/ptt/work`. The local
  checkout is never modified (not even fetched into or branched). However, `git`/`gh` run
  with the user's existing credentials — that is intentional and necessary for opening PRs.
- The Postmark token lives only in `~/.config/ptt/env` (mode 600) and the process
  environment; it is never logged or emailed.
- `ptt install` warns that the timer runs commands unattended with the above
  permissions, and prints the one-time `loginctl enable-linger` step (§14).
- Logs may contain repository content and Claude output; they live under the user's
  `~/.local/state` and are not transmitted anywhere except the summary email, which
  contains titles/URLs/summaries — not full diffs.

## 11. Outcome detection & reconciliation

Primary signal: the schema-enforced `structured_output` in Claude's stream-json `result`
event (§9). Backstop: a `gh` diff.

`outcomes.py` reconciles claimed vs observed:
- Compute the delta between pre- and post-run `gh pr list` / `gh issue list`
  snapshots (new PRs, new issues, issues that changed to `closed`).
- If Claude claims `action=pr` (or issue) and the gh delta confirms it → outcome
  stands, `verified=true`.
- If Claude claims an action but the gh delta does **not** confirm it → keep the
  claim but set `verified=false` and note "unverified" in the email.
- If Claude reports `none`/no result but the gh delta shows a new PR/issue → report the
  gh-observed action with `verified=true` and `source=gh`.
- If the structured result is missing/unparseable and there is no gh delta → outcome is
  `status=error, action=none, reason="claude produced no structured result"`.

The reconciled per-project record (status, action, url, title, summary, verified,
source, log paths, durations) is written to `projects/<name>/result.json` and included
in `run.json`.

## 12. Email summary (SMTP)

> ¹ **Superseded by [ADR 0001](../adr/0001-send-email-over-smtp.md).** The original
> design sent through Postmark's HTTP API; ptt now sends over SMTP so it isn't tied to
> any one email service. The `[email]` config, env-var names, and `notify.py` below
> reflect the current SMTP design; the historical Postmark-HTTP details are retained
> only where they explain the migration.

`notify.py` builds a plain `email.message.EmailMessage` (subject, text body, optional
HTML alternative) and hands it to any SMTP server via stdlib `smtplib` — `smtp_host`,
`smtp_port`, `smtp_security` (`starttls`/`ssl`/`none`), `smtp_username`, and a password
read from the env var named by `smtp_password_env`. Postmark is reached like any other
provider, over its SMTP endpoint.

- **Subject:** `[ptt] <routine> — <n> PR, <m> issue, <k> failed` (counts summarized).
- **Body (text):** one line per project:
  - `✅ rightkey — PR: "Extract config loader" https://github.com/...`
  - `⏭️  foo — nothing to do`
  - `❌ bar — failed (timeout) — log: ~/.local/state/ptt/runs/code-audit/<id>/projects/bar/`
  - Unverified actions are tagged `(unverified)`.
- **Footer:** run id, total duration, path to the run dir.

On HTTP/non-2xx failure: log the status + response body, write an `.email-failed`
marker in the run dir, attempt **one** retry, then give up without failing the run.

## 13. Error handling summary

| Failure                     | Handling |
|-----------------------------|----------|
| Missing config/token/`gh`/`claude` | `ptt doctor` and run preflight fail fast with a clear message, before any work. |
| Project origin missing / not github.com | That project → `error`; others continue. |
| `git clone`/`gh` command fails     | Captured to `git.log`; project → `error`; the clone is still removed. |
| Claude transient API error (429/5xx, e.g. 529) | Re-invoke with exponential backoff (up to `api_max_retries`, configurable); retries logged to `claude.retries.log`. Project → `error` only if every attempt fails. |
| Claude non-zero exit (non-API)     | Project → `error` with exit code + last stderr lines. |
| Claude timeout                     | Process group killed; project → `error (timeout)`. |
| Structured result missing/bad      | Fall back to gh delta; else `error (claude produced no structured result)`. |
| Postmark send fails                | Logged + marker + one retry; run record unaffected. |

Clone cleanup always runs in a `finally`. One project's failure never aborts the run.

## 14. systemd integration

`ptt install <routine>` writes two user units and enables the timer:

`~/.config/systemd/user/ptt-<routine>.service`
```ini
[Unit]
Description=ptt routine <routine>
[Service]
Type=oneshot
EnvironmentFile=%h/.config/ptt/env
ExecStart=%h/.local/bin/ptt run <routine>
```

`~/.config/systemd/user/ptt-<routine>.timer`
```ini
[Unit]
Description=ptt routine <routine> schedule
[Timer]
OnCalendar=<schedule>
Persistent=true
[Install]
WantedBy=timers.target
```

Then: `systemctl --user daemon-reload && systemctl --user enable --now ptt-<routine>.timer`.

`ptt install` validates the schedule via `systemd-analyze calendar "<schedule>"` first,
and prints the **one-time** requirement (run by the user, needs sudo):
`sudo loginctl enable-linger pmatos` — so timers fire while logged out. `ptt uninstall`
disables the timer and removes both unit files. `ptt list` reads
`systemctl --user list-timers` to show next run times.

## 15. CLI surface

```
ptt run <routine> [--project P] [--force]   # run now; --project: one project only;
                                            #   --force: ignore enabled=false
ptt list                                    # routines, enabled state, next run time
ptt logs <routine> [--run ID] [--project P] # show/tail logs for a run
ptt install <routine>                       # generate + enable systemd timer/service
ptt uninstall <routine>                     # disable + remove units
ptt doctor                                  # config, gh auth, claude, token present
ptt test-email                              # send a test Postmark email
```

## 16. Testing strategy

External seams (`claude`, `gh`, Postmark) are mocked so tests need no network, no real
Claude, and no real GitHub:

- **Unit tests:**
  - `config.py`: valid/invalid TOML, name/path/projects validation, `~` expansion.
  - `outcomes.py`: every reconciliation branch in §11 against fixture gh snapshots +
    `structured_output` fixtures (including missing/garbage stream-json).
  - `notify.py`: Postmark payload shape + headers (urllib mocked); subject/body
    rendering for mixed outcomes; retry-on-failure behavior.
  - `schedule.py`: generated unit-file text (string comparison) and the install command
    sequence (subprocess mocked).
- **Integration test:** put fake `claude` and `gh` executables on `PATH` (shell scripts
  that emit canned `stream-json` with a `structured_output` result event, and that fake
  `gh pr/issue list` deltas). Run the full `runner` against a temp git repo with a fake
  `origin`; assert `run.json`, per-project `result.json`, log files, and the captured
  Postmark payload. Cover: success-with-PR, no-action, failure, timeout, unverified.

## 17. Open questions / future work (not v1)

- Parallel project execution with a concurrency cap.
- Additional notifiers (Slack, desktop) behind a `notify` interface.
- Retry/backoff for transient `gh` failures (the `claude` API-error case is handled — §9).
- A `ptt status` dashboard or simple local web view over `run.json` history.
- Log retention/pruning policy (v1 keeps everything).

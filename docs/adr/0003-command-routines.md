---
status: accepted
---

# Command routines: run a local command on a schedule and email its stdout

ptt started as one shape of routine: a **project routine** — a prompt run through Claude
Code against a github.com repo, which may open a PR/issue, and whose email is a summary of
those git side effects. But a whole class of useful scheduled jobs is *gather → summarize →
email* with **no git project and no PR**: a daily mail digest, an RSS roundup, a calendar
summary. The motivating case is a mail-digest script that reads work email over IMAP and
summarizes it with a **local** model (Ollama) — deliberately local because the content is
private and must not leave the machine.

Forcing that through the project-routine path fits badly: there is no repo to clone, no
`gh` reconciliation, and the deliverable is the *content itself*, not a summary of git
actions. Routing private email through `claude -p` would also send it to the Anthropic API,
defeating the local-model choice.

ptt now supports a second routine kind, the **command routine**. A routine's TOML carries
**either** `projects` (project routine) **or** `command` (command routine) — mutually
exclusive, discriminated by which key is present. A command routine runs its `command`
(an argv array, no shell) in a throwaway working dir, captures stdout/stderr, and emails
the **stdout** as the digest. ptt stays a pure scheduler here: no clone, no Claude, no `gh`.

## Considered options

- **Make the digest a Claude-native routine** (Claude gathers data via tools/MCP and writes
  the summary). Rejected for the motivating case: it sends private email content to the
  Anthropic API and couples a local-model workflow to Claude. It remains available as an
  ordinary project-less idea for the future, but is not what "port this workflow" needed.
- **A narrow, mail-specific feature** (bake IMAP + summarization into ptt). Rejected: ptt is
  stdlib-only and service-agnostic; embedding IMAP and a model client is a large, single-use
  expansion. A generic command routine covers the mail digest *and* any other
  gather→summarize→email job, with the summarizer's choice left entirely to the command.
- **A separate per-routine email switch via the global `email.on`.** Rejected as the way to
  silence a self-notifying command: `email.on` is a single global setting shared with every
  project routine, so flipping it would suppress their mail too. A command routine instead
  has its own `notify` boolean.

## Consequences

- **New config surface.** A command routine has `command` (argv array, `~`-expanded like
  `prompt`/`projects`), `schedule`, optional `body_format` (`text` | `markdown`, default
  `text`), and optional `notify` (default `true`). `command` and `projects` are mutually
  exclusive.
- **stdout is the deliverable.** The email body is the command's stdout verbatim; with
  `body_format = "markdown"` it is rendered to HTML by a deliberately minimal renderer (not
  a general Markdown engine). The global `email.on` maps as: `always` → every run,
  `failures` → non-zero exit, `changes` → non-empty stdout.
- **`notify = false`** lets a command that delivers its own output (e.g. a script run with
  its own `--send`) run under ptt without ptt also emailing.
- **Isolation.** The command runs in its own process group under the routine's
  `timeout_minutes`; a timeout kills the whole group so an unattended run can't leak forked
  children. It inherits the process environment (so the systemd `EnvironmentFile` secrets
  and the baked `PTT_PATH` reach it); the command manages its own secrets.
- **Tooling relaxes for command-only installs.** `ptt doctor` only requires `claude`/`git`/
  `gh` when a project routine is configured; a command-only setup passes without them, and
  each command routine's executable is checked instead. `ptt logs` shows
  `command.stdout.log` / `command.stderr.log` / `command.txt` (a command run has no
  `projects/` subtree). `install`/`uninstall`/`list` are unchanged.
- **Run-dir layout differs.** A command run writes `command.stdout.log`,
  `command.stderr.log`, `command.txt`, and `run.json` directly under the run dir — the run
  dir *is* the log dir, with no per-project fan-out.

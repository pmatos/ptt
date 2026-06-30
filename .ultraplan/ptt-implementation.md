# Plan: Implement ptt (prompt-then-that)

## Goal
Build the `ptt` CLI exactly as specified in `docs/specs/2026-06-30-ptt-design.md`:
a local, single-user Python 3.11+ (stdlib-only) tool that runs a Markdown prompt
through `claude -p` against git projects on a schedule, opens PRs/issues via `gh`,
detects outcomes, emails a per-run summary via Postmark, and installs systemd user
timers. Everything is greenfield — every source file below is `[new]`.

## Spec deviations (intentional, minor)
Two small additions beyond the spec's listed module set, both for testability /
avoiding circular imports — not scope changes:
- **`ptt/models.py`** — shared dataclasses + `StrEnum`s (config + result types) consumed
  by nearly every module; keeps `config`/`runner`/`notify`/`outcomes` from importing
  each other for types.
- **`ptt/proc.py`** — one tiny synchronous subprocess+capture helper used by
  `git_ops`, `outcomes`, `schedule` (the simple commands). `claude.py` keeps its own
  bespoke streaming/process-group/timeout runner because its needs are different.

## Key Files
| File | Role | Status |
|------|------|--------|
| `pyproject.toml` | packaging, `ptt = ptt.cli:main` entry point, py>=3.11, pytest dev dep | [new] |
| `README.md` | install/config/usage, env file, linger note | [new] |
| `ptt/__init__.py` | package marker + `__version__` | [new] |
| `ptt/models.py` | dataclasses + enums (config + results) | [new] |
| `ptt/config.py` | XDG paths, load/validate global + routine TOML | [new] |
| `ptt/proc.py` | shared `run()` subprocess+capture+log helper | [new] |
| `ptt/logstore.py` | run-id, run/project dirs, json + log writers | [new] |
| `ptt/git_ops.py` | repo validation, fetch, worktree add/remove, branch name | [new] |
| `ptt/claude.py` | effective prompt + argv, streaming run with timeout kill | [new] |
| `ptt/outcomes.py` | gh snapshot, parse `.ptt-result.json`, reconcile | [new] |
| `ptt/notify.py` | Postmark payload build + send + policy + retry | [new] |
| `ptt/runner.py` | per-routine run, per-project fan-out, aggregation | [new] |
| `ptt/schedule.py` | systemd unit render + install/uninstall/list | [new] |
| `ptt/cli.py` | argparse subcommands, `validate`, `main()` | [new] |
| `tests/conftest.py` | temp XDG dirs, temp git repo, fake bins on PATH | [new] |
| `tests/fake_bin/claude`, `tests/fake_bin/gh` | canned subprocess fakes | [new] |
| `tests/test_config.py` … `tests/test_runner_integration.py` | unit + integration | [new] |

Reference for every step: section numbers (§) point into
`docs/specs/2026-06-30-ptt-design.md`.

## Steps (dependency-ordered)

### 1. Scaffold the package and packaging
- **Files**: `pyproject.toml` [new], `ptt/__init__.py` [new]
- **Change**:
  - `pyproject.toml`: `[build-system]` hatchling; `[project]` name=`ptt`,
    `requires-python = ">=3.11"`, no runtime deps; `[project.scripts]`
    `ptt = "ptt.cli:main"`; `[project.optional-dependencies]` `dev = ["pytest"]`;
    `[tool.pytest.ini_options]` `testpaths = ["tests"]`.
  - `ptt/__init__.py`: `__version__ = "0.1.0"`.
- **Verify**: `python -c "import tomllib"` works on the target interpreter (≥3.11).

### 2. Define shared models (§3, §7, §11)
- **File**: `ptt/models.py` [new]
- **Change**: `from enum import StrEnum`; `from dataclasses import dataclass, field`.
  - `class Status(StrEnum)`: `SUCCESS="success"`, `NO_ACTION="no_action"`, `ERROR="error"`.
  - `class Action(StrEnum)`: `PR="pr"`, `ISSUE_OPENED="issue_opened"`,
    `ISSUE_CLOSED="issue_closed"`, `COMMIT="commit"`, `NONE="none"`.
  - `class EmailOn(StrEnum)`: `ALWAYS`, `CHANGES`, `FAILURES`.
  - `class PermissionMode(StrEnum)`: `BYPASS="bypass"`, `ACCEPT_EDITS="acceptEdits"`.
  - `class Source(StrEnum)`: `CLAUDE="claude"`, `GH="gh"`.
  - `@dataclass EmailConfig`: `from_addr`, `to_addr`, `on: EmailOn`, `postmark_token_env`.
  - `@dataclass Defaults`: `permission_mode: PermissionMode`, `timeout_minutes: int`,
    `work_dir: Path`, `base_branch: str`.
  - `@dataclass GlobalConfig`: `email: EmailConfig`, `defaults: Defaults`.
  - `@dataclass Routine`: `name`, `description`, `enabled`, `prompt: Path`, `schedule`,
    `projects: list[Path]`, `base_branch`, `permission_mode: PermissionMode`,
    `model: str | None`, `timeout_minutes: int`, `work_dir: Path` (all defaults merged in).
  - `@dataclass Outcome`: `status`, `action`, `url: str|None`, `title`, `summary` (Claude's claim).
  - `@dataclass ProjectResult`: `name`, `path: str`, `status`, `action`, `url`, `title`,
    `summary`, `verified: bool`, `source: Source`, `reason: str|None`, `branch: str|None`,
    `duration_s: float`, `log_dir: str`. Add `to_dict()` (enum→`.value`) for JSON.
  - `@dataclass RunResult`: `routine`, `run_id`, `started_at`, `ended_at`,
    `overall_status: Status`, `projects: list[ProjectResult]`, `run_dir: str`; `to_dict()`.

### 3. XDG paths + config loading/validation (§6, §7)
- **File**: `ptt/config.py` [new]
- **Change**:
  - `ConfigError(Exception)`.
  - XDG helpers: `config_home()` = `$XDG_CONFIG_HOME or ~/.config` `/ptt`;
    `state_home()` = `$XDG_STATE_HOME or ~/.local/state` `/ptt`;
    `cache_home()` = `$XDG_CACHE_HOME or ~/.cache` `/ptt`. All `Path.expanduser()`.
  - `routines_dir()` = `config_home()/"routines"`; `global_config_path()` =
    `config_home()/"config.toml"`; `env_file_path()` = `config_home()/"env"`.
  - `load_global_config() -> GlobalConfig`: parse TOML; require `[email] from,to`;
    `on` ∈ EmailOn (default `always`); `postmark_token_env` (default `PTT_POSTMARK_TOKEN`);
    `[defaults]` with documented fallbacks (permission_mode=`bypass`, timeout_minutes=30,
    work_dir=`cache_home()/"work"`, base_branch=`main`). Raise `ConfigError` on bad enum/missing.
  - `load_routine(name, global_cfg) -> Routine`: read `routines_dir()/f"{name}.toml"`;
    enforce `name == stem` and `^[a-z0-9][a-z0-9-]*$`; `prompt` resolves+readable;
    `projects` non-empty; merge missing fields from `global_cfg.defaults`;
    `model` optional→None. Raise `ConfigError` with precise message. **Repo existence is
    NOT checked here** (per §7.1 — only at run time).
  - `list_routine_names() -> list[str]`: stems of `routines_dir()/*.toml`, sorted.
- **Reuses**: `tomllib`, `ptt.models` (step 2).

### 4. Subprocess helper (§5 seam)
- **File**: `ptt/proc.py` [new]
- **Change**: `@dataclass Completed: returncode:int, stdout:str, stderr:str`.
  `run(cmd: list[str], *, cwd=None, timeout=None, env=None, input=None,
  log_path: Path|None=None) -> Completed`: `subprocess.run(..., capture_output=True,
  text=True)`; if `log_path`, append `$ <cmd>\n<stdout>\n<stderr>\n`. Catch
  `subprocess.TimeoutExpired` → return `Completed(returncode=124,...)` with note in stderr.
- **Reuses**: `subprocess`, `shlex.join`.

### 5. Log store: run-id, dirs, writers (§6)
- **File**: `ptt/logstore.py` [new]
- **Change**:
  - `new_run_id() -> str`: `datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")`.
  - `project_dir_name(path: Path, taken: set[str]) -> str`: `path.name`; if it collides
    with a name already in `taken`, append `-<hashlib.sha1(str(full_path))[:6]>`. Caller
    passes the accumulating `taken` set so two projects with the same basename get distinct dirs.
  - `run_dir(routine, run_id) -> Path` under `config.state_home()/"runs"/routine/run_id`;
    create with `mkdir(parents=True, exist_ok=False)`. **Run-id collision (M5):** if the
    dir already exists (two runs within the same UTC second), append `-2`, `-3`, … to the
    run-id until the path is free; return the final run-id to the caller so all later paths
    use it. Never `exist_ok=True` (would clobber a sibling run's logs).
  - `project_dir(run_dir, name) -> Path` (`/projects/<name>`), creates it.
  - `snapshot_prompt(run_dir, prompt_path)`: copy prompt text → `run_dir/"prompt.md"`.
  - `claude_stdout_path/claude_stderr_path/result_path/git_log_path(project_dir)`.
  - `append_git_log(project_dir, text)`.
  - `write_result_json(project_dir, project_result)`; `write_run_json(run_dir, run_result)`
    using `to_dict()` + `json.dump(indent=2)`.
- **Reuses**: `ptt.config` paths (step 3), `ptt.models` (step 2), `json`, `hashlib`, `datetime`.

### 6. Git operations (§8 steps 1–2, 6; §7.1 repo check)
- **File**: `ptt/git_ops.py` [new]
- **Change**:
  - `GitError(Exception)`.
  - `branch_name(routine, run_id) -> str` → `f"ptt/{routine}/{run_id}"`.
  - `is_github_repo(path, log_path) -> bool`: `git -C <path> remote get-url origin`
    succeeds and contains `github.com`.
  - `fetch(path, base_branch, log_path)`: `git -C <path> fetch origin <base_branch>`;
    raise `GitError` on non-zero.
  - `add_worktree(repo, dest, branch, base_ref, log_path)`:
    `git -C <repo> worktree add <dest> -b <branch> origin/<base_ref>`.
  - `remove_worktree(repo, dest, log_path)`:
    `git -C <repo> worktree remove --force <dest>` (best-effort; log on failure).
- **Reuses**: `ptt.proc.run` (step 4), `ptt.logstore.append_git_log` via `log_path`.

### 7. Claude invocation (§9, §10)
- **File**: `ptt/claude.py` [new]
- **Change**:
  - `RESULT_FOOTER: str` — fixed instruction appended to the prompt telling Claude to do
    the task, create branch/commit/push + open PR/issue via `gh` when warranted, and
    **write `.ptt-result.json`** in repo root matching the §9 schema (status/action/url/
    title/summary). Document the exact JSON shape in the footer.
  - `build_prompt(prompt_text) -> str` = `prompt_text + "\n\n" + RESULT_FOOTER`.
  - `build_argv(routine) -> list[str]`: base
    `["claude","-p","--output-format","stream-json","--verbose"]`; map
    `PermissionMode.BYPASS → ["--dangerously-skip-permissions"]`,
    `ACCEPT_EDITS → ["--permission-mode","acceptEdits"]`; append `["--model",model]` if set.
  - `run_claude(routine, worktree, prompt_text, stdout_path, stderr_path, timeout_s)
    -> tuple[int, bool]` returns `(exit_code, timed_out)`. Use `subprocess.Popen` with
    `start_new_session=True` (own process group), `stdin=PIPE`, stdout/stderr → open file
    handles (`stdout_path`/`stderr_path`); write prompt to stdin, close; `proc.wait(timeout)`;
    on `TimeoutExpired` → `os.killpg(SIGTERM)`, short grace, `os.killpg(SIGKILL)`,
    return `(124, True)`.
- **Reuses**: `ptt.models.PermissionMode` (step 2), `subprocess`, `os`, `signal`.

### 8. Outcome detection + reconciliation (§9 schema, §11)
- **File**: `ptt/outcomes.py` [new]
- **Change**:
  - `gh_snapshot(worktree, log_path) -> tuple[dict, bool]`:
    `gh pr list --json number,url,headRefName` and
    `gh issue list --json number,url,state` (json.loads). Return
    `({"prs": {num:{url,headRefName}}, "issues": {num:{url,state}}}, ok)` where **`ok`
    distinguishes a real empty result from a gh command failure (M2)** — on non-zero gh
    exit, return `({...empty...}, False)` and log it. Reconciliation must treat
    `ok=False` as an error condition, never as "no changes".
  - `read_result_file(worktree) -> Outcome | None`: read `<worktree>/.ptt-result.json`;
    parse + coerce enums; return None on missing/invalid.
  - `reconcile(claimed, pre, post, pre_ok, post_ok, claude_rc, timed_out)
    -> ProjectResult-fields dict` implementing the §11 truth table:
    * timed_out → `error`/`none`/reason=`timeout`.
    * `not pre_ok or not post_ok` (gh snapshot failed, M2) → `error`/`none`/
      reason=`gh snapshot failed`.
    * claude_rc != 0 and no gh delta → `error`/`none`/reason from stderr tail.
    * compute deltas: new PRs (`post.prs - pre.prs`), new issues, issues now `closed`.
    * claim confirmed by delta → keep claim, `verified=True`, `source=claude`.
    * claim unconfirmed → keep claim, `verified=False`.
    * no/none claim but delta shows PR/issue → report observed, `verified=True`,
      `source=gh`.
    * missing result file AND no delta → `error`/`none`/reason=`no result file`.
- **Reuses**: `ptt.proc.run` (step 4), `ptt.models` (step 2), `json`.

### 9. Email notification (§12)
- **File**: `ptt/notify.py` [new]
- **Change**:
  - `should_send(run_result, on: EmailOn) -> bool`: ALWAYS→True; CHANGES→any action!=NONE;
    FAILURES→any status==ERROR.
  - `build_subject(run_result) -> str`: `[ptt] <routine> — <n> PR, <m> issue, <k> failed`.
  - `build_text(run_result) -> str`: one ✅/⏭️/❌ line per project (title + url or reason +
    log dir), `(unverified)` tag when `verified is False`; footer = run_id + duration +
    run_dir. `build_html` optional (wrap `<pre>`).
  - `send(subject, text, html, email_cfg, token)`: POST JSON `{From,To,Subject,TextBody,
    HtmlBody}` to `https://api.postmarkapp.com/email` with headers `Accept`,
    `Content-Type: application/json`, `X-Postmark-Server-Token: <token>` via
    `urllib.request`; raise on non-2xx.
  - `notify(run_result, email_cfg, token, run_dir)`: if `should_send`, build + `send`;
    on failure log + write `run_dir/".email-failed"`, retry once, then give up (never raise).
- **Reuses**: `ptt.models` (step 2), `urllib.request`, `json`.

### 10. Runner orchestration (§8, §13)
- **File**: `ptt/runner.py` [new]
- **Change**:
  - `run_routine(routine, global_cfg, *, only_project=None, force=False) -> RunResult`:
    * if `not routine.enabled and not force` → log + return early RunResult (status SUCCESS).
    * `run_id`, `run_dir` from `logstore.run_dir(...)` (collision-safe, M5).
    * **Prompt fast-fail (m1):** `snapshot_prompt` + read prompt text inside a guard; if the
      prompt file is missing/unreadable at run time, write a `run.json` with overall ERROR
      and return immediately — do not enter the project loop.
    * **`--project` matching (m3):** resolve `only_project` and each `routine.projects` entry
      with `Path(...).expanduser().resolve()`; filter by resolved-path equality (not basename).
      Error if `only_project` matches nothing.
    * build the per-run `taken` name set; for each project compute
      `name = logstore.project_dir_name(resolved_path, taken)` and `pdir = project_dir(...)`.
    * per project call `_run_one_project(...)`, collecting `ProjectResult`; never let one
      failure abort the loop (wrap in try/except → error ProjectResult with the traceback
      in `reason` and `log_dir` pointing at `pdir`).
    * `overall_status` = ERROR if any project ERROR else SUCCESS; `write_run_json`.
    * `token = os.environ.get(global_cfg.email.postmark_token_env)`; `notify(...)`.
    * return RunResult.
  - `_run_one_project(routine, repo_path, run_id, pdir, prompt_text, global_cfg)
    -> ProjectResult`. Time with `time.monotonic()`. `log_path = git_log_path(pdir)` is
    threaded into **every** git/gh call. `branch = git_ops.branch_name(routine.name, run_id)`.
    Flow: validate `is_github_repo` → `fetch` → compute `dest` (=`work_dir/run_id/name`)
    **before** the try → `add_worktree(repo, dest, branch, base_branch, log_path)`. Then a
    `try/finally` whose `finally` calls `remove_worktree(repo, dest, log_path)` best-effort
    (B2 — cleanup guaranteed even if everything after `add_worktree` throws): inside the try
    `pre,pre_ok=gh_snapshot`; `rc,timed_out=run_claude(...)`; `claimed=read_result_file`;
    `post,post_ok=gh_snapshot`; `reconcile(...)` → fill ProjectResult
    (name/path/branch/duration/log_dir). If `add_worktree` itself fails, return an error
    ProjectResult (nothing to clean).
  - `exit_code(run_result) -> int`: 0 unless any ERROR.
- **Reuses**: steps 3,5,6,7,8,9 + `ptt.models`.

### 11. systemd integration (§14)
- **File**: `ptt/schedule.py` [new]
- **Change**:
  - `units_dir()` = `~/.config/systemd/user` (expanduser).
  - `render_service(routine_name, ptt_path) -> str` and `render_timer(routine_name,
    schedule) -> str` from the §14 templates (use `%h` literally; `EnvironmentFile=
    %h/.config/ptt/env`).
  - `validate_schedule(schedule)`: `systemd-analyze calendar "<schedule>"`; raise on non-zero.
  - **systemd-absence (m2):** `install`/`uninstall`/`list_timers`/`validate_schedule` first
    check `shutil.which("systemctl")`/`("systemd-analyze")`; if missing, raise a clear
    "systemd user instance not available" error instead of a raw non-zero subprocess.
  - `install(routine)`: validate schedule; resolve `ptt` path
    (`shutil.which("ptt") or sys.executable -m ptt`); write `ptt-<name>.service`/`.timer`;
    `systemctl --user daemon-reload`; `systemctl --user enable --now ptt-<name>.timer`;
    print the one-time `sudo loginctl enable-linger <user>` note.
  - `uninstall(routine_name)`: `systemctl --user disable --now ptt-<name>.timer`
    (best-effort); remove both unit files; `daemon-reload`.
  - `list_timers() -> str`: `systemctl --user list-timers --all` output.
- **Reuses**: `ptt.proc.run` (step 4), `shutil`, `sys`, `getpass.getuser`.

### 12. CLI surface (§15)
- **File**: `ptt/cli.py` [new]
- **Change**: argparse with subparsers, each delegating to the modules above; `main(argv=None)`
  returns an int exit code.
  - `run <routine> [--project P] [--force]` → load cfg+routine, `runner.run_routine`,
    return `runner.exit_code`.
  - `list` → `config.list_routine_names()` + enabled + `schedule.list_timers()` next-run.
  - `logs <routine> [--run ID] [--project P]` → resolve latest/﹣specified run dir under
    `state_home`, print `run.json` summary + tail of selected logs.
  - `install <routine>` / `uninstall <routine>` → `schedule.*`.
  - `validate` → check `shutil.which` for claude/git/gh; `gh auth status` judged by **exit
    code** (gh writes status to stderr — m4); **token present** via
    `os.environ.get(global_cfg.email.postmark_token_env)`; `load_global_config()` + each
    routine loads. Print ✓/✗ table; non-zero exit on any failure.
  - `test-email` → read the token via `global_cfg.email.postmark_token_env` and **fail fast
    with a clear message if unset (M3)**; build a synthetic RunResult and `notify.send`.
- **Reuses**: every module; `argparse`, `shutil`.

### 13. README (§6, §7, §9, §10, §14)
- **File**: `README.md` [new]
- **Change**: quick start — create `~/.config/ptt/config.toml`, a routine TOML, the
  `env` file (`chmod 600`, `PTT_POSTMARK_TOKEN=...`); commands; the autonomy/permission
  note (§10); the one-time `sudo loginctl enable-linger` requirement (§14); where logs live.

### 14. Test harness + fakes (§16)
- **Files**: `tests/conftest.py` [new], `tests/fake_bin/claude` [new], `tests/fake_bin/gh` [new]
- **Change**:
  - `conftest.py` fixtures: `xdg_env` (monkeypatch `XDG_CONFIG_HOME/STATE_HOME/CACHE_HOME`
    to tmp dirs); `git_repo` (init a temp repo with a fake `origin` remote whose URL
    contains `github.com` — a bare repo on disk); `fake_path` (prepend `tests/fake_bin`
    to `PATH`, `chmod +x`).
  - `tests/fake_bin/claude`: shell script — read stdin, emit a line of canned
    `stream-json`, write a `.ptt-result.json` in cwd; behavior switchable by env var
    (e.g. `PTT_FAKE_MODE=pr|none|error|timeout`).
  - `tests/fake_bin/gh`: shell script — emit canned JSON for `pr list` / `issue list`,
    switchable via env (pre vs post snapshot).

### 15. Unit + integration tests (§16)
- **Files**: `tests/test_config.py`, `tests/test_outcomes.py`, `tests/test_notify.py`,
  `tests/test_schedule.py`, `tests/test_claude.py`, `tests/test_cli.py`,
  `tests/test_runner_integration.py` [all new]
- **Change**:
  - `test_config`: valid/invalid TOML, name/stem/regex, missing email fields, defaults
    merge, `~` expansion, bad enum values.
  - `test_outcomes`: every §11 branch with fixture snapshots + result files
    (confirmed PR, unverified claim, gh-observed-only, missing-file, timeout, **gh-snapshot
    failure → error, M2**).
  - `test_notify`: `should_send` matrix; subject/body for mixed outcomes; payload + headers
    with `urllib.request.urlopen` monkeypatched; retry-on-failure writes `.email-failed`;
    assert the token never appears in body/subject.
  - `test_schedule`: exact rendered `.service`/`.timer` text; install command sequence with
    `proc.run` monkeypatched; `validate_schedule` failure path; **missing-systemctl error (m2)**.
  - `test_claude` (**M4**): `build_argv` for both permission modes and model present/absent;
    `build_prompt` includes the result-schema footer.
  - `test_cli` (**B1**): `logs` run-resolution (latest vs `--run`, `--project` filter);
    `validate` pass/fail exit codes (claude/gh/token present vs missing); `list` output;
    `test-email` fails fast when token unset (M3).
  - `test_runner_integration`: with fakes on PATH + temp git repo, run `runner.run_routine`
    end-to-end for pr / none / error / timeout / unverified; assert `run.json`,
    per-project `result.json`, `git.log` + claude logs exist, captured Postmark payload
    (including the `(unverified)` tag and a `source=gh` line — m5), and exit code.
    **Also assert: (B2) `work_dir` is empty after a project that errors mid-run; (M5) two
    runs in the same second get distinct run dirs; (m3) `--project` selects the right repo
    when two projects share a basename.**

## Testing
- Run: `python -m pytest -q` (after `pip install -e ".[dev]"`).
- Manual smoke (optional, outside CI): `ptt validate` against a real `~/.config/ptt`.
- No test touches the network, real Claude, or real GitHub — all external seams faked/mocked.

## Risks
- **`claude -p` stream-json shape may differ from assumptions** → mitigation: ptt only
  treats stdout as an opaque log; outcomes come from `.ptt-result.json` + `gh` delta, not
  from parsing the stream. Keep it that way.
- **gh not authenticated / repo has no GitHub origin** → caught by `is_github_repo` and
  `ptt validate`; project marked `error`, others continue.
- **systemd user timers don't fire when logged out** → `install` prints the one-time
  `loginctl enable-linger` step (cannot be automated; needs sudo).
- **Process-group kill portability** → Linux-only is acceptable per spec (Arch target);
  uses `os.killpg` + `start_new_session=True`.
- **Postmark token leakage** → token only from env/`env` file; never written to logs,
  `run.json`, or email; assert this in `test_notify`.
- **Basename collisions across projects in one run** → `project_dir_name` appends a path
  hash; covered by a `test_logstore`-style assertion in integration test.

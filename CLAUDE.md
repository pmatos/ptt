# ptt — project guide

**ptt** (prompt-then-that) runs Markdown prompts through Claude Code against git projects
on a schedule, opens PRs/issues via `gh`, and emails summaries via Postmark. Python 3.11+,
**stdlib only** (no runtime deps). Tooling is **uv**.

## Commands

- Install deps: `uv sync`
- Run tests: `uv run pytest`
- Type-check: `uv run ty check`
- Lint / format: `uv run ruff check` / `uv run ruff format`
- Run the CLI: `uv run ptt <cmd>`  (or `ptt <cmd>` if installed via `uv tool install .`)

## Conventions

- **TDD**: add or adjust tests before implementation; keep the suite green.
- Keep modules single-purpose. The design lives in `docs/specs/2026-06-30-ptt-design.md`.
- External seams (`claude`, `gh`, Postmark) stay behind thin wrappers so they're mockable;
  tests use fakes in `tests/fake_bin/` — never hit the network or real services.

## Documentation rule (IMPORTANT)

Whenever you implement a new feature or change user-facing behavior, you MUST **amend or
extend the tutorial at `docs/tutorial.md`** in the same change (and update `README.md` if
setup or the command surface changed). The tutorial is the canonical walkthrough and is
expected to stay in sync — a feature is not "done" until the tutorial reflects it.

---
status: accepted
---

# Run every project in a per-run clone of its remote, not a worktree of a local checkout

ptt originally isolated each project by adding a `git worktree` off the user's **local
checkout**: `git -C <repo> fetch origin <base>` then `git -C <repo> worktree add … -b
ptt/<routine>/<run-id> origin/<base>`. When remote (ephemeral) projects were added, that
worktree path was kept for *local* entries while remotes were cloned. Running local
projects through a worktree of the local checkout has three concrete problems:

- **The local checkout had to exist.** A configured path that was moved or deleted failed
  with a misleading `not a GitHub repo (origin missing or non-github)` — because
  `git -C <missing>` exits non-zero and was classified as "no github origin".
- **It mutated the local repo.** `fetch` updated its remote-tracking refs, and
  `worktree add -b` created a `ptt/<routine>/<run-id>` branch in it. `worktree remove`
  deletes the worktree but **not** the branch, so every run leaked a dead branch into the
  user's repo.
- **It ran against local state**, sharing the checkout's object store and config.

ptt now clones the project's github.com remote fresh into `~/.cache/ptt/work` per run for
**every** project — local and remote alike (`git clone --single-branch --branch <base>
<url> <dest>` + `git checkout -b ptt/<routine>/<run-id>`), runs Claude there, and deletes
the directory afterward. A local entry contributes only its `origin` URL (read
`git config --get remote.origin.url`, read-only); it is never fetched into, branched, or
run in, and the worktree helpers are gone.

## Considered options

- **Keep worktrees for local entries** (the state after remote projects landed). Rejected:
  it leaves the exact coupling the user objected to — local dir required, mutated, and the
  `ptt/*` branch leaked — for the common case of a project you happen to have checked out.
- **Clone from the local path, then repoint `origin` to the github URL.** Works offline and
  cheaply, but clones *stale* local state rather than the latest remote, and still requires
  the local checkout to exist. Rejected: cloning the remote directly gives both isolation
  and freshness, and needs no local checkout.

## Consequences

- **Uniform, full isolation.** Local and remote projects take the same code path. Nothing
  in a local checkout is read beyond a read-only `git config --get remote.origin.url`, and
  no `ptt/*` branch is ever left behind.
- **Local checkout optional.** A project given as a slug or URL runs with no local clone
  present; a project given as a local path runs even if that path is on a different branch
  or has uncommitted work (the remote is what's cloned).
- **`git_ops` shrinks.** `fetch`, `add_worktree`, and `remove_worktree` are removed as dead
  code; `origin_url` is added. No project uses `git worktree` anymore.
- **Slightly more network/disk per run.** A full clone of `<base_branch>` replaces a local
  worktree checkout. Bounded with `--single-branch`; the throwaway clone is removed in a
  `finally`.

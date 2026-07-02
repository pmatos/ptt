"""Outcome detection: snapshot gh state, parse Claude's .ptt-result.json, and
reconcile the two into a final result (§11). reconcile() is pure so the whole
truth table is unit-testable without gh."""

from __future__ import annotations

import json
from pathlib import Path

from ptt import proc
from ptt import models as m


def gh_snapshot(worktree: Path, log_path: Path) -> tuple[dict, bool]:
    """Returns (snapshot, ok). ok=False means a gh command failed — the caller
    must treat that as an error, never as 'no changes'."""
    pr = proc.run(
        ["gh", "pr", "list", "--json", "number,url,headRefName"],
        cwd=str(worktree),
        log_path=log_path,
    )
    iss = proc.run(
        ["gh", "issue", "list", "--json", "number,url,state"],
        cwd=str(worktree),
        log_path=log_path,
    )
    empty = {"prs": {}, "issues": {}}
    if pr.returncode != 0 or iss.returncode != 0:
        return empty, False
    try:
        prs = {
            int(x["number"]): {"url": x.get("url"), "headRefName": x.get("headRefName")}
            for x in json.loads(pr.stdout or "[]")
        }
        issues = {
            int(x["number"]): {"url": x.get("url"), "state": x.get("state", "")}
            for x in json.loads(iss.stdout or "[]")
        }
    except (json.JSONDecodeError, KeyError, TypeError):
        return empty, False
    return {"prs": prs, "issues": issues}, True


def read_result_file(worktree: Path) -> m.Outcome | None:
    f = Path(worktree) / ".ptt-result.json"
    if not f.is_file():
        return None
    try:
        data = json.loads(f.read_text())
        return m.Outcome(
            status=m.Status(data["status"]),
            action=m.Action(data["action"]),
            url=data.get("url"),
            title=data.get("title", ""),
            summary=data.get("summary", ""),
        )
    except (json.JSONDecodeError, KeyError, ValueError, TypeError):
        return None


def reconcile(
    claimed,
    pre,
    post,
    pre_ok,
    post_ok,
    claude_rc,
    timed_out,
    stderr_tail: str = "",
    ephemeral: bool = False,
) -> dict:
    if timed_out:
        return _err(m.Action.NONE, "timeout")
    if not pre_ok or not post_ok:
        return _err(m.Action.NONE, "gh snapshot failed")

    new_prs = set(post["prs"]) - set(pre["prs"])
    new_issues = set(post["issues"]) - set(pre["issues"])
    newly_closed = {
        n
        for n, info in post["issues"].items()
        if str(info.get("state", "")).upper() == "CLOSED"
        and (
            n not in pre["issues"]
            or str(pre["issues"][n].get("state", "")).upper() != "CLOSED"
        )
    }
    observed = _observed(new_prs, new_issues, newly_closed, post)

    if claimed is None:
        if observed:
            return _from_observed(observed)
        if claude_rc != 0:
            return _err(
                m.Action.NONE, stderr_tail.strip() or f"claude exited {claude_rc}"
            )
        return _err(m.Action.NONE, "no result file")

    if claimed.status == m.Status.ERROR:
        if observed:
            return _from_observed(observed)
        return _err(claimed.action, claimed.summary or "claude reported error")

    if claimed.action in (m.Action.PR, m.Action.ISSUE_OPENED, m.Action.ISSUE_CLOSED):
        confirmed = _claim_confirmed(claimed.action, new_prs, new_issues, newly_closed)
        return _from_claim(
            claimed, verified=confirmed, reason=None if confirmed else "unverified"
        )

    # action none / commit: trust the claim unless gh observed something else
    if observed:
        return _from_observed(observed)
    if ephemeral and claimed.action == m.Action.COMMIT:
        # A remote project runs in a throwaway clone that is deleted after the run.
        # An unpushed commit would vanish with it, so a commit-only claim must not
        # be reported as success (unlike a local worktree, whose branch persists).
        return _err(
            m.Action.COMMIT,
            "commit was not pushed before the ephemeral clone was removed, so the "
            "change is lost; open a PR (push) for remote projects",
        )
    return _from_claim(claimed, verified=True, reason=None)


def _observed(new_prs, new_issues, newly_closed, post):
    if new_prs:
        n = sorted(new_prs)[0]
        return m.Action.PR, post["prs"][n].get("url")
    if new_issues:
        n = sorted(new_issues)[0]
        return m.Action.ISSUE_OPENED, post["issues"][n].get("url")
    if newly_closed:
        n = sorted(newly_closed)[0]
        return m.Action.ISSUE_CLOSED, post["issues"][n].get("url")
    return None


def _claim_confirmed(action, new_prs, new_issues, newly_closed) -> bool:
    return (
        (action == m.Action.PR and bool(new_prs))
        or (action == m.Action.ISSUE_OPENED and bool(new_issues))
        or (action == m.Action.ISSUE_CLOSED and bool(newly_closed))
    )


def _from_claim(claimed, *, verified, reason) -> dict:
    return {
        "status": claimed.status,
        "action": claimed.action,
        "url": claimed.url,
        "title": claimed.title,
        "summary": claimed.summary,
        "verified": verified,
        "source": m.Source.CLAUDE,
        "reason": reason,
    }


def _from_observed(observed) -> dict:
    action, url = observed
    return {
        "status": m.Status.SUCCESS,
        "action": action,
        "url": url,
        "title": f"{action} detected via gh",
        "summary": "Observed via gh state diff.",
        "verified": True,
        "source": m.Source.GH,
        "reason": None,
    }


def _err(action, reason) -> dict:
    return {
        "status": m.Status.ERROR,
        "action": action,
        "url": None,
        "title": "",
        "summary": reason,
        "verified": False,
        "source": m.Source.CLAUDE,
        "reason": reason,
    }

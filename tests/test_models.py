import json
from pathlib import Path

from ptt import models as m


def test_run_result_to_dict_is_json_serializable_with_plain_strings():
    pr = m.ProjectResult(
        name="rightkey",
        path="/home/x/dev/rightkey",
        status=m.Status.SUCCESS,
        action=m.Action.PR,
        url="https://github.com/x/rightkey/pull/9",
        title="Refactor",
        summary="did it",
        verified=True,
        source=m.Source.CLAUDE,
        reason=None,
        branch="ptt/code-audit/20260630T050000Z",
        duration_s=12.5,
        log_dir="/state/runs/code-audit/.../projects/rightkey",
    )
    run = m.RunResult(
        routine="code-audit",
        run_id="20260630T050000Z",
        started_at="2026-06-30T05:00:00Z",
        ended_at="2026-06-30T05:02:00Z",
        overall_status=m.Status.SUCCESS,
        projects=[pr],
        run_dir="/state/runs/code-audit/20260630T050000Z",
    )
    d = run.to_dict()
    # round-trips through json with no custom encoder -> enums became str values
    text = json.dumps(d)
    back = json.loads(text)
    assert back["overall_status"] == "success"
    assert back["projects"][0]["status"] == "success"
    assert back["projects"][0]["action"] == "pr"
    assert back["projects"][0]["source"] == "claude"
    assert back["projects"][0]["verified"] is True


def test_enums_are_plain_string_values():
    assert m.Status.NO_ACTION == "no_action"
    assert m.Action.ISSUE_CLOSED == "issue_closed"
    assert m.PermissionMode.ACCEPT_EDITS == "acceptEdits"
    assert m.EmailOn.CHANGES == "changes"


def test_routine_holds_expanded_paths():
    r = m.Routine(
        name="code-audit",
        description="",
        enabled=True,
        prompt=Path("/home/x/p.md"),
        schedule="Mon..Fri 05:00",
        projects=[Path("/home/x/dev/a")],
        base_branch="main",
        permission_mode=m.PermissionMode.BYPASS,
        model=None,
        timeout_minutes=30,
        work_dir=Path("/home/x/.cache/ptt/work"),
    )
    assert r.projects == [Path("/home/x/dev/a")]
    assert r.model is None

import json
import re

from ptt import logstore
from ptt import models as m


def test_new_run_id_format():
    assert re.match(r"^\d{8}T\d{6}Z$", logstore.new_run_id())


def test_make_run_dir_collision_gets_suffix(tmp_xdg):
    rid = "20260630T050000Z"
    id1, p1 = logstore.make_run_dir("audit", rid)
    id2, p2 = logstore.make_run_dir("audit", rid)  # same second
    assert id1 == rid and p1.is_dir()
    assert id2 == rid + "-2" and p2.is_dir()
    assert p1 != p2


def test_project_dir_name_disambiguates_collisions():
    taken: set[str] = set()
    n1 = logstore.project_dir_name("repo", "/a/repo", taken)
    taken.add(n1)
    n2 = logstore.project_dir_name("repo", "/b/repo", taken)
    assert n1 == "repo"
    assert n2 != n1 and n2.startswith("repo-")


def test_snapshot_prompt_copies_content(tmp_xdg, tmp_path):
    _, run_dir = logstore.make_run_dir("audit", "20260630T060000Z")
    src = tmp_path / "p.md"
    src.write_text("PROMPT BODY")
    logstore.snapshot_prompt(run_dir, src)
    assert (run_dir / "prompt.md").read_text() == "PROMPT BODY"


def test_git_log_append(tmp_xdg):
    _, run_dir = logstore.make_run_dir("audit", "20260630T070000Z")
    pdir = logstore.project_dir(run_dir, "repo")
    logstore.append_git_log(pdir, "line one")
    logstore.append_git_log(pdir, "line two")
    text = logstore.git_log_path(pdir).read_text()
    assert "line one" in text and "line two" in text


def test_write_run_json_roundtrip(tmp_xdg):
    _, run_dir = logstore.make_run_dir("audit", "20260630T080000Z")
    run = m.RunResult(
        routine="audit",
        run_id="20260630T080000Z",
        started_at="s",
        ended_at="e",
        overall_status=m.Status.SUCCESS,
        projects=[],
        run_dir=str(run_dir),
    )
    logstore.write_run_json(run_dir, run)
    data = json.loads((run_dir / "run.json").read_text())
    assert data["overall_status"] == "success"
    assert data["routine"] == "audit"

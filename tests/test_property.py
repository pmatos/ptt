"""Property-based tests (Hypothesis) for ptt's pure parse/normalize/reconcile
seams. These assert invariants over *arbitrary* input — totality (never crash),
safety (log-dir names never collapse or traverse), and shape (reconcile always
returns a well-formed result) — that the example-based tests can't exhaust."""

import json
from pathlib import Path

import pytest
from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from ptt import config, outcomes, projects, schedule
from ptt import models as m

# utf-8-encodable text excludes lone surrogates (which would blow up on write/encode);
# these domains mirror what actually reaches ptt: TOML/JSON strings and CLI args.
TEXT = st.text(st.characters(codec="utf-8"), max_size=120)
TEXT_NO_NUL = st.text(
    st.characters(codec="utf-8", exclude_characters="\x00"), max_size=120
)

ENUMS = [
    m.Status,
    m.Action,
    m.EmailOn,
    m.SmtpSecurity,
    m.PermissionMode,
    m.Effort,
    m.Source,
]


# --- projects.parse / _safe_name / slug_from_url -----------------------------


@given(raw=TEXT_NO_NUL)
def test_parse_never_yields_a_collapsing_or_traversing_name(raw):
    # dest = work_dir/<run_id>/<name> is rmtree'd on cleanup, so a name of
    # "", ".", or ".." (or one containing a separator) would delete the wrong dir.
    spec = projects.parse(raw)
    assert spec.name not in ("", ".", "..")
    assert "/" not in spec.name
    assert spec.raw == raw


@given(name=TEXT_NO_NUL, use_path=st.booleans())
def test_safe_name_is_always_a_usable_single_segment(name, use_path):
    path = Path(name) if use_path and name else None
    assert projects._safe_name(name, path) not in ("", ".", "..")


@given(url=TEXT)
def test_slug_from_url_is_none_or_a_single_slash_slug(url):
    slug = projects.slug_from_url(url)
    assert slug is None or slug.count("/") == 1


# --- outcomes.reconcile / read_result_file -----------------------------------

RESULT_KEYS = {
    "status",
    "action",
    "url",
    "title",
    "summary",
    "verified",
    "source",
    "reason",
}


def _snapshots():
    pr_entry = st.fixed_dictionaries(
        {"url": st.none() | TEXT, "headRefName": st.none() | TEXT}
    )
    issue_entry = st.fixed_dictionaries(
        {
            "url": st.none() | TEXT,
            "state": st.sampled_from(["OPEN", "CLOSED", ""]) | TEXT,
        }
    )
    return st.builds(
        lambda prs, issues: {"prs": prs, "issues": issues},
        st.dictionaries(st.integers(), pr_entry, max_size=5),
        st.dictionaries(st.integers(), issue_entry, max_size=5),
    )


@st.composite
def _claims(draw):
    return m.Outcome(
        status=draw(st.sampled_from(list(m.Status))),
        action=draw(st.sampled_from(list(m.Action))),
        url=draw(st.none() | TEXT),
        title=draw(TEXT),
        summary=draw(TEXT),
    )


@given(
    claimed=st.none() | _claims(),
    pre=_snapshots(),
    post=_snapshots(),
    pre_ok=st.booleans(),
    post_ok=st.booleans(),
    claude_rc=st.integers(min_value=-5, max_value=130),
    timed_out=st.booleans(),
    stderr_tail=TEXT,
    ephemeral=st.booleans(),
)
def test_reconcile_is_total_and_well_formed(
    claimed, pre, post, pre_ok, post_ok, claude_rc, timed_out, stderr_tail, ephemeral
):
    r = outcomes.reconcile(
        claimed,
        pre,
        post,
        pre_ok,
        post_ok,
        claude_rc,
        timed_out,
        stderr_tail,
        ephemeral,
    )
    assert isinstance(r, dict)
    assert set(r) == RESULT_KEYS
    assert r["status"] in set(m.Status)
    assert r["action"] in set(m.Action)
    assert r["source"] in set(m.Source)
    assert isinstance(r["verified"], bool)


_JSON_VALUES = st.none() | st.booleans() | st.integers() | TEXT
_JSON_OBJECTS = st.builds(json.dumps, st.dictionaries(TEXT, _JSON_VALUES, max_size=6))


@settings(suppress_health_check=[HealthCheck.function_scoped_fixture], deadline=None)
@given(content=TEXT | _JSON_OBJECTS)
def test_read_result_file_never_raises(tmp_path, content):
    (tmp_path / ".ptt-result.json").write_text(content)
    result = outcomes.read_result_file(tmp_path)
    assert result is None or isinstance(result, m.Outcome)


# --- config._enum ------------------------------------------------------------


@given(member=st.sampled_from([e for cls in ENUMS for e in cls]))
def test_enum_round_trips_every_valid_member(member):
    cls = type(member)
    assert config._enum(cls, str(member), "field") is member


@given(cls=st.sampled_from(ENUMS), value=TEXT)
def test_enum_rejects_unknown_values_as_configerror(cls, value):
    assume(value not in {str(e) for e in cls})
    with pytest.raises(config.ConfigError):
        config._enum(cls, value, "field")


# --- schedule.render_service / render_timer ----------------------------------


@given(name=TEXT, cmd=TEXT)
def test_render_service_is_total_and_structured(name, cmd):
    out = schedule.render_service(name, cmd)
    assert "[Unit]" in out and "[Service]" in out
    assert "Type=oneshot" in out
    assert f"ExecStart={cmd} run {name}" in out


@given(name=TEXT, sched=TEXT)
def test_render_timer_is_total_and_structured(name, sched):
    out = schedule.render_timer(name, sched)
    assert "[Timer]" in out and "[Install]" in out
    assert f"OnCalendar={sched}" in out

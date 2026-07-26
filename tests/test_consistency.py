"""Cross-artefact consistency — does the repo still agree with itself?

`decisions/0006` records the defect that motivated this: `BRAIN.md` routed every
session to `knowledge/`, a directory that had never been created. The link sat
dead until a human followed it. Tests proved the code agreed with itself; nothing
proved the prose did.

The last test in this module runs every check against the real repository. If it
fails, some document is making a claim the repository no longer supports.
"""

from __future__ import annotations

import pytest

from consistency import (
    PLANNED_PATHS,
    Finding,
    check_claimed_test_count,
    check_decision_numbering,
    check_evidence_ids,
    check_known_issues_numbering,
    check_planned_paths_are_still_planned,
    check_referenced_decisions,
    check_referenced_paths,
    check_thresholds_version,
    markdown_files,
    run_all,
)


@pytest.fixture
def repo(tmp_path):
    """A minimal but structurally valid repo."""
    (tmp_path / "decisions").mkdir()
    (tmp_path / "decisions" / "0001-first.md").write_text("# 1", encoding="utf-8")
    (tmp_path / "memory").mkdir()
    (tmp_path / "memory" / "KNOWN_ISSUES.md").write_text("### 1. one\n\n### 2. two\n", encoding="utf-8")
    (tmp_path / "config").mkdir()
    (tmp_path / "config" / "thresholds.yaml").write_text('version: "1.1.0"\n', encoding="utf-8")
    return tmp_path


def doc(root, name: str, body: str):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


# ======================================================================
# Referenced paths — the check that would have caught the dead link
# ======================================================================
def test_a_dead_directory_link_is_caught(repo):
    doc(repo, "BRAIN.md", "Load `knowledge/rules.md` every session.")
    findings = check_referenced_paths(repo)
    assert len(findings) == 1
    assert "knowledge/rules.md" in findings[0].detail


def test_a_live_link_passes(repo):
    doc(repo, "knowledge/rules.md", "rules")
    doc(repo, "BRAIN.md", "Load `knowledge/rules.md` every session.")
    assert check_referenced_paths(repo) == []


def test_layer_paths_resolve_without_the_src_prefix(repo):
    """Docs cite `engine/shariah.py`; the file lives at `src/engine/shariah.py`."""
    doc(repo, "src/engine/shariah.py", "")
    doc(repo, "BRAIN.md", "See `engine/shariah.py`.")
    assert check_referenced_paths(repo) == []


def test_a_symbol_reference_checks_its_module(repo):
    doc(repo, "src/engine/portfolio.py", "")
    doc(repo, "BRAIN.md", "See `engine/portfolio.trade_cost` and `engine/types.py::PeriodType`.")
    findings = check_referenced_paths(repo)
    assert [f.detail for f in findings] == ["engine/types.py does not exist"]


def test_schema_placeholders_are_not_treated_as_paths(repo):
    doc(repo, "BRAIN.md", "Layout is `memory/evidence/<publisher>/<series>/<as_of>/` and `decisions/NNNN-*.md`.")
    assert check_referenced_paths(repo) == []


def test_prose_and_identifiers_in_backticks_are_ignored(repo):
    doc(repo, "BRAIN.md", "Set `status` to `POPULATED`, see `DATA_INSUFFICIENT` and `max(a, b)`.")
    assert check_referenced_paths(repo) == []


def test_decisions_are_exempt_because_they_are_history(repo):
    """R5: an ADR cites where a file was when the decision was made. Forcing it
    current would mean editing the record of a past decision."""
    doc(repo, "decisions/0002-old.md", "We archived it at `memory/sources/old.xlsx`.")
    assert check_referenced_paths(repo) == []


def test_planned_paths_are_exempt_until_they_exist(repo):
    doc(repo, "memory/NEXT_TASK.md", "Build `engine/policy.py` next.")
    assert check_referenced_paths(repo) == []


def test_a_planned_path_that_now_exists_must_leave_the_allowlist(repo):
    """Otherwise the exemption outlives its reason and becomes a blind spot."""
    assert check_planned_paths_are_still_planned(repo) == []
    doc(repo, "src/engine/policy.py", "")
    findings = check_planned_paths_are_still_planned(repo)
    assert len(findings) == 1
    assert "PLANNED_PATHS" in findings[0].detail


def test_markdown_scan_skips_vendored_trees(repo):
    doc(repo, "BRAIN.md", "x")
    doc(repo, ".venv/lib/site-packages/other/README.md", "`nope/missing.md`")
    assert all(".venv" not in p.parts for p in markdown_files(repo))


# ======================================================================
# ADR citations and numbering
# ======================================================================
def test_a_citation_to_a_missing_adr_is_caught(repo):
    doc(repo, "BRAIN.md", "See `decisions/0009` for why.")
    findings = check_referenced_decisions(repo)
    assert len(findings) == 1
    assert "0009" in findings[0].detail


def test_a_citation_to_a_real_adr_passes(repo):
    doc(repo, "BRAIN.md", "See `decisions/0001` for why.")
    assert check_referenced_decisions(repo) == []


def test_adr_numbering_must_be_gap_free(repo):
    (repo / "decisions" / "0003-skipped.md").write_text("#", encoding="utf-8")
    findings = check_decision_numbering(repo)
    assert len(findings) == 1
    assert "0002" in findings[0].detail


def test_adr_numbering_passes_when_sequential(repo):
    (repo / "decisions" / "0002-next.md").write_text("#", encoding="utf-8")
    assert check_decision_numbering(repo) == []


def test_adr_numbering_on_an_empty_directory_is_silent(tmp_path):
    (tmp_path / "decisions").mkdir()
    assert check_decision_numbering(tmp_path) == []


# ======================================================================
# KNOWN_ISSUES numbering
# ======================================================================
def test_duplicate_issue_numbers_are_caught(repo):
    (repo / "memory" / "KNOWN_ISSUES.md").write_text("### 1. a\n\n### 1. b\n", encoding="utf-8")
    assert any("duplicate" in f.detail for f in check_known_issues_numbering(repo))


def test_non_sequential_issue_numbers_are_caught(repo):
    (repo / "memory" / "KNOWN_ISSUES.md").write_text("### 1. a\n\n### 3. b\n", encoding="utf-8")
    assert any("not 1..n" in f.detail for f in check_known_issues_numbering(repo))


def test_sequential_issue_numbers_pass(repo):
    assert check_known_issues_numbering(repo) == []


def test_a_missing_known_issues_file_is_reported(tmp_path):
    assert check_known_issues_numbering(tmp_path)[0].detail == "file is missing"


# ======================================================================
# Evidence ids
# ======================================================================
def test_an_unknown_evidence_id_is_caught(repo):
    doc(repo, "config/universe.yaml", 'retrieved:\n  evidence_id: "nope/missing/2026-01-01"\n')
    findings = check_evidence_ids(repo)
    assert len(findings) == 1
    assert "nope/missing/2026-01-01" in findings[0].detail


def test_a_known_evidence_id_passes(repo):
    ev = repo / "memory" / "evidence" / "pub" / "series" / "2026-01-01"
    ev.mkdir(parents=True)
    (ev / "f.txt").write_text("x", encoding="utf-8")
    (ev / "metadata.yaml").write_text(
        'id: "pub/series/2026-01-01"\ntitle: t\npublisher: p\nkind: K\nfile: f.txt\n'
        'sha256: "abc"\nas_of: "2026-01-01"\n',
        encoding="utf-8",
    )
    doc(repo, "config/universe.yaml", 'retrieved:\n  evidence_id: "pub/series/2026-01-01"\n')
    assert check_evidence_ids(repo) == []


# ======================================================================
# Claimed test count
# ======================================================================
def test_documents_that_disagree_on_the_test_count_are_caught(repo):
    doc(repo, "BRAIN.md", "tests/ <- 709 tests. If green, the rules hold.")
    doc(repo, "memory/CURRENT_STATE.md", "**Suite:** 700 tests green")
    assert any("disagree" in f.detail for f in check_claimed_test_count(repo, None))


def test_a_claim_that_does_not_match_the_suite_is_caught(repo):
    doc(repo, "BRAIN.md", "tests/ <- 709 tests.")
    doc(repo, "memory/CURRENT_STATE.md", "**Suite:** 709 tests green")
    findings = check_claimed_test_count(repo, actual=800)
    assert len(findings) == 2
    assert all("claims 709 tests, suite has 800" in f.detail for f in findings)


def test_matching_claims_pass(repo):
    doc(repo, "BRAIN.md", "tests/ <- 709 tests.")
    doc(repo, "memory/CURRENT_STATE.md", "**Suite:** 709 tests green")
    assert check_claimed_test_count(repo, actual=709) == []


def test_absent_claims_are_not_an_error(repo):
    assert check_claimed_test_count(repo, actual=709) == []


# ======================================================================
# Thresholds version
# ======================================================================
def test_a_stale_thresholds_version_in_prose_is_caught(repo):
    doc(repo, "memory/CURRENT_STATE.md", "`config/thresholds.yaml` v1.0.0 carries the fees")
    findings = check_thresholds_version(repo)
    assert len(findings) == 1
    assert "claims v1.0.0, config is v1.1.0" in findings[0].detail


def test_a_current_thresholds_version_passes(repo):
    doc(repo, "memory/CURRENT_STATE.md", "`config/thresholds.yaml` v1.1.0 carries the fees")
    assert check_thresholds_version(repo) == []


def test_adrs_may_quote_the_version_of_their_day(repo):
    """An ADR records what was true when it was written and is never edited."""
    doc(repo, "decisions/0002-old.md", "`config/thresholds.yaml` v1.0.0 was current then")
    assert check_thresholds_version(repo) == []


def test_a_missing_thresholds_file_is_reported(tmp_path):
    assert check_thresholds_version(tmp_path)[0].detail == "file is missing"


def test_findings_render_readably():
    assert str(Finding("c", "w", "d")) == "[c] w: d"


# ======================================================================
# THE REAL REPOSITORY
# ======================================================================
def test_the_repository_agrees_with_itself(collected_test_count):
    """Every check, against this repo. A failure here means a document is making
    a claim the repository no longer supports — fix the document or the repo,
    never this test."""
    findings = run_all(actual_test_count=collected_test_count)
    assert not findings, "\n".join(str(f) for f in findings)


def test_planned_paths_documents_its_reasons():
    """An allowlist without reasons rots into a list nobody dares to prune."""
    assert PLANNED_PATHS
    for path, reason in PLANNED_PATHS.items():
        assert len(reason) > 15, f"{path} needs a real reason, got {reason!r}"

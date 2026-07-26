"""Cross-artefact consistency checks — does the repo still agree with itself?

The test suite proves the *code* is self-consistent. Nothing proved that the
prose did: `BRAIN.md` routed sessions to `knowledge/` for weeks while that
directory did not exist, and the only thing that caught it was a human following
the link (`decisions/0006`). A router pointing at nothing is the memory system
failing in exactly the way it exists to prevent, and it is precisely the class of
defect a test can catch for free.

So this checks the seams between artefacts, not inside any one of them:

* every repo path a document points at exists
* every ``decisions/NNNN`` citation resolves to a real ADR
* every ``evidence_id`` resolves to a real evidence record
* ADR and KNOWN_ISSUES numbering is unique and gap-free
* the test count claimed in prose matches the suite
* the thresholds version claimed in prose matches ``config/thresholds.yaml``

Boundary module: it reads files, so it lives at the ``src/`` root beside
:mod:`config_loader` rather than inside a layer. Nothing in ``engine/`` imports it.

Run standalone::

    uv run python -m consistency
"""

from __future__ import annotations

import re
import sys
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent

#: Top-level directories a documented path may start with. Anything else in
#: backticks is prose, a config key, or a code identifier — not a path.
_REPO_DIRS = (
    "config",
    "decisions",
    "docs",
    "examples",
    "knowledge",
    "checklists",
    "memory",
    "src",
    "tests",
    # Layer names are cited without the `src/` prefix throughout the docs
    # ("engine/shariah.py"), so both spellings have to resolve.
    "engine",
    "ingestion",
    "validation",
    "reporting",
    "research",
    "store",
    "common",
    "db",
    "api",
)

#: Root-level files that may be cited bare.
_ROOT_FILES = ("BRAIN.md", "CLAUDE.md", "README.md", "pyproject.toml", ".gitignore")

#: A path reference inside backticks. Rejects anything with a placeholder or a
#: wildcard — `memory/evidence/<publisher>/<series>/` is a schema, not a path.
_BACKTICKED = re.compile(r"`([^`\n]+)`")
_PLACEHOLDER = re.compile(r"[<>*?{}\[\]…]|NNNN|\.\.\.")

#: File extensions this repo actually uses. A trailing dotted segment that is
#: not one of these is a symbol reference (`engine/portfolio.trade_cost`), not
#: a filename, so it is retried as a module path.
_KNOWN_EXTS = frozenset({".py", ".md", ".yaml", ".yml", ".jsonl", ".json", ".toml", ".txt", ".sql", ".xlsx", ".pdf"})

#: A bare ADR citation. Owned by :func:`check_referenced_decisions`, which
#: resolves the number against the real filenames — the path check would only
#: report a false miss because `decisions/0004` has no extension on disk.
_BARE_ADR = re.compile(r"^decisions/\d{4}$")

#: Paths documented on purpose before they exist. Each is a promise the specs
#: make about the target state, not a broken link. Adding to this list must be
#: a deliberate act: an entry here is a claim that something is *planned*, and
#: a stale entry hides a genuine dead reference.
PLANNED_PATHS: dict[str, str] = {
    "engine/policy.py": "M2 — the IPS module, not yet written",
    "config/ips_schema.yaml": "M2 — IPS schema, drafted in the spec only",
    "src/tools/": "M3 — the tool/MCP layer, not yet built",
    "config/cio_persona.md": "M8 — the persona file, not yet written",
    "memory/portfolio/ledger.jsonl": "created on the first portfolio event (decisions/0003)",
}

_DECISION_REF = re.compile(r"decisions/(\d{4})")
_EVIDENCE_ID_LINE = re.compile(r'evidence_id:\s*["\']([^"\']+)["\']')
_TEST_COUNT = re.compile(r"(\d[\d ,]*)\s*tests\b")
_KNOWN_ISSUE_HEADING = re.compile(r"^###\s+(\d+)\.\s", re.MULTILINE)
_ADR_FILENAME = re.compile(r"^(\d{4})-.+\.md$")


@dataclass(frozen=True)
class Finding:
    """One inconsistency. ``where`` is the file that made the claim."""

    check: str
    where: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.check}] {self.where}: {self.detail}"


def markdown_files(root: Path) -> list[Path]:
    """Every tracked markdown document, excluding anything under a venv."""
    out: list[Path] = []
    for path in sorted(root.rglob("*.md")):
        parts = set(path.parts)
        if parts & {".venv", "venv", "node_modules", ".git", "site-packages"}:
            continue
        out.append(path)
    return out


def _exists(root: Path, rel: str) -> bool:
    """Literal path, or the ``src/``-prefixed form — the docs cite engine
    modules as ``engine/shariah.py`` while they live under ``src/``."""
    return (root / rel).exists() or (root / "src" / rel).exists()


def _resolves(root: Path, candidate: str) -> bool:
    cleaned = candidate.rstrip("/")
    if _exists(root, cleaned):
        return True
    # `engine/portfolio.trade_cost` names a function, not a file. Retry the
    # module it lives in rather than reporting a dead path.
    tail = cleaned.rsplit("/", 1)[-1]
    if "." in tail and not any(tail.endswith(ext) for ext in _KNOWN_EXTS):
        return _exists(root, cleaned.rsplit(".", 1)[0] + ".py")
    return False


def _path_candidates(text: str) -> Iterable[str]:
    for raw in _BACKTICKED.findall(text):
        token = raw.strip()
        # `engine/types.py::PeriodType` — check the file, ignore the symbol.
        token = token.split("::", 1)[0].strip()
        if not token or _PLACEHOLDER.search(token) or " " in token:
            continue
        if _BARE_ADR.match(token) or token in PLANNED_PATHS:
            continue
        head = token.split("/")[0]
        if (head in _REPO_DIRS and "/" in token) or token in _ROOT_FILES:
            yield token


def check_referenced_paths(root: Path) -> list[Finding]:
    """Every backticked repo path in a *current* document must exist.

    This is the check that would have caught the dead ``knowledge/`` link.

    ``decisions/`` is deliberately exempt. ADRs are append-only history (R5):
    ``0005`` cites ``memory/sources/``, which is exactly where the file was
    when that decision was made. Requiring an ADR to stay current would mean
    editing the record of a past decision to match the present — the opposite
    of what an audit trail is for.
    """
    findings: list[Finding] = []
    for doc in markdown_files(root):
        if "decisions" in doc.relative_to(root).parts:
            continue
        text = doc.read_text(encoding="utf-8")
        for candidate in _path_candidates(text):
            if not _resolves(root, candidate):
                findings.append(
                    Finding("referenced-path", doc.relative_to(root).as_posix(), f"{candidate} does not exist")
                )
    return findings


def check_planned_paths_are_still_planned(root: Path) -> list[Finding]:
    """A path in :data:`PLANNED_PATHS` that now exists must leave the list.

    Without this the allowlist becomes a permanent blind spot: once
    ``engine/policy.py`` is written, every reference to it should be checked
    like any other, and the exemption must not silently outlive its reason.
    """
    return [
        Finding("planned-path", "src/consistency.py", f"{rel} exists now — remove it from PLANNED_PATHS ({why})")
        for rel, why in sorted(PLANNED_PATHS.items())
        if _exists(root, rel.rstrip("/"))
    ]


def check_referenced_decisions(root: Path) -> list[Finding]:
    """Every ``decisions/NNNN`` citation must resolve to an ADR on disk."""
    existing = {m.group(1) for p in (root / "decisions").glob("*.md") if (m := _ADR_FILENAME.match(p.name))}
    findings: list[Finding] = []
    for doc in markdown_files(root):
        text = doc.read_text(encoding="utf-8")
        for number in sorted(set(_DECISION_REF.findall(text))):
            if number not in existing:
                findings.append(
                    Finding("referenced-adr", doc.relative_to(root).as_posix(), f"decisions/{number} does not exist")
                )
    return findings


def check_decision_numbering(root: Path) -> list[Finding]:
    """ADR numbers must be unique and gap-free from 0001.

    A gap usually means an ADR was written and never committed; a duplicate
    means two decisions are competing for one citation.
    """
    numbers = sorted(int(m.group(1)) for p in (root / "decisions").glob("*.md") if (m := _ADR_FILENAME.match(p.name)))
    findings: list[Finding] = []
    if not numbers:
        return findings
    for position, number in enumerate(numbers, start=1):
        if number != position:
            findings.append(
                Finding(
                    "adr-numbering",
                    "decisions/",
                    f"expected {position:04d} at position {position}, found {number:04d}",
                )
            )
            break
    return findings


def check_known_issues_numbering(root: Path) -> list[Finding]:
    """`KNOWN_ISSUES.md` headings must stay uniquely and sequentially numbered.

    Issues get renumbered when one is inserted in the middle, and a stale
    duplicate makes "see issue 9" ambiguous.
    """
    path = root / "memory" / "KNOWN_ISSUES.md"
    if not path.is_file():
        return [Finding("known-issues", "memory/KNOWN_ISSUES.md", "file is missing")]
    numbers = [int(n) for n in _KNOWN_ISSUE_HEADING.findall(path.read_text(encoding="utf-8"))]
    findings: list[Finding] = []
    if len(numbers) != len(set(numbers)):
        findings.append(Finding("known-issues", "memory/KNOWN_ISSUES.md", f"duplicate issue numbers in {numbers}"))
    if numbers and numbers != list(range(1, len(numbers) + 1)):
        findings.append(Finding("known-issues", "memory/KNOWN_ISSUES.md", f"numbering is not 1..n: {numbers}"))
    return findings


def check_evidence_ids(root: Path) -> list[Finding]:
    """Every ``evidence_id:`` in config or golden files must resolve to a record."""
    from store.evidence import iter_records

    known = {r.id for r in iter_records(root / "memory" / "evidence")}
    findings: list[Finding] = []
    for pattern in ("config/**/*.yaml", "tests/golden/**/*.yaml"):
        for path in sorted(root.glob(pattern)):
            for evidence_id in _EVIDENCE_ID_LINE.findall(path.read_text(encoding="utf-8")):
                if evidence_id not in known:
                    findings.append(
                        Finding(
                            "evidence-id",
                            path.relative_to(root).as_posix(),
                            f"unknown evidence id {evidence_id!r}",
                        )
                    )
    return findings


def check_claimed_test_count(root: Path, actual: int | None) -> list[Finding]:
    """Prose claiming a test count must agree with the suite and with itself.

    ``BRAIN.md`` and ``CURRENT_STATE.md`` both advertise the size of the suite.
    Both went stale twice in a single working day before this check existed.
    """
    claims: dict[str, int] = {}
    for name in ("BRAIN.md", "memory/CURRENT_STATE.md"):
        path = root / name
        if not path.is_file():
            continue
        found = _TEST_COUNT.search(path.read_text(encoding="utf-8"))
        if found:
            claims[name] = int(found.group(1).replace(" ", "").replace(",", ""))

    findings: list[Finding] = []
    if len(set(claims.values())) > 1:
        findings.append(Finding("test-count", "BRAIN.md + CURRENT_STATE.md", f"documents disagree: {claims}"))
    if actual is not None:
        for name, claimed in claims.items():
            if claimed != actual:
                findings.append(Finding("test-count", name, f"claims {claimed} tests, suite has {actual}"))
    return findings


def check_thresholds_version(root: Path) -> list[Finding]:
    """A thresholds version quoted in prose must match the config file."""
    import yaml

    path = root / "config" / "thresholds.yaml"
    if not path.is_file():
        return [Finding("thresholds-version", "config/thresholds.yaml", "file is missing")]
    version = str(yaml.safe_load(path.read_text(encoding="utf-8"))["version"])
    findings: list[Finding] = []
    quoted = re.compile(r"thresholds\.yaml`?\s+v(\d+\.\d+\.\d+)")
    for doc in markdown_files(root):
        if doc.parts[-2:-1] == ("decisions",):
            continue  # ADRs are historical records; they cite the version of their day
        for claimed in quoted.findall(doc.read_text(encoding="utf-8")):
            if claimed != version:
                findings.append(
                    Finding(
                        "thresholds-version",
                        doc.relative_to(root).as_posix(),
                        f"claims v{claimed}, config is v{version}",
                    )
                )
    return findings


def run_all(root: Path | None = None, actual_test_count: int | None = None) -> list[Finding]:
    """Every check, in a stable order."""
    base = root if root is not None else _ROOT
    return [
        *check_referenced_paths(base),
        *check_planned_paths_are_still_planned(base),
        *check_referenced_decisions(base),
        *check_decision_numbering(base),
        *check_known_issues_numbering(base),
        *check_evidence_ids(base),
        *check_claimed_test_count(base, actual_test_count),
        *check_thresholds_version(base),
    ]


def main() -> int:
    findings = run_all()
    if not findings:
        print("consistency: repo agrees with itself")
        return 0
    print(f"consistency: {len(findings)} finding(s)")
    for finding in findings:
        print(f"  {finding}")
    return 1


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())

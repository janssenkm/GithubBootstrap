from __future__ import annotations

import re
from pathlib import Path

import yaml


POLICIES = (
    "context-policy.md",
    "engineering.md",
    "host-adapters.md",
    "issue-workflow.md",
    "security.md",
    "tool-policy.md",
    "verification.md",
)
SKILLS = ("issue-investigate", "issue-author", "issue-review", "issue-promote")
REQUIRED_SKILL_SECTIONS = (
    "Purpose",
    "Role",
    "Inputs",
    "Out of Scope",
    "Procedure",
    "Required Tools",
    "Evidence",
    "Acceptance",
    "Failure / Blocked Condition",
    "Prohibited Actions",
    "Handoff",
)


def _frontmatter(path: Path) -> tuple[dict, str]:
    text = path.read_text(encoding="utf-8")
    match = re.fullmatch(r"---\n(?P<meta>.*?)\n---\n(?P<body>.+)", text, re.DOTALL)
    assert match is not None, f"missing or malformed frontmatter: {path}"
    metadata = yaml.safe_load(match.group("meta"))
    assert isinstance(metadata, dict)
    return metadata, match.group("body")


def _sections(body: str) -> dict[str, str]:
    matches = list(re.finditer(r"^## (?P<title>[^\n]+)\n", body, re.MULTILINE))
    return {
        match.group("title"): body[match.end() : matches[index + 1].start() if index + 1 < len(matches) else None].strip()
        for index, match in enumerate(matches)
    }


def test_constitution_is_concise_and_indexes_every_policy(repository_root):
    text = (repository_root / "AGENTS.md").read_text(encoding="utf-8")
    assert len(text.splitlines()) <= 130
    for name in POLICIES:
        assert f".agent/{name}" in text
        assert (repository_root / ".agent" / name).is_file()
    for required in (
        "Bootstrap routing",
        "Human Git attribution",
        "Simplicity",
        "Surgical scope",
        "Evidence and completion",
        "Dispatch and independent verification",
    ):
        assert required in text


def test_skills_have_strict_metadata_and_role_contracts(repository_root):
    for name in SKILLS:
        path = repository_root / ".agents" / "skills" / name / "SKILL.md"
        metadata, body = _frontmatter(path)
        assert metadata == {
            "name": name,
            "description": metadata["description"],
        }
        assert isinstance(metadata["description"], str) and metadata["description"].startswith("Use when ")
        sections = _sections(body)
        assert tuple(sections) == REQUIRED_SKILL_SECTIONS
        assert all(sections.values())
        assert len(body.splitlines()) <= 110
        assert "model" not in metadata


def test_role_boundaries_and_fail_closed_tools(repository_root):
    investigator = (repository_root / ".agents/skills/issue-investigate/SKILL.md").read_text(encoding="utf-8")
    author = (repository_root / ".agents/skills/issue-author/SKILL.md").read_text(encoding="utf-8")
    reviewer = (repository_root / ".agents/skills/issue-review/SKILL.md").read_text(encoding="utf-8")
    promoter = (repository_root / ".agents/skills/issue-promote/SKILL.md").read_text(encoding="utf-8")
    tools = (repository_root / ".agent/tool-policy.md").read_text(encoding="utf-8")

    investigator_sections = _sections(_frontmatter(repository_root / ".agents/skills/issue-investigate/SKILL.md")[1])
    author_sections = _sections(_frontmatter(repository_root / ".agents/skills/issue-author/SKILL.md")[1])
    reviewer_sections = _sections(_frontmatter(repository_root / ".agents/skills/issue-review/SKILL.md")[1])
    promoter_sections = _sections(_frontmatter(repository_root / ".agents/skills/issue-promote/SKILL.md")[1])

    assert "read-only" in investigator.lower()
    assert "cited evidence" in author.lower()
    assert "author private reasoning" in reviewer.lower() and "must not" in reviewer.lower()
    assert "assume" in reviewer.lower() and "material error" in reviewer.lower()
    assert "/promote" in promoter and "must not" in promoter.lower()
    assert "remote" in promoter_sections["Out of Scope"].lower()
    assert "human authorization" in promoter_sections["Failure / Blocked Condition"].lower()

    for sections in (investigator_sections, author_sections, reviewer_sections, promoter_sections):
        procedure = sections["Procedure"]
        assert re.search(r"(?m)^1\. ", procedure)
        assert re.search(r"(?m)^2\. ", procedure)
        assert "blocked" in sections["Required Tools"].lower()
        assert "evidence" in sections["Acceptance"].lower()
        failure = sections["Failure / Blocked Condition"].lower()
        assert "evidence" in failure
        assert "authorization" in failure
        assert "tool" in failure

    assert "Context7 or official documentation" in tools
    assert "LSP" in tools and "auxiliary" in tools
    assert "blocked" in tools.lower()


def test_claude_thin_adapter_is_regular_file(repository_root):
    adapter = repository_root / "CLAUDE.md"
    assert adapter.is_file()
    assert not adapter.is_symlink()
    assert adapter.read_text(encoding="utf-8") == "@AGENTS.md\n"


def test_claude_skill_mirrors_are_byte_identical_regular_files(repository_root):
    for name in SKILLS:
        canonical = repository_root / ".agents" / "skills" / name / "SKILL.md"
        mirror = repository_root / ".claude" / "skills" / name / "SKILL.md"
        assert mirror.is_file()
        assert not mirror.is_symlink()
        assert mirror.read_bytes() == canonical.read_bytes()

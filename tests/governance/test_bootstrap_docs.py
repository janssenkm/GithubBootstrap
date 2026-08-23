from __future__ import annotations

import re
from pathlib import Path

import yaml


WORKFLOW_FILES = {
    "00-baseline-check.yml",
    "00-issue-intake.yml",
    "01-ai-development-workflow.yml",
    "02-engineering-governance.yml",
    "03-engineering-promotion.yml",
    "05-commit-lint.yml",
    "10-pr-ai-review.yml",
    "20-ci-build-test.yml",
    "50-milestone-review.yml",
    "51-milestone-review-provisional.yml",
    "52-milestone-review-finalize.yml",
    "53-milestone-acceptance-intent.yml",
    "54-milestone-acceptance-execute.yml",
    "55-milestone-acceptance-finalize.yml",
    "40-stale.yml",
}

V2_LABELS = {
    "type:intake",
    "type:candidate",
    "type:engineering",
    "state:new",
    "state:triaged",
    "state:investigating",
    "state:closed",
    "state:draft",
    "state:gate-failed",
    "state:gate-passed",
    "state:promoted",
    "state:contracted",
    "state:ready",
    "state:in-progress",
    "state:done",
    "state:cancelled",
    "governance:stale",
    "governance:transition-paused",
    "milestone:review",
}


def _text(root: Path, relative: str) -> str:
    return (root / relative).read_text(encoding="utf-8")


def test_settings_lists_complete_v2_taxonomy_and_safe_rollout(repository_root):
    settings = _text(repository_root, ".github/SETTINGS.md")
    for label in V2_LABELS:
        assert label in settings
    assert "AI_API_KEY" not in settings
    assert "AI_API_MODEL" not in settings
    assert "AI_API_BASEURL" not in settings
    assert "does not require repository AI secrets" in settings
    assert "dry-run" in settings and "warn" in settings and "enforce" in settings
    assert "fail closed" in settings
    assert "trusted_issue_authors" in settings
    assert "trusted_developers" in settings
    assert "trusted_reviewers" in settings
    assert "legacy transition" in settings.lower()
    for check in (
        "Engineering Contract Validation",
        "Configuration Validation",
        "Security Scanning",
    ):
        assert check in settings


def test_workflow_index_exactly_matches_workflow_directory(repository_root):
    index = _text(repository_root, ".github/WORKFLOWS.md")
    actual = {path.name for path in (repository_root / ".github/workflows").glob("*.yml")}
    indexed = set(re.findall(r"`([0-9][^`]*\.yml)`", index))
    assert actual == WORKFLOW_FILES
    assert indexed == actual
    assert "00-issue-ai-triage.yml" not in index
    for heading in (
        "Actor",
        "Input",
        "State transition",
        "Permissions",
        "Side effects",
        "Check",
        "Failure",
        "Recovery",
    ):
        assert heading in index
    assert "same job name" in index
    assert "Engineering Contract Validation" in index


def test_workflow_index_locks_sensitive_permissions_and_side_effects(repository_root):
    index = _text(repository_root, ".github/WORKFLOWS.md")
    rows = {
        match.group("file"): match.group("row")
        for match in re.finditer(
            r"^\| `(?P<file>[^`]+\.yml)`(?P<row>.*)$",
            index,
            re.MULTILINE,
        )
    }
    assert "`actions: read`, `contents: read`, `issues: read`" in rows["01-ai-development-workflow.yml"]
    assert "runner-local validation only; no repository or GitHub mutation" in rows["20-ci-build-test.yml"]
    assert "zero repository writes" in rows["50-milestone-review.yml"]
    assert "exact `/accept-milestone`" in rows["53-milestone-acceptance-intent.yml"]
    assert "single Milestone close" in rows["54-milestone-acceptance-execute.yml"]


def test_workflow_index_check_sets_match_all_actual_job_names(repository_root):
    index = _text(repository_root, ".github/WORKFLOWS.md")
    documented = {}
    for line in index.splitlines():
        if not line.startswith("| `") or ".yml`" not in line:
            continue
        columns = [column.strip() for column in line.strip("|").split("|")]
        filename = re.fullmatch(r"`([^`]+\.yml)` / .+", columns[0]).group(1)
        documented[filename] = {
            name.strip() for name in columns[7].split(";") if name.strip() not in {"—", "none"}
        }

    for path in sorted((repository_root / ".github/workflows").glob("*.yml")):
        workflow = yaml.safe_load(path.read_text(encoding="utf-8"))
        actual = {job["name"] for job in workflow["jobs"].values()}
        assert documented[path.name] == actual


def test_readme_requires_ready_human_authorization_and_receipt(repository_root):
    readme = " ".join(_text(repository_root, "README.md").split())
    assert "promoted and contracted" in readme
    assert "human-approved `ready`" in readme
    assert "valid ready receipt" in readme


def test_plan_classifies_closed_as_intake_terminal(repository_root):
    plan = " ".join(
        _text(repository_root, "docs/engineering-governance-v2-implementation-plan.md").split()
    )
    assert "Intake `state:new|triaged|investigating|closed`" in plan
    assert "Candidate `state:draft|gate-failed|gate-passed|promoted`" in plan


def test_bootstrap_documents_project_customization_and_boundaries(repository_root):
    bootstrap = _text(repository_root, "BOOTSTRAP.md")
    for required in (
        "trusted_issue_authors",
        "trusted_developers",
        "trusted_reviewers",
        "trusted_milestone_acceptors",
        "required_milestone_checks",
        "allowed_verification_commands",
        "CODEOWNERS",
        "repository-specific URL",
    ):
        assert required in bootstrap
    assert "does not validate whether CODEOWNERS contains a real owner" in bootstrap
    assert "Context7" in bootstrap and "LSP" in bootstrap
    assert "separate confirmation" in bootstrap


def test_public_issue_form_config_has_no_template_repository_link(repository_root):
    config = yaml.safe_load(_text(repository_root, ".github/ISSUE_TEMPLATE/config.yml"))
    assert config == {"blank_issues_enabled": False}


def test_root_docs_describe_v2_without_obsolete_cloud_agent_claims(repository_root):
    combined = "\n".join(
        _text(repository_root, path)
        for path in ("README.md", "CONTRIBUTING.md", "BOOTSTRAP.md")
    )
    assert "GitHub control plane" in combined
    assert "local execution plane" in combined
    assert "Engineering Issue" in combined
    assert "AI_API_KEY" not in combined
    assert "creates branches" not in combined
    assert "future Issue gate" not in combined


def test_required_file_lists_include_v2_agent_and_doc_assets(repository_root):
    manifests = (
        ".github/workflows/00-baseline-check.yml",
        ".github/workflows/10-pr-ai-review.yml",
        ".github/workflows/20-ci-build-test.yml",
    )
    required = (
        "CLAUDE.md",
        ".agent/engineering.md",
        ".agent/verification.md",
        ".agents/skills/issue-investigate/SKILL.md",
        ".agents/skills/issue-author/SKILL.md",
        ".agents/skills/issue-review/SKILL.md",
        ".agents/skills/issue-promote/SKILL.md",
        "tests/governance/test_bootstrap_docs.py",
        ".github/scripts/validate-rollout",
        "tests/governance/test_validate_rollout.py",
    )
    for manifest in manifests:
        text = _text(repository_root, manifest)
        for path in required:
            assert path in text


def test_bootstrap_documents_read_only_rollout_validation(repository_root):
    combined = _text(repository_root, "BOOTSTRAP.md") + _text(repository_root, ".github/SETTINGS.md")
    assert ".github/scripts/validate-rollout" in combined
    assert "repository-scoped" in combined
    assert "canonical JSON" in combined
    assert "Exit `0`" in combined and "exit `5`" in combined
    assert "cannot create labels" in combined


def test_generated_projects_must_replace_template_source_trust_bindings(repository_root):
    settings = " ".join(_text(repository_root, ".github/SETTINGS.md").split())
    assert "janssenkm" in settings and "chacha20" in settings
    assert "must replace all four trusted actor lists" in settings
    assert "must not inherit" in settings
    assert "TRUST-LISTS-EMPTY" in settings


def test_settings_is_human_runbook_not_yaml(repository_root):
    settings = repository_root / ".github/SETTINGS.md"
    assert settings.is_file()
    assert not (repository_root / ".github/settings.yml").exists()
    policy = yaml.safe_load(_text(repository_root, ".github/project-policy.yml"))
    assert policy["rollout_mode"] == "dry-run"
    for capability in (
        "trusted_issue_authors",
        "trusted_developers",
        "trusted_reviewers",
        "trusted_milestone_acceptors",
    ):
        assert policy[capability]
    assert set(policy["trusted_issue_authors"]).isdisjoint(policy["trusted_reviewers"])
    assert policy["required_milestone_checks"] == [
        "Configuration Validation",
        "Security Scanning",
    ]

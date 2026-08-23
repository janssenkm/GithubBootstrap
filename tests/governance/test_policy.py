from __future__ import annotations

from pathlib import Path

import json

import pytest

from github_governance.errors import PolicyError
from github_governance.__main__ import main
from github_governance.policy import authorized, load_policy


FIXTURES = Path(__file__).parent / "fixtures" / "policy"


def test_valid_policy_normalizes_logins(repository_root):
    policy = load_policy(FIXTURES / "valid.yml", repository_root)
    assert policy["trusted_issue_authors"] == ["author"]
    assert authorized(policy, "trusted_issue_authors", "AUTHOR")
    assert not authorized(policy, "trusted_developers", "author")
    assert authorized(policy, "trusted_milestone_acceptors", "acceptor")
    assert policy["required_milestone_checks"] == ["Quality Gate"]


@pytest.mark.parametrize(
    "name,finding",
    [("duplicate-key.yml", "POLICY-DUPLICATE-KEY"), ("alias.yml", "POLICY-YAML-INDIRECTION")],
)
def test_duplicate_keys_and_aliases_fail_closed(repository_root, name, finding):
    with pytest.raises(PolicyError) as captured:
        load_policy(FIXTURES / name, repository_root)
    assert captured.value.finding.id == finding


@pytest.mark.parametrize(
    "raw,finding",
    [
        ("trusted_issue_authors: [User, user]", "POLICY-DUPLICATE-LOGIN"),
        ("trusted_issue_authors: [' user']", "POLICY-SCHEMA"),
        ("trusted_issue_authors: [user--name]", "POLICY-SCHEMA"),
        ("unknown: true", "POLICY-SCHEMA"),
    ],
)
def test_unknown_fields_invalid_logins_and_normalized_duplicates_fail(tmp_path, repository_root, raw, finding):
    path = tmp_path / "policy.yml"
    path.write_text(
        "schema_version: 1\nrollout_mode: dry-run\ntrusted_developers: []\ntrusted_reviewers: []\ntrusted_milestone_acceptors: []\nrequired_milestone_checks: []\nallowed_verification_commands: []\n" + raw + "\n",
        encoding="utf-8",
    )
    with pytest.raises(PolicyError) as captured:
        load_policy(path, repository_root)
    assert captured.value.finding.id == finding


def test_empty_capabilities_are_valid_but_authorize_nothing(repository_root):
    policy = load_policy(repository_root / ".github/project-policy.yml", repository_root)
    assert not authorized(policy, "trusted_reviewers", "someone")
    assert not authorized(policy, "trusted_milestone_acceptors", "someone")


def test_invalid_policy_cli_path_uses_stable_exit_5(repository_root, capsys):
    code = main([
        "gate", "--body-file", str(repository_root / "tests/governance/fixtures/contracts/valid-candidate.md"),
        "--policy", str(FIXTURES / "duplicate-key.yml"), "--repository-root", str(repository_root), "--dry-run",
    ])
    assert code == 5
    assert "POLICY-DUPLICATE-KEY" in capsys.readouterr().err


@pytest.mark.parametrize("name", ["complex-sequence-key.yml", "complex-mapping-key.yml", "complex-inline-key.yml"])
def test_complex_mapping_keys_are_stable_policy_errors(repository_root, name):
    with pytest.raises(PolicyError) as captured:
        load_policy(FIXTURES / name, repository_root)
    assert captured.value.finding.id == "POLICY-YAML-KEY"
    assert captured.value.code == 5


@pytest.mark.parametrize("name", ["complex-sequence-key.yml", "complex-mapping-key.yml", "complex-inline-key.yml"])
def test_complex_mapping_key_cli_has_structured_error_without_traceback(repository_root, capsys, name):
    code = main([
        "gate", "--body-file", str(repository_root / "tests/governance/fixtures/contracts/valid-candidate.md"),
        "--policy", str(FIXTURES / name), "--repository-root", str(repository_root), "--dry-run",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 5
    assert captured.out == ""
    assert payload["finding_ids"] == ["POLICY-YAML-KEY"]
    assert "Traceback" not in captured.err


def test_non_mapping_policy_root_is_stable_policy_error(repository_root, tmp_path):
    path = tmp_path / "policy.yml"
    path.write_text("- list-root\n", encoding="utf-8")
    with pytest.raises(PolicyError) as captured:
        load_policy(path, repository_root)
    assert captured.value.finding.id == "POLICY-SHAPE"


def test_inline_yaml_merge_key_fails_before_mapping_flatten(repository_root):
    with pytest.raises(PolicyError) as captured:
        load_policy(FIXTURES / "merge-inline.yml", repository_root)
    assert captured.value.finding.id == "POLICY-YAML-MERGE"
    assert captured.value.code == 5


def test_inline_yaml_merge_key_cli_is_structured_without_traceback(repository_root, capsys):
    code = main([
        "gate", "--body-file", str(repository_root / "tests/governance/fixtures/contracts/valid-candidate.md"),
        "--policy", str(FIXTURES / "merge-inline.yml"), "--repository-root", str(repository_root), "--dry-run",
    ])
    captured = capsys.readouterr()
    payload = json.loads(captured.err)
    assert code == 5
    assert captured.out == ""
    assert payload["finding_ids"] == ["POLICY-YAML-MERGE"]
    assert "Traceback" not in captured.err

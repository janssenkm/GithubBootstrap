from __future__ import annotations

import copy

import pytest
import yaml

from github_governance.canonical import contract_hash, subject_digest
from github_governance.errors import GovernanceError
from github_governance.events import authorize_issue_comment, parse_command
from github_governance.state import build_ready_target, ready_findings


WORKFLOW = ".github/workflows/02-engineering-governance.yml"


def _contracted(valid_contract):
    value = copy.deepcopy(valid_contract)
    digest = subject_digest(value)
    value["status"] = "contracted"
    value["provenance"]["promoted_by"] = "approver"
    value["provenance"]["sources"].append(
        {"repository": "owner/repo", "number": 7, "role": "candidate"}
    )
    value["review"].update(
        reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest
    )
    value["approval"].update(
        decision="approved",
        actor="approver",
        decided_at="2026-08-23T00:30:00Z",
        evidence_ref="E-01",
        subject_revision=1,
        subject_digest=digest,
    )
    value["freeze"].update(frozen_at="2026-08-23T00:45:00Z", frozen_by="approver")
    value["freeze"]["contract_hash"] = contract_hash(value)
    return value


def _event(contract, actor="developer"):
    return {
        "action": "created",
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": actor},
        "issue": {
            "id": 202,
            "number": 8,
            "body": "not logged",
            "user": {"login": "github-actions[bot]", "type": "Bot"},
            "labels": [{"name": "type:engineering"}, {"name": "state:contracted"}],
        },
        "comment": {
            "id": 404,
            "body": "/ready-for-dev",
            "created_at": "2026-08-23T02:00:00Z",
            "updated_at": "2026-08-23T02:00:00Z",
            "user": {"login": actor, "type": "User"},
        },
    }


def test_ready_command_is_exact_and_requires_trusted_developer(valid_contract, policy):
    contract = _contracted(valid_contract)
    assert parse_command(" /ready-for-dev\n").action == "ready"
    for body in ("/ready-for-dev now", "> /ready-for-dev", "/Ready-for-dev", "／ready-for-dev"):
        assert parse_command(body) is None
    command = authorize_issue_comment(_event(contract), contract, policy)
    assert command.action == "ready"
    assert command.actor == "developer"
    replay_event = _event(contract)
    replay_event["issue"]["labels"][1]["name"] = "state:ready"
    assert authorize_issue_comment(replay_event, contract, policy).action == "ready"
    with pytest.raises(GovernanceError) as error:
        authorize_issue_comment(_event(contract, "approver"), contract, policy)
    assert error.value.finding.id == "EVENT-UNAUTHORIZED"


def test_ready_changes_only_lifecycle_freeze_and_full_hash(valid_contract):
    before = _contracted(valid_contract)
    target = build_ready_target(before, actor="developer", frozen_at="2026-08-23T02:00:00Z")
    assert target["status"] == "ready"
    assert target["issue_revision"] == before["issue_revision"]
    assert subject_digest(target) == subject_digest(before)
    assert target["review"] == before["review"]
    assert target["approval"] == before["approval"]
    assert target["freeze"]["frozen_by"] == "developer"
    assert target["freeze"]["contract_hash"] == contract_hash(target)
    assert target["freeze"]["contract_hash"] != before["freeze"]["contract_hash"]


@pytest.mark.parametrize(
    ("mutate", "finding"),
    [
        (lambda value: value["freeze"].update(contract_hash="sha256:" + "0" * 64), "STALE-CONTRACT-HASH"),
        (lambda value: value["review"].update(subject_revision=2), "STALE-REVIEW"),
        (lambda value: value["approval"].update(subject_digest="sha256:" + "0" * 64), "STALE-APPROVAL"),
        (lambda value: value.update(base_commit="f" * 40), "STALE-BASE-COMMIT"),
        (lambda value: value.update(dependency_locks=[{"name": "missing", "version": "1", "source": "missing.txt"}]), "STALE-DEPENDENCY-LOCK"),
        (lambda value: value.update(document_locks=[{"provider": "official-docs", "locator": "missing.md", "version_or_date": value["base_commit"], "content_sha256": "0" * 64}]), "STALE-DOCUMENT-LOCK"),
    ],
)
def test_ready_drift_has_stable_stale_finding(valid_contract, policy, repository_root, mutate, finding):
    contract = _contracted(valid_contract)
    mutate(contract)
    assert finding in {item.id for item in ready_findings(contract, policy, repository_root)}


def test_governance_workflow_keeps_per_entity_lock_and_opened_bot_read_only(repository_root):
    workflow = yaml.safe_load((repository_root / WORKFLOW).read_text())
    assert "issue.number" in workflow["concurrency"]["group"]
    assert workflow["permissions"] == {}
    read_job = workflow["jobs"]["read-and-revalidate"]
    assert read_job["permissions"] == {"actions": "read", "contents": "read", "issues": "read"}
    source = (repository_root / WORKFLOW).read_text()
    assert "github.event_name != 'issues' || github.event.issue.user.type != 'Bot'" in source
    assert "contents: write" not in source

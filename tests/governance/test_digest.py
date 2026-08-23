from __future__ import annotations

import copy
import json

import pytest

from conftest import render_body
from github_governance.canonical import contract_hash, normative_projection, subject_digest
from github_governance.contract import extract_contract
from github_governance.errors import GovernanceError
from github_governance.state import require_revision_change


def test_object_order_whitespace_crlf_and_narrative_do_not_change_digests(valid_contract):
    reversed_contract = dict(reversed(list(valid_contract.items())))
    bodies = [
        render_body(valid_contract),
        render_body(reversed_contract, before="不同 narrative\r\n", after="\r\nchanged"),
    ]
    parsed = [extract_contract(body.replace(b"\n", b"\r\n") if index else body).contract for index, body in enumerate(bodies)]
    assert subject_digest(parsed[0]) == subject_digest(parsed[1])
    assert contract_hash(parsed[0]) == contract_hash(parsed[1])


@pytest.mark.parametrize("top_level", ["status", "provenance", "review", "approval", "freeze"])
def test_lifecycle_and_attestation_members_do_not_change_subject(valid_contract, top_level):
    changed = copy.deepcopy(valid_contract)
    if top_level == "status":
        changed["status"] = "contracted"
    elif top_level == "provenance":
        changed["provenance"]["promoted_by"] = "promoter"
    elif top_level == "review":
        changed["review"]["reviewed_by"] = "reviewer"
    elif top_level == "approval":
        changed["approval"]["actor"] = "approver"
    else:
        changed["freeze"]["frozen_by"] = "promoter"
    assert subject_digest(changed) == subject_digest(valid_contract)
    assert contract_hash(changed) != contract_hash(valid_contract)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(goal="Changed goal"),
        lambda value: value["claims"][0].update(statement="Changed evidence-backed fact"),
        lambda value: value["evidence"][0].update(summary="Changed nested evidence"),
        lambda value: value["requirements"][0]["acceptance_criteria"].reverse(),
    ],
)
def test_every_normative_change_changes_subject(valid_contract, mutation):
    changed = copy.deepcopy(valid_contract)
    mutation(changed)
    changed["issue_revision"] += 1
    assert subject_digest(changed) != subject_digest(valid_contract)
    assert contract_hash(changed) != contract_hash(valid_contract)


def test_full_hash_excludes_only_itself_and_requires_lowercase(valid_contract):
    valid_contract["freeze"].update(frozen_at="2026-08-22T00:00:00Z", frozen_by="author")
    computed = contract_hash(valid_contract)
    valid_contract["freeze"]["contract_hash"] = computed
    assert contract_hash(valid_contract) == computed
    assert computed.startswith("sha256:") and computed == computed.lower()


def test_revision_changes_only_with_normative_subject(valid_contract):
    lifecycle = copy.deepcopy(valid_contract)
    lifecycle["status"] = "contracted"
    require_revision_change(valid_contract, lifecycle)
    normative = copy.deepcopy(valid_contract)
    normative["goal"] = "Changed goal"
    with pytest.raises(GovernanceError, match="issue_revision must be 2"):
        require_revision_change(valid_contract, normative)
    normative["issue_revision"] = 2
    require_revision_change(valid_contract, normative)


def test_revision_only_increment_is_rejected(valid_contract):
    changed = copy.deepcopy(valid_contract)
    changed["issue_revision"] = 2
    with pytest.raises(GovernanceError) as captured:
        require_revision_change(valid_contract, changed)
    assert captured.value.finding.id == "REVISION-MISMATCH"
    assert subject_digest(changed) != subject_digest(valid_contract)


def test_projection_is_an_exact_top_level_whitelist(valid_contract):
    projection = normative_projection(valid_contract)
    assert "kind" not in projection and "freeze" not in projection
    assert projection["evidence"] == valid_contract["evidence"]

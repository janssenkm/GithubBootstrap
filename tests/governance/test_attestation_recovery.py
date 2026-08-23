from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest

from github_governance.attestations import (
    COMPLETED,
    INTENT,
    SourceComment,
    build_operation,
    build_target,
    classify_recovery,
    compute_operation_id,
    parse_receipt,
    render_receipt,
    validate_receipt_chain,
    _verify_run,
)
from github_governance.canonical import contract_hash, sha256_tagged, subject_digest
from github_governance.errors import GovernanceError
from github_governance.events import AuthorizedCommand


def _review_command(contract):
    digest = subject_digest(contract)
    review = copy.deepcopy(contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    return AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, "/review-contract", "2026-08-23T01:00:00Z")


def _operation(contract):
    command = _review_command(contract)
    source = SourceComment(303, "/review-contract", "reviewer", "User", "2026-08-23T01:00:00Z", "2026-08-23T01:00:00Z")
    target = build_target(contract, command)
    return build_operation(55, 101, "owner/repo", 7, contract, target, command, source, 88, "https://example.invalid/runs/88")


def test_operation_id_binds_every_security_input(valid_contract):
    operation = _operation(valid_contract)
    changed = copy.deepcopy(operation)
    changed["actor"] = "other"
    assert operation["operation_id"] != build_operation(
        55,
        101,
        "owner/repo",
        7,
        valid_contract,
        build_target(valid_contract, _review_command(valid_contract)),
        _review_command(valid_contract),
        SourceComment(304, "/review-contract", "reviewer", "User", "2026-08-23T01:00:00Z", "2026-08-23T01:00:00Z"),
        88,
        "https://example.invalid/runs/88",
    )["operation_id"]
    assert operation["expected_before_hash"] == contract_hash(valid_contract)
    assert operation["target_hash"] == contract_hash(build_target(valid_contract, _review_command(valid_contract)))


def test_receipts_are_unique_bot_verified_and_round_trip(valid_contract):
    operation = _operation(valid_contract)
    intent = {**operation, "phase": INTENT}
    rendered = render_receipt(intent)
    assert parse_receipt(rendered) == intent
    completed = {
        "version": 1,
        "phase": COMPLETED,
        "operation_id": operation["operation_id"],
        "intent_comment_id": 901,
        "target_hash": operation["target_hash"],
        "result": "applied",
        "run_id": operation["run_id"],
        "run_url": operation["run_url"],
    }
    comments = [
        {"id": 901, "body": rendered, "user": {"login": "github-actions[bot]", "type": "Bot"}},
        {"id": 902, "body": render_receipt(completed), "user": {"login": "github-actions[bot]", "type": "Bot"}},
    ]
    assert validate_receipt_chain(comments, operation)[1]["id"] == 902
    comments.append(copy.deepcopy(comments[0]))
    comments[-1]["id"] = 903
    with pytest.raises(GovernanceError) as error:
        validate_receipt_chain(comments, operation)
    assert error.value.finding.id == "ATTESTATION-RECEIPT-CONFLICT"


@pytest.mark.parametrize(
    ("intent", "current", "completed", "expected"),
    [
        (False, "before", False, "start"),
        (False, "before", True, "conflict"),
        (True, "before", False, "write-target"),
        (True, "before", True, "conflict"),
        (True, "target", False, "write-completed"),
        (True, "target", True, "success"),
        (False, "target", False, "conflict"),
        (False, "target", True, "conflict"),
        (True, "unexpected", False, "conflict"),
    ],
)
def test_exhaustive_recovery_rows(intent, current, completed, expected):
    assert classify_recovery(intent, current, completed) == expected


def test_review_target_is_deterministic_and_keeps_subject(valid_contract):
    command = _review_command(valid_contract)
    target = build_target(valid_contract, command)
    assert target["review"]["result"] == "pass"
    assert target["review"]["reviewed_by"] == "reviewer"
    assert subject_digest(target) == subject_digest(valid_contract)
    stale = copy.deepcopy(command)
    object.__setattr__(stale, "review_block_digest", "sha256:" + "0" * 64)
    with pytest.raises(GovernanceError) as error:
        build_target(valid_contract, stale)
    assert error.value.finding.id == "ATTESTATION-REVIEW-DIGEST"


@pytest.mark.parametrize("mutation", ["extra", "missing", "wrong-operation-id"])
def test_intent_exact_keyset_and_recomputed_operation_id(valid_contract, mutation):
    operation = _operation(valid_contract)
    intent = {**operation, "phase": INTENT}
    if mutation == "extra":
        intent["unexpected"] = "field"
    elif mutation == "missing":
        del intent["expected_before_hash"]
    else:
        intent["operation_id"] = "0" * 64
    with pytest.raises(GovernanceError) as error:
        render_receipt(intent)
    assert error.value.finding.id in {"ATTESTATION-RECEIPT-SHAPE", "ATTESTATION-OPERATION-ID"}


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("expected_before_hash", "sha256:" + "1" * 64),
        ("target_hash", "sha256:" + "2" * 64),
        ("repository_id", 999),
        ("issue_id", 999),
        ("revision", 9),
    ],
)
def test_chain_rejects_recomputed_but_wrong_intent_inputs(valid_contract, field, value):
    operation = _operation(valid_contract)
    forged = {**operation, "phase": INTENT, field: value}
    forged["operation_id"] = compute_operation_id(forged)
    comments = [{"id": 901, "body": render_receipt(forged), "user": {"login": "github-actions[bot]", "type": "Bot"}}]
    with pytest.raises(GovernanceError) as error:
        validate_receipt_chain(comments, operation)
    assert error.value.finding.id == "ATTESTATION-RECEIPT-CONFLICT"


def test_completion_exactly_binds_intent_and_order(valid_contract):
    operation = _operation(valid_contract)
    intent = {**operation, "phase": INTENT}
    completed = {
        "version": 1,
        "phase": COMPLETED,
        "operation_id": operation["operation_id"],
        "intent_comment_id": 901,
        "target_hash": operation["target_hash"],
        "result": "applied",
        "run_id": operation["run_id"],
        "run_url": operation["run_url"],
    }
    comments = [
        {"id": 901, "body": render_receipt(intent), "user": {"login": "github-actions[bot]", "type": "Bot"}},
        {"id": 900, "body": render_receipt(completed), "user": {"login": "github-actions[bot]", "type": "Bot"}},
    ]
    with pytest.raises(GovernanceError) as error:
        validate_receipt_chain(comments, operation)
    assert error.value.finding.id == "ATTESTATION-RECEIPT-ORDER"
    comments[1]["id"] = 902
    broken = parse_receipt(comments[1]["body"])
    broken["run_id"] = 999
    comments[1]["body"] = render_receipt(broken)
    with pytest.raises(GovernanceError) as error:
        validate_receipt_chain(comments, operation)
    assert error.value.finding.id == "ATTESTATION-COMPLETED-MISMATCH"


def test_completion_fixture_rejects_missing_extra_and_conflicting_fields(valid_contract):
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/events/attestation-attacks.json").read_text(encoding="utf-8")
    )
    operation = _operation(valid_contract)
    intent = {**operation, "phase": INTENT}
    base = {
        "version": 1,
        "phase": COMPLETED,
        "operation_id": operation["operation_id"],
        "intent_comment_id": 901,
        "target_hash": operation["target_hash"],
        "result": "applied",
        "run_id": operation["run_id"],
        "run_url": operation["run_url"],
    }
    for mutation in fixture["completion_mutations"]:
        completed = copy.deepcopy(base)
        for key, value in mutation.items():
            if key not in {"name", "remove"}:
                completed[key] = value
        if "remove" in mutation:
            del completed[mutation["remove"]]
        with pytest.raises(GovernanceError):
            body = render_receipt(completed)
            validate_receipt_chain(
                [
                    {"id": 901, "body": render_receipt(intent), "user": {"login": "github-actions[bot]", "type": "Bot"}},
                    {"id": 902, "body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}},
                ],
                operation,
            )


def test_duplicate_json_key_and_human_pasted_exact_receipt_fail_closed(valid_contract):
    operation = _operation(valid_contract)
    intent = {**operation, "phase": INTENT}
    canonical = render_receipt(intent)
    duplicate = canonical.replace('{"action"', '{"action":"review","action"', 1)
    with pytest.raises(GovernanceError) as error:
        parse_receipt(duplicate)
    assert error.value.finding.id == "ATTESTATION-RECEIPT-SHAPE"
    with pytest.raises(GovernanceError) as error:
        validate_receipt_chain(
            [{"id": 901, "body": canonical, "user": {"login": "attacker", "type": "User"}}],
            operation,
        )
    assert error.value.finding.id == "ATTESTATION-RECEIPT-ACTOR"


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        ({"repository": {"full_name": "other/repo"}}, "ATTESTATION-RUN-REPOSITORY"),
        ({"path": ".github/workflows/other.yml"}, "ATTESTATION-RUN-WORKFLOW"),
        ({"head_sha": "f" * 40}, "ATTESTATION-RUN-HEAD"),
        ({"actor": {"login": "other"}}, "ATTESTATION-RUN-ACTOR"),
        ({"triggering_actor": {"login": "other"}}, "ATTESTATION-RUN-ACTOR"),
        ({"event": "pull_request"}, "ATTESTATION-RUN-EVENT"),
        ({"html_url": "https://example.invalid/forged"}, "ATTESTATION-RUN-MISMATCH"),
        ({"id": 999}, "ATTESTATION-RUN-MISMATCH"),
        ({"conclusion": None, "status": "completed"}, "ATTESTATION-RUN-INCOMPLETE"),
        ({"conclusion": "failure", "status": "completed"}, "ATTESTATION-RUN-FAILED"),
    ],
)
def test_workflow_run_is_bound_to_all_trusted_context(valid_contract, mutation, finding):
    receipt = {**_operation(valid_contract), "phase": INTENT}
    run = {
        "id": receipt["run_id"],
        "html_url": receipt["run_url"],
        "event": receipt["event"],
        "status": "completed",
        "conclusion": "success",
        "path": receipt["workflow_path"],
        "head_sha": receipt["head_sha"],
        "actor": {"login": receipt["actor"]},
        "triggering_actor": {"login": receipt["actor"]},
        "repository": {"full_name": receipt["repository"]},
    }
    run.update(mutation)

    class API:
        def get_workflow_run(self, identifier):
            return run

    with pytest.raises(GovernanceError) as error:
        _verify_run(API(), receipt, current_run_id=None)
    assert error.value.finding.id == finding


def test_only_explicit_current_run_may_be_in_progress(valid_contract):
    receipt = {**_operation(valid_contract), "phase": INTENT}
    run = {
        "id": receipt["run_id"],
        "html_url": receipt["run_url"],
        "event": receipt["event"],
        "status": "in_progress",
        "conclusion": None,
        "path": receipt["workflow_path"],
        "head_sha": receipt["head_sha"],
        "actor": {"login": receipt["actor"]},
        "triggering_actor": {"login": receipt["actor"]},
        "repository": {"full_name": receipt["repository"]},
    }

    class API:
        def get_workflow_run(self, identifier):
            return run

    _verify_run(API(), receipt, current_run_id=receipt["run_id"])
    with pytest.raises(GovernanceError) as error:
        _verify_run(API(), receipt, current_run_id=999)
    assert error.value.finding.id == "ATTESTATION-RUN-INCOMPLETE"

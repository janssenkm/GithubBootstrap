from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from github_governance.attestations import SourceComment, execute_attestation
from github_governance.audit import (
    classify_transition_recovery,
    parse_transition_receipt,
    render_transition_receipt,
    transition_operation_id,
)
from github_governance.canonical import contract_hash, sha256_tagged, subject_digest
from github_governance.contract import extract_contract
from github_governance.errors import GovernanceError
from github_governance.events import AuthorizedCommand, _intake_sources, execute_promotion, execute_ready
from github_governance.state import build_promotion_target

from conftest import render_body


def _operation():
    return {
        "repository_id": 55,
        "issue_id": 101,
        "action": "promote",
        "source_comment_id": 303,
        "source_body_digest": "sha256:" + "1" * 64,
        "actor": "approver",
        "revision": 1,
        "subject_digest": "sha256:" + "2" * 64,
        "review_block_digest": None,
        "expected_before_hash": "sha256:" + "3" * 64,
    }


def test_transition_operation_id_binds_every_plan_input():
    operation = _operation()
    value = transition_operation_id(operation)
    assert len(value) == 64
    for key in operation:
        changed = copy.deepcopy(operation)
        changed[key] = 999 if key.endswith("_id") or key == "revision" else "different"
        if key == "review_block_digest":
            changed[key] = "sha256:" + "4" * 64
        assert transition_operation_id(changed) != value


@pytest.mark.parametrize(
    ("intent", "current", "completed", "expected"),
    [
        (False, "before", False, "start"),
        (True, "before", False, "write-target"),
        (True, "target", False, "write-completed"),
        (True, "target", True, "success"),
        (False, "target", False, "conflict"),
        (False, "before", True, "conflict"),
        (True, "before", True, "conflict"),
        (True, "unexpected", False, "conflict"),
    ],
)
def test_transition_recovery_table(intent, current, completed, expected):
    assert classify_transition_recovery(intent, current, completed) == expected


def test_transition_receipts_are_canonical_and_reject_tampering():
    operation = _operation()
    receipt = {
        "version": 1,
        "phase": "intent",
        "operation_id": transition_operation_id(operation),
        **operation,
        "repository": "owner/repo",
        "issue_number": 7,
        "target_hash": "sha256:" + "4" * 64,
        "run_id": 88,
        "run_url": "https://example.invalid/runs/88",
        "workflow_path": ".github/workflows/03-engineering-promotion.yml",
        "head_sha": "0c934ebff5f442e5619136aaf95a106b7a677acd",
        "event": "issue_comment",
        "baseline_comment_ids": [303],
        "baseline_updated_at": "2026-08-23T01:00:00Z",
    }
    rendered = render_transition_receipt(receipt)
    assert parse_transition_receipt(rendered) == receipt
    with pytest.raises(GovernanceError):
        parse_transition_receipt(rendered.replace('"actor":"approver"', '"actor":"outsider"'))


def test_nonce_collision_is_a_conflict():
    operation = _operation()
    changed = copy.deepcopy(operation)
    changed["subject_digest"] = "sha256:" + "9" * 64
    assert transition_operation_id(operation) != transition_operation_id(changed)


class RepositoryAPI:
    def __init__(self, issue):
        self.issues = {issue["number"]: copy.deepcopy(issue)}
        self.comments = {issue["number"]: []}
        self.next_comment_id = 1000
        self.next_issue_number = 8
        self.mutations = []
        self.fail_create_response_once = False
        self.fail_comment_response_once = False
        self.fail_comment_body_contains_once = None
        self.fail_comment_on_issue = None
        self.fail_update_response_on_issue = None
        self.created_issue_user = {"login": "github-actions[bot]", "type": "Bot"}
        self.created_issue_editor = None
        self.created_issue_updated_at = "2026-08-23T03:00:00Z"
        self.get_issue_hook = None
        self.tamper_recovered_target_updated_at = False
        self.after_last_provenance_link_hook = None
        self.after_promotion_completion_hook = None

    def get_issue(self, number):
        if self.get_issue_hook is not None:
            self.get_issue_hook(self, number)
        if number not in self.issues:
            raise GovernanceError("GITHUB-API-NOT-FOUND", "simulated missing Issue")
        return copy.deepcopy(self.issues[number])

    def list_issues(self, *, state="all"):
        return [copy.deepcopy(value) for value in self.issues.values()]

    def create_issue(self, title, body, labels):
        number = self.next_issue_number
        self.next_issue_number += 1
        issue = {
            "id": 100 + number,
            "number": number,
            "title": title,
            "body": body,
            "created_at": "2026-08-23T03:00:00Z",
            "updated_at": self.created_issue_updated_at,
            "state": "open",
            "repository_url": "https://api.github.com/repos/owner/repo",
            "user": copy.deepcopy(self.created_issue_user),
            "labels": [{"name": value} for value in labels],
        }
        if self.created_issue_editor is not None:
            issue["editor"] = copy.deepcopy(self.created_issue_editor)
        self.issues[number] = issue
        self.comments[number] = []
        self.mutations.append(("create-issue", number))
        if self.fail_create_response_once:
            self.fail_create_response_once = False
            if self.tamper_recovered_target_updated_at:
                self.issues[number]["updated_at"] = "2026-08-23T03:01:00Z"
            raise GovernanceError("GITHUB-API-TRANSIENT", "simulated response loss", code=4)
        return copy.deepcopy(issue)

    def update_issue(self, number, *, body=None, labels=None):
        if body is not None:
            self.issues[number]["body"] = body
        if labels is not None:
            self.issues[number]["labels"] = [{"name": value} for value in labels]
        self.issues[number]["updated_at"] = "2026-08-23T04:00:00Z"
        self.mutations.append(("update-issue", number))
        if self.fail_update_response_on_issue == number:
            self.fail_update_response_on_issue = None
            raise GovernanceError("GITHUB-API-TRANSIENT", "simulated response loss", code=4)
        return copy.deepcopy(self.issues[number])

    def list_comments(self, number):
        return copy.deepcopy(self.comments[number])

    def add_human_comment(self, number, identifier, body, actor, created_at):
        comment = {
            "id": identifier,
            "body": body,
            "created_at": created_at,
            "updated_at": created_at,
            "user": {"login": actor, "type": "User"},
        }
        self.comments[number].append(comment)
        self.issues[number]["updated_at"] = created_at
        self.next_comment_id = max(self.next_comment_id, identifier + 1)
        return copy.deepcopy(comment)

    def create_comment(self, number, body):
        if self.fail_comment_on_issue == number:
            raise GovernanceError("GITHUB-API-FORBIDDEN", "simulated link failure")
        latest_issue_update = max(
            datetime.fromisoformat(issue["updated_at"].replace("Z", "+00:00"))
            for issue in self.issues.values()
        )
        timestamp = (latest_issue_update + timedelta(seconds=1)).isoformat().replace("+00:00", "Z")
        comment = {
            "id": self.next_comment_id,
            "body": body,
            "created_at": timestamp,
            "updated_at": timestamp,
            "user": {"login": "github-actions[bot]", "type": "Bot"},
        }
        self.next_comment_id += 1
        self.comments[number].append(comment)
        self.issues[number]["updated_at"] = timestamp
        self.mutations.append(("comment", number))
        if (
            self.after_last_provenance_link_hook is not None
            and '"phase":"provenance-link"' in body
            and f'"comment_issue_number":{number}' in body
            and f'"intake_issue_number":{number}' in body
        ):
            self.after_last_provenance_link_hook(self)
        if (
            self.after_promotion_completion_hook is not None
            and '"phase":"completed"' in body
            and '"action":"promote"' in body
        ):
            self.after_promotion_completion_hook(self)
        body_match = (
            isinstance(self.fail_comment_body_contains_once, str)
            and self.fail_comment_body_contains_once in body
        )
        if self.fail_comment_response_once or body_match:
            self.fail_comment_response_once = False
            self.fail_comment_body_contains_once = None
            raise GovernanceError("GITHUB-API-TRANSIENT", "simulated response loss", code=4)
        return copy.deepcopy(comment)

    def get_comment(self, identifier):
        return copy.deepcopy(
            next(comment for comments in self.comments.values() for comment in comments if comment["id"] == identifier)
        )

    def get_workflow_run(self, identifier):
        actors = {88: "reviewer", 89: "approver", 90: "approver", 91: "developer"}
        paths = {
            88: ".github/workflows/02-engineering-governance.yml",
            89: ".github/workflows/02-engineering-governance.yml",
            90: ".github/workflows/03-engineering-promotion.yml",
            91: ".github/workflows/02-engineering-governance.yml",
        }
        return {
            "id": identifier,
            "html_url": f"https://example.invalid/runs/{identifier}",
            "event": "issue_comment",
            "status": "completed",
            "conclusion": "success",
            "path": paths[identifier],
            "head_sha": "0c934ebff5f442e5619136aaf95a106b7a677acd",
            "actor": {"login": actors[identifier]},
            "triggering_actor": {"login": actors[identifier]},
            "repository": {"full_name": "owner/repo"},
        }


def _candidate_issue(contract):
    return {
        "id": 101,
        "number": 7,
        "title": "Verified task",
        "body": render_body(contract).decode(),
        "created_at": "2026-08-23T00:00:00Z",
        "updated_at": "2026-08-23T00:00:00Z",
        "state": "open",
        "repository_url": "https://api.github.com/repos/owner/repo",
        "user": {"login": "fixture-author", "type": "User"},
        "labels": [{"name": "type:candidate"}, {"name": "state:gate-passed"}],
    }


def _intake_issue(number):
    return {
        "id": 200 + number,
        "number": number,
        "title": f"Intake {number}",
        "body": f"External Intake {number}",
        "created_at": "2026-08-22T00:00:00Z",
        "updated_at": "2026-08-22T00:00:00Z",
        "state": "open",
        "repository_url": "https://api.github.com/repos/owner/repo",
        "user": {"login": "reporter", "type": "User"},
        "labels": [{"name": "type:intake"}, {"name": "state:triaged"}],
    }


def _attested_candidate(valid_contract, policy, intake_numbers=(6,), intake_repository="owner/repo"):
    contract = copy.deepcopy(valid_contract)
    contract["provenance"]["created_by"] = "fixture-author"
    contract["provenance"]["sources"] = [
        {"repository": intake_repository, "number": number, "role": "intake"}
        for number in intake_numbers
    ]
    contract["evidence"].append(
        {
            "id": "E-02",
            "type": "human-decision",
            "locator": "issue:7#issuecomment-2000",
            "summary": "Approver accepted the current subject.",
            "captured_at": "2026-08-23T01:30:00Z",
            "content_sha256": None,
        }
    )
    contract["approval"]["evidence_ref"] = "E-02"
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(contract)
    review = copy.deepcopy(contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    review_body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    api = RepositoryAPI(_candidate_issue(contract))
    for number in intake_numbers:
        api.issues[number] = _intake_issue(number)
        api.comments[number] = []
    review_source = api.add_human_comment(7, 303, review_body, "reviewer", "2026-08-23T01:00:00Z")
    review_command = AuthorizedCommand(
        "review", 1, digest, sha256_tagged(review), "reviewer", 303, review_body, "2026-08-23T01:00:00Z"
    )
    execute_attestation(
        api, 55, "owner/repo", 7, review_command, SourceComment.from_api(review_source), policy,
        88, "https://example.invalid/runs/88", repository_root="."
    )
    approval_body = f"/approve-contract 1 {digest}"
    approval_source = api.add_human_comment(7, 2000, approval_body, "approver", "2026-08-23T01:30:00Z")
    approval_command = AuthorizedCommand(
        "approve", 1, digest, None, "approver", 2000, approval_body, "2026-08-23T01:30:00Z"
    )
    execute_attestation(
        api, 55, "owner/repo", 7, approval_command, SourceComment.from_api(approval_source), policy,
        89, "https://example.invalid/runs/89", repository_root="."
    )
    return api, digest


def test_paginated_rest_style_candidate_to_contracted_to_ready_is_idempotent(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    promote_source = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    promote = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    promoted = execute_promotion(
        api, 55, "owner/repo", 7, promote, SourceComment.from_api(promote_source), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    assert promoted["target_issue_number"] == 8
    assert extract_contract(api.issues[8]["body"]).contract["status"] == "contracted"
    assert {label["name"] for label in api.issues[7]["labels"]} == {"type:candidate", "state:promoted"}
    assert execute_promotion(
        api, 55, "owner/repo", 7, promote, SourceComment.from_api(promote_source), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )["result"] == "idempotent"
    assert len([mutation for mutation in api.mutations if mutation[0] == "create-issue"]) == 1

    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    ready = AuthorizedCommand(
        "ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"
    )
    result = execute_ready(
        api, 55, "owner/repo", 8, ready, SourceComment.from_api(ready_source), policy,
        91, "https://example.invalid/runs/91", repository_root="."
    )
    assert result["result"] == "applied"
    ready_contract = extract_contract(api.issues[8]["body"]).contract
    assert ready_contract["status"] == "ready"
    assert ready_contract["issue_revision"] == 1
    assert subject_digest(ready_contract) == digest
    assert execute_ready(
        api, 55, "owner/repo", 8, ready, SourceComment.from_api(ready_source), policy,
        91, "https://example.invalid/runs/91", repository_root="."
    )["result"] == "idempotent"
    assert sum("Local execution handoff" in comment["body"] for comment in api.comments[8]) == 1


def test_promotion_recovers_create_and_receipt_response_loss(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    api.fail_comment_response_once = True
    api.fail_create_response_once = True
    result = execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    assert result["target_issue_number"] == 8
    assert len([item for item in api.mutations if item[0] == "create-issue"]) == 1


def test_candidate_finalization_response_loss_reads_back_success(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    api.fail_update_response_on_issue = 7
    result = execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    assert result["target_issue_number"] == 8
    assert {item["name"] for item in api.issues[7]["labels"]} == {"type:candidate", "state:promoted"}


def test_link_failure_keeps_candidate_unpromoted_and_retry_recovers_target(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    api.fail_comment_on_issue = 8
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "GITHUB-API-FORBIDDEN"
    assert {item["name"] for item in api.issues[7]["labels"]} == {"type:candidate", "state:gate-passed"}
    assert len([item for item in api.mutations if item[0] == "create-issue"]) == 1
    api.fail_comment_on_issue = None
    assert execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )["result"] == "recovered"
    assert len([item for item in api.mutations if item[0] == "create-issue"]) == 1


def test_duplicate_promotion_target_and_edited_source_fail_closed(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    api.comments[7][-1]["updated_at"] = "2026-08-23T02:01:00Z"
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "ATTESTATION-SOURCE-CHANGED"
    api.comments[7][-1]["updated_at"] = "2026-08-23T02:00:00Z"
    execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    duplicate = copy.deepcopy(api.issues[8])
    duplicate.update(id=109, number=9)
    api.issues[9] = duplicate
    api.comments[9] = []
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-TARGET-CONFLICT"


def test_missing_actions_read_and_ready_write_response_loss_fail_safely(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    original_run = api.get_workflow_run
    api.get_workflow_run = lambda identifier: (_ for _ in ()).throw(
        GovernanceError("GITHUB-API-FORBIDDEN", "actions: read is unavailable")
    ) if identifier == 90 else original_run(identifier)
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "GITHUB-API-FORBIDDEN"
    assert not [item for item in api.mutations if item[0] == "create-issue"]
    api.get_workflow_run = original_run
    execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    ready = AuthorizedCommand(
        "ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"
    )
    api.fail_update_response_on_issue = 8
    assert execute_ready(
        api, 55, "owner/repo", 8, ready, SourceComment.from_api(ready_source), policy,
        91, "https://example.invalid/runs/91", repository_root="."
    )["result"] == "applied"
    assert extract_contract(api.issues[8]["body"]).contract["status"] == "ready"


@pytest.mark.parametrize("phase", ["completed", "handoff"])
def test_ready_receipt_response_loss_recovers_once(valid_contract, policy, phase):
    api, digest = _attested_candidate(valid_contract, policy)
    promote_source = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    execute_promotion(
        api, 55, "owner/repo", 7,
        AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
        SourceComment.from_api(promote_source), policy, 90,
        "https://example.invalid/runs/90", repository_root="."
    )
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    ready = AuthorizedCommand(
        "ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"
    )
    api.fail_comment_body_contains_once = f'"phase":"{phase}"'
    assert execute_ready(
        api, 55, "owner/repo", 8, ready, SourceComment.from_api(ready_source), policy,
        91, "https://example.invalid/runs/91", repository_root="."
    )["result"] == "applied"
    receipts = [comment["body"] for comment in api.comments[8]]
    assert sum(f'"phase":"{phase}"' in body for body in receipts) == 1


def test_promotion_writes_exact_bidirectional_intake_candidate_engineering_links(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    result = execute_promotion(
        api, 55, "owner/repo", 7,
        AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
        SourceComment.from_api(source_api), policy, 90,
        "https://example.invalid/runs/90", repository_root="."
    )
    expected_prefix = "Promotion provenance: Intake #6 -> Candidate #7 -> Engineering #8.\n\n"
    for number in (6, 7, 8):
        links = [comment for comment in api.comments[number] if comment["body"].startswith(expected_prefix)]
        assert len(links) == 1
        receipt = parse_transition_receipt(links[0]["body"])
        assert receipt == {
            "version": 1,
            "phase": "provenance-link",
            "operation_id": result["operation_id"],
            "action": "promote",
            "repository": "owner/repo",
            "intake_issue_number": 6,
            "intake_issue_id": 206,
            "intake_repository_url": "https://api.github.com/repos/owner/repo",
            "intake_author": "reporter",
            "intake_author_type": "User",
            "intake_title": "Intake 6",
            "intake_created_at": "2026-08-22T00:00:00Z",
            "intake_body_digest": sha256_tagged("External Intake 6"),
            "intake_updated_at": "2026-08-22T00:00:00Z",
            "intake_state": "open",
            "intake_labels": ["state:triaged", "type:intake"],
            "intake_baseline_comment_ids": [],
            "candidate_issue_number": 7,
            "target_issue_number": 8,
            "comment_issue_number": number,
            "target_hash": result["target_hash"],
        }
    intake_link = next(
        item for item in api.comments[6]
        if '"phase":"provenance-link"' in item["body"]
    )
    assert api.issues[6]["updated_at"] == intake_link["updated_at"]
    assert api.issues[6]["updated_at"] != "2026-08-22T00:00:00Z"


def test_all_intakes_are_linked_in_deterministic_order_and_replay_is_idempotent(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy, intake_numbers=(6, 5))
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    first = execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    link_mutations = [number for kind, number in api.mutations if kind == "comment" and number in {5, 6}]
    assert link_mutations == [5, 6]
    assert execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )["result"] == "idempotent"
    for intake in (5, 6):
        for number in (intake, 7, first["target_issue_number"]):
            assert sum(
                f'"intake_issue_number":{intake}' in comment["body"]
                and '"phase":"provenance-link"' in comment["body"]
                for comment in api.comments[number]
            ) == 1


def test_partial_intake_link_failure_never_finalizes_candidate_and_retry_recovers(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy, intake_numbers=(5, 6))
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    api.fail_comment_on_issue = 6
    with pytest.raises(GovernanceError):
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert {item["name"] for item in api.issues[7]["labels"]} == {"type:candidate", "state:gate-passed"}
    api.fail_comment_on_issue = None
    assert execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )["result"] == "recovered"
    assert len([item for item in api.mutations if item[0] == "create-issue"]) == 1
    for intake in (5, 6):
        assert sum('"phase":"provenance-link"' in item["body"] for item in api.comments[intake]) == 1


def test_provenance_link_response_loss_recovers_without_duplicates(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    api.fail_comment_body_contains_once = '"phase":"provenance-link"'
    result = execute_promotion(
        api, 55, "owner/repo", 7,
        AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
        SourceComment.from_api(source_api), policy, 90,
        "https://example.invalid/runs/90", repository_root="."
    )
    assert result["target_issue_number"] == 8
    assert sum('"phase":"provenance-link"' in item["body"] for item in api.comments[6]) == 1


def test_missing_provenance_link_blocks_ready_chain_verification(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    promote_source = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    execute_promotion(
        api, 55, "owner/repo", 7,
        AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
        SourceComment.from_api(promote_source), policy, 90,
        "https://example.invalid/runs/90", repository_root="."
    )
    api.comments[6] = [
        comment for comment in api.comments[6]
        if '"phase":"provenance-link"' not in comment["body"]
    ]
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_ready(
            api, 55, "owner/repo", 8,
            AuthorizedCommand("ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"),
            SourceComment.from_api(ready_source), policy, 91,
            "https://example.invalid/runs/91", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-PROVENANCE-LINK"


def test_external_exact_target_is_rejected_before_candidate_write(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    source = SourceComment.from_api(source_api)
    current = extract_contract(api.issues[7]["body"]).contract
    operation_id = transition_operation_id(
        {
            "repository_id": 55,
            "issue_id": 101,
            "action": "promote",
            "source_comment_id": 3000,
            "source_body_digest": source.body_digest,
            "actor": "approver",
            "revision": 1,
            "subject_digest": digest,
            "review_block_digest": None,
            "expected_before_hash": contract_hash(current),
        }
    )
    api.issues[8] = {
        "id": 108,
        "number": 8,
        "title": "attacker",
        "body": f"<!-- github-governance-promotion-target:v1:{operation_id} -->",
        "created_at": "2026-08-23T02:01:00Z",
        "updated_at": "2026-08-23T02:01:00Z",
        "state": "open",
        "repository_url": "https://api.github.com/repos/owner/repo",
        "user": {"login": "attacker", "type": "User"},
        "labels": [{"name": "type:engineering"}, {"name": "state:contracted"}],
    }
    api.comments[8] = []
    before_comments = copy.deepcopy(api.comments[7])
    before_mutations = copy.deepcopy(api.mutations)
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            source, policy, 90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-TARGET-CREATOR"
    assert api.comments[7] == before_comments
    assert api.mutations == before_mutations
    marker = f"<!-- github-governance-promotion-target:v1:{operation_id} -->"
    api.issues[8]["user"] = {"login": "github-actions[bot]", "type": "Bot"}
    api.issues[8]["body"] = marker + marker
    with pytest.raises(GovernanceError) as duplicate_error:
        execute_promotion(
            api, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            source, policy, 90, "https://example.invalid/runs/90", repository_root="."
        )
    assert duplicate_error.value.finding.id == "PROMOTION-TARGET-MARKER"
    assert api.comments[7] == before_comments
    assert api.mutations == before_mutations


@pytest.mark.parametrize(
    ("tamper", "finding"),
    [
        ("creator", "PROMOTION-TARGET-CREATOR"),
        ("updated-at", "PROMOTION-TARGET-UPDATED"),
        ("editor", "PROMOTION-TARGET-UPDATED"),
    ],
)
def test_created_target_creator_and_initial_updated_at_are_verified(valid_contract, policy, tamper, finding):
    api, digest = _attested_candidate(valid_contract, policy)
    if tamper == "creator":
        api.created_issue_user = {"login": "attacker", "type": "User"}
    elif tamper == "updated-at":
        api.created_issue_updated_at = "2026-08-23T03:01:00Z"
    else:
        api.created_issue_editor = {"login": "attacker", "type": "User"}
        api.created_issue_updated_at = "2026-08-23T03:01:00Z"
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            SourceComment.from_api(source_api), policy, 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == finding
    assert {item["name"] for item in api.issues[7]["labels"]} == {"type:candidate", "state:gate-passed"}


def test_response_loss_recovery_rejects_bot_target_with_changed_updated_at(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    api.fail_create_response_once = True
    api.tamper_recovered_target_updated_at = True
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            SourceComment.from_api(source_api), policy, 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-TARGET-UPDATED"
    assert {item["name"] for item in api.issues[7]["labels"]} == {"type:candidate", "state:gate-passed"}
    assert not [
        item for item in api.comments[7]
        if '"phase":"completed"' in item["body"] and '"action":"promote"' in item["body"]
    ]


def _mutate_linked_intake(remote, attack):
    if attack == "delete":
        del remote.issues[6]
        return
    issue = remote.issues[6]
    if attack == "edit":
        issue["body"] += " attacker edit"
    elif attack == "transfer":
        issue["repository_url"] = "https://api.github.com/repos/other/repo"
    elif attack == "state":
        issue["labels"][1]["name"] = "state:unexpected"
    elif attack == "author":
        issue["user"] = {"login": "replacement", "type": "User"}
    elif attack == "title":
        issue["title"] += " attacker edit"
    elif attack == "created":
        issue["created_at"] = "2026-08-21T00:00:00Z"
    elif attack == "pr":
        issue["pull_request"] = {"url": "https://example.invalid/pr/6"}
    issue["updated_at"] = "2026-08-23T02:59:00Z"


@pytest.mark.parametrize("checkpoint", ["after-links", "after-completion"])
@pytest.mark.parametrize("attack", ["edit", "delete", "transfer", "state", "author", "title", "created", "pr"])
def test_last_link_and_prelabel_intake_attacks_never_finalize_or_refresh_snapshot(
    valid_contract, policy, checkpoint, attack
):
    api, digest = _attested_candidate(valid_contract, policy)
    hook = lambda remote: _mutate_linked_intake(remote, attack)
    if checkpoint == "after-links":
        api.after_last_provenance_link_hook = hook
    else:
        api.after_promotion_completion_hook = hook
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    source = SourceComment.from_api(source_api)
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    with pytest.raises(GovernanceError):
        execute_promotion(
            api, 55, "owner/repo", 7, command, source, policy, 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert "state:promoted" not in {item["name"] for item in api.issues[7]["labels"]}
    completions = [
        item for item in api.comments[7]
        if '"phase":"completed"' in item["body"] and '"action":"promote"' in item["body"]
    ]
    assert len(completions) == (0 if checkpoint == "after-links" else 1)
    api.after_last_provenance_link_hook = None
    api.after_promotion_completion_hook = None
    with pytest.raises(GovernanceError):
        execute_promotion(
            api, 55, "owner/repo", 7, command, source, policy, 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert "state:promoted" not in {item["name"] for item in api.issues[7]["labels"]}


@pytest.mark.parametrize(
    ("attack", "finding"),
    [
        ("edit", "PROMOTION-INTAKE-TOCTOU"),
        ("author", "PROMOTION-INTAKE-TOCTOU"),
        ("state", "PROMOTION-INTAKE-STATE"),
        ("delete", "GITHUB-API-NOT-FOUND"),
        ("transfer", "PROMOTION-INTAKE-REPOSITORY"),
        ("pr", "PROMOTION-INTAKE-PR"),
        ("duplicate", "PROMOTION-INTAKE-DUPLICATE"),
        ("cross-repo", "PROMOTION-INTAKE-REPOSITORY"),
    ],
)
def test_intake_source_attacks_fail_closed_before_candidate_finalization(valid_contract, policy, attack, finding):
    intake_numbers = (6, 6) if attack == "duplicate" else (6,)
    intake_repository = "other/repo" if attack == "cross-repo" else "owner/repo"
    api, digest = _attested_candidate(
        valid_contract, policy, intake_numbers=intake_numbers, intake_repository=intake_repository
    )
    if attack == "delete":
        del api.issues[6]
    elif attack == "transfer":
        api.issues[6]["repository_url"] = "https://api.github.com/repos/other/repo"
    elif attack == "pr":
        api.issues[6]["pull_request"] = {"url": "https://example.invalid/pr/6"}
    elif attack in {"edit", "author", "state"}:
        reads = {6: 0}

        def mutate_source(remote, number):
            if number == 6:
                reads[6] += 1
                if reads[6] == 2:
                    if attack == "edit":
                        remote.issues[6]["body"] += " edited"
                    elif attack == "author":
                        remote.issues[6]["user"] = {"login": "replacement", "type": "User"}
                    else:
                        remote.issues[6]["labels"][1]["name"] = "state:unexpected"
                    remote.issues[6]["updated_at"] = "2026-08-23T02:01:00Z"

        api.get_issue_hook = mutate_source
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            SourceComment.from_api(source_api), policy, 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == finding
    assert "state:promoted" not in {item["name"] for item in api.issues[7]["labels"]}


@pytest.mark.parametrize(
    ("sources", "finding"),
    [
        ([{"repository": "owner/repo", "number": 7, "role": "intake"}], "PROMOTION-INTAKE-CYCLE"),
        ([{"repository": "owner/repo", "number": 6, "role": "related"}], "PROMOTION-INTAKE-PROVENANCE"),
    ],
)
def test_forged_or_cyclic_intake_provenance_fails_closed(valid_contract, sources, finding):
    contract = copy.deepcopy(valid_contract)
    contract["provenance"]["sources"] = sources
    with pytest.raises(GovernanceError) as error:
        _intake_sources(contract, "owner/repo", 7)
    assert error.value.finding.id == finding


def test_second_promote_command_cannot_create_another_target(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    first = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    execute_promotion(
        api, 55, "owner/repo", 7, first, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    second_source = api.add_human_comment(7, 5000, "/promote", "approver", "2026-08-23T02:30:00Z")
    second = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 5000, "/promote", "2026-08-23T02:30:00Z"
    )
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api, 55, "owner/repo", 7, second, SourceComment.from_api(second_source), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-RECOVERY-CONFLICT"
    assert len([item for item in api.mutations if item[0] == "create-issue"]) == 1


def test_normative_candidate_drift_fails_but_narrative_edit_is_safe(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    original = api.issues[7]["body"]
    api.issues[7]["body"] += "\nMaintainer narrative note."
    api.issues[7]["updated_at"] = "2026-08-23T02:05:00Z"
    assert execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )["target_issue_number"] == 8

    api2, digest2 = _attested_candidate(valid_contract, copy.deepcopy(policy))
    source2 = api2.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    contract = extract_contract(api2.issues[7]["body"]).contract
    contract["goal"] += " Unauthorized normative change."
    api2.issues[7]["body"] = render_body(contract).decode()
    with pytest.raises(GovernanceError) as error:
        execute_promotion(
            api2, 55, "owner/repo", 7,
            AuthorizedCommand("promote", 1, digest2, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"),
            SourceComment.from_api(source2), copy.deepcopy(policy), 90,
            "https://example.invalid/runs/90", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-STALE-SUBJECT"
    assert original != api.issues[7]["body"]


def test_forged_promoted_by_without_receipt_chain_cannot_become_ready(valid_contract, policy):
    api, digest = _attested_candidate(valid_contract, policy)
    candidate = extract_contract(api.issues[7]["body"]).contract
    forged = build_promotion_target(
        candidate,
        repository="owner/repo",
        candidate_number=7,
        actor="approver",
        frozen_at="2026-08-23T02:00:00Z",
    )
    api.issues[8] = {
        "id": 108,
        "number": 8,
        "title": "forged",
        "body": render_body(forged).decode(),
        "created_at": "2026-08-23T03:00:00Z",
        "updated_at": "2026-08-23T03:00:00Z",
        "user": {"login": "github-actions[bot]", "type": "Bot"},
        "labels": [{"name": "type:engineering"}, {"name": "state:contracted"}],
    }
    api.comments[8] = []
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_ready(
            api, 55, "owner/repo", 8,
            AuthorizedCommand("ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"),
            SourceComment.from_api(ready_source), policy, 91,
            "https://example.invalid/runs/91", repository_root="."
        )
    assert error.value.finding.id == "PROMOTION-CHAIN-MARKER"


@pytest.mark.parametrize("attack", ["user", "bot", "same-time", "future", "past", "reorder", "mutate", "delete"])
def test_target_comment_timeline_rejects_unbound_or_non_monotonic_history(valid_contract, policy, attack):
    api, digest = _attested_candidate(valid_contract, policy)
    source_api = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    command = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    execute_promotion(
        api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    comments = api.comments[8]
    if attack == "mutate":
        comments[-1]["body"] += "tampered"
    elif attack == "delete":
        del comments[0]
    else:
        last_time = comments[-1]["created_at"]
        timestamp = {
            "same-time": last_time,
            "future": "2099-01-01T00:00:00Z",
            "past": "2026-08-22T00:00:00Z",
            "reorder": "2026-08-23T03:00:00Z",
        }.get(attack, "2026-08-23T03:00:10Z")
        identifier = comments[-1]["id"] - 1 if attack == "reorder" else comments[-1]["id"] + 100
        comments.append({
            "id": identifier,
            "body": "external comment",
            "created_at": timestamp,
            "updated_at": timestamp,
            "user": {
                "login": "github-actions[bot]" if attack == "bot" else "outsider",
                "type": "Bot" if attack == "bot" else "User",
            },
        })
        api.issues[8]["updated_at"] = timestamp
    with pytest.raises(GovernanceError):
        execute_promotion(
            api, 55, "owner/repo", 7, command, SourceComment.from_api(source_api), policy,
            90, "https://example.invalid/runs/90", repository_root="."
        )


def _engineering_ready_context(valid_contract, policy):
    """Promote an attested Candidate and return the Engineering Issue state."""

    api, digest = _attested_candidate(valid_contract, policy)
    promote_source = api.add_human_comment(7, 3000, "/promote", "approver", "2026-08-23T02:00:00Z")
    promote = AuthorizedCommand(
        "promote", 1, digest, None, "approver", 3000, "/promote", "2026-08-23T02:00:00Z"
    )
    execute_promotion(
        api, 55, "owner/repo", 7, promote, SourceComment.from_api(promote_source), policy,
        90, "https://example.invalid/runs/90", repository_root="."
    )
    return api, digest


def test_read_phase_ready_command_on_engineering_issue_verifies_promotion_chain(
    valid_contract, policy, monkeypatch, capsys
):
    import github_governance.events as events_module

    policy["rollout_mode"] = "enforce"
    api, digest = _engineering_ready_context(valid_contract, policy)
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    ready = AuthorizedCommand(
        "ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"
    )
    issue = api.get_issue(8)
    contract = extract_contract(issue["body"]).contract
    monkeypatch.setattr(
        events_module,
        "_workflow_context",
        lambda event: (
            api, policy, 55, "owner/repo", 8, issue, contract,
            (ready, SourceComment.from_api(ready_source)),
        ),
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issue_comment")
    event = {
        "action": "created",
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": "developer"},
        "issue": issue,
        "comment": ready_source,
    }
    assert events_module._read_phase(event) == 0
    output = capsys.readouterr().out
    assert "PASS" in output
    assert "PROMOTION-TARGET-COMMENT" not in output


def test_read_phase_issue_edit_after_ready_fails_closed(
    valid_contract, policy, monkeypatch, capsys
):
    import github_governance.events as events_module

    policy["rollout_mode"] = "enforce"
    api, digest = _engineering_ready_context(valid_contract, policy)
    ready_source = api.add_human_comment(8, 4000, "/ready-for-dev", "developer", "2026-08-23T03:30:00Z")
    ready = AuthorizedCommand(
        "ready", 1, digest, None, "developer", 4000, "/ready-for-dev", "2026-08-23T03:30:00Z"
    )
    execute_ready(
        api, 55, "owner/repo", 8, ready, SourceComment.from_api(ready_source), policy,
        91, "https://example.invalid/runs/91", repository_root="."
    )
    issue = api.get_issue(8)
    contract = extract_contract(issue["body"]).contract
    monkeypatch.setattr(
        events_module,
        "_workflow_context",
        lambda event: (
            api, policy, 55, "owner/repo", 8, issue, contract, None,
        ),
    )
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issues")
    event = {
        "action": "edited",
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": "outsider"},
        "issue": issue,
    }
    assert events_module._read_phase(event) == 0
    output = capsys.readouterr().out
    assert "PROMOTION-TARGET-COMMENT" in output

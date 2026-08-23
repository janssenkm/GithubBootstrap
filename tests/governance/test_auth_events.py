from __future__ import annotations

import copy

import pytest

from github_governance.canonical import sha256_tagged, subject_digest
from github_governance.errors import GovernanceError
from github_governance.events import (
    AuthorizedCommand,
    authorize_dispatch,
    authorize_issue_comment,
    parse_command,
)


def _comment_event(contract, body, actor="reviewer", *, action="created", pr=False):
    issue = {
        "id": 101,
        "number": 7,
        "body": "not logged",
        "user": {"login": contract["provenance"]["created_by"]},
        "labels": [{"name": "type:candidate"}, {"name": "state:draft"}],
    }
    if pr:
        issue["pull_request"] = {"url": "https://example.invalid/pr/7"}
    return {
        "action": action,
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": actor},
        "issue": issue,
        "comment": {
            "id": 303,
            "body": body,
            "created_at": "2026-08-23T01:00:00Z",
            "updated_at": "2026-08-23T01:00:00Z",
            "user": {"login": actor, "type": "User"},
        },
    }


def test_exact_command_grammar(valid_contract):
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(
        reviewed_by="reviewer",
        result="pass",
        subject_revision=1,
        subject_digest=digest,
    )
    review_digest = sha256_tagged(review)
    parsed = parse_command(f"\t/review-contract 1 {digest} {review_digest}\r\n")
    assert parsed.action == "review"
    assert parsed.revision == 1
    assert parsed.subject_digest == digest
    assert parsed.review_block_digest == review_digest
    assert parse_command(f"/approve-contract 1 {digest}").action == "approve"
    assert parse_command("a normal comment") is None


@pytest.mark.parametrize(
    "body",
    [
        "/review-contract 01 sha256:" + "a" * 64 + " sha256:" + "b" * 64,
        "/review-contract 1 sha256:" + "A" * 64 + " sha256:" + "b" * 64,
        "> /approve-contract 1 sha256:" + "a" * 64,
        "```\n/approve-contract 1 sha256:" + "a" * 64 + "\n```",
        "/approve-contract 1 sha256:" + "a" * 64 + " trailing",
        "／approve-contract 1 sha256:" + "a" * 64,
        "/Approve-contract 1 sha256:" + "a" * 64,
        "/approve-contract 1 sha256:" + "a" * 64 + "\nextra",
    ],
)
def test_command_smuggling_is_not_recognized(body):
    assert parse_command(body) is None


def test_review_authorization_uses_api_actor_and_separation(valid_contract, policy):
    valid_contract["provenance"]["created_by"] = "author"
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    event = _comment_event(valid_contract, f"/review-contract 1 {digest} {sha256_tagged(review)}")
    command = authorize_issue_comment(event, valid_contract, policy)
    assert isinstance(command, AuthorizedCommand)
    assert command.actor == "reviewer"
    assert command.source_comment_id == 303

    event["sender"]["login"] = "approver"
    with pytest.raises(GovernanceError, match="actor") as error:
        authorize_issue_comment(event, valid_contract, policy)
    assert error.value.finding.id == "EVENT-ACTOR-MISMATCH"


@pytest.mark.parametrize(
    ("mutation", "finding"),
    [
        (lambda event: event.update(action="edited"), "EVENT-ACTION"),
        (lambda event: event["issue"].update(pull_request={}), "EVENT-PR-COMMENT"),
        (lambda event: event["comment"].update(updated_at="2026-08-23T01:01:00Z"), "EVENT-SOURCE-EDITED"),
        (lambda event: event["comment"]["user"].update(type="Bot"), "EVENT-BOT-COMMAND"),
        (lambda event: event["issue"].update(labels=[{"name": "type:intake"}]), "EVENT-ENTITY"),
    ],
)
def test_event_shape_bypasses_fail_closed(valid_contract, policy, mutation, finding):
    digest = subject_digest(valid_contract)
    event = _comment_event(valid_contract, f"/approve-contract 1 {digest}", actor="approver")
    mutation(event)
    with pytest.raises(GovernanceError) as error:
        authorize_issue_comment(event, valid_contract, policy)
    assert error.value.finding.id == finding


def test_dispatch_requires_same_actor_and_exact_source(valid_contract, policy):
    digest = subject_digest(valid_contract)
    event = {
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": "approver"},
        "inputs": {"operation": "approve", "issue_number": "7", "source_comment_id": "303"},
    }
    comment = _comment_event(valid_contract, f"/approve-contract 1 {digest}", actor="approver")["comment"]
    command = authorize_dispatch(event, comment, valid_contract, policy)
    assert command.action == "approve"
    event["sender"]["login"] = "reviewer"
    with pytest.raises(GovernanceError) as error:
        authorize_dispatch(event, comment, valid_contract, policy)
    assert error.value.finding.id == "EVENT-ACTOR-MISMATCH"


def test_non_command_and_bot_receipt_are_noops(valid_contract, policy):
    event = _comment_event(valid_contract, "discussion only")
    assert authorize_issue_comment(event, valid_contract, policy) is None
    event["comment"]["body"] = '<!-- github-governance-receipt:v1:{"phase":"intent"} -->'
    event["comment"]["user"] = {"login": "github-actions[bot]", "type": "Bot"}
    event["sender"] = {"login": "github-actions[bot]"}
    assert authorize_issue_comment(event, valid_contract, policy) is None

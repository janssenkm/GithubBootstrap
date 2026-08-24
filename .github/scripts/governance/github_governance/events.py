"""Exact GitHub event and human-command authorization primitives."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from dataclasses import dataclass
from typing import Any

from .canonical import contract_hash, subject_digest
from .errors import GovernanceError
from .policy import authorized, normalize_login


_DIGEST = r"sha256:[0-9a-f]{64}"
_REVIEW = re.compile(rf"/review-contract ([1-9][0-9]*) ({_DIGEST}) ({_DIGEST})")
_APPROVE = re.compile(rf"/approve-contract ([1-9][0-9]*) ({_DIGEST})")
_PROMOTE = re.compile(r"/promote")
_READY = re.compile(r"/ready-for-dev")
_ASCII_EDGE_WHITESPACE = " \t\r\n"
_RECEIPT_PREFIX = "<!-- github-governance-receipt:v1:"


@dataclass(frozen=True)
class AuthorizedCommand:
    action: str
    revision: int
    subject_digest: str
    review_block_digest: str | None
    actor: str
    source_comment_id: int
    source_body: str
    source_created_at: str
    event_name: str = "issue_comment"


def _text(value: Any, finding_id: str, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GovernanceError(finding_id, f"{name} is missing or invalid", code=2)
    return value


def _positive_integer(value: Any, finding_id: str, name: str) -> int:
    if isinstance(value, bool):
        raise GovernanceError(finding_id, f"{name} is missing or invalid", code=2)
    try:
        number = int(value)
    except (TypeError, ValueError) as error:
        raise GovernanceError(finding_id, f"{name} is missing or invalid", code=2) from error
    if number < 1 or str(number) != str(value):
        raise GovernanceError(finding_id, f"{name} is missing or invalid", code=2)
    return number


def parse_command(body: Any) -> AuthorizedCommand | None:
    """Parse only exact V1 governance commands.

    The caller fills authenticated source/actor fields after resolving the
    comment through the API. Non-command and malformed command-like text are
    deliberately side-effect-free no-ops.
    """

    if not isinstance(body, str) or "\x00" in body:
        return None
    stripped = body.strip(_ASCII_EDGE_WHITESPACE)
    match = _REVIEW.fullmatch(stripped)
    if match:
        return AuthorizedCommand("review", int(match.group(1)), match.group(2), match.group(3), "", 0, stripped, "")
    match = _APPROVE.fullmatch(stripped)
    if match:
        return AuthorizedCommand("approve", int(match.group(1)), match.group(2), None, "", 0, stripped, "")
    if _PROMOTE.fullmatch(stripped):
        return AuthorizedCommand("promote", 0, "", None, "", 0, stripped, "")
    if _READY.fullmatch(stripped):
        return AuthorizedCommand("ready", 0, "", None, "", 0, stripped, "")
    return None


def _labels(issue: dict[str, Any]) -> set[str]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise GovernanceError("EVENT-ENTITY", "Issue labels are missing or invalid")
    values: set[str] = set()
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else label
        if not isinstance(name, str) or name in values:
            raise GovernanceError("EVENT-ENTITY", "Issue labels are missing, duplicated, or invalid")
        values.add(name)
    return values


def _issue_snapshot(issue: dict[str, Any]) -> dict[str, Any]:
    """Capture every field whose validation authorizes a later label write."""

    from .contract import extract_contract

    identifier = _positive_integer(issue.get("id"), "EVENT-LABEL-SNAPSHOT", "Issue id")
    number = _positive_integer(issue.get("number"), "EVENT-LABEL-SNAPSHOT", "Issue number")
    body = _text(issue.get("body"), "EVENT-LABEL-SNAPSHOT", "Issue body")
    updated_at = _text(issue.get("updated_at"), "EVENT-LABEL-SNAPSHOT", "Issue updated_at")
    labels = tuple(sorted(_labels(issue)))
    contract = extract_contract(body).contract
    return {
        "id": identifier,
        "number": number,
        "body": body,
        "updated_at": updated_at,
        "labels": labels,
        "revision": contract.get("issue_revision"),
        "subject_digest": subject_digest(contract),
        "contract_hash": contract_hash(contract),
    }


def _require_prewrite_snapshot(expected: dict[str, Any], issue: dict[str, Any]) -> None:
    try:
        actual = _issue_snapshot(issue)
    except GovernanceError as error:
        raise GovernanceError("EVENT-LABEL-TOCTOU", "Issue became invalid before the label write", code=3) from error
    if actual != expected:
        raise GovernanceError("EVENT-LABEL-TOCTOU", "Issue changed after Gate validation and before the label write", code=3)


def _require_label_readback(
    expected: dict[str, Any], issue: dict[str, Any], expected_labels: tuple[str, ...]
) -> dict[str, Any]:
    try:
        actual = _issue_snapshot(issue)
    except GovernanceError as error:
        raise GovernanceError("EVENT-LABEL-READBACK", "Issue became invalid after the label write", code=3) from error
    unchanged = ("id", "number", "body", "revision", "subject_digest", "contract_hash")
    if any(actual[key] != expected[key] for key in unchanged) or actual["labels"] != expected_labels:
        raise GovernanceError("EVENT-LABEL-READBACK", "Issue label write did not read back with the validated contract", code=3)
    return actual


def _authorize_source(
    repository: dict[str, Any],
    issue: dict[str, Any],
    sender: dict[str, Any],
    comment: dict[str, Any],
    contract: dict[str, Any],
    policy: dict[str, Any],
    *,
    expected_operation: str | None = None,
    event_name: str = "issue_comment",
) -> AuthorizedCommand | None:
    if "pull_request" in issue:
        raise GovernanceError("EVENT-PR-COMMENT", "governance commands are forbidden on pull requests")
    body = comment.get("body")
    parsed = parse_command(body)
    user = comment.get("user")
    sender_login = sender.get("login") if isinstance(sender, dict) else None
    user_login = user.get("login") if isinstance(user, dict) else None
    user_type = user.get("type") if isinstance(user, dict) else None
    if parsed is None:
        if user_type == "Bot" or (isinstance(body, str) and body.startswith(_RECEIPT_PREFIX)):
            return None
        return None
    labels = _labels(issue)
    entity_labels = labels & {"type:intake", "type:candidate", "type:engineering"}
    state_labels = {label for label in labels if label.startswith("state:")}
    if len(entity_labels) != 1 or len(state_labels) != 1:
        raise GovernanceError("EVENT-ENTITY", "governance commands require exactly one entity and state label")
    entity = next(iter(entity_labels))
    state = next(iter(state_labels))
    if parsed.action in {"review", "approve"} and entity != "type:candidate":
        raise GovernanceError("EVENT-ENTITY", "attestations require a Candidate entity")
    if parsed.action == "promote" and (entity != "type:candidate" or state not in {"state:gate-passed", "state:promoted"}):
        raise GovernanceError("EVENT-PROMOTION-STATE", "promotion requires a gate-passed Candidate", code=3)
    if parsed.action == "ready" and (entity != "type:engineering" or state not in {"state:contracted", "state:ready"}):
        raise GovernanceError("EVENT-READY-STATE", "ready requires a contracted Engineering Issue", code=3)
    if user_type != "User":
        raise GovernanceError("EVENT-BOT-COMMAND", "only a human User comment can authorize an attestation")
    if not isinstance(sender_login, str) or not isinstance(user_login, str):
        raise GovernanceError("EVENT-ACTOR-MISMATCH", "event and comment actors must be present")
    if normalize_login(sender_login) != normalize_login(user_login):
        raise GovernanceError("EVENT-ACTOR-MISMATCH", "event actor differs from the API comment actor")
    if expected_operation is not None and parsed.action != expected_operation:
        raise GovernanceError("EVENT-DISPATCH-OPERATION", "dispatch operation differs from its source command")
    created_at = _text(comment.get("created_at"), "EVENT-SOURCE-TIME", "source created_at")
    updated_at = _text(comment.get("updated_at"), "EVENT-SOURCE-TIME", "source updated_at")
    if created_at != updated_at:
        raise GovernanceError("EVENT-SOURCE-EDITED", "edited comments cannot authorize governance changes")
    comment_id = _positive_integer(comment.get("id"), "EVENT-SOURCE-ID", "source comment id")
    revision = contract.get("issue_revision")
    digest = subject_digest(contract)
    if parsed.action in {"review", "approve"} and (parsed.revision != revision or parsed.subject_digest != digest):
        raise GovernanceError("EVENT-STALE-SUBJECT", "command does not attest the current revision and subject digest")

    author = contract.get("provenance", {}).get("created_by")
    approval_actor = contract.get("approval", {}).get("actor")
    reviewer = contract.get("review", {}).get("reviewed_by")
    actor = normalize_login(user_login)
    if parsed.action == "review":
        if not authorized(policy, "trusted_reviewers", actor):
            raise GovernanceError("EVENT-UNAUTHORIZED", "actor is not authorized for contract review")
        if isinstance(author, str) and actor == normalize_login(author):
            raise GovernanceError("EVENT-REVIEW-SEPARATION", "reviewer must differ from Candidate author")
        if isinstance(approval_actor, str) and actor == normalize_login(approval_actor):
            raise GovernanceError("EVENT-REVIEW-SEPARATION", "reviewer must differ from approval actor")
    elif parsed.action == "approve":
        if not authorized(policy, "trusted_issue_authors", actor):
            raise GovernanceError("EVENT-UNAUTHORIZED", "actor is not authorized for contract approval")
        if isinstance(reviewer, str) and actor == normalize_login(reviewer):
            raise GovernanceError("EVENT-APPROVAL-SEPARATION", "approval actor must differ from reviewer")
    elif parsed.action == "promote":
        if not authorized(policy, "trusted_issue_authors", actor):
            raise GovernanceError("EVENT-UNAUTHORIZED", "actor is not authorized for promotion")
    elif parsed.action == "ready":
        if not authorized(policy, "trusted_developers", actor):
            raise GovernanceError("EVENT-UNAUTHORIZED", "actor is not authorized for readiness")
    else:
        raise GovernanceError("EVENT-COMMAND", "unsupported governance command", code=2)

    repository_id = _positive_integer(repository.get("id"), "EVENT-REPOSITORY", "repository id")
    _positive_integer(issue.get("id"), "EVENT-ISSUE", "Issue id")
    _positive_integer(issue.get("number"), "EVENT-ISSUE", "Issue number")
    if repository_id < 1:  # kept explicit for static readers
        raise GovernanceError("EVENT-REPOSITORY", "repository id is invalid", code=2)
    return AuthorizedCommand(
        parsed.action,
        parsed.revision if parsed.action in {"review", "approve"} else revision,
        parsed.subject_digest if parsed.action in {"review", "approve"} else digest,
        parsed.review_block_digest,
        actor,
        comment_id,
        str(body).strip(_ASCII_EDGE_WHITESPACE),
        created_at,
        event_name,
    )


def authorize_issue_comment(event: dict[str, Any], contract: dict[str, Any], policy: dict[str, Any]) -> AuthorizedCommand | None:
    if event.get("action") != "created":
        raise GovernanceError("EVENT-ACTION", "only newly created comments can authorize governance changes")
    return _authorize_source(
        event.get("repository", {}),
        event.get("issue", {}),
        event.get("sender", {}),
        event.get("comment", {}),
        contract,
        policy,
        event_name="issue_comment",
    )


def authorize_dispatch(
    event: dict[str, Any], source_comment: dict[str, Any], contract: dict[str, Any], policy: dict[str, Any]
) -> AuthorizedCommand:
    inputs = event.get("inputs")
    if not isinstance(inputs, dict) or inputs.get("operation") not in {"review", "approve", "promote", "ready"}:
        raise GovernanceError("EVENT-DISPATCH-OPERATION", "dispatch operation is missing or unsupported", code=2)
    expected_comment = _positive_integer(inputs.get("source_comment_id"), "EVENT-SOURCE-ID", "source comment id")
    if source_comment.get("id") != expected_comment:
        raise GovernanceError("EVENT-SOURCE-ID", "dispatch source comment does not match the requested database ID")
    issue_number = _positive_integer(inputs.get("issue_number"), "EVENT-ISSUE", "Issue number")
    issue_url = source_comment.get("issue_url")
    if issue_url is not None and (not isinstance(issue_url, str) or not issue_url.endswith(f"/issues/{issue_number}")):
        raise GovernanceError("EVENT-DISPATCH-SOURCE", "dispatch source comment belongs to a different Issue")
    transition = inputs["operation"]
    default_labels = (
        [{"name": "type:engineering"}, {"name": "state:contracted"}]
        if transition == "ready"
        else [{"name": "type:candidate"}, {"name": "state:gate-passed" if transition == "promote" else "state:draft"}]
    )
    issue = {
        "id": event.get("issue_id", issue_number),
        "number": issue_number,
        "labels": event.get("issue_labels", default_labels),
    }
    result = _authorize_source(
        event.get("repository", {}),
        issue,
        event.get("sender", {}),
        source_comment,
        contract,
        policy,
        expected_operation=inputs["operation"],
        event_name="workflow_dispatch",
    )
    if result is None:
        raise GovernanceError("EVENT-DISPATCH-SOURCE", "dispatch requires an exact human source command")
    return result


_PROMOTION_WORKFLOW = ".github/workflows/03-engineering-promotion.yml"
_GOVERNANCE_WORKFLOW = ".github/workflows/02-engineering-governance.yml"
_PROMOTION_TARGET = re.compile(r"<!-- github-governance-promotion-target:v1:([0-9a-f]{64}) -->")


def _bot_transition_comment(comment: dict[str, Any], receipt: dict[str, Any]) -> int:
    from .audit import parse_transition_receipt

    user = comment.get("user")
    if not isinstance(user, dict) or user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
        raise GovernanceError("TRANSITION-RECEIPT-ACTOR", "transition receipt is not bot-authored", code=3)
    identifier = comment.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
        raise GovernanceError("TRANSITION-RECEIPT-ID", "transition receipt comment ID is invalid", code=3)
    if parse_transition_receipt(comment.get("body")) != receipt:
        raise GovernanceError("TRANSITION-RECEIPT-READBACK", "transition receipt read-back differs", code=3)
    created_at = comment.get("created_at")
    if not isinstance(created_at, str) or not created_at or comment.get("updated_at") != created_at:
        raise GovernanceError("TRANSITION-RECEIPT-TIME", "transition receipt comment was edited", code=3)
    return identifier


def _transition_records(comments: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    from .audit import parse_transition_receipt

    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or "<!-- github-governance-transition:v1:" not in body:
            continue
        user = comment.get("user")
        is_bot = isinstance(user, dict) and user.get("login") == "github-actions[bot]" and user.get("type") == "Bot"
        try:
            receipt = parse_transition_receipt(body)
        except GovernanceError:
            if is_bot:
                raise
            continue
        if receipt is None:
            if is_bot:
                raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "bot transition marker is malformed", code=3)
            continue
        _bot_transition_comment(comment, receipt)
        records.append((comment, receipt))
    return records


def _source_from_comments(comments: list[dict[str, Any]], command: AuthorizedCommand, expected: Any) -> Any:
    from .attestations import SourceComment

    matches = [item for item in comments if item.get("id") == command.source_comment_id]
    if len(matches) != 1:
        raise GovernanceError("TRANSITION-SOURCE-MISSING", "source command is missing or duplicated", code=3)
    source = SourceComment.from_api(matches[0])
    source.verify_unchanged(expected)
    parsed = parse_command(source.body)
    if parsed is None or parsed.action != command.action or source.body.strip(_ASCII_EDGE_WHITESPACE) != command.source_body:
        raise GovernanceError("TRANSITION-SOURCE-BODY", "source comment is not the authorized exact command", code=3)
    if normalize_login(source.actor) != normalize_login(command.actor):
        raise GovernanceError("TRANSITION-SOURCE-ACTOR", "source actor differs from the event actor", code=3)
    return source


def _transition_operation(
    *,
    repository_id: int,
    issue: dict[str, Any],
    repository: str,
    command: AuthorizedCommand,
    source: Any,
    before: dict[str, Any],
    target: dict[str, Any],
    run_id: int,
    run_url: str,
    workflow_path: str,
    head_sha: str | None,
    comments: list[dict[str, Any]],
) -> dict[str, Any]:
    from .audit import transition_operation_id
    from .canonical import contract_hash

    issue_id = _positive_integer(issue.get("id"), "TRANSITION-ISSUE", "Issue id")
    issue_number = _positive_integer(issue.get("number"), "TRANSITION-ISSUE", "Issue number")
    operation_input = {
        "repository_id": repository_id,
        "issue_id": issue_id,
        "action": command.action,
        "source_comment_id": source.id,
        "source_body_digest": source.body_digest,
        "actor": normalize_login(command.actor),
        "revision": command.revision,
        "subject_digest": command.subject_digest,
        "review_block_digest": None,
        "expected_before_hash": contract_hash(before),
    }
    baseline_ids = [item.get("id") for item in comments]
    if any(not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1 for identifier in baseline_ids) or baseline_ids != sorted(set(baseline_ids)):
        raise GovernanceError("TRANSITION-COMMENT-BASELINE", "transition comment baseline is invalid", code=3)
    result = {
        "version": 1,
        "phase": "intent",
        "operation_id": transition_operation_id(operation_input),
        **operation_input,
        "repository": repository,
        "issue_number": issue_number,
        "target_hash": contract_hash(target),
        "run_id": run_id,
        "run_url": run_url,
        "workflow_path": workflow_path,
        "head_sha": head_sha or before.get("base_commit"),
        "event": command.event_name,
        "baseline_comment_ids": baseline_ids,
        "baseline_updated_at": issue.get("updated_at"),
    }
    # On recovery the canonical intent, not the now-advanced Issue timeline,
    # remains the authority for the frozen pre-operation baseline.
    existing = [
        receipt for _, receipt in _transition_records(comments)
        if receipt.get("phase") == "intent" and receipt.get("operation_id") == result["operation_id"]
    ]
    if len(existing) == 1:
        result["baseline_comment_ids"] = existing[0]["baseline_comment_ids"]
        result["baseline_updated_at"] = existing[0]["baseline_updated_at"]
    return result


def _verify_transition_run(api: Any, intent: dict[str, Any], *, current_run_id: int | None = None) -> None:
    run = api.get_workflow_run(intent["run_id"])
    if run.get("id") != intent["run_id"] or run.get("html_url") != intent["run_url"]:
        raise GovernanceError("TRANSITION-RUN-MISMATCH", "workflow run ID or URL differs from the intent", code=3)
    if run.get("path") != intent["workflow_path"] or run.get("head_sha") != intent["head_sha"]:
        raise GovernanceError("TRANSITION-RUN-WORKFLOW", "workflow path or head SHA differs from the intent", code=3)
    repository = run.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != intent["repository"]:
        raise GovernanceError("TRANSITION-RUN-REPOSITORY", "workflow run repository differs from the intent", code=3)
    for key in ("actor", "triggering_actor"):
        value = run.get(key)
        login = value.get("login") if isinstance(value, dict) else None
        if not isinstance(login, str) or normalize_login(login) != intent["actor"]:
            raise GovernanceError("TRANSITION-RUN-ACTOR", "workflow run actor differs from the intent", code=3)
    if run.get("event") != intent["event"]:
        raise GovernanceError("TRANSITION-RUN-EVENT", "workflow run event differs from the intent", code=3)
    if run.get("status") == "completed" and run.get("conclusion") == "success":
        return
    if current_run_id == intent["run_id"] and run.get("status") in {"queued", "in_progress"} and run.get("conclusion") is None:
        return
    raise GovernanceError("TRANSITION-RUN-INCOMPLETE", "workflow run is not a verified success", code=3)


def _create_transition_receipt(api: Any, issue_number: int, receipt: dict[str, Any]) -> dict[str, Any]:
    from .audit import render_transition_receipt

    body = render_transition_receipt(receipt)
    try:
        created = api.create_comment(issue_number, body)
    except GovernanceError as error:
        if error.code != 4:
            raise
        candidates = [
            item for item, value in _transition_records(api.list_comments(issue_number))
            if value.get("operation_id") == receipt["operation_id"] and value.get("phase") == receipt["phase"]
        ]
        if len(candidates) != 1:
            raise
        created = candidates[0]
    identifier = _bot_transition_comment(created, receipt)
    readback = api.get_comment(identifier)
    _bot_transition_comment(readback, receipt)
    return readback


def _operation_receipts(
    comments: list[dict[str, Any]], intent: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    records = _transition_records(comments)
    conflicts = [
        value for _, value in records
        if value.get("phase") == "intent"
        and value.get("action") == intent["action"]
        and value.get("source_comment_id") == intent["source_comment_id"]
        and value.get("operation_id") != intent["operation_id"]
    ]
    if conflicts:
        raise GovernanceError("TRANSITION-RECEIPT-CONFLICT", "source command has a conflicting intent", code=3)
    intents = [item for item in records if item[1].get("phase") == "intent" and item[1].get("operation_id") == intent["operation_id"]]
    completions = [item for item in records if item[1].get("phase") == "completed" and item[1].get("operation_id") == intent["operation_id"]]
    if len(intents) > 1 or len(completions) > 1:
        raise GovernanceError("TRANSITION-RECEIPT-CONFLICT", "duplicate transition receipts require reconciliation", code=3)
    intent_comment = intents[0][0] if intents else None
    completed_comment = completions[0][0] if completions else None
    if intent_comment is not None:
        stored = intents[0][1]
        if stored != intent:
            raise GovernanceError("TRANSITION-RECEIPT-CONFLICT", "stored transition intent differs", code=3)
        if _bot_transition_comment(intent_comment, stored) <= intent["source_comment_id"]:
            raise GovernanceError("TRANSITION-RECEIPT-ORDER", "intent must follow its source command", code=3)
    if completed_comment is not None:
        completed = completions[0][1]
        if intent_comment is None or completed["intent_comment_id"] != intent_comment["id"]:
            raise GovernanceError("TRANSITION-COMPLETED-WITHOUT-INTENT", "completion lacks its exact intent", code=3)
        if _bot_transition_comment(completed_comment, completed) <= intent_comment["id"]:
            raise GovernanceError("TRANSITION-RECEIPT-ORDER", "completion must follow its intent", code=3)
        if completed["target_hash"] != intent["target_hash"] or completed["run_id"] != intent["run_id"] or completed["run_url"] != intent["run_url"]:
            raise GovernanceError("TRANSITION-COMPLETED-MISMATCH", "completion differs from the intent", code=3)
    return intent_comment, completed_comment


def _transition_labels(issue: dict[str, Any]) -> tuple[str, ...]:
    return tuple(sorted(_labels(issue)))


def _replace_state(labels: tuple[str, ...], state: str) -> list[str]:
    return sorted([label for label in labels if not label.startswith("state:")] + [state])


def _promotion_body(candidate_body: str, target: dict[str, Any], operation_id: str, candidate_number: int) -> str:
    from .attestations import render_contract_body

    contract_body = render_contract_body(candidate_body, target)
    return (
        f"Promoted Engineering Issue from Candidate #{candidate_number}.\n\n"
        f"<!-- github-governance-promotion-target:v1:{operation_id} -->\n\n"
        + contract_body
    )


def _promotion_targets(api: Any, operation_id: str) -> list[dict[str, Any]]:
    marker = f"<!-- github-governance-promotion-target:v1:{operation_id} -->"
    targets: list[dict[str, Any]] = []
    for issue in api.list_issues(state="all"):
        body = issue.get("body")
        count = body.count(marker) if isinstance(body, str) else 0
        if count == 0:
            continue
        _verify_target_creator(issue)
        if count != 1:
            raise GovernanceError("PROMOTION-TARGET-MARKER", "promotion target marker is duplicated", code=3)
        targets.append(issue)
    return targets


def _verify_target_creator(issue: dict[str, Any]) -> None:
    if "pull_request" in issue:
        raise GovernanceError("PROMOTION-TARGET-PR", "promotion target cannot be a pull request", code=3)
    user = issue.get("user")
    if not isinstance(user, dict) or user.get("login") != "github-actions[bot]" or user.get("type") != "Bot":
        raise GovernanceError("PROMOTION-TARGET-CREATOR", "promotion target is not created by github-actions[bot]", code=3)


def _verify_target_provenance(issue: dict[str, Any]) -> None:
    _verify_target_creator(issue)
    created_at = issue.get("created_at")
    updated_at = issue.get("updated_at")
    if not isinstance(created_at, str) or not created_at or updated_at != created_at:
        raise GovernanceError("PROMOTION-TARGET-UPDATED", "promotion target was edited before contract verification", code=3)


def _timestamp(value: Any, finding: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise GovernanceError(finding, "comment timeline timestamp is invalid", code=3)
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise GovernanceError(finding, "comment timeline timestamp is invalid", code=3) from error


def _verify_operation_comment_timeline(
    comments: list[dict[str, Any]], *, issue_created_at: str, issue_updated_at: str,
    baseline_updated_at: str, baseline_ids: list[int], operation_id: str,
    finding: str,
) -> None:
    from .audit import parse_transition_receipt

    ids: list[int] = []
    times: list[datetime] = []
    for comment in comments:
        identifier = comment.get("id")
        if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
            raise GovernanceError(finding, "comment timeline identity is invalid", code=3)
        created = comment.get("created_at")
        if comment.get("updated_at") != created:
            raise GovernanceError(finding, "comment timeline contains an edited comment", code=3)
        ids.append(identifier)
        times.append(_timestamp(created, finding))
    if ids != sorted(set(ids)) or times != sorted(times) or len(set(times)) != len(times):
        raise GovernanceError(finding, "comment timeline is duplicated, reordered, or non-monotonic", code=3)
    if ids[:len(baseline_ids)] != baseline_ids or len(ids) < len(baseline_ids):
        raise GovernanceError(finding, "comment baseline was deleted, reordered, or replaced", code=3)
    created_time = _timestamp(issue_created_at, finding)
    updated_time = _timestamp(issue_updated_at, finding)
    baseline_time = _timestamp(baseline_updated_at, finding)
    if updated_time < created_time or baseline_time < created_time or updated_time < baseline_time:
        raise GovernanceError(finding, "Issue timeline moves before its immutable baseline", code=3)
    if updated_time > datetime.now(timezone.utc):
        raise GovernanceError(finding, "Issue timeline is in the future", code=3)
    for comment in comments[len(baseline_ids):]:
        receipt = parse_transition_receipt(comment.get("body"))
        if receipt is None or receipt.get("operation_id") != operation_id:
            raise GovernanceError(finding, "post-baseline comment is not canonical for this operation", code=3)
        _bot_transition_comment(comment, receipt)
    post_times = times[len(baseline_ids):]
    if post_times:
        if post_times[0] <= baseline_time or post_times[-1] != updated_time:
            raise GovernanceError(finding, "Issue update is not the strict canonical comment sequence", code=3)
    elif updated_time != baseline_time:
        raise GovernanceError(finding, "Issue update has no canonical post-baseline comment", code=3)


def _verify_target_comment_timeline(
    api: Any, issue: dict[str, Any], *, allow_later_operation: bool = False,
    later_source_comment_id: int | None = None,
) -> None:
    from .audit import parse_transition_receipt

    _verify_target_creator(issue)
    created_at = issue.get("created_at")
    updated_at = issue.get("updated_at")
    number = issue.get("number")
    if not isinstance(created_at, str) or not created_at or not isinstance(updated_at, str) or not updated_at:
        raise GovernanceError("PROMOTION-TARGET-UPDATED", "promotion target timestamps are invalid", code=3)
    if not isinstance(number, int) or isinstance(number, bool) or number < 1:
        raise GovernanceError("PROMOTION-TARGET", "promotion target number is invalid", code=3)
    comments = api.list_comments(number)
    links = [
        (comment, receipt) for comment, receipt in _transition_records(comments)
        if receipt.get("phase") == "link" and receipt.get("target_issue_number") == number
    ]
    if not links:
        if comments or updated_at != created_at:
            raise GovernanceError("PROMOTION-TARGET-UPDATED", "promotion target has no immutable link baseline", code=3)
        return
    if len(links) != 1:
        raise GovernanceError("PROMOTION-TARGET-COMMENT", "promotion target link is missing or duplicated", code=3)
    _, link = links[0]
    if link.get("target_issue_id") != issue.get("id") or link.get("target_created_at") != created_at:
        raise GovernanceError("PROMOTION-TARGET-UPDATED", "promotion target immutable snapshot differs", code=3)
    verified_comments = comments
    verified_updated_at = updated_at
    if allow_later_operation:
        verified_comments = []
        for comment in comments:
            receipt = next((value for item, value in _transition_records([comment]) if item["id"] == comment["id"]), None)
            if receipt is None or receipt.get("operation_id") != link["operation_id"]:
                break
            verified_comments.append(comment)
        if any(
            receipt.get("operation_id") == link["operation_id"]
            for _, receipt in _transition_records(comments[len(verified_comments):])
        ):
            raise GovernanceError("PROMOTION-TARGET-COMMENT", "promotion comments are not contiguous", code=3)
        verified_updated_at = verified_comments[-1]["created_at"]
        suffix = comments[len(verified_comments):]
        if suffix:
            if suffix[0].get("id") != later_source_comment_id:
                raise GovernanceError("PROMOTION-TARGET-COMMENT", "later operation does not start at its verified source", code=3)
            later_operation_ids: set[str] = set()
            for comment in suffix[1:]:
                receipt = parse_transition_receipt(comment.get("body"))
                if receipt is None or receipt.get("action") != "ready":
                    raise GovernanceError("PROMOTION-TARGET-COMMENT", "later suffix contains an unbound comment", code=3)
                _bot_transition_comment(comment, receipt)
                later_operation_ids.add(receipt["operation_id"])
                if receipt.get("phase") == "intent" and receipt.get("source_comment_id") != later_source_comment_id:
                    raise GovernanceError("PROMOTION-TARGET-COMMENT", "later intent does not bind its source", code=3)
            if len(later_operation_ids) > 1:
                raise GovernanceError("PROMOTION-TARGET-COMMENT", "later suffix mixes operations", code=3)
    _verify_operation_comment_timeline(
        verified_comments, issue_created_at=created_at, issue_updated_at=verified_updated_at,
        baseline_updated_at=created_at, baseline_ids=link["target_baseline_comment_ids"],
        operation_id=link["operation_id"], finding="PROMOTION-TARGET-COMMENT",
    )


def _intake_sources(contract: dict[str, Any], repository: str, candidate_number: int) -> list[int]:
    provenance = contract.get("provenance")
    sources = provenance.get("sources") if isinstance(provenance, dict) else None
    if not isinstance(sources, list):
        raise GovernanceError("PROMOTION-INTAKE-PROVENANCE", "promotion provenance sources are unavailable", code=3)
    intake_refs = [source for source in sources if isinstance(source, dict) and source.get("role") == "intake"]
    if not intake_refs:
        raise GovernanceError("PROMOTION-INTAKE-PROVENANCE", "promotion requires at least one explicit Intake source", code=3)
    numbers: list[int] = []
    seen: set[tuple[str, int]] = set()
    for source in intake_refs:
        source_repository = source.get("repository")
        number = source.get("number")
        if source_repository != repository:
            raise GovernanceError("PROMOTION-INTAKE-REPOSITORY", "cross-repository Intake linkage is not supported safely", code=3)
        if not isinstance(number, int) or isinstance(number, bool) or number < 1:
            raise GovernanceError("PROMOTION-INTAKE-PROVENANCE", "Intake source number is invalid", code=3)
        identity = (source_repository, number)
        if identity in seen:
            raise GovernanceError("PROMOTION-INTAKE-DUPLICATE", "promotion contains a duplicate Intake source", code=3)
        if number == candidate_number:
            raise GovernanceError("PROMOTION-INTAKE-CYCLE", "Candidate cannot also be its Intake source", code=3)
        seen.add(identity)
        numbers.append(number)
    return sorted(numbers)


def _intake_snapshot(api: Any, repository: str, number: int) -> dict[str, Any]:
    issue = api.get_issue(number)
    if "pull_request" in issue:
        raise GovernanceError("PROMOTION-INTAKE-PR", "Intake source cannot be a pull request", code=3)
    if issue.get("number") != number:
        raise GovernanceError("PROMOTION-INTAKE-IDENTITY", "Intake source number differs from its provenance", code=3)
    identifier = issue.get("id")
    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
        raise GovernanceError("PROMOTION-INTAKE-IDENTITY", "Intake source ID is invalid", code=3)
    expected_repository_url = f"https://api.github.com/repos/{repository}"
    if issue.get("repository_url") != expected_repository_url:
        raise GovernanceError("PROMOTION-INTAKE-REPOSITORY", "Intake source repository identity differs", code=3)
    user = issue.get("user")
    if (
        not isinstance(user, dict)
        or not isinstance(user.get("login"), str)
        or not user["login"]
        or user.get("type") not in {"User", "Bot"}
    ):
        raise GovernanceError("PROMOTION-INTAKE-AUTHOR", "Intake source author identity is invalid", code=3)
    labels = _transition_labels(issue)
    allowed_states = {"state:new", "state:triaged", "state:investigating", "state:closed"}
    if "type:intake" not in labels or len(set(labels) & allowed_states) != 1 or issue.get("state") not in {"open", "closed"}:
        raise GovernanceError("PROMOTION-INTAKE-STATE", "Intake source state is invalid", code=3)
    title = issue.get("title")
    body = issue.get("body")
    created_at = issue.get("created_at")
    updated_at = issue.get("updated_at")
    if (
        not isinstance(title, str) or not title
        or not isinstance(body, str)
        or not isinstance(created_at, str) or not created_at
        or not isinstance(updated_at, str) or not updated_at
    ):
        raise GovernanceError("PROMOTION-INTAKE-SHAPE", "Intake source body or update time is invalid", code=3)
    comments = api.list_comments(number)
    baseline_ids = [comment.get("id") for comment in comments]
    # A baseline can contain pre-existing discussion, but its identities and
    # ordering must already be unambiguous before they are frozen.
    if any(not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1 for identifier in baseline_ids) or baseline_ids != sorted(set(baseline_ids)):
        raise GovernanceError("PROMOTION-INTAKE-COMMENT", "Intake comment baseline is invalid", code=3)
    return {
        "id": identifier,
        "number": number,
        "title": title,
        "body": body,
        "created_at": created_at,
        "updated_at": updated_at,
        "state": issue["state"],
        "repository_url": issue["repository_url"],
        "user": {"login": normalize_login(user["login"]), "type": user["type"]},
        "labels": labels,
        "baseline_comment_ids": baseline_ids,
    }


def _verify_intake_unchanged(
    api: Any,
    repository: str,
    expected: dict[str, Any],
    *,
    operation_id: str | None = None,
    candidate_number: int | None = None,
    target_number: int | None = None,
) -> None:
    current = _intake_snapshot(api, repository, expected["number"])
    current_without_timeline = {key: value for key, value in current.items() if key not in {"updated_at", "baseline_comment_ids"}}
    expected_without_timeline = {key: value for key, value in expected.items() if key not in {"updated_at", "baseline_comment_ids"}}
    if current_without_timeline != expected_without_timeline:
        raise GovernanceError("PROMOTION-INTAKE-TOCTOU", "Intake source changed before linkage", code=3)
    if operation_id is None:
        if current["updated_at"] != expected["updated_at"] or current["baseline_comment_ids"] != expected["baseline_comment_ids"]:
            raise GovernanceError("PROMOTION-INTAKE-TOCTOU", "Intake source changed before linkage", code=3)
        return
    _verify_operation_comment_timeline(
        api.list_comments(expected["number"]), issue_created_at=expected["created_at"],
        issue_updated_at=current["updated_at"], baseline_updated_at=expected["updated_at"],
        baseline_ids=expected["baseline_comment_ids"], operation_id=operation_id,
        finding="PROMOTION-INTAKE-TOCTOU",
    )


def _verify_intake_snapshots(
    api: Any, repository: str, snapshots: list[dict[str, Any]], *,
    operation_id: str | None = None, candidate_number: int | None = None,
    target_number: int | None = None,
) -> None:
    for snapshot in sorted(snapshots, key=lambda value: value["number"]):
        _verify_intake_unchanged(
            api, repository, snapshot, operation_id=operation_id,
            candidate_number=candidate_number, target_number=target_number,
        )


def _bind_intake_snapshots_to_receipts(
    api: Any, snapshots: list[dict[str, Any]], operation_id: str,
) -> list[dict[str, Any]]:
    """Recover the original immutable Intake snapshot without refreshing it."""

    bound: list[dict[str, Any]] = []
    from .canonical import sha256_tagged
    for current in snapshots:
        receipts = [
            receipt for _, receipt in _transition_records(api.list_comments(current["number"]))
            if receipt.get("phase") == "provenance-link"
            and receipt.get("operation_id") == operation_id
            and receipt.get("intake_issue_number") == current["number"]
        ]
        if not receipts:
            bound.append(current)
            continue
        baseline = receipts[0]
        if any(
            receipt.get(key) != baseline.get(key)
            for receipt in receipts[1:]
            for key in (
                "intake_issue_id", "intake_repository_url", "intake_author",
                "intake_author_type", "intake_title", "intake_created_at",
                "intake_body_digest", "intake_updated_at", "intake_state", "intake_labels",
                "intake_baseline_comment_ids",
            )
        ):
            raise GovernanceError("PROMOTION-INTAKE-TOCTOU", "Intake receipts disagree on their immutable snapshot", code=3)
        if (
            current["id"] != baseline.get("intake_issue_id")
            or current["repository_url"] != baseline.get("intake_repository_url")
            or current["user"]["login"] != baseline.get("intake_author")
            or current["user"]["type"] != baseline.get("intake_author_type")
            or current["title"] != baseline.get("intake_title")
            or current["created_at"] != baseline.get("intake_created_at")
            or sha256_tagged(current["body"]) != baseline.get("intake_body_digest")
            or current["state"] != baseline.get("intake_state")
            or list(current["labels"]) != baseline.get("intake_labels")
        ):
            raise GovernanceError("PROMOTION-INTAKE-TOCTOU", "Intake differs from its immutable receipt snapshot", code=3)
        recovered = dict(current)
        recovered["updated_at"] = baseline["intake_updated_at"]
        recovered["baseline_comment_ids"] = baseline["intake_baseline_comment_ids"]
        bound.append(recovered)
    return bound


def _operation_updated_at(api: Any, issue_number: int, operation_id: str, initial: str) -> str:
    latest = initial
    for comment, receipt in _transition_records(api.list_comments(issue_number)):
        if receipt.get("operation_id") == operation_id:
            _bot_transition_comment(comment, receipt)
            latest = max(latest, comment["created_at"])
    return latest


def _ensure_unique_receipt(
    api: Any, issue_number: int, receipt: dict[str, Any]
) -> dict[str, Any]:
    def identity(value: dict[str, Any]) -> tuple[Any, ...]:
        if value.get("phase") == "provenance-link":
            return (
                value.get("phase"), value.get("intake_issue_number"),
                value.get("candidate_issue_number"), value.get("target_issue_number"),
                value.get("comment_issue_number"),
            )
        return (value.get("phase"),)

    expected_identity = identity(receipt)
    records = [
        item for item in _transition_records(api.list_comments(issue_number))
        if item[1].get("operation_id") == receipt["operation_id"] and identity(item[1]) == expected_identity
    ]
    if len(records) > 1:
        raise GovernanceError("TRANSITION-RECEIPT-CONFLICT", "duplicate transition receipt", code=3)
    if records:
        if records[0][1] != receipt:
            raise GovernanceError("TRANSITION-RECEIPT-CONFLICT", "transition receipt differs from its target", code=3)
        _bot_transition_comment(*records[0])
        return records[0][0]
    return _create_transition_receipt(api, issue_number, receipt)


def _provenance_link_receipt(
    *,
    repository: str,
    operation_id: str,
    intake_snapshot: dict[str, Any],
    candidate_number: int,
    target_number: int,
    comment_number: int,
    target_hash: str,
) -> dict[str, Any]:
    from .canonical import sha256_tagged

    return {
        "version": 1,
        "phase": "provenance-link",
        "operation_id": operation_id,
        "action": "promote",
        "repository": repository,
        "intake_issue_number": intake_snapshot["number"],
        "intake_issue_id": intake_snapshot["id"],
        "intake_repository_url": intake_snapshot["repository_url"],
        "intake_author": intake_snapshot["user"]["login"],
        "intake_author_type": intake_snapshot["user"]["type"],
        "intake_title": intake_snapshot["title"],
        "intake_created_at": intake_snapshot["created_at"],
        "intake_body_digest": sha256_tagged(intake_snapshot["body"]),
        "intake_updated_at": intake_snapshot["updated_at"],
        "intake_state": intake_snapshot["state"],
        "intake_labels": list(intake_snapshot["labels"]),
        "intake_baseline_comment_ids": list(intake_snapshot["baseline_comment_ids"]),
        "candidate_issue_number": candidate_number,
        "target_issue_number": target_number,
        "comment_issue_number": comment_number,
        "target_hash": target_hash,
    }


def _ensure_promotion_links(
    api: Any,
    repository: str,
    intake_snapshots: list[dict[str, Any]],
    candidate_number: int,
    target_number: int,
    operation_id: str,
    target_hash: str,
) -> None:
    for snapshot in intake_snapshots:
        intake_number = snapshot["number"]
        for comment_number in (candidate_number, target_number, intake_number):
            _verify_intake_unchanged(
                api, repository, snapshot, operation_id=operation_id,
                candidate_number=candidate_number, target_number=target_number,
            )
            receipt = _provenance_link_receipt(
                repository=repository,
                operation_id=operation_id,
                intake_snapshot=snapshot,
                candidate_number=candidate_number,
                target_number=target_number,
                comment_number=comment_number,
                target_hash=target_hash,
            )
            _ensure_unique_receipt(api, comment_number, receipt)


def _verify_promotion_links(
    api: Any,
    repository: str,
    contract: dict[str, Any],
    candidate_number: int,
    target_number: int,
    operation_id: str,
    target_hash: str,
) -> None:
    intake_numbers = _intake_sources(contract, repository, candidate_number)
    for intake_number in intake_numbers:
        snapshot = _intake_snapshot(api, repository, intake_number)
        snapshot = _bind_intake_snapshots_to_receipts(api, [snapshot], operation_id)[0]
        for comment_number in (candidate_number, target_number, intake_number):
            expected = _provenance_link_receipt(
                repository=repository,
                operation_id=operation_id,
                intake_snapshot=snapshot,
                candidate_number=candidate_number,
                target_number=target_number,
                comment_number=comment_number,
                target_hash=target_hash,
            )
            matches = [
                value for _, value in _transition_records(api.list_comments(comment_number))
                if value.get("operation_id") == operation_id
                and value.get("phase") == "provenance-link"
                and value.get("intake_issue_number") == intake_number
                and value.get("comment_issue_number") == comment_number
            ]
            if len(matches) != 1 or matches[0] != expected:
                raise GovernanceError("PROMOTION-PROVENANCE-LINK", "promotion provenance linkage is missing or conflicting", code=3)


def execute_promotion(
    api: Any,
    repository_id: int,
    repository: str,
    candidate_number: int,
    command: AuthorizedCommand,
    source: Any,
    policy: dict[str, Any],
    run_id: int,
    run_url: str,
    *,
    repository_root: str = ".",
    workflow_head_sha: str | None = None,
) -> dict[str, Any]:
    """Create or recover exactly one frozen Engineering Issue target."""

    from .attestations import candidate_gate
    from .canonical import contract_hash, subject_digest
    from .contract import extract_contract
    from .schema_validation import schema_findings
    from .semantic import semantic_findings
    from .state import build_promotion_target

    if command.action != "promote" or not authorized(policy, "trusted_issue_authors", command.actor):
        raise GovernanceError("PROMOTION-UNAUTHORIZED", "promotion actor is not authorized", code=3)
    candidate = api.get_issue(candidate_number)
    labels = _transition_labels(candidate)
    if "type:candidate" not in labels or not ({"state:gate-passed", "state:promoted"} & set(labels)):
        raise GovernanceError("PROMOTION-STATE", "promotion requires a gate-passed Candidate", code=3)
    if "pull_request" in candidate:
        raise GovernanceError("PROMOTION-PR", "a pull request cannot be promoted", code=3)
    comments = api.list_comments(candidate_number)
    source = _source_from_comments(comments, command, source)
    current = extract_contract(candidate.get("body", "")).contract
    if current.get("issue_revision") != command.revision or subject_digest(current) != command.subject_digest:
        raise GovernanceError("PROMOTION-STALE-SUBJECT", "Candidate changed after the promotion command", code=3)
    actual_author = candidate.get("user", {}).get("login")
    declared_author = current.get("provenance", {}).get("created_by")
    if not isinstance(actual_author, str) or not isinstance(declared_author, str) or normalize_login(actual_author) != normalize_login(declared_author):
        raise GovernanceError("PROMOTION-CANDIDATE-AUTHOR", "Candidate API author differs from provenance")
    findings = candidate_gate(
        current,
        policy,
        repository_root,
        issue_author=actual_author,
        comments=comments,
        api=api,
        repository_id=repository_id,
        issue_id=candidate.get("id"),
        repository=repository,
        issue_number=candidate_number,
    )
    if findings:
        raise GovernanceError("PROMOTION-GATE-FAIL", "Candidate does not pass the current deterministic Gate", code=3)
    target = build_promotion_target(
        current,
        repository=repository,
        candidate_number=candidate_number,
        actor=command.actor,
        frozen_at=source.created_at,
    )
    target_findings = schema_findings(target, repository_root)
    if not target_findings:
        target_findings = semantic_findings(target, policy, repository_root)
    if target_findings:
        raise GovernanceError("PROMOTION-TARGET-GATE", "rendered Engineering Issue fails deterministic validation", code=3)
    intake_numbers = _intake_sources(current, repository, candidate_number)
    intake_snapshots = [_intake_snapshot(api, repository, number) for number in intake_numbers]
    intent = _transition_operation(
        repository_id=repository_id,
        issue=candidate,
        repository=repository,
        command=command,
        source=source,
        before=current,
        target=target,
        run_id=run_id,
        run_url=run_url,
        workflow_path=_PROMOTION_WORKFLOW,
        head_sha=workflow_head_sha,
        comments=comments,
    )
    intake_snapshots = _bind_intake_snapshots_to_receipts(api, intake_snapshots, intent["operation_id"])
    title = candidate.get("title")
    if not isinstance(title, str) or not title:
        raise GovernanceError("PROMOTION-TITLE", "Candidate title is missing", code=3)
    target_title = f"[Engineering] {title}"
    target_body = _promotion_body(candidate["body"], target, intent["operation_id"], candidate_number)
    targets = _promotion_targets(api, intent["operation_id"])
    if len(targets) > 1:
        raise GovernanceError("PROMOTION-TARGET-CONFLICT", "multiple Engineering targets share the promotion nonce", code=3)
    if targets:
        preflight_target = targets[0]
        preflight_number = _positive_integer(preflight_target.get("number"), "PROMOTION-TARGET", "target Issue number")
        preflight_id = _positive_integer(preflight_target.get("id"), "PROMOTION-TARGET", "target Issue id")
        preflight_readback = api.get_issue(preflight_number)
        _verify_target_comment_timeline(api, preflight_readback)
        if (
            preflight_readback.get("id") != preflight_id
            or preflight_readback.get("updated_at") != preflight_target.get("updated_at")
            or preflight_readback.get("title") != target_title
            or preflight_readback.get("body") != target_body
            or _transition_labels(preflight_readback) != ("state:contracted", "type:engineering")
        ):
            raise GovernanceError("PROMOTION-TARGET-READBACK", "occupied promotion target marker differs", code=3)
        preflight_contract = extract_contract(preflight_readback["body"]).contract
        if preflight_contract != target or contract_hash(preflight_contract) != intent["target_hash"]:
            raise GovernanceError("PROMOTION-TARGET-HASH", "occupied promotion target contract differs", code=3)
    intent_comment, completed_comment = _operation_receipts(comments, intent)
    if intent_comment is None:
        if "state:promoted" in labels:
            raise GovernanceError("PROMOTION-RECOVERY-CONFLICT", "promoted Candidate has no authorizing intent", code=3)
        intent_comment = _create_transition_receipt(api, candidate_number, intent)
    _verify_transition_run(api, intent, current_run_id=run_id)
    if completed_comment is None:
        locked_candidate = api.get_issue(candidate_number)
        if (
            locked_candidate.get("id") != candidate.get("id")
            or locked_candidate.get("body") != candidate.get("body")
            or locked_candidate.get("updated_at") != _operation_updated_at(
                api, candidate_number, intent["operation_id"], candidate["updated_at"]
            )
            or _transition_labels(locked_candidate) != labels
        ):
            raise GovernanceError("PROMOTION-CANDIDATE-TOCTOU", "Candidate changed after promotion intent", code=3)

    created_now = False
    if not targets:
        try:
            created = api.create_issue(target_title, target_body, ["type:engineering", "state:contracted"])
            _verify_target_provenance(created)
            targets = [created]
            created_now = True
        except GovernanceError as error:
            if error.code != 4:
                raise
            targets = _promotion_targets(api, intent["operation_id"])
            if len(targets) != 1:
                raise
    target_issue = targets[0]
    target_number = _positive_integer(target_issue.get("number"), "PROMOTION-TARGET", "target Issue number")
    target_id = _positive_integer(target_issue.get("id"), "PROMOTION-TARGET", "target Issue id")
    readback = api.get_issue(target_number)
    _verify_target_comment_timeline(api, readback)
    if (
        readback.get("id") != target_id
        or readback.get("updated_at") != target_issue.get("updated_at")
        or readback.get("title") != target_title
        or readback.get("body") != target_body
        or _transition_labels(readback) != ("state:contracted", "type:engineering")
    ):
        raise GovernanceError("PROMOTION-TARGET-READBACK", "Engineering target read-back differs", code=3)
    readback_contract = extract_contract(readback["body"]).contract
    if (
        readback_contract != target
        or subject_digest(readback_contract) != command.subject_digest
        or readback_contract.get("issue_revision") != command.revision
        or contract_hash(readback_contract) != intent["target_hash"]
    ):
        raise GovernanceError("PROMOTION-TARGET-HASH", "Engineering target contract differs", code=3)
    if completed_comment is not None:
        from .audit import parse_transition_receipt

        existing_completion = parse_transition_receipt(completed_comment.get("body"))
        if (
            existing_completion is None
            or existing_completion.get("action") != "promote"
            or existing_completion.get("intent_comment_id") != intent_comment.get("id")
            or existing_completion.get("source_issue_number") != candidate_number
            or existing_completion.get("target_issue_number") != target_number
        ):
            raise GovernanceError("PROMOTION-COMPLETION-CONFLICT", "promotion completion target differs", code=3)

    link = {
        "version": 1,
        "phase": "link",
        "operation_id": intent["operation_id"],
        "action": "promote",
        "source_issue_number": candidate_number,
        "target_issue_number": target_number,
        "target_issue_id": target_id,
        "target_created_at": readback["created_at"],
        "target_baseline_comment_ids": [],
        "target_hash": intent["target_hash"],
        "source_comment_id": source.id,
    }
    _ensure_unique_receipt(api, target_number, link)
    _ensure_promotion_links(
        api,
        repository,
        intake_snapshots,
        candidate_number,
        target_number,
        intent["operation_id"],
        intent["target_hash"],
    )
    _verify_intake_snapshots(
        api, repository, intake_snapshots, operation_id=intent["operation_id"],
        candidate_number=candidate_number, target_number=target_number,
    )
    completion = {
        "version": 1,
        "phase": "completed",
        "operation_id": intent["operation_id"],
        "action": "promote",
        "intent_comment_id": intent_comment["id"],
        "source_issue_number": candidate_number,
        "target_issue_number": target_number,
        "target_hash": intent["target_hash"],
        "result": "applied" if created_now else "recovered",
        "run_id": intent["run_id"],
        "run_url": intent["run_url"],
    }
    if completed_comment is None:
        completion_comment = _ensure_unique_receipt(api, candidate_number, completion)
    else:
        completion_comment = completed_comment

    final_target = api.get_issue(target_number)
    _verify_target_comment_timeline(api, final_target)
    stable_target = api.get_issue(target_number)
    _verify_target_comment_timeline(api, stable_target)
    if (
        final_target.get("id") != target_id
        or final_target.get("title") != target_title
        or final_target.get("body") != target_body
        or _transition_labels(final_target) != ("state:contracted", "type:engineering")
        or stable_target.get("id") != final_target.get("id")
        or stable_target.get("title") != final_target.get("title")
        or stable_target.get("body") != final_target.get("body")
        or stable_target.get("updated_at") != final_target.get("updated_at")
        or _transition_labels(stable_target) != _transition_labels(final_target)
    ):
        raise GovernanceError("PROMOTION-TARGET-TOCTOU", "Engineering target changed before Candidate finalization", code=3)

    before_finalize = api.get_issue(candidate_number)
    if before_finalize.get("id") != candidate.get("id") or before_finalize.get("body") != candidate.get("body"):
        raise GovernanceError("PROMOTION-CANDIDATE-TOCTOU", "Candidate changed before finalization", code=3)
    final_labels = _replace_state(_transition_labels(before_finalize), "state:promoted")
    if "state:promoted" not in _transition_labels(before_finalize):
        prewrite = api.get_issue(candidate_number)
        if (
            prewrite.get("id") != before_finalize.get("id")
            or prewrite.get("body") != before_finalize.get("body")
            or prewrite.get("updated_at") != before_finalize.get("updated_at")
            or _transition_labels(prewrite) != _transition_labels(before_finalize)
        ):
            raise GovernanceError("PROMOTION-CANDIDATE-TOCTOU", "Candidate changed before label finalization", code=3)
        _verify_intake_snapshots(
            api, repository, intake_snapshots, operation_id=intent["operation_id"],
            candidate_number=candidate_number, target_number=target_number,
        )
        try:
            api.update_issue(candidate_number, labels=final_labels)
        except GovernanceError as error:
            if error.code != 4:
                raise
    finalized = api.get_issue(candidate_number)
    if finalized.get("body") != candidate.get("body") or _transition_labels(finalized) != tuple(final_labels):
        raise GovernanceError("PROMOTION-CANDIDATE-READBACK", "Candidate finalization read-back differs", code=3)
    return {
        "result": "idempotent" if completed_comment is not None else "applied" if created_now else "recovered",
        "operation_id": intent["operation_id"],
        "target_issue_number": target_number,
        "target_hash": intent["target_hash"],
    }


def _promotion_chain(
    api: Any,
    engineering_issue: dict[str, Any],
    contract: dict[str, Any],
    policy: dict[str, Any],
    repository_id: int,
    repository: str,
    repository_root: str,
    ready_source_comment_id: int = 0,
) -> dict[str, Any]:
    """Verify bot-authored Engineering provenance through the Candidate chain."""

    from .attestations import SourceComment, candidate_gate
    from .canonical import contract_hash
    from .contract import extract_contract
    from .state import build_promotion_target

    body = engineering_issue.get("body")
    markers = _PROMOTION_TARGET.findall(body if isinstance(body, str) else "")
    if len(markers) != 1:
        raise GovernanceError("PROMOTION-CHAIN-MARKER", "Engineering Issue lacks one promotion target marker", code=3)
    operation_id = markers[0]
    _verify_target_comment_timeline(
        api, engineering_issue, allow_later_operation=True,
        later_source_comment_id=ready_source_comment_id,
    )
    sources = [
        item for item in contract.get("provenance", {}).get("sources", [])
        if isinstance(item, dict) and item.get("role") == "candidate" and item.get("repository") == repository
    ]
    if len(sources) != 1:
        raise GovernanceError("PROMOTION-CHAIN-SOURCE", "Engineering provenance lacks one local Candidate source", code=3)
    candidate_number = sources[0].get("number")
    candidate = api.get_issue(candidate_number)
    if "state:promoted" not in _transition_labels(candidate) or "type:candidate" not in _transition_labels(candidate):
        raise GovernanceError("PROMOTION-CHAIN-CANDIDATE", "source Candidate is not finalized as promoted", code=3)
    candidate_contract = extract_contract(candidate.get("body", "")).contract
    candidate_comments = api.list_comments(candidate_number)
    records = _transition_records(candidate_comments)
    intents = [item for item in records if item[1].get("phase") == "intent" and item[1].get("operation_id") == operation_id]
    completions = [item for item in records if item[1].get("phase") == "completed" and item[1].get("operation_id") == operation_id]
    if len(intents) != 1 or len(completions) != 1:
        raise GovernanceError("PROMOTION-CHAIN-RECEIPT", "promotion requires one intent and completion", code=3)
    intent_comment, intent = intents[0]
    completed_comment, completion = completions[0]
    if intent.get("action") != "promote" or intent.get("repository_id") != repository_id:
        raise GovernanceError("PROMOTION-CHAIN-RECEIPT", "promotion intent identity differs", code=3)
    source_matches = [item for item in candidate_comments if item.get("id") == intent["source_comment_id"]]
    if len(source_matches) != 1:
        raise GovernanceError("PROMOTION-CHAIN-SOURCE", "promotion source command is missing", code=3)
    source = SourceComment.from_api(source_matches[0])
    source.verify_unchanged()
    parsed = parse_command(source.body)
    if parsed is None or parsed.action != "promote" or source.body_digest != intent["source_body_digest"]:
        raise GovernanceError("PROMOTION-CHAIN-SOURCE", "promotion source command changed", code=3)
    _verify_transition_run(api, intent)
    if completion.get("intent_comment_id") != intent_comment.get("id") or completion.get("target_issue_number") != engineering_issue.get("number"):
        raise GovernanceError("PROMOTION-CHAIN-COMPLETION", "promotion completion differs from the Engineering target", code=3)
    if completed_comment.get("id", 0) <= intent_comment.get("id", 0):
        raise GovernanceError("PROMOTION-CHAIN-ORDER", "promotion completion precedes its intent", code=3)
    target_comments = api.list_comments(engineering_issue["number"])
    links = [item for item in _transition_records(target_comments) if item[1].get("phase") == "link" and item[1].get("operation_id") == operation_id]
    if len(links) != 1 or links[0][1].get("source_issue_number") != candidate_number:
        raise GovernanceError("PROMOTION-CHAIN-LINK", "Engineering target linkage is missing or conflicting", code=3)
    target = build_promotion_target(
        candidate_contract,
        repository=repository,
        candidate_number=candidate_number,
        actor=intent["actor"],
        frozen_at=source.created_at,
    )
    if contract.get("status") == "contracted" and target != contract:
        raise GovernanceError("PROMOTION-CHAIN-TARGET", "contracted target differs from its promotion intent", code=3)
    if contract_hash(target) != intent["target_hash"] or completion["target_hash"] != intent["target_hash"]:
        raise GovernanceError("PROMOTION-CHAIN-HASH", "promotion target hash differs across receipts", code=3)
    _verify_promotion_links(
        api,
        repository,
        candidate_contract,
        candidate_number,
        engineering_issue["number"],
        operation_id,
        intent["target_hash"],
    )
    author = candidate.get("user", {}).get("login", "")
    findings = candidate_gate(
        candidate_contract,
        policy,
        repository_root,
        issue_author=author,
        comments=candidate_comments,
        api=api,
        repository_id=repository_id,
        issue_id=candidate.get("id"),
        repository=repository,
        issue_number=candidate_number,
    )
    if findings:
        raise GovernanceError("PROMOTION-CHAIN-GATE", "source Candidate no longer proves the promoted contract", code=3)
    return target


def execute_ready(
    api: Any,
    repository_id: int,
    repository: str,
    issue_number: int,
    command: AuthorizedCommand,
    source: Any,
    policy: dict[str, Any],
    run_id: int,
    run_url: str,
    *,
    repository_root: str = ".",
    workflow_head_sha: str | None = None,
) -> dict[str, Any]:
    """Apply or recover the contracted -> ready lifecycle-only transition."""

    from .attestations import render_contract_body
    from .audit import classify_transition_recovery
    from .canonical import contract_hash, subject_digest
    from .contract import extract_contract
    from .state import build_ready_target, ready_findings

    if command.action != "ready" or not authorized(policy, "trusted_developers", command.actor):
        raise GovernanceError("READY-UNAUTHORIZED", "ready actor is not authorized", code=3)
    issue = api.get_issue(issue_number)
    labels = _transition_labels(issue)
    if "type:engineering" not in labels or not ({"state:contracted", "state:ready"} & set(labels)):
        raise GovernanceError("READY-STATE", "ready requires a contracted Engineering Issue", code=3)
    comments = api.list_comments(issue_number)
    source = _source_from_comments(comments, command, source)
    current = extract_contract(issue.get("body", "")).contract
    if current.get("issue_revision") != command.revision or subject_digest(current) != command.subject_digest:
        raise GovernanceError("READY-STALE-SUBJECT", "Engineering contract changed after the ready command", code=3)
    promoted_target = _promotion_chain(
        api, issue, current, policy, repository_id, repository, repository_root, source.id
    )
    if current.get("status") == "contracted":
        findings = ready_findings(current, policy, repository_root)
        if findings:
            stale = next((item for item in findings if item.id.startswith("STALE-")), findings[0])
            raise GovernanceError(stale.id, stale.message, code=3, path=stale.path)
    target = build_ready_target(promoted_target, actor=command.actor, frozen_at=source.created_at)
    intent = _transition_operation(
        repository_id=repository_id,
        issue=issue,
        repository=repository,
        command=command,
        source=source,
        before=promoted_target,
        target=target,
        run_id=run_id,
        run_url=run_url,
        workflow_path=_GOVERNANCE_WORKFLOW,
        head_sha=workflow_head_sha,
        comments=comments,
    )
    intent_comment, completed_comment = _operation_receipts(comments, intent)
    current_hash = contract_hash(current)
    relation = "before" if current_hash == intent["expected_before_hash"] else "target" if current_hash == intent["target_hash"] else "unexpected"
    decision = classify_transition_recovery(intent_comment is not None, relation, completed_comment is not None)
    if decision == "conflict":
        raise GovernanceError("READY-RECOVERY-CONFLICT", "ready recovery state requires reconciliation", code=3)
    if intent_comment is None:
        intent_comment = _create_transition_receipt(api, issue_number, intent)
    _verify_transition_run(api, intent, current_run_id=run_id)
    target_body = render_contract_body(issue["body"], target)
    final_labels = _replace_state(labels, "state:ready")
    if decision in {"start", "write-target"}:
        reread = api.get_issue(issue_number)
        _verify_target_creator(reread)
        _verify_operation_comment_timeline(
            api.list_comments(issue_number), issue_created_at=reread["created_at"],
            issue_updated_at=reread["updated_at"], baseline_updated_at=intent["baseline_updated_at"],
            baseline_ids=intent["baseline_comment_ids"], operation_id=intent["operation_id"],
            finding="READY-TOCTOU",
        )
        if (
            reread.get("id") != issue.get("id")
            or reread.get("body") != issue.get("body")
            or reread.get("updated_at") != _operation_updated_at(
                api, issue_number, intent["operation_id"], issue["updated_at"]
            )
            or _transition_labels(reread) != labels
        ):
            raise GovernanceError("READY-TOCTOU", "Engineering Issue changed before ready mutation", code=3)
        try:
            api.update_issue(issue_number, body=target_body, labels=final_labels)
        except GovernanceError as error:
            if error.code != 4:
                raise
    readback = api.get_issue(issue_number)
    _verify_target_creator(readback)
    if readback.get("id") != issue.get("id") or readback.get("body") != target_body or _transition_labels(readback) != tuple(final_labels):
        raise GovernanceError("READY-TARGET-READBACK", "ready target did not read back exactly", code=3)
    if contract_hash(extract_contract(readback["body"]).contract) != intent["target_hash"]:
        raise GovernanceError("READY-TARGET-HASH", "ready target hash differs", code=3)
    if completed_comment is not None:
        from .audit import parse_transition_receipt

        existing_completion = parse_transition_receipt(completed_comment.get("body"))
        if (
            existing_completion is None
            or existing_completion.get("action") != "ready"
            or existing_completion.get("intent_comment_id") != intent_comment.get("id")
            or existing_completion.get("source_issue_number") != issue_number
            or existing_completion.get("target_issue_number") != issue_number
        ):
            raise GovernanceError("READY-COMPLETION-CONFLICT", "ready completion target differs", code=3)
    completion = {
        "version": 1,
        "phase": "completed",
        "operation_id": intent["operation_id"],
        "action": "ready",
        "intent_comment_id": intent_comment["id"],
        "source_issue_number": issue_number,
        "target_issue_number": issue_number,
        "target_hash": intent["target_hash"],
        "result": "recovered" if decision == "write-completed" else "applied",
        "run_id": intent["run_id"],
        "run_url": intent["run_url"],
    }
    if completed_comment is None:
        _ensure_unique_receipt(api, issue_number, completion)
    handoff = {
        "version": 1,
        "phase": "handoff",
        "operation_id": intent["operation_id"],
        "action": "ready",
        "issue_number": issue_number,
        "issue_revision": target["issue_revision"],
        "subject_digest": subject_digest(target),
        "contract_hash": contract_hash(target),
        "base_commit": target["base_commit"],
    }
    _ensure_unique_receipt(api, issue_number, handoff)
    return {"result": "idempotent" if decision == "success" else completion["result"], "operation_id": intent["operation_id"], "target_hash": intent["target_hash"]}


def _workflow_output(**values: object) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as output:
        for key, value in values.items():
            output.write(f"{key}={str(value).lower() if isinstance(value, bool) else value}\n")


def _event_file() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise GovernanceError("EVENT-FILE", "GITHUB_EVENT_PATH is required", code=2)
    try:
        with open(path, encoding="utf-8") as source:
            event = json.load(source)
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceError("EVENT-FILE", "GitHub event file is unreadable or invalid", code=2) from error
    if not isinstance(event, dict):
        raise GovernanceError("EVENT-SHAPE", "GitHub event must be an object", code=2)
    return event


def _workflow_context(event: dict[str, Any]):
    from .attestations import SourceComment
    from .contract import extract_contract
    from .github_api import GitHubAPI, urllib_transport
    from .policy import load_policy

    repository = event.get("repository", {})
    full_name = repository.get("full_name")
    repository_id = repository.get("id")
    if not isinstance(full_name, str) or not isinstance(repository_id, int):
        raise GovernanceError("EVENT-REPOSITORY", "repository identity is missing", code=2)
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GovernanceError("EVENT-TOKEN", "GitHub API token is unavailable", code=4)
    api = GitHubAPI(urllib_transport(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")), full_name)
    policy = load_policy(".github/project-policy.yml", ".")
    event_name = os.environ.get("GITHUB_EVENT_NAME", "")
    if event_name == "workflow_dispatch":
        inputs = event.get("inputs", {})
        issue_number = _positive_integer(inputs.get("issue_number"), "EVENT-ISSUE", "Issue number")
    else:
        issue_number = _positive_integer(event.get("issue", {}).get("number"), "EVENT-ISSUE", "Issue number")
    issue = api.get_issue(issue_number)
    labels = issue.get("labels", [])
    names = {label.get("name") for label in labels if isinstance(label, dict)}
    if not names & {"type:candidate", "type:engineering"}:
        return api, policy, repository_id, full_name, issue_number, issue, None, None
    contract = extract_contract(issue.get("body", "")).contract
    command = None
    source = None
    if event_name == "issue_comment":
        event = dict(event, issue=issue)
        command = authorize_issue_comment(event, contract, policy)
        if command is not None:
            source = SourceComment.from_api(event["comment"])
    elif event_name == "workflow_dispatch" and event.get("inputs", {}).get("operation") in {"review", "approve", "promote", "ready"}:
        source_api = api.get_comment(_positive_integer(event["inputs"].get("source_comment_id"), "EVENT-SOURCE-ID", "source comment id"))
        dispatch_event = dict(event, issue_id=issue.get("id"), issue_labels=labels)
        command = authorize_dispatch(dispatch_event, source_api, contract, policy)
        source = SourceComment.from_api(source_api)
    return api, policy, repository_id, full_name, issue_number, issue, contract, (command, source)


def _read_phase(event: dict[str, Any]) -> int:
    from .attestations import candidate_gate
    from .audit import gate_result

    api, policy, repository_id, full_name, issue_number, issue, contract, command_source = _workflow_context(event)
    mode = policy["rollout_mode"]
    if contract is None:
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result="PASS", operation="noop")
        print(json.dumps({"result": "PASS", "finding_ids": [], "operation": "noop"}, separators=(",", ":")))
        return 0
    if os.environ.get("GITHUB_EVENT_NAME") == "issue_comment" and not command_source[0]:
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result="PASS", operation="noop")
        print(json.dumps({"result": "PASS", "finding_ids": [], "operation": "noop"}, separators=(",", ":")))
        return 0
    command = command_source[0] if command_source else None
    if command is not None and command.action == "promote":
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result="PASS", operation="noop")
        print(json.dumps({"result": "PASS", "finding_ids": [], "operation": "noop"}, separators=(",", ":")))
        return 0
    labels = _labels(issue)
    if "type:engineering" in labels:
        from .state import ready_findings

        findings = ready_findings(contract, policy, ".") if contract.get("status") == "contracted" else []
        try:
            _promotion_chain(
                api, issue, contract, policy, repository_id, full_name, ".",
                command.source_comment_id if command is not None and command.action == "ready" else 0,
            )
        except GovernanceError as error:
            findings.append(error.finding)
        operation = command.action if command is not None else "promotion-target-verification"
        mutation = mode in {"warn", "enforce"} and command is not None and command.action == "ready"
        result = gate_result(findings, operation=operation)
        _workflow_output(mode=mode, mutation=mutation, issue_number=issue_number, gate_result=result["result"], operation=operation)
        print(json.dumps(result, separators=(",", ":")))
        return 0
    comments = api.list_comments(issue_number)
    findings = candidate_gate(
        contract,
        policy,
        ".",
        issue_author=issue.get("user", {}).get("login", ""),
        comments=comments,
        api=api,
        repository_id=repository_id,
        issue_id=issue.get("id"),
        repository=full_name,
        issue_number=issue_number,
    )
    if command is not None:
        findings = [
            finding
            for finding in findings
            if finding.id not in {"GATE-REVIEW-REQUIRED", "GATE-APPROVAL-REQUIRED", "ATTESTATION-CHAIN-MISSING"}
        ]
    result = gate_result(findings, operation=command.action if command else "gate")
    mutation = mode in {"warn", "enforce"} and (command is not None or os.environ.get("GITHUB_EVENT_NAME") == "issues")
    _workflow_output(
        mode=mode,
        mutation=mutation,
        issue_number=issue_number,
        gate_result=result["result"],
        operation=command.action if command else "gate",
    )
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _write_phase(event: dict[str, Any]) -> int:
    from .attestations import candidate_gate, execute_attestation

    api, policy, repository_id, full_name, issue_number, issue, contract, command_source = _workflow_context(event)
    mode = policy["rollout_mode"]
    if mode not in {"warn", "enforce"} or contract is None:
        raise GovernanceError("EVENT-WRITE-MODE", "write phase is unavailable in the current rollout mode", code=3)
    command, source = command_source or (None, None)
    if mode == "warn":
        delivery = event.get("comment", {}).get("id", event.get("action", "dispatch"))
        marker = f"<!-- github-governance-warning:v1:{issue_number}:{delivery} -->"
        if not any(comment.get("body") == marker for comment in api.list_comments(issue_number)):
            api.create_comment(issue_number, marker)
        print(json.dumps({"result": "PASS", "operation": "warning-only"}, separators=(",", ":")))
        return 0
    if command is not None and source is not None:
        run_id = _positive_integer(os.environ.get("GITHUB_RUN_ID"), "EVENT-RUN", "workflow run id")
        run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{full_name}/actions/runs/{run_id}"
        if command.action == "ready":
            result = execute_ready(
                api,
                repository_id,
                full_name,
                issue_number,
                command,
                source,
                policy,
                run_id,
                run_url,
                repository_root=".",
                workflow_head_sha=os.environ.get("GITHUB_SHA"),
            )
            print(json.dumps({"result": "PASS", **result}, separators=(",", ":")))
            return 0
        result = execute_attestation(
            api,
            repository_id,
            full_name,
            issue_number,
            command,
            source,
            policy,
            run_id,
            run_url,
            repository_root=".",
            workflow_head_sha=os.environ.get("GITHUB_SHA"),
        )
        issue = api.get_issue(issue_number)
        from .contract import extract_contract

        contract = extract_contract(issue["body"]).contract
    validated_snapshot = _issue_snapshot(issue)
    comments = api.list_comments(issue_number)
    findings = candidate_gate(
        contract,
        policy,
        ".",
        issue_author=issue.get("user", {}).get("login", ""),
        comments=comments,
        api=api,
        repository_id=repository_id,
        issue_id=issue.get("id"),
        repository=full_name,
        issue_number=issue_number,
        current_run_id=_positive_integer(os.environ.get("GITHUB_RUN_ID"), "EVENT-RUN", "workflow run id"),
    )
    labels = [label for label in validated_snapshot["labels"] if label not in {"state:draft", "state:gate-failed", "state:gate-passed"}]
    labels.append("state:gate-passed" if not findings else "state:gate-failed")
    labels = sorted(labels)
    _require_prewrite_snapshot(validated_snapshot, api.get_issue(issue_number))
    mutation_snapshot = _require_label_readback(
        validated_snapshot,
        api.update_issue(issue_number, labels=labels),
        tuple(labels),
    )
    readback_snapshot = _require_label_readback(
        validated_snapshot,
        api.get_issue(issue_number),
        tuple(labels),
    )
    if readback_snapshot != mutation_snapshot:
        raise GovernanceError("EVENT-LABEL-READBACK", "Issue changed between label mutation response and read-back", code=3)
    print(json.dumps({"result": "PASS" if not findings else "FAIL", "finding_ids": sorted({item.id for item in findings})}, separators=(",", ":")))
    return 0


def _promotion_read_phase(event: dict[str, Any]) -> int:
    from .attestations import candidate_gate
    from .audit import gate_result

    api, policy, repository_id, full_name, issue_number, issue, contract, command_source = _workflow_context(event)
    mode = policy["rollout_mode"]
    if contract is None:
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result="PASS", operation="noop")
        print(json.dumps({"result": "PASS", "finding_ids": [], "operation": "noop"}, separators=(",", ":")))
        return 0
    command = command_source[0] if command_source else None
    labels = _labels(issue)
    if os.environ.get("GITHUB_EVENT_NAME") == "issues" and "type:engineering" in labels:
        findings = []
        try:
            _promotion_chain(api, issue, contract, policy, repository_id, full_name, ".")
        except GovernanceError as error:
            findings.append(error.finding)
        result = gate_result(findings, operation="promotion-target-verification")
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result=result["result"], operation="promotion-target-verification")
        print(json.dumps(result, separators=(",", ":")))
        return 0
    if command is None or command.action != "promote":
        _workflow_output(mode=mode, mutation=False, issue_number=issue_number, gate_result="PASS", operation="noop")
        print(json.dumps({"result": "PASS", "finding_ids": [], "operation": "noop"}, separators=(",", ":")))
        return 0
    comments = api.list_comments(issue_number)
    findings = candidate_gate(
        contract,
        policy,
        ".",
        issue_author=issue.get("user", {}).get("login", ""),
        comments=comments,
        api=api,
        repository_id=repository_id,
        issue_id=issue.get("id"),
        repository=full_name,
        issue_number=issue_number,
    )
    result = gate_result(findings, operation="promote")
    mutation = mode in {"warn", "enforce"}
    _workflow_output(mode=mode, mutation=mutation, issue_number=issue_number, gate_result=result["result"], operation="promote")
    print(json.dumps(result, separators=(",", ":")))
    return 0


def _promotion_write_phase(event: dict[str, Any]) -> int:
    api, policy, repository_id, full_name, issue_number, issue, contract, command_source = _workflow_context(event)
    mode = policy["rollout_mode"]
    if mode not in {"warn", "enforce"} or contract is None:
        raise GovernanceError("EVENT-WRITE-MODE", "promotion write phase is unavailable", code=3)
    command, source = command_source or (None, None)
    if command is None or command.action != "promote" or source is None:
        raise GovernanceError("PROMOTION-COMMAND", "promotion write requires an exact source command", code=3)
    if mode == "warn":
        delivery = event.get("comment", {}).get("id", event.get("action", "dispatch"))
        marker = f"<!-- github-governance-warning:v1:{issue_number}:{delivery}:promote -->"
        if not any(comment.get("body") == marker for comment in api.list_comments(issue_number)):
            api.create_comment(issue_number, marker)
        print(json.dumps({"result": "PASS", "operation": "warning-only"}, separators=(",", ":")))
        return 0
    run_id = _positive_integer(os.environ.get("GITHUB_RUN_ID"), "EVENT-RUN", "workflow run id")
    run_url = f"{os.environ.get('GITHUB_SERVER_URL', 'https://github.com')}/{full_name}/actions/runs/{run_id}"
    result = execute_promotion(
        api,
        repository_id,
        full_name,
        issue_number,
        command,
        source,
        policy,
        run_id,
        run_url,
        repository_root=".",
        workflow_head_sha=os.environ.get("GITHUB_SHA"),
    )
    print(json.dumps({"result": "PASS", **result}, separators=(",", ":")))
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m github_governance.events")
    parser.add_argument("--phase", choices=("read", "write", "promotion-read", "promotion-write"), required=True)
    arguments = parser.parse_args(argv)
    try:
        event = _event_file()
        if arguments.phase == "read":
            return _read_phase(event)
        if arguments.phase == "write":
            return _write_phase(event)
        if arguments.phase == "promotion-read":
            return _promotion_read_phase(event)
        return _promotion_write_phase(event)
    except GovernanceError as error:
        print(json.dumps({"result": "FAIL", "finding_ids": [error.finding.id]}, separators=(",", ":")), file=sys.stderr)
        return error.code


if __name__ == "__main__":
    raise SystemExit(main())

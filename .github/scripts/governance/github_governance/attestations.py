"""Deterministic review/approval targets and recoverable receipt protocol."""

from __future__ import annotations

import copy
import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .canonical import canonicalize, contract_hash, sha256_tagged, subject_digest
from .contract import extract_contract
from .errors import Finding, GovernanceError
from .events import AuthorizedCommand
from .policy import authorized, normalize_login
from .schema_validation import schema_findings
from .semantic import semantic_findings


INTENT = "intent"
COMPLETED = "completed"
BOT_LOGIN = "github-actions[bot]"
WORKFLOW_PATH = ".github/workflows/02-engineering-governance.yml"
_RECEIPT_PREFIX = "<!-- github-governance-receipt:v1:"
_RECEIPT = re.compile(r"\A<!-- github-governance-receipt:v1:(\{.*\}) -->\Z", re.DOTALL)
_HASH = re.compile(r"\Asha256:[0-9a-f]{64}\Z")
_OPERATION_ID = re.compile(r"\A[0-9a-f]{64}\Z")
_HEAD_SHA = re.compile(r"\A[0-9a-f]{40}\Z")
_REPOSITORY = re.compile(r"\A[^/\s]+/[^/\s]+\Z")

OPERATION_INPUT_KEYS = frozenset(
    {
        "repository_id",
        "issue_id",
        "action",
        "source_comment_id",
        "source_body_digest",
        "actor",
        "revision",
        "subject_digest",
        "review_block_digest",
        "expected_before_hash",
    }
)
INTENT_KEYS = frozenset(
    {
        "version",
        "phase",
        "operation_id",
        *OPERATION_INPUT_KEYS,
        "repository",
        "issue_number",
        "target_hash",
        "run_id",
        "run_url",
        "workflow_path",
        "head_sha",
        "event",
    }
)
OPERATION_KEYS = INTENT_KEYS - {"phase"}
COMPLETED_KEYS = frozenset(
    {
        "version",
        "phase",
        "operation_id",
        "intent_comment_id",
        "target_hash",
        "result",
        "run_id",
        "run_url",
    }
)


@dataclass(frozen=True)
class SourceComment:
    id: int
    body: str
    actor: str
    actor_type: str
    created_at: str
    updated_at: str

    @classmethod
    def from_api(cls, value: dict[str, Any]) -> "SourceComment":
        user = value.get("user")
        if not isinstance(user, dict):
            raise GovernanceError("ATTESTATION-SOURCE-SHAPE", "source comment user is missing")
        identifier = value.get("id")
        if isinstance(identifier, bool) or not isinstance(identifier, int) or identifier < 1:
            raise GovernanceError("ATTESTATION-SOURCE-SHAPE", "source comment id is invalid")
        fields = (value.get("body"), user.get("login"), user.get("type"), value.get("created_at"), value.get("updated_at"))
        if any(not isinstance(field, str) or not field for field in fields):
            raise GovernanceError("ATTESTATION-SOURCE-SHAPE", "source comment fields are missing or invalid")
        return cls(identifier, *fields)

    @property
    def body_digest(self) -> str:
        return "sha256:" + hashlib.sha256(self.body.encode("utf-8", errors="strict")).hexdigest()

    def verify_unchanged(self, expected: "SourceComment" | None = None) -> None:
        if self.actor_type != "User":
            raise GovernanceError("ATTESTATION-SOURCE-ACTOR", "source command must be authored by a human User")
        if self.created_at != self.updated_at:
            raise GovernanceError("ATTESTATION-SOURCE-CHANGED", "source command was edited after creation", code=3)
        if expected is not None and self != expected:
            raise GovernanceError("ATTESTATION-SOURCE-CHANGED", "source command changed or was replaced", code=3)


def _require_keys(value: dict[str, Any], expected: frozenset[str], kind: str) -> None:
    actual = frozenset(value)
    if actual != expected:
        missing = sorted(expected - actual)
        extra = sorted(actual - expected)
        raise GovernanceError(
            "ATTESTATION-RECEIPT-SHAPE",
            f"{kind} receipt keys are invalid (missing={missing}, extra={extra})",
            code=3,
        )


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _operation_input(value: dict[str, Any]) -> dict[str, Any]:
    return {key: value[key] for key in sorted(OPERATION_INPUT_KEYS)}


def compute_operation_id(value: dict[str, Any]) -> str:
    """Recompute the only valid operation ID from the plan-defined operation input."""

    if not OPERATION_INPUT_KEYS.issubset(value):
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "operation input is incomplete", code=3)
    return hashlib.sha256(canonicalize(_operation_input(value))).hexdigest()


def _validate_intent(value: dict[str, Any]) -> None:
    _require_keys(value, INTENT_KEYS, "intent")
    if value["version"] != 1 or value["phase"] != INTENT:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent version or phase is invalid", code=3)
    if value["action"] not in {"review", "approve"}:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent action is invalid", code=3)
    for key in ("repository_id", "issue_id", "source_comment_id", "revision", "issue_number", "run_id"):
        if not _positive_int(value[key]):
            raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", f"intent {key} is invalid", code=3)
    if not isinstance(value["repository"], str) or _REPOSITORY.fullmatch(value["repository"]) is None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent repository is invalid", code=3)
    try:
        if normalize_login(value["actor"]) != value["actor"]:
            raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent actor is not normalized", code=3)
    except (GovernanceError, TypeError) as error:
        if isinstance(error, GovernanceError) and error.finding.id == "ATTESTATION-RECEIPT-SHAPE":
            raise
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent actor is invalid", code=3) from error
    for key in ("source_body_digest", "subject_digest", "expected_before_hash", "target_hash"):
        if not isinstance(value[key], str) or _HASH.fullmatch(value[key]) is None:
            raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", f"intent {key} is invalid", code=3)
    review_digest = value["review_block_digest"]
    if value["action"] == "review":
        if not isinstance(review_digest, str) or _HASH.fullmatch(review_digest) is None:
            raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "review intent digest is invalid", code=3)
    elif review_digest is not None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "approval intent review digest must be null", code=3)
    if not isinstance(value["operation_id"], str) or _OPERATION_ID.fullmatch(value["operation_id"]) is None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "operation ID is invalid", code=3)
    if value["operation_id"] != compute_operation_id(value):
        raise GovernanceError("ATTESTATION-OPERATION-ID", "operation ID does not bind the exact operation input", code=3)
    if not isinstance(value["run_url"], str) or not value["run_url"]:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent run URL is invalid", code=3)
    if value["workflow_path"] != WORKFLOW_PATH:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent workflow path is invalid", code=3)
    if not isinstance(value["head_sha"], str) or _HEAD_SHA.fullmatch(value["head_sha"]) is None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent head SHA is invalid", code=3)
    if value["event"] not in {"issue_comment", "workflow_dispatch"}:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "intent event is invalid", code=3)


def _validate_completed(value: dict[str, Any]) -> None:
    _require_keys(value, COMPLETED_KEYS, "completed")
    if value["version"] != 1 or value["phase"] != COMPLETED:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed version or phase is invalid", code=3)
    if not isinstance(value["operation_id"], str) or _OPERATION_ID.fullmatch(value["operation_id"]) is None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed operation ID is invalid", code=3)
    if not _positive_int(value["intent_comment_id"]) or not _positive_int(value["run_id"]):
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed receipt IDs are invalid", code=3)
    if not isinstance(value["target_hash"], str) or _HASH.fullmatch(value["target_hash"]) is None:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed target hash is invalid", code=3)
    if value["result"] not in {"applied", "recovered"}:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed result is invalid", code=3)
    if not isinstance(value["run_url"], str) or not value["run_url"]:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "completed run URL is invalid", code=3)


def _validate_operation(value: dict[str, Any]) -> None:
    _require_keys(value, OPERATION_KEYS, "operation")
    _validate_intent({**value, "phase": INTENT})


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt contains a duplicate JSON key", code=3)
        value[key] = item
    return value


def _evidence_index(contract: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        item["id"]: item
        for item in contract.get("evidence", [])
        if isinstance(item, dict) and isinstance(item.get("id"), str)
    }


def build_target(contract: dict[str, Any], command: AuthorizedCommand) -> dict[str, Any]:
    """Build the sole deterministic Slice 4 target without changing meaning."""

    if contract.get("status") != "candidate":
        raise GovernanceError("ATTESTATION-STATUS", "review and approval only apply to Candidate contracts", code=3)
    digest = subject_digest(contract)
    if command.revision != contract.get("issue_revision") or command.subject_digest != digest:
        raise GovernanceError("ATTESTATION-STALE", "command attests an earlier Candidate subject", code=3)
    target = copy.deepcopy(contract)
    evidence = _evidence_index(contract)
    if command.action == "review":
        review = target.get("review")
        if not isinstance(review, dict) or review.get("result") != "pending":
            raise GovernanceError("ATTESTATION-REVIEW-STATE", "review must be pending before attestation", code=3)
        for reference in review.get("evidence_refs", []):
            if reference not in evidence:
                raise GovernanceError("ATTESTATION-REVIEW-EVIDENCE", "review evidence must already exist in the normative evidence array")
        review["reviewed_by"] = command.actor
        review["result"] = "fail" if any(
            isinstance(finding, dict) and finding.get("disposition") == "open"
            for finding in review.get("findings", [])
        ) else "pass"
        review["subject_revision"] = command.revision
        review["subject_digest"] = digest
        expected = sha256_tagged(review)
        if command.review_block_digest != expected:
            raise GovernanceError("ATTESTATION-REVIEW-DIGEST", "review command does not bind the deterministic target review block")
    elif command.action == "approve":
        approval = target.get("approval")
        if not isinstance(approval, dict) or approval.get("decision") != "pending":
            raise GovernanceError("ATTESTATION-APPROVAL-STATE", "approval must be pending before attestation", code=3)
        reference = approval.get("evidence_ref")
        record = evidence.get(reference) if isinstance(reference, str) else None
        if record is None or record.get("type") != "human-decision":
            raise GovernanceError(
                "ATTESTATION-APPROVAL-EVIDENCE",
                "approval evidence must pre-exist as unique normative human-decision evidence",
            )
        approval["decision"] = "approved"
        approval["actor"] = command.actor
        approval["decided_at"] = command.source_created_at
        approval["subject_revision"] = command.revision
        approval["subject_digest"] = digest
    else:
        raise GovernanceError("ATTESTATION-ACTION", "unsupported Slice 4 attestation action", code=2)
    if subject_digest(target) != digest:
        raise GovernanceError("ATTESTATION-SUBJECT-CHANGED", "attestation target changed the normative subject", code=3)
    return target


def render_contract_body(raw_body: bytes | str, target: dict[str, Any]) -> str:
    extracted = extract_contract(raw_body)
    raw = extracted.body.encode("utf-8")
    payload = json.dumps(target, ensure_ascii=False, indent=2, allow_nan=False).encode("utf-8")
    rendered = raw[: extracted.payload_start] + payload + raw[extracted.payload_end :]
    return rendered.decode("utf-8", errors="strict")


def build_operation(
    repository_id: int,
    issue_id: int,
    repository: str,
    issue_number: int,
    before: dict[str, Any],
    target: dict[str, Any],
    command: AuthorizedCommand,
    source: SourceComment,
    run_id: int,
    run_url: str,
    *,
    workflow_head_sha: str | None = None,
) -> dict[str, Any]:
    source.verify_unchanged()
    actor = normalize_login(command.actor)
    operation_input = {
        "repository_id": repository_id,
        "issue_id": issue_id,
        "action": command.action,
        "source_comment_id": source.id,
        "source_body_digest": source.body_digest,
        "actor": actor,
        "revision": command.revision,
        "subject_digest": command.subject_digest,
        "review_block_digest": command.review_block_digest,
        "expected_before_hash": contract_hash(before),
    }
    operation = {
        "version": 1,
        "operation_id": compute_operation_id(operation_input),
        **operation_input,
        "repository": repository,
        "issue_number": issue_number,
        "target_hash": contract_hash(target),
        "run_id": run_id,
        "run_url": run_url,
        "workflow_path": WORKFLOW_PATH,
        "head_sha": workflow_head_sha or before.get("base_commit"),
        "event": getattr(command, "event_name", "issue_comment"),
    }
    _validate_operation(operation)
    return operation


def render_receipt(receipt: dict[str, Any]) -> str:
    if not isinstance(receipt, dict):
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt must be an object", code=3)
    if receipt.get("phase") == INTENT:
        _validate_intent(receipt)
    elif receipt.get("phase") == COMPLETED:
        _validate_completed(receipt)
    else:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt phase is invalid", code=3)
    encoded = canonicalize(receipt).decode("utf-8")
    if "--" in encoded:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt cannot be represented safely", code=3)
    return f"{_RECEIPT_PREFIX}{encoded} -->"


def parse_receipt(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    match = _RECEIPT.fullmatch(body)
    if match is None:
        return None
    try:
        value = json.loads(match.group(1), object_pairs_hook=_strict_pairs)
    except GovernanceError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt marker contains invalid JSON", code=3) from error
    if not isinstance(value, dict):
        raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "receipt marker has an invalid shape", code=3)
    if render_receipt(value) != body:
        raise GovernanceError("ATTESTATION-RECEIPT-CANONICAL", "receipt marker is not canonical", code=3)
    return value


def _comment_id(comment: dict[str, Any]) -> int:
    identifier = comment.get("id")
    if not _positive_int(identifier):
        raise GovernanceError("ATTESTATION-RECEIPT-ID", "receipt comment ID is invalid", code=3)
    return identifier


def _bot_comment(comment: dict[str, Any], receipt: dict[str, Any]) -> None:
    user = comment.get("user")
    if not isinstance(user, dict) or user.get("login") != BOT_LOGIN or user.get("type") != "Bot":
        raise GovernanceError("ATTESTATION-RECEIPT-ACTOR", "governance receipt is not bot-authored", code=3)
    if parse_receipt(comment.get("body")) != receipt:
        raise GovernanceError("ATTESTATION-RECEIPT-READBACK", "governance receipt read-back differs", code=3)


def _receipt_records(comments: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    records: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for comment in comments:
        body = comment.get("body")
        if not isinstance(body, str) or _RECEIPT_PREFIX not in body:
            continue
        user = comment.get("user")
        is_bot = isinstance(user, dict) and user.get("login") == BOT_LOGIN and user.get("type") == "Bot"
        try:
            receipt = parse_receipt(body)
        except GovernanceError:
            if is_bot:
                raise
            continue
        if receipt is None:
            if is_bot:
                raise GovernanceError("ATTESTATION-RECEIPT-SHAPE", "bot receipt marker is malformed", code=3)
            continue
        _comment_id(comment)
        records.append((comment, receipt))
    return records


def validate_receipt_chain(
    comments: list[dict[str, Any]], operation: dict[str, Any]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    _validate_operation(operation)
    records = _receipt_records(comments)
    relevant: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for record in records:
        receipt = record[1]
        if receipt["phase"] == INTENT and (
            receipt["source_comment_id"] == operation["source_comment_id"]
            and receipt["action"] == operation["action"]
        ):
            if receipt["operation_id"] != operation["operation_id"]:
                _bot_comment(*record)
                raise GovernanceError("ATTESTATION-RECEIPT-CONFLICT", "source command has a conflicting intent receipt", code=3)
            relevant.append(record)
        elif receipt["operation_id"] == operation["operation_id"]:
            relevant.append(record)
    intents = [record for record in relevant if record[1]["phase"] == INTENT]
    completions = [record for record in relevant if record[1]["phase"] == COMPLETED]
    if len(intents) > 1 or len(completions) > 1:
        raise GovernanceError("ATTESTATION-RECEIPT-CONFLICT", "duplicate or conflicting governance receipt", code=3)
    intent_comment, intent = intents[0] if intents else (None, None)
    completed_comment, completion = completions[0] if completions else (None, None)
    if intent is not None:
        _bot_comment(intent_comment, intent)
        for comment, candidate in records:
            if (
                candidate["phase"] == COMPLETED
                and candidate["intent_comment_id"] == _comment_id(intent_comment)
                and candidate["operation_id"] != operation["operation_id"]
            ):
                _bot_comment(comment, candidate)
                raise GovernanceError("ATTESTATION-RECEIPT-CONFLICT", "completed receipt conflicts with its named intent", code=3)
        expected = {**operation, "phase": INTENT}
        for key in INTENT_KEYS - {"run_id", "run_url"}:
            if intent[key] != expected[key]:
                raise GovernanceError("ATTESTATION-RECEIPT-CONFLICT", "intent receipt conflicts with the reconstructed operation", code=3)
        if _comment_id(intent_comment) <= operation["source_comment_id"]:
            raise GovernanceError("ATTESTATION-RECEIPT-ORDER", "intent receipt must follow its source command", code=3)
    if completion is not None:
        _bot_comment(completed_comment, completion)
        if intent is None:
            raise GovernanceError("ATTESTATION-COMPLETED-WITHOUT-INTENT", "completed receipt has no authorizing intent", code=3)
        expected_completion = {
            "version": 1,
            "phase": COMPLETED,
            "operation_id": operation["operation_id"],
            "intent_comment_id": _comment_id(intent_comment),
            "target_hash": operation["target_hash"],
            "result": completion["result"],
            "run_id": intent["run_id"],
            "run_url": intent["run_url"],
        }
        if completion != expected_completion:
            raise GovernanceError("ATTESTATION-COMPLETED-MISMATCH", "completed receipt conflicts with the intent or target", code=3)
        if _comment_id(completed_comment) <= _comment_id(intent_comment):
            raise GovernanceError("ATTESTATION-RECEIPT-ORDER", "completed receipt must follow its intent", code=3)
    return intent_comment, completed_comment


def classify_recovery(intent: bool, current: str, completed: bool) -> str:
    if current not in {"before", "target", "unexpected"}:
        raise GovernanceError("ATTESTATION-RECOVERY-SHAPE", "unknown recovery body relationship", code=3)
    if current == "unexpected":
        return "conflict"
    if not intent:
        return "start" if current == "before" and not completed else "conflict"
    if current == "before":
        return "write-target" if not completed else "conflict"
    return "success" if completed else "write-completed"


def _verify_run(api: Any, receipt: dict[str, Any], *, current_run_id: int | None = None) -> None:
    _validate_intent(receipt)
    run = api.get_workflow_run(receipt["run_id"])
    if run.get("id") != receipt["run_id"] or run.get("html_url") != receipt["run_url"]:
        raise GovernanceError("ATTESTATION-RUN-MISMATCH", "workflow run ID or URL does not match the receipt", code=3)
    repository = run.get("repository")
    if not isinstance(repository, dict) or repository.get("full_name") != receipt["repository"]:
        raise GovernanceError("ATTESTATION-RUN-REPOSITORY", "workflow run repository does not match the intent", code=3)
    if run.get("path") != receipt["workflow_path"] or receipt["workflow_path"] != WORKFLOW_PATH:
        raise GovernanceError("ATTESTATION-RUN-WORKFLOW", "workflow run path is not the fixed governance workflow", code=3)
    if run.get("head_sha") != receipt["head_sha"]:
        raise GovernanceError("ATTESTATION-RUN-HEAD", "workflow run head SHA does not match the intent", code=3)
    for key in ("actor", "triggering_actor"):
        actor = run.get(key)
        try:
            login = normalize_login(actor.get("login")) if isinstance(actor, dict) else None
        except GovernanceError:
            login = None
        if login != receipt["actor"]:
            raise GovernanceError("ATTESTATION-RUN-ACTOR", f"workflow run {key} does not match the intent actor", code=3)
    if run.get("event") != receipt["event"]:
        raise GovernanceError("ATTESTATION-RUN-EVENT", "workflow run event does not match the intent", code=3)
    status = run.get("status")
    conclusion = run.get("conclusion")
    if status == "completed" and conclusion == "success":
        return
    if current_run_id == receipt["run_id"] and status in {"queued", "in_progress"} and conclusion is None:
        return
    if status == "completed" and conclusion is None:
        raise GovernanceError("ATTESTATION-RUN-INCOMPLETE", "workflow run has no successful conclusion", code=3)
    if status == "completed":
        raise GovernanceError("ATTESTATION-RUN-FAILED", "workflow run did not complete successfully", code=3)
    raise GovernanceError("ATTESTATION-RUN-INCOMPLETE", "prior workflow run is not complete", code=3)


def _create_receipt(api: Any, issue_number: int, receipt: dict[str, Any], operation: dict[str, Any]) -> dict[str, Any]:
    body = render_receipt(receipt)
    try:
        created = api.create_comment(issue_number, body)
    except GovernanceError as error:
        if error.code != 4:
            raise
        records = _receipt_records(api.list_comments(issue_number))
        candidates = [
            item for item in records
            if item[1]["operation_id"] == operation["operation_id"] and item[1]["phase"] == receipt["phase"]
        ]
        if len(candidates) != 1:
            raise
        created = candidates[0][0]
    identifier = _comment_id(created)
    read_back = api.get_comment(identifier)
    _bot_comment(read_back, receipt)
    return read_back


def _contract_relation(current: dict[str, Any], operation: dict[str, Any]) -> str:
    digest = contract_hash(current)
    if digest == operation["expected_before_hash"]:
        return "before"
    if digest == operation["target_hash"]:
        return "target"
    return "unexpected"


def _find_source(comments: list[dict[str, Any]], source: SourceComment) -> SourceComment:
    matches = [comment for comment in comments if comment.get("id") == source.id]
    if len(matches) != 1:
        raise GovernanceError("ATTESTATION-SOURCE-MISSING", "source command is missing or duplicated", code=3)
    current = SourceComment.from_api(matches[0])
    current.verify_unchanged(source)
    return current


def _issue_labels(issue: dict[str, Any]) -> tuple[str, ...]:
    labels = issue.get("labels")
    if not isinstance(labels, list):
        raise GovernanceError("ATTESTATION-ISSUE-LABELS", "Issue labels are missing or invalid", code=3)
    names: list[str] = []
    for label in labels:
        name = label.get("name") if isinstance(label, dict) else label
        if not isinstance(name, str) or not name or name in names:
            raise GovernanceError("ATTESTATION-ISSUE-LABELS", "Issue labels are missing, duplicated, or invalid", code=3)
        names.append(name)
    return tuple(sorted(names))


def _require_issue_contract(
    issue: dict[str, Any],
    *,
    issue_id: int,
    body: str,
    labels: tuple[str, ...],
    target_hash: str,
    finding_id: str,
) -> None:
    try:
        current_hash = contract_hash(extract_contract(issue.get("body", "")).contract)
        current_labels = _issue_labels(issue)
    except GovernanceError as error:
        raise GovernanceError(finding_id, "Issue contract read-back is invalid", code=3) from error
    if (
        issue.get("id") != issue_id
        or issue.get("body") != body
        or current_labels != labels
        or current_hash != target_hash
    ):
        raise GovernanceError(finding_id, "Issue contract changed across the governed mutation", code=3)


def _reset_review(contract: dict[str, Any]) -> dict[str, Any]:
    before = copy.deepcopy(contract)
    review = before["review"]
    review["reviewed_by"] = None
    review["result"] = "pending"
    review["subject_revision"] = None
    review["subject_digest"] = None
    return before


def _reset_approval(contract: dict[str, Any]) -> dict[str, Any]:
    before = copy.deepcopy(contract)
    approval = before["approval"]
    approval["decision"] = "pending"
    approval["actor"] = None
    approval["decided_at"] = None
    approval["subject_revision"] = None
    approval["subject_digest"] = None
    return before


def _expected_operation_from_intent(
    intent: dict[str, Any],
    before: dict[str, Any],
    target: dict[str, Any],
    *,
    repository_id: int | None,
    issue_id: int | None,
    repository: str | None,
    issue_number: int | None,
) -> dict[str, Any]:
    operation = {key: value for key, value in intent.items() if key != "phase"}
    operation.update(
        {
            "repository_id": repository_id,
            "issue_id": issue_id,
            "repository": repository,
            "issue_number": issue_number,
            "expected_before_hash": contract_hash(before),
            "target_hash": contract_hash(target),
            "workflow_path": WORKFLOW_PATH,
        }
    )
    operation["operation_id"] = compute_operation_id(operation)
    _validate_operation(operation)
    return operation


def _validate_source_intent(
    comments: list[dict[str, Any]], intent_comment: dict[str, Any], intent: dict[str, Any]
) -> SourceComment:
    from .events import parse_command

    matches = [comment for comment in comments if comment.get("id") == intent["source_comment_id"]]
    if len(matches) != 1:
        raise GovernanceError("ATTESTATION-SOURCE-MISSING", "receipt source command is missing or duplicated", code=3)
    source = SourceComment.from_api(matches[0])
    source.verify_unchanged()
    command = parse_command(source.body)
    if command is None or (
        command.action,
        command.revision,
        command.subject_digest,
        command.review_block_digest,
    ) != (
        intent["action"],
        intent["revision"],
        intent["subject_digest"],
        intent["review_block_digest"],
    ):
        raise GovernanceError("ATTESTATION-SOURCE-BODY", "receipt is not bound to the exact source command", code=3)
    if source.body_digest != intent["source_body_digest"] or normalize_login(source.actor) != intent["actor"]:
        raise GovernanceError("ATTESTATION-SOURCE-BODY", "receipt source digest or actor differs", code=3)
    if source.id >= _comment_id(intent_comment):
        raise GovernanceError("ATTESTATION-RECEIPT-ORDER", "intent receipt must follow its source command", code=3)
    return source


def _action_chain(
    contract: dict[str, Any],
    comments: list[dict[str, Any]],
    action: str,
    *,
    repository_id: int | None,
    issue_id: int | None,
    repository: str | None,
    issue_number: int | None,
    api: Any | None,
    current_run_id: int | None,
) -> tuple[int, int, int]:
    records = _receipt_records(comments)
    digest = subject_digest(contract)
    intents = [
        item for item in records
        if item[1]["phase"] == INTENT
        and item[1]["action"] == action
        and item[1]["revision"] == contract["issue_revision"]
        and item[1]["subject_digest"] == digest
    ]
    if len(intents) != 1:
        raise GovernanceError("ATTESTATION-CHAIN-MISSING", f"{action} declaration lacks one current intent", code=3)
    intent_comment, intent = intents[0]
    repository_id = intent["repository_id"] if repository_id is None else repository_id
    issue_id = intent["issue_id"] if issue_id is None else issue_id
    repository = intent["repository"] if repository is None else repository
    issue_number = intent["issue_number"] if issue_number is None else issue_number
    if action == "approve":
        target = contract
        before = _reset_approval(target)
    else:
        target = _reset_approval(contract) if contract["approval"].get("decision") == "approved" else contract
        before = _reset_review(target)
    operation = _expected_operation_from_intent(
        intent,
        before,
        target,
        repository_id=repository_id,
        issue_id=issue_id,
        repository=repository,
        issue_number=issue_number,
    )
    _validate_source_intent(comments, intent_comment, intent)
    _, completed_comment = validate_receipt_chain(comments, operation)
    if completed_comment is None:
        raise GovernanceError("ATTESTATION-CHAIN-MISSING", f"{action} declaration lacks a completed receipt", code=3)
    if action == "review" and intent["review_block_digest"] != sha256_tagged(target["review"]):
        raise GovernanceError("ATTESTATION-REVIEW-DIGEST", "review receipt does not bind the current review declaration", code=3)
    declared_actor = target["review"].get("reviewed_by") if action == "review" else target["approval"].get("actor")
    if not isinstance(declared_actor, str) or normalize_login(declared_actor) != intent["actor"]:
        raise GovernanceError("ATTESTATION-CHAIN-ACTOR", f"{action} receipt actor differs from the declaration", code=3)
    if action == "approve" and target["approval"].get("decided_at") != SourceComment.from_api(
        next(comment for comment in comments if comment.get("id") == intent["source_comment_id"])
    ).created_at:
        raise GovernanceError("ATTESTATION-APPROVAL-TIME", "approval time differs from its source command", code=3)
    if api is not None:
        _verify_run(api, intent, current_run_id=current_run_id)
    return intent["source_comment_id"], _comment_id(intent_comment), _comment_id(completed_comment)


def candidate_gate(
    contract: dict[str, Any],
    policy: dict[str, Any],
    repository_root: str | Path,
    *,
    issue_author: str,
    comments: list[dict[str, Any]],
    api: Any | None = None,
    repository_id: int | None = None,
    issue_id: int | None = None,
    repository: str | None = None,
    issue_number: int | None = None,
    current_run_id: int | None = None,
) -> list[Finding]:
    """Evaluate Candidate PASS/FAIL prerequisites without executing commands."""

    findings = schema_findings(contract, repository_root)
    if not findings:
        findings.extend(semantic_findings(contract, policy, repository_root))
    declared = contract.get("provenance", {}).get("created_by")
    try:
        author_valid = (
            isinstance(declared, str)
            and normalize_login(declared) == normalize_login(issue_author)
            and authorized(policy, "trusted_issue_authors", issue_author)
        )
    except GovernanceError:
        author_valid = False
    if not author_valid:
        findings.append(Finding("GATE-CANDIDATE-AUTHOR", "Candidate author/provenance is not trusted and identical"))
    try:
        _receipt_records(comments)
        review = contract.get("review", {})
        approval = contract.get("approval", {})
        if review.get("result") != "pass":
            findings.append(Finding("GATE-REVIEW-REQUIRED", "current independent review must pass"))
        else:
            review_chain = _action_chain(
                contract,
                comments,
                "review",
                repository_id=repository_id,
                issue_id=issue_id,
                repository=repository,
                issue_number=issue_number,
                api=api,
                current_run_id=current_run_id,
            )
        if approval.get("decision") != "approved":
            findings.append(Finding("GATE-APPROVAL-REQUIRED", "current authorized human approval is required"))
        else:
            approval_chain = _action_chain(
                contract,
                comments,
                "approve",
                repository_id=repository_id,
                issue_id=issue_id,
                repository=repository,
                issue_number=issue_number,
                api=api,
                current_run_id=current_run_id,
            )
            if review.get("result") == "pass" and review_chain[2] >= approval_chain[0]:
                raise GovernanceError("ATTESTATION-CHAIN-ORDER", "approval source must follow the completed review chain", code=3)
    except GovernanceError as error:
        findings.append(error.finding)
    return sorted(findings)


def execute_attestation(
    api: Any,
    repository_id: int,
    repository: str,
    issue_number: int,
    command: AuthorizedCommand,
    source: SourceComment,
    policy: dict[str, Any],
    run_id: int,
    run_url: str,
    *,
    repository_root: str | Path,
    workflow_head_sha: str | None = None,
) -> dict[str, Any]:
    """Execute or resume the intent -> exact target -> completed protocol."""

    comments = api.list_comments(issue_number)
    _receipt_records(comments)
    current_source = _find_source(comments, source)
    if normalize_login(current_source.actor) != normalize_login(command.actor):
        raise GovernanceError("ATTESTATION-SOURCE-ACTOR", "source API actor differs from the authorized event actor", code=3)
    if current_source.body.strip(" \t\r\n") != command.source_body:
        raise GovernanceError("ATTESTATION-SOURCE-BODY", "source API body differs from the authorized exact command", code=3)
    if current_source.created_at != command.source_created_at:
        raise GovernanceError("ATTESTATION-SOURCE-CHANGED", "source command time differs from the authorized event", code=3)
    from .events import parse_command

    reparsed = parse_command(current_source.body)
    if reparsed is None or (
        reparsed.action,
        reparsed.revision,
        reparsed.subject_digest,
        reparsed.review_block_digest,
    ) != (command.action, command.revision, command.subject_digest, command.review_block_digest):
        raise GovernanceError("ATTESTATION-SOURCE-BODY", "source API body is not the authorized exact command", code=3)
    issue = api.get_issue(issue_number)
    if "pull_request" in issue:
        raise GovernanceError("ATTESTATION-PR", "attestations are forbidden on pull requests", code=3)
    issue_id = issue.get("id")
    if not _positive_int(issue_id):
        raise GovernanceError("ATTESTATION-ISSUE-ID", "Issue database ID is invalid", code=4)
    extracted = extract_contract(issue.get("body", ""))
    current = extracted.contract
    validated_labels = _issue_labels(issue)
    actual_author = issue.get("user", {}).get("login")
    declared_author = current.get("provenance", {}).get("created_by")
    if not isinstance(actual_author, str) or not isinstance(declared_author, str) or normalize_login(actual_author) != normalize_login(declared_author):
        raise GovernanceError("ATTESTATION-CANDIDATE-AUTHOR", "Candidate provenance does not match the API Issue author")
    if not authorized(policy, "trusted_issue_authors", actual_author):
        raise GovernanceError("ATTESTATION-CANDIDATE-AUTHOR", "Candidate Issue author is not trusted")
    if command.action == "review":
        if not authorized(policy, "trusted_reviewers", command.actor):
            raise GovernanceError("ATTESTATION-ACTOR-AUTH", "review actor is not trusted")
        if normalize_login(command.actor) == normalize_login(declared_author):
            raise GovernanceError("ATTESTATION-ACTOR-SEPARATION", "reviewer must differ from Candidate author")
        approval_actor = current.get("approval", {}).get("actor")
        if isinstance(approval_actor, str) and normalize_login(command.actor) == normalize_login(approval_actor):
            raise GovernanceError("ATTESTATION-ACTOR-SEPARATION", "reviewer must differ from approval actor")
    elif command.action == "approve":
        if not authorized(policy, "trusted_issue_authors", command.actor):
            raise GovernanceError("ATTESTATION-ACTOR-AUTH", "approval actor is not trusted")
        reviewer = current.get("review", {}).get("reviewed_by")
        if isinstance(reviewer, str) and normalize_login(command.actor) == normalize_login(reviewer):
            raise GovernanceError("ATTESTATION-ACTOR-SEPARATION", "approval actor must differ from reviewer")
    preliminary = schema_findings(current, repository_root)
    if not preliminary:
        preliminary = semantic_findings(current, policy, repository_root)
    if preliminary:
        raise GovernanceError("ATTESTATION-GATE-FAIL", "current Candidate fails deterministic validation")
    head_sha = workflow_head_sha or current.get("base_commit")
    block = current.get("review" if command.action == "review" else "approval", {})
    state = block.get("result" if command.action == "review" else "decision")
    if command.action == "approve":
        review_chain = _action_chain(
            current,
            comments,
            "review",
            repository_id=repository_id,
            issue_id=issue_id,
            repository=repository,
            issue_number=issue_number,
            api=api,
            current_run_id=None,
        )
        if review_chain[2] >= current_source.id:
            raise GovernanceError("ATTESTATION-CHAIN-ORDER", "approval source must follow the completed review chain", code=3)
    if state == "pending":
        before = current
        target = build_target(before, command)
        operation = build_operation(
            repository_id,
            issue_id,
            repository,
            issue_number,
            before,
            target,
            command,
            current_source,
            run_id,
            run_url,
            workflow_head_sha=head_sha,
        )
        intent_comment, completed_comment = validate_receipt_chain(comments, operation)
    else:
        records = _receipt_records(comments)
        matches = [
            item for item in records
            if item[1]["phase"] == INTENT
            and item[1]["action"] == command.action
            and item[1]["source_comment_id"] == source.id
        ]
        if len(matches) != 1:
            raise GovernanceError("ATTESTATION-DECLARATION-FORGED", "non-pending declaration lacks one exact intent receipt", code=3)
        intent_comment, intent = matches[0]
        if command.action == "approve":
            target = current
            before = _reset_approval(target)
        else:
            target = _reset_approval(current) if current["approval"].get("decision") == "approved" else current
            before = _reset_review(target)
        operation = _expected_operation_from_intent(
            intent,
            before,
            target,
            repository_id=repository_id,
            issue_id=issue_id,
            repository=repository,
            issue_number=issue_number,
        )
        _validate_source_intent(comments, intent_comment, intent)
        intent_comment, completed_comment = validate_receipt_chain(comments, operation)
        if command.action == "review" and current["approval"].get("decision") == "approved":
            if completed_comment is None:
                raise GovernanceError("ATTESTATION-CHAIN-MISSING", "superseded review lacks its completed receipt", code=3)
            findings = candidate_gate(
                current,
                policy,
                repository_root,
                issue_author=actual_author,
                comments=comments,
                api=api,
                repository_id=repository_id,
                issue_id=issue_id,
                repository=repository,
                issue_number=issue_number,
            )
            if findings:
                raise GovernanceError("ATTESTATION-RECOVERY-CONFLICT", "later Candidate state is not backed by a complete receipt chain", code=3)
            _verify_run(api, intent)
            return {"result": "idempotent", "operation_id": operation["operation_id"], "target_hash": operation["target_hash"]}
    relation = _contract_relation(current, operation)
    decision = classify_recovery(intent_comment is not None, relation, completed_comment is not None)
    if decision == "conflict":
        raise GovernanceError("ATTESTATION-RECOVERY-CONFLICT", "receipt/body recovery state requires human reconciliation", code=3)
    if intent_comment is not None:
        intent = parse_receipt(intent_comment["body"])
        assert intent is not None
        _verify_run(api, intent, current_run_id=run_id)
    if decision == "success":
        return {"result": "idempotent", "operation_id": operation["operation_id"], "target_hash": operation["target_hash"]}
    if decision == "start":
        intent = {**operation, "phase": INTENT}
        intent_comment = _create_receipt(api, issue_number, intent, operation)
        _verify_run(api, intent, current_run_id=run_id)

    if decision in {"start", "write-target"}:
        target_body = render_contract_body(extracted.body, target)
        reread = api.get_issue(issue_number)
        # Creating the canonical intent comment advances Issue.updated_at.  The
        # comment was read back and bot-validated by _create_receipt (or by the
        # receipt-chain validation on recovery), so only that exact timestamp
        # is an explainable change between the initial snapshot and the write.
        allowed_updated_at = {issue.get("updated_at"), intent_comment.get("created_at")}
        if (
            reread.get("id") != issue_id
            or reread.get("body") != extracted.body
            or reread.get("updated_at") not in allowed_updated_at
            or _issue_labels(reread) != validated_labels
        ):
            raise GovernanceError("ATTESTATION-TOCTOU", "Issue changed between validation and conditional write", code=3)
        try:
            api.update_issue(issue_number, body=target_body)
        except GovernanceError as error:
            if error.code != 4:
                raise
        read_back = api.get_issue(issue_number)
        if (
            read_back.get("id") != issue_id
            or read_back.get("body") != target_body
            or _issue_labels(read_back) != validated_labels
        ):
            raise GovernanceError("ATTESTATION-TARGET-READBACK", "Issue target body read-back differs", code=3)
        if contract_hash(extract_contract(read_back["body"]).contract) != operation["target_hash"]:
            raise GovernanceError("ATTESTATION-TARGET-HASH", "Issue target hash read-back differs", code=3)

    target_body = render_contract_body(extracted.body, target)
    _require_issue_contract(
        api.get_issue(issue_number),
        issue_id=issue_id,
        body=target_body,
        labels=validated_labels,
        target_hash=operation["target_hash"],
        finding_id="ATTESTATION-COMPLETION-TOCTOU",
    )

    result = "recovered" if decision == "write-completed" else "applied"
    completion = {
        "version": 1,
        "phase": COMPLETED,
        "operation_id": operation["operation_id"],
        "intent_comment_id": _comment_id(intent_comment),
        "target_hash": operation["target_hash"],
        "result": result,
        "run_id": intent["run_id"],
        "run_url": intent["run_url"],
    }
    _create_receipt(api, issue_number, completion, operation)
    validate_receipt_chain(api.list_comments(issue_number), operation)
    _require_issue_contract(
        api.get_issue(issue_number),
        issue_id=issue_id,
        body=target_body,
        labels=validated_labels,
        target_hash=operation["target_hash"],
        finding_id="ATTESTATION-COMPLETION-READBACK",
    )
    return {"result": result, "operation_id": operation["operation_id"], "target_hash": operation["target_hash"]}

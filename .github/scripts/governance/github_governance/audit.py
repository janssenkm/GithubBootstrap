"""Stable, non-sensitive local Gate result rendering."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

from .canonical import canonicalize
from .errors import Finding, GovernanceError


TRANSITION_INPUT_KEYS = frozenset(
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
_TRANSITION_PREFIX = "<!-- github-governance-transition:v1:"
_TRANSITION = re.compile(r"\A<!-- github-governance-transition:v1:(\{.*\}) -->\Z", re.DOTALL)


def gate_result(findings: list[Finding], **metadata: object) -> dict[str, object]:
    ordered = sorted(findings)
    return {
        "result": "FAIL" if ordered else "PASS",
        "finding_ids": sorted({finding.id for finding in ordered}),
        "findings": [finding.as_dict() for finding in ordered],
        **metadata,
    }


def transition_operation_id(value: dict[str, Any]) -> str:
    if frozenset(value) != TRANSITION_INPUT_KEYS:
        raise GovernanceError("TRANSITION-OPERATION-SHAPE", "transition operation input is not exact", code=3)
    return hashlib.sha256(canonicalize({key: value[key] for key in sorted(value)})).hexdigest()


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt has a duplicate key", code=3)
        value[key] = item
    return value


def _validate_transition_receipt(value: dict[str, Any]) -> None:
    if value.get("version") != 1 or value.get("phase") not in {"intent", "completed", "link", "provenance-link", "handoff"}:
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt version or phase is invalid", code=3)
    operation_id = value.get("operation_id")
    if not isinstance(operation_id, str) or re.fullmatch(r"[0-9a-f]{64}", operation_id) is None:
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition operation ID is invalid", code=3)
    if value["phase"] == "intent":
        operation = {key: value.get(key) for key in TRANSITION_INPUT_KEYS}
        if transition_operation_id(operation) != operation_id:
            raise GovernanceError("TRANSITION-OPERATION-ID", "transition receipt does not bind its operation input", code=3)
        required = TRANSITION_INPUT_KEYS | {
            "version", "phase", "operation_id", "repository", "issue_number", "target_hash",
            "run_id", "run_url", "workflow_path", "head_sha", "event",
            "baseline_comment_ids", "baseline_updated_at",
        }
        if frozenset(value) != required:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition intent fields are not exact", code=3)
        if value["action"] not in {"promote", "ready"} or value["review_block_digest"] is not None:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition intent action is invalid", code=3)
        expected_workflow = (
            ".github/workflows/03-engineering-promotion.yml"
            if value["action"] == "promote"
            else ".github/workflows/02-engineering-governance.yml"
        )
        if value["workflow_path"] != expected_workflow:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition workflow path is invalid", code=3)
        if value["event"] not in {"issue_comment", "workflow_dispatch"}:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition event is invalid", code=3)
        for key in ("repository_id", "issue_id", "source_comment_id", "revision", "issue_number", "run_id"):
            if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 1:
                raise GovernanceError("TRANSITION-RECEIPT-SHAPE", f"transition {key} is invalid", code=3)
        if not isinstance(value["repository"], str) or value["repository"].count("/") != 1:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition repository is invalid", code=3)
        if not isinstance(value["actor"], str) or not value["actor"] or value["actor"] != value["actor"].lower():
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition actor is invalid", code=3)
        if not isinstance(value["head_sha"], str) or re.fullmatch(r"[0-9a-f]{40}", value["head_sha"]) is None:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition head SHA is invalid", code=3)
        if not isinstance(value["run_url"], str) or not value["run_url"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition run URL is invalid", code=3)
        baseline_ids = value["baseline_comment_ids"]
        if (
            not isinstance(baseline_ids, list)
            or any(not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1 for identifier in baseline_ids)
            or baseline_ids != sorted(set(baseline_ids))
            or value["source_comment_id"] not in baseline_ids
            or not isinstance(value["baseline_updated_at"], str) or not value["baseline_updated_at"]
        ):
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition comment baseline is invalid", code=3)
    elif value["phase"] == "completed":
        required = {
            "version", "phase", "operation_id", "action", "intent_comment_id",
            "source_issue_number", "target_issue_number", "target_hash", "result", "run_id", "run_url",
        }
        if frozenset(value) != required or value["action"] not in {"promote", "ready"}:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition completion fields are not exact", code=3)
        if value["result"] not in {"applied", "recovered"}:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition completion result is invalid", code=3)
        for key in ("intent_comment_id", "source_issue_number", "target_issue_number", "run_id"):
            if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 1:
                raise GovernanceError("TRANSITION-RECEIPT-SHAPE", f"transition completion {key} is invalid", code=3)
    elif value["phase"] == "link":
        required = {
            "version", "phase", "operation_id", "action", "source_issue_number",
            "target_issue_number", "target_issue_id", "target_created_at", "target_baseline_comment_ids",
            "target_hash", "source_comment_id",
        }
        if frozenset(value) != required or value["action"] != "promote":
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion link fields are not exact", code=3)
        for key in ("source_issue_number", "target_issue_number", "target_issue_id", "source_comment_id"):
            if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 1:
                raise GovernanceError("TRANSITION-RECEIPT-SHAPE", f"promotion link {key} is invalid", code=3)
        if not isinstance(value["target_created_at"], str) or not value["target_created_at"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion target creation time is invalid", code=3)
        if value["target_baseline_comment_ids"] != []:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "new promotion target must have an empty comment baseline", code=3)
    elif value["phase"] == "provenance-link":
        required = {
            "version", "phase", "operation_id", "action", "repository",
            "intake_issue_number", "intake_issue_id", "intake_repository_url",
            "intake_author", "intake_author_type", "intake_title", "intake_created_at", "intake_body_digest",
            "intake_updated_at", "intake_state", "intake_labels", "intake_baseline_comment_ids",
            "candidate_issue_number", "target_issue_number", "comment_issue_number",
            "target_hash",
        }
        if frozenset(value) != required or value["action"] != "promote":
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance link fields are not exact", code=3)
        if not isinstance(value["repository"], str) or value["repository"].count("/") != 1:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance repository is invalid", code=3)
        if value["intake_repository_url"] != f"https://api.github.com/repos/{value['repository']}":
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance repository URL is invalid", code=3)
        for key in ("intake_issue_number", "intake_issue_id", "candidate_issue_number", "target_issue_number", "comment_issue_number"):
            if not isinstance(value[key], int) or isinstance(value[key], bool) or value[key] < 1:
                raise GovernanceError("TRANSITION-RECEIPT-SHAPE", f"promotion provenance {key} is invalid", code=3)
        if not isinstance(value["intake_author"], str) or not value["intake_author"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake author is invalid", code=3)
        if value["intake_author_type"] not in {"User", "Bot"}:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake author type is invalid", code=3)
        if not isinstance(value["intake_title"], str) or not value["intake_title"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake title is invalid", code=3)
        if not isinstance(value["intake_created_at"], str) or not value["intake_created_at"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake creation time is invalid", code=3)
        if not isinstance(value["intake_updated_at"], str) or not value["intake_updated_at"]:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake update time is invalid", code=3)
        if value["intake_state"] not in {"open", "closed"}:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake state is invalid", code=3)
        labels = value["intake_labels"]
        allowed_states = {"state:new", "state:triaged", "state:investigating", "state:closed"}
        if (
            not isinstance(labels, list)
            or any(not isinstance(label, str) or not label for label in labels)
            or labels != sorted(set(labels))
            or "type:intake" not in labels
            or len(set(labels) & allowed_states) != 1
        ):
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake labels are invalid", code=3)
        baseline_ids = value["intake_baseline_comment_ids"]
        if (
            not isinstance(baseline_ids, list)
            or any(not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1 for identifier in baseline_ids)
            or baseline_ids != sorted(set(baseline_ids))
        ):
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance Intake comment baseline is invalid", code=3)
        if value["comment_issue_number"] not in {
            value["intake_issue_number"], value["candidate_issue_number"], value["target_issue_number"]
        }:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance comment target is outside its chain", code=3)
        if len({value["intake_issue_number"], value["candidate_issue_number"], value["target_issue_number"]}) != 3:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "promotion provenance contains a link cycle", code=3)
    else:
        required = {
            "version", "phase", "operation_id", "action", "issue_number", "issue_revision",
            "subject_digest", "contract_hash", "base_commit",
        }
        if frozenset(value) != required or value["action"] != "ready":
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "ready handoff fields are not exact", code=3)
        if not isinstance(value["issue_number"], int) or value["issue_number"] < 1:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "ready handoff Issue number is invalid", code=3)
        if not isinstance(value["issue_revision"], int) or value["issue_revision"] < 1:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "ready handoff revision is invalid", code=3)
        if not isinstance(value["base_commit"], str) or re.fullmatch(r"[0-9a-f]{40}", value["base_commit"]) is None:
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "ready handoff base commit is invalid", code=3)
    for key in ("subject_digest", "source_body_digest", "expected_before_hash", "target_hash", "intake_body_digest"):
        if key in value and (not isinstance(value[key], str) or re.fullmatch(r"sha256:[0-9a-f]{64}", value[key]) is None):
            raise GovernanceError("TRANSITION-RECEIPT-SHAPE", f"transition {key} is invalid", code=3)


def render_transition_receipt(receipt: dict[str, Any]) -> str:
    if not isinstance(receipt, dict):
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt must be an object", code=3)
    _validate_transition_receipt(receipt)
    encoded = canonicalize(receipt).decode("utf-8")
    if "--" in encoded:
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt cannot be represented safely", code=3)
    prefix = ""
    if receipt["phase"] == "link":
        prefix = f"Engineering contract promoted from #{receipt['source_issue_number']} to #{receipt['target_issue_number']}.\n\n"
    elif receipt["phase"] == "provenance-link":
        prefix = (
            f"Promotion provenance: Intake #{receipt['intake_issue_number']} -> "
            f"Candidate #{receipt['candidate_issue_number']} -> "
            f"Engineering #{receipt['target_issue_number']}.\n\n"
        )
    elif receipt["phase"] == "completed" and receipt["action"] == "promote":
        prefix = f"Promotion completed as Engineering Issue #{receipt['target_issue_number']}.\n\n"
    elif receipt["phase"] == "completed":
        prefix = f"Ready transition completed for Engineering Issue #{receipt['target_issue_number']}.\n\n"
    elif receipt["phase"] == "handoff":
        prefix = (
            "Local execution handoff (no branch, commit, push, or PR was created).\n\n"
            f"- Engineering Issue: #{receipt['issue_number']}\n"
            f"- Revision: {receipt['issue_revision']}\n"
            f"- Subject: `{receipt['subject_digest']}`\n"
            f"- Contract: `{receipt['contract_hash']}`\n"
            f"- Base commit: `{receipt['base_commit']}`\n\n"
        )
    return f"{prefix}{_TRANSITION_PREFIX}{encoded} -->"


def parse_transition_receipt(body: Any) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    marker = body.find(_TRANSITION_PREFIX)
    match = _TRANSITION.fullmatch(body[marker:]) if marker >= 0 else None
    if match is None:
        return None
    try:
        value = json.loads(match.group(1), object_pairs_hook=_strict_pairs)
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt JSON is invalid", code=3) from error
    if not isinstance(value, dict):
        raise GovernanceError("TRANSITION-RECEIPT-SHAPE", "transition receipt must contain an object", code=3)
    if render_transition_receipt(value) != body:
        raise GovernanceError("TRANSITION-RECEIPT-CANONICAL", "transition receipt is not canonical", code=3)
    return value


def classify_transition_recovery(intent: bool, current: str, completed: bool) -> str:
    if current not in {"before", "target", "unexpected"}:
        raise GovernanceError("TRANSITION-RECOVERY-SHAPE", "unknown transition body relationship", code=3)
    if current == "unexpected":
        return "conflict"
    if not intent:
        return "start" if current == "before" and not completed else "conflict"
    if current == "before":
        return "write-target" if not completed else "conflict"
    return "success" if completed else "write-completed"

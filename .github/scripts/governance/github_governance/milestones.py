"""Deterministic milestone review and human acceptance primitives."""

from __future__ import annotations

from typing import Any, Callable
import json
import re
import io
import zipfile
from datetime import datetime

from .canonical import sha256_tagged
from .errors import GovernanceError
from .github_api import GitHubAPI
from .policy import normalize_login
from .contract import extract_contract

MARKER = re.compile(r"<!-- github-bootstrap:milestone-review (\{[^\r\n]*\}) -->")


def _remote_provenance_valid(api: GitHubAPI, marker: dict[str, Any]) -> bool:
    workflow = marker.get("workflow", {})
    artifact_marker = marker.get("artifact", {})
    run_id = workflow.get("run_id")
    if not isinstance(run_id, int) or isinstance(run_id, bool) or run_id < 1:
        return False
    try:
        run = api.get_workflow_run(run_id)
        artifacts = api.list_workflow_run_artifacts(run_id)
    except GovernanceError:
        return False
    matches = [value for value in artifacts if value.get("id") == artifact_marker.get("id")]
    return (
        len(matches) == 1
        and run.get("run_attempt") == workflow.get("run_attempt")
        and run.get("path") == workflow.get("path") == ".github/workflows/50-milestone-review.yml"
        and run.get("head_sha") == workflow.get("head_sha")
        and run.get("event") == workflow.get("event") == "workflow_dispatch"
        and run.get("actor", {}).get("login") == workflow.get("actor")
        and run.get("repository", {}).get("id") == workflow.get("repository", {}).get("id")
        and run.get("repository", {}).get("full_name") == workflow.get("repository", {}).get("full_name")
        and run.get("status") == "completed" and run.get("conclusion") == workflow.get("conclusion") == "success"
        and matches[0].get("name") == artifact_marker.get("name")
        and matches[0].get("archive_download_url") == artifact_marker.get("archive_download_url")
        and matches[0].get("expired") is False
    )


def parse_review_marker(body: Any) -> dict[str, Any]:
    if not isinstance(body, str):
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review Issue body is invalid")
    matches = MARKER.findall(body)
    if len(matches) != 1:
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review Issue must contain one canonical marker")
    raw = matches[0]
    try:
        marker = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, UnicodeError, ValueError) as error:
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker is invalid") from error
    if not isinstance(marker, dict) or raw != json.dumps(marker, sort_keys=True, separators=(",", ":")):
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker is not canonical JSON")
    if set(marker) != {"schema_version", "repository", "milestone", "snapshot_digest", "result", "workflow", "artifact", "review_issue"}:
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker keys are invalid")
    repository = marker.get("repository")
    milestone = marker.get("milestone")
    workflow = marker.get("workflow")
    artifact = marker.get("artifact")
    review_issue = marker.get("review_issue")
    valid = (
        marker.get("schema_version") == 1
        and isinstance(repository, dict) and set(repository) == {"id", "full_name"}
        and _positive(repository.get("id")) and _repository_name(repository.get("full_name"))
        and isinstance(milestone, dict) and set(milestone) == {"id", "number"}
        and _positive(milestone.get("id")) and _positive(milestone.get("number"))
        and isinstance(marker.get("snapshot_digest"), str)
        and bool(re.fullmatch(r"sha256:[0-9a-f]{64}", marker["snapshot_digest"]))
        and marker.get("result") in {"candidate-complete", "blocked"}
        and isinstance(workflow, dict)
        and set(workflow) == {"path", "run_id", "run_attempt", "head_sha", "event", "actor", "conclusion", "repository"}
        and workflow.get("path") == ".github/workflows/50-milestone-review.yml"
        and _positive(workflow.get("run_id")) and _positive(workflow.get("run_attempt"))
        and isinstance(workflow.get("head_sha"), str) and bool(re.fullmatch(r"[0-9a-f]{40}", workflow["head_sha"]))
        and workflow.get("event") == "workflow_dispatch" and workflow.get("conclusion") == "success"
        and isinstance(workflow.get("actor"), str) and bool(workflow["actor"])
        and workflow.get("repository") == repository
        and isinstance(artifact, dict) and set(artifact) == {"id", "name", "archive_download_url"}
        and _positive(artifact.get("id")) and isinstance(artifact.get("name"), str)
        and artifact.get("name") == f"milestone-review-{milestone.get('number')}-{workflow.get('run_id')}-{workflow.get('run_attempt')}"
        and _https_url(artifact.get("archive_download_url"))
        and isinstance(review_issue, dict) and set(review_issue) == {"id", "number", "url"}
        and _positive(review_issue.get("id")) and _positive(review_issue.get("number"))
        and _https_url(review_issue.get("url"))
    )
    if not valid:
        raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker shape is invalid")
    return marker


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _positive(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def _repository_name(value: Any) -> bool:
    return isinstance(value, str) and value.count("/") == 1 and all(value.split("/"))


def _https_url(value: Any) -> bool:
    return isinstance(value, str) and value.startswith("https://") and "\r" not in value and "\n" not in value


def load_review_artifact(
    archive_bytes: bytes,
    *,
    repository_id: int,
    milestone_id: int,
    milestone_number: int,
    run_id: int,
    run_attempt: int,
    artifact_name: str,
) -> dict[str, Any]:
    """Load the sole canonical review JSON from a bounded, non-link ZIP."""

    if not isinstance(archive_bytes, bytes) or len(archive_bytes) > 5_000_000:
        raise GovernanceError("MILESTONE-ARTIFACT", "review artifact archive is invalid or too large", code=4)
    try:
        with zipfile.ZipFile(io.BytesIO(archive_bytes)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != "milestone-review.json":
                raise GovernanceError("MILESTONE-ARTIFACT", "artifact must contain only milestone-review.json", code=4)
            member = members[0]
            mode = member.external_attr >> 16
            if member.is_dir() or (mode & 0o170000) == 0o120000 or member.file_size > 2_000_000 or member.compress_size > 2_000_000:
                raise GovernanceError("MILESTONE-ARTIFACT", "artifact member type or size is invalid", code=4)
            if member.compress_size == 0 or member.file_size > max(1_000_000, member.compress_size * 100):
                raise GovernanceError("MILESTONE-ARTIFACT", "artifact decompression ratio is unsafe", code=4)
            raw_bytes = archive.read(member)
    except GovernanceError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
        raise GovernanceError("MILESTONE-ARTIFACT", "artifact ZIP is invalid", code=4) from error
    try:
        raw = raw_bytes.decode("utf-8", errors="strict")
        document = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise GovernanceError("MILESTONE-ARTIFACT", "artifact JSON is invalid", code=4) from error
    if not isinstance(document, dict) or raw != json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n":
        raise GovernanceError("MILESTONE-ARTIFACT", "artifact JSON is not canonical", code=4)
    if set(document) != {"schema_version", "captured_at", "run", "artifact", "rollout_mode", "snapshot", "digest"}:
        raise GovernanceError("MILESTONE-ARTIFACT", "artifact envelope keys are invalid", code=4)
    snapshot = document.get("snapshot")
    expected_snapshot_keys = {"schema_version", "repository_id", "milestone", "issues", "pull_requests",
                              "required_checks", "blockers", "result", "candidate_complete", "documentation_status"}
    valid = (
        document.get("schema_version") == 1 and isinstance(document.get("captured_at"), str)
        and document.get("rollout_mode") in {"dry-run", "shadow", "warn", "enforce"}
        and document.get("run") == {"id": run_id, "attempt": run_attempt}
        and document.get("artifact") == {"name": artifact_name}
        and isinstance(snapshot, dict) and set(snapshot) == expected_snapshot_keys
        and snapshot.get("repository_id") == repository_id
        and snapshot.get("milestone", {}).get("id") == milestone_id
        and snapshot.get("milestone", {}).get("number") == milestone_number
        and document.get("digest") == sha256_tagged(snapshot)
    )
    try:
        _timestamp(document.get("captured_at"), "captured_at")
    except GovernanceError:
        valid = False
    if not valid:
        raise GovernanceError("MILESTONE-ARTIFACT", "artifact envelope is inconsistent", code=4)
    return document


def _integer(value: Any, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise GovernanceError("MILESTONE-SHAPE", f"{name} must be a positive integer", code=4)
    return value


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value:
        raise GovernanceError("MILESTONE-SHAPE", f"{name} must be a non-empty string", code=4)
    return value


def _timestamp(value: Any, name: str) -> str:
    text = _text(value, name)
    try:
        datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError as error:
        raise GovernanceError("MILESTONE-SHAPE", f"{name} must be an RFC 3339 timestamp", code=4) from error
    return text


def _labels(item: dict[str, Any]) -> set[str]:
    raw = item.get("labels", [])
    if not isinstance(raw, list):
        raise GovernanceError("MILESTONE-SHAPE", "labels must be a list", code=4)
    result: set[str] = set()
    for label in raw:
        name = label if isinstance(label, str) else label.get("name") if isinstance(label, dict) else None
        result.add(_text(name, "label name"))
    return result


def build_snapshot(
    api: GitHubAPI,
    milestone: dict[str, Any],
    items: list[dict[str, Any]],
    checks_by_pr: dict[int, dict[str, str | dict[str, Any]]],
    required_checks: list[str],
    *,
    repository_id: int = 1,
) -> dict[str, Any]:
    """Build a stable review subject. Runtime envelope fields are deliberately absent."""

    del api
    if len(required_checks) != len(set(required_checks)) or any(not isinstance(name, str) or not name for name in required_checks):
        raise GovernanceError("MILESTONE-CHECK-POLICY", "required milestone checks must be unique non-empty names", code=5)
    milestone_record = {
        "id": _integer(milestone.get("id"), "milestone.id"),
        "number": _integer(milestone.get("number"), "milestone.number"),
        "state": _text(milestone.get("state"), "milestone.state"),
    }
    _timestamp(milestone.get("updated_at"), "milestone.updated_at")
    issues: list[dict[str, Any]] = []
    pull_requests: list[dict[str, Any]] = []
    blockers: list[str] = []
    for item in sorted(items, key=lambda value: _integer(value.get("number"), "item.number")):
        number = _integer(item.get("number"), "item.number")
        base = {"id": _integer(item.get("id"), "item.id"), "number": number,
                "state": _text(item.get("state"), "item.state"),
                "updated_at": _timestamp(item.get("updated_at"), "item.updated_at")}
        if item.get("kind") == "pull_request" or "pull_request" in item:
            merged_at = item.get("merged_at")
            head = item.get("head", {})
            merge_sha = item.get("merge_commit_sha")
            observed = checks_by_pr.get(number, {})
            exact: list[dict[str, Any]] = []
            for name in required_checks:
                value = observed.get(name)
                if isinstance(value, dict):
                    identifier = value.get("id")
                    observed_name = value.get("name")
                    status = value.get("status")
                    conclusion = value.get("conclusion")
                    url = value.get("details_url")
                    record = {"id": identifier, "name": observed_name, "status": status,
                              "conclusion": conclusion, "details_url": url}
                    exact.append(record)
                    if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                        blockers.append(f"PR #{number} required check {name!r} has invalid ID")
                    if observed_name != name:
                        blockers.append(f"PR #{number} required check {name!r} has mismatched name")
                    if status != "completed":
                        blockers.append(f"PR #{number} required check {name!r} is not completed")
                    if conclusion != "success":
                        blockers.append(f"PR #{number} required check {name!r} conclusion is not success")
                    if not isinstance(url, str) or not url:
                        blockers.append(f"PR #{number} required check {name!r} has no evidence URL")
                else:
                    exact.append({"name": name, "status": "missing"})
                    blockers.append(f"PR #{number} required check {name!r} is missing")
            record = {**base, "merged_at": merged_at, "head_sha": head.get("sha"),
                      "merge_sha": merge_sha, "required_checks": exact}
            if not isinstance(merged_at, str) or not merged_at:
                blockers.append(f"PR #{number} is not merged")
            if not isinstance(record["head_sha"], str) or not re.fullmatch(r"[0-9a-f]{40}", record["head_sha"]):
                blockers.append(f"PR #{number} head SHA is invalid")
            if not isinstance(merge_sha, str) or not re.fullmatch(r"[0-9a-f]{40}", merge_sha):
                blockers.append(f"PR #{number} merge SHA is invalid")
            pull_requests.append(record)
        else:
            labels = _labels(item)
            if "milestone:review" in labels:
                continue
            if base["state"] == "closed" and {"type:engineering", "state:done"} <= labels:
                contract = item.get("contract", {})
                if not contract and isinstance(item.get("body"), str):
                    try:
                        parsed = extract_contract(item["body"]).contract
                        contract = {
                            "issue_revision": parsed.get("issue_revision"),
                            "contract_hash": parsed.get("freeze", {}).get("contract_hash"),
                            "acceptance_hash": sha256_tagged(parsed.get("requirements")),
                        }
                    except GovernanceError:
                        contract = {}
                contract_tuple = {
                    "issue_revision": contract.get("issue_revision"),
                    "contract_hash": contract.get("contract_hash"),
                    "acceptance_hash": contract.get("acceptance_hash"),
                }
                if (not isinstance(contract_tuple["issue_revision"], int)
                        or isinstance(contract_tuple["issue_revision"], bool)
                        or contract_tuple["issue_revision"] < 1
                        or any(not isinstance(contract_tuple[key], str)
                               or not re.fullmatch(r"sha256:[0-9a-f]{64}", contract_tuple[key])
                               for key in ("contract_hash", "acceptance_hash"))):
                    blockers.append(f"Issue #{number} contract tuple is invalid")
                issues.append({**base, "contract": contract_tuple})
            else:
                blockers.append(f"Issue #{number} is not a completed Engineering Issue")
    snapshot = {
        "schema_version": 1,
        "repository_id": _integer(repository_id, "repository_id"),
        "milestone": milestone_record,
        "issues": issues,
        "pull_requests": pull_requests,
        "required_checks": list(required_checks),
        "blockers": sorted(blockers),
        "result": "candidate-complete" if not blockers else "blocked",
        "candidate_complete": not blockers,
        "documentation_status": "human-confirmation-required",
    }
    return {"snapshot": snapshot, "digest": sha256_tagged(snapshot)}


def prepare_review(
    api: GitHubAPI,
    rollout_mode: str,
    milestone: dict[str, Any],
    review: dict[str, Any],
    *,
    repository_id: int = 1,
    repository_full_name: str = "owner/repo",
    run_id: int = 1,
    run_attempt: int = 1,
    provenance: dict[str, Any] | None = None,
) -> dict[str, Any]:
    number = _integer(milestone.get("number"), "milestone.number")
    artifact = f"milestone-review-{number}-{_integer(run_id, 'run_id')}-{_integer(run_attempt, 'run_attempt')}"
    result = {"artifact_name": artifact, "digest": review["digest"], "mutation": "summary-only"}
    if rollout_mode in {"dry-run", "shadow"}:
        return result
    if rollout_mode not in {"warn", "enforce"}:
        raise GovernanceError("MILESTONE-ROLLOUT", "unknown rollout mode", code=5)
    if provenance is None:
        raise GovernanceError("MILESTONE-PROVENANCE", "review publication requires completed workflow provenance", code=4)
    run = provenance.get("run")
    artifact_record = provenance.get("artifact")
    if not isinstance(run, dict) or not isinstance(artifact_record, dict):
        raise GovernanceError("MILESTONE-PROVENANCE", "workflow run and artifact provenance are required", code=4)
    expected_artifact = artifact
    valid_run = (
        run.get("id") == run_id
        and run.get("run_attempt") == run_attempt
        and run.get("path") == ".github/workflows/50-milestone-review.yml"
        and run.get("event") == "workflow_dispatch"
        and run.get("status") == "completed"
        and run.get("conclusion") == "success"
        and isinstance(run.get("head_sha"), str)
        and re.fullmatch(r"[0-9a-f]{40}", run["head_sha"])
        and isinstance(run.get("actor"), dict)
        and isinstance(run["actor"].get("login"), str)
        and run.get("repository", {}).get("id") == repository_id
        and run.get("repository", {}).get("full_name") == repository_full_name
    )
    valid_artifact = (
        isinstance(artifact_record.get("id"), int)
        and not isinstance(artifact_record.get("id"), bool)
        and artifact_record["id"] > 0
        and artifact_record.get("name") == expected_artifact
        and artifact_record.get("workflow_run", {}).get("id") == run_id
        and isinstance(artifact_record.get("archive_download_url"), str)
        and bool(artifact_record.get("archive_download_url"))
        and artifact_record.get("expired") is False
    )
    if not valid_run or not valid_artifact:
        raise GovernanceError("MILESTONE-PROVENANCE", "workflow run or artifact provenance is invalid", code=4)
    marker = {
        "schema_version": 1,
        "repository": {"id": repository_id, "full_name": repository_full_name},
        "milestone": {"id": _integer(milestone.get("id"), "milestone.id"), "number": number},
        "snapshot_digest": review["digest"], "result": review.get("snapshot", {}).get("result"),
        "workflow": {"path": run["path"], "run_id": run_id, "run_attempt": run_attempt,
                     "head_sha": run["head_sha"], "event": run["event"],
                     "actor": run["actor"]["login"], "conclusion": run["conclusion"],
                     "repository": {"id": repository_id, "full_name": repository_full_name}},
        "artifact": {"id": artifact_record["id"], "name": artifact,
                     "archive_download_url": artifact_record["archive_download_url"]},
    }
    body = "<!-- github-bootstrap:milestone-review " + json.dumps(marker, sort_keys=True, separators=(",", ":")) + " -->\n"
    body += "# Milestone review\n\nResult: `" + review.get("snapshot", {}).get("result", "blocked") + "`\n\nDocumentation: human confirmation required.\n"
    existing_issues = api.list_issues(state="all")
    for existing in existing_issues:
        try:
            prior = parse_review_marker(existing.get("body"))
        except GovernanceError:
            continue
        user = existing.get("user", {})
        existing_milestone = existing.get("milestone", {})
        names = _labels(existing)
        if (prior.get("repository") == marker["repository"]
                and prior.get("milestone") == marker["milestone"]
                and prior.get("snapshot_digest") == marker["snapshot_digest"]
                and prior.get("review_issue") == {"id": existing.get("id"), "number": existing.get("number"), "url": existing.get("html_url")}
                and user.get("login") == "github-actions[bot]" and user.get("type") == "Bot"
                and existing_milestone.get("id") == marker["milestone"]["id"]
                and existing_milestone.get("number") == number
                and "milestone:review" in names
                and _remote_provenance_valid(api, prior)):
            result.update({"mutation": "review-issue-existing", "review_issue_number": existing.get("number")})
            return result
    provisional = {
        "artifact": marker["artifact"], "milestone": marker["milestone"],
        "repository": marker["repository"], "snapshot_digest": marker["snapshot_digest"],
        "workflow": marker["workflow"],
    }
    provisional["operation_id"] = sha256_tagged(provisional)
    provisional_body = "<!-- github-bootstrap:milestone-review-provisional " + json.dumps(provisional, sort_keys=True, separators=(",", ":")) + " -->\nCanonical review Issue binding is pending read-back."
    provisional_matches = [value for value in existing_issues
                           if _provisional_issue_valid(api, value, provisional_body, marker)]
    if len(provisional_matches) > 1:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "duplicate provisional review Issues require reconciliation", code=3)
    if provisional_matches:
        created = provisional_matches[0]
    else:
        try:
            created = api.create_issue(f"Milestone review: #{number} ({review['digest'][7:19]})", provisional_body, ["milestone:review"], milestone=number)
        except GovernanceError as error:
            if error.finding.id != "GITHUB-API-TRANSIENT":
                raise
            recovered = [value for value in api.list_issues(state="all")
                         if _provisional_issue_valid(api, value, provisional_body, marker)]
            if len(recovered) != 1:
                raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "review Issue create response was lost; reconciliation is ambiguous", code=3) from error
            created = recovered[0]
    issue_id = _integer(created.get("id"), "review_issue.id")
    issue_number = _integer(created.get("number"), "review_issue.number")
    issue_url = created.get("html_url")
    if not _https_url(issue_url):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "review Issue URL is invalid", code=3)
    marker["review_issue"] = {"id": issue_id, "number": issue_number, "url": issue_url}
    body = "<!-- github-bootstrap:milestone-review " + json.dumps(marker, sort_keys=True, separators=(",", ":")) + " -->\n"
    body += "# Milestone review\n\nResult: `" + review.get("snapshot", {}).get("result", "blocked") + "`\n\nDocumentation: human confirmation required.\n"
    try:
        api.update_issue(issue_number, body=body)
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
    confirmed = api.get_issue(issue_number)
    confirmed_user = confirmed.get("user", {})
    confirmed_milestone = confirmed.get("milestone", {})
    if (confirmed.get("id") != issue_id or confirmed.get("number") != issue_number
            or confirmed.get("body") != body or confirmed.get("html_url") != issue_url
            or "milestone:review" not in _labels(confirmed)
            or confirmed_user.get("login") != "github-actions[bot]" or confirmed_user.get("type") != "Bot"
            or confirmed_milestone.get("id") != marker["milestone"]["id"]
            or confirmed_milestone.get("number") != number):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "review Issue publication read-back failed", code=3)
    result.update({"mutation": "review-issue-created", "review_issue_number": created.get("number")})
    return result


def accept_milestone(
    api: GitHubAPI,
    command: str,
    actor: str,
    trusted_acceptors: list[str],
    milestone: dict[str, Any],
    approved_digest: str,
    current_digest: str,
    *,
    review_issue_number: int = 1,
    rollout_mode: str = "enforce",
    recompute: Callable[[], tuple[dict[str, Any], str]] | None = None,
    operation_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if command != "/accept-milestone":
        raise GovernanceError("MILESTONE-COMMAND", "acceptance comment must be the exact command")
    normalized = normalize_login(actor)
    if normalized not in {normalize_login(value) for value in trusted_acceptors}:
        raise GovernanceError("MILESTONE-ACTOR", "actor is not a trusted milestone acceptor")
    if approved_digest != current_digest:
        raise GovernanceError("MILESTONE-SPEC-CHANGED", "milestone evidence digest changed")
    if rollout_mode in {"dry-run", "shadow"}:
        return {"status": "summary-only", "mutation": "none"}
    if rollout_mode == "warn":
        return {"status": "would-accept", "mutation": "none"}
    if rollout_mode != "enforce":
        raise GovernanceError("MILESTONE-ROLLOUT", "acceptance writes require warn or enforce", code=5)
    if not isinstance(operation_context, dict):
        raise GovernanceError("MILESTONE-OPERATION", "acceptance operation provenance is required", code=4)

    milestone_number = _integer(milestone.get("number"), "milestone.number")
    operation_id = sha256_tagged({"context": operation_context, "actor": normalized,
                                  "digest": approved_digest, "milestone_id": milestone.get("id")})
    envelope = {"schema_version": 1, "operation_id": operation_id,
                "review_issue": operation_context.get("review_issue"),
                "source_comment_id": operation_context.get("source_comment_id"),
                "workflow": operation_context.get("workflow"),
                "milestone": {"id": milestone.get("id"), "number": milestone_number},
                "snapshot_digest": approved_digest, "actor": normalized}
    completion_marker = json.dumps({**envelope, "result": "completed"}, sort_keys=True, separators=(",", ":"))
    completion_body = f"<!-- github-bootstrap:milestone-acceptance {completion_marker} -->\nMilestone acceptance completed."
    existing_comments = api.list_comments(review_issue_number)
    for existing in existing_comments:
        if _bot_comment_valid(existing, completion_body, operation_context.get("review_issue", {}).get("api_url")):
            if milestone.get("state") == "closed":
                return {"status": "already-completed", "digest": approved_digest,
                        "receipt_id": existing.get("id")}
            raise GovernanceError("MILESTONE-RECEIPT-CONFLICT", "completion receipt exists while milestone is open", code=3)
        conflicting = _acceptance_comment_envelope(existing.get("body"), "milestone-acceptance")
        if (_bot_comment_base_valid(existing, operation_context.get("review_issue", {}).get("api_url"))
                and conflicting is not None
                and conflicting.get("review_issue") == operation_context.get("review_issue")
                and conflicting.get("workflow") == operation_context.get("workflow")
                and conflicting.get("milestone") == {"id": milestone.get("id"), "number": milestone_number}):
            raise GovernanceError("MILESTONE-RECEIPT-CONFLICT", "conflicting milestone acceptance receipt exists", code=3)
    if milestone.get("state") != "open":
        raise GovernanceError("MILESTONE-STATE", "milestone must remain open")

    intent_marker = json.dumps({**envelope, "result": "intent"}, sort_keys=True, separators=(",", ":"))
    intent_body = f"<!-- github-bootstrap:milestone-acceptance-intent {intent_marker} -->\nMilestone close intent recorded; authoritative revalidation follows."
    try:
        intent = api.create_comment(review_issue_number, intent_body)
        intent_readback = api.get_comment(_integer(intent.get("id"), "intent.id"))
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
        matches = [value for value in api.list_comments(review_issue_number)
                   if _bot_comment_valid(value, intent_body, operation_context.get("review_issue", {}).get("api_url"))]
        if len(matches) != 1:
            raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "intent response was lost; no close attempted", code=3) from error
        intent_readback = matches[0]
    if not _bot_comment_valid(intent_readback, intent_body, operation_context.get("review_issue", {}).get("api_url")):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "intent read-back failed; no close attempted", code=3)

    # Intent is followed by authoritative rereads. Unknown results pause; they
    # never trigger an automatic reopen or an invented completion receipt.
    if recompute is None:
        before = api.get_milestone(_integer(milestone.get("number"), "milestone.number"))
        api.list_milestone_items(_integer(milestone.get("number"), "milestone.number"))
        latest_digest = current_digest
    else:
        before, latest_digest = recompute()
    if before.get("state") != "open" or before.get("updated_at") != milestone.get("updated_at"):
        raise GovernanceError("MILESTONE-TOCTOU", "milestone changed before close", code=3)
    if latest_digest != approved_digest:
        raise GovernanceError("MILESTONE-SPEC-CHANGED", "milestone evidence changed after acceptance intent", code=3)
    try:
        closed = api.update_milestone(milestone_number, state="closed")
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
        reconciled = api.get_milestone(milestone_number)
        if reconciled.get("state") != "closed":
            raise
        paused_marker = json.dumps({**envelope, "result": "transition-paused", "stage": "close-response-lost"},
                                   sort_keys=True, separators=(",", ":"))
        paused_body = f"<!-- github-bootstrap:milestone-transition-paused {paused_marker} -->\nRemote milestone is closed without a verified completion receipt; manual reconciliation required."
        paused = api.create_comment(review_issue_number, paused_body)
        paused_readback = api.get_comment(_integer(paused.get("id"), "paused.id"))
        if not _bot_comment_valid(paused_readback, paused_body, operation_context.get("review_issue", {}).get("api_url")):
            raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "transition-paused evidence read-back failed", code=3)
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "close response was lost; manual reconciliation required", code=3)
    try:
        readback = api.get_milestone(milestone_number)
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
        paused_marker = json.dumps({**envelope, "result": "transition-paused", "stage": "close-readback-lost"},
                                   sort_keys=True, separators=(",", ":"))
        paused_body = f"<!-- github-bootstrap:milestone-transition-paused {paused_marker} -->\nMilestone close read-back is unknown; manual reconciliation required."
        paused = api.create_comment(review_issue_number, paused_body)
        paused_readback = api.get_comment(_integer(paused.get("id"), "paused.id"))
        if not _bot_comment_valid(paused_readback, paused_body, operation_context.get("review_issue", {}).get("api_url")):
            raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "transition-paused evidence read-back failed", code=3)
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "milestone close read-back was lost", code=3) from error
    if closed.get("state") != "closed" or readback.get("state") != "closed":
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "close read-back is unknown; manual reconciliation required", code=3)
    try:
        receipt = api.create_comment(review_issue_number, completion_body)
        confirmed = api.get_comment(_integer(receipt.get("id"), "receipt.id"))
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
        matches = [value for value in api.list_comments(review_issue_number)
                   if _bot_comment_valid(value, completion_body, operation_context.get("review_issue", {}).get("api_url"))]
        if len(matches) == 1:
            return {"status": "completed", "digest": approved_digest, "receipt_id": matches[0].get("id")}
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "receipt response was lost; manual reconciliation required", code=3) from error
    if not _bot_comment_valid(confirmed, completion_body, operation_context.get("review_issue", {}).get("api_url")):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "receipt read-back failed; manual reconciliation required", code=3)
    return {"status": "completed", "digest": approved_digest, "receipt_id": receipt["id"]}


def _bot_comment_valid(comment: Any, body: str, issue_api_url: Any) -> bool:
    return (
        _bot_comment_base_valid(comment, issue_api_url)
        and comment.get("body") == body
    )


def _provisional_issue_valid(api: GitHubAPI, issue: Any, body: str, marker: dict[str, Any]) -> bool:
    if not isinstance(issue, dict) or not _positive(issue.get("id")) or not _positive(issue.get("number")):
        return False
    repository = marker["repository"]["full_name"]
    number = issue["number"]
    return (
        issue.get("body") == body
        and issue.get("url") == f"https://api.github.com/repos/{repository}/issues/{number}"
        and issue.get("html_url") == f"https://github.com/{repository}/issues/{number}"
        and "milestone:review" in _labels(issue)
        and issue.get("user", {}).get("login") == "github-actions[bot]"
        and issue.get("user", {}).get("type") == "Bot"
        and issue.get("milestone", {}).get("id") == marker["milestone"]["id"]
        and issue.get("milestone", {}).get("number") == marker["milestone"]["number"]
        and isinstance(issue.get("created_at"), str) and issue.get("updated_at") == issue.get("created_at")
        and _remote_provenance_valid(api, marker)
    )


def _bot_comment_base_valid(comment: Any, issue_api_url: Any) -> bool:
    return (
        isinstance(comment, dict) and _positive(comment.get("id"))
        and comment.get("issue_url") == issue_api_url and _https_url(issue_api_url)
        and comment.get("user", {}).get("login") == "github-actions[bot]"
        and comment.get("user", {}).get("type") == "Bot"
        and isinstance(comment.get("created_at"), str)
        and comment.get("updated_at") == comment.get("created_at")
    )


def _acceptance_comment_envelope(body: Any, marker_name: str) -> dict[str, Any] | None:
    if not isinstance(body, str):
        return None
    match = re.fullmatch(rf"<!-- github-bootstrap:{re.escape(marker_name)} (\{{[^\r\n]*\}}) -->\n[^\r\n]+", body)
    if match is None:
        return None
    raw = match.group(1)
    try:
        value = json.loads(raw, object_pairs_hook=_unique_json_object)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(value, dict) or raw != json.dumps(value, sort_keys=True, separators=(",", ":")):
        return None
    required = {"schema_version", "operation_id", "review_issue", "source_comment_id", "workflow",
                "milestone", "snapshot_digest", "actor", "result"}
    if frozenset(value) not in {frozenset(required), frozenset(required | {"stage"})}:
        return None
    if (value.get("schema_version") != 1 or not isinstance(value.get("operation_id"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["operation_id"])
            or not _positive(value.get("source_comment_id"))
            or not isinstance(value.get("actor"), str)
            or not isinstance(value.get("review_issue"), dict)
            or not isinstance(value.get("workflow"), dict)
            or not isinstance(value.get("milestone"), dict)
            or not isinstance(value.get("snapshot_digest"), str)
            or not re.fullmatch(r"sha256:[0-9a-f]{64}", value["snapshot_digest"])):
        return None
    return value

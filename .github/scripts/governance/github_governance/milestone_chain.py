"""Multi-run milestone review and acceptance state machine."""

from __future__ import annotations

import io
import hashlib
import json
import re
import zipfile
import os
from typing import Any

from .canonical import sha256_tagged
from .errors import GovernanceError
from .github_api import GitHubAPI
from .milestones import _positive, _timestamp, build_snapshot, parse_review_marker
from .policy import normalize_login


PHASES = {
    "review-provisional": (".github/workflows/50-milestone-review.yml", "milestone-review-", "milestone-review.json"),
    "review-finalize": (".github/workflows/51-milestone-review-provisional.yml", "milestone-review-provisional-", "phase-result.json"),
    "acceptance-execute": (".github/workflows/53-milestone-acceptance-intent.yml", "milestone-acceptance-intent-", "phase-result.json"),
    "acceptance-finalize": (".github/workflows/54-milestone-acceptance-execute.yml", "milestone-acceptance-execute-", "phase-result.json"),
}

BOT = {"login": "github-actions[bot]", "type": "Bot"}
HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
SHA = re.compile(r"[0-9a-f]{40}\Z")


def canonical_document(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n"


def operation_id(repository_id: int, phase: str, source_run_id: int, subject_digest: str) -> str:
    """Stable across rerun attempts; new source runs are new operations."""
    return sha256_tagged({"repository_id": repository_id, "phase": phase,
                          "source_run_id": source_run_id, "subject_digest": subject_digest})


def load_stage_zip(raw: bytes, expected_file: str) -> dict[str, Any]:
    if not isinstance(raw, bytes) or len(raw) > 5_000_000:
        raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact is invalid or too large", code=4)
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as archive:
            members = archive.infolist()
            if len(members) != 1 or members[0].filename != expected_file:
                raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact member is invalid", code=4)
            member = members[0]
            mode = member.external_attr >> 16
            if (member.is_dir() or member.filename.startswith(("/", "\\"))
                    or ".." in member.filename.split("/")
                    or (mode & 0o170000) == 0o120000 or member.file_size > 2_000_000
                    or member.compress_size > 2_000_000
                    or (member.compress_size == 0 and member.file_size != 0)
                    or (member.compress_size and member.file_size / member.compress_size > 100)):
                raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact member is unsafe", code=4)
            data = archive.read(member)
    except GovernanceError:
        raise
    except (zipfile.BadZipFile, OSError, RuntimeError, ValueError) as error:
        raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact ZIP is invalid", code=4) from error
    try:
        text = data.decode("utf-8", "strict")
        value = json.loads(text, object_pairs_hook=_unique)
    except (UnicodeError, json.JSONDecodeError, ValueError) as error:
        raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact JSON is invalid", code=4) from error
    if not isinstance(value, dict) or text != canonical_document(value):
        raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "stage artifact JSON is not canonical", code=4)
    return value


def _unique(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate key")
        result[key] = value
    return result


def authoritative_upstream(api: GitHubAPI, run_id: int, expected_path: str,
                           expected_event: str | None = None, *, event_attempt: int | None = None,
                           event_head_sha: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    run = api.get_workflow_run(run_id)
    repository = api.get_repository()
    event = expected_event or ({"50": "workflow_dispatch", "51": "workflow_run",
                                "53": "issue_comment", "54": "workflow_run"}.get(expected_path.rsplit("/", 1)[-1][:2]))
    if (run.get("id") != run_id or run.get("path") != expected_path or run.get("event") != event
            or (event_attempt is not None and run.get("run_attempt") != event_attempt)
            or (event_head_sha is not None and run.get("head_sha") != event_head_sha)
            or run.get("status") != "completed" or run.get("conclusion") != "success"
            or run.get("repository", {}).get("id") != repository.get("id")
            or run.get("repository", {}).get("full_name") != repository.get("full_name")
            or run.get("head_repository", {}).get("id") != repository.get("id")
            or run.get("head_repository", {}).get("full_name") != repository.get("full_name")
            or not _positive(run.get("run_attempt"))
            or not isinstance(run.get("head_sha"), str)
            or not SHA.fullmatch(run["head_sha"])
            or not isinstance(run.get("actor", {}).get("login"), str)
            or not isinstance(run.get("triggering_actor", {}).get("login"), str)):
        raise GovernanceError("MILESTONE-UPSTREAM", "upstream workflow run provenance is invalid", code=4)
    return run, repository


def exact_artifact(api: GitHubAPI, run: dict[str, Any], prefix: str,
                   expected_id: int | None = None) -> tuple[dict[str, Any], bytes]:
    expected_suffix = f"-{run['id']}-{run['run_attempt']}"
    if prefix == "milestone-review-":
        name_pattern = re.compile(rf"milestone-review-[1-9][0-9]*{re.escape(expected_suffix)}\Z")
    else:
        name_pattern = re.compile(re.escape(prefix + str(run["id"]) + "-" + str(run["run_attempt"])) + r"\Z")
    matches = [value for value in api.list_workflow_run_artifacts(run["id"])
               if isinstance(value.get("name"), str) and name_pattern.fullmatch(value["name"])
               and value.get("expired") is False]
    if (len(matches) != 1 or not _positive(matches[0].get("id"))
            or not _positive(expected_id) or matches[0]["id"] != expected_id
            or matches[0].get("workflow_run", {}).get("id") != run["id"]
            or not _positive(matches[0].get("size_in_bytes"))
            or not HASH.fullmatch(matches[0].get("digest", ""))):
        raise GovernanceError("MILESTONE-UPSTREAM-ARTIFACT", "exactly one upstream artifact is required", code=4)
    raw = api.download_artifact(matches[0]["id"])
    if "sha256:" + hashlib.sha256(raw).hexdigest() != matches[0]["digest"]:
        raise GovernanceError("MILESTONE-UPSTREAM-ARTIFACT", "artifact archive digest changed", code=4)
    return matches[0], raw


def phase_envelope(repository: dict[str, Any], phase: str, run: dict[str, Any],
                   artifact: dict[str, Any], subject: dict[str, Any]) -> dict[str, Any]:
    digest = sha256_tagged(subject)
    return {"schema_version": 1, "phase": phase,
            "operation_id": operation_id(repository["id"], phase, run["id"], digest),
            "repository": {"id": repository["id"], "full_name": repository["full_name"]},
            "source": {"run_id": run["id"], "run_attempt": run["run_attempt"],
                       "path": run["path"], "head_sha": run["head_sha"],
                       "actor": run["actor"]["login"],
                       "triggering_actor": run["triggering_actor"]["login"],
                       "artifact_id": artifact["id"], "artifact_name": artifact["name"],
                       "artifact_digest": artifact["digest"],
                       "artifact_size": artifact["size_in_bytes"]},
            "subject_digest": digest, "subject": subject}


def _exact_keys(value: Any, keys: set[str], finding: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != keys:
        raise GovernanceError(finding, "stage envelope schema is invalid", code=4)
    return value


def _validate_envelope(api: GitHubAPI, value: Any, phase: str, repository: dict[str, Any],
                       *, with_review_issue: bool = False) -> dict[str, Any]:
    keys = {"schema_version", "phase", "operation_id", "repository", "source",
            "subject_digest", "subject"}
    if with_review_issue:
        keys.add("review_issue")
    envelope = _exact_keys(value, keys, "MILESTONE-STAGE-ENVELOPE")
    source_keys = {"run_id", "run_attempt", "path", "head_sha", "actor", "triggering_actor",
                   "artifact_id", "artifact_name", "artifact_digest", "artifact_size"}
    source = _exact_keys(envelope.get("source"), source_keys, "MILESTONE-STAGE-ENVELOPE")
    subject = envelope.get("subject")
    digest = sha256_tagged(subject) if isinstance(subject, dict) else ""
    if (envelope.get("schema_version") != 1 or envelope.get("phase") != phase
            or envelope.get("repository") != {"id": repository.get("id"), "full_name": repository.get("full_name")}
            or envelope.get("subject_digest") != digest
            or envelope.get("operation_id") != operation_id(repository["id"], phase, source.get("run_id"), digest)
            or not HASH.fullmatch(envelope.get("operation_id", ""))):
        raise GovernanceError("MILESTONE-STAGE-ENVELOPE", "stage envelope binding is invalid", code=4)
    paths = {value[0]: value[1] for value in PHASES.values()}
    prefix = paths.get(source.get("path"))
    if prefix is None:
        raise GovernanceError("MILESTONE-STAGE-ENVELOPE", "stage source path is invalid", code=4)
    run, _ = authoritative_upstream(api, source.get("run_id"), source["path"])
    artifact, _ = exact_artifact(api, run, prefix, source.get("artifact_id"))
    expected_source = {"run_id": run["id"], "run_attempt": run["run_attempt"], "path": run["path"],
                       "head_sha": run["head_sha"], "actor": run["actor"]["login"],
                       "triggering_actor": run["triggering_actor"]["login"],
                       "artifact_id": artifact["id"], "artifact_name": artifact["name"],
                       "artifact_digest": artifact["digest"], "artifact_size": artifact["size_in_bytes"]}
    if source != expected_source:
        raise GovernanceError("MILESTONE-STAGE-ENVELOPE", "stage source tuple changed", code=4)
    return envelope


def _bot_issue(issue: dict[str, Any], repository: dict[str, Any], milestone: dict[str, Any],
               *, exact_body: str | None = None, pristine: bool = True) -> bool:
    number = issue.get("number")
    labels = issue.get("labels")
    names = [x if isinstance(x, str) else x.get("name") if isinstance(x, dict) else None
             for x in labels] if isinstance(labels, list) else []
    return (_positive(issue.get("id")) and _positive(number) and issue.get("user") == BOT
            and (not pristine or issue.get("created_at") == issue.get("updated_at"))
            and names == ["milestone:review"]
            and issue.get("milestone", {}).get("id") == milestone.get("id")
            and issue.get("milestone", {}).get("number") == milestone.get("number")
            and issue.get("url") == f"https://api.github.com/repos/{repository['full_name']}/issues/{number}"
            and issue.get("html_url") == f"https://github.com/{repository['full_name']}/issues/{number}"
            and (exact_body is None or issue.get("body") == exact_body))


def provisional_review(api: GitHubAPI, run_id: int, artifact_id: int,
                       event_attempt: int | None = None, event_head_sha: str | None = None) -> dict[str, Any]:
    run, repository = authoritative_upstream(api, run_id, PHASES["review-provisional"][0],
                                             event_attempt=event_attempt, event_head_sha=event_head_sha)
    artifact, raw = exact_artifact(api, run, PHASES["review-provisional"][1], artifact_id)
    review = load_stage_zip(raw, PHASES["review-provisional"][2])
    snapshot = review.get("snapshot")
    if (not isinstance(snapshot, dict) or review.get("digest") != sha256_tagged(snapshot)
            or snapshot.get("result") not in {"candidate-complete", "blocked"}
            or snapshot.get("candidate_complete") is not (snapshot.get("result") == "candidate-complete")
            or not isinstance(snapshot.get("blockers"), list)
            or any(not isinstance(x, str) or not x for x in snapshot["blockers"])
            or bool(snapshot["blockers"]) is (snapshot.get("result") == "candidate-complete")):
        raise GovernanceError("MILESTONE-STAGE-ARTIFACT", "capture digest is invalid", code=4)
    milestone = snapshot.get("milestone", {})
    subject = {"review_digest": review["digest"], "milestone": milestone,
               "result": snapshot["result"], "blockers": snapshot["blockers"],
               "capture_run_id": run_id, "capture_artifact_id": artifact["id"]}
    envelope = phase_envelope(repository, "review-provisional", run, artifact, subject)
    marker = canonical_document(envelope).strip()
    body = f"<!-- github-bootstrap:milestone-review-provisional {marker} -->\nReview finalization pending upstream success."
    number = milestone.get("number")
    all_issues = api.list_issues(state="all")
    existing = [issue for issue in all_issues if issue.get("body") == body
                and _bot_issue(issue, repository, milestone, exact_body=body)]
    conflicts = [issue for issue in all_issues if issue.get("body") == body and issue not in existing
                 and issue.get("user") == BOT]
    if conflicts:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "authoritative provisional Issue conflicts", code=3)
    if len(existing) > 1:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "duplicate provisional review Issues", code=3)
    if existing:
        issue = existing[0]
    else:
        try:
            issue = api.create_issue(f"Milestone review provisional: #{number}", body,
                                     ["milestone:review"], milestone=number)
        except GovernanceError as error:
            if error.finding.id != "GITHUB-API-TRANSIENT":
                raise
            recovered = [value for value in api.list_issues(state="all")
                         if value.get("body") == body and _bot_issue(value, repository, milestone, exact_body=body)]
            if len(recovered) != 1:
                raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "provisional create response is ambiguous", code=3) from error
            issue = recovered[0]
    readback = api.get_issue(issue["number"])
    if readback.get("id") != issue.get("id") or not _bot_issue(readback, repository, milestone, exact_body=body):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "provisional read-back failed", code=3)
    return {**envelope, "review_issue": {"id": issue["id"], "number": issue["number"],
              "api_url": issue.get("url"), "html_url": issue.get("html_url")}}


def finalize_review(api: GitHubAPI, run_id: int, artifact_id: int,
                    event_attempt: int | None = None, event_head_sha: str | None = None) -> dict[str, Any]:
    run, repository = authoritative_upstream(api, run_id, PHASES["review-finalize"][0],
                                             event_attempt=event_attempt, event_head_sha=event_head_sha)
    artifact, raw = exact_artifact(api, run, PHASES["review-finalize"][1], artifact_id)
    provisional = load_stage_zip(raw, PHASES["review-finalize"][2])
    _validate_envelope(api, provisional, "review-provisional", repository, with_review_issue=True)
    issue_ref = provisional.get("review_issue", {})
    issue = api.get_issue(issue_ref.get("number"))
    milestone = provisional.get("subject", {}).get("milestone", {})
    if issue.get("id") != issue_ref.get("id") or not _bot_issue(issue, repository, milestone):
        raise GovernanceError("MILESTONE-REVIEW-ISSUE", "provisional Issue identity changed", code=3)
    result = provisional["subject"].get("result")
    blockers = provisional["subject"].get("blockers")
    if result not in {"candidate-complete", "blocked"} or not isinstance(blockers, list):
        raise GovernanceError("MILESTONE-PROVISIONAL", "capture result was not preserved", code=4)
    final = {"schema_version": 1, "operation_id": provisional.get("operation_id"),
             "repository": provisional.get("repository"),
             "milestone": milestone,
             "snapshot_digest": provisional.get("subject", {}).get("review_digest"),
             "result": result, "blockers": blockers,
             "capture_source": provisional.get("source"),
             "provisional_source": {"run_id": run["id"], "run_attempt": run["run_attempt"],
                 "artifact_id": artifact["id"], "artifact_name": artifact["name"],
                 "artifact_digest": artifact["digest"]},
             "review_issue": issue_ref}
    body = "<!-- github-bootstrap:milestone-review-final " + canonical_document(final).strip() + " -->\n# Milestone review\n\nDocumentation: human-confirmation-required."
    try:
        api.update_issue(issue_ref["number"], body=body)
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
    confirmed = api.get_issue(issue_ref["number"])
    if not _bot_issue(confirmed, repository, milestone, exact_body=body, pristine=False):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "final review read-back failed", code=3)
    return phase_envelope(repository, "review-finalize", run, artifact, final)


def acceptance_intent(api: GitHubAPI, review_issue_number: int, comment_id: int,
                      policy: dict[str, Any]) -> dict[str, Any]:
    repository = api.get_repository()
    issue = api.get_issue(review_issue_number)
    comment = api.get_comment(comment_id)
    actor = comment.get("user", {}).get("login")
    if (comment.get("body") != "/accept-milestone" or comment.get("issue_url") != issue.get("url")
            or comment.get("updated_at") != comment.get("created_at")
            or normalize_login(actor) not in policy["trusted_milestone_acceptors"]):
        raise GovernanceError("MILESTONE-INTENT", "acceptance intent is unauthorized or edited")
    body = issue.get("body", "")
    match = re.search(r"<!-- github-bootstrap:milestone-review-final (\{[^\r\n]*\}) -->", body)
    if not match:
        raise GovernanceError("MILESTONE-INTENT", "final review marker is missing")
    marker = json.loads(match.group(1), object_pairs_hook=_unique)
    if match.group(1) != canonical_document(marker).strip():
        raise GovernanceError("MILESTONE-INTENT", "final review marker is not canonical")
    _exact_keys(marker, {"schema_version", "operation_id", "repository", "milestone",
                         "snapshot_digest", "result", "blockers", "capture_source",
                         "provisional_source", "review_issue"}, "MILESTONE-INTENT")
    if (marker.get("schema_version") != 1 or marker.get("result") != "candidate-complete"
            or marker.get("blockers") != [] or not HASH.fullmatch(marker.get("snapshot_digest", ""))
            or marker.get("repository") != {"id": repository.get("id"), "full_name": repository.get("full_name")}
            or marker.get("review_issue") != {"id": issue.get("id"), "number": issue.get("number"),
                                               "api_url": issue.get("url"), "html_url": issue.get("html_url")}
            or not _bot_issue(issue, repository, marker.get("milestone", {}), pristine=False)):
        raise GovernanceError("MILESTONE-INTENT", "final review is blocked or not authoritative")
    capture = marker["capture_source"]
    capture_run, _ = authoritative_upstream(api, capture.get("run_id"), PHASES["review-provisional"][0])
    capture_artifact, capture_raw = exact_artifact(api, capture_run, PHASES["review-provisional"][1],
                                                   capture.get("artifact_id"))
    captured = load_stage_zip(capture_raw, "milestone-review.json")
    if (captured.get("digest") != marker["snapshot_digest"]
            or captured.get("digest") != sha256_tagged(captured.get("snapshot"))
            or capture_artifact.get("name") != capture.get("artifact_name")
            or capture_artifact.get("digest") != capture.get("artifact_digest")):
        raise GovernanceError("MILESTONE-INTENT", "capture evidence no longer matches final review", code=4)
    milestone = marker.get("milestone", {})
    current = api.get_milestone(milestone.get("number"))
    if current.get("id") != milestone.get("id") or current.get("state") != "open":
        raise GovernanceError("MILESTONE-STATE", "milestone is not open")
    items = api.list_milestone_items(milestone["number"])
    checks: dict[int, dict[str, dict[str, Any]]] = {}
    enriched: list[dict[str, Any]] = []
    for item in items:
        if "pull_request" not in item:
            enriched.append(item)
            continue
        pull = api.get_pull_request(item["number"])
        pull["kind"] = "pull_request"
        enriched.append(pull)
        by_name: dict[str, dict[str, Any]] = {}
        for check in api.list_check_runs(pull.get("head", {}).get("sha")):
            name = check.get("name")
            if isinstance(name, str) and name in by_name:
                raise GovernanceError("MILESTONE-CHECK-DUPLICATE", "required check name is ambiguous", code=4)
            if isinstance(name, str):
                by_name[name] = check
        checks[item["number"]] = by_name
    recaptured = build_snapshot(api, current, enriched, checks,
                                policy["required_milestone_checks"], repository_id=repository["id"])
    if recaptured["digest"] != marker["snapshot_digest"]:
        raise GovernanceError("MILESTONE-SPEC-CHANGED", "current evidence differs from final review", code=3)
    subject = {"review_issue": {"id": issue["id"], "number": issue["number"],
                                "api_url": issue["url"], "html_url": issue["html_url"]},
               "source_comment": {"id": comment_id, "actor": normalize_login(actor),
                                  "created_at": comment["created_at"]},
               "milestone": {"id": current["id"], "number": current["number"]},
               "review_digest": marker.get("snapshot_digest"),
               "capture_source": capture, "provisional_source": marker["provisional_source"],
               "original_description": current.get("description") or ""}
    digest = sha256_tagged(subject)
    return {"schema_version": 1, "phase": "acceptance-intent",
            "operation_id": operation_id(repository["id"], "acceptance", comment_id, digest),
            "repository": {"id": repository["id"], "full_name": repository["full_name"]},
            "subject_digest": digest, "subject": subject}


def execute_acceptance(api: GitHubAPI, run_id: int, artifact_id: int,
                       current_run_id: int | None = None, current_run_attempt: int | None = None,
                       event_attempt: int | None = None, event_head_sha: str | None = None) -> dict[str, Any]:
    run, repository = authoritative_upstream(api, run_id, PHASES["acceptance-execute"][0],
                                             event_attempt=event_attempt, event_head_sha=event_head_sha)
    artifact, raw = exact_artifact(api, run, PHASES["acceptance-execute"][1], artifact_id)
    intent = load_stage_zip(raw, PHASES["acceptance-execute"][2])
    # Intent is produced by the current trusted 53 workflow and has no upstream artifact source.
    _exact_keys(intent, {"schema_version", "phase", "operation_id", "repository",
                         "subject_digest", "subject"}, "MILESTONE-INTENT")
    if (intent.get("schema_version") != 1 or intent.get("phase") != "acceptance-intent"
            or intent.get("repository") != {"id": repository.get("id"), "full_name": repository.get("full_name")}
            or intent.get("subject_digest") != sha256_tagged(intent.get("subject"))
            or intent.get("operation_id") != operation_id(repository["id"], "acceptance",
                                                            intent.get("subject", {}).get("source_comment", {}).get("id"),
                                                            intent.get("subject_digest"))):
        raise GovernanceError("MILESTONE-INTENT", "intent envelope is invalid", code=4)
    subject = intent.get("subject", {})
    milestone_ref = subject.get("milestone", {})
    milestone = api.get_milestone(milestone_ref.get("number"))
    current_run_id = current_run_id or int(os.environ.get("GITHUB_RUN_ID", "0"))
    current_run_attempt = current_run_attempt or int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
    current = api.get_workflow_run(current_run_id)
    if (current.get("id") != current_run_id or current.get("run_attempt") != current_run_attempt
            or current.get("path") != ".github/workflows/54-milestone-acceptance-execute.yml"
            or current.get("event") != "workflow_run" or current.get("status") not in {"queued", "in_progress"}
            or current.get("repository", {}).get("id") != repository.get("id")
            or current.get("repository", {}).get("full_name") != repository.get("full_name")
            or current.get("head_repository", {}).get("id") != repository.get("id")
            or current.get("head_repository", {}).get("full_name") != repository.get("full_name")
            or not SHA.fullmatch(current.get("head_sha", ""))):
        raise GovernanceError("MILESTONE-OPERATION", "current execution run provenance is invalid", code=4)
    marker = {"schema_version": 1, "operation_id": intent.get("operation_id"), "intent_run_id": run_id,
              "intent_artifact_id": artifact["id"], "subject_digest": intent.get("subject_digest")}
    marker["execution"] = {"run_id": current_run_id, "run_attempt": current_run_attempt,
                           "path": current["path"], "head_sha": current["head_sha"]}
    hidden = "<!-- github-bootstrap:milestone-acceptance " + canonical_document(marker).strip() + " -->"
    description = subject.get("original_description", "")
    target = description + (("\n\n" if description else "") + hidden)
    if milestone.get("state") == "closed":
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "milestone was already closed on entry", code=3)
    if milestone.get("state") != "open" or (milestone.get("description") or "") != description:
        raise GovernanceError("MILESTONE-TOCTOU", "milestone changed after intent", code=3)
    try:
        api.update_milestone(milestone_ref["number"], state="closed", description=target)
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
    confirmed = api.get_milestone(milestone_ref["number"])
    if confirmed.get("state") != "closed" or confirmed.get("description") != target:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "milestone close is not exactly reconciled", code=3)
    return phase_envelope(repository, "acceptance-execute", run, artifact,
                          {"milestone": milestone_ref, "review_issue": subject.get("review_issue"),
                           "marker": marker, "description": target})


def finalize_acceptance(api: GitHubAPI, run_id: int, artifact_id: int,
                        event_attempt: int | None = None, event_head_sha: str | None = None) -> dict[str, Any]:
    run, repository = authoritative_upstream(api, run_id, PHASES["acceptance-finalize"][0],
                                             event_attempt=event_attempt, event_head_sha=event_head_sha)
    artifact, raw = exact_artifact(api, run, PHASES["acceptance-finalize"][1], artifact_id)
    execution = load_stage_zip(raw, PHASES["acceptance-finalize"][2])
    _validate_envelope(api, execution, "acceptance-execute", repository)
    subject = execution.get("subject", {})
    milestone = api.get_milestone(subject.get("milestone", {}).get("number"))
    if milestone.get("state") != "closed" or milestone.get("description") != subject.get("description"):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "execution marker is not authoritative", code=3)
    marker = subject.get("marker", {})
    if marker.get("execution") != {"run_id": run_id, "run_attempt": run["run_attempt"],
                                   "path": run["path"], "head_sha": run["head_sha"]}:
        raise GovernanceError("MILESTONE-EXECUTION", "execution marker does not bind the successful run", code=4)
    receipt = {"schema_version": 1, "operation_id": marker.get("operation_id"),
               "result": "completed", "execution_run_id": run_id,
               "execution_run_attempt": run["run_attempt"], "execution_artifact_id": artifact["id"],
               "execution_artifact_digest": artifact["digest"], "milestone": subject.get("milestone"),
               "review_issue": subject.get("review_issue")}
    body = "<!-- github-bootstrap:milestone-acceptance-completed " + canonical_document(receipt).strip() + " -->\nMilestone acceptance completed."
    issue_number = subject.get("review_issue", {}).get("number")
    comments = api.list_comments(issue_number)
    expected_api = subject.get("review_issue", {}).get("api_url")
    existing = [value for value in comments if value.get("body") == body and value.get("user") == BOT
                and value.get("issue_url") == expected_api
                and value.get("created_at") == value.get("updated_at")]
    conflicts = []
    for value in comments:
        if (value.get("user") != BOT or value.get("issue_url") != expected_api
                or value.get("created_at") != value.get("updated_at") or value.get("body") == body):
            continue
        found = re.fullmatch(r"<!-- github-bootstrap:milestone-acceptance-completed (\{[^\r\n]*\}) -->\nMilestone acceptance completed\.", value.get("body", ""))
        if not found:
            continue
        try:
            prior = json.loads(found.group(1), object_pairs_hook=_unique)
        except (json.JSONDecodeError, ValueError):
            continue
        if (found.group(1) == canonical_document(prior).strip()
                and prior.get("operation_id") == receipt["operation_id"]):
            conflicts.append(value)
    if conflicts:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "authoritative completion receipt conflicts", code=3)
    if len(existing) == 1:
        return {"result": "already-completed", **receipt}
    if len(existing) > 1:
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "duplicate completion receipts", code=3)
    # Review Issue number is carried by the intent subject in real artifacts.
    try:
        created = api.create_comment(issue_number, body)
    except GovernanceError as error:
        if error.finding.id != "GITHUB-API-TRANSIENT":
            raise
        matches = [value for value in api.list_comments(issue_number) if value.get("body") == body
                   and value.get("user") == BOT and value.get("issue_url") == expected_api
                   and value.get("created_at") == value.get("updated_at")]
        if len(matches) != 1:
            raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "completion response is ambiguous", code=3) from error
        created = matches[0]
    confirmed = api.get_comment(created["id"])
    if (confirmed.get("id") != created.get("id") or confirmed.get("body") != body
            or confirmed.get("user") != BOT or confirmed.get("issue_url") != expected_api
            or confirmed.get("created_at") != confirmed.get("updated_at")):
        raise GovernanceError("MILESTONE-TRANSITION-PAUSED", "completion receipt read-back failed", code=3)
    return {"result": "completed", **receipt}

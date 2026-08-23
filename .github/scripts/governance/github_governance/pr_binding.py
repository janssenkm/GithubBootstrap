"""Evidence-bound local handoff and pull-request contract validation."""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .attestations import SourceComment
from .canonical import contract_hash, subject_digest
from .contract import extract_contract
from .errors import Finding, GovernanceError
from .events import (
    _bot_transition_comment,
    _transition_records,
    _verify_transition_run,
    parse_command,
)
from .github_api import GitHubAPI, urllib_transport
from .policy import authorized, load_policy, normalize_login
from .schema_validation import schema_findings
from .semantic import semantic_findings


START = "<!-- engineering-binding:start -->"
END = "<!-- engineering-binding:end -->"
MAX_BODY_BYTES = 262_144
MAX_BINDING_BYTES = 32_768
_HASH = re.compile(r"sha256:[0-9a-f]{64}\Z")
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")
_OWNER = re.compile(r"(?!.*--)[A-Za-z0-9](?:[A-Za-z0-9-]{0,37}[A-Za-z0-9])?\Z")
_REPOSITORY = re.compile(r"[A-Za-z0-9._-]{1,100}\Z")
_FIELDS = frozenset(
    {"issue_number", "issue_revision", "subject_digest", "contract_hash", "base_commit"}
)


@dataclass(frozen=True)
class Binding:
    issue_number: int
    issue_revision: int
    subject_digest: str
    contract_hash: str
    base_commit: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "issue_number": self.issue_number,
            "issue_revision": self.issue_revision,
            "subject_digest": self.subject_digest,
            "contract_hash": self.contract_hash,
            "base_commit": self.base_commit,
        }


@dataclass(frozen=True)
class Handoff:
    binding: Binding
    approved_commands: tuple[str, ...]
    operation_id: str
    fork: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "binding": self.binding.as_dict(),
            "approved_commands": list(self.approved_commands),
            "operation_id": self.operation_id,
            "fork": self.fork,
        }


class PullRequestGitHubAPI(GitHubAPI):
    """The one additional read needed by the PR binding workflow."""

    def get_pull(self, number: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/pulls/{number}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "pull-request response has an invalid shape", code=4)
        return value


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise GovernanceError("PR-BINDING-DUPLICATE-KEY", "PR binding has a duplicate key", code=2)
        value[key] = item
    return value


def _reject_constant(value: str) -> None:
    raise GovernanceError("PR-BINDING-NUMBER", f"PR binding contains forbidden number {value}", code=2)


def _text(raw: bytes | str) -> str:
    if isinstance(raw, str):
        try:
            encoded = raw.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise GovernanceError("PR-BINDING-UTF8", "PR body is not valid UTF-8", code=2) from error
    elif isinstance(raw, bytes):
        encoded = raw
    else:
        raise GovernanceError("PR-BINDING-TYPE", "PR body must be UTF-8 text", code=2)
    if len(encoded) > MAX_BODY_BYTES:
        raise GovernanceError("PR-BINDING-SIZE", "PR body exceeds the byte limit", code=2)
    try:
        value = encoded.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GovernanceError("PR-BINDING-UTF8", "PR body is not valid UTF-8", code=2) from error
    if "\x00" in value:
        raise GovernanceError("PR-BINDING-NUL", "PR body contains NUL", code=2)
    return value


def extract_binding(raw: bytes | str) -> Binding:
    """Extract one exact, duplicate-key-free five-field JSON binding."""

    body = _text(raw)
    if body.count(START) != 1 or body.count(END) != 1:
        raise GovernanceError("PR-BINDING-MARKERS", "PR body requires one binding marker pair", code=2)
    start = body.find(START)
    end = body.find(END)
    if start >= end:
        raise GovernanceError("PR-BINDING-MARKERS", "PR binding markers are reversed", code=2)
    inner = body[start + len(START) : end]
    match = re.fullmatch(r"\s*```json[ \t]*\r?\n(?P<payload>.*?)\r?\n```[ \t]*\s*", inner, re.DOTALL)
    if match is None or inner.count("```") != 2:
        raise GovernanceError("PR-BINDING-FENCE", "PR binding requires one lowercase json fence", code=2)
    payload = match.group("payload")
    if len(payload.encode("utf-8")) > MAX_BINDING_BYTES:
        raise GovernanceError("PR-BINDING-SIZE", "PR binding exceeds the byte limit", code=2)
    try:
        value = json.loads(
            payload,
            object_pairs_hook=_strict_pairs,
            parse_constant=_reject_constant,
        )
    except GovernanceError:
        raise
    except (json.JSONDecodeError, UnicodeError, RecursionError) as error:
        raise GovernanceError("PR-BINDING-JSON", "PR binding JSON is invalid", code=2) from error
    if not isinstance(value, dict) or frozenset(value) != _FIELDS:
        raise GovernanceError("PR-BINDING-SHAPE", "PR binding fields are not exact", code=1)
    for key in ("issue_number", "issue_revision"):
        item = value[key]
        if not isinstance(item, int) or isinstance(item, bool) or item < 1:
            raise GovernanceError("PR-BINDING-SHAPE", f"PR binding {key} is invalid", code=1)
    for key in ("subject_digest", "contract_hash"):
        if not isinstance(value[key], str) or _HASH.fullmatch(value[key]) is None:
            raise GovernanceError("PR-BINDING-SHAPE", f"PR binding {key} is invalid", code=1)
    if not isinstance(value["base_commit"], str) or _COMMIT.fullmatch(value["base_commit"]) is None:
        raise GovernanceError("PR-BINDING-SHAPE", "PR binding base_commit is invalid", code=1)
    return Binding(**value)


def binding_from_contract(issue_number: int, contract: dict[str, Any]) -> Binding:
    if not isinstance(issue_number, int) or isinstance(issue_number, bool) or issue_number < 1:
        raise GovernanceError("PR-ISSUE-NUMBER", "Engineering Issue number is invalid", code=2)
    return Binding(
        issue_number=issue_number,
        issue_revision=contract.get("issue_revision"),
        subject_digest=subject_digest(contract),
        contract_hash=contract_hash(contract),
        base_commit=contract.get("base_commit"),
    )


def _labels(issue: dict[str, Any]) -> set[str]:
    raw = issue.get("labels")
    if not isinstance(raw, list):
        raise GovernanceError("PR-ISSUE-LABELS", "Engineering Issue labels are invalid")
    labels: list[str] = []
    for item in raw:
        name = item.get("name") if isinstance(item, dict) else item
        if not isinstance(name, str) or not name:
            raise GovernanceError("PR-ISSUE-LABELS", "Engineering Issue labels are invalid")
        labels.append(name)
    if len(labels) != len(set(labels)):
        raise GovernanceError("PR-ISSUE-LABELS", "Engineering Issue labels are duplicated")
    return set(labels)


def _positive_integer(value: Any, finding: str, message: str, *, code: int = 1) -> int:
    if type(value) is not int or value < 1:
        raise GovernanceError(finding, message, code=code)
    return value


def _repository_name(value: Any, finding: str, message: str, *, code: int = 1) -> str:
    if not isinstance(value, str) or value.count("/") != 1:
        raise GovernanceError(finding, message, code=code)
    owner, name = value.split("/")
    if (
        _OWNER.fullmatch(owner) is None
        or _REPOSITORY.fullmatch(name) is None
        or name in {".", ".."}
    ):
        raise GovernanceError(finding, message, code=code)
    return value


def _issue_snapshot(issue: Any) -> tuple[Any, ...]:
    if not isinstance(issue, dict):
        raise GovernanceError("PR-ISSUE-SHAPE", "Engineering Issue snapshot is invalid")
    identity = _positive_integer(
        issue.get("id"), "PR-ISSUE-SHAPE", "Engineering Issue id is invalid"
    )
    number = _positive_integer(
        issue.get("number"), "PR-ISSUE-SHAPE", "Engineering Issue number is invalid"
    )
    body = issue.get("body")
    updated_at = issue.get("updated_at")
    if not isinstance(body, str) or not isinstance(updated_at, str) or not updated_at:
        raise GovernanceError("PR-ISSUE-SHAPE", "Engineering Issue snapshot is invalid")
    return (
        identity,
        number,
        body,
        updated_at,
        tuple(sorted(_labels(issue))),
    )


def _approved_commands(contract: dict[str, Any], policy: dict[str, Any]) -> tuple[str, ...]:
    allowed = set(policy["allowed_verification_commands"])
    result: list[str] = []
    for requirement in contract.get("requirements", []):
        for criterion in requirement.get("acceptance_criteria", []):
            verification = criterion.get("verification", {})
            if verification.get("type") == "command":
                command = verification.get("run")
                if command not in allowed:
                    raise GovernanceError("PR-COMMAND-NOT-ALLOWED", "contract command is not allowlisted")
                if command not in result:
                    result.append(command)
    return tuple(result)


def _ready_handoff(
    api: Any,
    policy: dict[str, Any],
    repository_id: int,
    repository: str,
    issue: dict[str, Any],
    contract: dict[str, Any],
    comments: list[dict[str, Any]],
) -> tuple[Binding, str]:
    binding = binding_from_contract(issue["number"], contract)
    records = _transition_records(comments)
    intents = [
        item for item in records
        if item[1].get("phase") == "intent"
        and item[1].get("action") == "ready"
        and item[1].get("target_hash") == binding.contract_hash
    ]
    if len(intents) != 1:
        raise GovernanceError("PR-READY-RECEIPTS", "ready contract requires one current intent", code=3)
    intent_comment, intent = intents[0]
    operation_id = intent["operation_id"]
    completions = [
        item for item in records
        if item[1].get("phase") == "completed" and item[1].get("operation_id") == operation_id
    ]
    handoffs = [
        item for item in records
        if item[1].get("phase") == "handoff" and item[1].get("operation_id") == operation_id
    ]
    if len(completions) != 1 or len(handoffs) != 1:
        raise GovernanceError("PR-READY-RECEIPTS", "ready contract requires one completion and handoff", code=3)
    completed_comment, completed = completions[0]
    handoff_comment, handoff = handoffs[0]
    source_matches = [item for item in comments if item.get("id") == intent["source_comment_id"]]
    if len(source_matches) != 1:
        raise GovernanceError("PR-READY-SOURCE", "ready source command is missing", code=3)
    try:
        source = SourceComment.from_api(source_matches[0])
        source.verify_unchanged()
    except GovernanceError as error:
        raise GovernanceError("PR-READY-SOURCE", "ready source command changed or is invalid", code=3) from error
    parsed = parse_command(source.body)
    if (
        parsed is None
        or parsed.action != "ready"
        or source.body_digest != intent["source_body_digest"]
        or normalize_login(source.actor) != intent["actor"]
        or not authorized(policy, "trusted_developers", source.actor)
    ):
        raise GovernanceError("PR-READY-SOURCE", "ready source command is not current and authorized", code=3)
    expected_intent = {
        "repository_id": repository_id,
        "issue_id": issue.get("id"),
        "repository": repository,
        "issue_number": issue.get("number"),
        "revision": binding.issue_revision,
        "subject_digest": binding.subject_digest,
        "target_hash": binding.contract_hash,
        "review_block_digest": None,
    }
    if any(intent.get(key) != value for key, value in expected_intent.items()):
        raise GovernanceError("PR-READY-INTENT", "ready intent does not bind the current Engineering Issue", code=3)
    intent_id = _bot_transition_comment(intent_comment, intent)
    completed_id = _bot_transition_comment(completed_comment, completed)
    handoff_id = _bot_transition_comment(handoff_comment, handoff)
    if not (source.id < intent_id < completed_id < handoff_id):
        raise GovernanceError("PR-READY-ORDER", "ready evidence order is invalid", code=3)
    expected_completion = {
        "action": "ready",
        "intent_comment_id": intent_id,
        "source_issue_number": binding.issue_number,
        "target_issue_number": binding.issue_number,
        "target_hash": binding.contract_hash,
        "run_id": intent["run_id"],
        "run_url": intent["run_url"],
    }
    if any(completed.get(key) != value for key, value in expected_completion.items()):
        raise GovernanceError("PR-READY-COMPLETION", "ready completion does not bind the current target", code=3)
    if handoff != {
        "version": 1,
        "phase": "handoff",
        "operation_id": operation_id,
        "action": "ready",
        **binding.as_dict(),
    }:
        raise GovernanceError("PR-READY-HANDOFF", "ready handoff tuple differs from the current contract", code=3)
    _verify_transition_run(api, intent)
    return binding, operation_id


def validate_ready_issue(
    api: Any,
    policy: dict[str, Any],
    repository_root: str | Path,
    repository_id: int,
    repository: str,
    issue_number: int,
) -> Handoff:
    """Read and re-read a ready Engineering Issue and its complete ready evidence."""

    _positive_integer(
        repository_id, "PR-REPOSITORY", "repository id is invalid", code=2
    )
    _repository_name(
        repository, "PR-REPOSITORY", "repository identity is invalid", code=2
    )
    _positive_integer(
        issue_number,
        "PR-ISSUE-NUMBER",
        "Engineering Issue number is invalid",
        code=2,
    )
    issue = api.get_issue(issue_number)
    snapshot = _issue_snapshot(issue)
    if issue.get("number") != issue_number:
        raise GovernanceError("PR-ISSUE-NUMBER", "Engineering Issue identity differs from the requested Issue")
    labels = _labels(issue)
    entity = labels & {"type:intake", "type:candidate", "type:engineering"}
    states = {item for item in labels if item.startswith("state:")}
    if entity != {"type:engineering"}:
        raise GovernanceError("PR-ISSUE-TYPE", "binding target is not one Engineering Issue")
    if states != {"state:ready"}:
        raise GovernanceError("PR-ISSUE-STATE", "Engineering Issue is not in ready state")
    if "pull_request" in issue:
        raise GovernanceError("PR-ISSUE-TYPE", "binding target is a pull request")
    contract = extract_contract(issue.get("body", "")).contract
    findings: list[Finding] = schema_findings(contract, repository_root)
    if not findings:
        findings.extend(semantic_findings(contract, policy, repository_root))
    if findings:
        finding = sorted(findings)[0]
        raise GovernanceError(finding.id, finding.message, path=finding.path)
    if contract.get("status") != "ready":
        raise GovernanceError("PR-ISSUE-STATE", "Engineering contract status is not ready")
    comments = api.list_comments(issue_number)
    binding, operation_id = _ready_handoff(
        api, policy, repository_id, repository, issue, contract, comments
    )
    if _issue_snapshot(api.get_issue(issue_number)) != snapshot:
        raise GovernanceError("PR-ISSUE-TOCTOU", "Engineering Issue changed during contract validation", code=3)
    return Handoff(binding, _approved_commands(contract, policy), operation_id)


def _repo_name(value: Any) -> str:
    if not isinstance(value, dict):
        raise GovernanceError("PR-EVENT-SHAPE", "pull-request repository identity is missing", code=2)
    return _repository_name(
        value.get("full_name"),
        "PR-EVENT-SHAPE",
        "pull-request repository identity is invalid",
        code=2,
    )


def _pull_snapshot(value: Any) -> tuple[Any, ...]:
    if not isinstance(value, dict):
        raise GovernanceError("PR-EVENT-SHAPE", "pull-request snapshot is invalid", code=2)
    base = value.get("base")
    head = value.get("head")
    if not isinstance(base, dict) or not isinstance(head, dict):
        raise GovernanceError("PR-EVENT-SHAPE", "pull-request refs are missing", code=2)
    identity = _positive_integer(
        value.get("id"), "PR-EVENT-SHAPE", "pull-request id is invalid", code=2
    )
    number = _positive_integer(
        value.get("number"), "PR-EVENT-SHAPE", "pull-request number is invalid", code=2
    )
    body = value.get("body")
    updated_at = value.get("updated_at")
    base_sha = base.get("sha")
    head_sha = head.get("sha")
    if (
        not isinstance(body, str)
        or not isinstance(updated_at, str)
        or not updated_at
        or not isinstance(base_sha, str)
        or _COMMIT.fullmatch(base_sha) is None
        or not isinstance(head_sha, str)
        or _COMMIT.fullmatch(head_sha) is None
    ):
        raise GovernanceError("PR-EVENT-SHAPE", "pull-request snapshot is invalid", code=2)
    snapshot = (
        identity,
        number,
        body,
        updated_at,
        base_sha,
        _repo_name(base.get("repo")),
        head_sha,
        _repo_name(head.get("repo")),
    )
    return snapshot


def validate_pull_request(
    api: Any,
    policy: dict[str, Any],
    repository_root: str | Path,
    event: dict[str, Any],
) -> Handoff:
    repository = event.get("repository")
    pull_event = event.get("pull_request")
    if not isinstance(repository, dict) or not isinstance(pull_event, dict):
        raise GovernanceError("PR-EVENT-SHAPE", "pull-request event is invalid", code=2)
    repository_id = _positive_integer(
        repository.get("id"), "PR-REPOSITORY", "base repository id is invalid", code=2
    )
    full_name = _repository_name(
        repository.get("full_name"),
        "PR-REPOSITORY",
        "base repository identity is invalid",
        code=2,
    )
    event_snapshot = _pull_snapshot(pull_event)
    number = event_snapshot[1]
    current = api.get_pull(number)
    current_snapshot = _pull_snapshot(current)
    if current_snapshot != event_snapshot:
        raise GovernanceError("PR-TOCTOU", "pull request changed after the event snapshot", code=3)
    snapshot = current_snapshot
    if snapshot[5] != full_name:
        raise GovernanceError("PR-BASE-REPOSITORY", "pull request does not target the base repository")
    binding = extract_binding(current["body"])
    handoff = validate_ready_issue(
        api, policy, repository_root, repository_id, full_name, binding.issue_number
    )
    if binding != handoff.binding:
        raise GovernanceError("PR-BINDING-STALE", "pull-request binding differs from the current ready contract")
    if _pull_snapshot(api.get_pull(number)) != snapshot:
        raise GovernanceError("PR-TOCTOU", "pull request changed during contract validation", code=3)
    return Handoff(handoff.binding, handoff.approved_commands, handoff.operation_id, snapshot[7] != full_name)


def _event_file() -> dict[str, Any]:
    path = os.environ.get("GITHUB_EVENT_PATH")
    if not path:
        raise GovernanceError("EVENT-FILE", "GITHUB_EVENT_PATH is required", code=2)
    try:
        value = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceError("EVENT-FILE", "GitHub event file is unreadable or invalid", code=2) from error
    if not isinstance(value, dict):
        raise GovernanceError("EVENT-SHAPE", "GitHub event must be an object", code=2)
    return value


def _write_outputs(result: Handoff) -> None:
    output_path = os.environ.get("GITHUB_OUTPUT")
    if output_path:
        with open(output_path, "a", encoding="utf-8") as output:
            for key, value in result.binding.as_dict().items():
                output.write(f"{key}={value}\n")
            output.write(f"operation_id={result.operation_id}\n")
            output.write(f"fork={str(result.fork).lower()}\n")
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        binding = result.binding
        with open(summary_path, "a", encoding="utf-8") as summary:
            summary.write(
                "## Local execution handoff\n\n"
                f"- Engineering Issue: #{binding.issue_number}\n"
                f"- Revision: {binding.issue_revision}\n"
                f"- Subject digest: `{binding.subject_digest}`\n"
                f"- Contract hash: `{binding.contract_hash}`\n"
                f"- Base commit: `{binding.base_commit}`\n"
                f"- Approved local commands: {len(result.approved_commands)}\n"
                "- No Issue command was executed and no Git ref was written\n"
            )


def evaluate_cli(arguments: Any) -> dict[str, Any]:
    event = _event_file()
    repository = event.get("repository", {})
    if not isinstance(repository, dict):
        raise GovernanceError("EVENT-REPOSITORY", "repository identity is missing", code=2)
    repository_id = _positive_integer(
        repository.get("id"), "EVENT-REPOSITORY", "repository identity is missing", code=2
    )
    full_name = _repository_name(
        repository.get("full_name"),
        "EVENT-REPOSITORY",
        "repository identity is missing",
        code=2,
    )
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        raise GovernanceError("EVENT-TOKEN", "GitHub API token is unavailable", code=4)
    api = PullRequestGitHubAPI(
        urllib_transport(token, os.environ.get("GITHUB_API_URL", "https://api.github.com")),
        full_name,
    )
    policy = load_policy(arguments.policy, arguments.repository_root)
    if arguments.mode == "handoff":
        if arguments.issue_number is None:
            raise GovernanceError("PR-ISSUE-NUMBER", "handoff requires an Engineering Issue number", code=2)
        result = validate_ready_issue(
            api, policy, arguments.repository_root, repository_id, full_name, arguments.issue_number
        )
    else:
        if arguments.issue_number is not None:
            raise GovernanceError("PR-ISSUE-NUMBER", "PR mode derives the Issue number from the binding", code=2)
        result = validate_pull_request(api, policy, arguments.repository_root, event)
    _write_outputs(result)
    return {"result": "PASS", **result.as_dict()}

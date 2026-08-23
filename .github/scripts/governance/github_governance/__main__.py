"""Command-line entry point for deterministic governance checks."""

from __future__ import annotations

import argparse
import json
import sys
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .audit import gate_result
from .canonical import contract_hash, subject_digest
from .contract import extract_contract_file
from .errors import GovernanceError, UsageError
from .policy import load_policy
from .schema_validation import schema_findings
from .semantic import semantic_findings
from .state import require_transition


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message: str) -> None:
        raise UsageError(message)


def _parser() -> argparse.ArgumentParser:
    parser = _ArgumentParser(prog="python -m github_governance")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for command in ("extract", "digest"):
        child = subparsers.add_parser(command)
        child.add_argument("--body-file", required=True)
    for command in ("validate", "gate"):
        child = subparsers.add_parser(command)
        child.add_argument("--body-file", required=True)
        child.add_argument("--policy", required=True)
        child.add_argument("--repository-root", required=True)
        child.add_argument("--dry-run", action="store_true")
    transition = subparsers.add_parser("transition")
    transition.add_argument("--entity", required=True)
    transition.add_argument("--from-state", required=True)
    transition.add_argument("--to-state", required=True)
    pr_binding = subparsers.add_parser("pr-binding")
    pr_binding.add_argument("--mode", choices=("handoff", "pr"), required=True)
    pr_binding.add_argument("--issue-number", type=int)
    pr_binding.add_argument("--policy", required=True)
    pr_binding.add_argument("--repository-root", required=True)
    review = subparsers.add_parser("milestone-review")
    review.add_argument("--milestone-number", type=int, required=True)
    review.add_argument("--policy", required=True)
    review.add_argument("--repository", required=True)
    review.add_argument("--run-id", type=int, required=True)
    review.add_argument("--run-attempt", type=int, required=True)
    review.add_argument("--summary-only", action="store_true")
    accept = subparsers.add_parser("milestone-accept")
    accept.add_argument("--review-issue-number", type=int, required=True)
    accept.add_argument("--comment-id", type=int, required=True)
    accept.add_argument("--policy", required=True)
    accept.add_argument("--repository", required=True)
    chain = subparsers.add_parser("milestone-chain")
    chain.add_argument("--phase", choices=("review-provisional", "review-finalize", "acceptance-intent", "acceptance-execute", "acceptance-finalize"), required=True)
    chain.add_argument("--upstream-run-id", type=int)
    chain.add_argument("--upstream-artifact-id", type=int)
    chain.add_argument("--upstream-run-attempt", type=int)
    chain.add_argument("--upstream-head-sha")
    chain.add_argument("--review-issue-number", type=int)
    chain.add_argument("--comment-id", type=int)
    chain.add_argument("--current-run-id", type=int)
    chain.add_argument("--current-run-attempt", type=int)
    chain.add_argument("--policy", required=True)
    chain.add_argument("--repository", required=True)
    return parser


def _emit(value: dict[str, Any], stream: Any = sys.stdout) -> None:
    stream.write(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def _run(arguments: argparse.Namespace) -> int:
    if arguments.command == "extract":
        extracted = extract_contract_file(arguments.body_file)
        _emit({"result": "PASS", **extracted.as_dict()})
        return 0
    if arguments.command == "digest":
        contract = extract_contract_file(arguments.body_file).contract
        _emit({"result": "PASS", "subject_digest": subject_digest(contract), "contract_hash": contract_hash(contract)})
        return 0
    if arguments.command in ("validate", "gate"):
        contract = extract_contract_file(arguments.body_file).contract
        policy = load_policy(arguments.policy, arguments.repository_root)
        findings = schema_findings(contract, arguments.repository_root)
        if not findings:
            findings.extend(semantic_findings(contract, policy, arguments.repository_root))
        metadata: dict[str, Any] = {"dry_run": bool(arguments.dry_run)}
        try:
            metadata["subject_digest"] = subject_digest(contract)
            metadata["contract_hash"] = contract_hash(contract)
        except GovernanceError:
            pass
        _emit(gate_result(findings, **metadata))
        return 1 if findings else 0
    if arguments.command == "transition":
        require_transition(arguments.entity, arguments.from_state, arguments.to_state)
        _emit({"result": "PASS", "allowed": True})
        return 0
    if arguments.command == "pr-binding":
        from .pr_binding import evaluate_cli

        _emit(evaluate_cli(arguments))
        return 0
    if arguments.command == "milestone-chain":
        from .github_api import GitHubAPI, urllib_transport
        from .milestone_chain import (acceptance_intent, canonical_document, execute_acceptance,
                                      finalize_acceptance, finalize_review, provisional_review)
        token = os.environ.get("GH_TOKEN")
        if not token:
            raise UsageError("GH_TOKEN is required")
        api = GitHubAPI(urllib_transport(token), arguments.repository)
        policy = load_policy(arguments.policy)
        if arguments.phase == "review-provisional":
            value = provisional_review(api, arguments.upstream_run_id, arguments.upstream_artifact_id,
                                       arguments.upstream_run_attempt, arguments.upstream_head_sha)
        elif arguments.phase == "review-finalize":
            value = finalize_review(api, arguments.upstream_run_id, arguments.upstream_artifact_id,
                                    arguments.upstream_run_attempt, arguments.upstream_head_sha)
        elif arguments.phase == "acceptance-intent":
            value = acceptance_intent(api, arguments.review_issue_number, arguments.comment_id, policy)
        elif arguments.phase == "acceptance-execute":
            value = execute_acceptance(api, arguments.upstream_run_id, arguments.upstream_artifact_id,
                                       arguments.current_run_id, arguments.current_run_attempt,
                                       arguments.upstream_run_attempt, arguments.upstream_head_sha)
        else:
            value = finalize_acceptance(api, arguments.upstream_run_id, arguments.upstream_artifact_id,
                                        arguments.upstream_run_attempt, arguments.upstream_head_sha)
        sys.stdout.write(canonical_document(value))
        return 0
    if arguments.command in {"milestone-review", "milestone-accept"}:
        from .github_api import GitHubAPI, urllib_transport
        from .milestones import accept_milestone, build_snapshot, prepare_review

        token = os.environ.get("GH_TOKEN")
        if not token:
            raise UsageError("GH_TOKEN is required")
        api = GitHubAPI(urllib_transport(token), arguments.repository)
        policy = load_policy(arguments.policy)
        if arguments.command == "milestone-review":
            milestone = api.get_milestone(arguments.milestone_number)
            repository = api.get_repository()
            items = api.list_milestone_items(arguments.milestone_number)
            checks: dict[int, dict[str, dict[str, Any]]] = {}
            enriched: list[dict[str, Any]] = []
            for item in items:
                if "pull_request" not in item:
                    enriched.append(item)
                    continue
                pull = api.get_pull_request(item["number"])
                pull["kind"] = "pull_request"
                enriched.append(pull)
                runs = api.list_check_runs(pull["head"]["sha"])
                by_name: dict[str, dict[str, Any]] = {}
                for run in runs:
                    name = run.get("name")
                    if name in by_name:
                        raise GovernanceError("MILESTONE-CHECK-DUPLICATE", "required check name is ambiguous", code=4)
                    if isinstance(name, str):
                        by_name[name] = run
                checks[item["number"]] = by_name
            review = build_snapshot(api, milestone, enriched, checks, policy["required_milestone_checks"], repository_id=repository["id"])
            artifact_name = f"milestone-review-{arguments.milestone_number}-{arguments.run_id}-{arguments.run_attempt}"
            if arguments.summary_only:
                _emit({"schema_version": 1, "captured_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
                       "run": {"id": arguments.run_id, "attempt": arguments.run_attempt},
                       "artifact": {"name": artifact_name}, "rollout_mode": policy["rollout_mode"], **review})
                return 0
            from .milestones import load_review_artifact
            provenance = None
            if not arguments.summary_only:
                run = api.get_workflow_run(arguments.run_id)
                matches = [artifact for artifact in api.list_workflow_run_artifacts(arguments.run_id)
                           if artifact.get("name") == artifact_name]
                if len(matches) != 1:
                    raise GovernanceError("MILESTONE-PROVENANCE", "exactly one bound review artifact is required", code=4)
                provenance = {"run": run, "artifact": matches[0]}
                artifact_document = load_review_artifact(
                    api.download_artifact(matches[0]["id"]), repository_id=repository["id"],
                    milestone_id=milestone["id"], milestone_number=arguments.milestone_number,
                    run_id=arguments.run_id, run_attempt=arguments.run_attempt, artifact_name=artifact_name,
                )
                if artifact_document["snapshot"] != review["snapshot"] or artifact_document["digest"] != review["digest"]:
                    raise GovernanceError("MILESTONE-ARTIFACT-STALE", "source artifact no longer matches authoritative evidence", code=3)
            _emit({"result": "PASS", **prepare_review(api, policy["rollout_mode"], milestone, artifact_document,
                                                       repository_id=repository["id"], repository_full_name=repository["full_name"],
                                                       run_id=arguments.run_id, run_attempt=arguments.run_attempt,
                                                       provenance=provenance)})
            return 0
        from .milestones import parse_review_marker
        repository = api.get_repository()
        review_issue = api.get_issue(arguments.review_issue_number)
        if "pull_request" in review_issue:
            raise GovernanceError("MILESTONE-REVIEW-ISSUE", "acceptance target must not be a pull request")
        labels = review_issue.get("labels")
        expected_issue_api_url = f"https://api.github.com/repos/{arguments.repository}/issues/{arguments.review_issue_number}"
        expected_issue_html_url = f"https://github.com/{arguments.repository}/issues/{arguments.review_issue_number}"
        if review_issue.get("url") != expected_issue_api_url or review_issue.get("html_url") != expected_issue_html_url:
            raise GovernanceError("MILESTONE-REVIEW-ISSUE", "review Issue URLs do not match the repository target")
        label_names = {value if isinstance(value, str) else value.get("name") for value in labels} if isinstance(labels, list) else set()
        if "milestone:review" not in label_names:
            raise GovernanceError("MILESTONE-REVIEW-ISSUE", "acceptance target is not a milestone review Issue")
        marker = parse_review_marker(review_issue.get("body"))
        marker_repository = marker.get("repository", {})
        marker_milestone = marker.get("milestone", {})
        marker_issue = marker.get("review_issue", {})
        marker_workflow = marker.get("workflow", {})
        marker_artifact = marker.get("artifact", {})
        milestone_number = marker_milestone.get("number")
        if not isinstance(milestone_number, int) or isinstance(milestone_number, bool):
            raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker milestone number is invalid")
        milestone = api.get_milestone(milestone_number)
        issue_user = review_issue.get("user", {})
        issue_milestone = review_issue.get("milestone", {})
        if (marker_repository != {"id": repository.get("id"), "full_name": repository.get("full_name")}
                or marker_milestone != {"id": milestone.get("id"), "number": milestone_number}
                or marker_issue != {"id": review_issue.get("id"), "number": review_issue.get("number"),
                                    "url": review_issue.get("html_url")}
                or issue_user.get("login") != "github-actions[bot]" or issue_user.get("type") != "Bot"
                or issue_milestone.get("id") != milestone.get("id") or issue_milestone.get("number") != milestone_number):
            raise GovernanceError("MILESTONE-REVIEW-MARKER", "review marker target does not match authoritative state")
        run = api.get_workflow_run(marker_workflow.get("run_id"))
        artifacts = api.list_workflow_run_artifacts(marker_workflow.get("run_id"))
        artifact = [value for value in artifacts if value.get("id") == marker_artifact.get("id")]
        if (len(artifact) != 1 or run.get("path") != marker_workflow.get("path")
                or run.get("run_attempt") != marker_workflow.get("run_attempt")
                or run.get("head_sha") != marker_workflow.get("head_sha")
                or run.get("event") != marker_workflow.get("event")
                or run.get("actor", {}).get("login") != marker_workflow.get("actor")
                or run.get("repository", {}).get("id") != repository.get("id")
                or run.get("repository", {}).get("full_name") != repository.get("full_name")
                or marker_workflow.get("repository") != {"id": repository.get("id"), "full_name": repository.get("full_name")}
                or run.get("status") != "completed" or run.get("conclusion") != "success"
                or artifact[0].get("name") != marker_artifact.get("name")
                or artifact[0].get("archive_download_url") != marker_artifact.get("archive_download_url")
                or artifact[0].get("expired") is not False):
            raise GovernanceError("MILESTONE-PROVENANCE", "review workflow or artifact provenance changed", code=4)
        comment = api.get_comment(arguments.comment_id)
        user = comment.get("user")
        if (comment.get("id") != arguments.comment_id or not isinstance(user, dict)
                or user.get("type") != "User"
                or comment.get("issue_url") != expected_issue_api_url
                or comment.get("body") != "/accept-milestone"
                or not isinstance(comment.get("created_at"), str)
                or comment.get("updated_at") != comment.get("created_at")):
            raise GovernanceError("MILESTONE-COMMENT", "authoritative comment does not bind to the review Issue")
        acceptance_run_id = int(os.environ.get("GITHUB_RUN_ID", "0"))
        acceptance_run_attempt = int(os.environ.get("GITHUB_RUN_ATTEMPT", "0"))
        acceptance_run = api.get_workflow_run(acceptance_run_id)
        if (acceptance_run.get("id") != acceptance_run_id
                or acceptance_run.get("run_attempt") != acceptance_run_attempt
                or acceptance_run.get("path") != ".github/workflows/51-milestone-acceptance.yml"
                or acceptance_run.get("event") != "issue_comment"
                or acceptance_run.get("repository", {}).get("id") != repository.get("id")
                or acceptance_run.get("actor", {}).get("login") != user.get("login")
                or acceptance_run.get("triggering_actor", {}).get("login") != user.get("login")
                or not isinstance(acceptance_run.get("head_sha"), str)
                or not re.fullmatch(r"[0-9a-f]{40}", acceptance_run["head_sha"])):
            raise GovernanceError("MILESTONE-OPERATION", "acceptance workflow provenance is invalid", code=4)
        operation = {"review_issue": {"id": review_issue["id"], "number": review_issue["number"],
                                      "url": expected_issue_html_url, "api_url": expected_issue_api_url},
                     "source_comment_id": arguments.comment_id,
                     "workflow": {"run_id": acceptance_run_id, "run_attempt": acceptance_run_attempt,
                                  "path": acceptance_run["path"], "head_sha": acceptance_run["head_sha"]}}
        def capture() -> tuple[dict[str, Any], dict[str, Any]]:
            observed_milestone = api.get_milestone(milestone_number)
            items = api.list_milestone_items(milestone_number)
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
                for run in api.list_check_runs(pull["head"]["sha"]):
                    name = run.get("name")
                    if name in by_name:
                        raise GovernanceError("MILESTONE-CHECK-DUPLICATE", "required check name is ambiguous", code=4)
                    if isinstance(name, str):
                        by_name[name] = run
                checks[item["number"]] = by_name
            return observed_milestone, build_snapshot(api, observed_milestone, enriched, checks, policy["required_milestone_checks"], repository_id=repository["id"])

        milestone, current = capture()
        if current["snapshot"]["result"] != "candidate-complete" or marker.get("result") != "candidate-complete":
            raise GovernanceError("MILESTONE-BLOCKED", "review is not candidate-complete")
        def recapture_after_intent() -> tuple[dict[str, Any], str]:
            latest_issue = api.get_issue(arguments.review_issue_number)
            latest_comment = api.get_comment(arguments.comment_id)
            latest_run = api.get_workflow_run(marker_workflow["run_id"])
            latest_acceptance_run = api.get_workflow_run(acceptance_run_id)
            latest_artifacts = api.list_workflow_run_artifacts(marker_workflow["run_id"])
            if (latest_issue != review_issue or latest_comment != comment or latest_run != run
                    or latest_acceptance_run != acceptance_run
                    or latest_artifacts != artifacts):
                raise GovernanceError("MILESTONE-TOCTOU", "acceptance authority or provenance changed after intent", code=3)
            latest_milestone, latest = capture()
            return latest_milestone, latest["digest"]

        result = accept_milestone(api, comment.get("body"), user.get("login"), policy["trusted_milestone_acceptors"],
                                  milestone, marker.get("snapshot_digest"), current["digest"],
                                  review_issue_number=arguments.review_issue_number,
                                  rollout_mode=policy["rollout_mode"],
                                  recompute=recapture_after_intent, operation_context=operation)
        _emit({"result": "PASS", **result})
        return 0
    raise UsageError("unsupported command")


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        return _run(arguments)
    except GovernanceError as error:
        _emit({"result": "FAIL", "finding_ids": [error.finding.id], "findings": [error.finding.as_dict()]}, sys.stderr)
        return error.code
    except KeyboardInterrupt:
        _emit({"result": "FAIL", "finding_ids": ["INTERRUPTED"]}, sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

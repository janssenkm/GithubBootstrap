from __future__ import annotations

import copy
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import yaml

from github_governance.audit import render_transition_receipt, transition_operation_id
from github_governance.canonical import contract_hash, subject_digest
from github_governance.contract import extract_contract
from github_governance.errors import GovernanceError
from github_governance.state import build_ready_target


WORKFLOW = ".github/workflows/01-ai-development-workflow.yml"
PR_WORKFLOW = ".github/workflows/10-pr-ai-review.yml"
MANIFESTS = (
    ".github/workflows/00-baseline-check.yml",
    PR_WORKFLOW,
    ".github/workflows/20-ci-build-test.yml",
)
MODULE = ".github/scripts/governance/github_governance/pr_binding.py"
MARKER = "<!-- github-governance-transition:v1:"


def _render_binding(binding: dict) -> str:
    return (
        "Narrative.\n\n<!-- engineering-binding:start -->\n```json\n"
        + json.dumps(binding, indent=2)
        + "\n```\n<!-- engineering-binding:end -->\n"
    )


def _ready_contract(valid_contract):
    candidate = copy.deepcopy(valid_contract)
    candidate["evidence"].append(
        {
            "id": "E-02",
            "type": "human-decision",
            "locator": "issue:7#issuecomment-302",
            "summary": "The current contract was approved.",
            "captured_at": "2026-08-23T00:30:00Z",
            "content_sha256": None,
        }
    )
    digest = subject_digest(candidate)
    candidate["review"].update(
        reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest
    )
    candidate["approval"].update(
        decision="approved",
        actor="approver",
        decided_at="2026-08-23T00:30:00Z",
        evidence_ref="E-02",
        subject_revision=1,
        subject_digest=digest,
    )
    candidate["status"] = "contracted"
    candidate["provenance"]["promoted_by"] = "approver"
    candidate["provenance"]["sources"].append(
        {"repository": "owner/repo", "number": 7, "role": "candidate"}
    )
    candidate["freeze"].update(
        frozen_at="2026-08-23T01:00:00Z", frozen_by="approver"
    )
    candidate["freeze"]["contract_hash"] = contract_hash(candidate)
    return candidate, build_ready_target(
        candidate, actor="developer", frozen_at="2026-08-23T02:00:00Z"
    )


def _comment(identifier, body, actor="github-actions[bot]", kind="Bot", timestamp=None):
    timestamp = timestamp or f"2026-08-23T02:00:{identifier % 60:02d}Z"
    return {
        "id": identifier,
        "body": body,
        "created_at": timestamp,
        "updated_at": timestamp,
        "user": {"login": actor, "type": kind},
    }


def _ready_evidence(valid_contract):
    before, ready = _ready_contract(valid_contract)
    issue = {
        "id": 202,
        "number": 8,
        "body": _render_contract(ready),
        "updated_at": "2026-08-23T02:00:04Z",
        "created_at": "2026-08-23T01:00:00Z",
        "labels": [{"name": "type:engineering"}, {"name": "state:ready"}],
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    source = _comment(404, "/ready-for-dev", "developer", "User", "2026-08-23T02:00:00Z")
    operation_input = {
        "repository_id": 55,
        "issue_id": 202,
        "action": "ready",
        "source_comment_id": 404,
        "source_body_digest": "sha256:" + hashlib.sha256(b"/ready-for-dev").hexdigest(),
        "actor": "developer",
        "revision": ready["issue_revision"],
        "subject_digest": subject_digest(ready),
        "review_block_digest": None,
        "expected_before_hash": contract_hash(before),
    }
    operation_id = transition_operation_id(operation_input)
    intent = {
        "version": 1,
        "phase": "intent",
        "operation_id": operation_id,
        **operation_input,
        "repository": "owner/repo",
        "issue_number": 8,
        "target_hash": contract_hash(ready),
        "run_id": 909,
        "run_url": "https://github.com/owner/repo/actions/runs/909",
        "workflow_path": ".github/workflows/02-engineering-governance.yml",
        "head_sha": ready["base_commit"],
        "event": "issue_comment",
        "baseline_comment_ids": [404],
        "baseline_updated_at": "2026-08-23T01:59:59Z",
    }
    completed = {
        "version": 1,
        "phase": "completed",
        "operation_id": operation_id,
        "action": "ready",
        "intent_comment_id": 405,
        "source_issue_number": 8,
        "target_issue_number": 8,
        "target_hash": contract_hash(ready),
        "result": "applied",
        "run_id": 909,
        "run_url": "https://github.com/owner/repo/actions/runs/909",
    }
    handoff = {
        "version": 1,
        "phase": "handoff",
        "operation_id": operation_id,
        "action": "ready",
        "issue_number": 8,
        "issue_revision": ready["issue_revision"],
        "subject_digest": subject_digest(ready),
        "contract_hash": contract_hash(ready),
        "base_commit": ready["base_commit"],
    }
    comments = [
        source,
        _comment(405, render_transition_receipt(intent), timestamp="2026-08-23T02:00:01Z"),
        _comment(406, render_transition_receipt(completed), timestamp="2026-08-23T02:00:03Z"),
        _comment(407, render_transition_receipt(handoff), timestamp="2026-08-23T02:00:04Z"),
    ]
    run = {
        "id": 909,
        "html_url": intent["run_url"],
        "path": intent["workflow_path"],
        "head_sha": intent["head_sha"],
        "repository": {"full_name": "owner/repo"},
        "actor": {"login": "developer"},
        "triggering_actor": {"login": "developer"},
        "event": "issue_comment",
        "status": "completed",
        "conclusion": "success",
    }
    return issue, comments, run, handoff


def _render_contract(contract):
    return (
        "Engineering contract.\n\n<!-- engineering-contract:start -->\n```json\n"
        + json.dumps(contract, indent=2)
        + "\n```\n<!-- engineering-contract:end -->\n"
    )


class API:
    def __init__(self, issue, comments, run, pull=None, run_error=None):
        self.issue = copy.deepcopy(issue)
        self.comments = copy.deepcopy(comments)
        self.run = copy.deepcopy(run)
        self.pull = copy.deepcopy(pull)
        self.run_error = run_error
        self.calls = []

    def get_issue(self, number):
        self.calls.append(("issue", number))
        return copy.deepcopy(self.issue)

    def list_comments(self, number):
        self.calls.append(("comments", number))
        return copy.deepcopy(self.comments)

    def get_workflow_run(self, run_id):
        self.calls.append(("run", run_id))
        if self.run_error:
            raise self.run_error
        return copy.deepcopy(self.run)

    def get_pull(self, number):
        self.calls.append(("pull", number))
        return copy.deepcopy(self.pull)


def _pull(binding, *, head_repository="fork/repo"):
    return {
        "id": 700,
        "number": 12,
        "body": _render_binding(binding),
        "updated_at": "2026-08-23T03:00:00Z",
        "base": {"sha": "1" * 40, "repo": {"full_name": "owner/repo"}},
        "head": {"sha": "2" * 40, "repo": {"full_name": head_repository}},
    }


def _event(pull):
    return {
        "action": "opened",
        "repository": {"id": 55, "full_name": "owner/repo"},
        "pull_request": copy.deepcopy(pull),
    }


def _malformed_identity_vectors(repository_root):
    return json.loads(
        (
            repository_root
            / "tests/governance/fixtures/pull_requests/malformed-identities.json"
        ).read_text()
    )


def test_strict_binding_parser_and_inert_narrative(repository_root):
    from github_governance.pr_binding import extract_binding

    valid = extract_binding(
        (repository_root / "tests/governance/fixtures/pull_requests/valid.md").read_bytes()
    )
    assert valid.issue_number == 8
    injected = extract_binding(
        (repository_root / "tests/governance/fixtures/pull_requests/injection.md").read_bytes()
    )
    assert injected == valid
    with pytest.raises(GovernanceError) as error:
        extract_binding(
            (repository_root / "tests/governance/fixtures/pull_requests/duplicate.md").read_bytes()
        )
    assert error.value.finding.id == "PR-BINDING-MARKERS"


@pytest.mark.parametrize(
    "body",
    [
        "",
        "<!-- engineering-binding:start -->\n```JSON\n{}\n```\n<!-- engineering-binding:end -->",
        "<!-- engineering-binding:start -->\n```json\n[]\n```\n<!-- engineering-binding:end -->",
        "<!-- engineering-binding:start -->\n```json\n{\"issue_number\":8,\"issue_number\":9}\n```\n<!-- engineering-binding:end -->",
        "<!-- engineering-binding:start -->\n```json\n{}\n```\nextra\n<!-- engineering-binding:end -->",
    ],
)
def test_malformed_bindings_fail_closed(body):
    from github_governance.pr_binding import extract_binding

    with pytest.raises(GovernanceError):
        extract_binding(body)


def test_valid_ready_receipt_chain_yields_five_field_handoff(valid_contract, policy, repository_root):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, expected = _ready_evidence(valid_contract)
    result = validate_ready_issue(
        API(issue, comments, run), policy, repository_root, 55, "owner/repo", 8
    )
    assert result.binding.as_dict() == {
        key: expected[key]
        for key in ("issue_number", "issue_revision", "subject_digest", "contract_hash", "base_commit")
    }
    assert result.approved_commands == ("test -f README.md",)
    assert result.operation_id == expected["operation_id"]


@pytest.mark.parametrize(
    ("mutate", "finding"),
    [
        (lambda issue, comments, run: issue["labels"][0].update(name="type:candidate"), "PR-ISSUE-TYPE"),
        (lambda issue, comments, run: issue["labels"][1].update(name="state:contracted"), "PR-ISSUE-STATE"),
        (lambda issue, comments, run: run.update(conclusion="failure"), "TRANSITION-RUN-INCOMPLETE"),
        (lambda issue, comments, run: comments.__setitem__(3, copy.deepcopy(comments[2])), "PR-READY-RECEIPTS"),
        (lambda issue, comments, run: comments[0].update(updated_at="2026-08-23T02:01:00Z"), "PR-READY-SOURCE"),
    ],
)
def test_ready_issue_receipt_attacks_fail_closed(valid_contract, policy, repository_root, mutate, finding):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, _ = _ready_evidence(valid_contract)
    mutate(issue, comments, run)
    with pytest.raises(GovernanceError) as error:
        validate_ready_issue(API(issue, comments, run), policy, repository_root, 55, "owner/repo", 8)
    assert error.value.finding.id == finding


def test_missing_actions_run_permission_fails_closed(valid_contract, policy, repository_root):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, _ = _ready_evidence(valid_contract)
    failure = GovernanceError("GITHUB-API-FORBIDDEN", "required permission unavailable")
    with pytest.raises(GovernanceError) as error:
        validate_ready_issue(
            API(issue, comments, run, run_error=failure), policy, repository_root, 55, "owner/repo", 8
        )
    assert error.value.finding.id == "GITHUB-API-FORBIDDEN"


def test_inaccessible_engineering_issue_fails_closed(policy, repository_root):
    from github_governance.pr_binding import validate_ready_issue

    class Inaccessible:
        def get_issue(self, _number):
            raise GovernanceError("GITHUB-API-NOT-FOUND", "required Issue is unavailable")

    with pytest.raises(GovernanceError) as error:
        validate_ready_issue(Inaccessible(), policy, repository_root, 55, "owner/repo", 8)
    assert error.value.finding.id == "GITHUB-API-NOT-FOUND"


def test_narrative_only_issue_edit_preserves_binding(valid_contract, policy, repository_root):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, expected = _ready_evidence(valid_contract)
    issue["body"] = "Updated non-contract narrative.\n\n" + issue["body"]
    issue["updated_at"] = "2026-08-23T02:00:05Z"
    result = validate_ready_issue(
        API(issue, comments, run), policy, repository_root, 55, "owner/repo", 8
    )
    assert result.binding.contract_hash == expected["contract_hash"]
    assert result.binding.subject_digest == expected["subject_digest"]


def test_fork_pr_uses_current_base_issue_and_binding(valid_contract, policy, repository_root):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    contract = extract_contract(issue["body"]).contract
    binding = binding_from_contract(8, contract).as_dict()
    pull = _pull(binding)
    api = API(issue, comments, run, pull)
    result = validate_pull_request(api, policy, repository_root, _event(pull))
    assert result.binding.as_dict() == binding
    assert result.fork is True
    assert ("run", 909) in api.calls
    assert api.calls.count(("pull", 12)) == 2


@pytest.mark.parametrize("field", ["issue_number", "issue_revision", "subject_digest", "contract_hash", "base_commit"])
def test_each_stale_binding_member_fails(valid_contract, policy, repository_root, field):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    if field in {"issue_number", "issue_revision"}:
        binding[field] += 1
    elif field == "base_commit":
        binding[field] = "f" * 40
    else:
        binding[field] = "sha256:" + "0" * 64
    pull = _pull(binding, head_repository="owner/repo")
    with pytest.raises(GovernanceError) as error:
        validate_pull_request(API(issue, comments, run, pull), policy, repository_root, _event(pull))
    assert error.value.finding.id in {"PR-BINDING-STALE", "PR-ISSUE-NUMBER"}


def test_pull_request_and_issue_toctou_fail_closed(valid_contract, policy, repository_root):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    pull = _pull(binding)

    class ChangingAPI(API):
        def get_pull(self, number):
            value = super().get_pull(number)
            if self.calls.count(("pull", number)) == 2:
                value["body"] += "changed"
            return value

    with pytest.raises(GovernanceError) as error:
        validate_pull_request(ChangingAPI(issue, comments, run, pull), policy, repository_root, _event(pull))
    assert error.value.finding.id == "PR-TOCTOU"


@pytest.mark.parametrize("field", ["repository_id", "issue_number", "issue_id"])
@pytest.mark.parametrize("invalid", [True, None, 0, -1, "8"])
def test_ready_rejects_non_positive_or_non_exact_integer_identity(
    valid_contract, policy, repository_root, field, invalid
):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, _ = _ready_evidence(valid_contract)
    repository_id = 55
    issue_number = 8
    if field == "repository_id":
        repository_id = invalid
    elif field == "issue_number":
        issue_number = invalid
    else:
        issue["id"] = invalid
    with pytest.raises(GovernanceError) as error:
        validate_ready_issue(
            API(issue, comments, run),
            policy,
            repository_root,
            repository_id,
            "owner/repo",
            issue_number,
        )
    assert error.value.finding.id in {
        "PR-REPOSITORY",
        "PR-ISSUE-NUMBER",
        "PR-ISSUE-SHAPE",
    }


@pytest.mark.parametrize("side", ["event", "api"])
@pytest.mark.parametrize("field", ["id", "number"])
@pytest.mark.parametrize("invalid", [True, None, 0, -1, "12"])
def test_pr_event_and_api_reject_non_positive_or_non_exact_integer_identity(
    valid_contract, policy, repository_root, side, field, invalid
):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    current = _pull(binding)
    event_pull = copy.deepcopy(current)
    target = event_pull if side == "event" else current
    target[field] = invalid
    with pytest.raises(GovernanceError) as error:
        validate_pull_request(
            API(issue, comments, run, current),
            policy,
            repository_root,
            _event(event_pull),
        )
    assert error.value.finding.id == "PR-EVENT-SHAPE"


@pytest.mark.parametrize("side", ["event", "api"])
@pytest.mark.parametrize("ref_name", ["base", "head"])
@pytest.mark.parametrize(
    "invalid_sha",
    [
        True,
        None,
        "A" * 40,
        "a" * 39,
        "g" * 40,
    ],
)
def test_pr_event_and_api_reject_non_lowercase_full_commit_sha(
    valid_contract, policy, repository_root, side, ref_name, invalid_sha
):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    current = _pull(binding)
    event_pull = copy.deepcopy(current)
    target = event_pull if side == "event" else current
    target[ref_name]["sha"] = invalid_sha
    with pytest.raises(GovernanceError) as error:
        validate_pull_request(
            API(issue, comments, run, current),
            policy,
            repository_root,
            _event(event_pull),
        )
    assert error.value.finding.id == "PR-EVENT-SHAPE"


@pytest.mark.parametrize("side", ["repository", "event-base", "event-head", "api-base", "api-head"])
def test_repository_names_reject_path_and_control_oddities(
    valid_contract, policy, repository_root, side
):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    for invalid in _malformed_identity_vectors(repository_root)["invalid_repositories"]:
        current = _pull(binding)
        event = _event(copy.deepcopy(current))
        if side == "repository":
            event["repository"]["full_name"] = invalid
        elif side.startswith("event-"):
            event["pull_request"][side.removeprefix("event-")]["repo"]["full_name"] = invalid
        else:
            current[side.removeprefix("api-")]["repo"]["full_name"] = invalid
        with pytest.raises(GovernanceError) as error:
            validate_pull_request(
                API(issue, comments, run, current), policy, repository_root, event
            )
        assert error.value.finding.id in {"PR-REPOSITORY", "PR-EVENT-SHAPE"}


def test_repository_names_accept_documented_github_boundaries(repository_root):
    from github_governance.pr_binding import _repository_name

    for valid in _malformed_identity_vectors(repository_root)["valid_repositories"]:
        assert _repository_name(valid, "TEST", "invalid") == valid


def test_huge_positive_api_identity_has_no_invented_upper_bound(
    valid_contract, policy, repository_root
):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    current = _pull(binding)
    current["id"] = _malformed_identity_vectors(repository_root)["huge_integer"]
    result = validate_pull_request(
        API(issue, comments, run, current), policy, repository_root, _event(current)
    )
    assert result.binding.issue_number == 8


@pytest.mark.parametrize("side", ["event", "api"])
@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.update(body=None),
        lambda value: value.update(updated_at=False),
        lambda value: value.update(base=None),
        lambda value: value["head"].update(repo=None),
        lambda value: value["base"].update(sha=123),
    ],
)
def test_pr_event_and_api_malformed_snapshot_shapes_fail_closed(
    valid_contract, policy, repository_root, side, mutation
):
    from github_governance.pr_binding import binding_from_contract, validate_pull_request

    issue, comments, run, _ = _ready_evidence(valid_contract)
    binding = binding_from_contract(8, extract_contract(issue["body"]).contract).as_dict()
    current = _pull(binding)
    event_pull = copy.deepcopy(current)
    mutation(event_pull if side == "event" else current)
    with pytest.raises(GovernanceError) as error:
        validate_pull_request(
            API(issue, comments, run, current),
            policy,
            repository_root,
            _event(event_pull),
        )
    assert error.value.finding.id == "PR-EVENT-SHAPE"


@pytest.mark.parametrize(
    ("field", "invalid"),
    [
        ("number", True),
        ("number", None),
        ("number", 0),
        ("number", -1),
        ("number", "8"),
        ("body", False),
        ("updated_at", 0),
        ("labels", None),
    ],
)
def test_issue_api_snapshot_shape_fails_closed(
    valid_contract, policy, repository_root, field, invalid
):
    from github_governance.pr_binding import validate_ready_issue

    issue, comments, run, _ = _ready_evidence(valid_contract)
    issue[field] = invalid
    with pytest.raises(GovernanceError) as error:
        validate_ready_issue(
            API(issue, comments, run), policy, repository_root, 55, "owner/repo", 8
        )
    assert error.value.finding.id in {
        "PR-ISSUE-NUMBER",
        "PR-ISSUE-SHAPE",
        "PR-ISSUE-LABELS",
    }


def test_global_cli_exposes_pr_binding_without_changing_existing_commands(repository_root, tmp_path):
    command = [
        sys.executable,
        "-m",
        "github_governance",
        "pr-binding",
        "--mode",
        "handoff",
        "--issue-number",
        "8",
        "--policy",
        ".github/project-policy.yml",
        "--repository-root",
        ".",
    ]
    environment = dict(os.environ, PYTHONPATH=str(repository_root / ".github/scripts/governance"))
    result = subprocess.run(command, cwd=repository_root, env=environment, text=True, capture_output=True)
    assert result.returncode == 2
    output = json.loads(result.stderr)
    assert output["result"] == "FAIL"
    assert output["finding_ids"] == ["EVENT-FILE"]
    assert subprocess.run(
        [sys.executable, "-m", "github_governance", "transition", "--entity", "intake", "--from-state", "new", "--to-state", "triaged"],
        cwd=repository_root,
        env=environment,
        capture_output=True,
    ).returncode == 0


def test_workflows_are_read_only_base_code_and_required_names_are_stable(repository_root):
    handoff = yaml.safe_load((repository_root / WORKFLOW).read_text())
    pr = yaml.safe_load((repository_root / PR_WORKFLOW).read_text())
    ci = yaml.safe_load((repository_root / ".github/workflows/20-ci-build-test.yml").read_text())
    assert handoff["permissions"] == {}
    assert handoff["jobs"]["handoff"]["permissions"] == {
        "actions": "read", "contents": "read", "issues": "read"
    }
    contract_job = pr["jobs"]["engineering-contract"]
    assert contract_job["name"] == "Engineering Contract Validation"
    assert contract_job["permissions"] == {
        "actions": "read", "contents": "read", "issues": "read", "pull-requests": "read"
    }
    assert ci["jobs"]["configuration-validation"]["name"] == "Configuration Validation"
    assert ci["jobs"]["security-check"]["name"] == "Security Scanning"
    for workflow in (handoff, pr):
        assert workflow["permissions"] == {}
        for job in workflow["jobs"].values():
            assert job.get("timeout-minutes")
            assert job.get("permissions", {}).get("contents") != "write"
    sources = "\n".join((repository_root / path).read_text() for path in (WORKFLOW, PR_WORKFLOW))
    assert "python -m github_governance pr-binding" in sources
    assert "github.event.repository.default_branch" in (
        repository_root / WORKFLOW
    ).read_text()
    assert "github.event.pull_request.base.sha" in sources
    assert "github.event.pull_request.head.sha }}" not in sources
    assert not re.search(r"(?i)(git\s+push|git\s+checkout\s+-b|AI_API_|OPENAI|ANTHROPIC|dev_plan|AI Development Agent)", sources)
    assert "github.event.pull_request.body" not in sources
    assert "contents: write" not in "\n".join(path.read_text() for path in (repository_root / ".github/workflows").glob("*.yml"))
    for manifest in MANIFESTS:
        assert MODULE in (repository_root / manifest).read_text()


def test_pr_template_has_exact_machine_binding_fields(repository_root):
    text = (repository_root / ".github/PULL_REQUEST_TEMPLATE.md").read_text()
    assert text.count("<!-- engineering-binding:start -->") == 1
    assert text.count("<!-- engineering-binding:end -->") == 1
    for field in ("issue_number", "issue_revision", "subject_digest", "contract_hash", "base_commit"):
        assert f'"{field}"' in text

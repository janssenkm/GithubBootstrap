from __future__ import annotations

import copy
import re

import pytest
import yaml

import github_governance.events as governance_events

from github_governance.attestations import (
    SourceComment,
    build_operation,
    build_target,
    candidate_gate,
    execute_attestation,
    render_contract_body,
    render_receipt,
)
from github_governance.canonical import sha256_tagged, subject_digest
from github_governance.errors import GovernanceError
from github_governance.events import AuthorizedCommand


WORKFLOW = ".github/workflows/02-engineering-governance.yml"
MANIFESTS = (
    ".github/workflows/00-baseline-check.yml",
    ".github/workflows/10-pr-ai-review.yml",
    ".github/workflows/20-ci-build-test.yml",
)
SLICE4_ASSETS = (
    ".github/scripts/governance/github_governance/events.py",
    ".github/scripts/governance/github_governance/github_api.py",
    ".github/scripts/governance/github_governance/attestations.py",
    WORKFLOW,
)


class MemoryAPI:
    def __init__(self, issue, source):
        self.issue = copy.deepcopy(issue)
        self.comments = [copy.deepcopy(source)]
        self.next_id = 1000
        self.mutations = []

    def get_issue(self, number):
        return copy.deepcopy(self.issue)

    def update_issue(self, number, *, body=None, labels=None):
        if body is not None:
            self.issue["body"] = body
        if labels is not None:
            self.issue["labels"] = [{"name": value} for value in labels]
        self.issue["updated_at"] = "2026-08-23T02:00:00Z"
        self.mutations.append(("issue", number))
        return copy.deepcopy(self.issue)

    def list_comments(self, number):
        return copy.deepcopy(self.comments)

    def create_comment(self, number, body):
        comment = {"id": self.next_id, "body": body, "user": {"login": "github-actions[bot]", "type": "Bot"}}
        self.next_id += 1
        self.comments.append(comment)
        self.mutations.append(("comment", number))
        return copy.deepcopy(comment)

    def get_comment(self, identifier):
        return copy.deepcopy(next(comment for comment in self.comments if comment["id"] == identifier))

    def get_workflow_run(self, identifier):
        return {
            "id": identifier,
            "html_url": f"https://example.invalid/runs/{identifier}",
            "event": "issue_comment",
            "status": "completed",
            "conclusion": "success",
            "path": ".github/workflows/02-engineering-governance.yml",
            "head_sha": "0c934ebff5f442e5619136aaf95a106b7a677acd",
            "actor": {"login": "reviewer" if identifier == 88 else "approver"},
            "triggering_actor": {"login": "reviewer" if identifier == 88 else "approver"},
            "repository": {"full_name": "owner/repo"},
        }


def _source(body, actor, identifier=303):
    return {
        "id": identifier,
        "body": body,
        "created_at": "2026-08-23T01:00:00Z",
        "updated_at": "2026-08-23T01:00:00Z",
        "user": {"login": actor, "type": "User"},
    }


def _issue(body, author="author"):
    return {
        "id": 101,
        "number": 7,
        "body": body.decode(),
        "updated_at": "2026-08-23T00:00:00Z",
        "user": {"login": author},
        "labels": [{"name": "type:candidate"}, {"name": "state:draft"}],
    }


def test_review_attestation_recovers_idempotently(valid_contract, valid_body, policy):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")
    api = MemoryAPI(_issue(valid_body, "fixture-author"), _source(body, "reviewer"))
    source = SourceComment.from_api(api.comments[0])
    result = execute_attestation(api, 55, "owner/repo", 7, command, source, policy, 88, "https://example.invalid/runs/88", repository_root=".")
    assert result["result"] == "applied"
    assert len(api.comments) == 3
    assert execute_attestation(api, 55, "owner/repo", 7, command, source, policy, 88, "https://example.invalid/runs/88", repository_root=".")["result"] == "idempotent"
    assert len(api.comments) == 3


def test_candidate_gate_requires_current_review_approval_and_receipts(valid_contract, policy, repository_root):
    findings = candidate_gate(valid_contract, policy, repository_root, issue_author="author", comments=[])
    assert {finding.id for finding in findings} >= {"GATE-REVIEW-REQUIRED", "GATE-APPROVAL-REQUIRED"}
    forged = copy.deepcopy(valid_contract)
    digest = subject_digest(forged)
    forged["review"].update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    forged["approval"].update(
        decision="approved",
        actor="approver",
        decided_at="2026-08-23T01:00:00Z",
        evidence_ref="E-02",
        subject_revision=1,
        subject_digest=digest,
    )
    findings = candidate_gate(forged, policy, repository_root, issue_author="author", comments=[])
    assert "ATTESTATION-CHAIN-MISSING" in {finding.id for finding in findings}


def test_source_comment_edit_and_body_toctou_fail_closed(valid_contract, valid_body, policy):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")
    source_api = _source(body, "reviewer")
    api = MemoryAPI(_issue(valid_body, "fixture-author"), source_api)
    source = SourceComment.from_api(source_api)
    api.comments[0]["updated_at"] = "2026-08-23T01:01:00Z"
    with pytest.raises(GovernanceError) as error:
        execute_attestation(api, 55, "owner/repo", 7, command, source, policy, 88, "https://example.invalid/runs/88", repository_root=".")
    assert error.value.finding.id == "ATTESTATION-SOURCE-CHANGED"


def test_review_then_approval_yields_gate_pass(valid_contract, valid_body, policy, repository_root, monkeypatch):
    from conftest import render_body

    policy["trusted_issue_authors"].append("fixture-author")
    contract = copy.deepcopy(valid_contract)
    contract["evidence"].append(
        {
            "id": "E-02",
            "type": "human-decision",
            "locator": "issue:7#issuecomment-1002",
            "summary": "Approver accepted the current Candidate subject.",
            "captured_at": "2026-08-23T01:30:00Z",
            "content_sha256": None,
        }
    )
    contract["approval"]["evidence_ref"] = "E-02"
    digest = subject_digest(contract)
    review = copy.deepcopy(contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    review_body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    review_command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, review_body, "2026-08-23T01:00:00Z")
    api = MemoryAPI(_issue(render_body(contract), "fixture-author"), _source(review_body, "reviewer"))
    execute_attestation(api, 55, "owner/repo", 7, review_command, SourceComment.from_api(api.comments[0]), policy, 88, "https://example.invalid/runs/88", repository_root=".")

    approval_body = f"/approve-contract 1 {digest}"
    approval_source = _source(approval_body, "approver", 1002)
    api.comments.append(approval_source)
    api.next_id = 1003
    approval_command = AuthorizedCommand("approve", 1, digest, None, "approver", 1002, approval_body, "2026-08-23T01:00:00Z")
    execute_attestation(api, 55, "owner/repo", 7, approval_command, SourceComment.from_api(approval_source), policy, 89, "https://example.invalid/runs/89", repository_root=".")

    from github_governance.contract import extract_contract

    current = extract_contract(api.issue["body"]).contract
    assert candidate_gate(current, policy, repository_root, issue_author="fixture-author", comments=api.comments, api=api) == []
    assert candidate_gate(current, policy, repository_root, issue_author="fixture-author", comments=api.comments, api=api) == []

    class WrongHistoricalHeadAPI(MemoryAPI):
        def get_workflow_run(self, identifier):
            run = super().get_workflow_run(identifier)
            run["head_sha"] = "b" * 40
            return run

    wrong_head_api = WrongHistoricalHeadAPI(api.issue, api.comments[0])
    wrong_head_api.comments = copy.deepcopy(api.comments)
    assert "ATTESTATION-RUN-HEAD" in {
        finding.id
        for finding in candidate_gate(
            current,
            policy,
            repository_root,
            issue_author="fixture-author",
            comments=api.comments,
            api=wrong_head_api,
        )
    }

    policy["rollout_mode"] = "enforce"
    monkeypatch.setattr(
        governance_events,
        "_workflow_context",
        lambda event: (api, policy, 55, "owner/repo", 7, api.get_issue(7), current, (None, None)),
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_SHA", "b" * 40)
    assert governance_events._write_phase({}) == 0
    assert {label["name"] for label in api.issue["labels"]} == {"type:candidate", "state:gate-passed"}

    receipt_count = len(api.comments)
    replay = execute_attestation(
        api,
        55,
        "owner/repo",
        7,
        review_command,
        SourceComment.from_api(api.comments[0]),
        policy,
        90,
        "https://example.invalid/runs/90",
        repository_root=".",
    )
    assert replay["result"] == "idempotent"
    assert len(api.comments) == receipt_count

    reordered = copy.deepcopy(api.comments)
    reordered[2]["id"] = 2000
    assert "ATTESTATION-CHAIN-ORDER" in {
        finding.id
        for finding in candidate_gate(
            current,
            policy,
            repository_root,
            issue_author="fixture-author",
            comments=reordered,
            api=api,
        )
    }


def test_valid_intent_plus_external_exact_target_recovers_completed(valid_contract, valid_body, policy):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")
    source_api = _source(body, "reviewer")
    source = SourceComment.from_api(source_api)
    target = build_target(valid_contract, command)
    operation = build_operation(55, 101, "owner/repo", 7, valid_contract, target, command, source, 88, "https://example.invalid/runs/88")
    intent = {**operation, "phase": "intent"}
    api = MemoryAPI(_issue(render_contract_body(valid_body, target).encode(), "fixture-author"), source_api)
    api.comments.append({"id": 1000, "body": render_receipt(intent), "user": {"login": "github-actions[bot]", "type": "Bot"}})
    api.next_id = 1001
    result = execute_attestation(api, 55, "owner/repo", 7, command, source, policy, 99, "https://example.invalid/runs/99", repository_root=".")
    assert result["result"] == "recovered"


def test_approval_cannot_launder_a_directly_forged_review(valid_contract, policy):
    from conftest import render_body

    policy["trusted_issue_authors"].append("fixture-author")
    contract = copy.deepcopy(valid_contract)
    contract["evidence"].append(
        {
            "id": "E-02",
            "type": "human-decision",
            "locator": "issue:7#issuecomment-304",
            "summary": "Approval evidence pre-exists.",
            "captured_at": "2026-08-23T01:30:00Z",
            "content_sha256": None,
        }
    )
    contract["approval"]["evidence_ref"] = "E-02"
    digest = subject_digest(contract)
    contract["review"].update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/approve-contract 1 {digest}"
    source_api = _source(body, "approver", 304)
    api = MemoryAPI(_issue(render_body(contract), "fixture-author"), source_api)
    command = AuthorizedCommand("approve", 1, digest, None, "approver", 304, body, "2026-08-23T01:00:00Z")
    with pytest.raises(GovernanceError) as error:
        execute_attestation(
            api,
            55,
            "owner/repo",
            7,
            command,
            SourceComment.from_api(source_api),
            policy,
            89,
            "https://example.invalid/runs/89",
            repository_root=".",
        )
    assert error.value.finding.id == "ATTESTATION-CHAIN-MISSING"
    assert len(api.comments) == 1


def test_issue_body_change_between_reads_is_rejected(valid_contract, valid_body, policy):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")

    class ChangingAPI(MemoryAPI):
        reads = 0

        def get_issue(self, number):
            self.reads += 1
            if self.reads == 2:
                self.issue["body"] += "external narrative edit"
                self.issue["updated_at"] = "2026-08-23T01:59:59Z"
            return super().get_issue(number)

    api = ChangingAPI(_issue(valid_body, "fixture-author"), _source(body, "reviewer"))
    source = SourceComment.from_api(api.comments[0])
    with pytest.raises(GovernanceError) as error:
        execute_attestation(api, 55, "owner/repo", 7, command, source, policy, 88, "https://example.invalid/runs/88", repository_root=".")
    assert error.value.finding.id == "ATTESTATION-TOCTOU"


def test_issue_label_change_before_contract_write_is_rejected(valid_contract, valid_body, policy):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")

    class ChangingLabelsAPI(MemoryAPI):
        reads = 0

        def get_issue(self, number):
            self.reads += 1
            if self.reads == 2:
                self.issue["labels"] = [{"name": "type:candidate"}, {"name": "state:gate-passed"}]
            return super().get_issue(number)

    api = ChangingLabelsAPI(_issue(valid_body, "fixture-author"), _source(body, "reviewer"))
    with pytest.raises(GovernanceError) as error:
        execute_attestation(
            api,
            55,
            "owner/repo",
            7,
            command,
            SourceComment.from_api(api.comments[0]),
            policy,
            88,
            "https://example.invalid/runs/88",
            repository_root=".",
        )
    assert error.value.finding.id == "ATTESTATION-TOCTOU"
    assert not any(mutation[0] == "issue" for mutation in api.mutations)


@pytest.mark.parametrize(
    ("race_read", "finding", "comment_count"),
    [
        (4, "ATTESTATION-COMPLETION-TOCTOU", 2),
        (5, "ATTESTATION-COMPLETION-READBACK", 3),
    ],
)
def test_completion_receipt_checks_target_immediately_before_and_after_mutation(
    valid_contract, valid_body, policy, race_read, finding, comment_count
):
    policy["trusted_issue_authors"].append("fixture-author")
    digest = subject_digest(valid_contract)
    review = copy.deepcopy(valid_contract["review"])
    review.update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    body = f"/review-contract 1 {digest} {sha256_tagged(review)}"
    command = AuthorizedCommand("review", 1, digest, sha256_tagged(review), "reviewer", 303, body, "2026-08-23T01:00:00Z")

    class CompletionRaceAPI(MemoryAPI):
        reads = 0

        def get_issue(self, number):
            self.reads += 1
            if self.reads == race_read:
                self.issue["body"] += "\nconcurrent completion race"
                self.issue["updated_at"] = "2026-08-23T02:00:01Z"
            return super().get_issue(number)

    api = CompletionRaceAPI(_issue(valid_body, "fixture-author"), _source(body, "reviewer"))
    with pytest.raises(GovernanceError) as error:
        execute_attestation(
            api,
            55,
            "owner/repo",
            7,
            command,
            SourceComment.from_api(api.comments[0]),
            policy,
            88,
            "https://example.invalid/runs/88",
            repository_root=".",
        )
    assert error.value.finding.id == finding
    assert len(api.comments) == comment_count


def test_workflow_permissions_actions_and_checkout_are_fail_closed(repository_root):
    text = (repository_root / WORKFLOW).read_text(encoding="utf-8")
    workflow = yaml.safe_load(text)
    assert workflow["permissions"] == {}
    read_job = workflow["jobs"]["read-and-revalidate"]
    write_job = workflow["jobs"]["gated-write"]
    assert read_job["permissions"] == {"actions": "read", "contents": "read", "issues": "read"}
    assert write_job["permissions"] == {"actions": "read", "contents": "read", "issues": "write"}
    assert "outputs.mutation == 'true'" in write_job["if"]
    assert "outputs.mode == 'warn'" in write_job["if"]
    assert "outputs.mode == 'enforce'" in write_job["if"]
    assert "receipt runs" in read_job["steps"][3]["name"].lower()
    assert "revalidate" in write_job["steps"][3]["name"].lower()
    assert "contents: write" not in text
    assert re.findall(r"uses:\s*[^\s#]+@([^\s#]+)", text)
    assert all(re.fullmatch(r"[0-9a-f]{40}", value) for value in re.findall(r"uses:\s*[^\s#]+@([^\s#]+)", text))
    assert text.count("persist-credentials: false") == 2
    assert not re.search(r"(?i)(openai|anthropic|claude|gemini|AI_API_|git\s+push|git\s+checkout\s+-b|\beval\s*\(|\bexec\s*\()", text)
    assert "github.event.issue.body" not in text


def test_candidate_documents_exact_attestation_commands(repository_root):
    text = (repository_root / ".github/ISSUE_TEMPLATE/candidate.md").read_text(encoding="utf-8")
    assert "/review-contract <positive-integer-revision> sha256:<64-lowercase-hex-subject-digest> sha256:<64-lowercase-hex-review-block-digest>" in text
    assert "/approve-contract <positive-integer-revision> sha256:<64-lowercase-hex-subject-digest>" in text
    assert "do not add" in text.lower() or "do not add evidence" in text.lower()
    assert "pull requests" in text.lower()


def test_required_file_manifests_include_slice4_atomically(repository_root):
    for relative in MANIFESTS:
        text = (repository_root / relative).read_text(encoding="utf-8")
        for asset in SLICE4_ASSETS:
            assert asset in text
    for asset in SLICE4_ASSETS:
        assert (repository_root / asset).is_file()


def test_bot_malformed_and_duplicate_receipts_fail_candidate_gate(valid_contract, policy, repository_root):
    forged = copy.deepcopy(valid_contract)
    digest = subject_digest(forged)
    forged["review"].update(reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest)
    malformed = {
        "id": 900,
        "body": '<!-- github-governance-receipt:v1:{"phase":"intent"} -->',
        "user": {"login": "github-actions[bot]", "type": "Bot"},
    }
    findings = candidate_gate(forged, policy, repository_root, issue_author="author", comments=[malformed])
    assert "ATTESTATION-RECEIPT-SHAPE" in {finding.id for finding in findings}


@pytest.mark.parametrize("race", ["body", "updated_at", "labels"])
def test_gate_label_prewrite_rejects_changed_validated_snapshot(
    valid_contract, valid_body, policy, monkeypatch, race
):
    policy["rollout_mode"] = "enforce"
    issue = _issue(valid_body, "author")

    class RaceAPI:
        mutations = []

        def list_comments(self, number):
            return []

        def get_issue(self, number):
            changed = copy.deepcopy(issue)
            if race == "body":
                changed["body"] += "\nconcurrent narrative"
            elif race == "updated_at":
                changed["updated_at"] = "2026-08-23T00:00:01Z"
            else:
                changed["labels"] = [{"name": "type:candidate"}, {"name": "state:gate-passed"}]
            return changed

        def update_issue(self, number, *, body=None, labels=None):
            self.mutations.append((number, labels))
            return copy.deepcopy(issue)

    api = RaceAPI()
    monkeypatch.setattr(
        governance_events,
        "_workflow_context",
        lambda event: (api, policy, 55, "owner/repo", 7, issue, valid_contract, (None, None)),
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_SHA", valid_contract["base_commit"])
    with pytest.raises(GovernanceError) as error:
        governance_events._write_phase({})
    assert error.value.finding.id == "EVENT-LABEL-TOCTOU"
    assert api.mutations == []


@pytest.mark.parametrize("race", ["partial-label", "body", "revision", "labels", "updated_at"])
def test_gate_label_postwrite_requires_exact_readback(
    valid_contract, valid_body, policy, monkeypatch, race
):
    from conftest import render_body

    policy["rollout_mode"] = "enforce"
    initial = _issue(valid_body, "author")

    class RaceAPI:
        def __init__(self):
            self.issue = copy.deepcopy(initial)
            self.updated = False

        def list_comments(self, number):
            return []

        def get_issue(self, number):
            current = copy.deepcopy(self.issue)
            if self.updated and race == "body":
                current["body"] += "\npost-write narrative"
            elif self.updated and race == "revision":
                changed = copy.deepcopy(valid_contract)
                changed["issue_revision"] = 2
                current["body"] = render_body(changed).decode()
            elif self.updated and race == "labels":
                current["labels"] = [{"name": "type:candidate"}, {"name": "state:draft"}]
            elif self.updated and race == "updated_at":
                current["updated_at"] = "2026-08-23T00:00:02Z"
            return current

        def update_issue(self, number, *, body=None, labels=None):
            self.updated = True
            if race != "partial-label":
                self.issue["labels"] = [{"name": value} for value in labels]
                self.issue["updated_at"] = "2026-08-23T00:00:01Z"
            return copy.deepcopy(self.issue)

    api = RaceAPI()
    monkeypatch.setattr(
        governance_events,
        "_workflow_context",
        lambda event: (api, policy, 55, "owner/repo", 7, initial, valid_contract, (None, None)),
    )
    monkeypatch.setenv("GITHUB_RUN_ID", "99")
    monkeypatch.setenv("GITHUB_SHA", valid_contract["base_commit"])
    with pytest.raises(GovernanceError) as error:
        governance_events._write_phase({})
    assert error.value.finding.id == "EVENT-LABEL-READBACK"

from pathlib import Path
import io
import json
import zipfile

import pytest
import yaml

from github_governance.milestone_chain import (
    acceptance_intent, canonical_document, execute_acceptance, finalize_acceptance,
    finalize_review, load_stage_zip, operation_id, provisional_review,
)
from github_governance.canonical import sha256_tagged
from github_governance.milestones import build_snapshot
from github_governance.github_api import GitHubAPI, Response
from github_governance.errors import GovernanceError


CHAIN = {
    "50-milestone-review.yml": ("workflow_dispatch", "Milestone Review Capture"),
    "51-milestone-review-provisional.yml": ("workflow_run", "Milestone Review Provisional"),
    "52-milestone-review-finalize.yml": ("workflow_run", "Milestone Review Finalize"),
    "53-milestone-acceptance-intent.yml": ("issue_comment", "Milestone Acceptance Intent"),
    "54-milestone-acceptance-execute.yml": ("workflow_run", "Milestone Acceptance Execute"),
    "55-milestone-acceptance-finalize.yml": ("workflow_run", "Milestone Acceptance Finalize"),
}


def test_milestone_workflow_chain_is_exact(repository_root):
    root = repository_root / ".github/workflows"
    for filename, (trigger, name) in CHAIN.items():
        document = yaml.safe_load((root / filename).read_text())
        assert document["name"] == name
        assert set(document.get("on", document.get(True))) == {trigger}
        assert document["permissions"] == {}
        assert all("timeout-minutes" in job for job in document["jobs"].values())
    assert not (root / "51-milestone-acceptance.yml").exists()


def test_only_designated_chain_phases_can_write_issues(repository_root):
    root = repository_root / ".github/workflows"
    read_only = {"50-milestone-review.yml", "53-milestone-acceptance-intent.yml"}
    writers = set(CHAIN) - read_only
    for filename in read_only:
        assert "issues: write" not in (root / filename).read_text()
    for filename in writers:
        assert "issues: write" in (root / filename).read_text()


def test_later_phases_have_no_manual_or_repository_dispatch(repository_root):
    root = repository_root / ".github/workflows"
    for filename in set(CHAIN) - {"50-milestone-review.yml"}:
        document = yaml.safe_load((root / filename).read_text())
        triggers = set(document.get("on", document.get(True)))
        assert "workflow_dispatch" not in triggers
        assert "repository_dispatch" not in triggers


def test_chain_uses_repository_wide_non_cancelling_concurrency(repository_root):
    root = repository_root / ".github/workflows"
    for filename in CHAIN:
        text = (root / filename).read_text()
        assert "github.repository_id" in text
        assert "cancel-in-progress: false" in text


def test_workflow_run_checkout_never_executes_upstream_head(repository_root):
    root = repository_root / ".github/workflows"
    for filename in ("51-milestone-review-provisional.yml", "52-milestone-review-finalize.yml",
                     "54-milestone-acceptance-execute.yml", "55-milestone-acceptance-finalize.yml"):
        text = (root / filename).read_text()
        assert "ref: '${{ github.sha }}'" in text
        assert "ref: '${{ needs.resolve-source.outputs.head-sha }}'" not in text
        assert "ref: '${{ github.event.workflow_run.head_sha }}'" not in text
        assert "--upstream-artifact-id '${{ needs.resolve-source.outputs.artifact-id }}'" in text
        assert "--upstream-run-attempt '${{ github.event.workflow_run.run_attempt }}'" in text
        assert "--upstream-head-sha '${{ github.event.workflow_run.head_sha }}'" in text


def test_operation_id_is_stable_across_attempts_by_construction():
    assert operation_id(1, "review", 99, "sha256:" + "a" * 64) == operation_id(
        1, "review", 99, "sha256:" + "a" * 64)
    assert operation_id(1, "review", 99, "sha256:" + "a" * 64) != operation_id(
        1, "review", 100, "sha256:" + "a" * 64)


def test_old_attempt_event_against_latest_rest_attempt_rejects_before_write():
    api = ChainAPI()
    snapshot = {"milestone": {"id": 90, "number": 7}, "result": "candidate-complete",
                "candidate_complete": True, "blockers": []}
    capture = {"snapshot": snapshot, "digest": sha256_tagged(snapshot)}
    artifact = _install_stage(api, 50, ".github/workflows/50-milestone-review.yml", "workflow_dispatch",
                              500, "milestone-review-7-50-2", capture, "milestone-review.json")
    api.runs[50]["run_attempt"] = 2
    with pytest.raises(GovernanceError) as error:
        provisional_review(api, 50, artifact["id"], event_attempt=1, event_head_sha="a" * 40)
    assert error.value.finding.id == "MILESTONE-UPSTREAM" and not api.calls


def _zip(name, data):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, data)
    return stream.getvalue()


def _run(identifier, path, event, *, status="completed", conclusion="success", attempt=1):
    repo = {"id": 1, "full_name": "owner/repo"}
    return {"id": identifier, "run_attempt": attempt, "path": path, "event": event,
            "status": status, "conclusion": conclusion, "head_sha": "a" * 40,
            "actor": {"login": "maintainer"}, "triggering_actor": {"login": "maintainer"},
            "repository": repo, "head_repository": repo}


def _artifact(identifier, name, run_id, raw):
    import hashlib
    return {"id": identifier, "name": name, "expired": False, "workflow_run": {"id": run_id},
            "size_in_bytes": len(raw), "digest": "sha256:" + hashlib.sha256(raw).hexdigest()}


def _issue(number, body, *, login="github-actions[bot]", kind="Bot", updated=None):
    stamp = "2026-08-23T00:00:00Z"
    return {"id": 900 + number, "number": number, "body": body,
            "url": f"https://api.github.com/repos/owner/repo/issues/{number}",
            "html_url": f"https://github.com/owner/repo/issues/{number}",
            "user": {"login": login, "type": kind}, "labels": [{"name": "milestone:review"}],
            "milestone": {"id": 90, "number": 7}, "created_at": stamp,
            "updated_at": updated or stamp}


class ChainAPI:
    def __init__(self):
        self.repository = {"id": 1, "full_name": "owner/repo"}
        self.runs = {}
        self.artifacts = {}
        self.raw = {}
        self.issues = []
        self.comments = []
        self.milestone = {"id": 90, "number": 7, "state": "open", "description": "",
                          "updated_at": "2026-08-23T00:00:00Z"}
        self.calls = []

    def get_repository(self): return self.repository
    def get_workflow_run(self, identifier):
        if identifier not in self.runs: raise GovernanceError("GITHUB-API-NOT-FOUND", "missing", code=4)
        return self.runs[identifier]
    def list_workflow_run_artifacts(self, identifier): return self.artifacts.get(identifier, [])
    def download_artifact(self, identifier): return self.raw[identifier]
    def list_issues(self, **_): return list(self.issues)
    def get_issue(self, number): return next(x for x in self.issues if x["number"] == number)
    def create_issue(self, title, body, labels, *, milestone=None):
        self.calls.append(("POST issue", title, body, labels, milestone))
        value = _issue(9, body); self.issues.append(value); return value
    def update_issue(self, number, *, body=None, labels=None):
        self.calls.append(("PATCH issue", number, body, labels))
        value = self.get_issue(number); value["body"] = body; value["updated_at"] = "2026-08-23T00:00:01Z"; return value
    def get_comment(self, identifier): return next(x for x in self.comments if x["id"] == identifier)
    def list_comments(self, _): return list(self.comments)
    def create_comment(self, number, body):
        self.calls.append(("POST comment", number, body)); value = {"id": 700, "body": body,
          "issue_url": f"https://api.github.com/repos/owner/repo/issues/{number}",
          "user": {"login": "github-actions[bot]", "type": "Bot"},
          "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z"}; self.comments.append(value); return value
    def get_milestone(self, _): return dict(self.milestone)
    def update_milestone(self, number, *, state, description=None):
        self.calls.append(("PATCH milestone", number, state, description)); self.milestone.update(state=state, description=description); return dict(self.milestone)
    def list_milestone_items(self, _): return []


def _install_stage(api, run_id, path, event, artifact_id, name, document, filename="phase-result.json"):
    raw = _zip(filename, canonical_document(document))
    api.runs[run_id] = _run(run_id, path, event)
    artifact = _artifact(artifact_id, name, run_id, raw)
    api.artifacts[run_id] = [artifact]; api.raw[artifact_id] = raw
    return artifact


def _envelope(api, phase, run_id, artifact, subject):
    run = api.runs[run_id]; digest = sha256_tagged(subject)
    return {"schema_version": 1, "phase": phase,
            "operation_id": operation_id(1, phase, run_id, digest), "repository": api.repository,
            "source": {"run_id": run_id, "run_attempt": run["run_attempt"], "path": run["path"],
                       "head_sha": run["head_sha"], "actor": run["actor"]["login"],
                       "triggering_actor": run["triggering_actor"]["login"], "artifact_id": artifact["id"],
                       "artifact_name": artifact["name"], "artifact_digest": artifact["digest"],
                       "artifact_size": artifact["size_in_bytes"]},
            "subject_digest": digest, "subject": subject}


def _intent_document(api, subject):
    subject = {**subject, "source_comment": {"id": 44}}
    digest = sha256_tagged(subject)
    return {"schema_version": 1, "phase": "acceptance-intent",
            "operation_id": operation_id(1, "acceptance", 44, digest),
            "repository": api.repository, "subject_digest": digest, "subject": subject}


def _install_execution(api, subject):
    upstream = _install_stage(api, 53, ".github/workflows/53-milestone-acceptance-intent.yml",
                              "issue_comment", 503, "milestone-acceptance-intent-53-1",
                              {"schema_version": 1})
    execution = _envelope(api, "acceptance-execute", 53, upstream, subject)
    artifact = _install_stage(api, 54, ".github/workflows/54-milestone-acceptance-execute.yml",
                              "workflow_run", 504, "milestone-acceptance-execute-54-1", execution)
    return execution, artifact


def _candidate_final_api():
    api = ChainAPI()
    capture = build_snapshot(api, api.milestone, [], {}, [], repository_id=1)
    artifact = _install_stage(api, 50, ".github/workflows/50-milestone-review.yml", "workflow_dispatch",
                              500, "milestone-review-7-50-1", capture, "milestone-review.json")
    provisional = provisional_review(api, 50, artifact["id"])
    _install_stage(api, 51, ".github/workflows/51-milestone-review-provisional.yml", "workflow_run",
                   501, "milestone-review-provisional-51-1", provisional)
    finalize_review(api, 51, 501)
    api.comments.append({"id": 44, "body": "/accept-milestone", "issue_url": api.issues[0]["url"],
                         "user": {"login": "alice", "type": "User"},
                         "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z"})
    api.calls.clear()
    return api


def test_stage_zip_requires_one_exact_canonical_member():
    value = {"schema_version": 1, "subject": {"id": 7}}
    assert load_stage_zip(_zip("phase-result.json", canonical_document(value)), "phase-result.json") == value
    with pytest.raises(GovernanceError):
        load_stage_zip(_zip("../phase-result.json", canonical_document(value)), "phase-result.json")
    with pytest.raises(GovernanceError):
        load_stage_zip(_zip("phase-result.json", json.dumps(value, indent=2)), "phase-result.json")


def test_stage_zip_rejects_duplicate_members_symlink_and_compression_bomb():
    value = canonical_document({"schema_version": 1})
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("phase-result.json", value)
        archive.writestr("phase-result.json", value)
    with pytest.raises(GovernanceError):
        load_stage_zip(stream.getvalue(), "phase-result.json")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        info = zipfile.ZipInfo("phase-result.json")
        info.external_attr = (0o120777 << 16)
        archive.writestr(info, value)
    with pytest.raises(GovernanceError):
        load_stage_zip(stream.getvalue(), "phase-result.json")

    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("phase-result.json", '"' + ("a" * 100_000) + '"')
    with pytest.raises(GovernanceError):
        load_stage_zip(stream.getvalue(), "phase-result.json")


def test_workflow_resolvers_paginate_and_require_exact_artifact_binding(repository_root):
    root = repository_root / ".github/workflows"
    for filename in ("51-milestone-review-provisional.yml", "52-milestone-review-finalize.yml",
                     "54-milestone-acceptance-execute.yml", "55-milestone-acceptance-finalize.yml"):
        text = (root / filename).read_text()
        assert "github.paginate" in text
        assert "workflow_run?.id===run.id" in text or "workflow_run?.id === run.id" in text
        assert "size_in_bytes" in text and "sha256:[0-9a-f]{64}" in text
        assert ".startsWith(" not in text and ".endsWith(" not in text
        for token in ("p.run_attempt!==run.run_attempt", "p.path!==run.path", "p.event!==run.event",
                      "p.head_sha!==run.head_sha", "p.actor?.login!==run.actor?.login",
                      "p.actor?.type!==run.actor?.type", "p.repository?.id!==run.repository.id",
                      "p.head_repository?.id!==run.head_repository.id", "github.rest.git.getRef",
                      "p.triggering_actor?.login!==run.triggering_actor?.login",
                      "p.triggering_actor?.type!==run.triggering_actor?.type",
                      "!p.triggering_actor?.login", "!p.triggering_actor?.type",
                      "!run.triggering_actor?.login", "!run.triggering_actor?.type",
                      "defaultRef.object.sha!==context.sha"):
            assert token in text


def test_resolve_source_permissions_are_exact(repository_root):
    root = repository_root / ".github/workflows"
    for filename in ("51-milestone-review-provisional.yml", "52-milestone-review-finalize.yml",
                     "54-milestone-acceptance-execute.yml", "55-milestone-acceptance-finalize.yml"):
        document = yaml.safe_load((root / filename).read_text())
        assert document["jobs"]["resolve-source"]["permissions"] == {"actions": "read", "contents": "read"}


def test_artifact_resolver_transport_reads_second_page_and_rejects_duplicate_id():
    class Pages:
        def __init__(self, duplicate=False): self.duplicate = duplicate; self.calls = []
        def __call__(self, method, path, *, params=None, body=None):
            self.calls.append((method, path, params, body)); page = params["page"]
            if page == 1:
                return Response(200, {"artifacts": [{"id": x} for x in range(1, 101)]})
            return Response(200, {"artifacts": [{"id": 100 if self.duplicate else 101}]})
    pages = Pages(); assert len(GitHubAPI(pages, "owner/repo").list_workflow_run_artifacts(7)) == 101
    assert [call[2]["page"] for call in pages.calls] == [1, 2]
    with pytest.raises(GovernanceError) as error:
        GitHubAPI(Pages(duplicate=True), "owner/repo").list_workflow_run_artifacts(7)
    assert error.value.finding.id == "GITHUB-API-PAGINATION"


def test_execute_binds_current_run_identity(repository_root):
    text = (repository_root / ".github/workflows/54-milestone-acceptance-execute.yml").read_text()
    assert "--current-run-id '${{ github.run_id }}'" in text
    assert "--current-run-attempt '${{ github.run_attempt }}'" in text


def test_blocked_capture_survives_provisional_and_final_and_acceptance_rejects_without_write():
    api = ChainAPI()
    snapshot = {"milestone": {"id": 90, "number": 7}, "result": "blocked",
                "candidate_complete": False, "blockers": ["PR #2 failed"]}
    capture = {"schema_version": 1, "snapshot": snapshot, "digest": sha256_tagged(snapshot)}
    artifact = _install_stage(api, 50, ".github/workflows/50-milestone-review.yml", "workflow_dispatch",
                              500, "milestone-review-7-50-1", capture, "milestone-review.json")
    provisional = provisional_review(api, 50, artifact["id"])
    assert provisional["subject"]["result"] == "blocked"
    _install_stage(api, 51, ".github/workflows/51-milestone-review-provisional.yml", "workflow_run",
                   501, "milestone-review-provisional-51-1", provisional)
    final = finalize_review(api, 51, 501)
    assert final["subject"]["result"] == "blocked" and final["subject"]["blockers"]
    api.comments.append({"id": 44, "body": "/accept-milestone",
                         "issue_url": api.issues[0]["url"], "user": {"login": "alice", "type": "User"},
                         "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z"})
    writes = len(api.calls)
    with pytest.raises(GovernanceError) as captured_error:
        acceptance_intent(api, 9, 44, {"trusted_milestone_acceptors": ["alice"], "required_milestone_checks": []})
    assert captured_error.value.finding.id == "MILESTONE-INTENT"
    assert len(api.calls) == writes


@pytest.mark.parametrize(("login", "kind"), [("mallory", "User"), ("dependabot[bot]", "Bot")])
def test_user_or_other_bot_cannot_claim_provisional_exact_body(login, kind):
    api = ChainAPI()
    snapshot = {"milestone": {"id": 90, "number": 7}, "result": "candidate-complete",
                "candidate_complete": True, "blockers": []}
    capture = {"snapshot": snapshot, "digest": sha256_tagged(snapshot)}
    artifact = _install_stage(api, 50, ".github/workflows/50-milestone-review.yml", "workflow_dispatch",
                              500, "milestone-review-7-50-1", capture, "milestone-review.json")
    # Learn canonical body without permitting a lasting write, then seed an unauthoritative collision.
    provisional = provisional_review(api, 50, artifact["id"])
    body = api.issues[0]["body"]; api.issues = [_issue(8, body, login=login, kind=kind)]; api.calls.clear()
    result = provisional_review(api, 50, artifact["id"])
    assert result["review_issue"]["number"] == 9
    assert [x[0] for x in api.calls] == ["POST issue"]


@pytest.mark.parametrize("mutation", [
    lambda value: {**value, "phase": "wrong"},
    lambda value: {**value, "subject_digest": "sha256:" + "0" * 64},
    lambda value: {**value, "extra": True},
    lambda value: {**value, "schema_version": 2},
    lambda value: {**value, "repository": {"id": 2, "full_name": "evil/repo"}},
    lambda value: {**value, "operation_id": "sha256:" + "0" * 64},
    lambda value: {**value, "source": {"run_id": 999}},
])
def test_wrong_stage_phase_digest_or_schema_causes_zero_writes(mutation):
    api = ChainAPI(); subject = {"milestone": {"id": 90, "number": 7}}
    envelope = {"schema_version": 1, "phase": "review-provisional", "operation_id": "sha256:" + "1" * 64,
                "repository": api.repository, "source": {}, "subject_digest": sha256_tagged(subject),
                "subject": subject, "review_issue": {"id": 909, "number": 9}}
    _install_stage(api, 51, ".github/workflows/51-milestone-review-provisional.yml", "workflow_run",
                   501, "milestone-review-provisional-51-1", mutation(envelope))
    with pytest.raises(GovernanceError): finalize_review(api, 51, 501)
    assert not api.calls


@pytest.mark.parametrize("attack", ["extra", "capture-run", "capture-artifact"])
def test_final_marker_extra_or_forged_source_provenance_rejects_without_write(attack):
    api = _candidate_final_api(); issue = api.issues[0]
    prefix = "<!-- github-bootstrap:milestone-review-final "
    raw = issue["body"].split(prefix, 1)[1].split(" -->", 1)[0]
    marker = json.loads(raw)
    if attack == "extra": marker["extra"] = True
    elif attack == "capture-run": marker["capture_source"]["run_id"] = 999
    else: marker["capture_source"]["artifact_id"] = 999
    issue["body"] = prefix + canonical_document(marker).strip() + " -->\n# Milestone review"
    with pytest.raises(GovernanceError):
        acceptance_intent(api, 9, 44, {"trusted_milestone_acceptors": ["alice"], "required_milestone_checks": []})
    assert not api.calls


def test_post_review_current_item_drift_rejects_before_intent_artifact():
    api = _candidate_final_api()
    api.list_milestone_items = lambda _: [{"id": 101, "number": 1, "state": "open",
                                           "updated_at": "2026-08-23T00:00:00Z", "labels": []}]
    with pytest.raises(GovernanceError) as error:
        acceptance_intent(api, 9, 44, {"trusted_milestone_acceptors": ["alice"], "required_milestone_checks": []})
    assert error.value.finding.id == "MILESTONE-SPEC-CHANGED"
    assert not api.calls


def test_execute_closed_on_entry_with_exact_marker_pauses_without_patch():
    api = ChainAPI(); api.milestone.update(state="closed")
    subject = {"milestone": {"id": 90, "number": 7}, "review_issue": {"number": 9},
               "original_description": "", "review_digest": "sha256:" + "2" * 64}
    intent = _intent_document(api, subject)
    _install_stage(api, 53, ".github/workflows/53-milestone-acceptance-intent.yml", "issue_comment",
                   503, "milestone-acceptance-intent-53-1", intent)
    api.runs[54] = _run(54, ".github/workflows/54-milestone-acceptance-execute.yml", "workflow_run",
                        status="in_progress", conclusion=None)
    with pytest.raises(GovernanceError) as error:
        execute_acceptance(api, 53, 503, 54, 1)
    assert error.value.finding.id == "MILESTONE-TRANSITION-PAUSED"
    assert not [x for x in api.calls if x[0] == "PATCH milestone"]


def test_execute_response_loss_after_actual_patch_reconciles_exactly_one_patch():
    class LostPatchAPI(ChainAPI):
        def __init__(self): super().__init__(); self.lost = False
        def update_milestone(self, number, *, state, description=None):
            value = super().update_milestone(number, state=state, description=description)
            if not self.lost:
                self.lost = True
                raise GovernanceError("GITHUB-API-TRANSIENT", "lost", code=4)
            return value
    api = LostPatchAPI()
    subject = {"milestone": {"id": 90, "number": 7}, "review_issue": {"number": 9},
               "original_description": "", "review_digest": "sha256:" + "2" * 64}
    intent = _intent_document(api, subject)
    _install_stage(api, 53, ".github/workflows/53-milestone-acceptance-intent.yml", "issue_comment",
                   503, "milestone-acceptance-intent-53-1", intent)
    api.runs[54] = _run(54, ".github/workflows/54-milestone-acceptance-execute.yml", "workflow_run",
                        status="in_progress", conclusion=None)
    result = execute_acceptance(api, 53, 503, 54, 1)
    assert result["phase"] == "acceptance-execute"
    assert len([x for x in api.calls if x[0] == "PATCH milestone"]) == 1


@pytest.mark.parametrize("forgery", [
    {"user": {"login": "mallory", "type": "User"}},
    {"user": {"login": "github-actions[bot]", "type": "Bot"}, "issue_url": "https://api.github.com/repos/owner/repo/issues/8"},
    {"user": {"login": "github-actions[bot]", "type": "Bot"}, "updated_at": "2026-08-23T00:00:01Z"},
    {"user": {"login": "dependabot[bot]", "type": "Bot"}},
])
def test_finalize_ignores_unauthoritative_same_body_receipts(forgery):
    api = ChainAPI(); api.milestone.update(state="closed", description="bound")
    subject = {"milestone": {"id": 90, "number": 7},
               "review_issue": {"id": 909, "number": 9, "api_url": "https://api.github.com/repos/owner/repo/issues/9", "html_url": "https://github.com/owner/repo/issues/9"},
               "description": "bound", "marker": {"operation_id": "sha256:" + "3" * 64,
               "execution": {"run_id": 54, "run_attempt": 1, "path": ".github/workflows/54-milestone-acceptance-execute.yml", "head_sha": "a" * 40}}}
    execution, artifact = _install_execution(api, subject)
    # Build the exact receipt body from a clean first run, then replay with a forged collision.
    finalize_acceptance(api, 54, artifact["id"]); body = api.comments[0]["body"]
    api.comments = [{"id": 1, "body": body, "issue_url": "https://api.github.com/repos/owner/repo/issues/9",
                     "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z",
                     "user": {"login": "github-actions[bot]", "type": "Bot"}, **forgery}]
    api.calls.clear(); result = finalize_acceptance(api, 54, artifact["id"])
    assert result["result"] == "completed"
    assert [x[0] for x in api.calls] == ["POST comment"]


def test_finalize_pauses_on_authoritative_conflicting_receipt_without_write():
    api = ChainAPI(); api.milestone.update(state="closed", description="bound")
    issue_ref = {"id": 909, "number": 9, "api_url": "https://api.github.com/repos/owner/repo/issues/9", "html_url": "https://github.com/owner/repo/issues/9"}
    subject = {"milestone": {"id": 90, "number": 7}, "review_issue": issue_ref,
               "description": "bound", "marker": {"operation_id": "sha256:" + "3" * 64,
               "execution": {"run_id": 54, "run_attempt": 1, "path": ".github/workflows/54-milestone-acceptance-execute.yml", "head_sha": "a" * 40}}}
    execution, artifact = _install_execution(api, subject)
    finalize_acceptance(api, 54, artifact["id"]); body = api.comments[0]["body"]
    raw = body.split("completed ", 1)[1].split(" -->", 1)[0]; receipt = json.loads(raw); receipt["result"] = "forged"
    api.comments = [{**api.comments[0], "body": "<!-- github-bootstrap:milestone-acceptance-completed " + canonical_document(receipt).strip() + " -->\nMilestone acceptance completed."}]
    api.calls.clear()
    with pytest.raises(GovernanceError) as error: finalize_acceptance(api, 54, artifact["id"])
    assert error.value.finding.id == "MILESTONE-TRANSITION-PAUSED" and not api.calls

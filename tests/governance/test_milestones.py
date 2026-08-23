from __future__ import annotations

import copy
import json
import io
import zipfile

import pytest

from github_governance.errors import GovernanceError
from github_governance.github_api import GitHubAPI, Response
from github_governance.milestones import (
    accept_milestone,
    build_snapshot,
    prepare_review,
    load_review_artifact,
    parse_review_marker,
)


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


class AcceptanceTransport:
    def __init__(self, *, lose_patch=False, lose_receipt=False, seeded_comments=None):
        self.calls = []
        self.comments = {}
        self.next_id = 10
        self.closed = False
        self.lose_patch = lose_patch
        self.lose_receipt = lose_receipt
        self.seeded_comments = list(seeded_comments or [])

    def __call__(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        if method == "GET" and path.endswith("/comments"):
            return Response(200, self.seeded_comments + [self._comment(key, value) for key, value in sorted(self.comments.items())])
        if method == "POST" and path.endswith("/comments"):
            identifier = self.next_id
            self.next_id += 1
            self.comments[identifier] = body["body"]
            if self.lose_receipt and "milestone-acceptance " in body["body"]:
                raise GovernanceError("GITHUB-API-TRANSIENT", "lost", code=4)
            return Response(201, self._comment(identifier, body["body"]))
        if method == "GET" and "/issues/comments/" in path:
            identifier = int(path.rsplit("/", 1)[-1])
            return Response(200, self._comment(identifier, self.comments[identifier]))
        if method == "GET" and "/milestones/" in path:
            return Response(200, milestone("closed" if self.closed else "open"))
        if method == "GET" and path.endswith("/issues"):
            return Response(200, [])
        if method == "PATCH" and "/milestones/" in path:
            self.closed = True
            if self.lose_patch:
                raise GovernanceError("GITHUB-API-TRANSIENT", "lost", code=4)
            return Response(200, milestone("closed"))
        raise AssertionError((method, path, params, body))

    @staticmethod
    def _comment(identifier, body):
        return {"id": identifier, "body": body,
                "issue_url": "https://api.github.com/repos/owner/repo/issues/9",
                "user": {"login": "github-actions[bot]", "type": "Bot"},
                "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z"}


def milestone(state="open"):
    return {"id": 90, "number": 7, "title": "V2 / release", "state": state,
            "updated_at": "2026-08-23T00:00:00Z"}


def issue(number=1, state="closed"):
    return {"id": 100 + number, "number": number, "title": f"Issue {number}",
            "state": state, "updated_at": "2026-08-23T00:00:00Z",
            "labels": ["type:engineering", "state:done"],
            "contract": {"issue_revision": 1, "contract_hash": "sha256:" + "c" * 64,
                         "acceptance_hash": "sha256:" + "d" * 64}}


def pr(number=2, merged=True):
    return {"kind": "pull_request", "id": 200 + number, "number": number, "title": f"PR {number}",
            "state": "closed", "updated_at": "2026-08-23T00:00:00Z",
            "pull_request": {"url": "x"}, "merged_at": "2026-08-22T00:00:00Z" if merged else None,
            "head": {"sha": "a" * 40}, "merge_commit_sha": "b" * 40}


def provenance():
    return {
        "run": {"id": 1, "run_attempt": 1, "path": ".github/workflows/50-milestone-review.yml",
                "event": "workflow_dispatch", "status": "completed", "conclusion": "success",
                "head_sha": "e" * 40, "actor": {"login": "maintainer"},
                "repository": {"id": 1, "full_name": "owner/repo"}},
        "artifact": {"id": 81, "name": "milestone-review-7-1-1", "expired": False,
                     "workflow_run": {"id": 1}, "archive_download_url": "https://api.example/artifacts/81/zip"},
    }


def operation_context():
    return {"review_issue": {"id": 901, "number": 9,
                              "url": "https://github.com/owner/repo/issues/9",
                              "api_url": "https://api.github.com/repos/owner/repo/issues/9"},
            "source_comment_id": 501,
            "workflow": {"run_id": 601, "run_attempt": 1,
                         "path": ".github/workflows/51-milestone-acceptance.yml",
                         "head_sha": "f" * 40}}


def test_snapshot_is_canonical_separates_prs_and_requires_human_doc_confirmation():
    api = GitHubAPI(Transport([]), "owner/repo")
    check = {"id": 71, "name": "Quality Gate", "status": "completed", "conclusion": "success", "details_url": "https://checks.example/1"}
    first = build_snapshot(api, milestone(), [pr(), issue()], {2: {"Quality Gate": check}}, ["Quality Gate"])
    second = build_snapshot(api, copy.deepcopy(milestone()), [issue(), pr()], {2: {"Quality Gate": check}}, ["Quality Gate"])
    assert first == second
    assert first["digest"].startswith("sha256:")
    assert [item["number"] for item in first["snapshot"]["issues"]] == [1]
    assert [item["number"] for item in first["snapshot"]["pull_requests"]] == [2]
    assert first["snapshot"]["documentation_status"] == "human-confirmation-required"
    assert first["snapshot"]["required_checks"] == ["Quality Gate"]
    assert first["snapshot"]["pull_requests"][0]["required_checks"] == [{
        "id": 71, "name": "Quality Gate", "status": "completed",
        "conclusion": "success", "details_url": "https://checks.example/1",
    }]
    assert "audit" not in str(first).lower()


def test_milestone_updated_at_is_observation_not_digest_subject():
    first = milestone()
    second = milestone()
    second["updated_at"] = "2026-08-24T00:00:00Z"
    api = GitHubAPI(Transport([]), "owner/repo")
    assert build_snapshot(api, first, [], {}, [])["digest"] == build_snapshot(api, second, [], {}, [])["digest"]


def test_review_issue_publication_does_not_self_invalidate_snapshot():
    api = GitHubAPI(Transport([]), "owner/repo")
    before = build_snapshot(api, milestone(), [], {}, [])
    review_issue = {"id": 901, "number": 9, "state": "open",
                    "updated_at": "2026-08-24T00:00:00Z", "labels": ["milestone:review"]}
    changed_milestone = milestone()
    changed_milestone["updated_at"] = "2026-08-24T00:00:00Z"
    after = build_snapshot(api, changed_milestone, [review_issue], {}, [])
    assert after["digest"] == before["digest"]


def test_missing_or_extra_check_name_never_counts_as_configured_check():
    api = GitHubAPI(Transport([]), "owner/repo")
    result = build_snapshot(api, milestone(), [pr()], {2: {"Quality Gate / unit": "success"}}, ["Quality Gate"])
    assert result["snapshot"]["pull_requests"][0]["required_checks"] == [{"name": "Quality Gate", "status": "missing"}]
    assert result["snapshot"]["candidate_complete"] is False


@pytest.mark.parametrize("mode,created", [("dry-run", False), ("shadow", False), ("warn", True), ("enforce", True)])
def test_review_rollout_write_semantics(mode, created):
    review = {"digest": "sha256:" + "b" * 64, "snapshot": {"candidate_complete": True}}
    marker = {"schema_version": 1, "repository": {"id": 1, "full_name": "owner/repo"},
              "milestone": {"id": 90, "number": 7}, "snapshot_digest": review["digest"],
              "result": None, "workflow": {"path": ".github/workflows/50-milestone-review.yml",
              "run_id": 1, "run_attempt": 1, "head_sha": "e" * 40, "event": "workflow_dispatch",
              "actor": "maintainer", "conclusion": "success",
              "repository": {"id": 1, "full_name": "owner/repo"}},
              "artifact": {"id": 81, "name": "milestone-review-7-1-1",
              "archive_download_url": "https://api.example/artifacts/81/zip"},
              "review_issue": {"id": 1, "number": 9, "url": "https://github.com/owner/repo/issues/9"}}
    body = "<!-- github-bootstrap:milestone-review " + json.dumps(marker, sort_keys=True, separators=(",", ":")) + " -->\n# Milestone review\n\nResult: `blocked`\n\nDocumentation: human confirmation required.\n"
    confirmed = {"id": 1, "number": 9, "html_url": "https://github.com/owner/repo/issues/9", "labels": ["milestone:review"],
                 "user": {"login": "github-actions[bot]", "type": "Bot"},
                 "milestone": {"id": 90, "number": 7}, "body": body}
    responses = [Response(200, []), Response(201, {"id": 1, "number": 9, "html_url": "https://github.com/owner/repo/issues/9"}),
                 Response(200, {"id": 1, "number": 9}), Response(200, confirmed)] if created else []
    transport = Transport(responses)
    result = prepare_review(GitHubAPI(transport, "owner/repo"), mode, milestone(), review, provenance=provenance())
    assert result["mutation"] == ("review-issue-created" if created else "summary-only")
    assert any(call[0] == "POST" for call in transport.calls) is created


@pytest.mark.parametrize(
    ("section", "field", "value"),
    [("run", "path", ".github/workflows/evil.yml"), ("run", "event", "pull_request"),
     ("run", "conclusion", "failure"), ("run", "head_sha", "bad"),
     ("artifact", "id", True), ("artifact", "name", "forged"),
     ("artifact", "expired", True), ("artifact", "archive_download_url", "")],
)
def test_forged_run_or_artifact_provenance_fails_closed(section, field, value):
    forged = provenance()
    forged[section][field] = value
    with pytest.raises(GovernanceError) as captured:
        prepare_review(GitHubAPI(Transport([]), "owner/repo"), "enforce", milestone(),
                       {"digest": "sha256:" + "b" * 64, "snapshot": {"result": "candidate-complete"}},
                       provenance=forged)
    assert captured.value.finding.id == "MILESTONE-PROVENANCE"


def test_accept_requires_exact_command_actor_open_milestone_and_current_digest():
    api = GitHubAPI(Transport([]), "owner/repo")
    with pytest.raises(GovernanceError) as captured:
        accept_milestone(api, "/accept-milestone please", "Alice", ["alice"], milestone(), "sha256:" + "a" * 64, "sha256:" + "a" * 64)
    assert captured.value.finding.id == "MILESTONE-COMMAND"
    with pytest.raises(GovernanceError) as captured:
        accept_milestone(api, "/accept-milestone", "mallory", ["alice"], milestone(), "sha256:" + "a" * 64, "sha256:" + "a" * 64)
    assert captured.value.finding.id == "MILESTONE-ACTOR"
    with pytest.raises(GovernanceError) as captured:
        accept_milestone(GitHubAPI(Transport([Response(200, [])]), "owner/repo"), "/accept-milestone", "alice", ["alice"], milestone("closed"), "sha256:" + "a" * 64, "sha256:" + "a" * 64, operation_context=operation_context())
    assert captured.value.finding.id == "MILESTONE-STATE"
    with pytest.raises(GovernanceError) as captured:
        accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone(), "sha256:" + "a" * 64, "sha256:" + "b" * 64)
    assert captured.value.finding.id == "MILESTONE-SPEC-CHANGED"


def test_accept_rereads_then_single_close_patch_readback_and_receipt():
    current = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [])["digest"]
    transport = AcceptanceTransport()
    api = GitHubAPI(transport, "owner/repo")
    # Empty snapshots have a stable digest; acceptance recomputes twice around intent.
    result = accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone(), current, current, review_issue_number=9, operation_context=operation_context())
    assert result["status"] == "completed"
    patches = [call for call in transport.calls if call[0] == "PATCH"]
    assert len(patches) == 1 and patches[0][3] == {"state": "closed"}
    assert transport.calls[-2][0:2] == ("POST", "/repos/owner/repo/issues/9/comments")
    assert transport.calls[-1][0:2] == ("GET", "/repos/owner/repo/issues/comments/11")


def test_response_loss_after_close_is_transition_paused_and_never_reopens():
    transport = AcceptanceTransport(lose_patch=True)
    api = GitHubAPI(transport, "owner/repo")
    current = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [])["digest"]
    with pytest.raises(GovernanceError) as captured:
        accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone(), current, current, review_issue_number=9, operation_context=operation_context())
    assert captured.value.finding.id == "MILESTONE-TRANSITION-PAUSED"
    assert any("transition-paused" in body for body in transport.comments.values())
    assert not any(call[0] == "PATCH" and call[3] == {"state": "open"} for call in transport.calls)


def test_receipt_response_loss_reconciles_exact_remote_receipt():
    transport = AcceptanceTransport(lose_receipt=True)
    api = GitHubAPI(transport, "owner/repo")
    current = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [])["digest"]
    result = accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone(), current, current, review_issue_number=9, operation_context=operation_context())
    assert result["status"] == "completed"
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == 1


def test_replay_closed_same_digest_returns_already_completed_without_patch():
    transport = AcceptanceTransport()
    api = GitHubAPI(transport, "owner/repo")
    current = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [])["digest"]
    assert accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone(), current, current, review_issue_number=9, operation_context=operation_context())["status"] == "completed"
    before = len([call for call in transport.calls if call[0] == "PATCH"])
    assert accept_milestone(api, "/accept-milestone", "alice", ["alice"], milestone("closed"), current, current, review_issue_number=9, operation_context=operation_context())["status"] == "already-completed"
    assert len([call for call in transport.calls if call[0] == "PATCH"]) == before


@pytest.mark.parametrize("forgery", [
    {"id": 77, "body": "<!-- github-bootstrap:milestone-acceptance {} -->\nMilestone acceptance completed.",
     "issue_url": "https://api.github.com/repos/owner/repo/issues/9",
     "user": {"login": "mallory", "type": "User"},
     "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:00Z"},
    {"id": 78, "body": "<!-- github-bootstrap:milestone-acceptance {} -->\nMilestone acceptance completed.",
     "issue_url": "https://api.github.com/repos/owner/repo/issues/8",
     "user": {"login": "github-actions[bot]", "type": "Bot"},
     "created_at": "2026-08-23T00:00:00Z", "updated_at": "2026-08-23T00:00:01Z"},
])
def test_unauthoritative_same_marker_comments_are_ignored(forgery):
    transport = AcceptanceTransport(seeded_comments=[forgery])
    current = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [])["digest"]
    result = accept_milestone(GitHubAPI(transport, "owner/repo"), "/accept-milestone", "alice", ["alice"],
                              milestone(), current, current, review_issue_number=9,
                              operation_context=operation_context())
    assert result["status"] == "completed"


def test_numeric_artifact_name_is_safe():
    result = prepare_review(GitHubAPI(Transport([]), "owner/repo"), "dry-run", milestone(), {"digest": "sha256:" + "b" * 64, "snapshot": {}})
    assert result["artifact_name"] == "milestone-review-7-1-1"


def artifact_zip(document, *, name="milestone-review.json"):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr(name, json.dumps(document, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")
    return stream.getvalue()


def artifact_document():
    review = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [], {}, [], repository_id=12)
    return {"schema_version": 1, "captured_at": "2026-08-23T00:01:00Z",
            "run": {"id": 44, "attempt": 2},
            "artifact": {"name": "milestone-review-7-44-2"},
            "rollout_mode": "warn", **review}


def test_review_artifact_zip_is_strict_and_self_consistent():
    document = artifact_document()
    assert load_review_artifact(artifact_zip(document), repository_id=12, milestone_id=90,
                                milestone_number=7, run_id=44, run_attempt=2,
                                artifact_name="milestone-review-7-44-2") == document


@pytest.mark.parametrize("name", ["../milestone-review.json", "nested/milestone-review.json", "other.json"])
def test_review_artifact_rejects_wrong_or_traversing_member(name):
    with pytest.raises(GovernanceError) as captured:
        load_review_artifact(artifact_zip(artifact_document(), name=name), repository_id=12,
                             milestone_id=90, milestone_number=7, run_id=44, run_attempt=2,
                             artifact_name="milestone-review-7-44-2")
    assert captured.value.finding.id == "MILESTONE-ARTIFACT"


def test_review_artifact_rejects_duplicate_json_keys():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        archive.writestr("milestone-review.json", '{"schema_version":1,"schema_version":1}\n')
    with pytest.raises(GovernanceError) as captured:
        load_review_artifact(stream.getvalue(), repository_id=12, milestone_id=90,
                             milestone_number=7, run_id=44, run_attempt=2,
                             artifact_name="milestone-review-7-44-2")
    assert captured.value.finding.id == "MILESTONE-ARTIFACT"


def test_review_marker_rejects_extra_fields_and_noncanonical_json():
    marker = {"schema_version": 1}
    body = "<!-- github-bootstrap:milestone-review " + json.dumps(marker) + " -->"
    with pytest.raises(GovernanceError) as captured:
        parse_review_marker(body)
    assert captured.value.finding.id == "MILESTONE-REVIEW-MARKER"


def test_real_pull_api_shape_with_explicit_kind_remains_a_pr():
    value = pr()
    value.pop("pull_request")
    check = {"id": 71, "name": "Quality Gate", "status": "completed", "conclusion": "success", "details_url": "https://checks.example/1"}
    result = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [value], {2: {"Quality Gate": check}}, ["Quality Gate"])
    assert not result["snapshot"]["issues"]
    assert result["snapshot"]["pull_requests"][0]["number"] == 2


def test_changed_check_id_changes_snapshot_digest():
    first = {"id": 71, "name": "Quality Gate", "status": "completed", "conclusion": "success", "details_url": "https://checks.example/1"}
    second = {**first, "id": 72}
    api = GitHubAPI(Transport([]), "owner/repo")
    assert build_snapshot(api, milestone(), [pr()], {2: {"Quality Gate": first}}, ["Quality Gate"])["digest"] != build_snapshot(api, milestone(), [pr()], {2: {"Quality Gate": second}}, ["Quality Gate"])["digest"]


@pytest.mark.parametrize("identifier", [True, 0, -1, "71"])
def test_invalid_check_ids_block(identifier):
    check = {"id": identifier, "name": "Quality Gate", "status": "completed", "conclusion": "success", "details_url": "https://checks.example/1"}
    result = build_snapshot(GitHubAPI(Transport([]), "owner/repo"), milestone(), [pr()], {2: {"Quality Gate": check}}, ["Quality Gate"])
    assert result["snapshot"]["result"] == "blocked"

from __future__ import annotations

import copy

import pytest
import yaml

from github_governance.canonical import contract_hash, subject_digest
from github_governance.errors import GovernanceError
from github_governance.events import authorize_issue_comment, parse_command
from github_governance.github_api import GitHubAPI, Response, urllib_transport
from github_governance.state import build_promotion_target


WORKFLOW = ".github/workflows/03-engineering-promotion.yml"
MANIFESTS = (
    ".github/workflows/00-baseline-check.yml",
    ".github/workflows/10-pr-ai-review.yml",
    ".github/workflows/20-ci-build-test.yml",
)


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.responses.pop(0)


def _event(contract, actor="approver"):
    return {
        "action": "created",
        "repository": {"id": 55, "full_name": "owner/repo"},
        "sender": {"login": actor},
        "issue": {
            "id": 101,
            "number": 7,
            "body": "not logged",
            "user": {"login": contract["provenance"]["created_by"]},
            "labels": [{"name": "type:candidate"}, {"name": "state:gate-passed"}],
        },
        "comment": {
            "id": 303,
            "body": "/promote",
            "created_at": "2026-08-23T01:00:00Z",
            "updated_at": "2026-08-23T01:00:00Z",
            "user": {"login": actor, "type": "User"},
        },
    }


def test_promote_command_is_exact_and_authorized(valid_contract, policy):
    assert parse_command("\t/promote\r\n").action == "promote"
    for body in ("/promote now", "> /promote", "```\n/promote\n```", "/Promote", "／promote"):
        assert parse_command(body) is None

    command = authorize_issue_comment(_event(valid_contract), valid_contract, policy)
    assert command.action == "promote"
    assert command.revision == valid_contract["issue_revision"]
    assert command.subject_digest == subject_digest(valid_contract)
    replay_event = _event(valid_contract)
    replay_event["issue"]["labels"][1]["name"] = "state:promoted"
    assert authorize_issue_comment(replay_event, valid_contract, policy).action == "promote"

    untrusted = _event(valid_contract, "outsider")
    with pytest.raises(GovernanceError) as error:
        authorize_issue_comment(untrusted, valid_contract, policy)
    assert error.value.finding.id == "EVENT-UNAUTHORIZED"
    bot = _event(valid_contract)
    bot["sender"] = {"login": "github-actions[bot]"}
    bot["comment"]["user"] = {"login": "github-actions[bot]", "type": "Bot"}
    with pytest.raises(GovernanceError) as error:
        authorize_issue_comment(bot, valid_contract, policy)
    assert error.value.finding.id == "EVENT-BOT-COMMAND"


def test_promotion_target_preserves_subject_and_freezes_contract(valid_contract):
    candidate = copy.deepcopy(valid_contract)
    digest = subject_digest(candidate)
    candidate["review"].update(
        reviewed_by="reviewer", result="pass", subject_revision=1, subject_digest=digest
    )
    candidate["approval"].update(
        decision="approved",
        actor="approver",
        decided_at="2026-08-23T00:30:00Z",
        evidence_ref="E-01",
        subject_revision=1,
        subject_digest=digest,
    )
    target = build_promotion_target(
        candidate,
        repository="owner/repo",
        candidate_number=7,
        actor="approver",
        frozen_at="2026-08-23T01:00:00Z",
    )
    assert target["status"] == "contracted"
    assert target["provenance"]["promoted_by"] == "approver"
    assert {source["role"] for source in target["provenance"]["sources"]} >= {"intake", "candidate"}
    assert target["issue_revision"] == candidate["issue_revision"]
    assert subject_digest(target) == subject_digest(candidate)
    assert target["freeze"]["contract_hash"] == contract_hash(target)


def test_issue_creation_and_repository_issue_listing_use_rest_pagination():
    first = [{"id": value, "number": value} for value in range(1, 101)]
    transport = Transport(
        [
            Response(200, first),
            Response(200, [{"id": 101, "number": 101}]),
            Response(201, {"id": 202, "number": 8, "body": "target"}),
        ]
    )
    api = GitHubAPI(transport, "owner/repo")
    assert len(api.list_issues()) == 101
    assert api.create_issue("Engineering contract", "target", ["type:engineering", "state:contracted"])["number"] == 8
    assert all("search" not in call[1] for call in transport.calls)


@pytest.mark.parametrize(
    ("status", "finding", "code"),
    [
        (403, "GITHUB-API-FORBIDDEN", 1),
        (404, "GITHUB-API-NOT-FOUND", 1),
        (409, "GITHUB-API-CONFLICT", 3),
        (422, "GITHUB-API-CONFLICT", 3),
        (429, "GITHUB-API-TRANSIENT", 4),
        (500, "GITHUB-API-TRANSIENT", 4),
        (503, "GITHUB-API-TRANSIENT", 4),
    ],
)
def test_exact_api_status_attack_matrix_fails_closed(status, finding, code):
    api = GitHubAPI(Transport([Response(status, {"message": "untrusted remote text"})]), "owner/repo")
    with pytest.raises(GovernanceError) as error:
        api.get_issue(7)
    assert error.value.finding.id == finding
    assert error.value.code == code
    assert "untrusted remote text" not in str(error.value)


@pytest.mark.parametrize(
    ("method", "response"),
    [
        ("issue", Response(200, "{malformed-json")),
        ("issue-list-missing-id", Response(200, [{"number": 7}])),
        ("comment-list-missing-id", Response(200, [{"body": "missing id"}])),
    ],
)
def test_malformed_api_shapes_fail_closed(method, response):
    api = GitHubAPI(Transport([response]), "owner/repo")
    with pytest.raises(GovernanceError) as error:
        if method == "issue":
            api.get_issue(7)
        elif method == "issue-list-missing-id":
            api.list_issues()
        else:
            api.list_comments(7)
    assert error.value.finding.id == "GITHUB-API-SHAPE"


def test_urllib_transport_rejects_malformed_json(monkeypatch):
    class Remote:
        status = 200
        headers = {}

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def read(self):
            return b"{not-json"

    monkeypatch.setattr("urllib.request.urlopen", lambda *_args, **_kwargs: Remote())
    transport = urllib_transport("not-logged")
    with pytest.raises(GovernanceError) as error:
        transport("GET", "/repos/owner/repo/issues/7")
    assert error.value.finding.id == "GITHUB-API-TRANSIENT"
    assert "not-logged" not in str(error.value)


@pytest.mark.parametrize("resource", ["issues", "comments"])
def test_rest_pagination_rejects_duplicate_entities(resource):
    if resource == "issues":
        first = [{"id": value, "number": value} for value in range(1, 101)]
        duplicate = {"id": 100, "number": 100}
    else:
        first = [{"id": value} for value in range(1, 101)]
        duplicate = {"id": 100}
    api = GitHubAPI(Transport([Response(200, first), Response(200, [duplicate])]), "owner/repo")
    with pytest.raises(GovernanceError) as error:
        if resource == "issues":
            api.list_issues()
        else:
            api.list_comments(7)
    assert error.value.finding.id == "GITHUB-API-PAGINATION"


def test_repository_issue_listing_excludes_pr_shaped_items_without_hiding_duplicates():
    response = [
        {"id": 1, "number": 7, "pull_request": {"url": "https://example.invalid/pr/7"}},
        {"id": 2, "number": 8},
    ]
    api = GitHubAPI(Transport([Response(200, response)]), "owner/repo")
    assert api.list_issues() == [{"id": 2, "number": 8}]


def test_promotion_workflow_has_global_lock_and_least_privilege(repository_root):
    workflow = yaml.safe_load((repository_root / WORKFLOW).read_text())
    assert workflow["permissions"] == {}
    group = workflow["concurrency"]["group"]
    assert "engineering-promotion-${{ github.repository_id }}" == group
    assert "issue" not in group.lower()
    for job in workflow["jobs"].values():
        permissions = job.get("permissions", {})
        assert permissions.get("contents") != "write"
        if permissions.get("issues") == "write":
            assert permissions.get("actions") == "read"
    source = (repository_root / WORKFLOW).read_text()
    assert "search" not in source.lower()
    assert "git push" not in source
    for manifest in MANIFESTS:
        assert WORKFLOW in (repository_root / manifest).read_text()

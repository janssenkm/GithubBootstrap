from __future__ import annotations

import pytest

from github_governance.errors import GovernanceError
from github_governance.github_api import GitHubAPI, Response


class Transport:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def __call__(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, params, body))
        return self.responses.pop(0)


def test_comment_listing_uses_rest_pagination():
    first = [{"id": index} for index in range(100)]
    transport = Transport([Response(200, first), Response(200, [{"id": 100}])])
    api = GitHubAPI(transport, "owner/repo")
    assert len(api.list_comments(7)) == 101
    assert [call[2]["page"] for call in transport.calls] == [1, 2]
    assert all("search" not in call[1] for call in transport.calls)


@pytest.mark.parametrize(
    ("status", "code", "finding"),
    [(403, 1, "GITHUB-API-FORBIDDEN"), (404, 1, "GITHUB-API-NOT-FOUND"), (409, 3, "GITHUB-API-CONFLICT"), (500, 4, "GITHUB-API-TRANSIENT")],
)
def test_api_statuses_fail_closed(status, code, finding):
    api = GitHubAPI(Transport([Response(status, {"message": "untrusted server detail"})]), "owner/repo")
    with pytest.raises(GovernanceError) as error:
        api.get_workflow_run(9)
    assert error.value.code == code
    assert error.value.finding.id == finding
    assert "untrusted" not in str(error.value)


def test_mutations_and_readbacks_use_expected_rest_paths():
    transport = Transport(
        [
            Response(200, {"id": 7, "body": "before"}),
            Response(200, {"id": 7, "body": "target"}),
            Response(201, {"id": 12, "body": "receipt"}),
            Response(200, {"id": 12, "body": "receipt"}),
        ]
    )
    api = GitHubAPI(transport, "owner/repo")
    assert api.get_issue(7)["body"] == "before"
    assert api.update_issue(7, body="target")["body"] == "target"
    assert api.create_comment(7, "receipt")["id"] == 12
    assert api.get_comment(12)["body"] == "receipt"
    assert [call[:2] for call in transport.calls] == [
        ("GET", "/repos/owner/repo/issues/7"),
        ("PATCH", "/repos/owner/repo/issues/7"),
        ("POST", "/repos/owner/repo/issues/7/comments"),
        ("GET", "/repos/owner/repo/issues/comments/12"),
    ]


def test_milestone_items_paginate_past_100_and_preserve_pr_separation():
    first = [{"id": index + 1, "number": index + 1} for index in range(99)]
    first.append({"id": 100, "number": 100, "pull_request": {"url": "x"}})
    transport = Transport([Response(200, first), Response(200, [{"id": 101, "number": 101}])])
    api = GitHubAPI(transport, "owner/repo")
    items = api.list_milestone_items(7)
    assert len(items) == 101 and "pull_request" in items[99]
    assert [call[2]["page"] for call in transport.calls] == [1, 2]
    assert all(call[2]["milestone"] == 7 for call in transport.calls)


def test_issue_listing_excludes_pull_requests_after_pagination():
    transport = Transport([Response(200, [
        {"id": 1, "number": 1},
        {"id": 2, "number": 2, "pull_request": {"url": "x"}},
    ])])
    assert [item["number"] for item in GitHubAPI(transport, "owner/repo").list_issues()] == [1]


def test_check_runs_paginate_and_reject_duplicate_ids():
    first = [{"id": index + 1, "name": f"check-{index}"} for index in range(100)]
    transport = Transport([Response(200, {"check_runs": first}), Response(200, {"check_runs": [{"id": 101, "name": "last"}]})])
    assert len(GitHubAPI(transport, "owner/repo").list_check_runs("a" * 40)) == 101
    duplicate = Transport([Response(200, {"check_runs": first}), Response(200, {"check_runs": [{"id": 100}]})])
    with pytest.raises(GovernanceError) as captured:
        GitHubAPI(duplicate, "owner/repo").list_check_runs("a" * 40)
    assert captured.value.finding.id == "GITHUB-API-PAGINATION"


def test_artifacts_paginate_and_reject_duplicate_ids():
    first = [{"id": index + 1, "name": f"artifact-{index}"} for index in range(100)]
    transport = Transport([Response(200, {"artifacts": first}), Response(200, {"artifacts": [{"id": 101, "name": "last"}]})])
    assert len(GitHubAPI(transport, "owner/repo").list_workflow_run_artifacts(9)) == 101
    duplicate = Transport([Response(200, {"artifacts": first}), Response(200, {"artifacts": [{"id": 100}]})])
    with pytest.raises(GovernanceError) as captured:
        GitHubAPI(duplicate, "owner/repo").list_workflow_run_artifacts(9)
    assert captured.value.finding.id == "GITHUB-API-PAGINATION"


def test_artifact_download_requires_bytes_and_positive_identity():
    assert GitHubAPI(Transport([Response(200, b"PKzip")]), "owner/repo").download_artifact(81) == b"PKzip"
    with pytest.raises(GovernanceError) as captured:
        GitHubAPI(Transport([Response(200, {"forged": True})]), "owner/repo").download_artifact(81)
    assert captured.value.finding.id == "GITHUB-API-SHAPE"
    with pytest.raises(GovernanceError) as captured:
        GitHubAPI(Transport([]), "owner/repo").download_artifact(True)
    assert captured.value.finding.id == "GITHUB-API-MUTATION"

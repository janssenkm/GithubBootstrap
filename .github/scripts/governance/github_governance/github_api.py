"""Small REST-only GitHub API adapter with fail-closed status handling."""

from __future__ import annotations

import json
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, Callable

from .errors import GovernanceError


@dataclass(frozen=True)
class Response:
    status: int
    data: Any
    headers: dict[str, str] = field(default_factory=dict)


Transport = Callable[..., Response]


def urllib_transport(token: str, api_url: str = "https://api.github.com") -> Transport:
    """Create a transport without logging token, payload, or remote error text."""

    base = api_url.rstrip("/")

    def request(method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> Response:
        query = urllib.parse.urlencode(params or {})
        url = base + path + ("?" + query if query else "")
        encoded = None if body is None else json.dumps(body, separators=(",", ":")).encode("utf-8")
        headers = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "github-bootstrap-governance-v1",
        }
        if encoded is not None:
            headers["Content-Type"] = "application/json"
        operation = urllib.request.Request(url, data=encoded, headers=headers, method=method)
        try:
            with urllib.request.urlopen(operation, timeout=30) as remote:
                raw = remote.read()
                if not raw:
                    data = None
                elif path.endswith("/zip"):
                    data = raw
                else:
                    data = json.loads(raw.decode("utf-8"))
                return Response(remote.status, data, dict(remote.headers.items()))
        except urllib.error.HTTPError as error:
            return Response(error.code, None, dict(error.headers.items()) if error.headers else {})
        except (urllib.error.URLError, TimeoutError, UnicodeError, json.JSONDecodeError) as error:
            raise GovernanceError("GITHUB-API-TRANSIENT", "GitHub API request/read-back failed", code=4) from error

    return request


class GitHubAPI:
    def __init__(self, transport: Transport, repository: str):
        if repository.count("/") != 1 or any(not part for part in repository.split("/")):
            raise GovernanceError("GITHUB-API-REPOSITORY", "repository must be owner/name", code=5)
        self._transport = transport
        self.repository = repository

    def _request(self, method: str, path: str, *, params: dict[str, Any] | None = None, body: Any = None) -> Any:
        try:
            response = self._transport(method, path, params=params, body=body)
        except GovernanceError:
            raise
        except Exception as error:
            raise GovernanceError("GITHUB-API-TRANSIENT", "GitHub API request/read-back failed", code=4) from error
        status = response.status
        if 200 <= status < 300:
            return response.data
        if status == 403:
            raise GovernanceError("GITHUB-API-FORBIDDEN", "required GitHub API permission or access is unavailable")
        if status == 404:
            raise GovernanceError("GITHUB-API-NOT-FOUND", "required GitHub API evidence is unavailable")
        if status in {409, 422}:
            raise GovernanceError("GITHUB-API-CONFLICT", "GitHub API lifecycle conflict requires reconciliation", code=3)
        if status == 429 or status >= 500:
            raise GovernanceError("GITHUB-API-TRANSIENT", "GitHub API request/read-back failed", code=4)
        raise GovernanceError("GITHUB-API-REJECTED", "GitHub API rejected the requested operation")

    def get_issue(self, number: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/issues/{number}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "Issue API response has an invalid shape", code=4)
        return value

    def get_repository(self) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "repository API response has an invalid shape", code=4)
        return value

    def get_milestone(self, number: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/milestones/{number}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "milestone API response has an invalid shape", code=4)
        return value

    def get_pull_request(self, number: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/pulls/{number}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "pull request response has an invalid shape", code=4)
        return value

    def list_check_runs(self, ref: str) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for page in range(1, 10_001):
            value = self._request("GET", f"/repos/{self.repository}/commits/{ref}/check-runs",
                                  params={"per_page": 100, "page": page})
            if not isinstance(value, dict) or not isinstance(value.get("check_runs"), list):
                raise GovernanceError("GITHUB-API-SHAPE", "check-runs response has an invalid shape", code=4)
            runs = value["check_runs"]
            for run in runs:
                if not isinstance(run, dict):
                    raise GovernanceError("GITHUB-API-SHAPE", "check-run item has an invalid shape", code=4)
                identifier = run.get("id")
                if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                    raise GovernanceError("GITHUB-API-SHAPE", "check-run identity is invalid", code=4)
                if identifier in seen_ids:
                    raise GovernanceError("GITHUB-API-PAGINATION", "check-run pagination contains a duplicate entity", code=4)
                seen_ids.add(identifier)
                result.append(run)
            if len(runs) < 100:
                return result
        raise GovernanceError("GITHUB-API-PAGINATION", "check-run pagination exceeded the safety bound", code=4)

    def update_milestone(self, number: int, *, state: str, description: str | None = None) -> dict[str, Any]:
        if state not in {"open", "closed"}:
            raise GovernanceError("GITHUB-API-MUTATION", "milestone state is invalid", code=2)
        payload: dict[str, Any] = {"state": state}
        if description is not None:
            if not isinstance(description, str):
                raise GovernanceError("GITHUB-API-MUTATION", "milestone description is invalid", code=2)
            payload["description"] = description
        value = self._request("PATCH", f"/repos/{self.repository}/milestones/{number}", body=payload)
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "milestone update response has an invalid shape", code=4)
        return value

    def list_milestone_items(self, milestone_number: int) -> list[dict[str, Any]]:
        return self._list_issue_items(state="all", milestone=milestone_number)

    def list_issues(self, *, state: str = "all") -> list[dict[str, Any]]:
        return [item for item in self._list_issue_items(state=state) if "pull_request" not in item]

    def _list_issue_items(self, *, state: str, milestone: int | None = None) -> list[dict[str, Any]]:
        if state not in {"open", "closed", "all"}:
            raise GovernanceError("GITHUB-API-ISSUE-STATE", "Issue listing state is invalid", code=2)
        result: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        seen_numbers: set[int] = set()
        for page in range(1, 10_001):
            params: dict[str, Any] = {"state": state, "per_page": 100, "page": page}
            if milestone is not None:
                params["milestone"] = milestone
            value = self._request(
                "GET",
                f"/repos/{self.repository}/issues",
                params=params,
            )
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise GovernanceError("GITHUB-API-SHAPE", "Issue list response has an invalid shape", code=4)
            for item in value:
                identifier = item.get("id")
                number = item.get("number")
                if (
                    not isinstance(identifier, int)
                    or isinstance(identifier, bool)
                    or identifier < 1
                    or not isinstance(number, int)
                    or isinstance(number, bool)
                    or number < 1
                ):
                    raise GovernanceError("GITHUB-API-SHAPE", "Issue list item identity is invalid", code=4)
                if identifier in seen_ids or number in seen_numbers:
                    raise GovernanceError("GITHUB-API-PAGINATION", "Issue pagination contains a duplicate entity", code=4)
                seen_ids.add(identifier)
                seen_numbers.add(number)
                result.append(item)
            if len(value) < 100:
                return result
        raise GovernanceError("GITHUB-API-PAGINATION", "Issue pagination exceeded the safety bound", code=4)

    def create_issue(self, title: str, body: str, labels: list[str], *, milestone: int | None = None) -> dict[str, Any]:
        if not isinstance(title, str) or not title or not isinstance(body, str) or not body:
            raise GovernanceError("GITHUB-API-MUTATION", "Issue title and body are required", code=2)
        if not isinstance(labels, list) or any(not isinstance(label, str) or not label for label in labels):
            raise GovernanceError("GITHUB-API-MUTATION", "Issue labels are invalid", code=2)
        payload: dict[str, Any] = {"title": title, "body": body, "labels": labels}
        if milestone is not None:
            if not isinstance(milestone, int) or isinstance(milestone, bool) or milestone < 1:
                raise GovernanceError("GITHUB-API-MUTATION", "milestone number is invalid", code=2)
            payload["milestone"] = milestone
        value = self._request(
            "POST",
            f"/repos/{self.repository}/issues",
            body=payload,
        )
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "Issue create response has an invalid shape", code=4)
        return value

    def update_issue(self, number: int, *, body: str | None = None, labels: list[str] | None = None) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if body is not None:
            payload["body"] = body
        if labels is not None:
            payload["labels"] = labels
        if not payload:
            raise GovernanceError("GITHUB-API-MUTATION", "empty Issue update is forbidden", code=2)
        value = self._request("PATCH", f"/repos/{self.repository}/issues/{number}", body=payload)
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "Issue update response has an invalid shape", code=4)
        return value

    def create_comment(self, issue_number: int, body: str) -> dict[str, Any]:
        value = self._request("POST", f"/repos/{self.repository}/issues/{issue_number}/comments", body={"body": body})
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "comment create response has an invalid shape", code=4)
        return value

    def get_comment(self, comment_id: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/issues/comments/{comment_id}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "comment API response has an invalid shape", code=4)
        return value

    def list_comments(self, issue_number: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen_ids: set[int] = set()
        for page in range(1, 10_001):
            value = self._request(
                "GET",
                f"/repos/{self.repository}/issues/{issue_number}/comments",
                params={"per_page": 100, "page": page},
            )
            if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
                raise GovernanceError("GITHUB-API-SHAPE", "comment list response has an invalid shape", code=4)
            for item in value:
                identifier = item.get("id")
                if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 0:
                    raise GovernanceError("GITHUB-API-SHAPE", "comment list item identity is invalid", code=4)
                if identifier in seen_ids:
                    raise GovernanceError("GITHUB-API-PAGINATION", "comment pagination contains a duplicate entity", code=4)
                seen_ids.add(identifier)
                result.append(item)
            if len(value) < 100:
                return result
        raise GovernanceError("GITHUB-API-PAGINATION", "comment pagination exceeded the safety bound", code=4)

    def get_workflow_run(self, run_id: int) -> dict[str, Any]:
        value = self._request("GET", f"/repos/{self.repository}/actions/runs/{run_id}")
        if not isinstance(value, dict):
            raise GovernanceError("GITHUB-API-SHAPE", "workflow-run response has an invalid shape", code=4)
        return value

    def list_workflow_run_artifacts(self, run_id: int) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[int] = set()
        for page in range(1, 10_001):
            value = self._request("GET", f"/repos/{self.repository}/actions/runs/{run_id}/artifacts",
                                  params={"per_page": 100, "page": page})
            if not isinstance(value, dict) or not isinstance(value.get("artifacts"), list):
                raise GovernanceError("GITHUB-API-SHAPE", "artifact list response has an invalid shape", code=4)
            artifacts = value["artifacts"]
            for artifact in artifacts:
                if not isinstance(artifact, dict):
                    raise GovernanceError("GITHUB-API-SHAPE", "artifact item has an invalid shape", code=4)
                identifier = artifact.get("id")
                if not isinstance(identifier, int) or isinstance(identifier, bool) or identifier < 1:
                    raise GovernanceError("GITHUB-API-SHAPE", "artifact identity is invalid", code=4)
                if identifier in seen:
                    raise GovernanceError("GITHUB-API-PAGINATION", "artifact pagination contains a duplicate entity", code=4)
                seen.add(identifier)
                result.append(artifact)
            if len(artifacts) < 100:
                return result
        raise GovernanceError("GITHUB-API-PAGINATION", "artifact pagination exceeded the safety bound", code=4)

    def download_artifact(self, artifact_id: int) -> bytes:
        if not isinstance(artifact_id, int) or isinstance(artifact_id, bool) or artifact_id < 1:
            raise GovernanceError("GITHUB-API-MUTATION", "artifact identity is invalid", code=2)
        value = self._request("GET", f"/repos/{self.repository}/actions/artifacts/{artifact_id}/zip")
        if not isinstance(value, bytes):
            raise GovernanceError("GITHUB-API-SHAPE", "artifact download is not a ZIP byte stream", code=4)
        return value

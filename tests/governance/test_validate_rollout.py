from __future__ import annotations

import json
import os
import ast
import shutil
import subprocess
from pathlib import Path


SCRIPT = ".github/scripts/validate-rollout"


def _write_executable(path: Path, body: str) -> None:
    path.write_text(body, encoding="utf-8")
    path.chmod(0o755)


def _run(repository_root: Path, tmp_path: Path, fixture: dict, *, malformed: str = "", timeout: str = ""):
    tmp_path.mkdir(parents=True, exist_ok=True)
    fixture_path = tmp_path / "fixture.json"
    fixture_path.write_text(json.dumps(fixture), encoding="utf-8")
    log_path = tmp_path / "calls.jsonl"
    gh = tmp_path / "gh"
    _write_executable(
        gh,
        """#!/usr/bin/env python3
import json, os, sys, time
fixture = json.load(open(os.environ['ROLLOUT_FIXTURE'], encoding='utf-8'))
args = sys.argv[1:]
with open(os.environ['ROLLOUT_CALLS'], 'a', encoding='utf-8') as log:
    log.write(json.dumps(args) + '\\n')
endpoint = args[-1]
if endpoint == os.environ.get('ROLLOUT_MALFORMED'):
    print('{not-json')
    raise SystemExit(0)
response = fixture.get(endpoint)
if response is None:
    print('missing fixture', file=sys.stderr)
    raise SystemExit(1)
if isinstance(response, dict) and '_exit' in response:
    print(response.get('_stderr', ''), file=sys.stderr)
    raise SystemExit(response['_exit'])
if isinstance(response, dict) and '_sleep' in response:
    time.sleep(response['_sleep'])
print(json.dumps(response))
""",
    )
    env = os.environ | {
        "PATH": f"{tmp_path}:{os.environ['PATH']}",
        "ROLLOUT_FIXTURE": str(fixture_path),
        "ROLLOUT_CALLS": str(log_path),
        "ROLLOUT_MALFORMED": malformed,
    }
    if timeout:
        env["ROLLOUT_VALIDATOR_TIMEOUT_SECONDS"] = timeout
    result = subprocess.run(
        [str(repository_root / SCRIPT), "--repository", "acme/widget", "--root", str(repository_root)],
        text=True,
        capture_output=True,
        env=env,
        check=False,
    )
    return result, json.loads(result.stdout), [json.loads(line) for line in log_path.read_text().splitlines()]


def _fixture(repository_root: Path) -> dict:
    workflows = [
        {"id": index, "path": f".github/workflows/{path.name}", "state": "active"}
        for index, path in enumerate(sorted((repository_root / ".github/workflows").glob("*.yml")), 1)
    ]
    labels = sorted(
        {
            label
            for form in (repository_root / ".github/ISSUE_TEMPLATE").glob("*.yml")
            for label in (__import__("yaml").safe_load(form.read_text()) or {}).get("labels", [])
        }
        | {
            "type:candidate", "type:engineering", "state:draft", "state:gate-failed",
            "type:intake", "state:new",
            "state:gate-passed", "state:promoted", "state:contracted", "state:ready",
            "state:in-progress", "state:done", "state:cancelled", "governance:stale",
            "governance:transition-paused", "milestone:review", "state:triaged",
            "state:investigating", "state:closed",
        }
    )
    sha = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repository_root, text=True).strip()
    return {
        "repos/acme/widget": {"id": 1, "full_name": "acme/widget", "default_branch": "main", "is_template": False},
        "repos/acme/widget/branches/main": {"name": "main", "commit": {"sha": sha}},
        "repos/acme/widget/actions/permissions": {"enabled": True, "allowed_actions": "all"},
        "repos/acme/widget/actions/permissions/workflow": {"default_workflow_permissions": "read", "can_approve_pull_request_reviews": False},
        "repos/acme/widget/actions/workflows?per_page=100&page=1": {"workflows": workflows},
        "repos/acme/widget/actions/workflows?per_page=100&page=2": {"workflows": []},
        "repos/acme/widget/labels?per_page=100&page=1": [{"id": index, "name": label} for index, label in enumerate(labels, 100)],
        "repos/acme/widget/labels?per_page=100&page=2": [],
        "repos/acme/widget/actions/runs?branch=main&per_page=100&page=1": {"workflow_runs": [{"id": 10, "head_sha": sha, "status": "completed", "conclusion": "success"}]},
        "repos/acme/widget/actions/runs?branch=main&per_page=100&page=2": {"workflow_runs": []},
        "repos/acme/widget/actions/runs/10/jobs?per_page=100&page=1": {"jobs": [{"id": 20, "name": "Quality Gate", "conclusion": "success"}]},
        "repos/acme/widget/actions/runs/10/jobs?per_page=100&page=2": {"jobs": []},
        f"repos/acme/widget/commits/{sha}/check-runs?per_page=100&page=1": {"check_runs": [{"id": 30, "name": "Quality Gate", "conclusion": "success"}]},
        f"repos/acme/widget/commits/{sha}/check-runs?per_page=100&page=2": {"check_runs": []},
        "repos/acme/widget/rulesets?per_page=100&page=1": [{"id": 1, "name": "Main Branch Protection", "enforcement": "active", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}}, "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Quality Gate"}]}}]}],
        "repos/acme/widget/rulesets?per_page=100&page=2": [],
        "repos/acme/widget/rulesets/1": {"id": 1, "name": "Main Branch Protection", "target": "branch", "enforcement": "active", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}}, "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Quality Gate"}]}}]},
        "repos/acme/widget/milestones?state=all&per_page=100&page=1": [],
    }


def test_ready_output_is_canonical_and_commands_are_read_only(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    # Trust lists are intentionally empty in the template checkout, so readiness is blocked.
    result, report, calls = _run(repository_root, tmp_path, fixture)
    assert result.returncode == 5
    assert any(item["id"] == "LOCAL-WORKTREE-DIRTY" for item in report["blocked"])
    assert report["schema_version"] == 1
    assert "TRUST-LISTS-EMPTY" in {item["id"] for item in report["blocked"]}
    assert result.stdout.strip() == json.dumps(report, sort_keys=True, separators=(",", ":"))
    for args in calls:
        assert args[0] == "api"
        assert args[1:3] == ["--method", "GET"]
        assert not any(word in " ".join(args).lower() for word in ("post", "put", "patch", "delete", "graphql", "dispatch"))
        assert args[-1].startswith("repos/acme/widget")


def test_detects_missing_labels_v1_workflows_checks_and_ruleset_mismatch(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/labels?per_page=100&page=1"] = []
    fixture["repos/acme/widget/actions/workflows?per_page=100&page=1"] = {"workflows": [{"id": 999, "path": ".github/workflows/00-issue-ai-triage.yml", "state": "active"}]}
    fixture["repos/acme/widget/actions/runs/10/jobs?per_page=100&page=1"] = {"jobs": []}
    sha = fixture["repos/acme/widget/branches/main"]["commit"]["sha"]
    fixture[f"repos/acme/widget/commits/{sha}/check-runs?per_page=100&page=1"] = {"check_runs": [{"id": 30, "name": "Quality Gate", "conclusion": "success"}, {"id": 31, "name": "Quality Gate", "conclusion": "success"}]}
    fixture["repos/acme/widget/rulesets?per_page=100&page=1"] = []
    result, report, _ = _run(repository_root, tmp_path, fixture)
    ids = {item["id"] for section in ("findings", "blocked") for item in report[section]}
    assert result.returncode == 5
    assert {"LABELS-MISSING", "REMOTE-WORKFLOWS-MISMATCH", "REQUIRED-CHECK-AMBIGUOUS", "RULESET-ABSENT"} <= ids


def test_403_and_malformed_json_fail_closed_without_secret_echo(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/rulesets?per_page=100&page=1"] = {"_exit": 1, "_stderr": "HTTP 403 token=super-secret"}
    result, report, _ = _run(repository_root, tmp_path, fixture)
    assert result.returncode == 5
    assert report["blocked"]
    assert "super-secret" not in result.stdout
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/actions/permissions"]["authorization"] = "Bearer should-not-appear"
    fixture["repos/acme/widget"]["secret"] = "root-secret"
    result, report, _ = _run(repository_root, tmp_path / "allowlist", fixture)
    assert "should-not-appear" not in result.stdout and "root-secret" not in result.stdout
    assert set(report["observed"]["actions"]) == {
        "allowed_actions", "enabled", "can_approve_pull_request_reviews", "default_workflow_permissions"
    }
    malformed_endpoint = "repos/acme/widget/actions/permissions"
    result, report, _ = _run(repository_root, tmp_path / "malformed", _fixture(repository_root), malformed=malformed_endpoint)
    assert result.returncode == 5
    assert any(item["id"] == "API-MALFORMED" for item in report["blocked"])


def test_pagination_is_followed(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/labels?per_page=100&page=1"] = fixture["repos/acme/widget/labels?per_page=100&page=1"][:1]
    fixture["repos/acme/widget/labels?per_page=100&page=2"] = _fixture(repository_root)["repos/acme/widget/labels?per_page=100&page=1"][1:]
    fixture["repos/acme/widget/labels?per_page=100&page=3"] = []
    _, _, calls = _run(repository_root, tmp_path, fixture)
    endpoints = {args[-1] for args in calls}
    assert "repos/acme/widget/labels?per_page=100&page=3" in endpoints


def test_source_contains_no_remote_mutation_interface(repository_root):
    source = (repository_root / SCRIPT).read_text(encoding="utf-8").lower()
    for forbidden in ("--method\", \"post", "--method\", \"put", "--method\", \"patch", "--method\", \"delete", "graphql", "workflow dispatch"):
        assert forbidden not in source
    assert '["gh","api","--method","get",self.pre+s]' in source
    tree = ast.parse(source)
    allowed_git_starts = {
        ("rev-parse", "head"),
        ("remote", "get-url", "origin"),
        ("status", "--porcelain=v1", "--untracked-files=all"),
        ("merge-base", "--is-ancestor"),
    }
    for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
        if not isinstance(call.func, ast.Name) or call.func.id != "git":
            continue
        constants = tuple(arg.value for arg in call.args[1:] if isinstance(arg, ast.Constant))
        assert any(constants[: len(prefix)] == prefix for prefix in allowed_git_starts)


def test_rejects_repeated_pages_and_pagination_cursors(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/labels?per_page=100&page=2"] = fixture["repos/acme/widget/labels?per_page=100&page=1"]
    result, report, _ = _run(repository_root, tmp_path / "repeat", fixture)
    assert result.returncode == 5
    assert any(item["id"] == "API-PAGINATION-CYCLE" for item in report["blocked"])
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/actions/workflows?per_page=100&page=1"]["next"] = "attacker"
    result, report, _ = _run(repository_root, tmp_path / "cursor", fixture)
    assert result.returncode == 5
    assert any(item["id"] == "API-PAGINATION-CURSOR" for item in report["blocked"])

    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/labels?per_page=100&page=1"].append(
        {"id": fixture["repos/acme/widget/labels?per_page=100&page=1"][0]["id"], "name": "different"}
    )
    result, report, _ = _run(repository_root, tmp_path / "duplicate-id", fixture)
    assert result.returncode == 5
    assert any(item["id"] == "API-DUPLICATE-ENTITY" for item in report["blocked"])


def test_pagination_has_a_hard_page_bound(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    for page in range(1, 101):
        fixture[f"repos/acme/widget/labels?per_page=100&page={page}"] = [
            {"id": 10_000 + page, "name": f"page-{page}"}
        ]
    result, report, calls = _run(repository_root, tmp_path, fixture)
    assert result.returncode == 5
    assert any(item["id"] == "API-PAGINATION-LIMIT" for item in report["blocked"])
    assert not any(call[-1] == "repos/acme/widget/labels?per_page=100&page=101" for call in calls)


def test_rejects_malformed_members_disabled_actions_and_missing_sha(repository_root, tmp_path):
    cases = []
    malformed = _fixture(repository_root)
    malformed["repos/acme/widget/labels?per_page=100&page=1"] = [{"id": True, "name": "type:intake"}]
    cases.append((malformed, "API-MALFORMED"))
    disabled = _fixture(repository_root)
    disabled["repos/acme/widget/actions/permissions"]["enabled"] = False
    cases.append((disabled, "ACTIONS-DISABLED"))
    missing_sha = _fixture(repository_root)
    missing_sha["repos/acme/widget/branches/main"] = {"name": "main", "commit": {}}
    cases.append((missing_sha, "REMOTE-DEFAULT-SHA-INVALID"))
    for index, (fixture, expected) in enumerate(cases):
        result, report, _ = _run(repository_root, tmp_path / str(index), fixture)
        assert result.returncode == 5
        assert any(item["id"] == expected for item in report["blocked"])
        assert "traceback" not in result.stderr.lower()


def test_rejects_strict_repository_injections_without_calling_gh(repository_root, tmp_path):
    for value in ("acme/widget?x=1", "acme/widget name", "../widget", "acme/..", "acmé/widget", "acme/widget\nnext"):
        result = subprocess.run(
            [str(repository_root / SCRIPT), "--repository", value, "--root", str(repository_root)],
            text=True, capture_output=True, check=False,
        )
        assert result.returncode == 2
        report = json.loads(result.stdout)
        assert report["blocked"] == [{"id": "CLI-USAGE", "message": "invalid command-line arguments"}]
        assert "traceback" not in result.stderr.lower()


def test_fourth_trust_list_and_ruleset_applicability_are_enforced(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    result, report, _ = _run(repository_root, tmp_path / "trust", fixture)
    finding = next(item for item in report["blocked"] if item["id"] == "TRUST-LISTS-EMPTY")
    assert "trusted_milestone_acceptors" in finding["message"]

    fixture = _fixture(repository_root)
    fixture["repos/acme/widget/rulesets?per_page=100&page=1"] = [
        {"id": 1, "name": "Other", "enforcement": "active"},
        {"id": 2, "name": "Main", "enforcement": "active"},
    ]
    fixture["repos/acme/widget/rulesets/1"] = {"id": 1, "name": "Other", "target": "branch", "enforcement": "active", "conditions": {"ref_name": {"include": ["refs/heads/release"]}}, "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": [{"context": "Quality Gate"}]}}]}
    fixture["repos/acme/widget/rulesets/2"] = {"id": 2, "name": "Main", "target": "branch", "enforcement": "active", "conditions": {"ref_name": {"include": ["~DEFAULT_BRANCH"]}}, "rules": [{"type": "required_status_checks", "parameters": {"required_status_checks": []}}]}
    result, report, _ = _run(repository_root, tmp_path / "irrelevant", fixture)
    assert any(item["id"] == "RULESET-CHECKS-MISMATCH" for item in report["findings"])

    fixture["repos/acme/widget/rulesets/2"]["rules"][0]["parameters"]["required_status_checks"] = [{"context": "Quality Gate"}, {"context": "Quality Gate"}]
    result, report, _ = _run(repository_root, tmp_path / "duplicate", fixture)
    assert result.returncode == 5
    assert any(item["id"] == "RULESET-CONTEXTS-DUPLICATE" for item in report["blocked"])


def test_noncanonical_template_flag_does_not_bypass_generated_trust(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget"]["is_template"] = True
    result, report, _ = _run(repository_root, tmp_path, fixture)
    assert result.returncode == 5
    assert report["observed"]["remote"]["scenario"] == "generated-project"
    assert any(item["id"] == "TRUST-LISTS-EMPTY" for item in report["blocked"])
    assert any(item["id"] == "TEMPLATE-IDENTITY-MISMATCH" for item in report["findings"])


def test_gh_and_git_timeouts_are_bounded_and_redacted(repository_root, tmp_path):
    fixture = _fixture(repository_root)
    fixture["repos/acme/widget"] = {"_sleep": 2, "secret": "never-print"}
    result, report, _ = _run(repository_root, tmp_path / "gh", fixture, timeout="1")
    assert result.returncode == 5
    assert any(item["id"] == "API-TIMEOUT" for item in report["blocked"])
    assert "never-print" not in result.stdout and "traceback" not in result.stderr.lower()

    git_dir = tmp_path / "git"
    git_dir.mkdir()
    real_git = shutil.which("git")
    _write_executable(
        git_dir / "git",
        f"""#!/usr/bin/env python3
import os, subprocess, sys, time
if sys.argv[1:3] == ['status', '--porcelain=v1']:
    time.sleep(2)
raise SystemExit(subprocess.run([{real_git!r}, *sys.argv[1:]]).returncode)
""",
    )
    fixture = _fixture(repository_root)
    result, report, _ = _run(repository_root, git_dir, fixture, timeout="1")
    assert result.returncode == 5
    assert any(item["id"] == "LOCAL-GIT-TIMEOUT" for item in report["blocked"])
    assert "traceback" not in result.stderr.lower()


def test_invalid_full_policy_schema_fails_closed(repository_root, tmp_path):
    local = tmp_path / "local"
    (local / ".github/schemas").mkdir(parents=True)
    (local / ".github/workflows").mkdir(parents=True)
    (local / ".github/scripts").mkdir(parents=True)
    shutil.copy2(repository_root / SCRIPT, local / SCRIPT)
    shutil.copy2(repository_root / ".github/project-policy.yml", local / ".github/project-policy.yml")
    shutil.copy2(repository_root / ".github/schemas/project-policy.schema.json", local / ".github/schemas/project-policy.schema.json")
    shutil.copy2(repository_root / ".github/workflows/00-baseline-check.yml", local / ".github/workflows/00-baseline-check.yml")
    policy = __import__("yaml").safe_load((local / ".github/project-policy.yml").read_text())
    policy["trusted_milestone_acceptors"] = ["valid", "VALID"]
    (local / ".github/project-policy.yml").write_text(__import__("yaml").safe_dump(policy), encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Human"], cwd=local, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/widget.git"], cwd=local, check=True)
    subprocess.run(["git", "add", "."], cwd=local, check=True)
    subprocess.run(["git", "commit", "-m", "test fixture"], cwd=local, check=True, capture_output=True)
    result, report, _ = _run(local, tmp_path / "api", _fixture(local))
    assert result.returncode == 5
    assert any(item["id"] == "LOCAL-POLICY-INVALID" for item in report["blocked"])


def test_clean_committed_generated_repository_can_be_ready(repository_root, tmp_path):
    local = tmp_path / "ready-local"
    (local / ".github/schemas").mkdir(parents=True)
    (local / ".github/workflows").mkdir(parents=True)
    (local / ".github/scripts").mkdir(parents=True)
    shutil.copy2(repository_root / SCRIPT, local / SCRIPT)
    shutil.copy2(repository_root / ".github/schemas/project-policy.schema.json", local / ".github/schemas/project-policy.schema.json")
    shutil.copy2(repository_root / ".github/workflows/00-baseline-check.yml", local / ".github/workflows/00-baseline-check.yml")
    policy = __import__("yaml").safe_load((repository_root / ".github/project-policy.yml").read_text())
    policy.update(
        trusted_issue_authors=["issue-author"],
        trusted_developers=["developer"],
        trusted_reviewers=["reviewer"],
        trusted_milestone_acceptors=["acceptor"],
        required_milestone_checks=["Quality Gate"],
    )
    (local / ".github/project-policy.yml").write_text(__import__("yaml").safe_dump(policy), encoding="utf-8")
    subprocess.run(["git", "init", "-b", "main"], cwd=local, check=True, capture_output=True)
    subprocess.run(["git", "config", "user.name", "Test Human"], cwd=local, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=local, check=True)
    subprocess.run(["git", "remote", "add", "origin", "https://github.com/acme/widget.git"], cwd=local, check=True)
    subprocess.run(["git", "add", "."], cwd=local, check=True)
    subprocess.run(["git", "commit", "-m", "ready fixture"], cwd=local, check=True, capture_output=True)
    fixture = _fixture(local)
    result, report, _ = _run(local, tmp_path / "ready-api", fixture)
    assert result.returncode == 0
    assert report["blocked"] == []
    assert report["findings"] == []
    assert report["unknown"] == []

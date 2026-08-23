from __future__ import annotations

import copy
import json
import subprocess

import pytest

from github_governance.canonical import subject_digest
from github_governance.semantic import _dependency_versions, semantic_findings


def ids(contract, policy, repository_root):
    return {finding.id for finding in semantic_findings(contract, policy, repository_root)}


def test_valid_candidate_passes_semantics(valid_contract, policy, repository_root):
    assert semantic_findings(valid_contract, policy, repository_root) == []


def test_global_duplicate_ids_across_arrays_fail(valid_contract, policy, repository_root):
    valid_contract["risks"][0]["id"] = "F-01"
    assert "SEMANTIC-DUPLICATE-ID" in ids(valid_contract, policy, repository_root)


def test_missing_and_wrong_type_references_fail(valid_contract, policy, repository_root):
    valid_contract["claims"][0]["evidence_refs"] = ["MISSING-01"]
    assert "SEMANTIC-REFERENCE" in ids(valid_contract, policy, repository_root)
    valid_contract["claims"][0].update(id="D-01", type="decision", evidence_refs=["E-01"])
    assert "SEMANTIC-DECISION-EVIDENCE" in ids(valid_contract, policy, repository_root)


def test_decision_requires_human_decision_evidence(valid_contract, policy, repository_root):
    valid_contract["claims"][0].update(id="D-01", type="decision")
    valid_contract["evidence"][0]["type"] = "human-decision"
    assert "SEMANTIC-DECISION-EVIDENCE" not in ids(valid_contract, policy, repository_root)


def test_unknown_resolution_and_risk_joins(valid_contract, policy, repository_root):
    valid_contract["unknowns"] = [{
        "id": "U-01", "statement": "Unknown", "impact": "low", "resolution": "open",
        "containment": "Keep the feature disabled.", "risk_refs": ["MISSING-01"],
    }]
    assert "SEMANTIC-REFERENCE" in ids(valid_contract, policy, repository_root)
    valid_contract["unknowns"][0].update(resolution="resolved", resolution_evidence_refs=[])
    assert "SEMANTIC-UNKNOWN-RESOLUTION" in ids(valid_contract, policy, repository_root)


def test_open_finding_and_resolution_evidence_join_fail(valid_contract, policy, repository_root):
    valid_contract["review"]["findings"] = [{"id": "RF-01", "severity": "high", "statement": "Open", "disposition": "open"}]
    assert "SEMANTIC-OPEN-FINDING" in ids(valid_contract, policy, repository_root)
    valid_contract["review"]["findings"][0] = {
        "id": "RF-01", "severity": "high", "statement": "Resolved", "disposition": "resolved",
        "resolution_evidence_refs": ["MISSING-01"],
    }
    assert "SEMANTIC-REFERENCE" in ids(valid_contract, policy, repository_root)


def test_command_allowlist_is_exact(valid_contract, policy, repository_root):
    valid_contract["requirements"][0]["acceptance_criteria"][0]["verification"]["run"] += " "
    assert "SEMANTIC-COMMAND-NOT-ALLOWED" in ids(valid_contract, policy, repository_root)


def test_source_base_path_and_symbol_are_verified(valid_contract, policy, repository_root):
    valid_contract["provenance"]["sources"][0]["role"] = "related"
    valid_contract["base_commit"] = "f" * 40
    assert {"SEMANTIC-SOURCE-CHAIN", "SEMANTIC-BASE-COMMIT"}.issubset(ids(valid_contract, policy, repository_root))
    valid_contract["base_commit"] = "0c934ebff5f442e5619136aaf95a106b7a677acd"
    valid_contract["affected_areas"][0]["symbol"] = "definitely-not-a-symbol"
    assert "SEMANTIC-AFFECTED-SYMBOL" in ids(valid_contract, policy, repository_root)


def test_review_and_approval_are_current_authorized_and_separated(valid_contract, policy, repository_root):
    valid_contract["evidence"].append({
        "id": "E-02", "type": "human-decision", "locator": "issue:1#comment-2",
        "summary": "Approval decision", "captured_at": "2026-08-22T00:01:00Z", "content_sha256": None,
    })
    valid_contract["claims"].append({"id": "D-01", "type": "decision", "statement": "Boundary", "evidence_refs": ["E-02"]})
    valid_contract["issue_revision"] = 2
    digest = subject_digest(valid_contract)
    valid_contract["review"].update(reviewed_by="reviewer", result="pass", subject_revision=2, subject_digest=digest, evidence_refs=["E-02"])
    valid_contract["approval"].update(decision="approved", actor="approver", decided_at="2026-08-22T00:02:00Z", evidence_ref="E-02", subject_revision=2, subject_digest=digest)
    assert not ids(valid_contract, policy, repository_root)
    valid_contract["approval"]["actor"] = "reviewer"
    assert {"SEMANTIC-APPROVER-AUTH", "SEMANTIC-APPROVAL-SEPARATION"}.issubset(ids(valid_contract, policy, repository_root))


def test_stale_attestations_and_wrong_full_hash_fail(valid_contract, policy, repository_root):
    valid_contract["review"].update(reviewed_by="reviewer", result="fail", subject_revision=1, subject_digest="sha256:" + "0" * 64)
    valid_contract["freeze"]["contract_hash"] = "sha256:" + "A" * 64
    assert {"SEMANTIC-REVIEW-STALE", "SEMANTIC-CONTRACT-HASH"}.issubset(ids(valid_contract, policy, repository_root))


def test_numbers_outside_the_rfc_8785_domain_fail(valid_contract, policy, repository_root):
    valid_contract["issue_revision"] = 9007199254740992
    assert "CANONICAL-INVALID" in ids(valid_contract, policy, repository_root)


def _lock_repository(tmp_path):
    repository = tmp_path / "repository"
    (repository / "docs").mkdir(parents=True)
    (repository / "README.md").write_text("fixture\n", encoding="utf-8")
    (repository / "dependencies.json").write_text('{"widget":"1.2.3"}\n', encoding="utf-8")
    (repository / "docs/reference.md").write_bytes(b"locked documentation\n")
    subprocess.run(["git", "init", "-q", "-b", "main"], cwd=repository, check=True)
    subprocess.run(["git", "add", "."], cwd=repository, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Fixture User", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", "test fixture",
        ],
        cwd=repository,
        check=True,
    )
    commit = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()
    return repository, commit


def _commit_file(repository, path, content):
    (repository / path).write_text(content, encoding="utf-8")
    subprocess.run(["git", "add", path], cwd=repository, check=True)
    subprocess.run(
        [
            "git", "-c", "user.name=Fixture User", "-c", "user.email=fixture@example.invalid",
            "-c", "commit.gpgsign=false", "commit", "-q", "-m", f"add {path}",
        ],
        cwd=repository,
        check=True,
    )
    return subprocess.run(["git", "rev-parse", "HEAD"], cwd=repository, check=True, text=True, capture_output=True).stdout.strip()


def _lock_fixture(repository_root, name, commit):
    raw = (repository_root / "tests/governance/fixtures/contracts/locks" / name).read_text(encoding="utf-8")
    return json.loads(raw.replace("BASE_COMMIT", commit))


def test_valid_repository_backed_dependency_and_document_locks(valid_contract, policy, repository_root, tmp_path):
    repository, commit = _lock_repository(tmp_path)
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract.update(_lock_fixture(repository_root, "valid.json", commit))
    assert not ids(valid_contract, policy, repository)


@pytest.mark.parametrize(
    "fixture,expected",
    [
        ("missing.json", {"SEMANTIC-DEPENDENCY-LOCK-MISSING", "SEMANTIC-DOCUMENT-LOCK-MISSING"}),
        ("stale.json", {"SEMANTIC-DEPENDENCY-LOCK-STALE", "SEMANTIC-DOCUMENT-LOCK-STALE"}),
        ("hash-mismatch.json", {"SEMANTIC-DOCUMENT-LOCK-HASH"}),
        ("unverifiable.json", {"SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE", "SEMANTIC-DOCUMENT-LOCK-UNVERIFIABLE"}),
    ],
)
def test_missing_stale_hash_mismatch_and_unverifiable_locks_fail_closed(
    valid_contract, policy, repository_root, tmp_path, fixture, expected
):
    repository, commit = _lock_repository(tmp_path)
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract.update(_lock_fixture(repository_root, fixture, commit))
    assert expected.issubset(ids(valid_contract, policy, repository))


def test_pip_compile_hash_continuations_bind_exact_versions(repository_root):
    fixture = repository_root / "tests/governance/fixtures/contracts/locks/pip-compile.txt"
    assert _dependency_versions(fixture.read_bytes(), "requirements.txt", "my-package") == {"1.2.3"}
    real_lock = repository_root / ".github/governance/requirements.txt"
    assert _dependency_versions(real_lock.read_bytes(), "requirements.txt", "jsonschema") == {"4.26.0"}


def test_pip_compile_normalized_name_conflicts_fail_closed(valid_contract, policy, repository_root, tmp_path):
    repository, _ = _lock_repository(tmp_path)
    fixture = repository_root / "tests/governance/fixtures/contracts/locks/pip-conflict.txt"
    commit = _commit_file(repository, "requirements.txt", fixture.read_text(encoding="utf-8"))
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract["dependency_locks"] = [{"name": "MY.PACKAGE", "version": "1.2.3", "source": "requirements.txt"}]
    assert "SEMANTIC-DEPENDENCY-LOCK-CONFLICT" in ids(valid_contract, policy, repository)


@pytest.mark.parametrize(
    "requirement",
    [
        "my-package>=1.2.3\n",
        "my-package @ https://example.invalid/archive.whl\n",
        "-e https://example.invalid/repository#egg=my-package\n",
        "my-package==1.2.3; python_version > '3.12'\n",
    ],
)
def test_unsupported_requirement_forms_are_unverifiable(
    valid_contract, policy, repository_root, tmp_path, requirement
):
    repository, _ = _lock_repository(tmp_path)
    commit = _commit_file(repository, "requirements.in", requirement)
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract["dependency_locks"] = [{"name": "my-package", "version": "1.2.3", "source": "requirements.in"}]
    assert "SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE" in ids(valid_contract, policy, repository)


@pytest.mark.parametrize("version", ["3.14.4", "1!3.14.4", "3.14.4+vendor.1"])
def test_python_version_source_accepts_exact_pep440_versions(version):
    assert _dependency_versions((version + "\n").encode(), ".python-version", "python") == {version}


@pytest.mark.parametrize("version", ["latest", "1.2.3!", "", "1.2.3 current"])
def test_python_version_source_rejects_non_pep440_versions(version):
    assert _dependency_versions((version + "\n").encode(), ".python-version", "python") is None


def test_python_version_lock_is_validated_end_to_end(valid_contract, policy, tmp_path):
    repository, _ = _lock_repository(tmp_path)
    commit = _commit_file(repository, ".python-version", "latest\n")
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract["dependency_locks"] = [{"name": "python", "version": "latest", "source": ".python-version"}]
    assert "SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE" in ids(valid_contract, policy, repository)


def test_packaging_is_a_declared_direct_dependency(repository_root):
    requirements = (repository_root / ".github/governance/requirements.in").read_text(encoding="utf-8")
    assert "packaging==26.3" in requirements.splitlines()


def _requirement_source(source, version):
    requirement = f"widget=={version}"
    if source.endswith(".txt"):
        return requirement + " \\\n    --hash=sha256:0000000000000000000000000000000000000000000000000000000000000000\n"
    return requirement + "\n"


@pytest.mark.parametrize("source", ["requirements.in", "requirements.txt"])
@pytest.mark.parametrize("version", ["latest", "1.2.3!", "legacy", "1.2.3 current"])
def test_requirement_sources_reject_non_pep440_exact_versions(source, version):
    raw = _requirement_source(source, version).encode()
    assert _dependency_versions(raw, source, "widget") is None


@pytest.mark.parametrize("source", ["requirements.in", "requirements.txt"])
@pytest.mark.parametrize("version", ["1!2.3", "2.3.4+vendor.1"])
def test_requirement_sources_accept_epoch_and_local_versions(source, version):
    raw = _requirement_source(source, version).encode()
    assert _dependency_versions(raw, source, "widget") == {version}


@pytest.mark.parametrize("source", ["requirements.in", "requirements.txt"])
@pytest.mark.parametrize(
    "version,valid",
    [
        ("latest", False),
        ("1.2.3!", False),
        ("legacy", False),
        ("1.2.3 current", False),
        ("1!2.3", True),
        ("2.3.4+vendor.1", True),
    ],
)
def test_requirement_version_lock_is_validated_end_to_end(
    valid_contract, policy, tmp_path, source, version, valid
):
    repository, _ = _lock_repository(tmp_path)
    commit = _commit_file(repository, source, _requirement_source(source, version))
    valid_contract["base_commit"] = commit
    valid_contract["affected_areas"][0]["path"] = "README.md"
    valid_contract["dependency_locks"] = [{"name": "widget", "version": version, "source": source}]
    lock_ids = {identifier for identifier in ids(valid_contract, policy, repository) if identifier.startswith("SEMANTIC-DEPENDENCY-LOCK")}
    if valid:
        assert lock_ids == set()
    else:
        assert lock_ids == {"SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE"}

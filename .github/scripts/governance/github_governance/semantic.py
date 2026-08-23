"""Repository-aware semantic joins for Engineering Issue contracts."""

from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path, PurePosixPath
from typing import Any, Iterable

from packaging.version import InvalidVersion, Version

from .canonical import contract_hash, subject_digest
from .errors import Finding
from .policy import authorized


def _all_records(contract: dict[str, Any]) -> Iterable[tuple[str, str]]:
    for collection in ("claims", "evidence", "requirements", "unknowns", "risks"):
        for index, record in enumerate(contract.get(collection, [])):
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                yield record["id"], f"/{collection}/{index}/id"
    for requirement_index, requirement in enumerate(contract.get("requirements", [])):
        if isinstance(requirement, dict):
            for criterion_index, criterion in enumerate(requirement.get("acceptance_criteria", [])):
                if isinstance(criterion, dict) and isinstance(criterion.get("id"), str):
                    yield criterion["id"], f"/requirements/{requirement_index}/acceptance_criteria/{criterion_index}/id"
    review = contract.get("review", {})
    if isinstance(review, dict):
        for index, finding in enumerate(review.get("findings", [])):
            if isinstance(finding, dict) and isinstance(finding.get("id"), str):
                yield finding["id"], f"/review/findings/{index}/id"


def _reference(findings: list[Finding], ref: Any, index: dict[str, tuple[str, dict[str, Any]]], *, path: str, types: set[str] | None = None) -> dict[str, Any] | None:
    target = index.get(ref) if isinstance(ref, str) else None
    if target is None:
        findings.append(Finding("SEMANTIC-REFERENCE", f"reference does not resolve: {ref!r}", path))
        return None
    collection, record = target
    if types is not None and record.get("type") not in types:
        findings.append(Finding("SEMANTIC-REFERENCE-TYPE", f"reference {ref!r} has the wrong evidence type", path))
        return None
    return record


def _git(repository_root: Path, *arguments: str) -> bool:
    result = subprocess.run(
        ["git", "-C", str(repository_root), *arguments],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.returncode == 0


def _git_blob(repository_root: Path, base: str, path: str) -> bytes | None:
    result = subprocess.run(
        ["git", "-C", str(repository_root), "show", f"{base}:{path}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _safe_path(path: Any) -> bool:
    if not isinstance(path, str) or not path or "\\" in path:
        return False
    pure = PurePosixPath(path)
    return not pure.is_absolute() and ".." not in pure.parts and str(pure) == path


def _validate_repository_locations(contract: dict[str, Any], repository_root: Path, findings: list[Finding]) -> None:
    base = contract.get("base_commit")
    if not isinstance(base, str) or not _git(repository_root, "cat-file", "-e", f"{base}^{{commit}}"):
        findings.append(Finding("SEMANTIC-BASE-COMMIT", "base_commit is unavailable in the repository", "/base_commit"))
        return
    for index, area in enumerate(contract.get("affected_areas", [])):
        if not isinstance(area, dict):
            continue
        path = area.get("path")
        location = f"/affected_areas/{index}/path"
        if not _safe_path(path):
            findings.append(Finding("SEMANTIC-AFFECTED-PATH", "affected path must be a safe repository-relative path", location))
            continue
        if not _git(repository_root, "cat-file", "-e", f"{base}:{path}"):
            findings.append(Finding("SEMANTIC-AFFECTED-PATH", "affected path does not exist at base_commit", location))
            continue
        symbol = area.get("symbol")
        if symbol is not None:
            result = subprocess.run(
                ["git", "-C", str(repository_root), "show", f"{base}:{path}"],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                check=False,
            )
            if result.returncode != 0 or str(symbol).encode("utf-8") not in result.stdout:
                findings.append(Finding("SEMANTIC-AFFECTED-SYMBOL", "affected symbol is not present at base_commit", f"/affected_areas/{index}/symbol"))


def _normalized_dependency_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def _requirements_versions(text: str) -> dict[str, set[str]] | None:
    requirement = re.compile(r"([A-Za-z0-9][A-Za-z0-9_.-]*)==([A-Za-z0-9][A-Za-z0-9_.+!-]*)(?:\s+(\\))?")
    hash_option = re.compile(r"--hash=sha256:[0-9a-f]{64}(?:\s+(\\))?")
    versions: dict[str, set[str]] = {}
    continuation = False
    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            if continuation:
                return None
            continue
        if continuation:
            match = hash_option.fullmatch(line)
            if match is None:
                return None
            continuation = match.group(1) is not None
            continue
        match = requirement.fullmatch(line)
        if match is None:
            return None
        try:
            Version(match.group(2))
        except InvalidVersion:
            return None
        normalized = _normalized_dependency_name(match.group(1))
        versions.setdefault(normalized, set()).add(match.group(2))
        continuation = match.group(3) is not None
    return None if continuation else versions


def _dependency_versions(blob: bytes, source: str, name: str) -> set[str] | None:
    try:
        text = blob.decode("utf-8", errors="strict")
    except UnicodeDecodeError:
        return None
    normalized_name = _normalized_dependency_name(name)
    if PurePosixPath(source).name == ".python-version":
        if normalized_name not in {"python", "cpython"}:
            return None
        value = text.strip()
        if not value or value != text.rstrip("\r\n") or "\n" in value or "\r" in value:
            return None
        try:
            Version(value)
        except InvalidVersion:
            return None
        return {value}
    if source.endswith((".txt", ".in")):
        versions = _requirements_versions(text)
        return None if versions is None else versions.get(normalized_name)
    if source.endswith(".json"):
        try:
            value = json.loads(text)
        except (json.JSONDecodeError, UnicodeError):
            return None
        if not isinstance(value, dict):
            return None
        dependencies = value.get("dependencies", value)
        if not isinstance(dependencies, dict):
            return None
        versions: set[str] = set()
        for dependency_name, version in dependencies.items():
            if isinstance(dependency_name, str) and _normalized_dependency_name(dependency_name) == normalized_name:
                if not isinstance(version, str):
                    return None
                versions.add(version)
        return versions or None
    return None


def _validate_locks(contract: dict[str, Any], repository_root: Path, findings: list[Finding]) -> None:
    base = contract.get("base_commit")
    if not isinstance(base, str) or not _git(repository_root, "cat-file", "-e", f"{base}^{{commit}}"):
        return
    for index, lock in enumerate(contract.get("dependency_locks", [])):
        if not isinstance(lock, dict):
            continue
        source = lock.get("source")
        path = f"/dependency_locks/{index}"
        if not _safe_path(source):
            findings.append(Finding("SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE", "dependency source is not a verifiable repository-relative path", path + "/source"))
            continue
        blob = _git_blob(repository_root, base, source)
        if blob is None:
            findings.append(Finding("SEMANTIC-DEPENDENCY-LOCK-MISSING", "dependency source is unavailable at base_commit", path + "/source"))
            continue
        observed = _dependency_versions(blob, source, str(lock.get("name", "")))
        if not observed:
            findings.append(Finding("SEMANTIC-DEPENDENCY-LOCK-UNVERIFIABLE", "dependency source format cannot deterministically bind name and version", path + "/source"))
        elif len(observed) > 1:
            findings.append(Finding("SEMANTIC-DEPENDENCY-LOCK-CONFLICT", "dependency source contains conflicting versions for the normalized package name", path + "/source"))
        elif next(iter(observed)) != lock.get("version"):
            findings.append(Finding("SEMANTIC-DEPENDENCY-LOCK-STALE", "dependency version differs from its base_commit source", path + "/version"))

    for index, lock in enumerate(contract.get("document_locks", [])):
        if not isinstance(lock, dict):
            continue
        locator = lock.get("locator")
        path = f"/document_locks/{index}"
        if not _safe_path(locator) or lock.get("content_sha256") is None:
            findings.append(Finding("SEMANTIC-DOCUMENT-LOCK-UNVERIFIABLE", "document lock requires a repository-relative locator and content SHA-256", path))
            continue
        locked_version = lock.get("version_or_date")
        if isinstance(locked_version, str) and re.fullmatch(r"[0-9a-f]{40}", locked_version):
            if locked_version != base:
                findings.append(Finding("SEMANTIC-DOCUMENT-LOCK-STALE", "document version differs from base_commit", path + "/version_or_date"))
                continue
        else:
            findings.append(Finding("SEMANTIC-DOCUMENT-LOCK-UNVERIFIABLE", "document version_or_date is not an offline-verifiable base commit", path + "/version_or_date"))
            continue
        blob = _git_blob(repository_root, base, locator)
        if blob is None:
            findings.append(Finding("SEMANTIC-DOCUMENT-LOCK-MISSING", "document is unavailable at base_commit", path + "/locator"))
            continue
        if hashlib.sha256(blob).hexdigest() != lock.get("content_sha256"):
            findings.append(Finding("SEMANTIC-DOCUMENT-LOCK-HASH", "document content SHA-256 does not match the base_commit blob", path + "/content_sha256"))


def semantic_findings(contract: dict[str, Any], policy: dict[str, Any], repository_root: str | Path) -> list[Finding]:
    findings: list[Finding] = []
    records = list(_all_records(contract))
    seen: dict[str, str] = {}
    for identifier, path in records:
        if identifier in seen:
            findings.append(Finding("SEMANTIC-DUPLICATE-ID", f"ID {identifier} is also used at {seen[identifier]}", path))
        else:
            seen[identifier] = path

    indexed: dict[str, tuple[str, dict[str, Any]]] = {}
    for collection in ("evidence", "risks"):
        for record in contract.get(collection, []):
            if isinstance(record, dict) and isinstance(record.get("id"), str):
                indexed.setdefault(record["id"], (collection, record))

    for index, claim in enumerate(contract.get("claims", [])):
        if not isinstance(claim, dict):
            continue
        resolved = [
            _reference(findings, ref, indexed, path=f"/claims/{index}/evidence_refs/{ref}")
            for ref in claim.get("evidence_refs", [])
        ]
        if claim.get("type") == "decision" and not any(
            item is not None and item.get("type") == "human-decision" for item in resolved
        ):
            findings.append(Finding("SEMANTIC-DECISION-EVIDENCE", "Decision requires human-decision evidence", f"/claims/{index}"))

    for index, unknown in enumerate(contract.get("unknowns", [])):
        if not isinstance(unknown, dict):
            continue
        if unknown.get("resolution") == "resolved":
            refs = unknown.get("resolution_evidence_refs", [])
            if not refs:
                findings.append(Finding("SEMANTIC-UNKNOWN-RESOLUTION", "resolved Unknown requires resolution evidence", f"/unknowns/{index}"))
            for ref in refs:
                _reference(findings, ref, indexed, path=f"/unknowns/{index}/resolution_evidence_refs/{ref}")
        if unknown.get("impact") == "low" and unknown.get("resolution") == "open":
            for ref in unknown.get("risk_refs", []):
                target = _reference(findings, ref, indexed, path=f"/unknowns/{index}/risk_refs/{ref}")
                if target is not None and indexed[ref][0] != "risks":
                    findings.append(Finding("SEMANTIC-UNKNOWN-RISK", "Unknown risk_refs must resolve to risks", f"/unknowns/{index}/risk_refs"))

    review = contract.get("review", {})
    if isinstance(review, dict):
        for index, ref in enumerate(review.get("evidence_refs", [])):
            _reference(findings, ref, indexed, path=f"/review/evidence_refs/{index}")
        for finding_index, review_finding in enumerate(review.get("findings", [])):
            if not isinstance(review_finding, dict):
                continue
            if review_finding.get("disposition") == "open":
                findings.append(Finding("SEMANTIC-OPEN-FINDING", "every review finding must be resolved", f"/review/findings/{finding_index}"))
            for ref_index, ref in enumerate(review_finding.get("resolution_evidence_refs", [])):
                _reference(findings, ref, indexed, path=f"/review/findings/{finding_index}/resolution_evidence_refs/{ref_index}")

    digest = None
    try:
        digest = subject_digest(contract)
        contract_hash(contract)
    except Exception:
        findings.append(Finding("CANONICAL-INVALID", "contract is outside the RFC 8785 domain", "/"))
    if review.get("result") in ("pass", "fail"):
        actor = review.get("reviewed_by")
        try:
            is_reviewer = isinstance(actor, str) and authorized(policy, "trusted_reviewers", actor)
        except Exception:
            is_reviewer = False
        if not is_reviewer:
            findings.append(Finding("SEMANTIC-REVIEWER-AUTH", "review actor is not trusted", "/review/reviewed_by"))
        if review.get("subject_revision") != contract.get("issue_revision") or review.get("subject_digest") != digest:
            findings.append(Finding("SEMANTIC-REVIEW-STALE", "review does not attest the current subject", "/review"))
        if isinstance(actor, str) and actor.lower() == str(contract.get("provenance", {}).get("created_by", "")).lower():
            findings.append(Finding("SEMANTIC-REVIEW-SEPARATION", "reviewer must differ from Candidate author", "/review/reviewed_by"))

    approval = contract.get("approval", {})
    if isinstance(approval, dict) and approval.get("decision") in ("approved", "rejected"):
        actor = approval.get("actor")
        try:
            is_author = isinstance(actor, str) and authorized(policy, "trusted_issue_authors", actor)
        except Exception:
            is_author = False
        if not is_author:
            findings.append(Finding("SEMANTIC-APPROVER-AUTH", "approval actor is not trusted", "/approval/actor"))
        if approval.get("subject_revision") != contract.get("issue_revision") or approval.get("subject_digest") != digest:
            findings.append(Finding("SEMANTIC-APPROVAL-STALE", "approval does not attest the current subject", "/approval"))
        evidence_ref = approval.get("evidence_ref")
        if evidence_ref is not None:
            _reference(findings, evidence_ref, indexed, path="/approval/evidence_ref", types={"human-decision"})
        reviewer = review.get("reviewed_by")
        if isinstance(actor, str) and isinstance(reviewer, str) and actor.lower() == reviewer.lower():
            findings.append(Finding("SEMANTIC-APPROVAL-SEPARATION", "approval actor must differ from reviewer", "/approval/actor"))

    for requirement_index, requirement in enumerate(contract.get("requirements", [])):
        if not isinstance(requirement, dict):
            continue
        polarities = {criterion.get("polarity") for criterion in requirement.get("acceptance_criteria", []) if isinstance(criterion, dict)}
        if not {"positive", "negative"}.issubset(polarities):
            findings.append(Finding("SEMANTIC-AC-POLARITY", "requirement needs positive and negative criteria", f"/requirements/{requirement_index}"))
        for criterion_index, criterion in enumerate(requirement.get("acceptance_criteria", [])):
            if not isinstance(criterion, dict):
                continue
            verification = criterion.get("verification", {})
            if isinstance(verification, dict) and verification.get("type") == "command":
                if verification.get("run") not in policy["allowed_verification_commands"]:
                    findings.append(Finding("SEMANTIC-COMMAND-NOT-ALLOWED", "verification command is not exactly allowlisted", f"/requirements/{requirement_index}/acceptance_criteria/{criterion_index}/verification/run"))

    sources = contract.get("provenance", {}).get("sources", [])
    roles = [source.get("role") for source in sources if isinstance(source, dict)]
    status = contract.get("status")
    if status == "candidate" and "intake" not in roles:
        findings.append(Finding("SEMANTIC-SOURCE-CHAIN", "Candidate must retain an Intake source", "/provenance/sources"))
    if status != "candidate" and "candidate" not in roles:
        findings.append(Finding("SEMANTIC-SOURCE-CHAIN", "Engineering Issue must retain a Candidate source", "/provenance/sources"))

    _validate_repository_locations(contract, Path(repository_root), findings)
    _validate_locks(contract, Path(repository_root), findings)
    stored_hash = contract.get("freeze", {}).get("contract_hash")
    if stored_hash is not None:
        try:
            expected = contract_hash(contract)
        except Exception:
            expected = None
        if stored_hash != expected:
            findings.append(Finding("SEMANTIC-CONTRACT-HASH", "freeze.contract_hash does not match the recomputed hash", "/freeze/contract_hash"))
    return sorted(findings)

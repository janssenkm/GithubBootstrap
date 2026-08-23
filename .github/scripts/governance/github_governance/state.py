"""Pure V1 entity state and revision invariants."""

from __future__ import annotations

import copy
from typing import Any

from .canonical import contract_hash, normative_changed, subject_digest
from .errors import Finding, GovernanceError
from .policy import normalize_login


TRANSITIONS = {
    "intake": {
        "new": {"triaged"},
        "triaged": {"investigating", "closed"},
        "investigating": {"closed", "draft"},
        "closed": set(),
    },
    "candidate": {
        "draft": {"gate-failed", "gate-passed", "closed"},
        "gate-failed": {"draft"},
        "gate-passed": {"promoted"},
        "promoted": {"draft"},
        "closed": set(),
    },
    "engineering": {
        "contracted": {"ready", "cancelled"},
        "ready": {"in-progress", "cancelled"},
        "in-progress": {"done", "cancelled"},
        "done": set(),
        "cancelled": set(),
    },
}
KNOWN_STATES = {state for states in TRANSITIONS.values() for state in states}


def can_transition(entity: str, before: str, after: str) -> bool:
    return after in TRANSITIONS.get(entity, {}).get(before, set())


def require_transition(entity: str, before: str, after: str) -> None:
    if entity not in TRANSITIONS:
        raise GovernanceError("STATE-UNKNOWN", f"unknown {entity!r} state {before!r}", code=3)
    if before not in TRANSITIONS[entity] or after not in KNOWN_STATES:
        raise GovernanceError("STATE-UNKNOWN", f"unknown {entity!r} state in {before!r} -> {after!r}", code=3)
    if not can_transition(entity, before, after):
        raise GovernanceError("STATE-TRANSITION", f"forbidden transition: {entity} {before} -> {after}", code=3)


def require_revision_change(before: dict[str, Any], after: dict[str, Any]) -> None:
    before_revision = before.get("issue_revision")
    after_revision = after.get("issue_revision")
    if not isinstance(before_revision, int) or not isinstance(after_revision, int):
        raise GovernanceError("REVISION-TYPE", "issue_revision must be an integer")
    changed = normative_changed(before, after)
    expected = before_revision + 1 if changed else before_revision
    if after_revision != expected:
        raise GovernanceError(
            "REVISION-MISMATCH",
            f"issue_revision must be {expected} when normative subject changed={str(changed).lower()}",
        )


def _freeze(target: dict[str, Any], actor: str, frozen_at: str) -> None:
    normalized = normalize_login(actor)
    if not isinstance(frozen_at, str) or not frozen_at:
        raise GovernanceError("FREEZE-TIME", "freeze time is missing or invalid", code=2)
    freeze = target.get("freeze")
    if not isinstance(freeze, dict):
        raise GovernanceError("HASH-FREEZE-SHAPE", "freeze must be an object")
    freeze.update(
        hash_algorithm="RFC8785+SHA-256",
        contract_hash=None,
        frozen_at=frozen_at,
        frozen_by=normalized,
    )
    freeze["contract_hash"] = contract_hash(target)


def build_promotion_target(
    candidate: dict[str, Any],
    *,
    repository: str,
    candidate_number: int,
    actor: str,
    frozen_at: str,
) -> dict[str, Any]:
    """Render the deterministic contracted target without changing its subject."""

    if candidate.get("status") != "candidate":
        raise GovernanceError("PROMOTION-STATUS", "only a Candidate contract can be promoted", code=3)
    if candidate.get("review", {}).get("result") != "pass":
        raise GovernanceError("PROMOTION-REVIEW", "current independent review must pass", code=3)
    if candidate.get("approval", {}).get("decision") != "approved":
        raise GovernanceError("PROMOTION-APPROVAL", "current human approval is required", code=3)
    if not isinstance(candidate_number, int) or isinstance(candidate_number, bool) or candidate_number < 1:
        raise GovernanceError("PROMOTION-SOURCE", "Candidate Issue number is invalid", code=2)
    if not isinstance(repository, str) or repository.count("/") != 1:
        raise GovernanceError("PROMOTION-SOURCE", "Candidate repository must be owner/name", code=2)
    if any(
        item.get("impact") == "high" and item.get("resolution") == "open"
        for item in candidate.get("unknowns", [])
        if isinstance(item, dict)
    ):
        raise GovernanceError("PROMOTION-HIGH-UNKNOWN", "a high-impact Unknown remains open", code=3)

    before_digest = subject_digest(candidate)
    before_revision = candidate.get("issue_revision")
    target = copy.deepcopy(candidate)
    target["status"] = "contracted"
    provenance = target.get("provenance")
    if not isinstance(provenance, dict) or not isinstance(provenance.get("sources"), list):
        raise GovernanceError("PROMOTION-PROVENANCE", "promotion provenance is missing")
    provenance["promoted_by"] = normalize_login(actor)
    candidate_source = {"repository": repository, "number": candidate_number, "role": "candidate"}
    matches = [source for source in provenance["sources"] if isinstance(source, dict) and source.get("role") == "candidate"]
    if matches and matches != [candidate_source]:
        raise GovernanceError("PROMOTION-SOURCE-CONFLICT", "Candidate source conflicts with the promotion target", code=3)
    if not matches:
        provenance["sources"].append(candidate_source)
    _freeze(target, actor, frozen_at)
    if target.get("issue_revision") != before_revision or subject_digest(target) != before_digest:
        raise GovernanceError("PROMOTION-SUBJECT-CHANGED", "promotion changed the normative Candidate subject", code=3)
    return target


def build_ready_target(contract: dict[str, Any], *, actor: str, frozen_at: str) -> dict[str, Any]:
    """Render the lifecycle-only ready target and recompute its full hash."""

    if contract.get("status") != "contracted":
        raise GovernanceError("READY-STATUS", "only a contracted Engineering Issue can become ready", code=3)
    stored = contract.get("freeze", {}).get("contract_hash")
    if not isinstance(stored, str) or stored != contract_hash(contract):
        raise GovernanceError("STALE-CONTRACT-HASH", "contract freeze differs from the recomputed full hash", code=3)
    digest = subject_digest(contract)
    revision = contract.get("issue_revision")
    review = contract.get("review", {})
    approval = contract.get("approval", {})
    if review.get("result") != "pass" or review.get("subject_revision") != revision or review.get("subject_digest") != digest:
        raise GovernanceError("STALE-REVIEW", "review does not attest the current subject", code=3)
    if approval.get("decision") != "approved" or approval.get("subject_revision") != revision or approval.get("subject_digest") != digest:
        raise GovernanceError("STALE-APPROVAL", "approval does not attest the current subject", code=3)
    target = copy.deepcopy(contract)
    target["status"] = "ready"
    _freeze(target, actor, frozen_at)
    if target.get("issue_revision") != revision or subject_digest(target) != digest:
        raise GovernanceError("READY-SUBJECT-CHANGED", "ready transition changed the normative subject", code=3)
    if target["review"] != contract["review"] or target["approval"] != contract["approval"]:
        raise GovernanceError("READY-ATTESTATION-CHANGED", "ready transition changed an attestation", code=3)
    return target


def ready_findings(
    contract: dict[str, Any], policy: dict[str, Any], repository_root: Any
) -> list[Finding]:
    """Return stable stale classes for every readiness prerequisite."""

    from .schema_validation import schema_findings
    from .semantic import semantic_findings

    findings: list[Finding] = []
    digest: str | None
    try:
        digest = subject_digest(contract)
        expected_hash = contract_hash(contract)
    except GovernanceError:
        digest = None
        expected_hash = None
    if contract.get("status") != "contracted":
        findings.append(Finding("READY-STATUS", "Engineering Issue is not contracted", "/status"))
    if contract.get("freeze", {}).get("contract_hash") != expected_hash:
        findings.append(Finding("STALE-CONTRACT-HASH", "contract freeze differs from the recomputed full hash", "/freeze/contract_hash"))
    revision = contract.get("issue_revision")
    review = contract.get("review", {})
    if review.get("result") != "pass" or review.get("subject_revision") != revision or review.get("subject_digest") != digest:
        findings.append(Finding("STALE-REVIEW", "review does not attest the current subject", "/review"))
    approval = contract.get("approval", {})
    if approval.get("decision") != "approved" or approval.get("subject_revision") != revision or approval.get("subject_digest") != digest:
        findings.append(Finding("STALE-APPROVAL", "approval does not attest the current subject", "/approval"))
    deterministic = schema_findings(contract, repository_root)
    if not deterministic:
        deterministic = semantic_findings(contract, policy, repository_root)
    for finding in deterministic:
        if finding.id == "SEMANTIC-BASE-COMMIT":
            findings.append(Finding("STALE-BASE-COMMIT", finding.message, finding.path))
        elif finding.id.startswith("SEMANTIC-DEPENDENCY-LOCK"):
            findings.append(Finding("STALE-DEPENDENCY-LOCK", finding.message, finding.path))
        elif finding.id.startswith("SEMANTIC-DOCUMENT-LOCK"):
            findings.append(Finding("STALE-DOCUMENT-LOCK", finding.message, finding.path))
        elif finding.id == "SEMANTIC-REVIEW-STALE":
            findings.append(Finding("STALE-REVIEW", finding.message, finding.path))
        elif finding.id == "SEMANTIC-APPROVAL-STALE":
            findings.append(Finding("STALE-APPROVAL", finding.message, finding.path))
        elif finding.id == "SEMANTIC-CONTRACT-HASH":
            findings.append(Finding("STALE-CONTRACT-HASH", finding.message, finding.path))
        else:
            findings.append(finding)
    return sorted(set(findings))

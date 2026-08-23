"""RFC 8785 canonicalization and V1 contract digest rules."""

from __future__ import annotations

import copy
import hashlib
from typing import Any

import rfc8785

from .errors import GovernanceError


NORMATIVE_FIELDS = (
    "schema_version",
    "issue_revision",
    "base_commit",
    "claims",
    "evidence",
    "goal",
    "non_goals",
    "affected_areas",
    "constraints",
    "implementation_boundaries",
    "requirements",
    "unknowns",
    "risks",
    "dependency_locks",
    "document_locks",
)


def canonicalize(value: Any) -> bytes:
    try:
        return rfc8785.dumps(value)
    except (rfc8785.CanonicalizationError, UnicodeError, RecursionError) as error:
        raise GovernanceError("CANONICAL-INVALID", "value is outside the RFC 8785 domain") from error


def sha256_tagged(value: Any) -> str:
    return "sha256:" + hashlib.sha256(canonicalize(value)).hexdigest()


def normative_projection(contract: dict[str, Any]) -> dict[str, Any]:
    missing = [field for field in NORMATIVE_FIELDS if field not in contract]
    if missing:
        raise GovernanceError("DIGEST-MISSING-FIELD", f"normative fields are missing: {', '.join(missing)}")
    return {field: copy.deepcopy(contract[field]) for field in NORMATIVE_FIELDS}


def normative_content_projection(contract: dict[str, Any]) -> dict[str, Any]:
    """Return normative meaning without the derived revision counter."""

    projection = normative_projection(contract)
    del projection["issue_revision"]
    return projection


def subject_digest(contract: dict[str, Any]) -> str:
    return sha256_tagged(normative_projection(contract))


def contract_hash(contract: dict[str, Any]) -> str:
    target = copy.deepcopy(contract)
    try:
        del target["freeze"]["contract_hash"]
    except (KeyError, TypeError) as error:
        raise GovernanceError("HASH-FREEZE-SHAPE", "freeze.contract_hash must exist before hashing") from error
    return sha256_tagged(target)


def verify_contract_hash(contract: dict[str, Any]) -> bool:
    stored = contract.get("freeze", {}).get("contract_hash")
    return stored is None or stored == contract_hash(contract)


def normative_changed(before: dict[str, Any], after: dict[str, Any]) -> bool:
    return canonicalize(normative_content_projection(before)) != canonicalize(normative_content_projection(after))

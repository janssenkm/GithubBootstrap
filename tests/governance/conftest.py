from __future__ import annotations

import copy
import json
import sys
from pathlib import Path

import pytest


REPOSITORY_ROOT = Path(__file__).parents[2]
PACKAGE_ROOT = REPOSITORY_ROOT / ".github" / "scripts" / "governance"
sys.path.insert(0, str(PACKAGE_ROOT))

VALID_CONTRACT = REPOSITORY_ROOT / "tests" / "governance" / "fixtures" / "contracts" / "valid-candidate.md"


@pytest.fixture
def repository_root() -> Path:
    return REPOSITORY_ROOT


@pytest.fixture
def valid_body() -> bytes:
    return VALID_CONTRACT.read_bytes()


@pytest.fixture
def valid_contract(valid_body):
    from github_governance.contract import extract_contract

    return copy.deepcopy(extract_contract(valid_body).contract)


@pytest.fixture
def policy() -> dict:
    return {
        "schema_version": 1,
        "rollout_mode": "dry-run",
        "trusted_issue_authors": ["approver"],
        "trusted_developers": ["developer"],
        "trusted_reviewers": ["reviewer"],
        "allowed_verification_commands": ["test -f README.md"],
    }


def render_body(contract: dict, before: str = "Narrative before.\n\n", after: str = "\nNarrative after.\n") -> bytes:
    payload = json.dumps(contract, ensure_ascii=False, indent=2)
    return (
        before
        + "<!-- engineering-contract:start -->\n```json\n"
        + payload
        + "\n```\n<!-- engineering-contract:end -->"
        + after
    ).encode("utf-8")

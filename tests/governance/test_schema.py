from __future__ import annotations

import copy

import pytest

from github_governance.schema_validation import schema_findings


def finding_ids(contract, repository_root):
    return {finding.id for finding in schema_findings(contract, repository_root)}


def test_valid_candidate_passes_draft_2020_12_schema(valid_contract, repository_root):
    assert schema_findings(valid_contract, repository_root) == []


@pytest.mark.parametrize(
    "mutation",
    [
        lambda value: value.pop("goal"),
        lambda value: value.update(extra=True),
        lambda value: value.update(schema_version="1.0.1"),
        lambda value: value.update(base_commit="HEAD"),
        lambda value: value["evidence"][0].update(captured_at="not-a-date"),
        lambda value: value["claims"][0].update(evidence_refs=[]),
        lambda value: value["requirements"][0].update(acceptance_criteria=value["requirements"][0]["acceptance_criteria"][:1]),
    ],
)
def test_required_type_format_additional_and_conditionals_fail(valid_contract, repository_root, mutation):
    mutation(valid_contract)
    assert finding_ids(valid_contract, repository_root)


def test_low_open_unknown_requires_containment_and_risk(valid_contract, repository_root):
    valid_contract["unknowns"] = [{"id": "U-01", "statement": "Unknown", "impact": "low", "resolution": "open"}]
    assert "SCHEMA-INVALID" in finding_ids(valid_contract, repository_root)


def test_high_open_unknown_is_allowed_only_for_candidate(valid_contract, repository_root):
    valid_contract["unknowns"] = [{"id": "U-01", "statement": "Unknown", "impact": "high", "resolution": "open"}]
    assert schema_findings(valid_contract, repository_root) == []
    valid_contract["status"] = "contracted"
    assert "SCHEMA-INVALID" in finding_ids(valid_contract, repository_root)


def test_review_finding_resolution_conditionals(valid_contract, repository_root):
    finding = {"id": "RF-01", "severity": "high", "statement": "Problem", "disposition": "resolved"}
    valid_contract["review"]["findings"] = [finding]
    assert "SCHEMA-INVALID" in finding_ids(valid_contract, repository_root)
    finding["resolution_evidence_refs"] = ["E-01"]
    assert schema_findings(valid_contract, repository_root) == []
    finding["disposition"] = "open"
    assert "SCHEMA-INVALID" in finding_ids(valid_contract, repository_root)

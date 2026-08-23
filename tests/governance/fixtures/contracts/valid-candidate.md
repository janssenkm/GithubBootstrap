Candidate fixture narrative. This text is not contractual.

<!-- engineering-contract:start -->
```json
{
  "schema_version": "1.0.0",
  "kind": "engineering-issue-contract",
  "status": "candidate",
  "provenance": {
    "created_by": "fixture-author",
    "promoted_by": null,
    "sources": [
      {
        "repository": "janssenkm/GithubBootstrap",
        "number": 1,
        "role": "intake"
      }
    ]
  },
  "issue_revision": 1,
  "base_commit": "0c934ebff5f442e5619136aaf95a106b7a677acd",
  "claims": [
    {
      "id": "F-01",
      "type": "fact",
      "statement": "README.md exists at the locked base commit.",
      "evidence_refs": ["E-01"]
    }
  ],
  "evidence": [
    {
      "id": "E-01",
      "type": "repository-file",
      "locator": "README.md@0c934ebff5f442e5619136aaf95a106b7a677acd",
      "summary": "The repository file was inspected at the locked base commit.",
      "captured_at": "2026-08-22T00:00:00Z",
      "content_sha256": null
    }
  ],
  "goal": "Keep the repository README present and deterministically verifiable.",
  "non_goals": [
    "Change files outside the declared affected area."
  ],
  "affected_areas": [
    {
      "path": "README.md",
      "symbol": null,
      "reason": "The fixture verifies a path known to exist at base_commit."
    }
  ],
  "constraints": [
    "Validation remains offline after dependency installation."
  ],
  "implementation_boundaries": [
    "Do not execute commands taken from the contract in GitHub Actions."
  ],
  "requirements": [
    {
      "id": "R-01",
      "statement": "The affected file remains available.",
      "acceptance_criteria": [
        {
          "id": "AC-01",
          "polarity": "positive",
          "statement": "README.md is present.",
          "verification": {
            "type": "command",
            "run": "test -f README.md",
            "expected_result": "The command exits with status 0."
          }
        },
        {
          "id": "AC-02",
          "polarity": "negative",
          "statement": "No unrelated file is required by this fixture.",
          "verification": {
            "type": "observation",
            "observe": "Inspect the affected_areas list.",
            "expected_result": "Only README.md is named."
          }
        }
      ]
    }
  ],
  "unknowns": [],
  "risks": [
    {
      "id": "RSK-01",
      "description": "The base commit could be unavailable in a shallow checkout.",
      "mitigation": "Fetch the contract base commit before running the local Gate."
    }
  ],
  "dependency_locks": [],
  "document_locks": [],
  "review": {
    "mode": "independent-adversarial",
    "reviewed_by": null,
    "result": "pending",
    "subject_revision": null,
    "subject_digest": null,
    "findings": [],
    "evidence_refs": []
  },
  "approval": {
    "decision": "pending",
    "actor": null,
    "decided_at": null,
    "evidence_ref": null,
    "subject_revision": null,
    "subject_digest": null
  },
  "freeze": {
    "hash_algorithm": "RFC8785+SHA-256",
    "contract_hash": null,
    "frozen_at": null,
    "frozen_by": null
  }
}
```
<!-- engineering-contract:end -->

Fixture narrative after the contract.

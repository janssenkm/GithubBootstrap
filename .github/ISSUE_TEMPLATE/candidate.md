<!--
This is not a public Issue Form. It is a repository-owned source for a local
Spec Author preparing a Candidate after read-only investigation.

Before creating a Candidate, replace every illustrative value, preserve the
Intake source, and run the deterministic Gate. The GitHub Issue author must
match provenance.created_by and be authorized by trusted_issue_authors in
.github/project-policy.yml. A Candidate grants no development authority.
-->

# Candidate Engineering Issue

Replace this narrative with a concise motivation and links to the Evidence
Bundle. Narrative outside the markers is not contractual.

<!-- engineering-contract:start -->
```json
{
  "schema_version": "1.0.0",
  "kind": "engineering-issue-contract",
  "status": "candidate",
  "provenance": {
    "created_by": "replace-with-trusted-author",
    "promoted_by": null,
    "sources": [
      {
        "repository": "owner/repository",
        "number": 1,
        "role": "intake"
      }
    ]
  },
  "issue_revision": 1,
  "base_commit": "0000000000000000000000000000000000000000",
  "claims": [
    {
      "id": "F-01",
      "type": "fact",
      "statement": "Replace with a verified fact.",
      "evidence_refs": ["E-01"]
    }
  ],
  "evidence": [
    {
      "id": "E-01",
      "type": "repository-file",
      "locator": "path@0000000000000000000000000000000000000000",
      "summary": "Replace with evidence captured at the locked base commit.",
      "captured_at": "1970-01-01T00:00:00Z",
      "content_sha256": null
    }
  ],
  "goal": "Replace with one falsifiable behavioral outcome.",
  "non_goals": [
    "Replace with an explicit excluded scope."
  ],
  "affected_areas": [
    {
      "path": "replace/with/verified/path",
      "symbol": null,
      "reason": "Replace with evidence for this affected area."
    }
  ],
  "constraints": [
    "Replace with an implementation constraint."
  ],
  "implementation_boundaries": [
    "Do not modify unrelated files or behavior."
  ],
  "requirements": [
    {
      "id": "R-01",
      "statement": "Replace with a testable requirement.",
      "acceptance_criteria": [
        {
          "id": "AC-01",
          "polarity": "positive",
          "statement": "Replace with what must occur.",
          "verification": {
            "type": "command",
            "run": "test -f README.md",
            "expected_result": "Replace with the expected exit status or output."
          }
        },
        {
          "id": "AC-02",
          "polarity": "negative",
          "statement": "Replace with what must not occur.",
          "verification": {
            "type": "observation",
            "observe": "Inspect the pull-request file list against affected_areas.",
            "expected_result": "Every changed file is traced to R-01."
          }
        }
      ]
    }
  ],
  "unknowns": [],
  "risks": [
    {
      "id": "RSK-01",
      "description": "Replace with a concrete risk.",
      "mitigation": "Replace with an actionable mitigation."
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

This Candidate remains `draft` until independent review, human approval, and
the deterministic Gate all pass. It cannot enter development or become ready.

After all review findings and their pre-existing evidence are represented in
the current Candidate, an independent trusted reviewer may post exactly:

```text
/review-contract <positive-integer-revision> sha256:<64-lowercase-hex-subject-digest> sha256:<64-lowercase-hex-review-block-digest>
```

After a unique normative `human-decision` evidence record is selected in
`approval.evidence_ref`, a trusted Issue author other than that reviewer may
post exactly:

```text
/approve-contract <positive-integer-revision> sha256:<64-lowercase-hex-subject-digest>
```

Commands in prose, quotes, fences, edited comments, pull requests, or with
extra text are no-ops. These commands attest existing evidence; they do not add
evidence, execute verification text, promote the Candidate, or authorize work.

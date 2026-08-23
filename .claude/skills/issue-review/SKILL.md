---
name: issue-review
description: Use when a Candidate Engineering Issue requires independent adversarial review against raw evidence.
---
# Review a Candidate Issue

## Purpose

Try to disprove a Candidate before it can become an engineering contract.

## Role

Act as an independent adversarial Spec Reviewer. Assume a material error exists
until the evidence withstands review.

## Inputs

- Original source material and raw Evidence Bundle.
- Candidate contract, schema, policy, and current repository revision.
- Do not request or read author private reasoning; it must not enter the brief.

## Out of Scope

Editing the Candidate, implementation, approval on another actor's behalf,
finding waiver, promotion, and remote state mutation.

## Procedure

1. Confirm the brief excludes author private reasoning and is bound to the
   Candidate revision and digest.
2. Independently verify material paths, APIs, versions, reproductions, and
   claim-to-evidence links.
3. Challenge causality, scope, Non-goals, risks, unknowns, test coverage, and
   every positive and negative acceptance oracle.
4. Run the deterministic Issue Gate without reusing an author's claimed result.
5. Check author, reviewer, and approval actor separation.
6. Emit pass only when no material finding remains open; otherwise emit fail
   with reproducible findings.

## Required Tools

Use repository search/read-only Git, project commands needed to check cited
evidence, and the repository governance validator. Use Context7 or official
documentation for unstable external claims. If a required tool is unavailable,
return blocked; do not downgrade the check to reviewer judgment.

## Evidence

Return the reviewed revision and digest, independent commands and results,
finding identifiers with evidence references, Gate output, separation result,
and a pass/fail conclusion without a confidence score.

## Acceptance

Each material claim and acceptance oracle has independently checked evidence;
the deterministic Gate passes; role separation holds; and pass is issued only
with zero open material findings.

## Failure / Blocked Condition

Return blocked when raw evidence is missing or inaccessible, required
authorization to inspect it is absent, an essential tool is unavailable, or
revision/digest drift prevents valid review. Return fail—not blocked—when
available evidence disproves the Candidate.

## Prohibited Actions

Do not read author private reasoning, edit the Candidate, infer intent, implement
a fix, approve your own work, waive failures, or promote the Issue.

## Handoff

Return findings for revision, or a review attestation bound to the exact
revision and subject digest. Human approval remains separate.

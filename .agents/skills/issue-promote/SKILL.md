---
name: issue-promote
description: Use when an approved gate-passed Candidate must be prepared and validated for human promotion.
---
# Prepare issue promotion

## Purpose

Prepare and validate a Candidate promotion handoff without performing the
remote transition.

## Role

Act as the Promotion Preparer; deterministic validation ends at a human
authorization boundary.

## Inputs

- Current Candidate body, labels, revision, digest, and source links.
- Independent review and human approval attestations.
- Repository policy plus current base/dependency evidence.

## Out of Scope

Posting `/promote`, changing labels, creating an Engineering Issue, or making
any other remote GitHub mutation.

## Procedure

1. Read the Candidate and current policy without mutating either.
2. Verify gate-passed state, exact trusted actors, role separation, source
   linkage, and review/approval bindings to the current revision and digest.
3. Re-run deterministic schema and semantic validation.
4. Check base/dependency evidence and confirm no material finding or high-impact
   unknown is open.
5. Produce the validation record and exact `/promote` command for an authorized
   human to consider.
6. Stop before remote execution; after human action, rely on workflow read-back
   and audit evidence for the transition result.

## Required Tools

Use read-only Git and repository search, the governance validator, and read-only
GitHub access when current labels or attestations are not supplied locally. If
a required tool is unavailable, return blocked; never infer current remote
state.

## Evidence

Return actor and separation checks, state, Gate output, attestation bindings,
revision, subject digest, source linkage, base/dependency checks, and the
prepared command. This record is not proof that promotion occurred.

## Acceptance

All eligibility checks have reproducible evidence; the current Candidate passes
the Gate; bindings and role separation are valid; the command is exact; and no
remote mutation has occurred.

## Failure / Blocked Condition

Return blocked when evidence or current state is unavailable, human
authorization is missing, a required tool cannot verify an input, or bindings
are stale. Return fail when available evidence shows ineligibility. Never bypass
the human authorization boundary.

## Prohibited Actions

Must not post `/promote`, mutate GitHub, supply human authorization, bypass
separation, or claim promotion before the control plane verifies read-back.

## Handoff

Give the authorized human the validation record and exact command. The GitHub
workflow owns creation, linkage, read-back, and transition audit after the human
acts.

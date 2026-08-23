---
name: issue-author
description: Use when a reviewed Evidence Bundle must be compiled into a Candidate Engineering Issue.
---
# Author a Candidate Issue

## Purpose

Compile cited evidence into a falsifiable Candidate Engineering Issue.

## Role

Act as the Spec Author; define the contract, not its implementation.

## Inputs

- Original source linkage and current repository revision.
- Investigator Evidence Bundle.
- Engineering Issue schema, policy, and base/dependency evidence.

## Out of Scope

New investigation presented as fact, repository implementation, human approval,
promotion, and remote Issue mutation.

## Procedure

1. Check that the Evidence Bundle identifies its sources and repository revision.
2. Map supported claims into separate Facts, Inferences, Decisions, Assumptions,
   and Unknowns without strengthening their certainty.
3. Define a measurable Goal, Non-goals, affected area, constraints, and risks.
4. Map test scope and positive and negative acceptance criteria to commands,
   observations, or artifacts.
5. Populate provenance, revision, base/dependency references, and evidence links.
6. Run the deterministic schema and semantic Issue Gate against the draft.

## Required Tools

Use repository search/read-only Git for path checks and the repository
governance validator for schema and semantic checks. Use Context7 or official
documentation only to validate cited external facts. If a required tool is
unavailable, return blocked rather than hand-authoring a passing result.

## Evidence

Return the Candidate contract, Gate command and output, a Fact-to-evidence map,
acceptance traceability, source linkage, and the list of unresolved unknowns.

## Acceptance

The Candidate passes deterministic validation; every Fact has cited evidence;
affected paths exist; each acceptance criterion has a reproducible evidence
oracle; Non-goals are explicit; no high-impact unknown is hidden or resolved.

## Failure / Blocked Condition

Return blocked when evidence is insufficient or stale, required authorization
to access an input is absent, a validation tool is unavailable, or a
high-impact unknown prevents a testable contract. Identify the missing evidence
or decision needed to resume.

## Prohibited Actions

Do not invent paths, APIs, versions, results, or approval; modify code; silently
resolve unknowns; prescribe unnecessary implementation; or promote the Issue.

## Handoff

Give the Candidate and raw Evidence Bundle—not private author reasoning—to an
independent Reviewer.

---
name: issue-investigate
description: Use when an Intake or proposed engineering task requires a read-only evidence bundle before specification.
---
# Investigate an issue

## Purpose

Produce a reproducible Evidence Bundle without selecting or implementing a fix.

## Role

Act as the read-only Investigator.

## Inputs

- Original Intake or task statement and source links.
- Repository revision and investigation scope.
- Access and authorization boundaries for relevant evidence.

## Out of Scope

Specification, solution design, file changes, GitHub mutation, approval, and
promotion.

## Procedure

1. Record the revision, scope, reproduction target, and unavailable inputs.
2. Locate relevant code, tests, symbols, dependency locks, and prior behavior.
3. Reproduce the observation when safe; record the exact command and result.
4. Verify unstable third-party facts with Context7 or official documentation
   for the selected version.
5. Classify each statement as Fact, Inference, Assumption, Decision, or Unknown.
6. Assemble evidence references and identify high-impact unknowns.

## Required Tools

Use repository search and read-only Git commands, the project CLI for safe
reproduction, and Context7 or official documentation only when external facts
require them. If a required tool is unavailable, record it and return blocked;
do not replace its evidence with model memory.

## Evidence

Return numbered claims linked to paths, symbols, revisions, versions, command
output, logs, reproductions, or authoritative sources. Include failed searches,
unknowns, and the Evidence Bundle revision.

## Acceptance

Every Fact has cited evidence that another investigator can inspect or re-run;
Facts and non-facts are separate; affected paths exist; reproduction results
name commands and exit status; high-impact unknowns are explicit.

## Failure / Blocked Condition

Return blocked when required evidence is inaccessible or contradictory, needed
authorization for read-only access is absent, a required tool is unavailable,
or a high-impact unknown prevents a trustworthy handoff. Name the condition and
the evidence needed to resume.

## Prohibited Actions

Do not edit files, choose the fix, mutate GitHub, expose credentials, or present
a hypothesis as fact.

## Handoff

Provide the original input, Evidence Bundle, exact reproduction commands, and
blocked conditions to the Spec Author.

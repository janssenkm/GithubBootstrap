# Engineering Governance V2

## Milestone evidence chain

Workflows 50–55 form a non-transitive evidence chain: capture, provisional
publication, review finalization, acceptance intent, atomic execution, and
receipt finalization. Later stages are `workflow_run`-driven, validate the exact
successful upstream run and unique canonical artifact through REST, and check
out only the validated upstream SHA. Repository-wide non-cancelling locks
serialize transitions. Closure is one state-plus-description PATCH containing
the preserved description and a hidden operation marker; response loss is
reconciled by read-back, never by reopening or inventing success.

## Purpose and V1 decisions

GithubBootstrap V2 is a multi-agent, locally executed, Issue-driven engineering
governance template. GitHub is the control plane: it records intake, contracts,
pull requests, quality gates, milestones, authorization, and audit history. The
maintainer's machine is the execution plane: OpenCode, Codex, Claude Code, or
another host investigates, plans, edits, tests, reviews, and uses `git` and
`gh` under human control.

GitHub Actions MUST NOT run a coding agent, create implementation branches, or
author patches. Actions may perform deterministic contract validation, policy
checks, CI, security scanning, and audit annotation. Local agents may propose
state changes, but evidence, policy, and an authorized human command are needed
to change an Issue's governed state.

V1 makes these choices rather than leaving them configurable:

- `AGENTS.md` remains the repository-wide constitution; host adapters and
  Skills may load narrower procedures without defining competing policy.
- Intake, Candidate, and Engineering Issue are distinct GitHub issues. An
  external Intake is never relabelled as ready and never directly drives work.
- Promotion creates a new Engineering Issue and preserves immutable references
  to its Intake and Candidate sources.
- Trusted actors are exact GitHub login names in a repository-owned allowlist.
  `author_association` is informational and never sufficient authorization.
- The machine contract is one fenced JSON value in the Issue body. Narrative
  text is explicitly outside the contract and outside its digests.
- A normative `subject_digest` binds review and approval to task meaning; a
  separate full-body `contract_hash` detects contract tampering and staleness.
  Both use RFC 8785 and SHA-256.
- GitHub lifecycle automation stops at governance and validation; coding stays
  local.

## Entities, states, and transitions

The three entities answer different questions:

| Entity | Question | States |
| --- | --- | --- |
| Intake Issue | What was reported or proposed? | `new`, `triaged`, `investigating`, `closed` |
| Candidate Issue | What does the evidence support? | `draft`, `gate-failed`, `gate-passed`, `promoted`, `closed` |
| Engineering Issue | What work has been authorized? | `contracted`, `ready`, `in-progress`, `done`, `cancelled` |

Allowed transitions are:

```text
INTAKE new -> triaged -> investigating -> closed
                          |
                          +-> CANDIDATE draft -> gate-failed -> draft
                                                |
                             draft -> gate-passed -> promoted
                                                   |
                                                   +-> ENGINEERING contracted
ENGINEERING contracted -> ready -> in-progress -> done
           contracted/ready/in-progress -> cancelled
```

The Candidate workflow states are GitHub labels/audit states; while the
machine artifact is attached to a Candidate, its contract `status` is
`candidate`. The contract status changes to `contracted` only in the newly
created Engineering Issue. Engineering states and contract `status` then move
together.

`triaged -> closed`, `draft -> closed`, and repeated discovery from
`investigating -> draft` are also allowed. No Intake or Candidate transition
targets `ready`. Reopening a promoted Candidate does not mutate its Engineering
Issue; it begins a new Candidate revision and, if material, a replacement
promotion.

### Promotion

On a Candidate Issue, an allowlisted human posts an exact `/promote` command on
its own line. The control plane MUST:

1. reject pull-request comments and comments on any entity other than a
   `gate-passed` Candidate;
2. re-run deterministic schema and semantic gates against the current body;
3. require an independent adversarial review result of `pass` and a human
   approval of `approved`, both attesting the Candidate's current
   `issue_revision` and `subject_digest`;
4. create a new Engineering Issue, never convert the Intake or Candidate;
5. copy the normative payload without modification, retain its approved
   `issue_revision`, add promotion provenance, set `status` to `contracted`,
   and compute a new full `contract_hash`;
6. link Intake -> Candidate -> Engineering in both machine provenance and
   human-readable comments; and
7. mark the Candidate `promoted` only after the new issue is read back and its
   hash is verified.

Failure before step 7 leaves the Candidate unchanged. If issue creation
succeeds but linkage fails, recovery reads the audit marker containing the
Candidate number and hash, links that existing Engineering Issue, and does not
create a duplicate.

### Ready for development

On an Engineering Issue, an allowlisted human posts an exact `/ready-for-dev`
line. It is accepted only when the entity is `contracted`, the current contract
passes all gates, no high-impact Unknown remains open, `base_commit` still
exists, dependency and document locks remain available, and the recomputed
`subject_digest` and `issue_revision` equal both review and approval
attestations. The current full `contract_hash` must also equal `freeze`. The
transition changes only lifecycle `status`, does not increment
`issue_revision`, and recomputes `contract_hash`. Local workers bind to both
digests and the revision.

The implementation must distinguish an Issue comment from a pull-request
comment: GitHub's `issue_comment` event covers both, and a payload containing a
`pull_request` object is rejected. Commands in quoted text, edited prose,
substrings, or code fences do not count. A PR may report the Engineering Issue
number and contract hash, but `/promote` and `/ready-for-dev` on a PR do nothing.

## Authorization policy

V1 will add a repository policy file with three explicit arrays of normalized,
case-insensitive GitHub logins:

```yaml
trusted_issue_authors:
  - maintainer-login
trusted_developers:
  - maintainer-login
trusted_reviewers:
  - maintainer-login
```

The future implementation location is `.github/project-policy.yml`. The V1
authorization matrix is exact and non-transitive:

| Action | Required allowlist | Separation |
| --- | --- | --- |
| independent specification review | `trusted_reviewers` | reviewer differs from Candidate author and approval actor |
| human contract approval | `trusted_issue_authors` | approval actor differs from reviewer |
| `/promote` | `trusted_issue_authors` | command actor is recorded as promoter |
| `/ready-for-dev` | `trusted_developers` | command actor is recorded in the transition audit |

Membership in one list grants no capability from another. Empty or missing
lists fail closed. Both commands are accepted only as Issue comments and are
rejected on PR comments. GitHub teams and an API-backed policy store are future
extensions, not V1 fallbacks. `OWNER`, `MEMBER`, or `COLLABORATOR` association
alone never grants authority.

## Contract representation, attestations, and freeze

An Engineering or Candidate Issue body contains a narrative section followed
by exactly one contract block:

````markdown
Human-readable motivation and context.

<!-- engineering-contract:start -->
```json
{ "schema_version": "1.0.0" }
```
<!-- engineering-contract:end -->
````

The markers must appear once, in order. Between them there must be one and only
one fenced `json` block containing one JSON object and no other non-whitespace
content. Only that parsed object is contractual. Titles, labels, comments, and
narrative outside the markers are audit/user-interface data and do not affect
either digest.

### Normative subject digest

`subject_digest` is lowercase
`sha256:<64 hex>` over the UTF-8 RFC 8785/JCS serialization of a normative
projection. The projection is a new object containing these top-level members,
with exactly these names, in this set:

```text
schema_version
issue_revision
base_commit
claims
evidence
goal
non_goals
affected_areas
constraints
implementation_boundaries
requirements
unknowns
risks
dependency_locks
document_locks
```

Every nested member and array element below those included members is included
exactly as stored; no nested evidence, Claim, AC, verification, Unknown, risk,
or lock field is stripped. The projection excludes the complete top-level
members `kind`, `status`, `provenance`, `review`, `approval`, and `freeze`.
Those are lifecycle, promotion/audit metadata, attestations, or derived data,
not task meaning. The whitelist is normative: future top-level fields are not
implicitly included and require a schema-version decision.

An independent review with result `pass` stores the current projection's
`issue_revision` and digest in `review.subject_revision` and
`review.subject_digest`. Human approval does the same in
`approval.subject_revision` and `approval.subject_digest`. These attestations
are excluded from their own subject. Cross-field equality cannot be expressed
reliably in JSON Schema, so the deterministic semantic validator recomputes the
projection and requires both attestation pairs to equal it.

The Candidate locks `base_commit`, dependency versions, and document versions
before review or approval. Promotion copies the same normative projection to a
new Issue; only excluded lifecycle and provenance data change, so
`subject_digest` remains identical. If promotion would change any included
member, promotion fails and the change returns to the Candidate for a new
revision, review, and approval.

`issue_revision` starts at 1 and increments only when an included normative
member changes. Because revision is itself included, the increment is part of
the new subject. Pure `status`, promotion/audit, review, approval, or freeze
changes do not increment it. `base_commit` is exact lowercase 40-hex.
Dependency locks record package version and repository source; document locks
record provider, locator, version/date, and a content hash where capturable.

### Full contract hash

`contract_hash` detects any change to the extracted contract, including
lifecycle, provenance, review, and approval. To compute it:

1. parse the JSON and reject duplicate object keys;
2. validate the source/current contract and all non-derived target members
   against schema `1.0.0`, with Draft 2020-12 `date-time` format checking, and
   run semantic gates; the in-construction target need not pass the final
   non-null hash condition yet;
3. choose and store final `freeze.hash_algorithm`, `freeze.frozen_at`, and
   `freeze.frozen_by` values;
4. make a deep copy of the complete contract and remove only
   `freeze.contract_hash` from that copy;
5. serialize the copy with RFC 8785/JCS, hash its UTF-8 bytes with SHA-256, and
   store lowercase `sha256:<64 hex>` in `freeze.contract_hash`; and
6. validate the completed target, recompute using the same single-field
   exclusion, and read back.

The only exclusion is `freeze.contract_hash` itself, so the algorithm has no
self-reference. `frozen_at` and `frozen_by` are inputs fixed before hashing.
Every lifecycle or attestation change recomputes `contract_hash` but does not
change the normative digest or revision. Narrative-only edits affect neither.

A worker records `(engineering issue, issue_revision, subject_digest,
contract_hash, base_commit)` before planning. Any mismatch at implementation,
review, PR, or merge makes work stale. A normative change refreshes evidence,
increments revision, reruns review/approval, and computes both digests. A pure
lifecycle change retains revision and attestations but recomputes the full
hash. Digests are never manually copied forward.

## Claims, evidence, and ambiguity

Contract statements use these meanings:

- `fact`: directly supported by current code, command output, runtime evidence,
  Git history, or version-locked documentation. It requires at least one valid
  evidence reference.
- `inference`: a conclusion drawn from evidence. It may guide discovery but is
  not silently promoted to fact.
- `decision`: an authorized engineering choice. It requires evidence whose type
  is `human-decision` and whose actor is authorized by policy.
- Unknowns are separate records because absence of knowledge is not a claim.
  Each is `low` or `high` impact and `open` or `resolved`.

Facts without evidence are invalid. Evidence identifiers must resolve to a
unique record. Every Claim -> Evidence link must exist, and a Decision must
resolve at least one link to `human-decision`. High-impact open Unknowns block
promotion and readiness. A low-impact open Unknown may remain only with a
non-empty `containment` procedure and one or more `risk_refs`; each reference
must resolve to a declared risk whose mitigation makes the containment
actionable. Resolving an Unknown requires at least one resolution evidence
reference.

The contract also fixes one goal, explicit non-goals, affected paths/symbols,
constraints, implementation boundaries, and risks with mitigations. Affected
symbols are verified against repository code or symbol tooling; a guessed path
or API is not accepted.

## Requirements and verification

Every requirement owns its acceptance criteria. Each requirement must contain
at least one `positive` criterion describing what must occur and at least one
`negative` criterion describing what must not occur. Every criterion has a
falsifiable verification:

- `command` supplies the exact project command and expected result; or
- `observation` supplies an objectively inspectable observation and expected
  result when a command is not appropriate.

Vague statements such as "quality is good" are rejected. Commands are not run
from untrusted Issue text by GitHub Actions. A local verifier executes approved
commands in the checked-out project; CI runs repository-owned commands. The
Gate checks that declared commands are syntactically present and allowed by
project policy, while their successful output becomes completion evidence.

## Issue Quality Gate

The Gate has three layers and reports only `PASS` or `FAIL` with findings:

| Layer | Responsibility | Examples |
| --- | --- | --- |
| Schema checks | Draft 2020-12 validator | types, enums, required attestation fields, SHA shape, Fact refs non-empty, both AC polarities, high-impact Unknown blocking, low/open containment shape, no open finding when review is `pass`, resolution refs required for resolved findings and forbidden for open findings |
| Semantic checks | Repository-owned deterministic validator | unique IDs, reference resolution/types including Unknown `risk_refs` and finding `resolution_evidence_refs`, source chain, exact actor allowlists/separation, base commit existence, lock availability, normative projection, attestation revision/digest equality, full hash recomputation |
| Adversarial reviewer and human judgment | Independent reviewer plus authorized maintainer | whether cited resolution evidence actually resolves its finding, scope value, normative risk decisions, design choices, promotion and readiness authorization |

The schema directly enforces a Fact's non-empty `evidence_refs`, at least one
positive and negative criterion per requirement, valid verification shape, and
the absence of high-impact open Unknowns in `contracted`, `ready`,
`in-progress`, and `done`; low/open Unknown containment and risk references are
also structurally required. A review cannot be `pass` unless every finding,
regardless of severity, is `resolved`. Finding disposition has only `open` and
`resolved`; V1 has no finding risk-acceptance state. A resolved finding requires
one or more unique `resolution_evidence_refs`, while an open finding is
fail-closed and must omit that field. V1's maximum finding severity is `high`;
introducing `critical` requires a schema-version change and receives the same
resolved-only rule.
JSON Schema cannot reliably express cross-array joins. The semantic validator
therefore enforces that references resolve, Decision evidence includes a
`human-decision`, reviewer/approval/command actors satisfy the exact matrix and
separation rules, IDs are globally unique, resolved Unknowns have evidence,
low/open Unknown `risk_refs` resolve, both review and approval attest the current
normative revision/digest, and the stored full contract hash matches. For every
resolved finding it also resolves each `resolution_evidence_refs` entry against
the top-level `evidence` array; because the refs are nested in that finding,
this is the deterministic binding to the current finding. Determining whether
the referenced evidence is substantively sufficient remains an adversarial
reviewer and human judgment, not a string-reference check.

Low-impact uncertainty that is safe to carry into work is represented only as
a normative Unknown with `impact: low`, `resolution: open`, non-empty
`containment`, and valid `risk_refs`. It is never represented by accepting a
review finding.

The adversarial reviewer receives the raw source evidence and Candidate
artifact, but not author reasoning, confidence, self-review, suspected defect,
or proposed fix. Its instruction assumes at least one material error and tries
to falsify facts, scope, API claims, and tests. `role != model`: Investigator,
Spec Author, Spec Reviewer, Planner, Worker, Verifier, Reviewer, and
Orchestrator are contracts that any supported model/host may fulfill. No model
vote replaces deterministic evidence or human authorization.

## Tool contract

- Repository code and version manifests are primary for current project facts.
- For a third-party API signature, configuration schema, deprecation,
  version-specific behavior, security setting, serialization, driver semantic,
  or framework lifecycle not established by the repository, use Context7 for
  the locked version when available and confirm with official documentation
  when security or compatibility is material. Model memory only navigates or
  proposes hypotheses.
- LSP may locate symbols and provide rapid diagnostics. It is auxiliary
  evidence, never final verification.
- Repository-owned build, test, lint, typecheck, and security CLI commands are
  final local evidence; CI is the merge gate.
- Agents load the repository-prescribed Skill for their role and remain within
  the current Engineering Issue boundaries.
- Local `git`/`gh` operations retain the human attribution and confirmation
  boundaries in `AGENTS.md` and `BOOTSTRAP.md`.

No design here promises that every host exposes Context7 or LSP identically. If
a required capability is unavailable, the agent records the missing evidence
and the Gate fails; it does not substitute memory.

## Schema lifecycle

The authoritative schema is
`.github/schemas/engineering-issue.schema.json`, Draft 2020-12. Contracts use a
SemVer `schema_version`, and V1 accepts exactly `1.0.0`. Patch releases may
tighten documentation or validators without changing accepted JSON. A backward
compatible field addition requires a new minor schema and an explicit
migration tool because `additionalProperties: false` intentionally fails
unknown fields. A breaking rename, semantic change, or removal requires a new
major version.

Validators select a checked-in schema by exact version; they never fetch a
floating remote schema. Existing frozen contracts remain validated with their
original schema. Migration creates a new revision, records old and new hashes
in the audit comment, reruns review/approval, and never rewrites historical
comments. Because `schema_version` is normative, every schema migration changes
the projection and increments the revision. V1 has no open extension maps:
every object uses
`additionalProperties: false` so misspellings fail closed.

## Audit, failures, and recovery

Audit comments are append-only and include event type, actor, UTC time, source
and target issue numbers, revision, base commit, schema version,
`subject_digest`, `contract_hash`, Gate result, and finding IDs. They contain no
secrets or untrusted command output.
GitHub's event actor is the authoritative command actor; a username inside JSON
is only checked against that event and policy.

| Failure | Result | Safe recovery |
| --- | --- | --- |
| malformed/multiple contract blocks | `FAIL` | correct Candidate body, increment revision only if the correction changes the normative projection, rerun Gate |
| schema or semantic finding | `FAIL` | add evidence or correct the claim; never waive automatically |
| unauthorized command | no transition | append rejection audit without disclosing policy beyond actor/result |
| stale subject/revision/base/lock | no transition | refresh normative evidence, increment revision, review, approval, and both digests |
| stale full contract hash only | no transition | identify unauthorized/lifecycle change and recompute only through an authorized transition |
| Engineering Issue created but response lost | Candidate stays unpromoted | find target by source/digest audit marker and link it idempotently |
| CI unavailable | not complete | preserve evidence and rerun; inability to verify is not success |
| any open reviewer finding | `FAIL` | fix the issue or modify the normative contract, attach resolution evidence, then rerun independent review and approval; V1 has no finding risk-acceptance shortcut |

## Minimal valid Candidate contract

The following is schema-valid. It remains a Candidate because review, approval,
and freeze have not occurred. Its Decision is already tied to a human-decision
evidence record; the semantic Gate will also verify the actor and every
reference.

<!-- engineering-contract:start -->
```json
{
  "schema_version": "1.0.0",
  "kind": "engineering-issue-contract",
  "status": "candidate",
  "provenance": {
    "created_by": "maintainer-login",
    "promoted_by": null,
    "sources": [
      {
        "repository": "janssenkm/GithubBootstrap",
        "number": 81,
        "role": "intake"
      }
    ]
  },
  "issue_revision": 1,
  "base_commit": "0123456789abcdef0123456789abcdef01234567",
  "claims": [
    {
      "id": "F-01",
      "type": "fact",
      "statement": "The affected path exists at the locked base commit.",
      "evidence_refs": ["E-01"]
    },
    {
      "id": "D-01",
      "type": "decision",
      "statement": "Public behavior outside the affected path remains unchanged.",
      "evidence_refs": ["E-02"]
    }
  ],
  "evidence": [
    {
      "id": "E-01",
      "type": "repository-file",
      "locator": "README.md@0123456789abcdef0123456789abcdef01234567",
      "summary": "Repository file inspected at the locked base commit.",
      "captured_at": "2026-08-22T00:00:00Z",
      "content_sha256": null
    },
    {
      "id": "E-02",
      "type": "human-decision",
      "locator": "issue:81#issuecomment-1",
      "summary": "Maintainer approved the compatibility boundary.",
      "captured_at": "2026-08-22T00:01:00Z",
      "content_sha256": null
    }
  ],
  "goal": "Make the documented behavior verifiable without changing unrelated behavior.",
  "non_goals": [
    "Refactor unrelated repository areas."
  ],
  "affected_areas": [
    {
      "path": "README.md",
      "symbol": null,
      "reason": "The source evidence and behavior description are located here."
    }
  ],
  "constraints": [
    "Preserve public behavior outside the stated requirement."
  ],
  "implementation_boundaries": [
    "Only files traced to this Engineering Issue may change."
  ],
  "requirements": [
    {
      "id": "R-01",
      "statement": "The governed behavior is documented and testable.",
      "acceptance_criteria": [
        {
          "id": "AC-01",
          "polarity": "positive",
          "statement": "The required behavior is present.",
          "verification": {
            "type": "command",
            "run": "test -f README.md",
            "expected_result": "The command exits with status 0."
          }
        },
        {
          "id": "AC-02",
          "polarity": "negative",
          "statement": "No unrelated file is changed.",
          "verification": {
            "type": "observation",
            "observe": "Review the pull-request file list against affected_areas.",
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
      "description": "The example base commit is illustrative, not a live repository claim.",
      "mitigation": "The semantic Gate verifies the actual commit before promotion."
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

## V1 implementation map

This document and schema are design assets only. A later, separately reviewed
implementation maps the current repository as follows:

| Current asset | V1 target change |
| --- | --- |
| `AGENTS.md` | concise constitution and index; preserve mandatory common rules |
| `CLAUDE.md` (absent) | thin adapter importing common rules |
| `.agent/` (absent) | context, tool, verification, issue, and security policies |
| `.agents/skills/` (absent) | `issue-investigate`, `issue-author`, `issue-review`, and `issue-promote` Skills first |
| issue templates | separate Intake and Candidate/Engineering forms |
| `.github/project-policy.yml` (absent) | exact trusted-login allowlists |
| `01-ai-development-workflow.yml` | remove coding/branch behavior; validate local-execution readiness only |
| new governance workflow/scripts | parse contract, validate schema/semantics, promote idempotently, audit transitions |
| `.github/SETTINGS.md` | document new labels and least-privilege policy after implementation exists |
| `.github/WORKFLOWS.md` | describe observed final workflow names/checks after implementation |

The current workflow, templates, rules, and settings are intentionally not
changed in this design stage.

## V1 out of scope

- running any coding model in GitHub Actions;
- automatic implementation, branch creation, commits, PRs, or merges;
- majority-vote model review or numeric AI confidence;
- GitHub team expansion, organization directory lookup, or external policy API;
- executing Issue-provided shell text in Actions;
- prescribing one model for any role or identical host internals;
- automatic migration of pre-V1 Issues;
- waiving high-impact Unknowns or deterministic Gate failures;
- any finding risk-acceptance workflow. If a maintainer changes the task to
  eliminate or explicitly accept a risk, that decision must be a normative
  Decision claim and/or constraint, increment `issue_revision`, change
  `subject_digest`, and receive new independent review and approval; finding
  disposition still remains `open` until resolution evidence supports changing
  it to `resolved`;
- release, deployment, milestone redesign, or project-specific build commands.

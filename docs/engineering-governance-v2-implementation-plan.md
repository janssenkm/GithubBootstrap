# Engineering Governance V2: V1 Implementation Plan

## Milestone V2 execution slice

The milestone slice uses workflows 50–55: read-only review capture,
provisional publication, review finalization, read-only acceptance intent,
atomic execution, and receipt finalization. `workflow_run` stages resolve the
source run and unique artifact through REST before checkout; checkout uses only
the resolved 40-hex SHA. Stage artifacts are canonical single-member ZIP
payloads and operation identities exclude rerun attempts. Closure is one
state-plus-description PATCH preserving the human description with a hidden
operation marker. Lost or conflicting responses pause; no stage reopens a
milestone or claims completion before authoritative read-back.

## Status and authority

This is an implementation-preparation artifact, not an implementation. It fixes
the V1 scope, dependency order, file manifest, verification, rollout, and
rollback plan for the accepted design in
[`engineering-governance-v2.md`](engineering-governance-v2.md) and the accepted
Draft 2020-12 schema in
[`engineering-issue.schema.json`](../.github/schemas/engineering-issue.schema.json).
If this plan conflicts with either accepted artifact, implementation stops and
the conflict is resolved by a separately reviewed design change; an executor
does not silently reinterpret the contract.

The untracked root document
`GithubBootstrap_V2_Implementation_and_Documentation_Plan.md` is not V1
authority, is not a V1 manifest input, and is not modified by this plan. Its
ideas may be triaged later as backlog input, but they cannot override or extend
the accepted design, accepted schema, or this implementation sequence. Any
conflict is rejected unless it first becomes an explicitly approved change to
the authoritative design/schema.

The repository is the template source, not a generated project. Evidence:
`origin` is `https://github.com/janssenkm/GithubBootstrap.git`, the current
branch is `main`, and `BOOTSTRAP.md` names `janssenkm/GithubBootstrap` as the
template source. Local writes follow the template-source path in
`BOOTSTRAP.md`; every GitHub remote write remains a separate human-confirmed
batch.

## V1 success definition

V1 succeeds when all of the following are observable:

1. Intake, Candidate, and Engineering Issues are distinct artifacts with only
   the state transitions defined by the accepted design.
2. A strict extractor, Draft 2020-12 schema validator, semantic validator, and
   complete RFC 8785/JCS implementation deterministically validate the same
   contract locally and in CI.
3. Every governed transition is authorized by exact username allowlists,
   event-backed actor identity, role separation, exact commands, and current
   contract evidence.
4. `/promote` creates a new Engineering Issue, preserves the normative subject,
   verifies the new issue, and recovers idempotently after partial failure.
5. `/ready-for-dev` only changes a valid contracted Engineering Issue to ready,
   recomputes the full hash, and emits a local-execution handoff. It never runs
   a coding agent, creates or pushes a development branch, commits, opens a PR,
   or merges.
6. Workers and PR checks bind to issue number, `issue_revision`,
   `subject_digest`, `contract_hash`, and `base_commit`; drift fails closed as
   stale.
7. The four upstream Skills and host adapters expose one logical engineering
   contract without duplicating policy.
8. Repository-owned unit, schema, semantic, parser, hash, authorization, state,
   mocked API, workflow-static, and end-to-end dry-run tests pass from a clean
   checkout with locked dependencies.
9. Required checks are made mandatory only after their exact names have been
   observed succeeding on GitHub and a separate human authorizes the ruleset
   write.

GitHub is the control plane and the maintainer's machine is the execution
plane. **GitHub Actions MUST NOT run a coding agent or automatically push any
development branch.**

### V1 out of scope

- coding-model execution, automatic implementation, branch creation, commits,
  pushes, PR creation, review approval, merge, release, or deployment in
  Actions;
- majority-vote model review, numeric AI confidence, or model memory as factual
  evidence;
- GitHub teams, organization directory lookup, an external authorization
  service, or authorization through `author_association`;
- automatic migration or destructive rewriting of pre-V1 Issues;
- executing verification commands copied from Issue bodies in Actions;
- waivers for deterministic Gate failures, high-impact open Unknowns, or open
  review findings;
- finding risk acceptance, milestone redesign, or project-specific build and
  release policy;
- identical Context7/LSP internals across hosts;
- remote labels, repository settings, workflows, or rulesets in the local
  implementation batch.

## Observed baseline and migration constraints

The local baseline at planning time is commit
`0c934ebff5f442e5619136aaf95a106b7a677acd`. The accepted design, schema, and
`.gitignore` are pre-existing untracked user assets and must be preserved:

```text
?? .github/schemas/engineering-issue.schema.json
?? .gitignore
?? docs/engineering-governance-v2.md
```

The separately created untracked root proposal
`GithubBootstrap_V2_Implementation_and_Documentation_Plan.md` is also preserved
without modification, but is excluded from V1 authority and manifests as stated
above.

Current repository behavior that V1 must deliberately replace includes:

- `.github/workflows/01-ai-development-workflow.yml` grants `contents: write`,
  treats `OWNER`/`MEMBER`/`COLLABORATOR` as authorization, matches command
  substrings, configures `AI Developer <dev@bot.local>`, creates a timestamped
  `dev/issue-*` branch, and pushes it;
- `.github/workflows/00-issue-ai-triage.yml` sends untrusted Intake text to an
  optional model and presents generated acceptance criteria as triage output;
- the three issue templates do not distinguish Intake from an engineering
  contract;
- `CONTRIBUTING.md` and `.github/WORKFLOWS.md` direct contributors to the
  branch-creating `/ready-for-dev` workflow;
- required-file lists are duplicated in three workflows and do not include the
  accepted governance assets;
- `.github/settings.yml` recommends legacy labels and describes automated
  development branches.

Read-only remote inventory observed on 2026-08-22 found only GitHub's default
labels, no milestones, no pull requests, and no ruleset. The governance-related
workflow has not produced a successful observed run. These are observations,
not authority to change the remote. No ruleset may name a governance check
until GitHub has observed that exact check successfully.

## Human inputs and fail-closed defaults

| Input | Safe default before confirmation | Unlock condition |
| --- | --- | --- |
| `trusted_issue_authors` | `janssenkm` is an observed owner and only a **candidate**; an empty list is used in executable policy until confirmed | Maintainer confirms exact, case-insensitive GitHub login list |
| `trusted_developers` | `janssenkm` is only a candidate; empty means no ready transition | Maintainer confirms exact login list |
| `trusted_reviewers` | empty; no review can pass and no Candidate can promote | Maintainer supplies at least one explicit login that can differ from Candidate author and approval actor |
| actor separation | always enforced; one account cannot fill all roles | A distinct eligible reviewer exists for the concrete Candidate |
| verification command allowlist | empty except repository-owned validation commands added and tested in the same PR | Maintainer approves any project-specific exact commands during generated-project bootstrap |
| Python and package versions | no guessed version and no unlocked install | Network-enabled dependency-resolution spike records exact versions, hashes, licenses, provenance, and test results |
| Action references | no tag-only or invented SHA | Resolve the intended release tag to its full commit SHA from the action's official repository and review the diff/release notes |
| Claude shared-Skill adapter | no promise that `.agents/skills` or symlinked Skill folders work | Compatibility spike on the supported Claude Code release proves discovery/loading, or a generated-copy adapter plus drift test is selected |
| OpenCode/Codex extra config | native documented `AGENTS.md` and Skill paths only; no speculative config file | Compatibility spike proves a repo-local adapter is recognized and needed |
| remote labels/settings/ruleset | no remote write | Separate preview names the repository, exact mutation, rollback, and receives human confirmation |
| enforcement mode | state-changing event automation disabled; local dry-run only | Local tests pass, shadow evidence is reviewed, and maintainer confirms warn/enforce stage |

Implementation cannot honestly reach promotion readiness without a distinct
reviewer. This is a deliberate blocked state, not a reason to weaken separation.

## Runtime, canonicalization, and supply chain

### Technical choice

V1 uses a small Python command-line package under `.github/scripts/governance/`.
Python is already a documented bootstrap prerequisite and supports all required
fail-closed parsing without a JavaScript action wrapper:

- `json.loads` with an `object_pairs_hook` rejects duplicate object keys;
- `parse_constant` rejects `NaN`, `Infinity`, and `-Infinity`;
- the selected `jsonschema` release supplies a Draft 2020-12 validator and an
  explicit `FormatChecker` for `date-time`;
- the selected `rfc8785` package must implement RFC 8785 number and Unicode
  serialization and pass the RFC's official vectors.

`json.dumps(sort_keys=True)` is forbidden as a canonicalizer: it does not
implement complete RFC 8785/JCS semantics. If the selected RFC 8785 package
fails any official vector, the dependency spike fails and implementation does
not substitute a home-grown `sort_keys` approximation.

Core logic is GitHub-independent. Thin workflow entry points translate event
JSON and API responses into calls to tested parser, policy, state, hash, and
audit modules. Tests use the standard library plus locked test dependencies;
production does not download schemas or execute Issue-provided shell strings.

### Reproducibility and dependency-resolution spike

Within Slice 1, before its lockfiles are accepted, perform this network-enabled,
read-only-to-upstream/write-local-lockfiles spike in that reviewed PR:

1. Check the current CPython support table and the `ubuntu-latest` runner tool
   cache; select an exact supported patch release and record it in
   `.python-version` and workflow setup. Do not use `3.x` or an unbounded
   `python-version`.
2. Query the official PyPI JSON metadata and upstream repositories for
   `jsonschema`, its format extras/dependencies, `rfc8785`, `PyYAML`, and the
   chosen test runner. Reject yanked releases, unexpected ownership/source,
   incompatible licenses, and releases without an auditable source tag.
3. In a clean temporary virtual environment, resolve exact versions from
   `.github/governance/requirements.in` and generate
   `.github/governance/requirements.txt` with hashes for every transitive wheel
   and sdist. Installation uses
   `python -m pip install --require-hashes -r .github/governance/requirements.txt`.
4. Run official RFC 8785 examples and Appendix B number vectors plus repository
   Unicode, control-character, large-number, and duplicate-key vectors against
   the chosen canonicalizer. Record only reproducible test fixtures, not a
   trust statement.
5. Resolve each intended `actions/checkout`, `actions/setup-python`, and
   `actions/github-script` release tag through the official Git repository,
   including the peeled commit for annotated tags. Review release notes and
   changed permissions, then record the full 40-hex commit in workflows. Never
   fabricate a hash or copy one from an unrelated repository.
6. Resolve a maintained `actionlint` release from its official repository,
   record the exact version and upstream-published artifact checksum, and
   independently verify the downloaded artifact before use. If upstream does
   not publish an authenticated checksum, compute and review a repository-owned
   checksum from the approved artifact once, commit it, and require it
   thereafter. A floating download or unchecked executable is forbidden.
7. Run `pip-audit` (locked as a development dependency), license review,
   checksum-pinned `actionlint`, and repository static security checks. A known
   unmitigated critical/high advisory blocks the slice.

The committed input file documents direct intent; the hash-locked output is the
only CI install source. Dependabot may update the GitHub Actions references and
Python ecosystem only through a PR that regenerates hashes and reruns all
vectors. Actions always use full commit SHAs. Runtime network access is limited
to GitHub API calls made by a state workflow; validation never fetches code,
schemas, Context7 pages, or package metadata.

Slice 1 adds `.github/scripts/test-governance.sh` as the single clean-checkout
test entry point. It creates a task-specific directory with
`mktemp -d /tmp/github-governance-tests.XXXXXX`, installs the exact Python patch
runtime's hash-locked dependencies into a venv inside that directory, verifies
the locked `actionlint` checksum, runs `actionlint` and the complete governance
suite, and removes the directory with a trap. It never installs into the user
environment, writes a persistent venv, or depends on the user's `.gitignore`.
By default it may use the network to obtain locked artifacts. Offline use must
set documented `GOVERNANCE_WHEELHOUSE` and `GOVERNANCE_TOOL_CACHE` paths whose
contents still pass requirements hashes and the actionlint checksum; a missing
artifact fails rather than falling back online in offline mode. CI starts from
a fresh runner, may populate keyed caches only as an optimization, and always
reruns every hash/checksum verification. No slice assumes a prior runner or
persistent shell environment.
With no arguments it runs the complete suite. Its only additional interfaces
are `--pytest <path>...` for a named fixture subset and
`--governance <subcommand>...` for the installed governance CLI; both still
create the temporary venv, verify all artifacts, run actionlint, and clean up.
Slice-specific commands below use those interfaces and never invoke a presumed
global `pytest` or leave `PYTHONPATH` state behind.

## Command-line and extractor contract

The single entry point is:

```text
python -m github_governance <command> [options]
```

It provides `extract`, `validate`, `digest`, `gate`, `authorize-event`,
`transition`, `pr-binding`, and `render-audit`. Machine output is UTF-8 JSON on
stdout; diagnostics are UTF-8 on stderr. Stable exit codes are:

| Code | Meaning |
| ---: | --- |
| 0 | requested check or dry-run passed; no hidden warning |
| 1 | contract, schema, semantic, Gate, authorization, or stale check failed |
| 2 | CLI usage error or unreadable/invalid input encoding/shape |
| 3 | lifecycle conflict, duplicate/replayed event, or non-idempotent target conflict |
| 4 | transient GitHub API/read-back failure; safe retry may be attempted |
| 5 | missing, invalid, or unsafe repository policy/dependency configuration |

No command emits a secret, raw untrusted log, or complete event payload.

The extractor accepts at most 262,144 UTF-8 bytes for the complete body and
131,072 UTF-8 bytes for the fenced JSON payload. It uses a fatal UTF-8 decoder
and fails closed on invalid UTF-8, NUL, forbidden Unicode scalar values, or
limit excess. It requires:

1. exactly one literal `<!-- engineering-contract:start -->` and one literal
   `<!-- engineering-contract:end -->`;
2. the start marker before the end marker, with no overlap, nesting, duplicate,
   escaped copy, or residual contract marker anywhere else;
3. between the markers, exactly one triple-backtick fence opened as lowercase
   `json`, followed by one JSON object and a closing triple-backtick fence;
4. only whitespace outside that fence but inside the markers;
5. no second fence, nested fence, trailing token, comment, JSON scalar/array,
   duplicate object key at any depth, non-finite number, or parse residual.

Narrative before or after the unique marker pair is retained for rendering but
never enters `subject_digest` or `contract_hash`. The parser returns byte
offsets and the exact extracted object so a transition can replace only the
contract block after a pre-mutation body/hash recheck. CRLF and LF bodies that
parse to the same object produce identical digests. Tests cover duplicated and
reversed markers, marker text inside fences, nested/residual fences, blank
payload, multiple JSON values, duplicate nested keys, invalid UTF-8, NUL,
over-limit body and contract, deep nesting, trailing garbage, and narrative-
only edits.

## Digest, revision, and semantic invariants

`subject_digest` is SHA-256 over RFC 8785 bytes of the exact top-level
whitelist in the accepted design, including every nested member. The projection
excludes only `kind`, `status`, `provenance`, `review`, `approval`, and `freeze`.
`contract_hash` is SHA-256 over RFC 8785 bytes of the complete contract after
removing only `freeze.contract_hash`; final `hash_algorithm`, `frozen_at`, and
`frozen_by` are inputs before hashing.

Golden vectors must prove:

- object-key order and insignificant source whitespace do not change a digest;
- array order, a nested evidence field, or any normative member does change the
  subject digest;
- narrative-only, lifecycle, provenance, review, approval, and freeze changes
  do not change the subject digest;
- lifecycle, provenance, review, approval, freeze time/actor, or any normative
  change does change the full contract hash;
- only a normative change increments `issue_revision`; the increment itself is
  hashed;
- promotion keeps revision and subject digest but changes status, provenance,
  freeze fields, and full contract hash;
- ready keeps revision and attestations, changes status/freeze, and recomputes
  only the full hash;
- an attestation to an earlier revision/digest, manual hash carry-forward, or
  wrong-case hash fails.

Semantic validation additionally enforces global ID uniqueness; every Claim,
Unknown, risk, review, approval, finding, and evidence cross-reference; Fact
evidence; Decision-to-`human-decision` evidence; resolved Unknown evidence;
low/open Unknown containment and valid mitigated risk; no high/open Unknown for
promotion/readiness; positive and negative ACs; exact allowed verification
commands; source chain; affected path/symbol existence at `base_commit`;
base-commit and dependency/document lock availability; review finding
resolution evidence; reviewer/approval current digest and revision; actor
allowlists/separation; and full hash recomputation. Any open review finding
fails, regardless of severity. Substantive sufficiency of evidence remains an
independent reviewer and human decision, never a string-join heuristic.

## Authorization, events, state, and audit

`.github/project-policy.yml` is real YAML validated against
`.github/schemas/project-policy.schema.json`. It contains exact arrays
`trusted_issue_authors`, `trusted_developers`, and `trusted_reviewers`, plus an
exact repository-owned `allowed_verification_commands` array and a rollout
mode (`dry-run`, `shadow`, `warn`, or `enforce`). Login comparison is
case-insensitive after GitHub-compatible normalization; whitespace, empty
items, duplicates after normalization, YAML aliases/tags, unknown fields, and
missing/empty capability lists fail closed. Membership is non-transitive.

Recognized comment bodies are matched after trimming only leading/trailing
ASCII whitespace. The entire remaining body must be one of these exact forms:

```text
/review-contract <positive-integer-revision> sha256:<64-lowercase-hex-subject-digest> sha256:<64-lowercase-hex-review-block-digest>
/approve-contract <positive-integer-revision> sha256:<64-lowercase-hex>
/promote
/ready-for-dev
```

There are no substrings, extra lines, Markdown quotes, code fences, Unicode
lookalikes, comments edited after creation, or case variants. `issue_comment`
payloads containing `issue.pull_request` are rejected. Only `created` comments
are commands. `workflow_dispatch` uses a typed operation and the same issue,
revision/digest, actor, authorization, separation, current-body, and Gate
checks. A state-changing dispatch must supply the database ID of a still-valid
exact human source command comment; `github.actor` must equal that comment's API
actor, and the normal receipt/idempotency rules apply. Dispatch without such a
comment is limited to read-only dry-run/revalidation. Manual dispatch is not an
authorization or event-attestation bypass.

The Issue body's `review`, `approval`, and `provenance.promoted_by` values are
claims, never authorization evidence by themselves. The event handler is the
only writer allowed to change `review.result` away from `pending`,
`approval.decision` away from `pending`, or to set `provenance.promoted_by` and
governed lifecycle fields. A direct body edit that asserts `pass`, `approved`,
or a promoter without the receipt chain below fails closed.

For review, the pending body supplies findings and `evidence_refs`. The handler
derives the complete target review block by setting `reviewed_by`, `result`,
`subject_revision`, and `subject_digest` from the API-authenticated command and
current contract. `review_block_digest` is lowercase SHA-256 over RFC 8785/JCS
of that complete target `review` object. The `/review-contract` command must
name that digest exactly. The review command adds no top-level evidence and
therefore cannot change the normative subject.

For approval, `approval.evidence_ref` must already point, before the approval
comment is created, to a unique normative top-level evidence record of type
`human-decision` that states the decision and its basis. The handler derives
the target approval block from that existing reference, the API actor, source
comment time, current revision, and current subject digest. The command attests
to that decision and subject; it does not add evidence and therefore does not
change `subject_digest`. Review evidence follows the same rule: all
`review.evidence_refs` must already resolve in normative evidence before the
review command.

Every state-changing attestation is a recoverable three-phase operation:
**bot intent receipt -> exact target mutation -> bot completed receipt**. The
handler fetches comments through the GitHub REST API with full pagination. The
source must be a non-PR Issue comment, still present, with an exact command body;
its numeric REST `id` is the database ID used by the operation. Its raw API body
UTF-8 bytes are stored as lowercase `sha256:<64-hex>`. V1 accepts it only when
`created_at == updated_at`; deletion, edit, body-digest mismatch, author
mismatch, or timestamp mismatch invalidates the operation. Actor identity comes
only from API `user.login` and must satisfy the corresponding allowlist and
separation rule.

`operation_id` is lowercase SHA-256 over an RFC 8785 object containing exactly:
repository database ID, Issue database ID, action, source comment database ID,
raw source-body digest, API actor login normalized for comparison, revision,
subject digest, review-block digest (`null` for approval/promotion/ready), and
the recomputed expected-before contract hash (computed by the normal single-
field-exclusion algorithm even when the Candidate's stored
`freeze.contract_hash` is still `null`). This makes authorization, intended
target, and compare-before-write state inseparable; a stored hash is never
trusted as the expected-before value without recomputation.

Before body mutation, the handler writes exactly one append-only intent receipt
whose versioned marker contains `operation_id`, every operation input, workflow
run ID/URL, expected-before hash, and intended target hash. It immediately reads
the comment back through the API and verifies its unique marker, fields,
`github-actions[bot]` author/bot type, source comment, repository, workflow run,
and action. Only then may it generate the target body deterministically from the
validated before-body and fixed event inputs. It performs a read-before-write
comparison of body/hash/`updated_at`, applies the exact target mutation, and
reads back byte-equivalent extracted target content plus the exact target hash.
Finally it writes exactly one completed receipt containing the same
`operation_id`, intent comment database ID, target hash, result, and run ID/URL,
then reads that receipt back and verifies its bot author, unique marker, and all
fields.

Retry/recovery is the following exhaustive state table; `before`, `target`, and
`unexpected` compare the current extracted contract/hash to the operation's
recorded values:

| Intent | Current body | Completed | Result |
| --- | --- | --- | --- |
| absent | before | absent | start: authorize, write/read-back intent, then continue |
| absent | before | present | fail closed: completed receipt has no authorizing intent or target |
| present and valid | before | absent | resume conditional target write |
| present and valid | before | present | fail closed: completed receipt contradicts the before-body |
| present and valid | target | absent | mutation already landed; read-back target and write/read-back completed receipt |
| present and valid | target | present and valid | idempotent success; emit no new comment or mutation |
| absent | target | absent/present | fail closed: hand-edited target cannot be retroactively authorized |
| any | unexpected | any | fail closed and require human reconciliation |
| invalid/multiple/conflicting intent or completed receipt | any | any | fail closed with exit 3 |

API response loss at any cut point recovers only by REST pagination over exact
`operation_id` markers; it never uses Search. An authorized intent followed by
an external writer producing the exact deterministic target is not privilege
escalation, but the handler must still independently read back that target and
write/read-back the completed receipt. An external near-target or a target with
no intent is never adopted.

Gate accepts non-pending `review`/`approval`, `promoted_by`, or lifecycle state
only when the complete valid intent+completed chain exists; the source comment
remains present and unedited; the API actor, run, action, revision, digests, and
expected-before hash agree; and the current body/hash equals the recorded exact
target. A body forged without intent can never be repaired by adding a receipt.
`/promote` and `/ready-for-dev` use the same protocol; only review carries a
non-null `review_block_digest`.

The GitHub event actor is authoritative. Review attestation requires a
`trusted_reviewers` actor different from Candidate author and approval actor.
Approval and promotion require `trusted_issue_authors`; ready requires
`trusted_developers`. A Candidate's `provenance.created_by` must match its
actual GitHub author and policy. An Engineering Issue created by
`github-actions[bot]` is authorized through the event-backed human stored in
`provenance.promoted_by` and the verified promotion receipt, never through the
bot's Issue authorship.

Ordinary per-entity transitions use concurrency key
`engineering-governance-<repository-id>-<issue-number>` with
`cancel-in-progress: false`. Promotion is cross-entity and uses a separate
workflow with the repository-global key
`engineering-promotion-<repository-id>`, serializing V1 promotions. The fixed
lock/order protocol is: acquire repository promotion concurrency; revalidate
and logically lock the Candidate by writing one promotion-intent receipt;
enumerate all repository Issues and comments through REST pagination for the
exact nonce/marker; create or recover the target; read/hash-verify the target;
write target linkage receipt; finalize the Candidate; release. Ready on a new
target fails until the Candidate finalization receipt exists, so a per-target
run cannot overtake promotion.

For promotion, the promotion nonce is the protocol's `operation_id`, with
action `promote`, `review_block_digest: null`, and the Candidate's current full
hash as expected-before. Exactly one bot-authored promotion-intent receipt binds
that nonce before issue creation. The deterministically rendered Engineering
Issue body/hash is the target; Candidate finalization occurs only after its
completed receipt and target linkage read back. Recovery uses paginated REST
`issues.listForRepo` and
`issues.listComments` results and exact hidden markers; it never depends on the
eventually consistent Search API. Zero verified targets permits creation, one
permits recovery/linkage, and multiple/conflicting targets exit 3 for human
reconciliation. `issues.opened` for a bot-created Engineering Issue is routed
to a read-only verification job and can neither relabel nor start a second
promotion.

Each delivery is keyed by event type, issue, comment/delivery identifier,
revision, and subject digest. Before mutation the handler scans append-only
machine audit markers, reads the current Issue again, and compares issue ID,
body hash, `updated_at`, revision, subject digest, full hash, labels, and
expected state. After mutation it reads back and verifies. This narrows GitHub
API TOCTOU; a mismatch never gets treated as success. Duplicate comments,
redeliveries, and reruns return the existing receipt without creating a new
comment or Issue. Failure after creation but before linkage leaves Candidate
state unchanged and the next run recovers the existing target.

Workflows declare top-level `permissions: {}`. Dry-run/shadow/read/validation
jobs have job-level `contents: read` and `issues: read`. Every read or write job
that revalidates an intent/completed receipt's workflow run additionally has
exactly `actions: read`; jobs that do not read a workflow run must not receive
that permission. A separate warn/enforce mutation job is gated on validated
outputs and rollout mode and receives job-level `contents: read`,
`issues: write`, plus `actions: read` only when it performs run revalidation; no
read-only job can mint a token with write scope, and no job receives
`contents: write`. Promotion creation uses the same isolated write-job pattern.
Mocked 403 responses caused by omitted `actions: read` are mandatory fixtures
and must fail closed rather than skipping run verification.

Audit comments are append-only and contain a versioned hidden marker plus event,
event actor, UTC time, source/target issue, revision, base commit, schema,
subject digest, contract hash, Gate result, finding IDs, idempotency key, and
recovery status. They exclude secrets, raw Issue instructions, raw logs, tokens,
and author reasoning. Rejections are audited without enumerating private policy
membership. Logs and summaries expose counts/IDs, never tokens or full
untrusted bodies. Metrics include Gate pass/fail, failure class, stale/replay,
transition latency, API retries, idempotent recovery, and read-back mismatch.
Non-command comments, including the workflow's own bot audit/receipt comments,
are side-effect-free no-ops: no new audit comment, label, body write, or dispatch
occurs. This is required to prevent recursive `issue_comment` runs.

## Threat model and mandatory fixtures

| Threat | V1 control and fixture |
| --- | --- |
| prompt injection in external text | Intake is data only; Actions never send it to a model or execute it; fixture contains instructions to alter policy/run shell |
| body tampering | strict extraction, dual digests, pre-mutation reread, freeze/read-back; fixtures mutate narrative, normative, attestation, and lifecycle fields separately |
| TOCTOU | shared concurrency, body/`updated_at` recheck, post-write read-back; mocked API changes the body between reads |
| replay/duplicate delivery | deterministic operation ID plus intent/completed markers and exhaustive recovery table; fixtures repeat each delivery at every API cut point |
| permission escalation | exact non-transitive arrays, workflow-dispatch parity, top-level `{}`, `actions: read` only for run revalidation, no `contents: write`; fixtures use association-only collaborator, cross-list actor, overbroad jobs, and 403 when run-read scope is absent |
| actor spoofing | event actor and API-resolved comment author override JSON usernames; fixtures put a trusted login only in contract text |
| attestation forgery | body declarations require unchanged source plus unique intent/completed bot receipts, verified run, expected-before hash, and exact target; fixtures directly edit `pass`, `approved`, and `promoted_by`, delete/edit sources, paste markers, omit intent, and replay old revision/digests |
| recursive bot events | non-command and bot receipt comments are no-audit no-ops; fixture redelivers the receipt's own `issue_comment` event |
| fork PR | PR workflows use base repository code, read-only permissions, no secrets, and never execute fork-controlled scripts or Issue commands |
| untrusted checkout | state workflows checkout the default branch by immutable event/base SHA, never a PR head or Issue-supplied ref |
| command smuggling | exact trimmed whole-body grammar; fixtures cover quote, fence, suffix, prefix, CR/LF extra line, Unicode slash, case change |
| duplicate contract/parser ambiguity | fatal UTF-8, exact markers/fence/object, duplicate-key rejection, limits; parser corpus covers every bypass |
| hash confusion | official RFC vectors, projection whitelist, single full-hash exclusion, lowercase algorithm-tagged hashes |
| bot-author authorization | promoted human comes from authorized command event and is copied to `promoted_by`; fixture rejects bot author as a human capability |
| malicious verification text | Gate only compares exact allowlisted commands; Actions never run contract commands |
| dependency/action compromise | hash-locked Python graph, full Action SHAs, advisory/license review, Dependabot PR verification |

## Dependency order and PR slices

Slices are merged in order. Acceptance criteria are fixed here; weakening one
requires explicit maintainer approval and a plan revision.
Every **Verify** block starts from a clean checkout. Slices 1-9 invoke
`.github/scripts/test-governance.sh`; caches are optional inputs, never prior
runner state. Slice 0 necessarily uses only repository/bootstrap prerequisites
because it precedes the locked test entry point, and Slice 1 immediately brings
it under the unified check.

### Slice 0 — Safety containment of existing automation

**Goal:** remove the repository's currently observable remote-code-write and
model-driven Issue-mutation paths before installing dependencies or adding new
governance. This is containment only and is the first merge/deploy slice.

**Add:** no files. **Modify:**
`.github/workflows/01-ai-development-workflow.yml` and
`.github/workflows/00-issue-ai-triage.yml`. Preserve both paths so the current
required-file manifests remain valid. **Delete:** no files.

**Out of scope:** new governance logic, policy/schema, labels, Issue body edits,
comments, branch creation, dependency installation, model calls, required-check
changes, and any remote write in the local implementation batch.

**Prerequisites:** accepted design/schema/plan; clean checkout; explicit local
confirmation for these two file edits. Merge/push and Actions enable/disable are
separate remote writes requiring their own target/operation preview, human
confirmation, observed run, and read-back.

**Behavior:** replace each workflow with a minimal manually dispatched,
read-only containment notice/check. Triggers are only `workflow_dispatch`; top
permissions are `{}` and the single explanatory job has only
`contents: read`. It may check out the default branch using the already pinned
checkout SHA and print a fixed repository-owned notice, but it does not accept
an Issue number/body, use Secrets, call a model/API, grant `issues: write`,
create a comment/label/branch/commit/artifact, or run untrusted content. The
workflow names and paths remain stable until their later atomic replacements.

**Fixtures:** the hazardous strings and constructs in the pre-containment files
are the regression corpus: `contents: write`, `issues: write`,
`issue_comment`, Issue open/edit triggers, `AI_API_*`, `curl`, `addLabels`,
`createComment`, `git checkout -b`, bot Git identity, and `git push`. No fixture
file is added in this containment slice.

**Verify:** from a clean checkout run
`python3 -c "from pathlib import Path; import yaml; [yaml.safe_load(p.read_text()) for p in map(Path, ['.github/workflows/01-ai-development-workflow.yml', '.github/workflows/00-issue-ai-triage.yml'])]"`;
run
`! rg -n 'contents:[[:space:]]*write|issues:[[:space:]]*write|issue_comment:|AI_API_|git[[:space:]]+push|git[[:space:]]+checkout[[:space:]]+-b|addLabels|createComment|issues\.update|branches\.create|curl[[:space:]]' .github/workflows/01-ai-development-workflow.yml .github/workflows/00-issue-ai-triage.yml`;
run
`rg -n 'workflow_dispatch:|contents:[[:space:]]*read' .github/workflows/01-ai-development-workflow.yml .github/workflows/00-issue-ai-triage.yml`;
run `git diff --check` and inspect the complete two-file diff. Slice 0 predates
the locked test entry/actionlint in Slice 1; after Slice 1 lands, the unified
entry retests these stubs on every later slice.

**Acceptance:** both workflows parse, have no automatic Issue/comment trigger,
have no model/Secret/Issue mutation or Git-write primitive, grant no write
permission, and can only produce a manual read-only run summary. Current
required manifests still pass because both paths remain. No statement claims
the containment is remotely active until the separately confirmed push/run is
read back.

**Rollback:** fail safe. Before remote deployment, correct or abandon the local
patch without touching GitHub. After remote deployment, disable the two
workflows through a separately confirmed remote operation and read back the
disabled state; never restore their former model call, Issue mutation, branch
creation, or push behavior. Preserve run history and audit evidence.

### Slice 1 — compatibility and dependency lock

**Goal:** prove the chosen runtime/canonicalizer and host discovery assumptions
before governance code depends on them.

**Add:** `.python-version`, `.github/governance/requirements.in`,
`.github/governance/requirements.txt`,
`.github/governance/tools/actionlint.version`,
`.github/governance/tools/actionlint.sha256`,
`.github/scripts/test-governance.sh`,
`tests/governance/test_rfc8785_compat.py`,
`tests/governance/fixtures/rfc8785/` (official vectors with provenance), and
`tests/host-compatibility/README.md`.

**Modify:** `.github/dependabot.yml` to cover the locked Python dependency
location. **Delete:** nothing.

**Out of scope:** extractor, schema/semantic Gate, workflows, state writes,
Skills, host adapter installation, and remote changes.

**Prerequisites:** network approval for version/hash resolution; official
upstream metadata available.

**Behavior:** execute the dependency spike above; test Codex's documented root
`.agents/skills`, progressive disclosure, and symlinked Skill-folder behavior;
test OpenCode's documented `.agents/skills` discovery/on-demand loading; test
Claude Code's documented `CLAUDE.md` import and separately test whether a
canonical `.agents/skills` folder or a symlink adapter is actually supported.
The tests record versions and outcomes, not model opinions.
The test entry point implements online and explicit offline-cache modes exactly
as defined above and always starts with a new `/tmp` venv.

**Fixtures:** official RFC 8785 examples/number vectors, UTF-16 key ordering,
escaped controls, Unicode, invalid surrogate/non-finite/out-of-range cases, and
a minimal disposable Skill for discovery tests.

**Verify:** `.github/scripts/test-governance.sh` from a clean checkout (online);
repeat with populated `GOVERNANCE_WHEELHOUSE`, `GOVERNANCE_TOOL_CACHE`, and
offline mode; remove one cached artifact and verify fail-closed; verify
`actionlint` version and checksum before it parses every workflow;
the documented manual commands in `tests/host-compatibility/README.md` for each
installed host; `git diff --check`.

**Acceptance:** clean task-specific venv and hash-locked install; online and
complete offline-cache runs pass while incomplete offline cache fails; locked
`actionlint` validates every workflow; every accepted RFC vector passes;
licenses/advisories are recorded and acceptable; full Action SHAs are resolved
but not yet inserted; each host claim is either demonstrated or explicitly
routes to the fallback adapter. No unverified shared-Skill claim remains.

**Rollback:** revert this slice; no Issue, workflow, or remote state exists.

### Slice 2 — strict contract, schema, semantic, policy, and hash core

**Goal:** deliver a deterministic offline Issue Quality Gate library and CLI.

**Add:**

```text
.github/project-policy.yml
.github/schemas/project-policy.schema.json
.github/scripts/governance/github_governance/__init__.py
.github/scripts/governance/github_governance/__main__.py
.github/scripts/governance/github_governance/audit.py
.github/scripts/governance/github_governance/canonical.py
.github/scripts/governance/github_governance/contract.py
.github/scripts/governance/github_governance/errors.py
.github/scripts/governance/github_governance/policy.py
.github/scripts/governance/github_governance/schema_validation.py
.github/scripts/governance/github_governance/semantic.py
.github/scripts/governance/github_governance/state.py
tests/governance/conftest.py
tests/governance/test_contract_parser.py
tests/governance/test_schema.py
tests/governance/test_semantic.py
tests/governance/test_policy.py
tests/governance/test_digest.py
tests/governance/test_state.py
tests/governance/fixtures/contracts/**
tests/governance/fixtures/parser/**
tests/governance/fixtures/policy/**
```

**Modify:** `.github/governance/requirements.in` and locked output only if a
reviewed test dependency was omitted; do not change the accepted Engineering
Issue schema unless a separately approved schema defect is found. Rename
`.github/settings.yml` to `.github/SETTINGS.md` in the same commit, preserving
its human-runbook content. Update every link and required-file entry in
`README.md`, `BOOTSTRAP.md`, `CONTRIBUTING.md`, `.github/WORKFLOWS.md`,
`.github/workflows/00-baseline-check.yml`,
`.github/workflows/10-pr-ai-review.yml`, and
`.github/workflows/20-ci-build-test.yml` from the old path to the new path.
Those three required-file arrays also add `.python-version`, both locked
requirements files, both actionlint lock files,
`.github/scripts/test-governance.sh`, `.github/project-policy.yml`,
`.github/schemas/project-policy.schema.json`, the accepted Engineering Issue
schema, and every shipped `github_governance` module listed in this slice.
**Delete:** `.github/settings.yml` only as the rename source; delete no runbook
content.

**Out of scope:** GitHub API calls, issue creation/comments/labels, workflows,
remote changes, and execution of AC commands.

**Prerequisites:** Slices 0-1 accepted; exact human allowlists confirmed or policy
committed empty so authorization tests fail closed.

**Behavior:** implement the extractor/CLI/exit codes, Draft 2020-12 plus
`date-time` checking, accepted schema selection by exact `schema_version`,
semantic joins, exact command allowlist, state table, normative projection,
dual hashes, and revision rules. Policy loading uses safe YAML and rejects tags,
aliases, duplicate keys, unknown fields, and normalized duplicates.
`.github/SETTINGS.md` remains prose and shell examples and is never passed to a
YAML parser; the only machine policy is `.github/project-policy.yml` validated
against its checked-in schema.

**Fixtures:** all parser and threat-model bypasses above; every schema conditional;
duplicate IDs across arrays; missing/wrong-type cross-references; Decision
without human decision; resolved/open finding combinations; Unknown
containment/risk/resolution combinations; wrong source chain; stale base/locks;
approval/review actor collisions; both AC polarities; allowed/disallowed
commands; all digest/revision golden vectors.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_contract_parser.py tests/governance/test_schema.py tests/governance/test_semantic.py tests/governance/test_policy.py tests/governance/test_digest.py tests/governance/test_state.py`;
`.github/scripts/test-governance.sh --governance gate --body-file tests/governance/fixtures/contracts/valid-candidate.md --policy .github/project-policy.yml --repository-root . --dry-run`;
`test -f .github/SETTINGS.md && test ! -e .github/settings.yml`;
validate `.github/project-policy.yml` as YAML and against
`.github/schemas/project-policy.schema.json`; assert the workflow YAML loop does
not include `.github/SETTINGS.md`;
`! rg -n '\.github/settings\.yml' README.md BOOTSTRAP.md CONTRIBUTING.md .github/WORKFLOWS.md .github/workflows`;
`git diff --check`.

**Acceptance:** valid fixture exits 0; each invalid fixture returns its specified
1/2/5 code and stable finding ID; after the locked install, core/fixture
execution uses no network; narrative-only changes
leave both digests unchanged; full RFC vectors still pass; code coverage reaches
every state transition and fail-closed branch. `SETTINGS.md` is preserved as
human prose, is absent from YAML validation inputs, and all live links/manifests
name the new path; project policy parses and passes its schema.

**Rollback:** revert the slice as one unit, including the
`.github/SETTINGS.md` -> `.github/settings.yml` rename and every synchronized
link/manifest. It has no workflow or remote state. Preserve the accepted
design/schema files; never leave both settings paths or a required manifest
pointing at a missing path.

### Slice 3 — Intake, Candidate, and Engineering surfaces

**Goal:** make entity type and lifecycle visible without granting development
authority.

**Add:** `.github/ISSUE_TEMPLATE/config.yml`,
`.github/ISSUE_TEMPLATE/intake-bug.yml`,
`.github/ISSUE_TEMPLATE/intake-feature.yml`,
`.github/ISSUE_TEMPLATE/intake-refactor.yml`,
`.github/ISSUE_TEMPLATE/candidate.md`,
`.github/templates/engineering-issue.md`,
`.github/workflows/00-issue-intake.yml`, and
`tests/governance/test_issue_surfaces.py`.

**Modify:** `.github/workflows/00-baseline-check.yml`,
`.github/workflows/10-pr-ai-review.yml`, and
`.github/workflows/20-ci-build-test.yml` in this same slice: remove all four
deleted paths from each required-file array and add
`.github/ISSUE_TEMPLATE/config.yml`, the three `intake-*.yml` forms,
`.github/ISSUE_TEMPLATE/candidate.md`,
`.github/templates/engineering-issue.md`, and
`.github/workflows/00-issue-intake.yml`. **Delete:**
`.github/ISSUE_TEMPLATE/bug.md`,
`.github/ISSUE_TEMPLATE/feature.md`,
`.github/ISSUE_TEMPLATE/refactor.md`, and
`.github/workflows/00-issue-ai-triage.yml`.

**Out of scope:** promotion, ready, coding, remote label creation, and any model
call. No public template creates an Engineering Issue.

**Prerequisites:** Slice 2; labels are represented as planned values but remote
application is deferred.

**Behavior:** forms create Intake data only. Candidate is a contract-bearing
artifact whose author/provenance/policy are Gate-checked. Engineering Issues are
rendered only by promotion. The intake workflow is deterministic, treats all
text as untrusted data, uses `contents: read`/`issues: write`, rejects PR-shaped
events, and in dry-run/shadow performs no state mutation.

**Fixtures:** external prompt injection, trusted/untrusted Candidate author,
wrong/multiple entity labels, Intake with contract markers, Candidate without a
contract, reopened promoted Candidate, and PR masquerading as Issue.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_issue_surfaces.py`;
checksum-verified `actionlint` over every workflow; schema-aware Issue Form YAML
checks; static assertion that no model endpoint/secret/curl/eval/Issue-body
execution exists; `rg` proves no required-file manifest references any deleted
path and each lists every added replacement; `git diff --check`.

**Acceptance:** all public forms yield Intake; no Intake can reach ready; only a
policy-valid Candidate can enter Candidate Gate evaluation; no model is called;
no Engineering Issue can be manually authorized by template choice.

**Rollback:** revert forms, intake workflow, and all three required-file
manifests in one commit so CI never observes a half-migrated file list. Existing
Intake/Candidate Issues and comments remain untouched and are relabelled only
after separate approval; rollback never invokes the removed model workflow.

### Slice 4 — Issue Quality Gate and attestations

**Goal:** run deterministic Gate checks and bind independent review and approval
to the current Candidate subject.

**Add:** `.github/scripts/governance/github_governance/events.py`,
`.github/scripts/governance/github_governance/github_api.py`,
`.github/scripts/governance/github_governance/attestations.py`,
`.github/workflows/02-engineering-governance.yml`,
`tests/governance/test_auth_events.py`,
`tests/governance/test_attestation_recovery.py`,
`tests/governance/test_github_api.py`,
`tests/governance/test_gate_e2e.py`, and
`tests/governance/fixtures/events/**`.

**Modify:** `.github/project-policy.yml` with only confirmed logins and rollout
mode; `.github/ISSUE_TEMPLATE/candidate.md` to document exact commands; and the
required-file arrays in `.github/workflows/00-baseline-check.yml`,
`.github/workflows/10-pr-ai-review.yml`, and
`.github/workflows/20-ci-build-test.yml` to add
`.github/workflows/02-engineering-governance.yml` and the shipped event/API/
attestation modules.
**Delete:** nothing.

**Out of scope:** promotion creation, ready transition, branch/push/PR, remote
enforcement, and running declared verification commands.

**Prerequisites:** Slices 2-3; distinct reviewer input for a passing path;
resolved Action SHAs.

**Behavior:** on Candidate edit/comment or typed dispatch, extract and Gate the
current body. The exact review form is
`/review-contract <revision> <subject_digest> <review_block_digest>`; the exact
approval form is `/approve-contract <revision> <subject_digest>`. Both use
the fixed API source-comment and recoverable intent→target→completed protocol
above. The handler alone writes non-pending review/approval fields. It computes
`operation_id` and deterministic target, writes/read-backs the bot intent,
conditionally writes/read-backs the exact target body/hash, then
writes/read-backs the bot completed receipt. Gate validates the body
declaration, current revision/subject, unchanged source comment,
actor/list/separation, pre-existing normative evidence, target review-block
digest, unique intent/completed receipts, expected-before/target hashes, and
workflow run as one chain. Gate labels move only `draft <-> gate-failed` or
`draft -> gate-passed`; a pass requires all findings resolved, current review,
current approval, no high/open Unknown, and all semantic checks. Top-level
workflow permissions are empty; read/dry-run/shadow and write/warn/enforce jobs
are separate with the fixed job-level scopes. Non-command and bot comments exit
0 before mutation and produce no receipt/audit comment.

**Fixtures:** every exact-command bypass; PR comment; unauthorized dispatch;
reviewer equals author/approver; claimed actor differs from event actor; stale
digest/revision/review-block digest; direct body edits forging `pass` or
`approved`; approval evidence added after its command; wrong/non-human-decision
approval evidence; deleted or edited source comment; `created_at != updated_at`;
source body digest mismatch; human-pasted bot marker; duplicate/conflicting
receipt; receipt with wrong actor/run/action/hash; replay from an old revision;
non-command, bot receipt, and receipt-redelivery no-ops; API 403/404/409/5xx,
including 403 from missing `actions: read`; pagination; body changes between
reads; unexpected body; no-intent forged exact target; external exact-target
write after valid intent; duplicate/conflicting intent/completed receipts; and
response loss/retry at every API cut point: intent create/read-back, pre-write
read, body write/read-back, completed create/read-back, and workflow-run read.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_auth_events.py tests/governance/test_github_api.py tests/governance/test_gate_e2e.py`;
`.github/scripts/test-governance.sh --pytest tests/governance/test_attestation_recovery.py`;
mocked end-to-end event command with zero network; safe workflow YAML parse;
checksum-verified `actionlint`; static permissions/action-SHA/untrusted-checkout
scan; assert top-level permissions are empty, read jobs have no write scope,
only run-revalidating jobs have `actions: read`, no job has `contents: write`,
and write jobs are mode/output gated; `git diff --check`.

**Acceptance:** Gate returns only PASS/FAIL with stable findings; attestations
are event-backed by one unchanged source plus a complete, unique, read-back
verified intent/completed chain and exact current target; every state-table row
and API cut point recovers or fails exactly as specified; a 403 workflow-run
read fails closed; direct body claims and all bypasses fail closed;
non-command/bot comments are no-audit no-ops; dry-run/shadow makes no GitHub
mutation; warn may comment but cannot transition; no untrusted command is
executed and no permission is broader than specified.

**Rollback:** set rollout to `dry-run` or disable the workflow. Existing bodies
and append-only audits remain; labels can be corrected by an approved remote
batch without deleting contracts.

### Slice 5 — promotion, contract freeze, ready, stale, and recovery

**Goal:** implement the two authorized state-changing commands with idempotent
GitHub recovery.

**Add:** `tests/governance/test_promotion.py`,
`tests/governance/test_ready.py`,
`tests/governance/test_recovery.py`, and
`tests/governance/fixtures/api/**`; add
`.github/workflows/03-engineering-promotion.yml` as the isolated cross-entity
promotion workflow.

**Modify:** `.github/scripts/governance/github_governance/github_api.py`,
`.github/scripts/governance/github_governance/state.py`,
`.github/scripts/governance/github_governance/audit.py`,
`.github/scripts/governance/github_governance/events.py`,
`.github/workflows/02-engineering-governance.yml`, and the required-file arrays
in `.github/workflows/00-baseline-check.yml`,
`.github/workflows/10-pr-ai-review.yml`, and
`.github/workflows/20-ci-build-test.yml` to add
`.github/workflows/03-engineering-promotion.yml`.
**Delete:** nothing.

**Out of scope:** implementation branches, Issue command execution, PR creation,
merge, and remote activation.

**Prerequisites:** Slice 4; confirmed author/developer lists; a passing Candidate
requires a distinct reviewer; enforcement remains off during local tests.

**Behavior:** implement the accepted seven-step promotion sequence, promotion
provenance, target render/read-back, exact source links, promotion nonce/intent
receipt, REST-pagination recovery, global promotion lock and fixed lock order,
single-target idempotency, bot-created `issues.opened` read-only verification,
and post-verification Candidate label. The handler alone sets
`provenance.promoted_by`; Gate verifies its source comment and bot receipt.
Implement
ready preconditions, lifecycle-only revision behavior, full rehash, and local
handoff audit. Base, dependency, document, attestation, body, and hash drift
produce stale findings. Duplicate commands replay the existing result. A failed
write never reports success; partial-create recovery never creates a second
Engineering Issue.

**Fixtures:** two concurrent promote comments on one Candidate and on two
Candidates, nonce collision/conflict, lock-order assertion, duplicate
delivery/comment, create success plus response loss, REST pagination spanning
multiple issue/comment pages, search returning stale/empty data while REST
recovers correctly, link failure, conflicting target/intent markers,
response loss/retry before and after promotion intent, Issue create, target
read-back, completed receipt, linkage, and Candidate finalization; 403 on
workflow-run revalidation without `actions: read`;
Candidate changed after Gate, stale base/lock/review/approval/full hash,
narrative-only edit, bot-authored Engineering Issue with human promoter,
direct body edit forging `promoted_by`, unauthorized bot-as-promoter,
bot-created `issues.opened` attempting mutation, and ready retry after read-back
failure.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_promotion.py tests/governance/test_ready.py tests/governance/test_recovery.py`;
mocked end-to-end `candidate -> engineering contracted -> ready` with paginated
REST recovery; checksum-verified `actionlint`; assert the promotion workflow's
repository-global concurrency and the per-entity workflow's distinct key;
assert opened bot Issues route only to read jobs; assert every job that reads a
receipt run has `actions: read`, all others lack it, and none has
`contents: write`; `git diff --check`.

**Acceptance:** one valid promotion creates exactly one verified target and
keeps the subject digest/revision; ready changes only allowed lifecycle/freeze
data; every partial failure has the specified safe resume point and audit;
concurrent/replayed events cannot duplicate or skip state.
Promotions across Candidates serialize in the declared lock order, and recovery
does not consult GitHub Search. Missing `actions: read` yields a fail-closed 403
fixture rather than skipped receipt/run validation.

**Rollback:** switch workflow to `warn`/disable it. Never delete the created
Engineering Issue or its frozen contract; label it `governance:transition-
paused` through a separately approved remote action and recover from the
promotion intent/audit receipts. Disable both governance and promotion write
jobs together; retain every contract, nonce, receipt, and audit comment.

### Slice 6 — local handoff, PR stale binding, and required checks

**Goal:** replace automated development with an evidence-bound local handoff
and make contract validity a CI signal.

**Add:** `.github/scripts/governance/github_governance/pr_binding.py`,
`tests/governance/test_pr_binding.py`, and
`tests/governance/fixtures/pull_requests/**`.

**Modify:** completely rewrite
`.github/workflows/01-ai-development-workflow.yml`; modify
`.github/PULL_REQUEST_TEMPLATE.md`,
`.github/workflows/10-pr-ai-review.yml`,
`.github/workflows/20-ci-build-test.yml`, and
`.github/workflows/00-baseline-check.yml`.
**Delete:** every branch creation/push, bot Git identity, `contents: write`,
`AI Development Agent`, `dev_plan.json`, and implementation-stage code from the
old `01-ai-development-workflow.yml`.

**Out of scope:** executing Issue AC commands, coding, push, PR creation, merge,
or ruleset write.

**Prerequisites:** Slice 5; exact desired required job names fixed before this
slice. The V1 names are `Configuration Validation`, `Security Scanning`, and
`Engineering Contract Validation`.

**Behavior:** the rewritten workflow validates a ready Engineering Issue and
emits a read-only handoff containing the binding tuple and approved local
commands. Its job has only `contents: read` and `issues: read`; any audit receipt
plus `actions: read` because ready-contract validation revalidates the receipt
workflow run; any audit receipt belongs to the separately gated governance
write job. It performs no Git or Issue write. PR template requires the binding
tuple. PR checks
read the current Engineering Issue from the base repository, reject forks from
running untrusted repo code/secrets, compare all five binding values, and fail
stale. Required-file lists include every shipped V1 asset.

**Fixtures:** missing/malformed PR binding, wrong issue type, current/stale each
binding member, fork PR, PR body injection, inaccessible Issue, and narrative-
only Issue edit.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_pr_binding.py`;
checksum-verified `actionlint`; scan every workflow for
full Action SHAs, finite timeout, minimum permissions, `git push`, branch
creation, bot Git identity, model/API secrets, Issue-body shell interpolation,
and untrusted PR checkout; assert `actions: read` exists on the handoff/PR job
that revalidates receipts, is absent elsewhere unless required, and
`contents: write` is absent everywhere; include a mocked 403 Actions-run read;
run
`.github/scripts/check-commit-attribution.sh <reviewed-range>` when a commit is
proposed; `git diff --check`.

**Acceptance:** `/ready-for-dev` cannot cause a Git ref write; valid binding
passes and any tuple drift fails `Engineering Contract Validation`; existing
configuration/security check names remain stable; fork PR is read-only and no
untrusted code is executed. A missing Actions-run permission/read fails the
contract check; it never degrades to hash-only validation.

**Rollback:** revert required-check code before any remote ruleset addition, or
remove only the new check from the ruleset through a separately confirmed
remote change. Keep Issue contracts/bindings and never restore automated branch
push behavior.

### Slice 7 — constitution, sub-policies, upstream Skills, and host adapters

**Goal:** expose one logical contract to local roles across supported hosts.

**Add:**

```text
.agent/context-policy.md
.agent/engineering.md
.agent/host-adapters.md
.agent/issue-workflow.md
.agent/security.md
.agent/tool-policy.md
.agent/verification.md
.agents/skills/issue-investigate/SKILL.md
.agents/skills/issue-author/SKILL.md
.agents/skills/issue-review/SKILL.md
.agents/skills/issue-promote/SKILL.md
CLAUDE.md
tests/governance/test_agent_assets.py
```

Conditional on the accepted Slice 1 spike, add either symlink adapters under
`.claude/skills/<name>` pointing to the canonical `.agents/skills/<name>` or a
generated mirror plus a byte-for-byte drift check. Add no speculative
`.opencode` or `.codex` configuration: their V1 adapters are their documented
native loading of root `AGENTS.md` and `.agents/skills` unless the spike proves
an additional recognized file is necessary.

**Modify:** `AGENTS.md` into the concise constitution/index while preserving
bootstrap routing, human attribution, simplicity, surgical changes,
goal-driven evidence, dispatch/verification, and all mandatory shared rules.
**Delete:** nothing.

**Out of scope:** implementation/commit/PR Skills, model assignment, identical
LSP/Context7 integration, and remote changes.

**Prerequisites:** Slice 6 and host compatibility results.

**Behavior:** each Skill defines role, inputs, evidence outputs, prohibited
actions, verification, and handoff. Investigator is read-only; Author compiles
only cited evidence; Reviewer receives raw evidence plus Candidate but no author
reasoning and assumes a material error; Promote prepares/validates but cannot
bypass the human GitHub command. Tool policy makes repository code primary,
requires current-version Context7/official docs for unstable third-party facts,
and treats LSP as auxiliary versus CLI/CI evidence. `CLAUDE.md` imports
`@AGENTS.md` and contains no competing policy.

**Fixtures:** missing Skill frontmatter, duplicate/drifting adapters, overlong
always-on constitution, reviewer brief containing author reasoning, Skill that
permits mutation outside its role, and unavailable Context7/LSP fail-closed
handoff.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_agent_assets.py`; each host
compatibility command from Slice 1; check Skill names/descriptions and canonical
content drift; check all indexed `.agent` files exist; `git diff --check`.

**Acceptance:** one canonical policy/Skill source; Codex and OpenCode discover
the four Skills on demand; Claude imports the constitution and loads the tested
adapter; no adapter silently copies divergent governance text.

**Rollback:** remove host adapters while retaining `AGENTS.md`, `.agent`, and
canonical Skills. A host that fails discovery is reported unsupported for V1,
not given a divergent rule set.

### Slice 8 — template propagation and documentation synchronization

**Goal:** make template-source maintenance and generated-project bootstrap
responsibilities explicit and synchronized.

**Add:** `tests/governance/test_bootstrap_docs.py`. **Modify:** `README.md`,
`BOOTSTRAP.md`, `CONTRIBUTING.md`,
`.github/WORKFLOWS.md`, `.github/SETTINGS.md`, `.github/dependabot.yml`, and
the required-file manifests in `00-baseline-check.yml`, `10-pr-ai-review.yml`,
and `20-ci-build-test.yml`. **Delete:** obsolete claims that Actions develop
code, create `dev/*`, or accept `/ready-for-dev` on ordinary Issues.

**Out of scope:** remote writes, target-project-specific commands, migration of
existing generated repositories, milestones, and releases.

**Prerequisites:** Slices 0-7 accepted so documentation describes observed file
names and checks.

**Behavior:** README states control/execution planes; CONTRIBUTING requires an
Engineering Issue contract; WORKFLOWS records final triggers/permissions/check
names; `SETTINGS.md` remains the human runbook for planned labels and staged
ruleset procedure and is never machine policy; BOOTSTRAP
collects trusted actors, reviewer separation, verification allowlist, host
availability, and separate remote confirmations. Generated projects rewrite
project identity, policy accounts/commands, affected tools, CI, CODEOWNERS, and
the settings runbook; template source retains canonical generic schemas, scripts, Skills,
and propagation checklists. Template generation does not propagate labels,
rulesets, settings, runs, or secrets, so bootstrap applies and reads those back
per target.

**Fixtures:** generated-project inventory with empty policy, missing reviewer,
different owner, unsupported host, no Context7/LSP, and existing user-authored
files that must not be overwritten.

**Verify:** `.github/scripts/test-governance.sh` first;
`.github/scripts/test-governance.sh --pytest tests/governance/test_bootstrap_docs.py`;
checksum-verified `actionlint`; parse only actual YAML
assets; explicitly assert `.github/SETTINGS.md` is excluded from YAML parsing
and `.github/project-policy.yml` passes its schema; search for obsolete
automated-development and `.github/settings.yml` claims; confirm exact check
names occur consistently; `git diff --check`.

**Acceptance:** every bootstrap responsibility has one owner and synchronization
point; generated-project instructions fail closed on missing actors/commands;
local and remote confirmation boundaries remain unchanged; documentation does
not claim V1 is remotely active before rollout.

**Rollback:** revert documentation as a unit with any local behavior rollback;
never claim old branch automation is restored unless its code actually is
(restoration itself is prohibited by V1 scope).

### Slice 9 — remote dry-run, shadow, warn, enforce, and ruleset rollout

**Goal:** apply the verified local system to `janssenkm/GithubBootstrap` without
surprise remote writes or loss of contracts.

**Add:** no local files. **Modify:** no local files. **Delete:** no local files.
If read-back exposes a defect, its separately reviewed fix returns to the owning
earlier slice. Remote objects are not represented as committed state.

**Out of scope:** coding agents, development branch pushes, migrations,
milestones, PR creation, and secrets.

**Prerequisites:** Slices 0-8 pass from a clean checkout; human confirms exact
actor lists including a distinct reviewer; workflow SHAs and dependencies are
locked; exact remote mutation preview approved.

**Behavior and order:**

1. **Dry-run:** dispatch against fixture/dedicated non-contract Issue with no
   writes in the read-only job; compare local and Actions Gate JSON. The write
   job is skipped and receives no token.
2. **Shadow:** enable event reads/summaries only; observe authorization, parser,
   hash, and Gate results without labels/body/comments/issue creation. The
   separate write job remains skipped.
3. **Warn:** allow append-only audit/warning comments but no contract/state
   mutation or issue creation; review noise, permissions, and injection safety.
4. **Enforce:** after separate confirmation, create/update the planned labels,
   enable authorized transitions, and read every object back. Start with one
   maintainer-authored test chain.
5. **Required check:** after `Engineering Contract Validation`,
   `Configuration Validation`, and `Security Scanning` have each appeared and
   succeeded on the intended branch/event, preview a main ruleset referencing
   the exact observed check names; obtain separate confirmation; create it;
   read back its ID, target, bypass actors, PR rule, and checks. Do not copy a
   ruleset ID from the template.

Planned label families are `type:intake`, `type:candidate`,
`type:engineering`; Intake `state:new|triaged|investigating|closed`; Candidate
`state:draft|gate-failed|gate-passed|promoted`; Engineering
`state:contracted|ready|in-progress|done|cancelled`; and
`governance:stale|transition-paused`. Exact colors/descriptions are previewed
before the label write. The shared terminal label `state:closed` also remains
valid for a withdrawn Candidate under the V1 state machine. Legacy default
labels are not deleted in V1.

**Fixtures/tests/evidence:** workflow run URLs/IDs, check-suite names, read-back JSON with
tokens removed, one unauthorized comment, one PR-comment rejection, duplicate
command delivery, stale body edit, partial-create recovery in a dedicated test
chain, and ruleset read-back.

**Verify:** `.github/scripts/test-governance.sh` from a clean checkout first;
then `gh api` read-only queries for repository,
labels, workflows/runs/jobs, Issues/comments created by the approved test,
Actions permissions, and rulesets; compare read-back to the approved preview.

**Acceptance:** each phase has reviewed evidence before the next; no workflow
pushes a branch or calls a model; only exact trusted actors transition state;
one end-to-end chain promotes once and becomes ready with verified digests;
required checks are observed before ruleset creation; remote read-back matches
the preview.

**Rollback:** first set rollout to shadow/disable state workflows, then remove
only the newly required governance check or disable the new ruleset after a
separate confirmation. Do not delete Issues, audit comments, frozen contracts,
or legacy labels. Mark interrupted contracts `governance:transition-paused` and
resume from their audit marker after correction.

## File-level manifest and design traceability

| Files | Slice | Design/Gate responsibility |
| --- | ---: | --- |
| contained `00-issue-ai-triage.yml`, `01-ai-development-workflow.yml` | 0 | immediate manual/read-only safety boundary before governance implementation |
| `.python-version`, `.github/governance/requirements.*`, actionlint version/checksum, `test-governance.sh` | 1 | clean-checkout reproducible Draft 2020-12/JCS/workflow runtime and supply chain |
| `.github/schemas/engineering-issue.schema.json` | accepted, unchanged by default | authoritative structural Gate |
| `.github/schemas/project-policy.schema.json`, `.github/project-policy.yml` | 2 | exact authorization and command policy |
| `github_governance/contract.py`, `canonical.py` | 2 | strict extraction, normative projection, dual hashes |
| `schema_validation.py`, `semantic.py`, `policy.py`, `state.py` | 2 | schema/semantic Gate, joins, actor/state rules |
| `audit.py`, `events.py`, `github_api.py`, `attestations.py` | 4-5 | exact events, recoverable intent/target/completed attestation, append-only audit, recovery/read-back |
| issue forms/templates and `00-issue-intake.yml` | 3 | Intake/Candidate/Engineering separation |
| `02-engineering-governance.yml`, `03-engineering-promotion.yml` | 4-5 | Gate/attestations/ready and separately locked cross-entity promote/freeze/stale |
| rewritten `01-ai-development-workflow.yml` | 6 | local-only handoff; no branch/push/model |
| `pr_binding.py`, PR template, PR/CI workflows | 6 | five-field stale binding and required check |
| `AGENTS.md`, `.agent/*.md` | 7 | constitution, context/tool/verification/security contracts |
| four `.agents/skills/*/SKILL.md` | 7 | investigator/author/adversarial review/promotion roles |
| `CLAUDE.md` and tested conditional adapters | 7 | thin host compatibility without policy forks |
| `.github/SETTINGS.md` rename plus links/manifests | 2 | human runbook separated from schema-validated machine policy |
| README/BOOTSTRAP/CONTRIBUTING/WORKFLOWS/SETTINGS | 8 | template-source/generated-project propagation and operator duties |
| settings labels/ruleset (remote only) | 9 | visible states, CI enforcement, read-back audit |
| `tests/governance/**`, `tests/host-compatibility/**` | 1-8 | every Gate field and known bypass becomes rerunnable evidence |

`GithubBootstrap_V2_Implementation_and_Documentation_Plan.md` is deliberately
absent from this manifest. It is neither copied nor validated as a V1 contract
asset; future backlog triage may cite it only as non-authoritative input.

No file is deleted except the three legacy Issue Markdown templates and the
model-backed triage workflow in Slice 3; their governed replacements must exist
and pass before deletion. The existing development workflow is rewritten in
place so references remain traceable, while every Git-writing behavior is
explicitly deleted.

## Consolidated test matrix

| Layer | Required coverage |
| --- | --- |
| unit | projection, hash, revision, ID index, policy normalization, command grammar, operation ID, deterministic target, review-block digest, intent/completed verification, recovery table, audit rendering |
| parser | UTF-8, byte limits, exact markers/fence/object, duplicate keys, residual/nested content, non-finite/deep input |
| schema | every required/type/enum/pattern/conditional/`additionalProperties` branch with format checking |
| semantic | Claim/evidence, Decision actor, Unknown/risk, finding resolution, source chain, locks, path/symbol, attestations |
| hash | official RFC 8785 vectors and V1 dual-digest/revision golden vectors |
| authorization | three exact arrays, non-transitivity, separation, Issue-vs-PR, exact unchanged source comments, unique intent/completed bot receipts and run, expected-before/target hashes, dispatch parity, bot provenance |
| state | every allowed/forbidden transition, stale tuple, lifecycle-only update, promoted Candidate reopening |
| mocked API | every intent/body/completed/run cut point, REST pagination, 403 missing run-read permission, retryable errors, source edit/delete, forged/no-intent body, exact external target, TOCTOU, response loss, nonce and duplicate/conflicting receipts/target |
| workflow static | checksum-locked actionlint, full Action SHAs, top-level `{}`, `actions: read` only on run-revalidating jobs, no `contents: write`, finite job scopes, read/write separation, safe checkout, no model/push/Issue command execution |
| end-to-end dry run | Intake -> Candidate -> review -> approval -> Gate -> promotion -> contracted -> ready -> PR binding, plus every failure stop |
| host compatibility | current Codex/OpenCode/Claude loading of constitution and each canonical Skill/fallback adapter |
| bootstrap | template-source versus generated-project inventories, empty policy, missing reviewer, remote confirmation/read-back |

The threat-model fixtures, extractor corpus, exact-command bypasses, review
finding cases, Unknown cases, cross-reference failures, actor spoofing, fork PR,
and concurrency/recovery cases in this plan are mandatory acceptance assets,
not optional follow-up tests.

## Observability and incident recovery

Each workflow job writes a terse step summary with operation, issue IDs,
revision, shortened digests for display plus full digests in machine JSON, Gate
result, finding IDs, mutation mode, `operation_id`, intent/completed comment IDs,
recovery-table row, idempotency result, and safe resume point.
Structured logs use stable event/failure codes so runs can be compared without
parsing prose. API retries use bounded exponential backoff only for documented
transient classes; authorization, validation, 404 on required evidence, and
conflict are never retried into success.

An operator can reconstruct: who requested a transition, which contract was
read, which Gate ran, whether a mutation occurred, which target was created,
what read-back proved, and where recovery starts. Secrets, tokens, raw external
text, model reasoning, and raw command output never enter audit comments. A
security-relevant mismatch sets `transition-paused`, disables further automatic
mutation for that Issue, and requires human review; rollback disables control
plane writes but never deletes the contract or audit history.

## Definition of Ready for implementation

Implementation may start only when:

- the accepted design, schema, and this plan have independent review approval;
- the working-tree baseline, the three accepted/untracked assets, and the
  separate non-authoritative root proposal are recorded and an executor brief
  protects them without adding the proposal to V1 scope;
- Slice 0's local containment change is separately authorized; any merge/push
  and remote workflow read-back remains a distinct confirmation;
- Slice 1 has network approval and its exact dependency/Action resolution method
  is accepted;
- the maintainer confirms whether `janssenkm` belongs in author and developer
  lists;
- a distinct reviewer login is supplied for any passing promotion test, or the
  implementation is explicitly limited to fail-closed paths until one exists;
- the local slice's exact file list, commands, acceptance, and rollback are
  authorized;
- no GitHub remote write is bundled with local implementation authority.

## Definition of Done for V1

V1 is done only when:

1. Slices 0-8 are merged in order with their exact tests passing and attribution
   checked for each proposed commit range.
2. The strict extractor, accepted schema, semantic Gate, RFC 8785 dual hashes,
   revision, unchanged source-comment plus deterministic
   intent/target/completed attestations,
   authorization, actor separation, transitions, cross-entity locking,
   REST-pagination recovery, audit, and PR stale binding have complete fixture
   evidence.
3. Static scans prove no Action runs a coding model, writes Git refs, uses an
   unpinned action, executes Issue commands, checks out untrusted code in a
   privileged job, or grants broader permissions than its operation needs.
   The clean-checkout entry point verifies hash-locked dependencies and the
   locked actionlint version/checksum on every run.
   Receipt-run jobs alone have `actions: read`; no job has `contents: write`.
4. The four canonical Skills and each claimed host adapter pass compatibility
   checks on recorded supported versions; unsupported behavior is not claimed.
5. Bootstrap and maintenance documents match the final files, check names,
   labels, confirmations, and template propagation boundaries.
6. A separately authorized Slice 9 rollout passes dry-run, shadow, warn, and one
   enforce test chain; every remote write is read back.
7. Only after observed successful checks, the separately approved ruleset names
   the exact required checks and its active configuration is read back.
8. Rollback has been rehearsed without deleting or rewriting any frozen
   contract or audit comment.

If remote rollout or the distinct reviewer input is withheld, local
implementation may be reported as `implemented` and locally `verified`, but V1
must remain `blocked` rather than claimed complete.

## Official host sources

Host behavior is version-sensitive and must be rechecked during Slice 1 against
current primary documentation:

- Codex Skills: <https://developers.openai.com/codex/skills> — root
  `.agents/skills`, progressive disclosure, and symlinked Skill folders are
  supported; verify on the installed version.
- Codex `AGENTS.md`: <https://developers.openai.com/codex/guides/agents-md> —
  instructions load along the directory chain with deeper scope taking
  precedence; the default combined limit is 32 KiB, so `AGENTS.md` remains a
  concise constitution/index.
- OpenCode Skills: <https://opencode.ai/docs/skills/> — project
  `.agents/skills/<name>/SKILL.md` is a compatibility source and Skill bodies
  load on demand.
- OpenCode rules: <https://opencode.ai/docs/rules/> — verify root `AGENTS.md`
  discovery and precedence on the selected release before claiming the adapter.
- Claude Code memory: <https://code.claude.com/docs/en/memory> — Claude Code
  reads `CLAUDE.md`, not `AGENTS.md`, and officially documents importing
  `AGENTS.md` from a thin `CLAUDE.md`.
- Claude Code Skills: <https://code.claude.com/docs/en/slash-commands> — the
  documented project location is `.claude/skills/<name>/SKILL.md` and bodies
  load when used. This does **not** establish direct `.agents/skills` or
  symlink-folder compatibility; Slice 1 must test and select the adapter.

Documentation citations establish discovery behavior, not that Context7, LSP,
or every host version is installed. Missing required evidence remains a Gate
failure under the accepted tool contract.

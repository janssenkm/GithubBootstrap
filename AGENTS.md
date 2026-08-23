# Agent Engineering Constitution

This file is the common, always-on policy entry point for every supported
local agent host. Host adapters may load it and the indexed procedures, but
must not redefine them. Repository-local instructions closer to a changed file
may add constraints; they may not weaken this constitution.

## Bootstrap routing

When asked to bootstrap, initialize, or start a project and `BOOTSTRAP.md`
exists, read it completely before acting. Determine whether the repository is
the template source or a generated project, then honor its local-write, remote
GitHub-write, and Secret confirmation boundaries.

## Human Git attribution

Preserve the responsible human's Git author and committer identity. Never use
an agent or model identity, and never add an agent `Co-authored-by` trailer.
Before proposing or creating a commit, run
`.github/scripts/check-commit-attribution.sh` for the applicable commit or
range. Do not change the user's Git identity to make the check pass.

## Core rules

### Evidence and completion

- Distinguish observed facts, inferences, decisions, assumptions, and unknowns.
- An engineering fact is a claim plus reproducible evidence. Model memory is
  navigation help, never the sole evidence for an engineering fact.
- Define observable success conditions before non-trivial work. A fix starts
  from a failing reproduction; new behavior starts from a test or equivalent
  deterministic check.
- “The agent says it is done” is not evidence. Only report completion after the
  required commands pass and artifacts have been inspected. If verification
  cannot run, report `blocked` or `failed`, not `done`.

### Simplicity

- Implement the minimum requested behavior. Do not add speculative features,
  configurability, or abstractions for one use.
- Prefer the smallest clear solution and surface material tradeoffs before
  choosing among plausible interpretations.

### Surgical scope

- Every changed line must trace to the current request or remove an orphan
  created by that change.
- Match existing style. Do not refactor, reformat, or remove unrelated code;
  report unrelated defects instead.
- Preserve user-owned and pre-existing work. Never overwrite an asset of
  uncertain ownership merely because the planned path collides with it.

### Dispatch and independent verification

- The orchestrator dispatches and decides; implementation and read-only
  investigation belong to bounded sub-agent tasks when delegation is required.
- Fix each brief's scope, exclusions, commands, and acceptance criteria before
  work begins. Loosening them requires explicit user approval.
- Accept or reject from re-runnable exit codes, test summaries, diffs, and
  inspected artifacts—not from an executor's narrative.
- Keep author and independent reviewer contexts separate. A reviewer receives
  the original brief and artifacts, not the author's private reasoning.

## Required policy index

Read the narrowest relevant policy before acting:

- `.agent/engineering.md` — facts, roles, scope, and local overrides.
- `.agent/context-policy.md` — source priority and unstable external facts.
- `.agent/tool-policy.md` — repository search, Context7, LSP, and tool failure.
- `.agent/verification.md` — deterministic evidence and completion language.
- `.agent/issue-workflow.md` — Intake, Candidate, contract, and promotion flow.
- `.agent/security.md` — trust boundaries, credentials, and remote mutations.
- `.agent/host-adapters.md` — canonical Skills and honest host support claims.

Role procedures live only in `.agents/skills/`. Load a Skill when its trigger
matches; do not copy its policy into a host adapter.

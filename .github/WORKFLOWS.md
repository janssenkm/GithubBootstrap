# GitHub Workflows

This is the machine-surface index for the fifteen workflow files shipped by the
template. GitHub is the control plane; investigation, implementation, and
independent review run in the local execution plane. Generated projects extend
project CI only after their commands and trust policy are known.

## Machine index

| File / workflow name | Trigger | Actor | Input | State transition | Permissions | Side effects | Check | Failure | Recovery |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `00-baseline-check.yml` / Baseline Check | push or PR to `main`; weekly; manual | pusher, PR actor, scheduler, dispatcher | trusted checkout | none | `contents: read` | summary only | Configuration Baseline | missing asset or invalid workflow YAML fails | restore the asset/YAML and rerun |
| `00-issue-intake.yml` / Issue Intake Surface | Issue opened or reopened | Issue author/editor | Issue labels; body is untrusted data | validates Intake only; no transition | job `contents: read` | none | Intake Surface Validation | missing/conflicting entity or state labels fail closed | correct labels, then reopen or rerun the event |
| `01-ai-development-workflow.yml` / Local Development Handoff | manual | dispatcher | Issue number | none | job `actions: read`, `contents: read`, `issues: read` | summary only; never creates `dev/*` | Local Execution Handoff | invalid input/policy or missing Issue fails | correct input/policy and dispatch again |
| `02-engineering-governance.yml` / Engineering Contract Validation | Issue opened/edited/reopened; Issue comment; manual | event actor or dispatcher; policy resolves trusted capability | Candidate number, operation, source comment ID | gate/review/approve/ready attestation according to current state and rollout mode | read job: Actions/content/Issues read; gated job: Issues write | in `warn`/`enforce`, mode-limited attestation comment/body or label mutation after revalidation | Engineering Contract Validation; Governance Attestation Mutation | invalid contract, actor, event, receipt, or state fails closed | repair evidence/contract or submit a new authorized command; rerun |
| `03-engineering-promotion.yml` / Engineering Contract Promotion | Issue comment; Issue opened; manual | event actor or dispatcher; trusted promoter required | Candidate number, gate/promote operation, source comment ID | Candidate promotion creates/binds a Formal Engineering Issue only in an allowed mode | read job: Actions/content/Issues read; gated job: Issues write | in `warn`/`enforce`, mode-limited promotion mutation after revalidation | Engineering Promotion Validation; Engineering Promotion Mutation | invalid actor, Candidate, receipt, or promotion state fails closed | repair Candidate/evidence or submit a new authorized `/promote` command |
| `05-commit-lint.yml` / Lint Commit Messages | PR to `main` | PR actor | PR commit range | none | `contents: read`, `pull-requests: write` | a nonconformance warning comment may be added | Commitlint | lint is advisory; Action/API errors can fail the job | amend commits for conformance, or rerun after an Action/API error |
| `10-pr-ai-review.yml` / PR Engineering Contract | PR opened/edited/synchronized/reopened | PR actor | PR body, linked Engineering Issue, ready receipt, base SHA | none | Actions/content/Issues/PR read | none | Engineering Contract Validation | missing/stale binding, authorization evidence, or required Actions read fails closed | update contract/receipt/PR binding, then synchronize or reopen |
| `20-ci-build-test.yml` / CI: Configuration Validation | push to `main`, `develop`, `dev/**`; PR to `main`, `develop`; manual | pusher, PR actor, dispatcher | repository checkout and commit range | none | `contents: read` | runner-local validation only; no repository or GitHub mutation | Configuration Validation; Security Scanning | repository, attribution, dependency, Action-pin, or security validation fails | fix the finding and rerun |
| `50-milestone-review.yml` / Milestone Review Capture | manual | dispatcher | numeric milestone number | none | checks/content/Issues/PR read | canonical artifact; zero repository writes | Milestone Review Capture | invalid evidence fails closed | correct evidence and redispatch |
| `51-milestone-review-provisional.yml` / Milestone Review Provisional | completed 50 run | authoritative upstream actor | exact upstream run/artifact | provisional only | Resolve Source: Actions/content read; Provisional: Actions/checks/content/PR read and Issues write | canonical provisional Issue and artifact | Resolve Source; Milestone Review Provisional | provenance mismatch fails | rerun capture |
| `52-milestone-review-finalize.yml` / Milestone Review Finalize | completed 51 run | authoritative upstream actor | exact upstream run/artifact | provisional to final review | Resolve Source: Actions/content read; Finalize: Actions/content read and Issues write | final review and receipt | Resolve Source; Milestone Review Finalize | mismatch pauses | reconcile provisional |
| `53-milestone-acceptance-intent.yml` / Milestone Acceptance Intent | Issue comment | trusted acceptor | exact `/accept-milestone` | none | Actions/checks/content/Issues/PR read | intent artifact; zero repository writes | Milestone Acceptance Intent | invalid contract fails | create fresh exact command |
| `54-milestone-acceptance-execute.yml` / Milestone Acceptance Execute | completed 53 run | authoritative upstream actor | exact upstream run/artifact | single Milestone close | Resolve Source: Actions/content read; Execute: Actions/checks/content/PR read and Issues write | atomic state+description marker and execution artifact | Resolve Source; Milestone Acceptance Execute | unknown close pauses; never reopens | reconcile authoritative milestone |
| `55-milestone-acceptance-finalize.yml` / Milestone Acceptance Finalize | completed 54 run | authoritative upstream actor | exact upstream run/artifact and description marker | acceptance finalized | Resolve Source: Actions/content read; Finalize: Actions/content read and Issues write | canonical completion comment | Resolve Source; Milestone Acceptance Finalize | receipt mismatch pauses | reconcile exact bot receipt |
| `40-stale.yml` / Stale Issue & PR Management | daily; manual | scheduler or dispatcher | repository Issues/PRs | legacy stale mark/close rules | `issues: write`, `pull-requests: write` | labels, comments, and closes eligible items | Mark stale / close | Action/API error fails | correct configuration/labels and rerun |

Both `02-engineering-governance.yml` and `10-pr-ai-review.yml` expose a job with
the same job name, `Engineering Contract Validation`. GitHub required-check
configuration is context-sensitive; observe successful runs for the intended
event and branch and confirm the exact context before binding a ruleset. This
index does not claim that a local file name alone disambiguates that context.

## Conventions and synchronization

- Third-party actions use a full 40-character commit SHA.
- Every job has finite `timeout-minutes` and the minimum permissions its current
  behavior requires.
- `.github/SETTINGS.md` is a human runbook, never machine policy or proof of
  remote state. `.github/project-policy.yml` is the machine trust policy.
- Required-file lists in `00-baseline-check.yml`, `10-pr-ai-review.yml`, and
  `20-ci-build-test.yml` must change together with reusable assets.
- Milestone review records only observed evidence. Documentation remains
  `human-confirmation-required`; no workflow claims an audit it did not run.

Run the canonical local verification before proposing a change:

```bash
.github/scripts/test-governance.sh
git diff --check
```

When a workflow name, trigger, permission, side effect, job/check name, or
recovery path changes, update this index in the same change. Add a ruleset check
only after it has appeared successfully in Actions and the separately confirmed
remote read-back matches the intended context.

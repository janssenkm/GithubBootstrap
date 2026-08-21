# GitHub Workflows

This template supplies a language-neutral collaboration and repository-safety
baseline. Generated projects may add source code and should extend these
workflows for their own build, test, documentation, and release requirements.

## Included baseline

| File | Workflow name | Trigger | Required check |
| ---- | ------------- | ------- | -------------- |
| `00-baseline-check.yml` | Baseline Check | push/PR `main`, weekly, manual | — |
| `00-issue-ai-triage.yml` | Issue Triage & Assignment | issue events and comments | — |
| `01-ai-development-workflow.yml` | Development Workflow | `/ready-for-dev` by write-access users, manual | — |
| `05-commit-lint.yml` | Lint Commit Messages | PR to `main` | — |
| `10-pr-ai-review.yml` | PR Quality | PR | — |
| `20-ci-build-test.yml` | CI: Configuration Validation | push `main,develop,dev/**`; PR `main,develop` | **Configuration Validation**, **Security Scanning** |
| `30-milestone-acceptance.yml` | Milestone Acceptance & Human Review | milestone closed, manual | — |
| `40-stale.yml` | Stale Issue & PR Management | daily, manual | — |

The baseline validates these reusable assets:

- `README.md` and `CONTRIBUTING.md`, both of which generated projects must
  rewrite for their own requirements and design;
- `BOOTSTRAP.md`, the first-run playbook for generated projects and template
  publication;
- `AGENTS.md` with repository-wide development instructions;
- issue/PR templates, setup guidance, dependency configuration, and workflows.

It intentionally permits any additional project files and directories.

## Conventions

- Third-party actions use a full 40-character commit SHA.
- Workflows declare minimum permissions and a finite `timeout-minutes` value.
- Required check names stay synchronized with each repository's branch ruleset.
- `Configuration Validation` blocks commits whose author, committer, or
  `Co-authored-by` attribution uses `claude` as a case-insensitive independent
  word or identifier. It checks declared Git attribution, not tool use.
- Development branches use `dev/*`; the automated workflow creates
  `dev/issue-*` branches.
- `.github/settings.yml` is a recommendation checklist, not a remote-state
  ledger.

## Project-specific workflows

Release, CodeQL, and documentation synchronization are not included in the
language-neutral baseline. Enable them in each generated project only after the
following prerequisites are known:

- Release: build inputs, artifact format, versioning, and publishing target;
- CodeQL: supported project languages and build mode;
- documentation synchronization: source paths, destination, and real app
  credentials.

Pin every added action to a full commit SHA. Give every job a finite timeout and
the minimum permissions it needs. Add a check to the branch ruleset only after it
has appeared successfully in Actions.

To run the same attribution check locally for a pull-request range:

```bash
BASE_SHA="<base-sha>"
HEAD_SHA="<head-sha>"
.github/scripts/check-commit-attribution.sh "$BASE_SHA..$HEAD_SHA"
```

## Changing the baseline

When changing reusable assets or automation:

1. update the required-file lists in `00-baseline-check.yml`,
   `10-pr-ai-review.yml`, and `20-ci-build-test.yml` when applicable;
2. update this index if a workflow name, trigger, or required check changes;
3. update `README.md`, `CONTRIBUTING.md`, `BOOTSTRAP.md`, or
   `.github/settings.yml` when their instructions change;
4. validate YAML and action references locally;
5. open a pull request and verify all relevant checks before merging.

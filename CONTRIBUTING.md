# Contributing to the GitHub Project Template

This guide applies only to maintaining `janssenkm/GithubBootstrap`. Contributions
should keep the template broadly reusable across personal accounts and
organizations, without assuming a target project's language, build system, or
ownership.

> [!IMPORTANT]
> A repository generated from this template must rewrite `CONTRIBUTING.md` for
> its own requirements, design process, development environment, tests, and
> review policy. Do not retain these template-maintenance rules as if they were
> the target project's contribution guide.

## Propose a change

Open an Intake Issue that states the reusable problem and available evidence.
Non-trivial implementation requires a promoted Engineering Issue whose frozen
contract records scope, non-goals, evidence, risks, tests, and falsifiable
acceptance criteria. Small typo or link fixes may go directly to a focused pull
request.

For approved implementation work:

1. after work is authorized, create a focused branch through the local
   development process; `/ready-for-dev` does not create a branch;
2. change only the files needed by the issue;
3. keep third-party workflow actions pinned to full commit SHAs;
4. update [`BOOTSTRAP.md`](BOOTSTRAP.md), `.github/WORKFLOWS.md`, and
   `.github/SETTINGS.md` together when initialization behavior changes;
5. run the relevant validation before opening a pull request.

Automated cloud-agent development is not part of this template. GitHub is the
GitHub control plane; local agents are the local execution plane. The governance
workflows validate Intake, contracts, attestations, promotion, and PR binding.
The legacy-named local-handoff workflow is a read-only manual notice and does
not create a branch or mutate an Issue.

## Validate locally

From the repository root:

```bash
.github/scripts/test-governance.sh
git diff --check
```

The stable repository checks are `Configuration Validation` and `Security
Scanning`. Contract workflows also expose `Engineering Contract Validation`;
read [`.github/WORKFLOWS.md`](.github/WORKFLOWS.md) before binding that shared
job name to a ruleset context. If a required name changes, update the ruleset
through the separately confirmed remote process.

## Human attribution policy

Tools, including Claude, may assist with a contribution, but Git attribution
must identify the human who is responsible for it. Do not use `claude` as an
independent, case-insensitive word or identifier in a new commit's author name,
author email, committer name, committer email, or `Co-authored-by` trailer.

This policy does not attempt to detect undisclosed tool use. Mentioning Claude
in documentation or ordinary commit-message prose is allowed. Validate the
commits being proposed with:

```bash
BASE_SHA="<base-sha>"
HEAD_SHA="<head-sha>"
.github/scripts/check-commit-attribution.sh "$BASE_SHA..$HEAD_SHA"
```

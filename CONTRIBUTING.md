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

Open an issue that states the reusable problem, scope, alternatives, and
verifiable acceptance criteria. Small typo or link fixes may go directly to a
focused pull request.

For approved implementation work:

1. create a focused branch, normally through the included `/ready-for-dev`
   workflow;
2. change only the files needed by the issue;
3. keep third-party workflow actions pinned to full commit SHAs;
4. update [`BOOTSTRAP.md`](BOOTSTRAP.md), `.github/WORKFLOWS.md`, and
   `.github/settings.yml` together when initialization behavior changes;
5. run the relevant validation before opening a pull request.

## Validate locally

From the repository root:

```bash
python3 - <<'PY'
from pathlib import Path
import yaml

for path in Path('.github/workflows').glob('*.yml'):
    yaml.safe_load(path.read_text())
PY
git diff --check
```

The required GitHub checks remain `Configuration Validation` and `Security
Scanning`. If their job names change, update the branch ruleset at the same time.

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

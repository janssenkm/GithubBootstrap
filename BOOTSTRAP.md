# Bootstrap a GitHub Project

This is the first-run guide for `janssenkm/GithubBootstrap`. It covers two
different scenarios:

1. **Initialize a generated project** — the primary workflow: collect the
   project's requirements and design, materialize its files, validate them, and
   configure its GitHub repository.
2. **Maintain or publish the template source** — prepare
   `janssenkm/GithubBootstrap` itself for reuse.

Decide which scenario applies before changing files or GitHub state.

## Start a generated project with an agent

Give a coding agent this exact instruction from the generated repository root:

> Read `BOOTSTRAP.md` completely. First perform a read-only inventory and decide
> whether this is the template source or a generated project. Ask for missing
> inputs in small batches, then preview a phased plan. Do not write local files
> until I confirm that plan. Preview and obtain separate confirmation before any
> GitHub remote write.

This repository currently provides an **agent-run playbook using `git` and
`gh`**. It does not contain a zero-touch bootstrap script or a workflow that
performs the initialization automatically.

## Prerequisites

For a generated project, first create a repository from the accessible
`janssenkm/GithubBootstrap` template, using **Use this template** or GitHub CLI.
The owner may be a personal account or an organization where the caller can
create repositories:

```bash
OWNER="<owner>"
REPO="<repo>"
gh repo create "${OWNER}/${REPO}" \
  --template janssenkm/GithubBootstrap \
  --public \
  --clone
```

Replace `--public` if the confirmed visibility differs and owner policy permits
it. Template accessibility and owner policy determine whether cross-user or
cross-organization generation is available.

Before executing the playbook, normally ensure that:

- `git` and `gh` are available, and `gh auth status` identifies the intended
  account;
- `python3` with PyYAML is available for the template's existing workflow YAML
  validation; the target project also needs the tools required by its selected
  language and build, test, lint, and security commands;
- the operator has the repository permissions needed for each planned remote
  operation (organization policy may restrict some settings);
- the checkout points to the intended owner and repository;
- the working tree is clean, or every pre-existing change has been identified
  and preserved;
- Actions and repository features allowed by the owner have been reviewed.

Do not assume that template generation copied GitHub settings. It copies the
directories and files from the default branch, or from all branches when that
option is explicitly selected. The generated repository starts with new commit
history and does not inherit the template repository's commit history. It also
does not inherit repository settings, labels, milestones, Projects,
environments, rulesets, branch protection, Secrets, variables, credentials, or
installed apps.

## Inputs to collect

Collect these required inputs before proposing implementation:

- repository owner, name, visibility, and description;
- project goal, target users, MVP scope, and explicit non-goals;
- requirements and design decisions, including any supplied source documents;
- language, runtime, framework, dependency, and supported-platform choices;
- build, test, lint, formatting, and security commands or intended baseline;
- license choice;
- `CODEOWNERS`, review policy, merge policy, and other governance decisions;
- documentation, release, versioning, packaging, and distribution expectations.

Collect optional inputs only when relevant:

- Discussions, Wiki, Projects, environments, milestones, issue forms, or extra
  labels;
- deployment, release signing, CodeQL, documentation publishing, or external
  integrations;
- AI-assisted triage and its provider/model configuration.

Never ask the user to paste a Secret into chat, an issue, a pull request, or a
repository file. Record only Secret names and whether they are configured.

## Confirmation and safety boundaries

- Read-only inventory and validation need no confirmation.
- Before local file writes, show one concrete plan listing files to create,
  rewrite, or preserve, and obtain one confirmation for that local batch.
- A Git commit is a local operation. Create one only when it is explicitly
  included in the confirmed local plan, or after separately previewing and
  confirming it.
- Before any GitHub remote write, show the exact target and operation, then
  obtain separate confirmation. This includes repository settings, labels,
  rulesets, Secrets, variables, environments, pushes, and creation of issues,
  pull requests, releases, or other remote objects.
- Never silently overwrite non-template content. Show a diff or explain the
  conflict and ask how it should be resolved.
- Do not push or open a pull request unless those actions were included in an
  explicitly confirmed remote plan.

## Initialization stages

Run these stages in order. Stop at a failed stage and report the last completed
stage, the failure, and the safe resume point.

1. **Detect** — identify the repository, remotes, branch, working-tree state,
   copied template assets, existing project files, available tools, and current
   read-only GitHub state. Verify that the target and scenario are unambiguous.
2. **Collect** — gather the required inputs above in small batches; identify
   optional inputs that affect the plan. Verify that unresolved choices are
   listed rather than guessed.
3. **Plan** — preview local changes, remote operations, commands, confirmation
   points, and acceptance checks. Obtain confirmation for the local batch.
4. **Local materialization** — rewrite `README.md` and `CONTRIBUTING.md` for the
   target project; customize `AGENTS.md`, `.github/CODEOWNERS`,
   `.github/settings.yml`, and workflows; create requirements and design
   documents only as confirmed. Preserve this guide until bootstrap is complete,
   then keep or adapt it according to the approved documentation plan.
5. **Local validation** — run the selected formatter, lint, build, test, and
   security checks plus workflow YAML, link, and commit-attribution validation.
   Review the complete diff and verify that no Secret or unrelated content is
   present. If a local commit is confirmed, preserve the responsible human's
   author and committer identity, do not add Claude as a `Co-authored-by`, and
   validate the resulting commit or range with
   `.github/scripts/check-commit-attribution.sh`. This check enforces declared
   Git attribution; it does not detect tool use. Create a local commit only if
   it has the local confirmation required above.
6. **Remote initialize** — preview and confirm the remote batch, then apply the
   approved repository features, merge settings, Actions policy, labels, and
   optional integrations. Verify each setting by reading it back.
7. **First checks** — after an approved local commit, when applicable, and a
   separately confirmed push, observe the first workflow runs. Verify the exact
   required check names
   `Configuration Validation` and `Security Scanning` before depending on them.
8. **Ruleset** — preview and confirm the ruleset operation, then protect `main`
   with the approved review and required-check policy. Read back the active
   ruleset and avoid copying identifiers from the template source.
9. **Handoff** — report initialized files and remote state, validation results,
   remaining choices, intentionally deferred work, and exact next steps.

The recommendations and example commands in [`.github/settings.yml`](.github/settings.yml)
support the remote stages. Adapt them to the confirmed target and governance;
do not treat that file as proof of remote state.

## Configure Secrets safely

Configure only confirmed Secret names, after separate approval for the remote
write. Use an interactive terminal so the value is not placed in arguments or
logs:

```bash
REPOSITORY="<owner>/<repo>"
gh secret set AI_API_KEY --repo "$REPOSITORY"
```

If AI triage is enabled, configure `AI_API_MODEL` and `AI_API_BASEURL` the same
way rather than storing their values in a file.

Alternatively, pipe a value obtained from an approved secure environment or
secret manager into `gh secret set`, and immediately unset temporary shell
variables. Never echo, persist, print, or include the value in a report. The
optional AI triage uses its rule-based fallback when its Secret names are not
configured.

## Repeat runs and recovery

The playbook is designed to be safely repeatable, not blindly replayed. On every
run, detect current local and remote state first, skip outcomes already
satisfied, preview differences, and preserve user-authored content. Do not
replace a changed file merely because it originally came from the template.
After a failure, resume from the first incomplete stage once its cause is
resolved; do not redo successful remote writes without comparing state.

## Completion standard

Bootstrap is complete only when:

- project identity, goal, users, MVP, non-goals, requirements, and design are
  represented in the approved project documentation;
- project-specific `README.md`, `CONTRIBUTING.md`, agent guidance, ownership,
  settings guidance, and CI reflect the agreed project;
- approved local checks pass and the diff contains no Secrets;
- approved repository settings and labels have been read back;
- first Actions runs succeed and the active `main` ruleset references observed
  required checks, when rulesets are in scope;
- deferred items and any permission or organization-policy limitations are
  explicit.

The final report must identify the repository and commit/branch examined,
summarize local and remote changes separately, list validation evidence without
Secret values, describe anything skipped or failed, and give the next action.

## Maintain and publish the template source

Use this section only for `janssenkm/GithubBootstrap`, not for a generated
project. From a clean directory with no existing `.git` history, the initial
publication sequence is:

```bash
git init -b main
git add -- README.md CONTRIBUTING.md AGENTS.md BOOTSTRAP.md .github
git commit -m "chore: initialize reusable GitHub project template"
gh repo create janssenkm/GithubBootstrap \
  --public \
  --source=. \
  --remote=origin \
  --push
```

Treat `git init`, `git add`, and `git commit` as the local batch. Treat
`gh repo create --push` as a separate remote batch; preview and confirm each
batch under the boundaries above before running it.

Do not use `git init` as a way to erase an existing history. Back up and prepare
a separate clean directory if a repository rebuild is intentionally required.

Mark the published repository as a template only after previewing and confirming
the remote write:

```bash
gh api --method PATCH repos/janssenkm/GithubBootstrap -F is_template=true
```

Then apply [`.github/settings.yml`](.github/settings.yml) to
`janssenkm/GithubBootstrap`, create its automation labels, run its workflows,
and configure the `main` ruleset only after GitHub has observed
`Configuration Validation` and `Security Scanning`. Do not add placeholder
Secrets. Template maintainers must keep this guide, required-file lists, and
[`.github/WORKFLOWS.md`](.github/WORKFLOWS.md) synchronized when the bootstrap
contract changes.

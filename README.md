# Issue-Driven Multi-Agent Engineering Governance Template

This repository is a reusable starting point for projects that separate a
GitHub control plane from a local execution plane. GitHub Issues, pull requests,
Actions, rulesets, and milestones retain contracts and evidence; replaceable
local agents investigate, implement, review, and verify. Deterministic checks
and human authorization—not an agent's assertion—decide completion.

> [!IMPORTANT]
> This README describes the template repository itself. After creating a target
> project from the template, rewrite `README.md` for that project's requirements,
> design, setup, and usage. Do not present this template description as the
> target project's documentation.

## Use this template

The published template source is `janssenkm/GithubBootstrap`. Create a
repository under any personal account or organization where you have permission:

```bash
OWNER="replace-with-owner"
REPO="replace-with-repository"
gh repo create "${OWNER}/${REPO}" \
  --template janssenkm/GithubBootstrap \
  --public \
  --clone
```

The GitHub web interface offers the same owner selection through **Use this
template**. After generation, start with [`BOOTSTRAP.md`](BOOTSTRAP.md) and use
its agent-assisted flow to collect project inputs, preview changes, and perform
the confirmed local and GitHub initialization. The same guide also documents
publishing and maintaining the template source.

## Included baseline

- `.github/`: Intake forms, Engineering Issue contracts, deterministic gates,
  dependency updates, and setup docs;
- `.agent/` and `.agents/skills/`: one shared policy index and canonical local
  role procedures for supported agent hosts;
- `BOOTSTRAP.md`: the first-run playbook for generated projects and template
  publication;
- `CONTRIBUTING.md`: maintenance guidance for this reusable template;
- `AGENTS.md`: repository-wide instructions for coding agents.

Local implementation is authorized only after an Engineering Issue is
promoted and contracted, receives human-approved `ready` state, and has a valid
ready receipt. Public
Issue Forms collect Intake input; trusted actors and independent review control
promotion. See
[`docs/engineering-governance-v2.md`](docs/engineering-governance-v2.md)
for the design and [`.github/WORKFLOWS.md`](.github/WORKFLOWS.md) for the exact
machine surfaces.

Generated repositories may add source code, documentation, tests, and any other
project files. After generation, replace the placeholder ownership guidance in
`.github/CODEOWNERS`, review `.github/SETTINGS.md`, and adapt project-specific
checks to the selected language and release process.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes to this
template.

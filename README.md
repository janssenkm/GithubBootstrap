# GitHub Project Template

This repository is a reusable starting point for public GitHub projects. It
provides issue and pull-request templates, pinned GitHub Actions workflows,
recommended repository settings, and shared agent instructions.

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

- `.github/`: issue/PR templates, automation, dependency updates, and setup docs;
- `BOOTSTRAP.md`: the first-run playbook for generated projects and template
  publication;
- `CONTRIBUTING.md`: maintenance guidance for this reusable template;
- `AGENTS.md`: repository-wide instructions for coding agents.

Generated repositories may add source code, documentation, tests, and any other
project files. After generation, replace the placeholder ownership guidance in
`.github/CODEOWNERS`, review `.github/settings.yml`, and adapt project-specific
checks to the selected language and release process.

See [`CONTRIBUTING.md`](CONTRIBUTING.md) before proposing changes to this
template.

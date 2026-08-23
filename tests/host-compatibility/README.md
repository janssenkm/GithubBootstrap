# Slice 1 compatibility evidence

Recorded on 2026-08-22. This file records reproducible commands and observed
outcomes; it does not claim support that the installed host could not prove.

## Runtime and dependency resolution

CPython `3.14.4` is fixed in `.python-version`. Python's official support table
lists 3.14 in bugfix support through 2030-10, the official Actions Python
manifest marks 3.14.4 stable and provides Linux x64 builds for Ubuntu 22.04 and
24.04, and the Ubuntu 24.04 runner toolset includes `3.14.*`:

- <https://devguide.python.org/versions/>
- <https://raw.githubusercontent.com/actions/python-versions/main/versions-manifest.json>
- <https://github.com/actions/runner-images/blob/main/images/ubuntu/toolsets/toolset-2404.json>

The direct dependencies were not yanked in PyPI metadata and their release
tags resolved to these official-source commits:

| Package | Version | License | Official source tag commit |
| --- | --- | --- | --- |
| jsonschema | 4.26.0 | MIT | `a7277432b0f7bcd0551f6e589d30457017125df4` |
| rfc8785 | 0.1.4 | Apache-2.0 | `4d9b161f6054301d98d0566e813d020fb019ee10` |
| PyYAML | 6.0.3 | MIT | `49790e73684bebad1df05ef8d828fa12f685bffb` |
| pytest | 9.1.1 | MIT | `cf470ec0bf7eb89cd97dd56df4859eae5db46447` |
| pip-audit | 2.10.1 | Apache-2.0 | `8894eb8cee033531a1fbd9f2fb160892531c14e3` |

The 51-package resolved graph was inspected from installed wheel metadata.
Declared licenses were MIT, Apache-2.0, BSD-2-Clause, BSD-3-Clause, ISC,
MPL-2.0, PSF, or compatible dual-license forms. `isoduration` reports
`UNKNOWN` in its installed metadata, so its wheel's bundled `LICENSE` was
checked directly and is ISC. `python-dateutil` reports `Dual License`; its
official `LICENSE` states that the BSD-3-Clause terms apply to all code, with
relicensed/newer contributions also under Apache-2.0. No GPL dependency is
selected; `jsonschema[format-nongpl]` is intentional.

The audit command run in a clean venv was:

```bash
python -m pip_audit --local --progress-spinner off
```

It exited 0 with `No known vulnerabilities found`. This is a point-in-time
advisory result, not a claim that the graph has no undisclosed vulnerability.

## Action and workflow tooling

The intended upstream Action tags were resolved with `git ls-remote`, including
the peeled commit for the annotated `actions/github-script` tag. They are
recorded here for later workflow slices and are not inserted by Slice 1:

| Action | Tag | Full commit |
| --- | --- | --- |
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/setup-python | v6.2.0 | `a309ff8b426b58ec0e2a45f0f869d46889d02405` |
| actions/github-script | v9.0.0 | `3a2844b7e9c422d3c10d287c895573f7108da1b3` |

Release review found that checkout v7 defaults to refusing unsafe fork code in
privileged trigger contexts, setup-python v6 uses Node 24 and requires runner
v2.327.1 or later, and github-script v9 upgrades `@actions/github` and its
Octokit interface. The actions do not grant repository permission by
themselves; later workflows must retain explicit least-privilege job scopes.

`actionlint` is locked to v1.7.12. Its official Linux x86_64 release checksum is
`8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8`:
<https://github.com/rhysd/actionlint/releases/tag/v1.7.12>. The test entry point
verifies this checksum before extracting or executing the binary, then passes
every workflow path to it.

## Disposable host probe

The probe uses a body-only marker to distinguish Skill-body loading from Skill
metadata discovery. Create the disposable fixtures once:

```bash
mkdir -p /tmp/github-governance-host-codex/.agents/skills
mkdir -p /tmp/github-governance-host-codex/canonical-skill
printf '%s\n' '# Disposable host compatibility constitution' \
  'When asked for the constitution marker, answer `CODEX_CONSTITUTION_7B9E`.' \
  > /tmp/github-governance-host-codex/AGENTS.md
printf '%s\n' '---' 'name: slice1-probe' \
  'description: Use when explicitly asked to invoke the Slice 1 compatibility probe.' \
  '---' 'When invoked, report the body-only marker `CODEX_SKILL_BODY_4D2A`.' \
  > /tmp/github-governance-host-codex/canonical-skill/SKILL.md
test -L /tmp/github-governance-host-codex/.agents/skills/slice1-probe || \
  ln -s ../../canonical-skill /tmp/github-governance-host-codex/.agents/skills/slice1-probe

mkdir -p /tmp/github-governance-host-opencode/.agents/skills/slice1-probe
printf '%s\n' '# Disposable host compatibility constitution' \
  'When asked for the constitution marker, answer `OPENCODE_CONSTITUTION_9C31`.' \
  > /tmp/github-governance-host-opencode/AGENTS.md
printf '%s\n' '---' 'name: slice1-probe' \
  'description: Use when explicitly asked to invoke the Slice 1 compatibility probe.' \
  '---' 'When invoked, report the body-only marker `OPENCODE_SKILL_BODY_A806`.' \
  > /tmp/github-governance-host-opencode/.agents/skills/slice1-probe/SKILL.md

mkdir -p /tmp/github-governance-host-claude/.agents/skills/slice1-probe
printf '%s\n' '# Disposable host compatibility constitution' \
  'When asked for the constitution marker, answer `CLAUDE_CONSTITUTION_15F0`.' \
  > /tmp/github-governance-host-claude/AGENTS.md
printf '%s\n' '@AGENTS.md' > /tmp/github-governance-host-claude/CLAUDE.md
printf '%s\n' '---' 'name: slice1-probe' \
  'description: Use when explicitly asked to invoke the Slice 1 compatibility probe.' \
  '---' 'When invoked, report the body-only marker `CLAUDE_AGENTS_SKILL_62BC`.' \
  > /tmp/github-governance-host-claude/.agents/skills/slice1-probe/SKILL.md
```

The fixture contents used for the recorded run are intentionally shown by the
markers in the commands and results below. They contain no repository policy.

### Codex

Primary documentation:
<https://developers.openai.com/codex/skills> and
<https://developers.openai.com/codex/guides/agents-md>.

```bash
codex --version
codex exec --ephemeral --skip-git-repo-check --sandbox read-only \
  --cd /tmp/github-governance-host-codex --json \
  'Report the constitution marker, then invoke the slice1-probe skill and report its body-only marker. Do not inspect files with shell commands.'
```

Observed with `codex-cli 0.149.0`: exit 0. `AGENTS.md` supplied
`CODEX_CONSTITUTION_7B9E`; the Skill body was then read through the symlinked
`.agents/skills/slice1-probe` folder and supplied `CODEX_SKILL_BODY_4D2A`. The
event order showed constitution context before the requested Skill-body read,
demonstrating progressive disclosure rather than eager body inclusion.

### OpenCode

Primary documentation: <https://opencode.ai/docs/skills/> and
<https://opencode.ai/docs/rules/>.

```bash
opencode --version
opencode run --dir /tmp/github-governance-host-opencode --format json \
  'Report the constitution marker, then invoke the slice1-probe skill and report its body-only marker. Do not inspect files with shell commands.'
```

Observed with OpenCode `1.18.20`: exit 1 before discovery because the configured
provider returned HTTP 401 (`AuthN_MissOrInvalidAuthorizationHeader`). Root
`AGENTS.md` and `.agents/skills` discovery therefore remain **blocked**, not
passed. V1 must not add an OpenCode-specific adapter; authenticated rerun of
this exact probe is required before OpenCode support is claimed. Until then,
the documented canonical `.agents/skills` layout is the only fallback path.

### Claude Code

Primary documentation: <https://code.claude.com/docs/en/memory> and
<https://code.claude.com/docs/en/slash-commands>.

```bash
claude --version
(cd /tmp/github-governance-host-claude && \
  claude -p 'Report the constitution marker exactly. Do not use tools.' \
    --no-session-persistence --tools '')
(cd /tmp/github-governance-host-claude && \
  claude -p '/slice1-probe' --no-session-persistence)
mkdir -p /tmp/github-governance-host-claude/.claude/skills
test -L /tmp/github-governance-host-claude/.claude/skills/slice1-probe || \
  ln -s ../../.agents/skills/slice1-probe \
    /tmp/github-governance-host-claude/.claude/skills/slice1-probe
(cd /tmp/github-governance-host-claude && \
  claude -p '/slice1-probe' --no-session-persistence)
```

Observed with Claude Code `2.1.232`: the `CLAUDE.md` import probe exited 1 with
`Not logged in · Please run /login`, so import content was not demonstrated.
The direct `.agents/skills` invocation returned `Unknown command:
/slice1-probe`, proving that this installed release does not discover the
canonical folder directly. After a `.claude/skills` symlink was added, the same
invocation advanced to the authentication check and exited 1 with `Not logged
in`, which demonstrates symlink-folder discovery but not Skill-body loading.
The selected Slice 7 fallback therefore remains a thin `CLAUDE.md` importing
`AGENTS.md` plus a generated copy in the documented `.claude/skills` location,
with a repository drift test. Symlink use remains disallowed until an
authenticated probe proves complete loading.

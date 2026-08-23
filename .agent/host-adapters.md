# Host adapter policy

`AGENTS.md`, `.agent/`, and `.agents/skills/` are canonical. Host adapters may
only import the constitution or mechanically mirror a canonical Skill with a
byte-for-byte drift test. They must not contain competing governance prose.

Codex discovery of root `AGENTS.md`, progressive Skill loading, and symlinked
canonical Skill folders was demonstrated by the recorded Slice 1 probe.
OpenCode uses its documented root `AGENTS.md` and `.agents/skills` paths, but
the installed probe was blocked by provider authentication; V1 must not claim
an authenticated discovery result.

Claude uses a regular thin `CLAUDE.md` containing only `@AGENTS.md`. Direct
`.agents/skills` discovery was not observed. Until an authenticated probe proves
complete symlink loading, `.claude/skills/` contains generated regular-file
mirrors checked byte-for-byte against canonical Skills. Authentication failure
must be reported as blocked, not support.

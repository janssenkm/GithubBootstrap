# Verification policy

Define each acceptance criterion with a command, observation, or artifact that
can falsify it. Run the narrowest reproducer first, then all required regression
and repository checks. Record command, exit code, and an exact result summary.

Review the diff for scope, generated-file drift, credentials, attribution, and
unrelated changes. LSP diagnostics and agent judgments may guide work but do
not replace deterministic CLI or CI evidence.

Report states precisely:

- `implemented`: artifacts changed but required verification is incomplete;
- `verified`: every fixed acceptance command passed and artifacts were checked;
- `failed`: a required check disproved the result;
- `blocked`: required evidence or authority is unavailable.

Only `verified` may be described as complete. Never weaken acceptance criteria
after seeing results without explicit user approval.

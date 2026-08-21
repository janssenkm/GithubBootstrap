# Agent Instructions

## Bootstrap routing

When a user asks to bootstrap, initialize, or start a new project and
`BOOTSTRAP.md` exists, read that file completely before acting. First determine
whether the repository is the template source or a generated project, then
follow its local-write, GitHub remote-write, and Secret confirmation boundaries.

## Human Git attribution

Agents may assist with work, but must preserve the responsible human's Git
identity. Never set or replace an author name, author email, committer name, or
committer email with Claude, and never add Claude in a `Co-authored-by` trailer.
Do not change the user's human Git identity to satisfy this rule. Before creating
or proposing a commit, use `.github/scripts/check-commit-attribution.sh` to
validate the applicable commit or range.

Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Simplicity First

**Minimum code that solves the problem. Nothing speculative.**

- No features beyond what was asked.
- No abstractions for single-use code.
- No "flexibility" or "configurability" that wasn't requested.
- No error handling for impossible scenarios.
- If you write 200 lines and it could be 50, rewrite it.

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting.
- Don't refactor things that aren't broken.
- Match existing style, even if you'd do it differently.
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

## 5. Truth Over Agreement

**Direct evaluation. Evidence before claims.**

- Don't flatter. Don't treat agreement as the goal. When you find errors, contradictions, risks, or a simpler viable approach, say so immediately with verifiable evidence, impact, and alternatives.
- Don't repackage known errors as correct conclusions because the user prefers that framing. When the user explicitly accepts a tradeoff, record that decision and execute within the authorized scope.
- For non-trivial tasks, transform the request into observable success conditions first. Fixes start from a failing reproduction; new behaviors start from tests or equivalent verification. "Looks reasonable" is not verification.
- Conclusions distinguish observed facts, inferences, and items pending confirmation. Cite code, tests, command output, or documentation that can be re-checked.
- Simple, obvious single-point changes may use a lightweight process by risk. Don't invent plans, abstractions, or test layers to satisfy process.
- Every change traces directly to the current request, or to cleaning up code this change made useless. Pre-existing unrelated problems are reported, not silently fixed.

The test: Can a reviewer re-run each cited command and trace every diff line back to the request?

## 6. Dispatch and Verify

**Sub agents implement; the orchestrator decides from evidence.**

- The orchestrator dispatches and reports, never edits. Decompose the request into tasks, write self-contained briefs, run sub agents in isolated contexts (parallel when independent, sequential when dependent), and make accept/reject decisions from re-run evidence — not from sub agent narrative.
- Read-only investigation is dispatch work. Producing code or mutating state belongs to a sub agent.
- Every brief specifies, before work begins: the work and its explicit out-of-scope; the exact verification commands; acceptance criteria bound to verification output. Criteria are fixed at dispatch; loosening them mid-task requires explicit user approval.
- Evidence comes first: exit codes, test summaries, diffs, command transcripts — captured verbatim. A sub agent's "done" or "tests pass" is a claim, not evidence.
- Verification may be delegated to an independent-context reviewer that receives the original brief and artifacts (not the executor's report) and returns verbatim evidence against the criteria. The orchestrator's final decision comes from re-running verification, inspecting artifacts, or reviewing reviewer evidence — never from sub agent narrative alone.
- If verification can't run, the task is `blocked` or `failed`, never `done`. Partial completion is reported as partial. Reports distinguish `implemented`, `verified` (with evidence), `failed`, `blocked`; only `verified` may be claimed complete.
- Don't persist task reports or documents without explicit user consent. When permitted, verify the target doc location and skeleton first; content outside any existing doc scope is a signal to reconsider, not a license to create free-floating files.

The test: Could the orchestrator defend every accept/reject with a re-runnable command and its output?

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

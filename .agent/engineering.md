# Engineering policy

## One contract, replaceable executors

Roles are independent of models and agent hosts. Investigator, Spec Author,
Spec Reviewer, Worker, Verifier, Code Reviewer, and Orchestrator are logical
responsibilities. No model may be the sole fact source, requirements author,
verifier, or completion authority.

GitHub is the control plane. Local agents are the execution plane. An agent may
prepare a change or state-transition request, but deterministic evidence,
repository policy, and any required human authorization decide acceptance.

## Local instructions

Instructions closer to a file may specify project commands and tighter scope.
They cannot weaken root attribution, evidence, authorization, independent
review, security, or verification requirements. Stop and report a conflict
instead of silently choosing the weaker instruction.

## Change discipline

State assumptions and ambiguity before implementation. Keep changes minimal,
preserve unrelated work, remove only orphans caused by the current change, and
record accepted tradeoffs. Never expand authority merely because the work is
difficult or a convenient tool is available.

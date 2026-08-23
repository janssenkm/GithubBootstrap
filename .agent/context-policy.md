# Context policy

Use evidence in this order:

1. repository code;
2. repository tests and reproducible command output;
3. dependency locks and module manifests;
4. current-version Context7 or official documentation;
5. model memory for navigation or hypotheses only.

Third-party API signatures, version-specific behavior, configuration schemas,
deprecations, security behavior, framework lifecycle, serialization, and
driver semantics require evidence for the selected version. Record the source
and version. If current authoritative evidence is unavailable, classify the
claim as unknown and hand off as blocked when it affects scope, safety, or
acceptance.

Keep Fact, Inference, Decision, Assumption, and Unknown visibly separate. Do
not convert repeated statements or plausible explanations into facts.

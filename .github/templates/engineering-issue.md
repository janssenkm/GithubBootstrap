<!--
Internal promotion renderer input. Do not create or authorize an Engineering
Issue by copying this file or selecting a template. The promotion workflow
must render the verified Candidate contract, add provenance, set contracted
lifecycle fields, recompute the full hash, and read the created Issue back.
-->

# Engineering Issue: {{ title }}

Promoted from Candidate #{{ candidate_issue_number }}. This artifact is valid
only after the promotion audit and deterministic read-back succeed.

<!-- engineering-contract:start -->
```json
{{ promoted_contract_json }}
```
<!-- engineering-contract:end -->

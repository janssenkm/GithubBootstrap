from __future__ import annotations

import json

import pytest

from conftest import render_body
from github_governance.contract import END_MARKER, MAX_BODY_BYTES, MAX_PAYLOAD_BYTES, START_MARKER, extract_contract
from github_governance.errors import GovernanceError
from github_governance.__main__ import main


def test_extracts_exact_object_and_offsets(valid_body):
    extracted = extract_contract(valid_body)
    assert extracted.contract["schema_version"] == "1.0.0"
    raw = extracted.body.encode("utf-8")
    assert raw[extracted.marker_start :].startswith(START_MARKER.encode())
    assert raw[: extracted.marker_end].endswith(END_MARKER.encode())
    assert json.loads(raw[extracted.payload_start : extracted.payload_end]) == extracted.contract


def test_offsets_are_utf8_byte_offsets(valid_contract):
    extracted = extract_contract(render_body(valid_contract, before="汉字 narrative\n"))
    raw = extracted.body.encode("utf-8")
    assert raw[extracted.marker_start :].startswith(START_MARKER.encode())
    assert json.loads(raw[extracted.payload_start : extracted.payload_end]) == valid_contract


def test_lf_and_crlf_are_equivalent(valid_body):
    lf = extract_contract(valid_body)
    crlf = extract_contract(valid_body.replace(b"\n", b"\r\n"))
    assert crlf.contract == lf.contract


@pytest.mark.parametrize(
    "body,finding",
    [
        (b"narrative only", "CONTRACT-MARKER-COUNT"),
        ((END_MARKER + START_MARKER).encode(), "CONTRACT-MARKER-ORDER"),
        ((START_MARKER + "\n```JSON\n{}\n```\n" + END_MARKER).encode(), "CONTRACT-FENCE"),
        ((START_MARKER + "\n```json\n[]\n```\n" + END_MARKER).encode(), "CONTRACT-OBJECT"),
        ((START_MARKER + "\n```json\n{} {}\n```\n" + END_MARKER).encode(), "CONTRACT-JSON"),
        ((START_MARKER + "\n```json\n\n```\n" + END_MARKER).encode(), "CONTRACT-EMPTY"),
        ((START_MARKER + "\n```json\n{\"x\":NaN}\n```\n" + END_MARKER).encode(), "CONTRACT-NON-FINITE"),
        ((START_MARKER + "\n```json\n{}\n``` trailing\n" + END_MARKER).encode(), "CONTRACT-FENCE"),
        ((START_MARKER + "\n```json\n{}\n```\n```\n" + END_MARKER).encode(), "CONTRACT-RESIDUAL-FENCE"),
        ((START_MARKER + "\n```json\n{\"x\":\"```\"}\n```\n" + END_MARKER).encode(), "CONTRACT-RESIDUAL-FENCE"),
        ((START_MARKER + "\n```json\n{}\n```\n" + END_MARKER + START_MARKER).encode(), "CONTRACT-MARKER-COUNT"),
        (b"&lt;!-- engineering-contract:start --&gt;" + render_body({}), "CONTRACT-ESCAPED-MARKER"),
        (b"\xff", "CONTRACT-UTF8"),
        ((START_MARKER + "\n```json\n{\"x\":\"\\u0000\"}\n```\n" + END_MARKER).encode(), "CONTRACT-NUL"),
    ],
)
def test_parser_bypasses_fail_closed(body, finding):
    with pytest.raises(GovernanceError) as captured:
        extract_contract(body)
    assert captured.value.finding.id == finding


def test_duplicate_key_is_rejected_at_any_depth(repository_root):
    body = (repository_root / "tests/governance/fixtures/parser/duplicate-nested-key.md").read_bytes()
    with pytest.raises(GovernanceError) as captured:
        extract_contract(body)
    assert captured.value.finding.id == "CONTRACT-DUPLICATE-KEY"


def test_body_and_payload_byte_limits_are_enforced():
    with pytest.raises(GovernanceError, match="262144"):
        extract_contract(b"x" * (MAX_BODY_BYTES + 1))
    payload = b'{"x":"' + b"a" * MAX_PAYLOAD_BYTES + b'"}'
    body = START_MARKER.encode() + b"\n```json\n" + payload + b"\n```\n" + END_MARKER.encode()
    with pytest.raises(GovernanceError, match="131072"):
        extract_contract(body)


def test_deep_json_fails_as_invalid_instead_of_crashing():
    nested = "[" * 1200 + "0" + "]" * 1200
    body = (START_MARKER + "\n```json\n{\"x\":" + nested + "}\n```\n" + END_MARKER).encode()
    with pytest.raises(GovernanceError) as captured:
        extract_contract(body)
    assert captured.value.finding.id == "CONTRACT-JSON"


def test_narrative_is_retained_but_not_parsed_as_contract(valid_contract):
    body = render_body(valid_contract, before="评论 /promote must remain data.\n", after="\nReview comment text.")
    extracted = extract_contract(body)
    assert "/promote" in extracted.narrative_before
    assert extracted.contract == valid_contract


def test_benign_html_entity_in_narrative_is_allowed(valid_contract):
    assert extract_contract(render_body(valid_contract, before="Fish &amp; Chips.\n")).contract == valid_contract


@pytest.mark.parametrize(
    "fixture,finding",
    [
        ("escaped-marker-key.md", "CONTRACT-RESIDUAL-MARKER"),
        ("escaped-marker-value.md", "CONTRACT-RESIDUAL-MARKER"),
        ("escaped-fence-value.md", "CONTRACT-RESIDUAL-FENCE"),
    ],
)
def test_json_escapes_cannot_hide_markers_or_fences(repository_root, fixture, finding):
    body = (repository_root / "tests/governance/fixtures/parser" / fixture).read_bytes()
    with pytest.raises(GovernanceError) as captured:
        extract_contract(body)
    assert captured.value.finding.id == finding


def test_cli_usage_and_malformed_body_use_stable_exit_2(repository_root, capsys):
    assert main(["extract"]) == 2
    usage = json.loads(capsys.readouterr().err)
    assert usage["finding_ids"] == ["CLI-USAGE"]
    assert main(["extract", "--body-file", str(repository_root / "tests/governance/fixtures/parser/comment-source.md")]) == 2
    malformed = json.loads(capsys.readouterr().err)
    assert malformed["finding_ids"] == ["CONTRACT-MARKER-COUNT"]

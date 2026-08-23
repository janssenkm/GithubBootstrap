"""Strict extraction of the sole contract object from an Issue body."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .errors import GovernanceError


START_MARKER = "<!-- engineering-contract:start -->"
END_MARKER = "<!-- engineering-contract:end -->"
MAX_BODY_BYTES = 262_144
MAX_PAYLOAD_BYTES = 131_072

_BLOCK = re.compile(
    rb"\A[ \t\r\n]*```json[ \t]*\r?\n(?P<payload>.*?)\r?\n```[ \t]*(?:\r?\n)?[ \t\r\n]*\Z",
    re.DOTALL,
)


@dataclass(frozen=True)
class ExtractedContract:
    contract: dict[str, Any]
    body: str
    narrative_before: str
    narrative_after: str
    marker_start: int
    marker_end: int
    payload_start: int
    payload_end: int

    def as_dict(self) -> dict[str, Any]:
        return {
            "contract": self.contract,
            "offsets": {
                "marker_start": self.marker_start,
                "marker_end": self.marker_end,
                "payload_start": self.payload_start,
                "payload_end": self.payload_end,
            },
            "narrative_before": self.narrative_before,
            "narrative_after": self.narrative_after,
        }


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GovernanceError("CONTRACT-DUPLICATE-KEY", f"duplicate JSON object key: {key}")
        result[key] = value
    return result


def _reject_constant(value: str) -> None:
    raise GovernanceError("CONTRACT-NON-FINITE", f"non-finite JSON number is forbidden: {value}")


def _forbidden_scalar(value: int) -> bool:
    return (
        0xD800 <= value <= 0xDFFF
        or 0xFDD0 <= value <= 0xFDEF
        or value & 0xFFFF in (0xFFFE, 0xFFFF)
    )


def _validate_text(value: str, finding_id: str) -> None:
    if "\x00" in value:
        raise GovernanceError("CONTRACT-NUL", "NUL is forbidden in Issue bodies and contracts", code=2)
    if any(_forbidden_scalar(ord(character)) for character in value):
        raise GovernanceError(finding_id, "forbidden Unicode scalar value", code=2)


def _validate_json_strings(value: Any) -> None:
    if isinstance(value, str):
        _validate_text(value, "CONTRACT-UNICODE")
        if START_MARKER in value or END_MARKER in value:
            raise GovernanceError("CONTRACT-RESIDUAL-MARKER", "decoded JSON strings and keys cannot contain contract markers", code=2)
        if "```" in value:
            raise GovernanceError("CONTRACT-RESIDUAL-FENCE", "decoded JSON strings and keys cannot contain fences", code=2)
    elif isinstance(value, list):
        for item in value:
            _validate_json_strings(item)
    elif isinstance(value, dict):
        for key, item in value.items():
            _validate_json_strings(key)
            _validate_json_strings(item)


def _decode_body(raw_body: bytes) -> str:
    if len(raw_body) > MAX_BODY_BYTES:
        raise GovernanceError("CONTRACT-BODY-LIMIT", "Issue body exceeds 262144 UTF-8 bytes", code=2)
    try:
        body = raw_body.decode("utf-8", errors="strict")
    except UnicodeDecodeError as error:
        raise GovernanceError("CONTRACT-UTF8", "Issue body is not valid UTF-8", code=2) from error
    _validate_text(body, "CONTRACT-UNICODE")
    return body


def extract_contract(raw_body: bytes | str) -> ExtractedContract:
    """Extract exactly one marked, lowercase-json fenced object."""

    if isinstance(raw_body, str):
        try:
            raw = raw_body.encode("utf-8", errors="strict")
        except UnicodeEncodeError as error:
            raise GovernanceError("CONTRACT-UTF8", "Issue body is not valid UTF-8", code=2) from error
    else:
        raw = raw_body
    body = _decode_body(raw)
    if body.count(START_MARKER) != 1 or body.count(END_MARKER) != 1:
        raise GovernanceError("CONTRACT-MARKER-COUNT", "exactly one start and one end marker are required", code=2)
    start = body.find(START_MARKER)
    end = body.find(END_MARKER)
    if start >= end:
        raise GovernanceError("CONTRACT-MARKER-ORDER", "contract markers are reversed or overlapping", code=2)

    narrative_and_payload = body.replace(START_MARKER, "").replace(END_MARKER, "")
    unescaped = html.unescape(narrative_and_payload)
    if START_MARKER in unescaped or END_MARKER in unescaped:
        raise GovernanceError("CONTRACT-ESCAPED-MARKER", "escaped contract markers are forbidden", code=2)

    inner_start = start + len(START_MARKER)
    inner = body[inner_start:end]
    inner_bytes = inner.encode("utf-8")
    match = _BLOCK.fullmatch(inner_bytes)
    if not match:
        raise GovernanceError(
            "CONTRACT-FENCE",
            "markers must contain exactly one lowercase json fence and no residual content",
            code=2,
        )
    payload = match.group("payload")
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise GovernanceError("CONTRACT-PAYLOAD-LIMIT", "contract payload exceeds 131072 UTF-8 bytes", code=2)
    if b"```" in payload or START_MARKER.encode() in payload or END_MARKER.encode() in payload:
        raise GovernanceError("CONTRACT-RESIDUAL-FENCE", "nested fences or contract markers are forbidden", code=2)
    if not payload.strip():
        raise GovernanceError("CONTRACT-EMPTY", "contract JSON object is empty", code=2)
    try:
        decoded_payload = payload.decode("utf-8", errors="strict")
        contract = json.loads(
            decoded_payload,
            object_pairs_hook=_reject_duplicate_keys,
            parse_constant=_reject_constant,
        )
    except GovernanceError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as error:
        raise GovernanceError("CONTRACT-JSON", "contract payload is not one valid JSON value", code=2) from error
    if not isinstance(contract, dict):
        raise GovernanceError("CONTRACT-OBJECT", "contract payload must be a JSON object", code=2)
    try:
        _validate_json_strings(contract)
    except RecursionError as error:
        raise GovernanceError("CONTRACT-JSON", "contract JSON nesting is too deep", code=2) from error

    payload_byte_start = match.start("payload")
    return ExtractedContract(
        contract=contract,
        body=body,
        narrative_before=body[:start],
        narrative_after=body[end + len(END_MARKER) :],
        marker_start=len(body[:start].encode("utf-8")),
        marker_end=len(body[: end + len(END_MARKER)].encode("utf-8")),
        payload_start=len(body[:inner_start].encode("utf-8")) + payload_byte_start,
        payload_end=len(body[:inner_start].encode("utf-8")) + match.end("payload"),
    )


def extract_contract_file(path: str | Path) -> ExtractedContract:
    try:
        raw = Path(path).read_bytes()
    except OSError as error:
        raise GovernanceError("INPUT-READ", f"cannot read body file: {path}", code=2) from error
    return extract_contract(raw)

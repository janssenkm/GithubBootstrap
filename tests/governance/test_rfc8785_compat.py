import json
import math
import struct
from pathlib import Path

import pytest
import rfc8785


FIXTURES = Path(__file__).parent / "fixtures" / "rfc8785"


def load_fixture(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def test_rfc_primitive_and_recursive_sorting_example():
    fixture = load_fixture("primitive-example.json")
    assert rfc8785.dumps(fixture["input"]).decode("utf-8") == fixture["expected"]


def test_rfc_appendix_b_number_vectors():
    for ieee_hex, expected, comment in load_fixture("appendix-b.json"):
        value = struct.unpack(">d", bytes.fromhex(ieee_hex))[0]
        if expected is None:
            with pytest.raises(rfc8785.CanonicalizationError):
                rfc8785.dumps(value)
        else:
            assert rfc8785.dumps(value).decode("ascii") == expected, comment or ieee_hex


def test_rfc_utf16_property_order():
    fixture = load_fixture("utf16-order.json")
    canonical = rfc8785.dumps(fixture["input"])
    decoded = json.loads(canonical, object_pairs_hook=dict)
    assert list(decoded.values()) == fixture["expected_value_order"]


def test_control_character_and_unicode_serialization():
    fixture = load_fixture("boundary-cases.json")
    controls = "".join(chr(value) for value in fixture["escaped_controls"]["input_codepoints"])
    assert rfc8785.dumps(controls).decode("utf-8") == fixture["escaped_controls"]["expected"]
    assert rfc8785.dumps(fixture["unicode"]["input"]).decode("utf-8") == fixture["unicode"]["expected"]


def test_invalid_surrogate_non_finite_and_integer_domain_fail_closed():
    fixture = load_fixture("boundary-cases.json")
    invalid_values = [
        chr(fixture["invalid_surrogate_codepoint"]),
        math.nan,
        math.inf,
        -math.inf,
        fixture["first_unsafe_integer"],
        -fixture["first_unsafe_integer"],
    ]
    for value in invalid_values:
        with pytest.raises(rfc8785.CanonicalizationError):
            rfc8785.dumps(value)

    largest_safe = fixture["largest_safe_integer"]
    assert rfc8785.dumps(largest_safe) == str(largest_safe).encode("ascii")


def test_duplicate_keys_are_rejected_before_canonicalization():
    raw = (FIXTURES / "duplicate-key.json").read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate JSON key"):
        json.loads(raw, object_pairs_hook=reject_duplicate_keys)

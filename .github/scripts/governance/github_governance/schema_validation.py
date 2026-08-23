"""Checked-in Draft 2020-12 schema selection and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator, FormatChecker

from .errors import Finding, GovernanceError


SUPPORTED_SCHEMA_VERSION = "1.0.0"
SCHEMA_RELATIVE_PATH = Path(".github/schemas/engineering-issue.schema.json")


def _strict_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise GovernanceError("SCHEMA-DUPLICATE-KEY", f"duplicate schema key: {key}", code=5)
        result[key] = value
    return result


def load_engineering_schema(repository_root: str | Path) -> dict[str, Any]:
    path = Path(repository_root) / SCHEMA_RELATIVE_PATH
    try:
        schema = json.loads(path.read_text(encoding="utf-8"), object_pairs_hook=_strict_pairs)
    except GovernanceError:
        raise
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise GovernanceError("SCHEMA-LOAD", f"cannot load checked-in schema: {path}", code=5) from error
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise GovernanceError("SCHEMA-INVALID", "checked-in Engineering Issue schema is invalid", code=5) from error
    return schema


def schema_findings(contract: dict[str, Any], repository_root: str | Path) -> list[Finding]:
    if contract.get("schema_version") != SUPPORTED_SCHEMA_VERSION:
        return [Finding("SCHEMA-VERSION", "schema_version must be exactly 1.0.0", "/schema_version")]
    validator = Draft202012Validator(
        load_engineering_schema(repository_root),
        format_checker=FormatChecker(),
    )
    findings: list[Finding] = []
    for error in sorted(validator.iter_errors(contract), key=lambda item: list(item.absolute_path)):
        path = "/" + "/".join(str(part) for part in error.absolute_path)
        findings.append(Finding("SCHEMA-INVALID", error.message, path))
    return findings

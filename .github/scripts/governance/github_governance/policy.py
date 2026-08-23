"""Strict project-policy loading and exact capability checks."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml
from jsonschema import Draft202012Validator
from yaml.tokens import AliasToken, AnchorToken, TagToken

from .errors import PolicyError


LOGIN = re.compile(r"^(?=.{1,39}$)[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9]))*$")
POLICY_SCHEMA = Path(".github/schemas/project-policy.schema.json")
CAPABILITIES = ("trusted_issue_authors", "trusted_developers", "trusted_reviewers", "trusted_milestone_acceptors")


class _UniqueSafeLoader(yaml.SafeLoader):
    pass


def _construct_mapping(loader: _UniqueSafeLoader, node: yaml.MappingNode, deep: bool = False) -> dict[Any, Any]:
    if any(key_node.tag == "tag:yaml.org,2002:merge" for key_node, _ in node.value):
        raise PolicyError("POLICY-YAML-MERGE", "YAML merge keys are forbidden")
    try:
        loader.flatten_mapping(node)
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as error:
        raise PolicyError("POLICY-YAML-SHAPE", "project policy contains an invalid YAML mapping") from error
    result: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        try:
            key = loader.construct_object(key_node, deep=True)
        except PolicyError:
            raise
        except (yaml.YAMLError, TypeError, ValueError, RecursionError) as error:
            raise PolicyError("POLICY-YAML-KEY", "project policy contains an invalid YAML mapping key") from error
        if not isinstance(key, str):
            raise PolicyError("POLICY-YAML-KEY", "project policy mapping keys must be strings")
        if key in result:
            raise PolicyError("POLICY-DUPLICATE-KEY", f"duplicate YAML key: {key}")
        try:
            result[key] = loader.construct_object(value_node, deep=deep)
        except PolicyError:
            raise
        except (yaml.YAMLError, TypeError, ValueError, RecursionError) as error:
            raise PolicyError("POLICY-YAML-SHAPE", "project policy contains an invalid YAML value") from error
    return result


_UniqueSafeLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping)


def normalize_login(login: str) -> str:
    if login != login.strip() or not LOGIN.fullmatch(login):
        raise PolicyError("POLICY-LOGIN", f"invalid GitHub login: {login!r}")
    return login.lower()


def _reject_yaml_indirection(raw: str) -> None:
    try:
        for token in yaml.scan(raw):
            if isinstance(token, (AliasToken, AnchorToken, TagToken)):
                raise PolicyError("POLICY-YAML-INDIRECTION", "YAML aliases, anchors, and explicit tags are forbidden")
    except yaml.YAMLError as error:
        raise PolicyError("POLICY-YAML", "project policy is not valid YAML") from error


def load_policy(path: str | Path, repository_root: str | Path | None = None) -> dict[str, Any]:
    policy_path = Path(path)
    try:
        raw = policy_path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise PolicyError("POLICY-READ", f"cannot read project policy: {policy_path}") from error
    _reject_yaml_indirection(raw)
    try:
        policy = yaml.load(raw, Loader=_UniqueSafeLoader)
    except PolicyError:
        raise
    except (yaml.YAMLError, TypeError, ValueError, RecursionError) as error:
        raise PolicyError("POLICY-YAML", "project policy is not valid safe YAML") from error
    if not isinstance(policy, dict):
        raise PolicyError("POLICY-SHAPE", "project policy must be a mapping")

    root = Path(repository_root) if repository_root is not None else policy_path.parents[1]
    schema_path = root / POLICY_SCHEMA
    try:
        import json

        schema = json.loads(schema_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, ValueError) as error:
        raise PolicyError("POLICY-SCHEMA-READ", f"cannot load project policy schema: {schema_path}") from error
    errors = sorted(Draft202012Validator(schema).iter_errors(policy), key=lambda item: list(item.absolute_path))
    if errors:
        error = errors[0]
        location = "/" + "/".join(str(part) for part in error.absolute_path)
        raise PolicyError("POLICY-SCHEMA", error.message, path=location)

    normalized = dict(policy)
    for capability in CAPABILITIES:
        values = [normalize_login(value) for value in policy[capability]]
        if len(values) != len(set(values)):
            raise PolicyError("POLICY-DUPLICATE-LOGIN", f"{capability} contains normalized duplicates")
        normalized[capability] = values
    commands = policy["allowed_verification_commands"]
    if len(commands) != len(set(commands)):
        raise PolicyError("POLICY-DUPLICATE-COMMAND", "allowed_verification_commands contains duplicates")
    checks = policy["required_milestone_checks"]
    if len(checks) != len(set(checks)):
        raise PolicyError("POLICY-DUPLICATE-CHECK", "required_milestone_checks contains duplicates")
    return normalized


def authorized(policy: dict[str, Any], capability: str, actor: str) -> bool:
    if capability not in CAPABILITIES:
        raise PolicyError("POLICY-CAPABILITY", f"unknown capability: {capability}")
    return normalize_login(actor) in policy[capability]

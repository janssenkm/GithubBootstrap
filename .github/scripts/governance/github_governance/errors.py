"""Stable error and finding types used by the governance CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class Finding:
    id: str
    message: str
    path: str = ""

    def as_dict(self) -> dict[str, str]:
        result = {"id": self.id, "message": self.message}
        if self.path:
            result["path"] = self.path
        return result


class GovernanceError(Exception):
    """An expected fail-closed error with a stable process exit code."""

    def __init__(self, finding_id: str, message: str, *, code: int = 1, path: str = ""):
        super().__init__(message)
        self.code = code
        self.finding = Finding(finding_id, message, path)


class UsageError(GovernanceError):
    def __init__(self, message: str):
        super().__init__("CLI-USAGE", message, code=2)


class PolicyError(GovernanceError):
    def __init__(self, finding_id: str, message: str, *, path: str = ""):
        super().__init__(finding_id, message, code=5, path=path)

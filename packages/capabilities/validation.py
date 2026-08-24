from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class PluginSchemaIssue:
    path: str
    reason: str

    def as_dict(self) -> dict[str, str]:
        return {"path": self.path, "reason": self.reason}


class PluginSchemaError(ValueError):
    def __init__(self, message: str, *, issues: list[PluginSchemaIssue] | None = None):
        super().__init__(message)
        self.issues = tuple(issues or (PluginSchemaIssue("$", message),))

    def as_errors(self) -> list[dict[str, str]]:
        return [issue.as_dict() for issue in self.issues]


def _fail(path: str, reason: str) -> None:
    raise PluginSchemaError(reason, issues=[PluginSchemaIssue(path, reason)])


def validate_arguments(schema: dict[str, Any], arguments: dict[str, Any]) -> None:
    if not isinstance(arguments, dict):
        _fail("$", "Plugin arguments must be an object")

    properties = schema.get("properties") or {}
    required = set(schema.get("required") or [])
    missing = sorted(required - set(arguments))
    if missing:
        raise PluginSchemaError(
            "Missing required arguments: " + ", ".join(missing),
            issues=[PluginSchemaIssue(name, "required property is missing") for name in missing],
        )

    if schema.get("additionalProperties") is False:
        unknown = sorted(set(arguments) - set(properties))
        if unknown:
            raise PluginSchemaError(
                "Unknown arguments: " + ", ".join(unknown),
                issues=[PluginSchemaIssue(name, "unknown property") for name in unknown],
            )

    python_types = {
        "string": str,
        "integer": int,
        "number": (int, float),
        "boolean": bool,
        "object": dict,
        "array": list,
    }
    for name, value in arguments.items():
        rule = properties.get(name, {})
        expected = python_types.get(rule.get("type"))
        if expected and (
            not isinstance(value, expected)
            or rule.get("type") == "integer" and isinstance(value, bool)
        ):
            _fail(name, f"must be {rule['type']}")
        if "enum" in rule and value not in rule["enum"]:
            _fail(name, "is not an allowed value")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in rule and value < rule["minimum"]:
                _fail(name, "is below minimum")
            if "maximum" in rule and value > rule["maximum"]:
                _fail(name, "exceeds maximum")
        if isinstance(value, str):
            if "minLength" in rule and len(value) < int(rule["minLength"]):
                _fail(name, "is shorter than minLength")
            if "maxLength" in rule and len(value) > int(rule["maxLength"]):
                _fail(name, "exceeds maxLength")
        if isinstance(value, list):
            if "minItems" in rule and len(value) < int(rule["minItems"]):
                _fail(name, "has fewer than minItems")
            if "maxItems" in rule and len(value) > int(rule["maxItems"]):
                _fail(name, "exceeds maxItems")

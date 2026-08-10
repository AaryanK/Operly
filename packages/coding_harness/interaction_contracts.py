"""Deterministic behavioral contracts for generated browser controls."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from packages.custom_software.source_bundles import SourceBundle


class InteractionContractError(ValueError):
    pass


_CONTROL_TAGS = {"button", "form", "input", "select", "textarea"}
_REQUIRED_FIELDS = {
    "id", "control", "event", "handler", "operation", "success",
    "rejection", "stateChange", "stateProbe", "uiEvidence", "uiProjection",
    "persistence", "reloadOperation", "testId", "requirementIds",
}
_PERSISTENCE = {"reload_preserved", "session_only", "not_applicable"}


class _ControlInventory(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.controls: list[tuple[str, str | None]] = []

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): value for key, value in attrs}
        tag = tag.lower()
        role_control = values.get("role") in {"button", "tab", "menuitem", "option", "switch"}
        if tag not in _CONTROL_TAGS and tag != "a" and not role_control:
            return
        if tag == "input" and str(values.get("type") or "").lower() == "hidden":
            return
        if str(values.get("aria-hidden") or "").lower() == "true":
            return
        self.controls.append((tag, values.get("data-operly-interaction")))


def _texts(bundle: SourceBundle, suffixes: tuple[str, ...]) -> list[str]:
    return [item.content.decode("utf-8", errors="replace") for item in bundle.files if item.path.lower().endswith(suffixes)]


def _is_test_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    return lowered.startswith("tests/") or name.startswith("test_") or ".test." in name or ".spec." in name


def _handler_calls_operation(source: str, handler: str, operation: str) -> bool:
    escaped_handler = re.escape(handler)
    escaped_operation = re.escape(operation)
    patterns = (
        rf"function\s+{escaped_handler}\s*\([^)]*\)\s*{{[\s\S]{{0,3000}}?\b{escaped_operation}\s*\(",
        rf"(?:const|let|var)\s+{escaped_handler}\s*=\s*(?:async\s*)?(?:\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>[\s\S]{{0,3000}}?\b{escaped_operation}\s*\(",
        rf"def\s+{escaped_handler}\s*\([^)]*\)\s*:[\s\S]{{0,3000}}?\b{escaped_operation}\s*\(",
    )
    return any(re.search(pattern, source) for pattern in patterns)


def validate_interaction_contract(bundle: SourceBundle) -> dict:
    """Reject visible controls that lack executable, traceable behavior."""
    # Python standard-library apps commonly embed their HTML template directly
    # in app.py, so inventory both standalone markup and server source.
    html_sources = _texts(bundle, (".html", ".py"))
    if not html_sources:
        return {"controls": 0, "contracts": 0}
    inventory = _ControlInventory()
    for source in html_sources:
        inventory.feed(source)
    if not inventory.controls:
        return {"controls": 0, "contracts": 0}

    missing_annotations = [tag for tag, interaction_id in inventory.controls if not interaction_id]
    if missing_annotations:
        raise InteractionContractError(
            "Every visible interactive control must declare a unique data-operly-interaction id; "
            f"missing on {', '.join(missing_annotations[:8])}"
        )
    ids = [interaction_id for _, interaction_id in inventory.controls if interaction_id]
    if len(ids) != len(set(ids)):
        raise InteractionContractError("Visible interactive controls must use unique data-operly-interaction ids")

    artifact = next((item for item in bundle.files if item.path.lower() == "operly.interactions.json"), None)
    if artifact is None:
        raise InteractionContractError("Interactive browser applications must include operly.interactions.json")
    try:
        payload = json.loads(artifact.content.decode("utf-8"))
        contracts = payload["interactions"]
    except Exception as error:
        raise InteractionContractError("operly.interactions.json is invalid") from error
    if payload.get("schemaVersion") != 1 or not isinstance(contracts, list):
        raise InteractionContractError("Interaction contract must use schemaVersion 1 and an interactions array")
    by_id = {str(item.get("id") or ""): item for item in contracts if isinstance(item, dict)}
    if set(ids) != set(by_id):
        missing = sorted(set(ids) - set(by_id))
        extra = sorted(set(by_id) - set(ids))
        raise InteractionContractError(f"Interaction manifest must exactly cover rendered controls; missing={missing}, extra={extra}")

    js_source = "\n".join(
        item.content.decode("utf-8", errors="replace")
        for item in bundle.files
        if item.path.lower().endswith((".js", ".mjs", ".cjs", ".py")) and not _is_test_path(item.path)
    )
    test_source = "\n".join(
        item.content.decode("utf-8", errors="replace")
        for item in bundle.files
        if _is_test_path(item.path)
    )
    for interaction_id in ids:
        item = by_id[interaction_id]
        missing_fields = sorted(field for field in _REQUIRED_FIELDS if not str(item.get(field) or "").strip())
        if missing_fields:
            raise InteractionContractError(f"Interaction {interaction_id} is missing behavioral fields: {', '.join(missing_fields)}")
        if item["persistence"] not in _PERSISTENCE:
            raise InteractionContractError(f"Interaction {interaction_id} has an invalid persistence contract")
        if not isinstance(item["requirementIds"], list) or not all(re.fullmatch(r"R-[0-9]{3,}", str(value)) for value in item["requirementIds"]):
            raise InteractionContractError(f"Interaction {interaction_id} must trace to requirementIds")
        handler = str(item["handler"])
        operation = str(item["operation"])
        ui_projection = str(item["uiProjection"])
        reload_operation = str(item["reloadOperation"])
        if (
            interaction_id not in js_source
            or not _handler_calls_operation(js_source, handler, operation)
            or not _handler_calls_operation(js_source, handler, ui_projection)
        ):
            raise InteractionContractError(f"Interaction {interaction_id} is not wired from its rendered control through handler and domain operation")
        if not re.search(r"addEventListener|\.on(?:click|change|submit|input)\s*=|def\s+do_(?:GET|POST|PUT|PATCH|DELETE)", js_source):
            raise InteractionContractError("Interactive application JavaScript contains no real event-handler registration")
        required_evidence = [
            interaction_id,
            handler,
            operation,
            str(item["stateProbe"]),
            ui_projection,
            reload_operation,
            str(item["testId"]),
        ]
        if any(value not in test_source for value in required_evidence):
            raise InteractionContractError(f"Interaction {interaction_id} lacks an executable test tracing its operation and testId")
        if "node:assert" not in test_source and not re.search(r"\bassert\b|\.assert[A-Z]", test_source):
            raise InteractionContractError("Interaction tests must make executable assertions, not merely invoke application code")
    return {"controls": len(ids), "contracts": len(contracts)}

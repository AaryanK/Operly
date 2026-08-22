"""Deterministic behavioral contracts for generated browser controls."""
from __future__ import annotations

import json
import re
from html.parser import HTMLParser

from packages.custom_software.source_bundles import SourceBundle


class InteractionContractError(ValueError):
    pass


_CONTROL_TAGS = {"button", "form", "input", "select", "textarea"}
_NATIVE_INTRINSIC_INPUT_TYPES = {
    "checkbox",
    "radio",
    "range",
    "color",
    "date",
    "datetime-local",
    "month",
    "time",
    "week",
    "file",
}
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
        self._form_stack: list[bool] = []

    @staticmethod
    def _native_form(values: dict[str, str | None]) -> bool:
        """Return true when the browser can submit the form without application JS."""
        action = str(values.get("action") or "").strip()
        method = str(values.get("method") or "get").strip().lower()
        return bool(action) and not action.lower().startswith("javascript:") and method in {"get", "post"}

    def handle_starttag(self, tag: str, attrs) -> None:
        values = {str(key).lower(): value for key, value in attrs}
        tag = tag.lower()
        role_control = values.get("role") in {"button", "tab", "menuitem", "option", "switch"}

        # A normal GET/POST form with an action is already wired by the browser to a
        # server/domain boundary. Requiring an invented JavaScript handler, state
        # probe, requirement ledger and interaction manifest for such forms caused
        # Studio website generation to loop until its model budget was exhausted.
        if tag == "form":
            native = self._native_form(values)
            self._form_stack.append(native)
            if native:
                return

        in_native_form = any(self._form_stack)
        if in_native_form:
            # Standard fields are part of the browser's native form submission.
            if tag in {"select", "textarea"}:
                return
            if tag == "input":
                input_type = str(values.get("type") or "text").strip().lower()
                if input_type != "button":
                    return
            if tag == "button":
                button_type = str(values.get("type") or "submit").strip().lower()
                if button_type in {"submit", "reset"}:
                    return

        # Some standalone inputs have complete browser-native state semantics and are
        # commonly used by static websites without JavaScript. A CSS-only mobile-nav
        # checkbox, date picker, radio group, range/color picker, etc. should not be
        # forced through an invented domain-operation contract. Plain text/search
        # inputs outside a real form remain suspicious and are still validated below.
        if tag == "input" and not role_control:
            input_type = str(values.get("type") or "text").strip().lower()
            if input_type in _NATIVE_INTRINSIC_INPUT_TYPES:
                return

        # Normal anchors already have deterministic browser behavior through href.
        # Requiring a JavaScript interaction manifest for ordinary website navigation
        # made Studio source edits burn model turns inventing handlers/tests for links
        # such as the brand/home anchor. Only anchors promoted to app-style controls
        # through an interactive ARIA role belong in the custom interaction contract.
        if tag == "a" and not role_control:
            href = str(values.get("href") or "").strip()
            if href and not href.lower().startswith("javascript:"):
                return

        if tag not in _CONTROL_TAGS and tag != "a" and not role_control:
            return
        if tag == "input" and str(values.get("type") or "").lower() == "hidden":
            return
        if str(values.get("aria-hidden") or "").lower() == "true":
            return
        self.controls.append((tag, values.get("data-operly-interaction")))

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "form" and self._form_stack:
            self._form_stack.pop()


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
    """Reject visible scripted controls that lack executable, traceable behavior."""
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
            "Every visible scripted control must declare a unique data-operly-interaction id; "
            f"missing on {', '.join(missing_annotations[:8])}"
        )
    ids = [interaction_id for _, interaction_id in inventory.controls if interaction_id]
    if len(ids) != len(set(ids)):
        raise InteractionContractError("Visible scripted controls must use unique data-operly-interaction ids")

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

    # Rendered scripted controls must be covered. Extra dormant contracts are
    # harmless and can occur after a source edit converts a scripted control into
    # native browser behavior; they should not force another expensive model turn.
    missing = sorted(set(ids) - set(by_id))
    if missing:
        raise InteractionContractError(f"Interaction manifest is missing rendered controls: {missing}")

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

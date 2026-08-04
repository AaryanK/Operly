MODULES = {
    "audit": {"version": 1, "description": "Immutable application audit events", "dependencies": [], "removable": False},
    "authentication": {"version": 1, "description": "Shared Operly identity, membership, secure sessions and protected routes", "dependencies": ["audit", "permissions"], "removable": False},
    "dashboard": {"version": 1, "description": "Authenticated application shell and navigation", "dependencies": ["authentication"]},
    "crud_entity": {"version": 1, "description": "Managed entity definitions and records", "dependencies": ["audit", "permissions"]},
    "form": {"version": 1, "description": "Validated managed record form", "dependencies": ["crud_entity"]},
    "data_table": {"version": 1, "description": "Permission-filtered managed record table", "dependencies": ["crud_entity"]},
    "permissions": {"version": 1, "description": "Owner, manager and employee application roles", "dependencies": ["audit"], "removable": False},
    "theme": {"version": 1, "description": "Application design tokens and scoped overrides", "dependencies": []},
    "workflow": {"version": 1, "description": "Approved trigger/action bindings", "dependencies": ["audit"]},
    "navigation": {"version": 1, "description": "Internal-route navigation", "dependencies": []},
}

COMPONENTS = {
    "Page": ([], ["Section", "Navigation"]), "Section": (["Page", "Column"], ["Grid", "Row", "Heading", "TextBlock", "Form", "DataTable", "Button", "EmptyRegion"]),
    "Grid": (["Section", "Column"], ["Row", "Column", "Card", "MetricCard"]), "Row": (["Section", "Grid", "Column"], ["Column", "Card", "Button"]),
    "Column": (["Row", "Grid"], ["Section", "Heading", "TextBlock", "Image", "Badge", "Form", "DataTable", "Button", "Divider", "Spacer"]),
    "Card": (["Page", "Section", "Grid", "Row", "Column"], ["Heading", "TextBlock", "Form", "Button"]), "Form": (["Page", "Section", "Column", "Card"], ["TextInput", "EmailInput", "PasswordInput", "Select", "Checkbox", "DateInput", "SubmitButton"]),
    "Heading": (["Section", "Column", "Card"], []), "TextBlock": (["Section", "Column", "Card"], []), "Image": (["Section", "Column"], []), "Badge": (["Section", "Column", "Card"], []),
    "TextInput": (["Form"], []), "EmailInput": (["Form"], []), "PasswordInput": (["Form"], []), "Select": (["Form"], []), "Checkbox": (["Form"], []), "DateInput": (["Form"], []), "SubmitButton": (["Form"], []),
    "Button": (["Section", "Row", "Column", "Card"], []), "MetricCard": (["Grid", "Row"], []), "DataTable": (["Page", "Section", "Column"], []), "Navigation": (["Page"], []), "Tabs": (["Section", "Column"], ["Section"]), "Modal": (["Page"], ["Section"]), "Alert": (["Section", "Column"], []), "EmptyState": (["Section", "Column"], []), "EmptyRegion": (["Section", "Column"], ["Section", "Grid", "Form", "DataTable"]), "Divider": (["Section", "Column"], []), "Spacer": (["Section", "Column"], []),
}

ALLOWED_FIELDS = {"text", "long_text", "integer", "decimal", "boolean", "date", "datetime", "email", "phone", "status", "relation", "user_reference"}
ALLOWED_ACTIONS = {"navigate", "open_modal", "submit_form", "run_workflow", "create_record", "request_approval", "open_operly_chat"}
PALETTE = {"dark green": "forest", "green": "emerald", "cream": "cream", "orange": "orange", "blue": "blue", "red": "red", "gray": "slate", "white": "white"}


def module_catalog():
    return [{"moduleId": key, **value} for key, value in MODULES.items()]


def component_catalog():
    return [{"type": key, "allowedParents": parents, "allowedChildren": children, "editableProperties": ["label", "hidden", "variant", "tokenOverride"], "arbitraryCode": False} for key, (parents, children) in COMPONENTS.items()]

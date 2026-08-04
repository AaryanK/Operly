from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Could not patch {label}: expected text not found")
    return text.replace(old, new, 1)


# 1. Register database models.
db_path = Path("packages/database/db.py")
db_text = db_path.read_text(encoding="utf-8")

model_anchor = "    from packages.database import models  # noqa: F401"
operations_import = (
    "    from packages.database import operations_models  # noqa: F401"
)

if operations_import not in db_text:
    db_text = replace_once(
        db_text,
        model_anchor,
        model_anchor + "\n" + operations_import,
        "operations database models",
    )

db_path.write_text(db_text, encoding="utf-8")


# 2. Register API router.
main_path = Path("apps/api/main.py")
main_text = main_path.read_text(encoding="utf-8")

dependency_anchor = (
    "from apps.api.dependencies import AuthContext, get_auth_context, get_db\n"
)
router_import = (
    "from apps.api.operations_router import router as operations_router\n"
)

if router_import not in main_text:
    main_text = replace_once(
        main_text,
        dependency_anchor,
        dependency_anchor + router_import,
        "operations API import",
    )

if "app.include_router(operations_router)" not in main_text:
    frontend_markers = ["WEB_STATIC = ", "WEB_DIST = "]
    index = -1
    for marker in frontend_markers:
        index = main_text.find(marker)
        if index != -1:
            break
    if index == -1:
        raise RuntimeError("Could not locate frontend serving block")

    main_text = (
        main_text[:index]
        + "app.include_router(operations_router)\n\n"
        + main_text[index:]
    )

main_path.write_text(main_text, encoding="utf-8")


# 3. Register operations tools in the shared agent.
tools_path = Path("packages/business_brain/tools.py")
tools_text = tools_path.read_text(encoding="utf-8")

operations_tools_import = (
    "from packages.business_brain.operations_tools import "
    "register_operations_tools\n"
)
if operations_tools_import not in tools_text:
    import_anchor = (
        "from packages.business_brain.registry import ToolRegistry\n"
    )
    tools_text = replace_once(
        tools_text,
        import_anchor,
        import_anchor + operations_tools_import,
        "operations tool import",
    )

if "    register_operations_tools(registry)\n" not in tools_text:
    return_anchor = "    return registry\n"
    position = tools_text.rfind(return_anchor)
    if position == -1:
        raise RuntimeError("Could not locate shared tool registry return")
    tools_text = (
        tools_text[:position]
        + "    register_operations_tools(registry)\n\n"
        + tools_text[position:]
    )

tools_path.write_text(tools_text, encoding="utf-8")


# 4. Add website navigation and static assets.
html_path = Path("apps/web/static/index.html")
html = html_path.read_text(encoding="utf-8")

nav_anchor = '<button data-page="overview" class="active">▦ Overview</button>'
nav_items = '''<button data-page="overview" class="active">▦ Overview</button>
        <button data-page="induction">◫ Induction</button>
        <button data-page="operationsCenter">◆ Operations</button>
        <button data-page="audit">◈ Audit</button>
        <button data-page="operatingPlan">⌘ Operating plan</button>'''

if 'data-page="induction"' not in html:
    html = replace_once(
        html,
        nav_anchor,
        nav_items,
        "operations navigation",
    )

if "/static/operations-phase.css" not in html:
    html = replace_once(
        html,
        '<link rel="stylesheet" href="/static/styles.css">',
        '<link rel="stylesheet" href="/static/styles.css">\n'
        '  <link rel="stylesheet" href="/static/operations-phase.css">',
        "operations stylesheet",
    )

if "/static/operations-phase.js" not in html:
    html = replace_once(
        html,
        '<script src="/static/app.js"></script>',
        '<script src="/static/app.js"></script>\n'
        '  <script src="/static/operations-phase.js"></script>\n'
        '  <script src="/static/operations-phase-bridge.js"></script>',
        "operations scripts",
    )

html_path.write_text(html, encoding="utf-8")

print("OPERLY Operations Phase installed.")

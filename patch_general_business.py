from pathlib import Path

# 1. Ensure business models are created.
db_path = Path("packages/database/db.py")
db_text = db_path.read_text(encoding="utf-8")
old = "from packages.database import models  # noqa: F401"
new = (
    "from packages.database import models  # noqa: F401\n"
    "    from packages.database import business_models  # noqa: F401"
)
if "business_models" not in db_text:
    db_text = db_text.replace(old, new)
    db_path.write_text(db_text, encoding="utf-8")

# 2. Add API router before the frontend catch-all route.
main_path = Path("apps/api/main.py")
main_text = main_path.read_text(encoding="utf-8")

if "from apps.api.business import router as business_router" not in main_text:
    insert_after = "from apps.api.dependencies import AuthContext, get_auth_context, get_db\n"
    main_text = main_text.replace(
        insert_after,
        insert_after + "from apps.api.business import router as business_router\n",
    )

if "app.include_router(business_router)" not in main_text:
    marker = "WEB_STATIC = "
    index = main_text.find(marker)
    if index == -1:
        marker = "WEB_DIST = "
        index = main_text.find(marker)
    if index == -1:
        raise RuntimeError("Could not locate frontend serving block in apps/api/main.py")
    main_text = (
        main_text[:index]
        + "app.include_router(business_router)\n\n"
        + main_text[index:]
    )

main_path.write_text(main_text, encoding="utf-8")

# 3. Add sidebar navigation.
html_path = Path("apps/web/static/index.html")
html = html_path.read_text(encoding="utf-8")

nav_anchor = '<button data-page="tasks">✓ Tasks</button>'
nav_extension = '''<button data-page="tasks">✓ Tasks</button>
        <button data-page="crm">◎ CRM</button>
        <button data-page="catalog">▤ Catalog</button>
        <button data-page="sales">$ Sales</button>
        <button data-page="calendar">□ Calendar</button>
        <button data-page="team">♙ Team</button>
        <button data-page="reports">↗ Report</button>'''

if 'data-page="crm"' not in html:
    html = html.replace(nav_anchor, nav_extension)

if '/static/general-business.css' not in html:
    html = html.replace(
        '<link rel="stylesheet" href="/static/styles.css">',
        '<link rel="stylesheet" href="/static/styles.css">\n'
        '  <link rel="stylesheet" href="/static/general-business.css">',
    )

if '/static/general-business.js' not in html:
    html = html.replace(
        '<script src="/static/app.js"></script>',
        '<script src="/static/app.js"></script>\n'
        '  <script src="/static/general-business.js"></script>\n'
        '  <script src="/static/general-business-bridge.js"></script>',
    )

html_path.write_text(html, encoding="utf-8")

# 4. Add bridge because the original app.js keeps renderPage in its own scope.
bridge_path = Path("apps/web/static/general-business-bridge.js")
bridge_path.write_text(
    r'''
document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-page]");
  if (!button || !window.operlyBusinessPages) return;

  const page = button.dataset.page;
  const renderer = window.operlyBusinessPages[page];
  if (!renderer) return;

  event.stopImmediatePropagation();

  document.querySelectorAll("#nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === page);
  });

  document.querySelector("#page-title").textContent =
    window.operlyBusinessTitles[page] || page;

  try {
    await renderer();
  } catch (error) {
    document.querySelector("#content").innerHTML =
      `<div class="error">${String(error.message || error)}</div>`;
  }
}, true);
'''.lstrip(),
    encoding="utf-8",
)

print("OPERLY General Business Pack installed.")

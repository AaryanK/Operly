from pathlib import Path


def replace_once(text: str, old: str, new: str, label: str) -> str:
    if new in text:
        return text
    if old not in text:
        raise RuntimeError(f"Could not patch {label}; expected text was not found")
    return text.replace(old, new, 1)


# 1. Ensure agent and business models are created.
db_path = Path("packages/database/db.py")
db_text = db_path.read_text(encoding="utf-8")

models_import = "    from packages.database import models  # noqa: F401"
if "from packages.database import agent_models" not in db_text:
    db_text = replace_once(
        db_text,
        models_import,
        models_import
        + "\n"
        + "    from packages.database import agent_models  # noqa: F401",
        "database agent model import",
    )

if "from packages.database import business_models" not in db_text:
    db_text = replace_once(
        db_text,
        models_import,
        models_import
        + "\n"
        + "    from packages.database import business_models  # noqa: F401",
        "database business model import",
    )

db_path.write_text(db_text, encoding="utf-8")


# 2. Patch FastAPI imports, middleware and routers.
main_path = Path("apps/api/main.py")
main_text = main_path.read_text(encoding="utf-8")

dependency_import = (
    "from apps.api.dependencies import AuthContext, get_auth_context, get_db\n"
)
extra_imports = (
    "from apps.api.agent_router import router as agent_router\n"
    "from apps.api.csrf import CSRFMiddleware\n"
    "from apps.api.session import router as session_router\n"
)

if "from apps.api.agent_router import router as agent_router" not in main_text:
    main_text = replace_once(
        main_text,
        dependency_import,
        dependency_import + extra_imports,
        "API imports",
    )

cors_marker = "app.add_middleware(\n    CORSMiddleware,"
if "app.add_middleware(CSRFMiddleware)" not in main_text:
    cors_index = main_text.find(cors_marker)
    if cors_index == -1:
        raise RuntimeError("Could not locate CORS middleware")
    # Insert CSRF middleware before the CORS block.
    main_text = (
        main_text[:cors_index]
        + "app.add_middleware(CSRFMiddleware)\n\n"
        + main_text[cors_index:]
    )

# Disable the old JSON token-returning login endpoint.
legacy_start = main_text.find('@app.post("/api/auth/login")')
legacy_end = main_text.find('@app.get("/api/me")')
if legacy_start != -1 and legacy_end != -1:
    replacement = '''@app.post("/api/auth/login", include_in_schema=False)
async def legacy_login_disabled():
    raise HTTPException(
        status_code=410,
        detail="Use /api/session/login",
    )


'''
    main_text = (
        main_text[:legacy_start]
        + replacement
        + main_text[legacy_end:]
    )

if "app.include_router(session_router)" not in main_text:
    marker_candidates = ["WEB_STATIC = ", "WEB_DIST = "]
    index = -1
    for marker in marker_candidates:
        index = main_text.find(marker)
        if index != -1:
            break
    if index == -1:
        raise RuntimeError("Could not locate frontend serving block")

    router_block = (
        "app.include_router(session_router)\n"
        "app.include_router(agent_router)\n\n"
    )
    main_text = main_text[:index] + router_block + main_text[index:]

main_path.write_text(main_text, encoding="utf-8")


# 3. Patch website HTML.
html_path = Path("apps/web/static/index.html")
html = html_path.read_text(encoding="utf-8")

if 'data-page="assistant"' not in html:
    anchor = '<button data-page="overview" class="active">▦ Overview</button>'
    html = replace_once(
        html,
        anchor,
        anchor + '\n        <button data-page="assistant">✦ OPERLY AI</button>',
        "AI navigation",
    )

if "/static/ai-assistant.css" not in html:
    html = replace_once(
        html,
        '<link rel="stylesheet" href="/static/styles.css">',
        '<link rel="stylesheet" href="/static/styles.css">\n'
        '  <link rel="stylesheet" href="/static/ai-assistant.css">',
        "AI stylesheet",
    )

if "/static/ai-assistant.js" not in html:
    html = replace_once(
        html,
        '<script src="/static/app.js"></script>',
        '<script src="/static/app.js"></script>\n'
        '  <script src="/static/ai-assistant.js"></script>\n'
        '  <script src="/static/ai-assistant-bridge.js"></script>',
        "AI scripts",
    )

html_path.write_text(html, encoding="utf-8")


# 4. Convert the static app from localStorage bearer tokens to HttpOnly cookies.
app_js_path = Path("apps/web/static/app.js")
app_js = app_js_path.read_text(encoding="utf-8")

# Add cookie helpers near the top.
if "function csrfToken()" not in app_js:
    insertion = '''function csrfToken() {
  const match = document.cookie.match(/(?:^|; )operly_csrf=([^;]*)/);
  return match ? decodeURIComponent(match[1]) : "";
}

'''
    app_js = replace_once(
        app_js,
        'const state = { me: null, page: "overview" };\n\n',
        'const state = { me: null, page: "overview" };\n\n' + insertion,
        "CSRF helper",
    )

old_api = '''async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = localStorage.getItem(TOKEN_KEY);
  if (token) headers.Authorization = `Bearer ${token}`;
  const response = await fetch(`/api${path}`, { ...options, headers });
  if (response.status === 401) {
    localStorage.removeItem(TOKEN_KEY);
    show("#login");
  }
'''

new_api = '''async function api(path, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const method = (options.method || "GET").toUpperCase();
  if (!["GET", "HEAD", "OPTIONS"].includes(method)) {
    const csrf = csrfToken();
    if (csrf) headers["X-CSRF-Token"] = csrf;
  }
  const response = await fetch(`/api${path}`, {
    ...options,
    headers,
    credentials: "same-origin"
  });
  if (response.status === 401) {
    show("#login");
  }
'''

if old_api in app_js:
    app_js = app_js.replace(old_api, new_api, 1)

app_js = app_js.replace(
    'const result = await api("/auth/login", {',
    'await api("/session/login", {',
)

app_js = app_js.replace(
    '''    localStorage.setItem(TOKEN_KEY, result.token);
    await enterDashboard();''',
    '''    await enterDashboard();''',
)

old_logout = '''$("#logout").addEventListener("click", () => {
  localStorage.removeItem(TOKEN_KEY); state.me = null; show("#landing");
});'''
new_logout = '''$("#logout").addEventListener("click", async () => {
  try {
    await api("/session/logout", { method: "POST" });
  } catch {}
  state.me = null;
  show("#landing");
});'''
if old_logout in app_js:
    app_js = app_js.replace(old_logout, new_logout, 1)

old_bootstrap = '''if (localStorage.getItem(TOKEN_KEY)) enterDashboard().catch(() => show("#login"));'''
new_bootstrap = '''enterDashboard().catch(() => show("#landing"));'''
if old_bootstrap in app_js:
    app_js = app_js.replace(old_bootstrap, new_bootstrap, 1)

app_js_path.write_text(app_js, encoding="utf-8")

print("OPERLY shared AI and secure channel patch installed.")

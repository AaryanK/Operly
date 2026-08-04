from pathlib import Path

path = Path("apps/api/main.py")
text = path.read_text(encoding="utf-8")

marker = "WEB_DIST = "
index = text.find(marker)
if index != -1:
    text = text[:index]

text += '''
WEB_STATIC = Path(__file__).resolve().parents[1] / "web" / "static"

app.mount(
    "/static",
    StaticFiles(directory=WEB_STATIC),
    name="static",
)

@app.get("/{path:path}", include_in_schema=False)
async def frontend(path: str):
    requested = WEB_STATIC / path
    if path and requested.is_file():
        return FileResponse(requested)
    return FileResponse(WEB_STATIC / "index.html")
'''

path.write_text(text, encoding="utf-8")
print("Patched apps/api/main.py for no-npm frontend.")

import html
from packages.application_builder.schema import ApplicationManifest

COLORS = {"forest":"#174c3c","emerald":"#16866f","cream":"#fff4d6","orange":"#ef7d22","blue":"#2563eb","red":"#dc2626","slate":"#475569","white":"#ffffff"}


def render_application(manifest: ApplicationManifest, page_id: str | None = None, *, studio: bool = False) -> str:
    page = next((p for p in manifest.pages if p.id == page_id), manifest.pages[0] if manifest.pages else None)
    theme = manifest.theme
    roots = sorted([x for x in manifest.components if not x.parentId and (not page or x.id in page.componentIds)], key=lambda x: x.order)
    by_parent = {}
    for component in manifest.components:
        by_parent.setdefault(component.parentId, []).append(component)
    def node(component):
        props = component.properties
        children = "".join(node(x) for x in sorted(by_parent.get(component.id, []), key=lambda x:x.order))
        attrs = f' data-operly-component-id="{html.escape(component.id)}" data-operly-component-type="{component.type}"'
        if studio: attrs += ' tabindex="0"'
        label = html.escape(str(props.get("text") or props.get("label") or component.label))
        if component.type in {"Page","Section","Grid","Row","Column","Card","EmptyRegion"}: return f'<section{attrs} class="c c-{component.type.lower()}">{children or label}</section>'
        if component.type == "Heading": return f'<h2{attrs}>{label}</h2>'
        if component.type == "TextBlock": return f'<p{attrs}>{label}</p>'
        if component.type in {"Button","SubmitButton"}: return f'<button{attrs} type="button">{label}</button>'
        if component.type in {"TextInput","EmailInput","PasswordInput","DateInput"}: return f'<label{attrs}>{label}<input type="{component.type.replace("Input","").lower()}" disabled></label>'
        if component.type == "Form": return f'<form{attrs} onsubmit="return false">{children}</form>'
        if component.type == "DataTable": return f'<div{attrs} class="table">{label}<div>No records yet</div></div>'
        return f'<div{attrs}>{label}{children}</div>'
    body = "".join(node(x) for x in roots) or '<section class="blank" data-operly-region-id="root">Blank application — select this region to add a capability or component.</section>'
    css = f':root{{--primary:{COLORS[theme.primary]};--background:{COLORS[theme.background]};--surface:{COLORS[theme.surface]};--text:{COLORS[theme.text]}}}body{{margin:0;padding:32px;background:var(--background);color:var(--text);font-family:system-ui}}.c{{box-sizing:border-box;margin:8px;padding:16px}}.c-section,.c-card{{background:var(--surface);border:1px solid #cbd5e1;border-radius:12px}}button{{background:var(--primary);color:white;border:0;border-radius:8px;padding:10px 16px}}[data-operly-component-id]:hover{{outline:2px solid #60a5fa}}.selected{{outline:3px solid #f97316!important}}'
    script = "" if not studio else "<script>document.addEventListener('click',e=>{const n=e.target.closest('[data-operly-component-id]');if(!n)return;e.preventDefault();if(!e.shiftKey)document.querySelectorAll('.selected').forEach(x=>x.classList.remove('selected'));n.classList.add('selected');parent.postMessage({type:'OPERLY_SELECT',id:n.dataset.operlyComponentId,componentType:n.dataset.operlyComponentType,multi:e.shiftKey},location.origin)})</script>"
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>{css}</style></head><body>{body}{script}</body></html>'

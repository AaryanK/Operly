import html
import json
from urllib.parse import quote

from packages.application_builder.schema import ApplicationManifest

COLORS={"forest":"#174c3c","emerald":"#16866f","cream":"#fff4d6","orange":"#ef7d22","blue":"#2563eb","red":"#dc2626","slate":"#475569","white":"#ffffff"}


def _safe_json(value):
    return json.dumps(value,separators=(",",":"),ensure_ascii=False).replace("<","\\u003c").replace(">","\\u003e").replace("&","\\u0026")


def render_application(manifest:ApplicationManifest,page_id:str|None=None,*,application_id:str|None=None,version_id:str|None=None,base_path:str="",route:str="/",studio:bool=False,role:str="owner")->str:
    application_id=application_id or manifest.application.get("id","");version_id=version_id or "preview"
    normalized_route="/"+route.strip("/") if route!="/" else "/"
    page=next((p for p in manifest.pages if p.id==page_id),None) if page_id else next((p for p in manifest.pages if p.route==normalized_route),None)
    entities={entity.id:entity for entity in manifest.entities};components={component.id:component for component in manifest.components};by_parent={}
    for component in manifest.components:by_parent.setdefault(component.parentId,[]).append(component)
    nav=[]
    for item in manifest.pages:
        sep="&" if "?" in base_path else "?";href=f"{base_path}{sep}route={quote(item.route)}" if studio else f"{base_path}{item.route if item.route!='/' else ''}"
        nav.append(f'<a href="{html.escape(href)}" class="{"active" if page and page.id==item.id else ""}" data-operly-page-id="{html.escape(item.id)}" data-operly-route="{html.escape(item.route)}">{html.escape(item.name)}</a>')
    tables=[]
    def attrs(component,entity_id=None,field_id=None):
        values={"application-id":application_id,"application-version":version_id,"page-id":page.id if page else "","route-id":page.id if page else "","component-id":component.id,"component-type":component.type,"entity-id":entity_id or "","field-id":field_id or ""}
        result="".join(f' data-operly-{key}="{html.escape(str(value))}"' for key,value in values.items() if value)
        return result+(' tabindex="0"' if studio else "")
    def node(component):
        if role in component.hiddenFor:return ""
        props=component.properties;entity_id=props.get("entityId");field_id=props.get("fieldId");label=html.escape(str(props.get("text") or props.get("label") or component.label));children="".join(node(x) for x in sorted(by_parent.get(component.id,[]),key=lambda x:x.order))
        metadata=attrs(component,entity_id,field_id)
        if component.type in {"Page","Section","Grid","Row","Column","Card","EmptyRegion"}:return f'<section{metadata} class="c c-{component.type.lower()}">{children or label}</section>'
        if component.type=="Heading":return f'<h2{metadata}>{label}</h2>'
        if component.type=="TextBlock":return f'<p{metadata}>{label}</p>'
        if component.type=="Form":
            if props.get("authFlow") is True:return f'<section{metadata} class="auth-form"><h2>{label}</h2>{children}<p>Authentication uses the active OPERLY session.</p></section>'
            return f'<form{metadata} class="managed-form" data-form-id="{html.escape(component.id)}" data-entity-id="{html.escape(str(entity_id))}" novalidate><h2>{label}</h2>{children}<div class="form-general" role="alert"></div><div class="form-success" role="status"></div></form>'
        if component.type in {"TextInput","EmailInput","PasswordInput","DateInput","Select","Checkbox"}:
            parent=components.get(component.parentId);entity=entities.get(parent.properties.get("entityId")) if parent else None;field=next((x for x in entity.fields if x.id==field_id),None) if entity else None
            input_type={"EmailInput":"email","PasswordInput":"password","DateInput":"date","Checkbox":"checkbox"}.get(component.type,"text");required=" required" if field and field.required else "";maxlength=f' maxlength="{field.maxLength}"' if field and field.maxLength else "";control_name=str(field_id or component.id)
            if component.type=="Select":control=f'<select name="{html.escape(str(field_id))}"{required}>'+"".join(f'<option value="{html.escape(x)}">{html.escape(x)}</option>' for x in (field.options if field else []))+"</select>"
            else:control=f'<input name="{html.escape(control_name)}" type="{input_type}"{required}{maxlength}>'
            return f'<label{metadata}>{label}{control}<span class="field-error" data-error-for="{html.escape(str(field_id))}"></span></label>'
        if component.type=="SubmitButton":
            parent=components.get(component.parentId);button_type="button" if parent and parent.properties.get("authFlow") is True else "submit"
            return f'<button{metadata} type="{button_type}">{label}</button>'
        if component.type=="Button":return f'<button{metadata} type="button">{label}</button>'
        if component.type=="DataTable":
            entity=entities.get(entity_id);declared=[x.id for x in entity.fields] if entity else [];columns=[x for x in props.get("columns",declared) if x in declared];tables.append({"componentId":component.id,"entityId":entity_id,"columns":columns})
            headers="".join(f'<th>{html.escape(next(x.name for x in entity.fields if x.id==column))}</th>' for column in columns)
            return f'<section{metadata} class="managed-table" data-table-id="{html.escape(component.id)}"><h2>{label}</h2><div class="table-state">Loading…</div><div class="table-scroll"><table hidden><thead><tr>{headers}</tr></thead><tbody></tbody></table></div></section>'
        return f'<div{metadata}>{label}{children}</div>'
    if page:roots=sorted([components[cid] for cid in page.componentIds if cid in components],key=lambda x:x.order);body="".join(node(x) for x in roots)
    elif not manifest.pages:
        roots=sorted(by_parent.get(None,[]),key=lambda x:x.order);body="".join(node(x) for x in roots) or '<section class="blank" data-operly-region-id="root">Blank application — select this region to add a capability or component.</section>'
    else:body='<section class="not-found"><h1>Page not found</h1><p>The requested managed application route does not exist.</p></section>'
    config={"applicationId":application_id,"versionId":version_id,"recordBase":f"/api/application-builder/applications/{application_id}/entities","studio":studio,"tables":tables}
    theme=manifest.theme
    css=f''':root{{--primary:{COLORS[theme.primary]};--background:{COLORS[theme.background]};--surface:{COLORS[theme.surface]};--text:{COLORS[theme.text]}}}*{{box-sizing:border-box}}body{{margin:0;background:var(--background);color:var(--text);font-family:system-ui}}nav{{display:flex;gap:8px;flex-wrap:wrap;padding:14px 24px;background:var(--surface);border-bottom:1px solid #cbd5e1}}nav a{{padding:9px 12px;border-radius:8px;color:var(--text);text-decoration:none}}nav a.active{{background:var(--primary);color:white}}main{{max-width:1100px;margin:auto;padding:24px}}.c{{margin:8px 0;padding:16px}}.c-section,.c-card{{background:var(--surface);border:1px solid #cbd5e1;border-radius:12px}}.managed-form{{display:grid;gap:14px;max-width:620px;background:var(--surface);padding:20px;border-radius:12px}}label{{display:grid;gap:6px}}input,select{{padding:11px;border:1px solid #94a3b8;border-radius:8px;font:inherit}}button{{background:var(--primary);color:white;border:0;border-radius:8px;padding:11px 16px;cursor:pointer}}button[disabled]{{opacity:.6}}.field-error,.form-general{{color:#b42318;font-size:13px;min-height:1em}}.form-success{{color:#167044}}.table-scroll{{overflow:auto}}table{{width:100%;border-collapse:collapse;background:var(--surface)}}th,td{{padding:10px;border-bottom:1px solid #e2e8f0;text-align:left}}[data-operly-component-id]:hover{{outline:2px solid #60a5fa}}.selected{{outline:3px solid #f97316!important}}@media(max-width:600px){{main{{padding:14px}}nav{{padding:10px}}th,td{{min-width:120px}}}}'''
    runtime='''(()=>{const cfg=JSON.parse(document.querySelector("#operly-runtime-config").textContent);const csrf=()=>decodeURIComponent((document.cookie.match(/(?:^|; )operly_csrf=([^;]*)/)||[])[1]||"");const esc=v=>String(v??"");async function loadTable(def){const root=document.querySelector(`[data-table-id="${CSS.escape(def.componentId)}"]`);if(!root)return;const state=root.querySelector(".table-state"),table=root.querySelector("table"),body=root.querySelector("tbody");state.textContent="Loading…";try{const response=await fetch(`${cfg.recordBase}/${encodeURIComponent(def.entityId)}/records?limit=50`,{credentials:"same-origin"});if(!response.ok)throw new Error("Records could not be loaded.");const payload=await response.json();body.replaceChildren();payload.records.forEach(record=>{const tr=document.createElement("tr");def.columns.forEach(column=>{const td=document.createElement("td");td.textContent=esc(record.data[column]);tr.append(td)});body.append(tr)});table.hidden=!payload.records.length;state.textContent=payload.records.length?"": "No records yet."}catch(error){table.hidden=true;state.textContent=error.message}}async function loadTables(){await Promise.all(cfg.tables.map(loadTable))}document.querySelectorAll(".managed-form").forEach(form=>form.addEventListener("submit",async event=>{event.preventDefault();if(cfg.studio){form.querySelector(".form-general").textContent="Preview forms do not persist records. Apply and open the application to submit.";return}const button=form.querySelector('[type="submit"]');if(button.disabled)return;button.disabled=true;form.querySelectorAll(".field-error").forEach(x=>x.textContent="");form.querySelector(".form-general").textContent="";form.querySelector(".form-success").textContent="";const data={};new FormData(form).forEach((value,key)=>data[key]=value);try{const response=await fetch(`${cfg.recordBase}/${encodeURIComponent(form.dataset.entityId)}/records`,{method:"POST",credentials:"same-origin",headers:{"Content-Type":"application/json","X-CSRF-Token":csrf()},body:JSON.stringify({data,formId:form.dataset.formId,versionId:cfg.versionId,idempotencyKey:crypto.randomUUID()})});const payload=await response.json();if(!response.ok){if(payload.detail?.errors)payload.detail.errors.forEach(error=>{const node=form.querySelector(`[data-error-for="${CSS.escape(error.field)}"]`);if(node)node.textContent=error.message});throw new Error(payload.detail?.code==="record_validation_failed"?"Please correct the highlighted fields.":String(payload.detail||"Record could not be saved."))}form.reset();form.querySelector(".form-success").textContent="Saved successfully.";await loadTables()}catch(error){form.querySelector(".form-general").textContent=error.message}finally{button.disabled=false}}));loadTables();if(cfg.studio)document.addEventListener("click",event=>{const node=event.target.closest("[data-operly-component-id]");if(!node)return;if(!event.shiftKey)document.querySelectorAll(".selected").forEach(x=>x.classList.remove("selected"));node.classList.add("selected");parent.postMessage({type:"OPERLY_SELECT",id:node.dataset.operlyComponentId,componentType:node.dataset.operlyComponentType,pageId:node.dataset.operlyPageId,routeId:node.dataset.operlyRouteId,entityId:node.dataset.operlyEntityId,fieldId:node.dataset.operlyFieldId,multi:event.shiftKey},location.origin)},true)})();'''
    return f'<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>{css}</style></head><body><nav aria-label="Application pages">{"".join(nav)}</nav><main>{body}</main><script id="operly-runtime-config" type="application/json">{_safe_json(config)}</script><script src="/static/managed-runtime.js"></script></body></html>'

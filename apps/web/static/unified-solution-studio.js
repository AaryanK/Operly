(() => {
  const S = {
    solutions: [], active: null, runtime: null, source: null, selected: null,
    viewport: "desktop", pending: null, messages: [], leftOpen: true, rightOpen: true,
  };
  const $ = (q, r=document) => r.querySelector(q);
  const $$ = (q, r=document) => [...r.querySelectorAll(q)];
  const esc = (v="") => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const compact = (v, n=700) => String(v ?? "").replace(/\s+/g, " ").trim().slice(0,n);
  const kind = s => ({digital_presence:"Website",business_app:"Business app",custom_solution:"Custom software"}[s?.solution_type] || "Solution");
  const status = s => String(s?.status || "draft").replaceAll("_", " ");
  const root = () => $("#content");
  const title = v => { const n=$("#page-title"); if(n) n.textContent=v; };
  const isWebsite = () => S.runtime?.kind === "studio";

  function friendly(error) {
    if (!error) return "Something went wrong.";
    const raw = String(error.message || error);
    try {
      const parsed = JSON.parse(raw);
      const detail = parsed.detail || parsed;
      if (typeof detail === "string") return detail;
      if (detail?.message) return detail.message;
    } catch {}
    return raw.replace(/^Error:\s*/i, "").slice(0,1200);
  }

  function setImmersive(on) {
    document.body.classList.toggle("operly-studio-open", !!on);
  }

  function previewState(state, label) {
    const n=$("#ss-preview-state"); if(!n) return;
    n.className=`ss-preview-state ${state}`;
    n.innerHTML=`<i></i><span>${esc(label)}</span>`;
  }

  function selector(el) {
    if (el.id) return `#${el.id.replace(/[^A-Za-z0-9_-]/g,"_")}`;
    const out=[]; let node=el;
    while(node && node.nodeType===1 && out.length<5){
      let part=node.tagName.toLowerCase();
      const classes=[...node.classList].slice(0,2).map(x=>x.replace(/[^A-Za-z0-9_-]/g,"_"));
      if(classes.length) part += "." + classes.join(".");
      out.unshift(part); node=node.parentElement;
    }
    return out.join(" > ");
  }

  function selectionFromLegacy(el, frame) {
    const view=el.ownerDocument.defaultView, rect=el.getBoundingClientRect(), cs=view.getComputedStyle(el), style={};
    ["display","position","width","height","color","backgroundColor","fontSize","fontWeight","lineHeight","padding","margin","gap","borderRadius","flexDirection","justifyContent","alignItems","gridTemplateColumns"].forEach(k=>{
      if(cs[k] && cs[k]!=="none" && cs[k]!=="normal") style[k]=compact(cs[k],200);
    });
    return {
      selector:selector(el), tag:el.tagName.toLowerCase(), text:compact(el.textContent,900), outerHTML:compact(el.outerHTML,2600),
      rect:{x:Math.round(rect.x),y:Math.round(rect.y),width:Math.round(rect.width),height:Math.round(rect.height)}, computedStyle:style,
      componentId:el.dataset?.operlyComponentId||null, componentType:el.dataset?.operlyComponentType||null,
      pageId:el.dataset?.operlyPageId||null, entityId:el.dataset?.operlyEntityId||null, fieldId:el.dataset?.operlyFieldId||null,
      page:{title:compact(el.ownerDocument.title,200),path:new URL(frame.src,location.origin).pathname}
    };
  }

  function setSelection(value) {
    S.selected=value||null;
    const label=$("#ss-selected");
    if(label) label.textContent=S.selected ? (S.selected.componentType||S.selected.componentId||S.selected.selector||S.selected.tag||"Selected element") : "Whole page";
    renderInspector();
    renderContextLine();
  }

  function clearSelection() {
    setSelection(null);
    if (!S.source) {
      try {
        const doc=$("#ss-frame")?.contentDocument;
        doc?.querySelectorAll("[data-ss-selected]").forEach(node=>{
          node.style.outline=node.dataset.ssOutline||"";
          node.removeAttribute("data-ss-selected");
        });
      } catch {}
    }
  }

  function bindLegacyFrame(frame) {
    if(S.source) return;
    try {
      const doc=frame.contentDocument;
      if(!doc) return;
      previewState("ready","Live preview");
      if(doc.documentElement.dataset.ssBound) return;
      doc.documentElement.dataset.ssBound="1";
      doc.addEventListener("click", event=>{
        const el=event.target?.closest?.("*"); if(!el) return;
        event.preventDefault(); event.stopPropagation(); event.stopImmediatePropagation();
        doc.querySelectorAll("[data-ss-selected]").forEach(node=>{
          node.style.outline=node.dataset.ssOutline||"";
          node.removeAttribute("data-ss-selected");
        });
        el.dataset.ssOutline=el.style.outline||""; el.dataset.ssSelected="1";
        el.style.outline="2px solid #65b7ff"; el.style.outlineOffset="3px";
        setSelection(selectionFromLegacy(el,frame));
      },true);
    } catch {
      previewState("ready","Live preview");
    }
  }

  window.addEventListener("message", event=>{
    if(event.source !== $("#ss-frame")?.contentWindow) return;
    if(event.data?.type === "OPERLY_STUDIO_SELECT" && event.data.selection) setSelection(event.data.selection);
  });

  function renderInspector() {
    const host=$("#ss-inspector"); if(!host) return;
    const x=S.selected;
    if(!x){
      host.innerHTML=`<div class="ss-empty"><span>⌖</span><h3>Nothing selected</h3><p>Click any part of the website. Operly will attach the element, page and visual evidence to your next message.</p></div>`;
      return;
    }
    host.innerHTML=`
      <div class="ss-inspect-head"><div><small>SELECTED</small><h3>${esc(x.componentType||x.tag||"Element")}</h3></div><button id="ss-clear" aria-label="Clear selection">×</button></div>
      <dl class="ss-inspect-list">
        <div><dt>Identity</dt><dd>${esc(x.componentId||x.selector||"—")}</dd></div>
        ${x.rect?`<div><dt>Size</dt><dd>${esc(x.rect.width)} × ${esc(x.rect.height)}</dd></div>`:""}
        ${x.page?.title?`<div><dt>Page</dt><dd>${esc(x.page.title)}</dd></div>`:""}
      </dl>
      ${x.text?`<section class="ss-inspect-copy"><small>CONTENT</small><p>${esc(x.text)}</p></section>`:""}
      ${x.computedStyle?`<details><summary>Visual evidence</summary><pre>${esc(JSON.stringify(x.computedStyle,null,2))}</pre></details>`:""}`;
    $("#ss-clear")?.addEventListener("click",clearSelection);
  }

  function renderContextLine() {
    const n=$("#ss-command-context"); if(!n) return;
    const source = S.source ? `Source S${S.source.sourceVersion}` : "Legacy website";
    const selected = S.selected ? ` · ${S.selected.componentType||S.selected.tag||"element"} selected` : " · whole page";
    n.textContent = isWebsite() ? `${source}${selected} · ${S.viewport}` : `${kind(S.active)}${selected} · ${S.viewport}`;
  }

  function recordMessage(role, content) {
    S.messages.push({role,content:compact(content,4000)});
    S.messages=S.messages.slice(-16);
  }

  function msg(who, body, meta="") {
    const thread=$("#ss-thread"); if(!thread) return;
    const role=who==="me"?"user":"assistant";
    recordMessage(role,body);
    thread.insertAdjacentHTML("beforeend",`<article class="ss-msg ${who}"><span>${who==="me"?"You":"✦"}</span><div><p>${esc(body)}</p>${meta?`<small>${esc(meta)}</small>`:""}</div></article>`);
    while(thread.children.length>8) thread.firstElementChild.remove();
    thread.scrollTop=thread.scrollHeight;
  }

  async function runtime() {
    if(S.runtime) return S.runtime;
    const response=await fetch(`/api/solutions/${encodeURIComponent(S.active.id)}/preview`,{credentials:"same-origin",redirect:"follow",cache:"no-store"});
    if(!response.ok) throw new Error(`Preview could not be resolved (${response.status})`);
    const path=new URL(response.url,location.origin).pathname; let match;
    if((match=path.match(/^\/api\/studio\/projects\/([^/]+)\/preview$/))) S.runtime={kind:"studio",id:match[1]};
    else if((match=path.match(/^\/apps\/([^/]+)\/preview$/))) S.runtime={kind:"app",id:match[1]};
    else if((match=path.match(/^\/api\/custom-software\/projects\/([^/]+)\/preview$/))) S.runtime={kind:"generated",id:match[1]};
    else S.runtime={kind:"unknown",id:null};
    return S.runtime;
  }

  async function refreshSourceState(rt) {
    if(rt.kind!=="studio") { S.source=null; return null; }
    try { S.source=await api(`/studio/projects/${rt.id}/source`); }
    catch { S.source=null; }
    renderContextLine();
    return S.source;
  }

  function previewUrl(rt, sourceId=null) {
    if(rt?.kind==="studio" && S.source) return `/api/studio/projects/${rt.id}/source/preview/${sourceId?`?sourceId=${encodeURIComponent(sourceId)}`:""}`;
    if(rt?.kind==="studio") return `/api/studio/projects/${rt.id}/preview`;
    if(rt?.kind==="app") return `/apps/${rt.id}/preview`;
    if(rt?.kind==="generated") return `/api/custom-software/projects/${rt.id}/preview`;
    return S.active?`/api/solutions/${S.active.id}/preview`:"about:blank";
  }

  async function loadPreview(url=null) {
    const frame=$("#ss-frame"); if(!frame) return;
    previewState("loading","Loading preview…"); clearSelection();
    const errorHost=$("#ss-preview-error"); if(errorHost) errorHost.hidden=true;
    try {
      const rt=S.runtime||await runtime();
      const target=url||previewUrl(rt);
      if(rt.kind==="studio" && S.source) frame.setAttribute("sandbox","allow-scripts");
      else frame.removeAttribute("sandbox");
      const join=target.includes("?")?"&":"?";
      frame.src=`${target}${join}studio=${Date.now()}`;
      const open=$("#ss-open-preview"); if(open) open.href=target;
    } catch(error) {
      previewState("error","Preview unavailable");
      if(errorHost){errorHost.hidden=false;$("p",errorHost).textContent=friendly(error);}
    }
  }

  function sourceContext() {
    return {
      route:"/",
      viewport:S.viewport,
      selection:S.selected,
      conversation:S.messages.slice(-10).map(({role,content})=>({role,content})),
    };
  }

  async function generateInitialSource(rt) {
    if(rt.kind!=="studio" || S.source) return;
    previewState("loading","Operly is designing the website…");
    msg("ai","I’m creating the first source version from the business context. The canvas will update when it is ready.","Source agent");
    try {
      const source=await api(`/studio/projects/${rt.id}/source/generate`,{method:"POST",body:JSON.stringify({context:sourceContext()})});
      S.source=source;
      await loadPreview();
      msg("ai",source.summary||"The first source version is ready.",`Source S${source.sourceVersion}`);
      await history();
    } catch(error) {
      msg("ai",friendly(error),"The legacy preview is still untouched");
      S.source=null;
      await loadPreview(`/api/studio/projects/${rt.id}/preview`);
    }
  }

  async function websiteEdit(rt,instruction) {
    const prior=S.source?.id||null;
    const source=await api(`/studio/projects/${rt.id}/source/edits`,{method:"POST",body:JSON.stringify({instruction,context:sourceContext()})});
    S.source=source;
    S.pending={kind:"source",prior,current:source.id};
    await loadPreview(`/api/studio/projects/${rt.id}/source/preview/?sourceId=${encodeURIComponent(source.id)}`);
    msg("ai",source.summary||"I updated the website source and refreshed the preview.",`Source S${source.sourceVersion} · reversible`);
    changebar(); await history();
  }

  async function appEdit(rt,instruction) {
    const [app,me]=await Promise.all([api(`/application-builder/applications/${rt.id}`),api("/me")]);
    const ids=S.selected?.componentId?[S.selected.componentId]:[];
    const change=await api("/application-builder/proposals",{method:"POST",body:JSON.stringify({message:instruction,context:{workspaceId:me.tenant.id,applicationId:rt.id,route:"/",pageId:S.selected?.pageId||null,mode:"studio",selectionScope:ids.length?"component":"application",selectedIds:ids,selectedMetadata:S.selected?[S.selected]:[],activeVersionId:app.activeVersionId,viewport:S.viewport,userRole:me.role}})});
    await api(`/application-builder/change-sets/${change.id}/preview`,{method:"POST",body:"{}"});
    S.pending={kind:"app",id:change.id,ops:change.operations||[]};
    await loadPreview(`/apps/${rt.id}/preview?changeSetId=${encodeURIComponent(change.id)}`);
    msg("ai","I prepared the application change and loaded its preview.",`${S.pending.ops.length} operation${S.pending.ops.length===1?"":"s"} · not applied yet`);
    changebar();
  }

  async function send() {
    const input=$("#ss-input"),button=$("#ss-send"),instruction=input?.value.trim();
    if(!instruction||!S.active||button?.disabled) return;
    msg("me",instruction,S.selected?`Context: ${S.selected.componentType||S.selected.selector||S.selected.tag}`:"Context: whole Solution");
    input.value=""; input.style.height="auto"; button.disabled=true; button.textContent="Working…";
    previewState("loading","Operly is editing…");
    try {
      const rt=await runtime();
      if(rt.kind==="studio") await websiteEdit(rt,instruction);
      else if(rt.kind==="app") await appEdit(rt,instruction);
      else msg("ai","This generated-software runtime still uses its dedicated coding-harness flow. I did not mutate it from this Studio session.","No change applied");
    } catch(error) {
      msg("ai",friendly(error),"Your previous version is unchanged");
      previewState("ready","Preview unchanged");
    } finally {
      button.disabled=false; button.textContent="Send"; input.focus();
    }
  }

  function changebar() {
    const bar=$("#ss-change"),change=S.pending; if(!bar) return;
    if(!change){bar.classList.add("hidden");bar.innerHTML="";return;}
    bar.classList.remove("hidden");
    if(change.kind==="app"){
      bar.innerHTML=`<div><small>PROPOSED APPLICATION CHANGE</small><strong>${change.ops.length} validated operation${change.ops.length===1?"":"s"}</strong></div><div><button id="ss-discard">Discard</button><button class="primary" id="ss-apply">Apply</button></div>`;
      $("#ss-apply").onclick=async()=>{try{const r=await api(`/application-builder/change-sets/${change.id}/apply`,{method:"POST",body:"{}"});S.pending=null;await loadPreview();msg("ai","Applied. The new application version is active.",`Version ${r.versionNumber}`);changebar();history()}catch(e){msg("ai",friendly(e),"Apply failed")}};
      $("#ss-discard").onclick=async()=>{S.pending=null;await loadPreview();msg("ai","Discarded. The active application was not changed.");changebar()};
      return;
    }
    bar.innerHTML=`<div><small>SOURCE UPDATED</small><strong>Source S${S.source?.sourceVersion||""} · immutable and reversible</strong></div><div>${change.prior?'<button id="ss-undo">Undo</button>':""}<button class="primary" id="ss-keep">Keep</button></div>`;
    $("#ss-keep").onclick=()=>{S.pending=null;changebar()};
    $("#ss-undo")?.addEventListener("click",async()=>{
      try{
        const restored=await api(`/studio/projects/${S.runtime.id}/source/${change.prior}/rollback`,{method:"POST",body:"{}"});
        S.source=restored;S.pending=null;
        await loadPreview(`/api/studio/projects/${S.runtime.id}/source/preview/?sourceId=${encodeURIComponent(restored.id)}`);
        msg("ai","Undone. I restored the previous source as a new immutable version.",`Source S${restored.sourceVersion}`);
        changebar();history();
      }catch(error){msg("ai",friendly(error),"Undo failed")}
    });
  }

  async function history() {
    const host=$("#ss-history"); if(!host||!S.active) return;
    try{
      const rows=await api(`/solutions/${S.active.id}/versions`);
      host.innerHTML=rows.slice(0,14).map((v,i)=>`<article class="${i===0?"active":""}"><span>${esc(v.version)}</span><div><strong>${esc(v.summary||v.status||"Version")}</strong><small>${esc(v.kind==="legacy_schema"?"Legacy · "+v.status:v.status||"")}</small></div></article>`).join("")||'<p class="ss-history-empty">No versions yet.</p>';
    }catch(error){host.innerHTML=`<p class="ss-history-empty">${esc(friendly(error))}</p>`}
  }

  async function publish() {
    const button=$("#ss-publish"); if(!button) return;
    button.disabled=true;button.textContent="Publishing…";
    try{
      const result=await api(`/solutions/${S.active.id}/approve`,{method:"POST",body:"{}"});
      S.active=result.solution||S.active;
      const job=result.job||{};
      if(job.status==="succeeded") msg("ai","Published. The current verified website is live.",S.active.production?.url||"Live");
      else if(job.status==="failed") msg("ai",job.evidence?.reason||"Publishing failed. The previous live version was preserved.","Publish failed");
      else msg("ai","Publishing is in progress.",job.status||"queued");
    }catch(error){msg("ai",friendly(error),"Publish failed")}
    finally{button.disabled=false;button.textContent="Publish"}
  }

  function setPane(side,open) {
    if(side==="left") S.leftOpen=open; else S.rightOpen=open;
    const shell=$(".ss-shell"); if(!shell) return;
    shell.classList.toggle("left-closed",!S.leftOpen);shell.classList.toggle("right-closed",!S.rightOpen);
    $("#ss-project-toggle")?.setAttribute("aria-pressed",String(S.leftOpen));
    $("#ss-inspector-toggle")?.setAttribute("aria-pressed",String(S.rightOpen));
  }

  function wireEditor() {
    $("#ss-back")?.addEventListener("click",()=>{setImmersive(false);list()});
    $("#ss-project-toggle")?.addEventListener("click",()=>setPane("left",!S.leftOpen));
    $("#ss-inspector-toggle")?.addEventListener("click",()=>setPane("right",!S.rightOpen));
    $("#ss-refresh")?.addEventListener("click",()=>loadPreview());
    $("#ss-publish")?.addEventListener("click",publish);
    $("#ss-retry")?.addEventListener("click",()=>loadPreview());
    $("#ss-send")?.addEventListener("click",send);
    const input=$("#ss-input");
    input?.addEventListener("input",()=>{input.style.height="auto";input.style.height=Math.min(140,Math.max(48,input.scrollHeight))+"px"});
    input?.addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();send()}});
    $$('[data-view]').forEach(button=>button.addEventListener("click",()=>{
      S.viewport=button.dataset.view;$$('[data-view]').forEach(x=>x.classList.toggle("active",x===button));
      $("#ss-canvas").dataset.view=S.viewport;renderContextLine();
    }));
    $("#ss-frame")?.addEventListener("load",()=>{
      const errorHost=$("#ss-preview-error");if(errorHost)errorHost.hidden=true;
      if(S.source) previewState("ready","Source preview"); else bindLegacyFrame($("#ss-frame"));
    });
  }

  async function editor(solution,{generate=false}={}) {
    S.active=solution;S.runtime=null;S.source=null;S.selected=null;S.pending=null;S.messages=[];S.viewport="desktop";S.leftOpen=true;S.rightOpen=true;
    setImmersive(true);title(`${solution.name} · Studio`);
    root().innerHTML=`
      <section class="ss-shell">
        <header class="ss-top">
          <div class="ss-brand"><button id="ss-back" aria-label="Back to Solutions">←</button><div><small>${esc(kind(solution))}</small><h2>${esc(solution.name)}</h2></div><em>${esc(status(solution))}</em></div>
          <div class="ss-actions">
            <button id="ss-project-toggle" class="ss-pane-toggle" aria-pressed="true">Project</button>
            <button id="ss-inspector-toggle" class="ss-pane-toggle" aria-pressed="true">Inspector</button>
            <div class="ss-view"><button class="active" data-view="desktop">Desktop</button><button data-view="tablet">Tablet</button><button data-view="mobile">Mobile</button></div>
            <a id="ss-open-preview" target="_blank" rel="noopener">Open</a><button id="ss-refresh">Refresh</button><button class="primary" id="ss-publish">Publish</button>
          </div>
        </header>
        <div class="ss-work">
          <aside class="ss-left"><header><span>PROJECT</span><button title="Collapse project" onclick="document.querySelector('#ss-project-toggle')?.click()">‹</button></header><nav><button class="active"><b>Canvas</b><small>Live website</small></button></nav><section><span class="ss-side-label">HISTORY</span><div id="ss-history"></div></section></aside>
          <main class="ss-main">
            <div class="ss-toolbar"><div><button class="active">⌖ Select</button><span id="ss-selected">Whole page</span></div><div id="ss-preview-state" class="ss-preview-state loading"><i></i><span>Loading preview…</span></div></div>
            <div class="ss-canvas" id="ss-canvas" data-view="desktop"><div class="ss-device"><iframe id="ss-frame" title="Solution preview"></iframe><div class="ss-preview-error" id="ss-preview-error" hidden><strong>Preview unavailable</strong><p></p><button id="ss-retry">Retry</button></div></div></div>
            <div id="ss-change" class="ss-change hidden"></div>
            <section class="ss-chat"><div id="ss-thread"></div><div class="ss-command"><div class="ss-command-head"><span>✦ Ask Operly</span><small id="ss-command-context">Loading context…</small></div><div class="ss-compose"><textarea id="ss-input" rows="1" placeholder="Tell Operly what you want to change…"></textarea><button id="ss-send" class="primary">Send</button></div><footer>Enter to send · Shift+Enter for a new line · current project, selection and recent Studio conversation are attached automatically</footer></div></section>
          </main>
          <aside class="ss-right"><header><span>INSPECTOR</span><button title="Collapse inspector" onclick="document.querySelector('#ss-inspector-toggle')?.click()">›</button></header><div id="ss-inspector"></div></aside>
        </div>
      </section>`;
    wireEditor();renderInspector();history();
    try{
      const rt=await runtime();await refreshSourceState(rt);await loadPreview();
      if(generate && rt.kind==="studio" && !S.source) generateInitialSource(rt);
    }catch(error){previewState("error","Preview unavailable");msg("ai",friendly(error),"No changes applied")}
  }

  function createModal() {
    const d=document.createElement("dialog");d.className="ss-modal";
    d.innerHTML=`<form id="ss-create-form"><header><div><small>NEW WEBSITE</small><h2>What are we building?</h2><p>Give the project a name. Operly will use the business context it already knows and let the source agent design the first version.</p></div><button type="button" data-close>×</button></header><label>Website name<input id="ss-create-name" maxlength="200" required autocomplete="off" placeholder="e.g. Antu Hill Travels"></label><div id="ss-modal-error" class="ss-modal-error" hidden></div><footer><button type="button" data-close>Cancel</button><button type="submit" class="primary" id="ss-create-submit">Create website</button></footer></form>`;
    document.body.append(d);$$('[data-close]',d).forEach(b=>b.onclick=()=>d.close());d.addEventListener("close",()=>d.remove());d.showModal();setTimeout(()=>$("#ss-create-name",d)?.focus(),20);
    $("#ss-create-form",d).onsubmit=async event=>{
      event.preventDefault();const name=$("#ss-create-name",d).value.trim(),submit=$("#ss-create-submit",d),error=$("#ss-modal-error",d);if(!name)return;
      submit.disabled=true;submit.textContent="Creating…";error.hidden=true;
      try{const solution=await api("/solutions",{method:"POST",body:JSON.stringify({solution_type:"digital_presence",name})});d.close();editor(solution,{generate:true})}
      catch(ex){error.textContent=friendly(ex);error.hidden=false;submit.disabled=false;submit.textContent="Create website"}
    };
  }

  function card(solution) {
    const preview=solution.preview?.url;
    return `<article class="ss-card"><div class="ss-thumb">${preview?`<iframe src="${esc(preview)}" tabindex="-1" loading="lazy" sandbox=""></iframe>`:`<div>No preview yet</div>`}</div><div class="ss-cardbody"><div><small>${esc(kind(solution))}</small><em>${esc(status(solution))}</em></div><h3>${esc(solution.name)}</h3><p>${esc(solution.description||"Open Studio to build and refine this Solution.")}</p><footer><span>${solution.production?.state==="live"?"● Live":"Private"}</span><button data-open-solution="${esc(solution.id)}">Open Studio →</button></footer></div></article>`;
  }

  async function list() {
    setImmersive(false);title("Solutions");S.active=null;S.runtime=null;S.source=null;S.pending=null;S.selected=null;S.messages=[];
    const host=root();if(!host)return;host.innerHTML='<div class="ss-list-loading"><span></span><p>Loading Solutions…</p></div>';
    try{
      S.solutions=await api("/solutions");
      host.innerHTML=`<section class="ss-list"><header><div><small>BUILD & RUN</small><h2>Solutions</h2><p>Websites, business apps and custom software in one workspace.</p></div><button class="primary" id="ss-new">+ New website</button></header>${S.solutions.length?`<div class="ss-cards">${S.solutions.map(card).join("")}</div>`:`<div class="ss-list-empty"><span>✦</span><h3>Build your first Solution</h3><p>Start with a website. Operly will create real source you can keep shaping through conversation.</p><button class="primary" id="ss-empty-new">Create website</button></div>`}</section>`;
      $("#ss-new")?.addEventListener("click",createModal);$("#ss-empty-new")?.addEventListener("click",createModal);
      $$('[data-open-solution]').forEach(button=>button.addEventListener("click",()=>{const solution=S.solutions.find(x=>x.id===button.dataset.openSolution);if(solution)editor(solution)}));
    }catch(error){host.innerHTML=`<div class="ss-list-error"><strong>Solutions could not load</strong><p>${esc(friendly(error))}</p><button id="ss-list-retry">Retry</button></div>`;$("#ss-list-retry")?.addEventListener("click",list)}
  }

  window.operlyStudio=list;
  window.operlyUnifiedSolutionStudio={state:S,open:editor,list};
})();

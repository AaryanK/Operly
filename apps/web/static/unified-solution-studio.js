(() => {
  const S = {
    solutions: [], active: null, runtime: null, source: null, selected: null,
    viewport: "desktop", pending: null, messages: [], leftOpen: true, rightOpen: true,
    run: null, runPollToken: 0,
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
  const sleep = ms => new Promise(resolve => setTimeout(resolve, ms));
  const activeRun = run => ["queued","running"].includes(run?.state);

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

  function setBusy(on) {
    const button=$("#ss-send");
    if(button){button.disabled=!!on;button.textContent=on?"Running…":"Send";}
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
    while(thread.children.length>12) thread.firstElementChild.remove();
    thread.scrollTop=thread.scrollHeight;
  }

  function eventMeta(event) {
    const d=event?.detail||{};
    const bits=[];
    if(d.step) bits.push(`turn ${d.step}`);
    if(d.tool) bits.push(d.tool);
    if(d.path) bits.push(d.path);
    if(d.elapsedSeconds!=null) bits.push(`${Math.round(Number(d.elapsedSeconds)||0)}s`);
    return bits.join(" · ");
  }

  function renderRun(run) {
    S.run=run||null;
    const host=$("#ss-run-trace"); if(!host) return;
    if(!run){host.hidden=true;host.innerHTML="";return;}
    host.hidden=false;
    const events=Array.isArray(run.events)?run.events:[];
    const latest=events.at(-1);
    const elapsed=run.elapsedSeconds==null?"":`${Math.round(Number(run.elapsedSeconds)||0)}s`;
    const stateLabel={queued:"Queued",running:"Working",succeeded:"Complete",failed:"Stopped",needs_input:"Needs input"}[run.state]||run.state;
    const rows=events.slice(-12).map(event=>{
      const detail=eventMeta(event);
      const failed=event.phase==="error"||event.detail?.ok===false;
      return `<div class="ss-run-event ${failed?"bad":""}"><i></i><div><strong>${esc(event.summary)}</strong>${detail?`<small>${esc(detail)}</small>`:""}${event.detail?.detail?`<em>${esc(compact(event.detail.detail,700))}</em>`:""}</div></div>`;
    }).join("");
    host.innerHTML=`
      <div class="ss-run-head"><div><span class="ss-run-dot ${esc(run.state)}"></span><strong>${esc(stateLabel)}</strong><small>${esc(run.modelId||"authorizing model")}${elapsed?` · ${esc(elapsed)}`:""}</small></div><span>${esc(run.operation||"edit")}</span></div>
      <div class="ss-run-now">${esc(latest?.summary||"Preparing Studio agent…")}</div>
      <details ${activeRun(run)?"open":""}><summary>Agent activity <span>${events.length} event${events.length===1?"":"s"}</span></summary><div class="ss-run-events">${rows||'<p>Waiting for the first harness event…</p>'}</div></details>`;
  }

  async function runtime() {
    if(S.runtime) return S.runtime;
    const hint=S.active?.runtime;
    if(hint?.kind==="studio" && hint.id){S.runtime={kind:"studio",id:hint.id};return S.runtime;}
    if(hint?.kind==="app" && hint.id){S.runtime={kind:"app",id:hint.id};return S.runtime;}
    if(hint?.kind==="generated" && hint.id){
      const project=await api(`/custom-software/projects/${hint.id}`);
      S.runtime={kind:"generated",id:hint.id,planId:project.planId||null,approvedVersion:project.approvedPlanVersion||null,previewUrl:S.active.preview?.url||`/api/custom-software/projects/${hint.id}/preview`};
      return S.runtime;
    }
    const response=await fetch(`/api/solutions/${encodeURIComponent(S.active.id)}/preview`,{credentials:"same-origin",redirect:"follow",cache:"no-store"});
    if(!response.ok) throw new Error(`Preview could not be resolved (${response.status})`);
    const path=new URL(response.url,location.origin).pathname; let match;
    if((match=path.match(/^\/api\/studio\/projects\/([^/]+)\/preview$/))) S.runtime={kind:"studio",id:match[1]};
    else if((match=path.match(/^\/apps\/([^/]+)\/preview$/))) S.runtime={kind:"app",id:match[1]};
    else if((match=path.match(/^\/api\/custom-software\/previews\/([^/]+)\/?$/))) S.runtime={kind:"generated",id:null,previewUrl:path};
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
    if(rt?.kind==="generated" && rt.previewUrl) return rt.previewUrl;
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

  async function pollWebsiteRun(rt, initialRun, {prior=null, announce=true}={}) {
    const token=++S.runPollToken;
    let run=initialRun;
    renderRun(run);setBusy(activeRun(run));
    if(activeRun(run)) previewState("loading",run.operation==="generate"?"Operly is designing…":"Operly is editing…");
    while(activeRun(run) && token===S.runPollToken && S.runtime?.id===rt.id){
      await sleep(850);
      try{run=await api(`/studio/projects/${rt.id}/source/runs/${run.id}`);renderRun(run)}
      catch(error){if(token===S.runPollToken) renderRun({...run,state:"failed",error:friendly(error)});break}
    }
    if(token!==S.runPollToken || S.runtime?.id!==rt.id) return run;
    setBusy(false);
    if(run.state==="succeeded"){
      const before=S.source?.id||prior||null;
      await refreshSourceState(rt);
      await loadPreview(S.source?`/api/studio/projects/${rt.id}/source/preview/?sourceId=${encodeURIComponent(S.source.id)}`:null);
      previewState("ready","Source preview");
      if(run.operation==="edit" && S.source) S.pending={kind:"source",prior:before,current:S.source.id};
      if(announce) msg("ai",run.events?.at(-1)?.summary||S.source?.summary||"The source update is ready.",S.source?`Source S${S.source.sourceVersion} · ${run.modelId||"model"}`:"Complete");
      changebar();await history();
    }else if(run.state==="needs_input"){
      previewState("ready","Waiting for your input");
      if(announce) msg("ai",run.error||"I need one decision before I can continue.","No source was changed");
    }else if(run.state==="failed"){
      previewState("ready","Preview unchanged");
      if(announce) msg("ai",run.error||"The source change stopped safely.","Your previous version is unchanged");
    }
    return run;
  }

  async function startWebsiteRun(rt, operation, instruction="", {prior=null,announce=true}={}) {
    const run=await api(`/studio/projects/${rt.id}/source/runs`,{method:"POST",body:JSON.stringify({operation,instruction,context:sourceContext()})});
    return pollWebsiteRun(rt,run,{prior,announce});
  }

  async function resumeLatestRun(rt) {
    if(rt.kind!=="studio") return null;
    try{
      const run=await api(`/studio/projects/${rt.id}/source/runs/latest`);
      renderRun(run);
      if(activeRun(run)) return pollWebsiteRun(rt,run,{prior:S.source?.id||null,announce:true});
      return run;
    }catch{renderRun(null);return null}
  }

  async function generateInitialSource(rt) {
    if(rt.kind!=="studio" || S.source) return;
    msg("ai","I’m creating the first real source version from the business context. The live trace below shows what the harness is doing.","Source agent");
    try { await startWebsiteRun(rt,"generate","",{announce:true}); }
    catch(error){msg("ai",friendly(error),"The legacy preview is still untouched");S.source=null;await loadPreview(`/api/studio/projects/${rt.id}/preview`)}
  }

  async function websiteEdit(rt,instruction) {
    const prior=S.source?.id||null;
    await startWebsiteRun(rt,"edit",instruction,{prior,announce:true});
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

  function generatedEditMode(instruction) {
    if(S.selected) return "visual";
    if(/\b(api|backend|database|server|endpoint|persist|storage|auth|login|worker|job)\b/i.test(instruction)) return "backend";
    if(/\b(css|style|layout|responsive|frontend|button|color|spacing|mobile|tablet|component)\b/i.test(instruction)) return "frontend";
    return "source";
  }

  function generatedBuildKey() {
    const suffix=globalThis.crypto?.randomUUID?.()||Math.random().toString(36).slice(2);
    return `studio-edit-${Date.now()}-${suffix}`.slice(0,120);
  }

  async function generatedEdit(rt,instruction) {
    if(!rt.planId||!rt.approvedVersion) throw new Error("This generated Solution is not linked to an approved coding plan yet.");
    const mode=generatedEditMode(instruction);
    previewState("loading","Editing source…");
    const updated=await api(`/coding-harness/plans/${rt.planId}/source/edits`,{method:"POST",body:JSON.stringify({planId:rt.planId,approvedVersion:rt.approvedVersion,instruction,mode,context:sourceContext()})});
    msg("ai",updated.summary||"The source edit is ready. I’m building and verifying it now.",`Source S${updated.sourceVersion} · ${mode}`);
    previewState("loading","Building & verifying…");
    const build=await api("/coding-harness/builds",{method:"POST",body:JSON.stringify({planId:rt.planId,approvedVersion:rt.approvedVersion,idempotencyKey:generatedBuildKey()})});
    if(build.state!=="preview_ready"||!build.preview?.url){
      const evidence=build.result?.failureEvidence||{};
      throw new Error(evidence.message||build.failureClassification||`Generated app build stopped in ${build.state||"an unknown state"}`);
    }
    rt.previewUrl=build.preview.url;
    S.source=build.source||updated;
    await loadPreview(rt.previewUrl);
    previewState("ready","Verified app preview");
    const repairs=Number(build.repairCount||0);
    msg("ai","Updated, built, tested and loaded the verified application preview.",`Source S${build.source?.sourceVersion||updated.sourceVersion}${repairs?` · ${repairs} automatic repair${repairs===1?"":"s"}`:" · checks passed"}`);
    await history();
  }

  async function send() {
    const input=$("#ss-input"),button=$("#ss-send"),instruction=input?.value.trim();
    if(!instruction||!S.active||button?.disabled) return;
    msg("me",instruction,S.selected?`Context: ${S.selected.componentType||S.selected.selector||S.selected.tag}`:"Context: whole Solution");
    input.value=""; input.style.height="auto"; setBusy(true);
    try {
      const rt=await runtime();
      if(rt.kind==="studio") await websiteEdit(rt,instruction);
      else if(rt.kind==="app") await appEdit(rt,instruction);
      else if(rt.kind==="generated") await generatedEdit(rt,instruction);
      else msg("ai","I could not resolve this Solution's editable runtime.","No change applied");
    } catch(error) {
      msg("ai",friendly(error),"Your previous verified version is unchanged");
      previewState("ready","Preview unchanged");
    } finally {
      setBusy(false);input.focus();
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
    $("#ss-back")?.addEventListener("click",()=>{S.runPollToken++;setImmersive(false);list()});
    $("#ss-project-toggle")?.addEventListener("click",()=>setPane("left",!S.leftOpen));
    $("#ss-inspector-toggle")?.addEventListener("click",()=>setPane("right",!S.rightOpen));
    $("#ss-refresh")?.addEventListener("click",()=>loadPreview());
    $("#ss-publish")?.addEventListener("click",publish);
    $("#ss-retry")?.addEventListener("click",()=>loadPreview());
    $("#ss-send")?.addEventListener("click",send);
    const input=$("#ss-input");
    input?.addEventListener("input",()=>{input.style.height="auto";input.style.height=Math.min(220,Math.max(64,input.scrollHeight))+"px"});
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
    S.runPollToken++;S.active=solution;S.runtime=null;S.source=null;S.selected=null;S.pending=null;S.messages=[];S.run=null;S.viewport="desktop";S.leftOpen=true;S.rightOpen=true;
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
            <section class="ss-chat"><section id="ss-run-trace" class="ss-run-trace" hidden></section><div id="ss-thread"></div><div class="ss-command"><div class="ss-command-head"><span>✦ Ask Operly</span><small id="ss-command-context">Loading context…</small></div><div class="ss-compose"><textarea id="ss-input" rows="2" placeholder="Tell Operly what you want to change…"></textarea><button id="ss-send" class="primary">Send</button></div><footer>Live agent activity appears above · project, business context, selection and recent Studio conversation are attached automatically</footer></div></section>
          </main>
          <aside class="ss-right"><header><span>INSPECTOR</span><button title="Collapse inspector" onclick="document.querySelector('#ss-inspector-toggle')?.click()">›</button></header><div id="ss-inspector"></div></aside>
        </div>
      </section>`;
    wireEditor();renderInspector();history();
    try{
      const rt=await runtime();await refreshSourceState(rt);await loadPreview();
      const latest=await resumeLatestRun(rt);
      if(generate && rt.kind==="studio" && !S.source && !activeRun(latest)) generateInitialSource(rt);
    }catch(error){previewState("error","Preview unavailable");msg("ai",friendly(error),"No changes applied")}
  }

  async function createWebsite(button=null) {
    const existing=S.solutions.find(solution=>solution.solution_type==="digital_presence");
    if(existing){editor(existing);return;}
    const priorLabel=button?.textContent||"Create website";
    if(button){button.disabled=true;button.textContent="Creating…";}
    const error=$("#ss-create-error");if(error){error.hidden=true;error.textContent="";}
    try{
      // Digital Presence is workspace-scoped today. The server derives its identity
      // from the active tenant's CompanyProfile, falling back to the workspace name.
      const solution=await api("/solutions",{method:"POST",body:JSON.stringify({solution_type:"digital_presence"})});
      const index=S.solutions.findIndex(item=>item.id===solution.id);
      if(index>=0)S.solutions[index]=solution;else S.solutions.unshift(solution);
      editor(solution,{generate:true});
    }catch(ex){
      if(error){error.textContent=friendly(ex);error.hidden=false;}
      else alert(friendly(ex));
      if(button){button.disabled=false;button.textContent=priorLabel;}
    }
  }

  function card(solution) {
    const preview=solution.preview?.url;
    return `<article class="ss-card"><div class="ss-thumb">${preview?`<iframe src="${esc(preview)}" tabindex="-1" loading="lazy" sandbox=""></iframe>`:`<div>No preview yet</div>`}</div><div class="ss-cardbody"><div><small>${esc(kind(solution))}</small><em>${esc(status(solution))}</em></div><h3>${esc(solution.name)}</h3><p>${esc(solution.description||"Open Studio to build and refine this Solution.")}</p><footer><span>${solution.production?.state==="live"?"● Live":"Private"}</span><button data-open-solution="${esc(solution.id)}">Open Studio →</button></footer></div></article>`;
  }

  async function list() {
    S.runPollToken++;setImmersive(false);title("Solutions");S.active=null;S.runtime=null;S.source=null;S.pending=null;S.selected=null;S.messages=[];S.run=null;
    const host=root();if(!host)return;host.innerHTML='<div class="ss-list-loading"><span></span><p>Loading Solutions…</p></div>';
    try{
      S.solutions=await api("/solutions");
      const website=S.solutions.find(solution=>solution.solution_type==="digital_presence");
      const websiteAction=website
        ? '<button class="primary" id="ss-open-website">Open website</button>'
        : '<button class="primary" id="ss-new">Create website</button>';
      host.innerHTML=`<section class="ss-list"><header><div><small>BUILD & RUN</small><h2>Solutions</h2><p>Websites, business apps and custom software in one workspace.</p></div>${websiteAction}</header><div id="ss-create-error" class="ss-modal-error" hidden></div>${S.solutions.length?`<div class="ss-cards">${S.solutions.map(card).join("")}</div>`:`<div class="ss-list-empty"><span>✦</span><h3>Build your first Solution</h3><p>Create the workspace website directly from the business context Operly already knows.</p><button class="primary" id="ss-empty-new">Create website</button></div>`}</section>`;
      $("#ss-open-website")?.addEventListener("click",()=>editor(website));
      $("#ss-new")?.addEventListener("click",event=>createWebsite(event.currentTarget));
      $("#ss-empty-new")?.addEventListener("click",event=>createWebsite(event.currentTarget));
      $$('[data-open-solution]').forEach(button=>button.addEventListener("click",()=>{const solution=S.solutions.find(x=>x.id===button.dataset.openSolution);if(solution)editor(solution)}));
    }catch(error){host.innerHTML=`<div class="ss-list-error"><strong>Solutions could not load</strong><p>${esc(friendly(error))}</p><button id="ss-list-retry">Retry</button></div>`;$("#ss-list-retry")?.addEventListener("click",list)}
  }

  window.operlyStudio=list;
  window.operlyUnifiedSolutionStudio={state:S,open:editor,list};
})();

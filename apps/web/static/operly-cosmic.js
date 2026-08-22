(() => {
  const KEY="operly.workspace.navigation.collapsed";
  const AUTH_BOOT="20260822-auth-v3";
  const $=(q,r=document)=>r.querySelector(q);
  let editTicker=null;
  let authRedirectPending=false;
  const nativeFetch=window.fetch.bind(window);

  // Authentication is a hard application boundary. A successful session should
  // not depend on the signed-out DOM, legacy renderers, or an old cached auth.js
  // finishing its own in-page transition. Keep this small interception limited to
  // successful authentication endpoints only.
  window.fetch=async(...args)=>{
    const response=await nativeFetch(...args);
    try{
      const request=args[0];
      const raw=typeof request==="string"?request:request?.url;
      const path=raw?new URL(raw,location.href).pathname:"";
      if(response.ok&&path==="/api/auth/login"){
        if(!authRedirectPending){
          authRedirectPending=true;
          setTimeout(()=>location.replace(`/app?auth_boot=${AUTH_BOOT}`),0);
        }
      }else if(response.ok&&path==="/api/auth/google"){
        response.clone().json().then(body=>{
          if(body?.new_account||authRedirectPending)return;
          authRedirectPending=true;
          location.replace(`/app?auth_boot=${AUTH_BOOT}`);
        }).catch(()=>{});
      }
    }catch{}
    return response;
  };

  function ensureCanonicalStyleOrder(){
    const links=[...document.querySelectorAll('link[rel="stylesheet"]')];
    let canonical=links.find(link=>link.href.includes("/static/operly-cosmic.css"));
    if(!canonical){
      canonical=document.createElement("link");
      canonical.rel="stylesheet";
      canonical.href="/static/operly-cosmic.css?v=20260822-coherence-v2";
    }
    // workspace-shell.js injects its legacy structural layers at runtime. Keep the
    // canonical visual system last so those layers cannot re-introduce green/light
    // color tokens over the dark Operly shell.
    if(document.head.lastElementChild!==canonical)document.head.append(canonical);
  }

  function ensureRepairStyles(){
    if($("#operly-shell-repair-styles"))return;
    const style=document.createElement("style");
    style.id="operly-shell-repair-styles";
    style.textContent=`
      #dashboard.workspace-shell-ready .operly-shell-hero h2,
      #dashboard.workspace-shell-ready .operly-shell-page h2,
      #dashboard.workspace-shell-ready .op-section-title h3{color:#f7f8ff!important}
      #dashboard.workspace-shell-ready .operly-shell-hero p,
      #dashboard.workspace-shell-ready .operly-shell-page p{color:#a7b0c6!important}
      .op-solution-actions{display:flex;align-items:center;gap:9px;flex-wrap:wrap}
      .op-solution-actions .op-new-website{border:1px solid rgba(151,169,255,.18)!important;background:rgba(255,255,255,.05)!important;color:#f7f8ff!important}
      .op-solution-actions .op-new-website:hover{background:rgba(91,129,255,.13)!important;border-color:rgba(113,151,255,.34)!important}
      @media(max-width:760px){
        #dashboard.workspace-shell-ready{display:block!important;padding-bottom:calc(70px + env(safe-area-inset-bottom,0px))!important}
        #dashboard.workspace-shell-ready #mobile-nav-toggle{display:grid!important;place-items:center!important;flex:0 0 40px!important;width:40px!important;height:40px!important;padding:0!important;border:1px solid rgba(151,169,255,.16)!important;border-radius:12px!important;background:rgba(255,255,255,.055)!important;color:#f7f8ff!important;font-size:20px!important;cursor:pointer!important}
        #dashboard.workspace-shell-ready .topbar{position:sticky!important;top:0!important;z-index:72!important;min-height:64px!important}
        #dashboard.workspace-shell-ready .operly-section-nav{display:flex!important;position:fixed!important;inset:0 auto 0 0!important;width:min(318px,88vw)!important;height:100dvh!important;min-height:100dvh!important;z-index:90!important;transform:translateX(-103%)!important;transition:transform .2s ease!important;box-shadow:24px 0 70px rgba(0,0,0,.42)!important}
        #dashboard.workspace-shell-ready.operly-mobile-nav-open .operly-section-nav{transform:translateX(0)!important}
        #dashboard.workspace-shell-ready .mobile-nav-backdrop{display:block!important;position:fixed!important;inset:0!important;z-index:89!important;width:100%!important;height:100%!important;border:0!important;background:rgba(2,4,12,.66)!important;backdrop-filter:blur(4px)!important;opacity:0!important;pointer-events:none!important;transition:opacity .18s ease!important}
        #dashboard.workspace-shell-ready.operly-mobile-nav-open .mobile-nav-backdrop{opacity:1!important;pointer-events:auto!important}
        #dashboard.workspace-shell-ready .operly-section-nav .operly-side-toggle{display:none!important}
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-section-nav{width:min(318px,88vw)!important}
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-workspace-head>button:not(.operly-side-toggle),
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-head-caret,
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-nav-group,
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-nav-item>span:not(.operly-nav-icon),
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-nav-pill,
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-user-meta,
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-user-menu{display:initial!important}
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-nav-item{justify-content:flex-start!important;padding:0 10px!important}
        #dashboard.workspace-shell-ready.operly-nav-collapsed .operly-nav-footer{display:grid!important}
        .op-solution-actions{width:100%;display:grid;grid-template-columns:1fr 1fr}
        .op-solution-actions button{width:100%}
      }
    `;
    document.head.append(style);
  }

  function apply(collapsed){
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return;
    dashboard.classList.toggle("operly-nav-collapsed",collapsed);
    const button=$("#operly-side-toggle");
    if(button){
      const glyph=collapsed?"›":"‹";
      // This function is called from a child-list MutationObserver. Replacing the
      // same text node on every pass creates another child-list mutation and can
      // lock the renderer in a self-triggering loop. Only mutate when state changes.
      if(button.textContent!==glyph)button.textContent=glyph;
      button.setAttribute("aria-expanded",String(!collapsed));
      button.setAttribute("aria-label",collapsed?"Expand navigation":"Collapse navigation");
      button.title=collapsed?"Expand navigation (Ctrl+B)":"Collapse navigation (Ctrl+B)";
    }
  }

  function read(){
    try{return localStorage.getItem(KEY)==="1"}catch{return false}
  }
  function write(value){try{localStorage.setItem(KEY,value?"1":"0")}catch{}}
  function toggle(){const dashboard=$("#dashboard.workspace-shell-ready");if(!dashboard)return;const next=!dashboard.classList.contains("operly-nav-collapsed");write(next);apply(next)}

  function ensureAuthenticatedScreenBoundary(){
    const dashboard=$("#dashboard");
    if(!dashboard)return false;
    const screens=document.querySelectorAll(".screen");
    if(dashboard.classList.contains("hidden")){
      // Signed-out routing is class-driven. Release any inline visibility locks
      // from the prior authenticated session so login/signup/onboarding can show.
      screens.forEach(screen=>{
        screen.style.removeProperty("display");
        screen.removeAttribute("aria-hidden");
      });
      return false;
    }
    screens.forEach(screen=>{
      const active=screen===dashboard;
      screen.classList.toggle("hidden",!active);
      if(active){
        screen.style.removeProperty("display");
        screen.removeAttribute("aria-hidden");
      }else{
        // Several legacy/cosmetic stylesheets can style auth screens with higher
        // specificity than the generic .hidden rule. Once the authenticated shell
        // is visible, make the screen boundary explicit so no signed-out surface can
        // remain painted over a valid session.
        screen.style.setProperty("display","none","important");
        screen.setAttribute("aria-hidden","true");
      }
    });
    return true;
  }

  async function reconcileAuthenticatedRoute(){
    if(location.pathname!=="/app")return false;
    try{
      const response=await nativeFetch("/api/me",{
        method:"GET",
        credentials:"same-origin",
        cache:"no-store",
        headers:{Accept:"application/json"}
      });
      if(!response.ok)return false;
      const dashboard=$("#dashboard");
      if(!dashboard)return false;
      dashboard.classList.remove("hidden");
      ensureAuthenticatedScreenBoundary();
      document.documentElement.dataset.operlyAuth="authenticated";
      return true;
    }catch{
      return false;
    }
  }

  function ensureSideToggle(){
    const head=$(".operly-workspace-head");
    if(!head)return false;
    if(!$("#operly-side-toggle",head)){
      const button=document.createElement("button");
      button.type="button";button.id="operly-side-toggle";button.className="operly-side-toggle";
      button.addEventListener("click",toggle);
      head.append(button);
    }
    apply(read());
    return true;
  }

  function setMobileNav(open){
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return;
    dashboard.classList.toggle("operly-mobile-nav-open",!!open);
    const button=$("#mobile-nav-toggle");
    if(button){
      button.setAttribute("aria-expanded",String(!!open));
      button.setAttribute("aria-label",open?"Close navigation":"Open navigation");
    }
  }

  function ensureMobileNavigation(){
    const dashboard=$("#dashboard.workspace-shell-ready");
    const button=$("#mobile-nav-toggle");
    const backdrop=$("#mobile-nav-backdrop");
    const nav=$(".operly-section-nav");
    if(!dashboard||!button||!nav)return false;
    button.setAttribute("aria-controls","operly-section-navigation");
    nav.id="operly-section-navigation";
    if(!button.dataset.operlyShellBound){
      button.dataset.operlyShellBound="1";
      button.addEventListener("click",event=>{
        event.preventDefault();
        event.stopPropagation();
        setMobileNav(!dashboard.classList.contains("operly-mobile-nav-open"));
      });
      backdrop?.addEventListener("click",()=>setMobileNav(false));
      nav.addEventListener("click",event=>{
        if(event.target.closest?.("[data-shell-page]"))setMobileNav(false);
      });
    }
    return true;
  }

  function ensureWorkspaceSettings(){
    const rail=$("#operly-workspace-rail");
    if(!rail)return false;
    if(!$("#operly-rail-settings",rail)){
      const button=document.createElement("button");
      button.type="button";
      button.id="operly-rail-settings";
      button.className="operly-rail-settings";
      button.textContent="⚙";
      button.title="Workspace settings";
      button.setAttribute("aria-label","Workspace settings");
      button.addEventListener("click",()=>{
        const settings=$(".operly-nav-item[data-shell-page='workspace']");
        if(settings)settings.click();
      });
      const spacer=$(".operly-rail-spacer",rail);
      rail.insertBefore(button,spacer||null);
    }
    return true;
  }

  async function createAdditionalWebsite(button){
    const prior=button.textContent;
    const name=window.prompt("Website name");
    if(!name?.trim())return;
    const description=window.prompt("What should this website be for?","")||"";
    button.disabled=true;button.textContent="Creating…";
    try{
      const project=await api("/studio/projects",{method:"POST",body:JSON.stringify({name:name.trim(),description:description.trim()})});
      const solutions=await api("/solutions");
      const solution=solutions.find(item=>item.runtime?.kind==="studio"&&item.runtime?.id===project.id);
      if(!solution)throw new Error("The website was created but could not be opened in Solutions yet.");
      if(window.operlyUnifiedSolutionStudio?.open){
        await window.operlyUnifiedSolutionStudio.open(solution,{generate:true});
      }else if(window.operlyStudio){
        await window.operlyStudio();
      }
    }catch(error){
      alert(error?.message||String(error));
      button.disabled=false;button.textContent=prior;
    }
  }

  function ensureSolutionCreationAction(){
    const list=$(".ss-list");
    const open=$("#ss-open-website",list);
    if(!list||!open||$("#op-new-website",list))return false;
    const action=document.createElement("div");
    action.className="op-solution-actions";
    open.parentNode.insertBefore(action,open);
    action.append(open);
    const button=document.createElement("button");
    button.type="button";
    button.id="op-new-website";
    button.className="op-new-website";
    button.textContent="+ New website";
    button.addEventListener("click",()=>createAdditionalWebsite(button));
    action.append(button);
    return true;
  }

  function mount(){
    ensureAuthenticatedScreenBoundary();
    ensureCanonicalStyleOrder();
    ensureRepairStyles();
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return false;
    ensureSideToggle();
    ensureMobileNavigation();
    ensureWorkspaceSettings();
    ensureSolutionCreationAction();
    return true;
  }

  function editDescription(instruction){
    const context=$("#ss-command-context")?.textContent?.trim()||"current Solution";
    const text=String(instruction||"").replace(/\s+/g," ").trim();
    const clipped=text.length>72?text.slice(0,69)+"…":text;
    return clipped?`${clipped} · ${context}`:context;
  }

  function startEditStatus(instruction){
    if(editTicker)clearInterval(editTicker);
    const started=Date.now();
    const description=editDescription(instruction);
    const update=()=>{
      const send=$("#ss-send");
      if(!send||!send.disabled){clearInterval(editTicker);editTicker=null;return;}
      const state=$("#ss-preview-state span");
      if(!state)return;
      const seconds=Math.max(0,Math.round((Date.now()-started)/1000));
      state.textContent=`Editing: ${description} · ${seconds}s`;
      state.title=`Operly is editing ${description}`;
    };
    setTimeout(update,0);
    editTicker=setInterval(update,1000);
  }

  function captureStudioSend(event){
    const target=event.target;
    if(!target)return;
    if(event.type==="click"&&target.closest?.("#ss-send")){
      const instruction=$("#ss-input")?.value||"";
      startEditStatus(instruction);
      return;
    }
    if(event.type==="keydown"&&target.id==="ss-input"&&event.key==="Enter"&&!event.shiftKey){
      startEditStatus(target.value||"");
    }
  }

  document.addEventListener("keydown",event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="b"&&!event.altKey){
      const target=event.target;
      if(target&&["INPUT","TEXTAREA","SELECT"].includes(target.tagName))return;
      event.preventDefault();toggle();
    }
    if(event.key==="Escape")setMobileNav(false);
  });
  document.addEventListener("click",captureStudioSend,true);
  document.addEventListener("keydown",captureStudioSend,true);
  window.addEventListener("resize",()=>{if(innerWidth>760)setMobileNav(false)});

  mount();
  reconcileAuthenticatedRoute();
  window.addEventListener("pageshow",()=>reconcileAuthenticatedRoute());

  // Watch only the authenticated application subtree. The previous document-wide
  // observer made unrelated public/auth DOM work invoke product-shell repairs.
  const dashboardRoot=$("#dashboard");
  if(dashboardRoot){
    const observer=new MutationObserver(()=>mount());
    observer.observe(dashboardRoot,{childList:true,subtree:true});

    // Authentication reveals #dashboard by removing .hidden. Watch only that class
    // transition instead of every class change in the application.
    const authObserver=new MutationObserver(()=>ensureAuthenticatedScreenBoundary());
    authObserver.observe(dashboardRoot,{attributes:true,attributeFilter:["class"]});
  }
})();
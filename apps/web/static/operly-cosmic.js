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

  function mount(){
    ensureAuthenticatedScreenBoundary();
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return false;
    ensureSideToggle();
    ensureWorkspaceSettings();
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
  });
  document.addEventListener("click",captureStudioSend,true);
  document.addEventListener("keydown",captureStudioSend,true);

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
(() => {
  const KEY="operly.workspace.navigation.collapsed";
  const $=(q,r=document)=>r.querySelector(q);
  let editTicker=null;

  function apply(collapsed){
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return;
    dashboard.classList.toggle("operly-nav-collapsed",collapsed);
    const button=$("#operly-side-toggle");
    if(button){
      button.textContent=collapsed?"›":"‹";
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
  // The workspace shell and rail are intentionally re-renderable. Keep this
  // observer alive so collapse/settings controls are restored after workspace or
  // navigation refreshes instead of disappearing after the first successful mount.
  const observer=new MutationObserver(()=>mount());
  observer.observe(document.documentElement,{childList:true,subtree:true});
})();

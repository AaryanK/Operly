(() => {
  const KEY="operly.workspace.navigation.collapsed";
  const $=(q,r=document)=>r.querySelector(q);

  function apply(collapsed){
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!dashboard)return;
    dashboard.classList.toggle("operly-nav-collapsed",collapsed);
    const button=$("#operly-side-toggle");
    if(button){
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

  function mount(){
    const head=$(".operly-workspace-head");
    const dashboard=$("#dashboard.workspace-shell-ready");
    if(!head||!dashboard)return false;
    if(!$("#operly-side-toggle",head)){
      const button=document.createElement("button");
      button.type="button";button.id="operly-side-toggle";button.className="operly-side-toggle";button.textContent="‹";
      button.addEventListener("click",toggle);
      head.append(button);
    }
    apply(read());
    return true;
  }

  document.addEventListener("keydown",event=>{
    if((event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==="b"&&!event.altKey){
      const target=event.target;
      if(target&&["INPUT","TEXTAREA","SELECT"].includes(target.tagName))return;
      event.preventDefault();toggle();
    }
  });

  if(!mount()){
    const observer=new MutationObserver(()=>{if(mount())observer.disconnect()});
    observer.observe(document.documentElement,{childList:true,subtree:true});
  }
})();

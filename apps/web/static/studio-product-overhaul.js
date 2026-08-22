(() => {
  const LEFT_KEY="operly.studio.project.collapsed",RIGHT_KEY="operly.studio.inspector.collapsed";
  const $=(q,r=document)=>r.querySelector(q),$$=(q,r=document)=>[...r.querySelectorAll(q)];
  const read=k=>{try{return localStorage.getItem(k)==="1"}catch{return false}};
  const write=(k,v)=>{try{localStorage.setItem(k,v?"1":"0")}catch{}};
  let activeShell=null,lastSelectionKey="";

  function state(){return window.operlyUnifiedSolutionStudio?.state||null}
  function prompt(text){const input=$("#ss-input");if(!input)return;input.value=text;input.dispatchEvent(new Event("input",{bubbles:true}));input.focus();input.setSelectionRange(input.value.length,input.value.length)}
  function selectionLabel(){const s=state()?.selected;if(!s)return "Whole page";return s.componentType||s.tag||s.componentId||s.selector||"Selected element"}
  function selectionKey(){const s=state()?.selected;return s?JSON.stringify([s.componentId,s.selector,s.text?.slice(0,80)]):"page"}

  function actions(){
    const s=state()?.selected;
    if(!s)return [
      ["Redesign page","Redesign this page with stronger visual hierarchy, better spacing, more premium composition, and clearer conversion flow."],
      ["Add social proof","Add a tasteful social-proof section in the most natural place on this page."],
      ["Improve mobile","Improve the responsive/mobile layout of this page while preserving its content and brand direction."],
      ["Make it premium","Make this page feel much more premium and intentional without adding unnecessary clutter."]
    ];
    const type=String(s.componentType||s.tag||"").toLowerCase();
    if(type.includes("hero")||s.tag==="h1")return [
      ["Premium hero","Make this hero feel premium, distinctive and conversion-focused while keeping the core message."],
      ["Rewrite copy","Rewrite the selected hero copy to be concise, specific and compelling."],
      ["Improve spacing","Improve the selected hero's spacing, typography scale and responsive balance."],
      ["Strengthen CTA","Strengthen the call-to-action in the selected hero and make its hierarchy obvious."]
    ];
    if(s.tag==="a"||s.tag==="button")return [
      ["Improve CTA","Make the selected call-to-action clearer, more prominent and better aligned with the page hierarchy."],
      ["Rewrite label","Rewrite the selected CTA label to be concise and action-oriented."],
      ["Refine style","Refine the selected control's size, spacing and visual emphasis."],
      ["Check mobile","Make sure the selected control works beautifully on mobile and touch screens."]
    ];
    return [
      ["Improve layout","Improve the selected element's layout, spacing and visual hierarchy."],
      ["Rewrite copy","Rewrite the selected element's copy to be clearer and more persuasive."],
      ["Make premium","Make the selected element feel more premium while staying consistent with the rest of the page."],
      ["Optimize mobile","Optimize the selected element for mobile without hurting desktop layout."]
    ];
  }

  function renderCommandMeta(){
    const chat=$(".ss-chat");if(!chat)return;
    let meta=$(".ss-command-meta",chat);
    if(!meta){meta=document.createElement("div");meta.className="ss-command-meta";chat.insertBefore(meta,$(".ss-compose",chat))}
    const rows=actions().slice(0,3);
    meta.innerHTML=`<span>Editing <strong>${selectionLabel().replace(/[<>]/g,"")}</strong></span><div class="ss-command-chips">${rows.map(([label],i)=>`<button class="ss-command-chip" data-command-chip="${i}">${label}</button>`).join("")}</div>`;
    $$('[data-command-chip]',meta).forEach((b,i)=>b.onclick=()=>prompt(rows[i][1]));
  }

  function renderInspectorTools(){
    const host=$("#ss-inspector");if(!host)return;
    let tools=$(".ss-inspector-tools",host);
    if(!tools){tools=document.createElement("section");tools.className="ss-inspector-tools";host.append(tools)}
    const rows=actions();
    tools.innerHTML=`<small>QUICK CHANGES</small><div class="tool-grid">${rows.map(([label],i)=>`<button data-inspector-action="${i}">${label}</button>`).join("")}</div>`;
    $$('[data-inspector-action]',tools).forEach((b,i)=>b.onclick=()=>prompt(rows[i][1]));
  }

  function applyPaneState(shell){
    shell.classList.toggle("studio-left-collapsed",read(LEFT_KEY));
    shell.classList.toggle("studio-right-collapsed",read(RIGHT_KEY));
    const left=$("#ss-toggle-project",shell),right=$("#ss-toggle-inspector",shell);
    if(left){left.textContent=read(LEFT_KEY)?"Show project":"Project";left.setAttribute("aria-pressed",String(read(LEFT_KEY)))}
    if(right){right.textContent=read(RIGHT_KEY)?"Show inspector":"Inspector";right.setAttribute("aria-pressed",String(read(RIGHT_KEY)))}
  }

  function enhance(shell){
    if(shell.dataset.productOverhaul==="1")return;
    shell.dataset.productOverhaul="1";activeShell=shell;
    const actionsHost=$(".ss-actions",shell);
    if(actionsHost){
      const left=document.createElement("button");left.id="ss-toggle-project";left.className="ss-pane-toggle";left.type="button";
      const right=document.createElement("button");right.id="ss-toggle-inspector";right.className="ss-pane-toggle";right.type="button";
      actionsHost.prepend(right);actionsHost.prepend(left);
      left.onclick=()=>{const next=!read(LEFT_KEY);write(LEFT_KEY,next);applyPaneState(shell)};
      right.onclick=()=>{const next=!read(RIGHT_KEY);write(RIGHT_KEY,next);applyPaneState(shell)};
    }
    applyPaneState(shell);
    const input=$("#ss-input",shell);
    if(input){
      const resize=()=>{input.style.height="auto";input.style.height=Math.min(96,Math.max(32,input.scrollHeight))+"px"};
      input.addEventListener("input",resize);resize();
      input.addEventListener("keydown",event=>{if((event.ctrlKey||event.metaKey)&&event.key==="Enter"){event.preventDefault();$("#ss-send",shell)?.click()}});
    }
    renderCommandMeta();renderInspectorTools();lastSelectionKey=selectionKey();
  }

  const observer=new MutationObserver(()=>{const shell=$(".ss-shell");if(shell)enhance(shell)});
  observer.observe(document.documentElement,{childList:true,subtree:true});
  const initial=$(".ss-shell");if(initial)enhance(initial);

  setInterval(()=>{
    const shell=$(".ss-shell");if(!shell)return;
    if(shell!==activeShell)enhance(shell);
    const key=selectionKey();
    if(key!==lastSelectionKey){lastSelectionKey=key;renderCommandMeta();renderInspectorTools()}
    else if(!$(".ss-inspector-tools",$("#ss-inspector")||document))renderInspectorTools();
  },350);
})();

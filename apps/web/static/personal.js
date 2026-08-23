(() => {
  const $p = (q, r=document) => r.querySelector(q);
  const escp = (v="") => String(v ?? "").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
  const state={conversationId:null,busy:false};

  function add(role,text,meta=""){
    const host=$p("#personal-messages");if(!host)return;
    host.insertAdjacentHTML("beforeend",`<article class="personal-message ${role}"><strong>${role==="user"?"You":"✦ Operly"}</strong><p>${escp(text)}</p>${meta?`<small>${escp(meta)}</small>`:""}</article>`);
    host.scrollTop=host.scrollHeight;
  }

  async function loadWorkspaces(){
    const select=$p("#personal-workspace-select"),list=$p("#personal-workspace-list");
    if(!select||!list)return;
    const rows=await api("/auth/workspaces");
    select.innerHTML='<option value="">Personal scope only</option>'+rows.map(row=>`<option value="${escp(row.id)}">${escp(row.name)} · ${escp(row.role)}</option>`).join("");
    list.innerHTML=rows.length?rows.map(row=>`<button data-personal-workspace="${escp(row.id)}"><b>${escp(row.name)}</b><small>${escp(row.role)}</small></button>`).join(""):'<p>No workspaces yet. Your account and Personal AI work without one.</p>';
    list.querySelectorAll("[data-personal-workspace]").forEach(button=>button.addEventListener("click",async()=>{
      button.disabled=true;
      try{
        await api("/auth/switch-workspace",{method:"POST",body:JSON.stringify({tenant_id:button.dataset.personalWorkspace})});
        location.assign("/app");
      }catch(error){add("assistant",error.message,"Workspace switch failed");button.disabled=false;}
    }));
  }

  async function send(){
    const input=$p("#personal-input"),button=$p("#personal-send");
    const text=input?.value.trim();if(!text||state.busy)return;
    state.busy=true;if(button)button.disabled=true;input.value="";add("user",text);
    try{
      const result=await api("/personal-agent/chat",{method:"POST",body:JSON.stringify({message:text,conversation_id:state.conversationId,selected_workspace_id:$p("#personal-workspace-select")?.value||null})});
      state.conversationId=result.conversation_id||state.conversationId;
      add("assistant",result.message,result.selected_workspace_id?"Private answer using your authorized workspace context":"Private account scope");
    }catch(error){add("assistant",error.message,"No action was taken");}
    finally{state.busy=false;if(button)button.disabled=false;input?.focus();}
  }

  async function createWorkspace(){
    const input=$p("#personal-workspace-name"),button=$p("#personal-create-workspace"),name=input?.value.trim();
    if(!name)return;
    button.disabled=true;
    try{
      await api("/auth/workspaces",{method:"POST",body:JSON.stringify({name})});
      location.assign("/app");
    }catch(error){add("assistant",error.message,"Workspace was not created");button.disabled=false;}
  }

  async function mount(){
    state.conversationId=null;
    const host=$p("#personal-messages");if(host)host.innerHTML="";
    add("assistant","This is your private Personal AI. You can use Operly without a workspace, or privately ask about workspaces you are authorized to access.");
    await loadWorkspaces().catch(error=>add("assistant",error.message,"Workspace list unavailable"));
    $p("#personal-send")?.addEventListener("click",send,{once:false});
    $p("#personal-input")?.addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();send();}},{once:false});
    $p("#personal-create-workspace")?.addEventListener("click",createWorkspace,{once:false});
    $p("#personal-logout")?.addEventListener("click",async()=>{try{await api("/auth/logout",{method:"POST",body:"{}"});location.assign("/login");}catch(error){add("assistant",error.message,"Sign out failed");}},{once:false});
  }

  window.operlyPersonal={mount,loadWorkspaces,send};
})();

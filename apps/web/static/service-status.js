(() => {
  const config=JSON.parse(document.querySelector('#service-config').textContent);const card=document.querySelector('#status-card');
  const labels={submitted:'Request received',assigned:'Responder assigned',en_route:'Responder en route',completed:'Rescue completed'};
  async function load(){try{const response=await fetch(`/api/public/service-projects/${encodeURIComponent(config.slug)}/requests/${encodeURIComponent(config.reference)}/status?token=${encodeURIComponent(config.token)}`);if(!response.ok)throw new Error('This status link is no longer available.');const data=await response.json();card.innerHTML=`<span class="status-pulse"></span><p class="eyebrow">Current state</p><h2>${labels[data.status]||data.status}</h2>${data.assignedTo?`<p>${data.assignedTo} is handling this request.</p>`:'<p>Dispatch is reviewing your request now.</p>'}<div class="status-track"><i class="on"></i><i class="${data.status!=='submitted'?'on':''}"></i><i class="${['en_route','completed'].includes(data.status)?'on':''}"></i><i class="${data.status==='completed'?'on':''}"></i></div>`}catch(error){card.innerHTML=`<p>${error.message}</p>`}}
  load();setInterval(load,15000);
})();

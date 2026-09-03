// Retired disposable smoke app; Railway start command now exits immediately.
import http from "node:http";
import crypto from "node:crypto";

const port = Math.max(1, Math.min(Number(process.env.PORT || 3000), 65535));
const startedAt = new Date().toISOString();
const items = [
  { id: crypto.randomUUID(), text: "Deployed by Operly Worker", created_at: startedAt },
];

function json(res, status, value) {
  const body = Buffer.from(JSON.stringify(value));
  res.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "content-length": String(body.length),
    "cache-control": "no-store",
  });
  res.end(body);
}

function html(res) {
  const body = Buffer.from(`<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Operly Worker Full-stack Smoke</title>
<style>body{font-family:system-ui,sans-serif;max-width:760px;margin:56px auto;padding:0 20px;background:#0b1020;color:#e8eefc}main{background:#151d33;border:1px solid #2a3658;border-radius:18px;padding:28px}h1{margin-top:0}input,button{font:inherit;padding:10px 12px;border-radius:10px;border:1px solid #43527a}input{width:65%;background:#0f1629;color:#fff}button{background:#e8eefc;color:#101528;cursor:pointer}li{margin:9px 0}.ok{color:#7ee787}</style></head>
<body><main><div class="ok">● live</div><h1>Operly Worker full-stack deployment</h1><p>This frontend and its JSON API are running from the service created by the Operly deployment worker smoke.</p><form id="f"><input id="v" placeholder="Add an item" required><button>Add</button></form><ul id="items"></ul><pre id="meta"></pre></main>
<script>
async function load(){const r=await fetch('/api/items');const d=await r.json();document.querySelector('#items').innerHTML=d.items.map(x=>'<li>'+x.text.replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]))+'</li>').join('');const i=await (await fetch('/api/info')).json();document.querySelector('#meta').textContent=JSON.stringify(i,null,2)}
document.querySelector('#f').onsubmit=async e=>{e.preventDefault();const v=document.querySelector('#v');await fetch('/api/items',{method:'POST',headers:{'content-type':'application/json'},body:JSON.stringify({text:v.value})});v.value='';await load()};load();
</script></body></html>`);
  res.writeHead(200, { "content-type": "text/html; charset=utf-8", "content-length": String(body.length) });
  res.end(body);
}

async function body(req) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > 64 * 1024) throw new Error("request too large");
    chunks.push(chunk);
  }
  return chunks.length ? JSON.parse(Buffer.concat(chunks).toString("utf8")) : {};
}

const server = http.createServer(async (req, res) => {
  const url = new URL(req.url || "/", "http://localhost");
  try {
    if (req.method === "GET" && url.pathname === "/health") return json(res, 200, { ok: true, service: "operly-worker-fullstack-smoke" });
    if (req.method === "GET" && url.pathname === "/api/info") return json(res, 200, { ok: true, stack: ["frontend", "node-api"], deployed_by: "operly-worker", started_at: startedAt });
    if (req.method === "GET" && url.pathname === "/api/items") return json(res, 200, { items });
    if (req.method === "POST" && url.pathname === "/api/items") {
      const data = await body(req);
      const text = String(data.text || "").trim().slice(0, 200);
      if (!text) return json(res, 422, { detail: "text is required" });
      const item = { id: crypto.randomUUID(), text, created_at: new Date().toISOString() };
      items.push(item);
      return json(res, 201, item);
    }
    if (req.method === "GET" && url.pathname === "/") return html(res);
    return json(res, 404, { detail: "not found" });
  } catch (error) {
    return json(res, 400, { detail: String(error?.message || error) });
  }
});

server.listen(port, "0.0.0.0", () => console.log(`Operly full-stack smoke listening on :${port}`));

from __future__ import annotations

RUNTIME_PY = r'''from __future__ import annotations
import json, subprocess, sys, time
from pathlib import Path

HELPER="/opt/operly-computer-tool.py"
HELPER_PY="/opt/operly-py/bin/python"
WORK=Path("/workspace/work")
REQ=WORK/".vibe-requests"
REQ.mkdir(parents=True,exist_ok=True)
trace=[]

def slim(result):
    keep={}
    for key in ("path","size_bytes","sha256","exit_code","timed_out","truncated","background","network_policy","profile"):
        if key in result: keep[key]=result[key]
    if "items" in result: keep["items"]=result["items"][:12]
    if "stdout" in result: keep["stdout"]=str(result["stdout"])[:1200]
    if result.get("stderr"): keep["stderr"]=str(result["stderr"])[:500]
    return keep

def tool(name,args,summary):
    request=REQ/f"{len(trace)+1:02d}-{name.replace('.', '-')}.json"
    request.write_text(json.dumps(args),encoding="utf-8")
    proc=subprocess.run([HELPER_PY,HELPER,name,str(request)],capture_output=True,text=True,timeout=180)
    raw=(proc.stdout or "").strip()
    try: packet=json.loads(raw or "{}")
    except Exception: raise RuntimeError(f"{name} returned invalid Computer tool output: {raw[:500]}")
    if proc.returncode!=0 or not packet.get("ok"):
        raise RuntimeError(f"{name} failed: {packet.get('error') or proc.stderr or raw}")
    result=packet.get("result") or {}
    trace.append({"tool":name,"summary":summary,"status":"ok","result":slim(result)})
    return result

packet=json.load(sys.stdin)
state=(packet.get("arguments") or {}).get("state") or {}
brief=state.get("brief") if isinstance(state,dict) else {}
brief=brief if isinstance(brief,dict) else {}
site_name=str(brief.get("site_name") or "Untitled Studio")[:120]
business=str(brief.get("business") or "Modern business")[:240]
tagline=str(brief.get("tagline") or "Build something worth remembering.")[:300]
audience=str(brief.get("audience") or "modern teams")[:240]
direction=str(brief.get("visual_direction") or "dark, premium, minimal")[:400]
sections=[x.strip().lower() for x in str(brief.get("sections") or "hero, proof, services, contact").split(",") if x.strip()][:12]
cta=str(brief.get("cta") or "Get started")[:80]
accent=str(brief.get("accent") or "#8b5cf6")[:32]
refinement=str(brief.get("refinement") or "")[:800]

tool("environment.info",{},"Inspect the isolated coding computer environment")
tool("files.mkdir",{"path":"vibe-site/assets"},"Create the generated site project directories")
spec={"site_name":site_name,"business":business,"tagline":tagline,"audience":audience,"visual_direction":direction,"sections":sections,"cta":cta,"accent":accent,"refinement":refinement}
tool("files.write",{"path":"vibe-site/spec.json","content":json.dumps(spec,indent=2)},"Write the persisted build brief into the project")
base_html="""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{SITE_NAME}}</title><meta name="description" content="{{TAGLINE}}"><link rel="stylesheet" href="assets/styles.css"></head><body><header class="nav"><a class="logo" href="#">{{SITE_NAME}}</a><nav><a href="#services">Services</a><a href="#process">Process</a><a href="#contact">Contact</a></nav></header><main><!-- GENERATED_SECTIONS --></main><script src="assets/app.js"></script></body></html>"""
css="""*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:#080a0f;color:#f8fafc;font-family:Inter,ui-sans-serif,system-ui,-apple-system,sans-serif;line-height:1.5}.nav{height:76px;display:flex;align-items:center;justify-content:space-between;padding:0 6vw;border-bottom:1px solid #ffffff12;position:sticky;top:0;backdrop-filter:blur(18px);background:#080a0fdd;z-index:5}.logo{font-weight:800;color:white;text-decoration:none}.nav nav{display:flex;gap:24px}.nav nav a{color:#aab1bf;text-decoration:none}.hero{min-height:78vh;display:grid;place-items:center;padding:90px 6vw;text-align:center;background:radial-gradient(circle at 50% 0%,ACCENT33,transparent 38%)}.hero .wrap{max-width:950px}.eyebrow{color:ACCENT;font-weight:700;font-size:12px;letter-spacing:.14em;text-transform:uppercase}.hero h1{font-size:clamp(48px,8vw,104px);line-height:.96;letter-spacing:-.06em;margin:18px 0}.hero p{max-width:720px;margin:0 auto;color:#a9b0bd;font-size:clamp(18px,2vw,23px)}.cta{display:inline-block;margin-top:30px;padding:13px 20px;border-radius:999px;background:ACCENT;color:white;text-decoration:none;font-weight:700}.section{padding:100px 6vw;border-top:1px solid #ffffff0d}.inner{max-width:1180px;margin:auto}.section h2{font-size:clamp(34px,5vw,58px);letter-spacing:-.045em;margin:0 0 18px}.section p{color:#929baa;max-width:700px}.cards{display:grid;grid-template-columns:repeat(3,1fr);gap:14px;margin-top:34px}.card{padding:24px;border:1px solid #ffffff14;border-radius:18px;background:linear-gradient(180deg,#121722,#0d1118)}.card b{display:block;font-size:18px;margin-bottom:8px}.card span{color:#929baa}.price{font-size:48px;font-weight:800}.faq{display:grid;gap:10px}.faq details{padding:18px;border:1px solid #ffffff12;border-radius:12px}.contact{text-align:center;background:linear-gradient(180deg,transparent,ACCENT18)}footer{padding:30px 6vw;color:#697383;border-top:1px solid #ffffff10}@media(max-width:760px){.nav nav{display:none}.cards{grid-template-columns:1fr}.hero{min-height:68vh}.section{padding:72px 5vw}}""".replace("ACCENT",accent)
js="""document.querySelectorAll('a[href^="#"]').forEach(a=>a.addEventListener('click',e=>{const id=a.getAttribute('href');if(id&&id.length>1){const el=document.querySelector(id);if(el){e.preventDefault();el.scrollIntoView({behavior:'smooth'})}}}));const observer=new IntersectionObserver(es=>es.forEach(e=>{if(e.isIntersecting)e.target.animate([{opacity:0,transform:'translateY(18px)'},{opacity:1,transform:'translateY(0)'}],{duration:550,fill:'both'})}),{threshold:.12});document.querySelectorAll('.section .inner,.card').forEach(x=>observer.observe(x));"""
tool("files.write",{"path":"vibe-site/index.html","content":base_html},"Write the starter HTML file")
tool("files.write",{"path":"vibe-site/assets/styles.css","content":css},"Write the starter CSS file")
tool("files.write",{"path":"vibe-site/assets/app.js","content":js},"Write the starter JavaScript file")
py_code="""
import html,json
from pathlib import Path
root=Path('vibe-site'); spec=json.loads((root/'spec.json').read_text())
site=html.escape(spec['site_name']); biz=html.escape(spec['business']); tagline=html.escape(spec['tagline']); audience=html.escape(spec['audience']); cta=html.escape(spec['cta']); direction=html.escape(spec['visual_direction']); refine=html.escape(spec.get('refinement') or '')
sections=[]
def sec(kind,title,body,extra=''): return f'<section class="section" id="{kind}"><div class="inner"><span class="eyebrow">{kind}</span><h2>{title}</h2><p>{body}</p>{extra}</div></section>'
for kind in spec['sections']:
    if kind=='hero': sections.append(f'<section class="hero"><div class="wrap"><span class="eyebrow">{biz}</span><h1>{tagline}</h1><p>Built for {audience}. {direction.capitalize()}.</p><a class="cta" href="#contact">{cta}</a></div></section>')
    elif kind in {'proof','testimonials'}: sections.append(sec(kind,'Proof, not promises','Small teams can move with enterprise-grade execution.','<div class="cards"><div class="card"><b>4.9/5</b><span>Client satisfaction</span></div><div class="card"><b>3.2×</b><span>Faster iteration</span></div><div class="card"><b>24h</b><span>First prototype</span></div></div>'))
    elif kind=='services': sections.append(sec(kind,'From idea to working system','One integrated loop across product, design, software and AI.','<div class="cards"><div class="card"><b>Strategy</b><span>Clarify direction.</span></div><div class="card"><b>Design + engineering</b><span>Prototype and ship.</span></div><div class="card"><b>AI systems</b><span>Automate real work.</span></div></div>'))
    elif kind=='process': sections.append(sec(kind,'A fast loop with high standards','Less ceremony, tighter feedback, better artifacts.','<div class="cards"><div class="card"><b>01 · Understand</b><span>Map the problem.</span></div><div class="card"><b>02 · Build</b><span>Create the system.</span></div><div class="card"><b>03 · Compound</b><span>Expand what works.</span></div></div>'))
    elif kind=='pricing': sections.append(sec(kind,'Simple starting point','Scope expands with evidence.','<div class="card" style="margin-top:26px"><span>Focused engagement</span><div class="price">$4.8k</div><span>Start small, expand when earned.</span></div>'))
    elif kind=='faq': sections.append(sec(kind,'Questions, answered','A few things teams usually want to know.','<div class="faq"><details><summary>How fast can we start?</summary><p>Usually within a few days.</p></details><details><summary>Can you work with an existing team?</summary><p>Yes.</p></details></div>'))
    elif kind=='contact': sections.append(f'<section class="section contact" id="contact"><div class="inner"><span class="eyebrow">contact</span><h2>Ready to make this real?</h2><p>{refine or "Tell us what you want to build."}</p><a class="cta" href="mailto:hello@example.com">{cta}</a></div></section>')
    else: sections.append(sec(kind,kind.replace('-',' ').title(),f'A focused {kind.replace("-"," ")} section for {site}.'))
text=(root/'index.html').read_text().replace('{{SITE_NAME}}',site).replace('{{TAGLINE}}',tagline).replace('<!-- GENERATED_SECTIONS -->',''.join(sections))
text += f'<footer>© {site} · Generated inside an Operly Sandbox computer.</footer>'
(root/'index.html').write_text(text)
report={'section_count':len(sections),'html_bytes':len(text.encode()),'style_bytes':(root/'assets/styles.css').stat().st_size,'script_bytes':(root/'assets/app.js').stat().st_size,'site_name':spec['site_name']}
(root/'build-report.json').write_text(json.dumps(report,indent=2)); print(json.dumps(report))
"""
tool("python.exec",{"code":py_code,"cwd":".","timeout_seconds":90},"Use Python to transform the starter project and modify index.html")
tool("terminal.exec",{"command":"node --check assets/app.js && wc -c index.html assets/styles.css assets/app.js build-report.json && sha256sum index.html assets/styles.css assets/app.js","cwd":"vibe-site","timeout_seconds":90,"background":False},"Validate JavaScript and inspect generated files from the command line")
html_read=tool("files.read",{"path":"vibe-site/index.html","max_bytes":400000},"Read the generated HTML back from the computer")
css_read=tool("files.read",{"path":"vibe-site/assets/styles.css","max_bytes":400000},"Read the generated CSS back from the computer")
js_read=tool("files.read",{"path":"vibe-site/assets/app.js","max_bytes":400000},"Read the generated JavaScript back from the computer")
report_read=tool("files.read",{"path":"vibe-site/build-report.json","max_bytes":100000},"Read Python build metadata")
tool("files.list",{"path":"vibe-site","recursive":True,"max_entries":100},"List the complete generated project tree")
tool("terminal.exec",{"command":"zip -qr site-bundle.zip index.html assets build-report.json spec.json && unzip -l site-bundle.zip","cwd":"vibe-site","timeout_seconds":90,"background":False},"Package the generated site into a ZIP from the command line")
bundle=tool("artifact.export",{"path":"vibe-site/site-bundle.zip","max_bytes":10000000},"Export the generated site bundle as a Sandbox artifact")
index_html=html_read.get("content") or ""; styles=css_read.get("content") or ""; script=js_read.get("content") or ""
preview=index_html.replace('<link rel="stylesheet" href="assets/styles.css">',f'<style>{styles}</style>').replace('<script src="assets/app.js"></script>',f'<script>{script}</script>')
try: report=json.loads(report_read.get("content") or "{}")
except Exception: report={}
result={"summary":f"Generated and packaged {site_name} using Operly Computer tools.","files":{"index.html":index_html,"styles.css":styles,"app.js":script,"build-report.json":json.dumps(report,indent=2)},"preview_html":preview,"trace":trace,"report":report,"bundle_base64":bundle.get("content_base64") or "","bundle_sha256":bundle.get("sha256") or "","built_at":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime())}
print(json.dumps({"result":result},separators=(",",":")))
'''


__all__ = ["RUNTIME_PY"]

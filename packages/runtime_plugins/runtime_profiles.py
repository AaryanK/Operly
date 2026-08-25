"""Finite execution mechanics, independent of application domain.

Profiles are deterministic control-plane policy. Models may choose source shapes,
but they never author the commands or isolation settings used to execute them.
"""
from copy import deepcopy


_NODE_STATIC_ANALYSIS = """const fs=require('fs'),p=require('path'),cp=require('child_process');const files=[];function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){if(e.name==='node_modules'||e.name.startsWith('.'))continue;const f=p.join(d,e.name);if(e.isDirectory())walk(f);else if(/\\.(m?js|cjs)$/.test(e.name))files.push(f)}}walk('.');for(const f of files){const r=cp.spawnSync(process.execPath,['--check',f],{stdio:'inherit'});if(r.status!==0)process.exit(r.status||1)}"""
_NODE_STATIC_BUILD = """const fs=require('fs');if(!fs.existsSync('index.html')){console.error('index.html is required');process.exit(1)}"""
_NODE_STATIC_SERVER = """const http=require('http'),fs=require('fs'),p=require('path');const args=process.argv.slice(1);const value=(name,fallback)=>{const i=args.indexOf(name);return i>=0&&args[i+1]?args[i+1]:fallback};const host=value('--host','0.0.0.0'),port=Number(value('--port','8080'));const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.cjs':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'};http.createServer((req,res)=>{let raw=decodeURIComponent((req.url||'/').split('?')[0]);if(raw==='/')raw='/index.html';const rel=p.normalize(raw).replace(/^([/\\\\])+/, '');const file=p.resolve('.',rel);const root=p.resolve('.');if(!file.startsWith(root+p.sep)&&file!==root){res.writeHead(403);return res.end('forbidden')}fs.stat(file,(err,stat)=>{if(err||!stat.isFile()){res.writeHead(404);return res.end('not found')}res.writeHead(200,{'Content-Type':types[p.extname(file).toLowerCase()]||'application/octet-stream'});fs.createReadStream(file).pipe(res)})}).listen(port,host);"""


_COMMON_RESOURCES={"cpu":1,"memoryMb":512,"processes":32,"openFiles":256,"diskMb":256,"durationSeconds":300,"idleSeconds":60,"logBytes":1000000,"artifactBytes":10000000,"previewSeconds":1800}
_FULLSTACK_RESOURCES={"cpu":2,"memoryMb":1536,"processes":64,"openFiles":512,"diskMb":1024,"durationSeconds":600,"idleSeconds":120,"logBytes":2000000,"artifactBytes":50000000,"previewSeconds":1800}
_COMMON_FILESYSTEM={"source":"read_only_after_stage","writable":["runtime","artifacts","logs","tmp"],"hostMounts":False,"engineSocket":False}

PROFILES={
 "python-stdlib-web":{
  "profileVersion":1,"baseRuntime":"python:3.12-slim","language":"Python 3.12 standard-library web application","dependencyManifests":[],"operations":["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],"commands":{"static_analysis":["python","-m","py_compile","app.py"],"build":["python","build.py"],"test":["python","-m","unittest","-v"],"start":["python","app.py","--host","0.0.0.0","--port","8080"]},"ports":[8080],"health":{"path":"/health","expectedStatus":200,"bodyMarker":"operly-generated-ok"},"filesystem":_COMMON_FILESYSTEM,"network":{"install":"none","runtime":"none"},"resources":_COMMON_RESOURCES,"artifactPaths":["artifacts"]
 },
 "static-web-js":{
  "profileVersion":1,"baseRuntime":"node:22-slim","language":"HTML/CSS/JavaScript with Node 22 verification","dependencyManifests":[],"operations":["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],"commands":{"static_analysis":["node","-e",_NODE_STATIC_ANALYSIS],"build":["node","-e",_NODE_STATIC_BUILD],"test":["node","--test"],"start":["node","-e",_NODE_STATIC_SERVER,"--","--host","0.0.0.0","--port","8080"]},"ports":[8080],"health":{"path":"/","expectedStatus":200,"bodyMarker":None},"filesystem":_COMMON_FILESYSTEM,"network":{"install":"none","runtime":"none"},"resources":_COMMON_RESOURCES,"artifactPaths":["artifacts"]
 },
 "operly-fullstack-v1":{
  "profileVersion":1,
  "baseRuntime":"operly-fullstack-python312-node22-v1",
  "language":"Controlled Python 3.12 backend/workers with static or npm-built browser frontend",
  "dependencyManifests":["backend/requirements.lock","frontend/package-lock.json"],
  "operations":["stage_source","resolve_dependencies","static_analysis","build","test","start","health_check","acceptance_test"],
  "execution":{"backend":{"mode":"python-cli","entrypoint":"backend/app.py"},"worker":{"mode":"optional-python-cli","entrypoint":"workers/worker.py"},"frontend":{"modes":["static","npm-build"],"staticRoot":"frontend","buildRoot":"frontend/dist"}},
  "ports":[8080],
  "health":{"path":"/health","expectedStatus":200,"bodyMarker":None},
  "filesystem":_COMMON_FILESYSTEM,
  "network":{"install":"dependency_registry_only","runtime":"loopback_only"},
  "resources":_FULLSTACK_RESOURCES,
  "artifactPaths":["artifacts","frontend/dist"]
 }
}


def runtime_profile(profile_id:str)->dict:
 if profile_id not in PROFILES:raise ValueError("Unsupported runtime profile")
 return deepcopy(PROFILES[profile_id])


def runtime_capabilities()->dict:
 """Public runner capability description; commands remain deterministic server policy."""
 return {"protocolVersion":2,"profiles":{key:{"profileVersion":value["profileVersion"],"baseRuntime":value["baseRuntime"],"language":value["language"],"operations":value["operations"]} for key,value in PROFILES.items()}}

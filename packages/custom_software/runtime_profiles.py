"""Finite runtime mechanics, independent of application domain."""
from copy import deepcopy


_NODE_STATIC_ANALYSIS = """const fs=require('fs'),p=require('path'),cp=require('child_process');const files=[];function walk(d){for(const e of fs.readdirSync(d,{withFileTypes:true})){if(e.name==='node_modules'||e.name.startsWith('.'))continue;const f=p.join(d,e.name);if(e.isDirectory())walk(f);else if(/\\.(m?js|cjs)$/.test(e.name))files.push(f)}}walk('.');for(const f of files){const r=cp.spawnSync(process.execPath,['--check',f],{stdio:'inherit'});if(r.status!==0)process.exit(r.status||1)}"""
_NODE_STATIC_BUILD = """const fs=require('fs');if(!fs.existsSync('index.html')){console.error('index.html is required');process.exit(1)}"""
_NODE_STATIC_SERVER = """const http=require('http'),fs=require('fs'),p=require('path');const args=process.argv.slice(1);const value=(name,fallback)=>{const i=args.indexOf(name);return i>=0&&args[i+1]?args[i+1]:fallback};const host=value('--host','0.0.0.0'),port=Number(value('--port','8080'));const types={'.html':'text/html; charset=utf-8','.css':'text/css; charset=utf-8','.js':'text/javascript; charset=utf-8','.mjs':'text/javascript; charset=utf-8','.json':'application/json; charset=utf-8','.svg':'image/svg+xml','.png':'image/png','.jpg':'image/jpeg','.jpeg':'image/jpeg','.webp':'image/webp'};http.createServer((req,res)=>{let raw=decodeURIComponent((req.url||'/').split('?')[0]);if(raw==='/')raw='/index.html';const rel=p.normalize(raw).replace(/^([/\\\\])+/, '');const file=p.resolve('.',rel);const root=p.resolve('.');if(!file.startsWith(root+p.sep)&&file!==root){res.writeHead(403);return res.end('forbidden')}fs.stat(file,(err,stat)=>{if(err||!stat.isFile()){res.writeHead(404);return res.end('not found')}res.writeHead(200,{'Content-Type':types[p.extname(file).toLowerCase()]||'application/octet-stream'});fs.createReadStream(file).pipe(res)})}).listen(port,host);"""


PROFILES={
 "python-stdlib-web":{
  "baseRuntime":"python:3.12-slim","language":"Python 3.12","dependencyManifests":[],"operations":["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],"commands":{"static_analysis":["python","-m","py_compile","app.py"],"build":["python","build.py"],"test":["python","-m","unittest","-v"],"start":["python","app.py","--host","0.0.0.0","--port","8080"]},"ports":[8080],"health":{"path":"/health","expectedStatus":200,"bodyMarker":"operly-generated-ok"},"filesystem":{"source":"read_only_after_stage","writable":["runtime","artifacts","logs","tmp"],"hostMounts":False,"engineSocket":False},"network":{"install":"none","runtime":"none"},"resources":{"cpu":1,"memoryMb":512,"processes":32,"openFiles":256,"diskMb":256,"durationSeconds":300,"idleSeconds":60,"logBytes":1000000,"artifactBytes":10000000,"previewSeconds":1800},"artifactPaths":["artifacts"]
 },
 "static-web-js":{
  "baseRuntime":"node:22-slim","language":"HTML/CSS/JavaScript with Node 22 verification","dependencyManifests":[],"operations":["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],"commands":{"static_analysis":["node","-e",_NODE_STATIC_ANALYSIS],"build":["node","-e",_NODE_STATIC_BUILD],"test":["node","--test"],"start":["node","-e",_NODE_STATIC_SERVER,"--","--host","0.0.0.0","--port","8080"]},"ports":[8080],"health":{"path":"/","expectedStatus":200,"bodyMarker":None},"filesystem":{"source":"read_only_after_stage","writable":["runtime","artifacts","logs","tmp"],"hostMounts":False,"engineSocket":False},"network":{"install":"none","runtime":"none"},"resources":{"cpu":1,"memoryMb":512,"processes":32,"openFiles":256,"diskMb":256,"durationSeconds":300,"idleSeconds":60,"logBytes":1000000,"artifactBytes":10000000,"previewSeconds":1800},"artifactPaths":["artifacts"]
 }
}

def runtime_profile(profile_id:str)->dict:
 if profile_id not in PROFILES:raise ValueError("Unsupported runtime profile")
 return deepcopy(PROFILES[profile_id])

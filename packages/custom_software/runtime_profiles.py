"""Finite runtime mechanics, independent of application domain."""
from copy import deepcopy

PROFILES={
 "python-stdlib-web":{
  "baseRuntime":"python:3.12-slim","language":"Python 3.12","dependencyManifests":[],"operations":["stage_source","static_analysis","build","test","start","health_check","acceptance_test"],"commands":{"static_analysis":["python","-m","py_compile","app.py"],"build":["python","build.py"],"test":["python","-m","unittest","-v"],"start":["python","app.py","--host","0.0.0.0","--port","8080"]},"ports":[8080],"health":{"path":"/health","expectedStatus":200,"bodyMarker":"operly-generated-ok"},"filesystem":{"source":"read_only_after_stage","writable":["runtime","artifacts","logs","tmp"],"hostMounts":False,"engineSocket":False},"network":{"install":"none","runtime":"none"},"resources":{"cpu":1,"memoryMb":512,"processes":32,"openFiles":256,"diskMb":256,"durationSeconds":300,"idleSeconds":60,"logBytes":1000000,"artifactBytes":10000000,"previewSeconds":1800},"artifactPaths":["artifacts"]
 }
}
def runtime_profile(profile_id:str)->dict:
 if profile_id not in PROFILES:raise ValueError("Unsupported runtime profile")
 return deepcopy(PROFILES[profile_id])

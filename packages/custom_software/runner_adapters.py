"""External production runner interface and explicitly test-only implementations."""
from __future__ import annotations
import abc,asyncio,hashlib,hmac,json,os,subprocess,sys,tempfile,time,urllib.error,urllib.request
from pathlib import Path
import aiohttp
from packages.custom_software.runner_contracts import BuildSubmission,RunnerResult
from packages.custom_software.source_bundles import SourceBundle
from packages.custom_software.sandbox import validate_runner_url,SandboxUnavailable,SandboxFailure

class RunnerAdapter(abc.ABC):
 implementation="abstract";isolation_profile="unknown"
 @abc.abstractmethod
 async def submit(self,submission:BuildSubmission,bundle:SourceBundle)->dict:...
 async def status(self,job_id:str)->dict:raise NotImplementedError
 async def cancel(self,job_id:str)->dict:raise NotImplementedError
 async def cleanup(self,job_id:str)->dict:raise NotImplementedError
 async def stop_preview(self,preview_id:str)->dict:raise NotImplementedError

class ExternalRunnerAdapter(RunnerAdapter):
 implementation="external_https_v1";isolation_profile="remote_container_or_microvm"
 def __init__(self,url=None,token=None):self.url=(url or os.getenv("OPERLY_SANDBOX_RUNNER_URL","")).rstrip("/");self.token=token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN")
 async def _request(self,method,path,payload=None):
  if not self.url or not self.token:raise SandboxUnavailable("External isolated runner is not configured")
  self.url=validate_runner_url(self.url)
  raw=json.dumps(payload or {},sort_keys=True).encode();signature=hmac.new(self.token.encode(),raw,hashlib.sha256).hexdigest()
  timeout=aiohttp.ClientTimeout(total=30)
  try:
   async with aiohttp.ClientSession(timeout=timeout) as session:
    async with session.request(method,self.url+path,data=raw,headers={"Authorization":f"Bearer {self.token}","Content-Type":"application/json","X-Operly-Signature":signature}) as response:
     body=await response.read()
     if response.status not in range(200,300):raise SandboxFailure(f"Runner request failed with status {response.status}")
     expected=hmac.new(self.token.encode(),body,hashlib.sha256).hexdigest()
     if not hmac.compare_digest(response.headers.get("X-Operly-Signature",""),expected):raise SandboxFailure("External runner response signature is invalid")
     return json.loads(body)
  except (aiohttp.ClientError,ValueError) as error:raise SandboxFailure("External runner communication failed") from error
 async def submit(self,submission,bundle):return await self._request("POST","/v1/builds",{"submission":submission.model_dump(mode="json"),"bundle":{"manifest":bundle.manifest,"files":[{"path":x.path,"content":x.content.decode(),"generatedBy":x.generated_by} for x in bundle.files]}})
 async def status(self,job_id):return await self._request("GET",f"/v1/builds/{job_id}")
 async def cancel(self,job_id):return await self._request("POST",f"/v1/builds/{job_id}/cancel")
 async def cleanup(self,job_id):return await self._request("POST",f"/v1/builds/{job_id}/cleanup")
 async def stop_preview(self,preview_id):return await self._request("DELETE",f"/v1/previews/{preview_id}")

class FakeRunnerAdapter(RunnerAdapter):
 implementation="fake_test_only";isolation_profile="none_fake"
 def __init__(self,fail_at=None):self.fail_at=fail_at;self.jobs={}
 async def submit(self,submission,bundle):
  job_id=f"fake-{len(self.jobs)+1}";ok=self.fail_at is None;result=RunnerResult(buildSuccess=ok,testSuccess=ok,processStartSuccess=ok,healthCheckSuccess=ok,acceptanceCheckSuccess=ok,previewAvailable=ok,failureEvidence={} if ok else {"classification":self.fail_at,"message":"deterministic fake failure"})
  data={"jobId":job_id,"state":"preview_ready" if ok else "failed","result":result.model_dump(),"events":[{"state":"created"},{"state":self.fail_at or "preview_ready"}],"preview":{"id":f"preview-{job_id}","targetUrl":"http://runner.invalid"} if ok else None};self.jobs[job_id]=data;return data
 async def cancel(self,job_id):self.jobs[job_id]["state"]="cancelled";return {"state":"cancelled"}
 async def status(self,job_id):return self.jobs[job_id]
 async def cleanup(self,job_id):self.jobs[job_id]["state"]="cleaned";return {"state":"cleaned"}
 async def stop_preview(self,preview_id):return {"state":"stopped"}

class LocalSubprocessTestRunner(RunnerAdapter):
 """Integration-test runner. Process isolation only; never selectable in production."""
 implementation="local_subprocess_test_only";isolation_profile="constrained_subprocess_not_os_isolated"
 def __init__(self):
  if os.getenv("OPERLY_ENABLE_TEST_SUBPROCESS_RUNNER")!="1" or os.getenv("OPERLY_ENV","test") not in {"test","development"}:raise SandboxUnavailable("Test subprocess runner is disabled")
  self.jobs={};self.previews={}
 async def submit(self,submission,bundle):
  root=Path(tempfile.mkdtemp(prefix="operly-runner-test-"));source=root/"source";runtime=root/"runtime";artifacts=root/"artifacts";logs=root/"logs";tmp=root/"tmp"
  for p in (source,runtime,artifacts,logs,tmp):p.mkdir()
  for item in bundle.files:
   target=source/item.path;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(item.content)
  env={"PYTHONPATH":str(source),"PATH":os.environ.get("PATH","")}
  for key in ("SYSTEMROOT","WINDIR","TEMP","TMP"):
   if os.environ.get(key):env[key]=os.environ[key]
  events=[]
  def run(label,args,timeout=30):
   try:result=subprocess.run(args,cwd=source,env=env,capture_output=True,text=True,timeout=min(timeout,submission.maxDurationSeconds))
   except subprocess.TimeoutExpired:
    events.append({"state":"timed_out","exitCode":None,"message":"Typed operation exceeded its execution limit"});return None
   output=result.stdout+result.stderr
   if len(output.encode())>submission.resources.logBytes:
    events.append({"state":"resource_exceeded","exitCode":result.returncode,"message":"Log output exceeded policy"});return None
   events.append({"state":label,"exitCode":result.returncode,"message":output[-4000:]});return result
  for label,args in (("static_analysis",[sys.executable,"-m","py_compile","app.py"]),("building",[sys.executable,"build.py"]),("testing",[sys.executable,"-m","unittest","-v"])):
   result=await asyncio.to_thread(run,label,args)
   if result is None:return {"jobId":root.name,"state":"failed","result":RunnerResult(failureEvidence={"classification":"resource_violation","log":events[-1]["message"]}).model_dump(),"events":events}
   if result.returncode:return {"jobId":root.name,"state":"failed","result":RunnerResult(failureEvidence={"classification":"test_failure" if label=="testing" else "build_failure","log":events[-1]["message"]}).model_dump(),"events":events}
  port=await asyncio.to_thread(_free_port);process=subprocess.Popen([sys.executable,"app.py","--host","127.0.0.1","--port",str(port)],cwd=source,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True)
  url=f"http://127.0.0.1:{port}";healthy=False
  for _ in range(30):
   try:
    with urllib.request.urlopen(url+submission.healthCheck.path,timeout=1) as response:healthy=response.status==submission.healthCheck.expectedStatus and (not submission.healthCheck.bodyMarker or submission.healthCheck.bodyMarker in response.read().decode())
   except Exception:await asyncio.sleep(.1)
   if healthy:break
  if not healthy:process.terminate();return {"jobId":root.name,"state":"failed","result":RunnerResult(buildSuccess=True,testSuccess=True,processStartSuccess=process.poll() is None,failureEvidence={"classification":"health_check_failure"}).model_dump(),"events":events}
  acceptance=await asyncio.to_thread(_football_acceptance,url)
  ok=acceptance["passed"];preview_id="preview-"+root.name;self.jobs[root.name]={"root":root,"process":process}
  if ok:self.previews[preview_id]={"job":root.name,"url":url}
  else:await self.cleanup(root.name)
  built=source/"artifacts"/"build.txt";artifact_data=built.read_bytes() if built.exists() else b""
  result=RunnerResult(buildSuccess=True,testSuccess=True,processStartSuccess=True,healthCheckSuccess=True,acceptanceCheckSuccess=ok,previewAvailable=ok,artifacts=[{"kind":"build_output","name":"build.txt","digest":"sha256:"+hashlib.sha256(artifact_data).hexdigest(),"sizeBytes":len(artifact_data),"reference":"runner-artifact://build.txt"}],testReport={"unit":"passed","acceptance":acceptance},staticAnalysisReport={"pythonCompile":"passed"},dependencyReport={"dependencies":[],"networkUsed":False},resourceUsage={"profile":"test_subprocess","limitsEnforced":"timeout and bounded output only"})
  return {"jobId":root.name,"state":"preview_ready" if ok else "failed","result":result.model_dump(),"events":events+[ {"state":"preview_ready" if ok else "acceptance_failed","message":json.dumps(acceptance)}],"preview":{"id":preview_id,"targetUrl":url} if ok else None}
 async def cancel(self,job_id):return await self.cleanup(job_id)
 async def status(self,job_id):return {"jobId":job_id,"state":"preview_ready" if job_id in self.jobs else "cleaned"}
 async def cleanup(self,job_id):
  job=self.jobs.pop(job_id,None)
  if job and job["process"].poll() is None:job["process"].terminate();job["process"].wait(timeout=5)
  if job:
   import shutil;shutil.rmtree(job["root"],ignore_errors=True)
  return {"state":"cleaned"}
 async def stop_preview(self,preview_id):
  item=self.previews.pop(preview_id,None)
  if item:return await self.cleanup(item["job"])
  return {"state":"cleaned"}
def _free_port():
 import socket
 with socket.socket() as s:s.bind(("127.0.0.1",0));return s.getsockname()[1]
def _football_acceptance(url):
 def get(path):
  with urllib.request.urlopen(url+path,timeout=3) as r:return json.loads(r.read()) if "json" in r.headers.get("content-type","") else r.read().decode()
 payload=json.dumps({"home":"North FC","away":"South FC","homeScore":2,"awayScore":1}).encode();request=urllib.request.Request(url+"/api/matches",data=payload,headers={"Content-Type":"application/json"},method="POST")
 with urllib.request.urlopen(request,timeout=3) as response:first=json.loads(response.read())
 reloaded=get("/api/standings");page=get("/")
 return {"passed":first["standings"][0]["points"]==3 and reloaded[0]["points"]==3 and "field service" not in page.lower(),"persistence":reloaded,"pageMarker":"Football Match Intelligence" in page}

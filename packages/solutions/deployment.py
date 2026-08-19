import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

class DeploymentUnavailable(RuntimeError):pass
class DeploymentFailure(RuntimeError):pass

@dataclass(frozen=True)
class DeploymentResult:
 provider_reference:str;artifact_reference:str;artifact_digest:str;health_state:str;health_evidence:dict

class DeploymentProvider:
 name="abstract"
 async def deploy(self,*,solution_id,version_reference,content):raise NotImplementedError
 async def health(self,result):raise NotImplementedError

class UnconfiguredDeploymentProvider(DeploymentProvider):
 name="unconfigured"
 async def deploy(self,**kwargs):raise DeploymentUnavailable("Production deployment provider is not configured")
 async def health(self,result):return False,{"reason":"provider_unconfigured"}

class ManagedStaticDeploymentProvider(DeploymentProvider):
 """Immutable static artifact deployment; it never executes generated source."""
 name="managed_static_v1"
 def __init__(self,root=None,max_bytes=2_000_000):
  value=root or os.getenv("OPERLY_DEPLOYMENT_ROOT","")
  if not value:raise DeploymentUnavailable("OPERLY_DEPLOYMENT_ROOT is not configured")
  self.root=Path(value).resolve();self.max_bytes=max_bytes;self.root.mkdir(parents=True,exist_ok=True)
 async def deploy(self,*,solution_id,version_reference,content):
  raw=content.encode("utf-8")
  if len(raw)>self.max_bytes:raise DeploymentFailure("Deployment artifact exceeds the size limit")
  if re.search(rb"(?i)<\s*(object|embed)|javascript:",raw):raise DeploymentFailure("Deployment artifact violates static content policy")
  digest=hashlib.sha256(raw).hexdigest();folder=(self.root/solution_id).resolve()
  if self.root not in folder.parents:raise DeploymentFailure("Invalid deployment target")
  folder.mkdir(parents=True,exist_ok=True);target=(folder/f"{digest}.html").resolve()
  if folder not in target.parents:raise DeploymentFailure("Invalid artifact target")
  target.write_bytes(raw)
  return DeploymentResult(f"static:{solution_id}:{digest[:16]}",str(target),f"sha256:{digest}","pending",{})
 async def health(self,result):
  path=Path(result.artifact_reference)
  try:raw=path.read_bytes()
  except OSError:return False,{"reason":"artifact_unreadable"}
  digest="sha256:"+hashlib.sha256(raw).hexdigest();healthy=digest==result.artifact_digest and b"<html" in raw.lower() and len(raw)<=self.max_bytes
  return healthy,{"artifact_digest":digest,"bytes":len(raw),"static_validation":healthy}

def configured_provider():
 kind=os.getenv("OPERLY_DEPLOYMENT_PROVIDER","").strip().lower()
 if kind=="managed_static":
  try:return ManagedStaticDeploymentProvider()
  except DeploymentUnavailable:return UnconfiguredDeploymentProvider()
 return UnconfiguredDeploymentProvider()

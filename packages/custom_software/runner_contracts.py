"""Typed contracts shared by the OPERLY control plane and isolated runners."""
from datetime import datetime
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,field_validator,model_validator

class Strict(BaseModel):model_config=ConfigDict(extra="forbid")
class Dependency(Strict):
 name:str=Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$");version:str=Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.+!-]{0,79}$");registry:str="pypi"
class ResourcePolicy(Strict):
 cpu:float=Field(default=1,gt=0,le=4);memoryMb:int=Field(default=512,ge=128,le=4096);processes:int=Field(default=32,ge=1,le=128);openFiles:int=Field(default=256,ge=32,le=2048);diskMb:int=Field(default=256,ge=32,le=2048);durationSeconds:int=Field(default=300,ge=10,le=1800);idleSeconds:int=Field(default=60,ge=5,le=300);logBytes:int=Field(default=1_000_000,ge=1024,le=10_000_000);artifactBytes:int=Field(default=10_000_000,ge=1024,le=100_000_000);previewSeconds:int=Field(default=1800,ge=60,le=14400)
class NetworkPolicy(Strict):
 mode:Literal["none","loopback_only","dependency_registry_only","approved_hosts","sandbox_integrations"]="none";approvedHosts:list[str]=[]
 @field_validator("approvedHosts")
 @classmethod
 def safe_hosts(cls,hosts):
  forbidden=("169.254.169.254","metadata.google.internal","localhost","127.","10.","192.168.","172.16.")
  if any(any(h.lower().startswith(x) for x in forbidden) for h in hosts):raise ValueError("Private and metadata hosts are forbidden")
  return hosts
class HealthCheck(Strict):path:str=Field(default="/health",pattern=r"^/[A-Za-z0-9_./-]*$");expectedStatus:int=200;bodyMarker:str|None=None;timeoutSeconds:int=30
class BuildSubmission(Strict):
 workspaceId:str;applicationId:str;planVersion:int=Field(ge=1);sourceVersion:int=Field(ge=1);stackId:str;sourceBundleDigest:str=Field(pattern=r"^sha256:[a-f0-9]{64}$");dependencies:list[Dependency]=Field(default=[],max_length=50);operations:list[Literal["stage_source","resolve_dependencies","static_analysis","build","test","start","health_check","acceptance_test"]];healthCheck:HealthCheck;resources:ResourcePolicy=ResourcePolicy();network:NetworkPolicy=NetworkPolicy();secretAliases:list[str]=Field(default=[],max_length=20);requiredPorts:list[int]=Field(default=[8080],max_length=4);artifactPaths:list[str]=Field(default=["artifacts"],max_length=20);maxDurationSeconds:int=Field(default=300,ge=10,le=1800);idempotencyKey:str=Field(min_length=8,max_length=120)
 @model_validator(mode="after")
 def validate_submission(self):
  if any(p<1024 or p>65535 for p in self.requiredPorts):raise ValueError("Only unprivileged ports are permitted")
  if len(set(self.requiredPorts))!=len(self.requiredPorts):raise ValueError("Ports must be unique")
  if any("/" in d.name or "\\" in d.name for d in self.dependencies):raise ValueError("Local path dependencies are forbidden")
  return self
class RunnerEventContract(Strict):
 sequence:int=Field(ge=1);timestamp:datetime;eventType:str;state:str;message:str=Field(max_length=4000);commandLabel:str|None=None;exitCode:int|None=None;artifactReference:str|None=None;logReference:str|None=None;securityEvent:bool=False;resourceEvent:bool=False
class RunnerResult(Strict):
 buildSuccess:bool=False;testSuccess:bool=False;processStartSuccess:bool=False;healthCheckSuccess:bool=False;acceptanceCheckSuccess:bool=False;previewAvailable:bool=False;artifacts:list[dict]=[];testReport:dict={};staticAnalysisReport:dict={};dependencyReport:dict={};resourceUsage:dict={};failureEvidence:dict={}
 @model_validator(mode="after")
 def preview_truth(self):
  if self.previewAvailable and not all((self.buildSuccess,self.testSuccess,self.processStartSuccess,self.healthCheckSuccess,self.acceptanceCheckSuccess)):raise ValueError("Preview requires every execution quality gate")
  return self
class RunnerJobContract(Strict):
 jobId:str;jobType:Literal["build","repair","cleanup"];state:str;createdAt:datetime;startedAt:datetime|None=None;completedAt:datetime|None=None;sourceDigest:str;runnerImplementation:str;isolationProfile:str;resources:ResourcePolicy;attempt:int=1;parentRepairAttempt:str|None=None;failureClassification:str|None=None;exitInformation:dict={}

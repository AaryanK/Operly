"""Fail-closed adapter for a separately isolated code-generation runner."""
import os
import hashlib
import hmac
import ipaddress
import json
from urllib.parse import urlparse

import aiohttp

from packages.custom_software.architectures import architecture_plan


FRAMEWORKS={"commerce":"nextjs-postgres","marketplace":"nextjs-postgres","booking":"nextjs-postgres","membership":"nextjs-postgres","inventory":"fastapi-react-postgres","crm":"fastapi-react-postgres","quotation":"fastapi-react-postgres","approval":"fastapi-react-postgres","field_service":"fastapi-react-postgres","support_desk":"fastapi-react-postgres","project_management":"nextjs-postgres","content_platform":"nextjs-postgres","custom":"agent-selected"}
RUNNER_POLICY={"network":"deny_by_default","dependencies":"allowlist_and_lockfile","secrets":"preview_scoped","filesystem":"ephemeral","productionDeploy":False,"tests":["typecheck","unit","integration","browser","authorization"],"limits":{"cpu":2,"memoryMb":2048,"buildSeconds":900}}


class SandboxUnavailable(RuntimeError):pass
class SandboxFailure(RuntimeError):pass

def validate_runner_url(url:str)->str:
    parsed=urlparse(url)
    if parsed.scheme!="https" or not parsed.hostname or parsed.username or parsed.password:raise SandboxUnavailable("The isolated runner URL must be an authenticated HTTPS origin")
    allowed={x.strip().lower() for x in os.getenv("OPERLY_SANDBOX_RUNNER_HOSTS","").split(",") if x.strip()}
    if allowed and parsed.hostname.lower() not in allowed:raise SandboxUnavailable("The isolated runner host is not allowlisted")
    if parsed.hostname.lower() in {"localhost","localhost.localdomain"}:raise SandboxUnavailable("Private runner addresses are forbidden")
    try:
        if ipaddress.ip_address(parsed.hostname).is_private or ipaddress.ip_address(parsed.hostname).is_loopback:raise SandboxUnavailable("Private runner addresses are forbidden")
    except ValueError:pass
    return url.rstrip("/")


def generation_plan(prompt:str)->dict:
    plan=architecture_plan(prompt);plan.update({"framework":FRAMEWORKS[plan["family"]],"policy":RUNNER_POLICY,"outputs":["sourceArchive","testReport","artifactGraph","previewUrl","buildDigest"]});return plan


class SandboxRunner:
    def __init__(self,url:str|None=None,token:str|None=None):self.url=(url or os.getenv("OPERLY_SANDBOX_RUNNER_URL") or "").rstrip("/");self.token=token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN")
    async def generate(self,prompt:str,tenant_id:str,user_id:str)->dict:
        if not self.url or not self.token:raise SandboxUnavailable("An isolated code runner is not configured")
        self.url=validate_runner_url(self.url)
        payload={"prompt":prompt,"tenantId":tenant_id,"requestedBy":user_id,"plan":generation_plan(prompt)}
        timeout=aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.url}/v1/generation-jobs",json=payload,headers={"Authorization":f"Bearer {self.token}"}) as response:
                    raw=await response.read();signature=response.headers.get("X-Operly-Signature","")
                    expected=hmac.new(self.token.encode(),raw,hashlib.sha256).hexdigest()
                    if not hmac.compare_digest(signature,expected):raise SandboxFailure("The isolated runner response signature is invalid")
                    try:data=json.loads(raw)
                    except (TypeError,ValueError) as error:raise SandboxFailure("The isolated runner returned invalid JSON") from error
                    if response.status not in {200,201,202}:raise SandboxFailure("The isolated runner rejected the generation job")
        except aiohttp.ClientError as error:raise SandboxFailure("The isolated runner is unavailable") from error
        required={"jobId","status"}
        if not isinstance(data,dict) or not required<=set(data) or set(data)-{"jobId","status","previewUrl","buildDigest"}:raise SandboxFailure("The isolated runner returned an invalid response")
        if data["status"] not in {"queued","running","completed","failed","cancelled"}:raise SandboxFailure("The isolated runner returned an invalid status")
        return data

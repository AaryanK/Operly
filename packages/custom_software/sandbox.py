"""Fail-closed adapter for a separately isolated code-generation runner."""
import os

import aiohttp

from packages.custom_software.architectures import architecture_plan


FRAMEWORKS={"commerce":"nextjs-postgres","marketplace":"nextjs-postgres","booking":"nextjs-postgres","membership":"nextjs-postgres","inventory":"fastapi-react-postgres","crm":"fastapi-react-postgres","quotation":"fastapi-react-postgres","approval":"fastapi-react-postgres","field_service":"fastapi-react-postgres"}
RUNNER_POLICY={"network":"deny_by_default","dependencies":"allowlist_and_lockfile","secrets":"preview_scoped","filesystem":"ephemeral","productionDeploy":False,"tests":["typecheck","unit","integration","browser","authorization"],"limits":{"cpu":2,"memoryMb":2048,"buildSeconds":900}}


class SandboxUnavailable(RuntimeError):pass
class SandboxFailure(RuntimeError):pass


def generation_plan(prompt:str)->dict:
    plan=architecture_plan(prompt);plan.update({"framework":FRAMEWORKS[plan["family"]],"policy":RUNNER_POLICY,"outputs":["sourceArchive","testReport","artifactGraph","previewUrl","buildDigest"]});return plan


class SandboxRunner:
    def __init__(self,url:str|None=None,token:str|None=None):self.url=(url or os.getenv("OPERLY_SANDBOX_RUNNER_URL") or "").rstrip("/");self.token=token or os.getenv("OPERLY_SANDBOX_RUNNER_TOKEN")
    async def generate(self,prompt:str,tenant_id:str,user_id:str)->dict:
        if not self.url or not self.token:raise SandboxUnavailable("An isolated code runner is not configured")
        payload={"prompt":prompt,"tenantId":tenant_id,"requestedBy":user_id,"plan":generation_plan(prompt)}
        timeout=aiohttp.ClientTimeout(total=30)
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(f"{self.url}/v1/generation-jobs",json=payload,headers={"Authorization":f"Bearer {self.token}"}) as response:
                    data=await response.json()
                    if response.status not in {200,201,202}:raise SandboxFailure("The isolated runner rejected the generation job")
        except aiohttp.ClientError as error:raise SandboxFailure("The isolated runner is unavailable") from error
        required={"jobId","status"}
        if not required<=set(data):raise SandboxFailure("The isolated runner returned an invalid response")
        return data

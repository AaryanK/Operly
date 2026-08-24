"""Production isolated-runner gateway for Operly generated software.

The base Docker lifecycle remains unchanged; this package bootstrap extends its
semantic capability sidecars with trusted built-in runtime gateways. Keeping the
extension here lets every import path use the same hardened class without forking
the large build/test/runtime lifecycle.
"""
from __future__ import annotations

import uuid

from apps.runner import docker_backend as _docker_backend
from packages.app_identity.contracts import APP_IDENTITY_CAPABILITY_ID
from packages.relational_data.contracts import RELATIONAL_CAPABILITY_ID
from packages.runtime_plugins.app_identity_source_validation import validate_app_identity_source
from packages.workspace_entities.contracts import WORKSPACE_ENTITY_CAPABILITY_ID
from packages.workspace_entities.manifest import validate_workspace_entity_source

_BaseDockerIsolationBackend = _docker_backend.DockerIsolationBackend


class DockerIsolationBackend(_BaseDockerIsolationBackend):
    _BINDING_PREFIXES = {
        RELATIONAL_CAPABILITY_ID: "/api/runtime/relational",
        WORKSPACE_ENTITY_CAPABILITY_ID: "/api/runtime/entities",
        APP_IDENTITY_CAPABILITY_ID: "/api/runtime/app-identity",
    }

    def _binding_file_rows(self, submission, short: str) -> list[dict]:
        rows: list[dict] = []
        for binding in submission.serviceBindings:
            if binding.transport is not None and binding.capabilityId not in self._BINDING_PREFIXES:
                raise _docker_backend.IsolationFailure("Runner transport is unsupported for this capability")
            row = {
                "semanticName": binding.semanticName,
                "capabilityId": binding.capabilityId,
                "required": binding.required,
            }
            if binding.capabilityId in self._BINDING_PREFIXES:
                if binding.transport is None:
                    raise _docker_backend.IsolationFailure("Capability binding is missing runner transport authorization")
                row["endpoint"] = f"http://{self._binding_container_name(short, binding.semanticName)}:8083"
            rows.append(row)
        return rows

    def _start_binding_proxies(self, submission, network, labels: dict, short: str) -> list:
        proxies = []
        try:
            for binding in submission.serviceBindings:
                prefix = self._BINDING_PREFIXES.get(binding.capabilityId)
                if prefix is None:
                    continue
                transport = binding.transport
                if transport is None:
                    raise _docker_backend.IsolationFailure("Capability runtime authorization is unavailable")
                gateway = self._validated_binding_url(transport.gatewayUrl)
                proxy = self.client.containers.run(
                    self.proxy_image,
                    detach=True,
                    name=self._binding_container_name(short, binding.semanticName),
                    network=network.name,
                    environment={
                        "OPERLY_PROXY_MODE": "binding",
                        "OPERLY_PROXY_PORT": "8083",
                        "OPERLY_PROXY_BINDING_TARGET": gateway,
                        "OPERLY_PROXY_BINDING_TOKEN": transport.runtimeToken,
                        "OPERLY_PROXY_BINDING_PREFIX": prefix,
                    },
                    labels=labels,
                    mem_limit="96m",
                    pids_limit=64,
                    cap_drop=["ALL"],
                    security_opt=["no-new-privileges:true"],
                    read_only=True,
                    tmpfs={"/tmp": "rw,noexec,nosuid,size=16m"},
                )
                self.client.networks.get(self.egress_network).connect(proxy)
                proxies.append(proxy)
        except Exception:
            for proxy in proxies:
                try:
                    proxy.remove(force=True)
                except Exception:
                    pass
            raise
        return proxies

    def run_job(self, submission, bundle, *, event_callback=lambda _event: None, cancelled=lambda: False, job_id=None):
        entities = validate_workspace_entity_source(bundle)
        identity = validate_app_identity_source(bundle)
        errors = tuple(dict.fromkeys((*entities.errors, *identity.errors)))
        if not entities.valid or not identity.valid:
            return self._failed(
                job_id or uuid.uuid4().hex,
                [],
                "security_policy_violation",
                "; ".join(errors),
            )
        return super().run_job(
            submission,
            bundle,
            event_callback=event_callback,
            cancelled=cancelled,
            job_id=job_id,
        )


# All existing imports use apps.runner.docker_backend.DockerIsolationBackend. Replace
# that exported class once at package bootstrap so gateway and acceptance tests
# exercise the same semantic-binding extension.
_docker_backend.DockerIsolationBackend = DockerIsolationBackend

__all__ = ["DockerIsolationBackend"]

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable

from packages.plugins.contracts import NetworkPolicy, ResourcePolicy


@dataclass(frozen=True, slots=True)
class RuntimeProfile:
    id: str
    display_name: str
    description: str
    kind: str
    languages: frozenset[str]
    source_markers: tuple[str, ...]
    build_commands: tuple[tuple[str, ...], ...] = ()
    start_command: tuple[str, ...] | None = None
    supports_preview: bool = False
    supports_deploy: bool = False
    supports_service_bindings: bool = True
    default_network: NetworkPolicy = field(default_factory=NetworkPolicy)
    default_resources: ResourcePolicy = field(default_factory=ResourcePolicy)
    allowed_dependency_managers: frozenset[str] = frozenset()

    def public_dict(self) -> dict:
        return {
            "id": self.id,
            "display_name": self.display_name,
            "description": self.description,
            "kind": self.kind,
            "languages": sorted(self.languages),
            "source_markers": list(self.source_markers),
            "supports_preview": self.supports_preview,
            "supports_deploy": self.supports_deploy,
            "supports_service_bindings": self.supports_service_bindings,
            "default_network": {
                "mode": self.default_network.mode,
                "allowed_hosts": list(self.default_network.allowed_hosts),
            },
            "default_resources": {
                "cpu_millicores": self.default_resources.cpu_millicores,
                "memory_mb": self.default_resources.memory_mb,
                "disk_mb": self.default_resources.disk_mb,
                "max_runtime_seconds": self.default_resources.max_runtime_seconds,
                "max_concurrency": self.default_resources.max_concurrency,
            },
            "allowed_dependency_managers": sorted(self.allowed_dependency_managers),
        }


class RuntimeProfileRegistry:
    """Trusted mechanics available to user/generated plugin source.

    Profiles are infrastructure policy, not model-authored commands. Source may match or
    request a profile, but only this registry determines executable build/start mechanics.
    """

    def __init__(self, profiles: Iterable[RuntimeProfile] = ()) -> None:
        self._profiles: dict[str, RuntimeProfile] = {}
        for profile in profiles:
            self.register(profile)

    def register(self, profile: RuntimeProfile) -> None:
        key = profile.id.strip().lower()
        if not key or key != profile.id:
            raise ValueError("Runtime profile IDs must be normalized lowercase")
        if key in self._profiles:
            raise ValueError(f"Duplicate runtime profile: {key}")
        self._profiles[key] = profile

    def get(self, profile_id: str) -> RuntimeProfile:
        key = str(profile_id or "").strip().lower()
        try:
            return self._profiles[key]
        except KeyError as error:
            raise LookupError(f"Unknown runtime profile: {key or '<empty>'}") from error

    def all(self) -> tuple[RuntimeProfile, ...]:
        return tuple(self._profiles[key] for key in sorted(self._profiles))

    def detect(self, paths: Iterable[str]) -> tuple[RuntimeProfile, ...]:
        normalized = {str(path or "").strip().lower().lstrip("./") for path in paths}
        matches: list[RuntimeProfile] = []
        for profile in self.all():
            if any(marker.lower().lstrip("./") in normalized for marker in profile.source_markers):
                matches.append(profile)
        return tuple(matches)


def default_runtime_profiles() -> RuntimeProfileRegistry:
    common = ResourcePolicy(cpu_millicores=500, memory_mb=768, disk_mb=2048, max_runtime_seconds=900, max_concurrency=1)
    web_resources = ResourcePolicy(cpu_millicores=1000, memory_mb=1024, disk_mb=4096, max_runtime_seconds=3600, max_concurrency=4)
    return RuntimeProfileRegistry(
        (
            RuntimeProfile(
                id="static-web",
                display_name="Static web",
                description="Trusted build/serve policy for static HTML, CSS and browser JavaScript.",
                kind="static",
                languages=frozenset({"html", "css", "javascript"}),
                source_markers=("index.html",),
                supports_preview=True,
                supports_deploy=True,
                default_resources=common,
            ),
            RuntimeProfile(
                id="react-vite",
                display_name="React + Vite",
                description="Node build profile for Vite-based React applications; production output is immutable dist/.",
                kind="static",
                languages=frozenset({"typescript", "javascript", "html", "css"}),
                source_markers=("vite.config.ts", "vite.config.js"),
                build_commands=(("npm", "ci"), ("npm", "run", "build")),
                supports_preview=True,
                supports_deploy=True,
                default_network=NetworkPolicy(mode="egress"),
                default_resources=web_resources,
                allowed_dependency_managers=frozenset({"npm"}),
            ),
            RuntimeProfile(
                id="node-web",
                display_name="Node web service",
                description="Long-lived Node HTTP service profile with bounded resources and explicit ingress.",
                kind="web",
                languages=frozenset({"javascript", "typescript"}),
                source_markers=("package.json",),
                build_commands=(("npm", "ci"),),
                start_command=("npm", "start"),
                supports_preview=True,
                supports_deploy=True,
                default_network=NetworkPolicy(mode="egress"),
                default_resources=web_resources,
                allowed_dependency_managers=frozenset({"npm"}),
            ),
            RuntimeProfile(
                id="python-fastapi",
                display_name="Python web service",
                description="Python ASGI service profile for FastAPI-compatible applications using a locked dependency set.",
                kind="web",
                languages=frozenset({"python"}),
                source_markers=("pyproject.toml", "requirements.txt"),
                supports_preview=True,
                supports_deploy=True,
                default_network=NetworkPolicy(mode="egress"),
                default_resources=web_resources,
                allowed_dependency_managers=frozenset({"pip", "uv"}),
            ),
            RuntimeProfile(
                id="worker",
                display_name="Background worker",
                description="Long-lived non-public worker profile for event, queue and scheduled digital workloads.",
                kind="worker",
                languages=frozenset({"python", "javascript", "typescript"}),
                source_markers=("worker.toml",),
                supports_preview=False,
                supports_deploy=True,
                default_network=NetworkPolicy(mode="egress"),
                default_resources=web_resources,
                allowed_dependency_managers=frozenset({"pip", "uv", "npm"}),
            ),
            RuntimeProfile(
                id="sandbox-job",
                display_name="Ephemeral sandbox job",
                description="Short-lived isolated execution for build, test, import/export and one-off plugin work.",
                kind="job",
                languages=frozenset({"python", "javascript", "typescript", "shell"}),
                source_markers=("operly.plugin.json",),
                supports_preview=False,
                supports_deploy=False,
                default_network=NetworkPolicy(mode="off"),
                default_resources=common,
                allowed_dependency_managers=frozenset({"pip", "uv", "npm"}),
            ),
            RuntimeProfile(
                id="remote-http",
                display_name="Remote HTTP adapter",
                description="No hosted code; capabilities are implemented by a separately operated HTTPS service behind Operly policy.",
                kind="remote",
                languages=frozenset(),
                source_markers=(),
                supports_preview=False,
                supports_deploy=False,
                default_network=NetworkPolicy(mode="egress"),
                default_resources=ResourcePolicy(cpu_millicores=50, memory_mb=64, disk_mb=64, max_runtime_seconds=120, max_concurrency=10),
            ),
        )
    )

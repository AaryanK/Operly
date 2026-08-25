from .contracts import ProjectState, SoftwareProject, SourceVersion, StudioSession

__all__ = [
    "ProjectState",
    "SoftwareProject",
    "SourceVersion",
    "StudioSession",
    "SoftwareProjectService",
    "SoftwareSourceError",
    "SoftwareSourceService",
    "files_from_row",
    "source_json",
]


def __getattr__(name):
    """Keep lightweight source/runtime primitives importable without the DB stack."""
    if name == "SoftwareProjectService":
        from .service import SoftwareProjectService

        return SoftwareProjectService
    if name in {
        "SoftwareSourceError",
        "SoftwareSourceService",
        "files_from_row",
        "source_json",
    }:
        from . import source_service

        return getattr(source_service, name)
    raise AttributeError(name)

"""Workspace-owned Agent Computer runtime package.

Keep package initialization deliberately empty. Workspace tool composition imports
``native_tools`` directly, while the API imports ``router`` directly; eagerly
importing either here would couple those two composition paths and create an
initialization cycle.
"""

__all__: list[str] = []

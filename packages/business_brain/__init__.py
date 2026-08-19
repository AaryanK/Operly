__all__ = ["AgentInput", "AgentService", "get_agent_service"]


def __getattr__(name):
    if name in __all__:
        from packages.business_brain import agent

        return getattr(agent, name)
    raise AttributeError(name)

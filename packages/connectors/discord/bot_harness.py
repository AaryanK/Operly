"""Compatibility entrypoint for the canonical Discord channel adapter.

Legacy deployments may still launch this module. Keep that command working while
ensuring every Discord event uses ChannelService -> AgentService -> PluginAgentHarness.
"""

from packages.connectors.discord.bot_shared import main


if __name__ == "__main__":
    main()

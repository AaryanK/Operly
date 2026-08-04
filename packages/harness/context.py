from dataclasses import dataclass

import discord


@dataclass(slots=True)
class ToolContext:
    tenant_id: str
    message: discord.Message
    bot: discord.Client

    @property
    def guild_id(self) -> int | None:
        return self.message.guild.id if self.message.guild else None

    @property
    def channel_id(self) -> int:
        return self.message.channel.id

    @property
    def user_id(self) -> int:
        return self.message.author.id

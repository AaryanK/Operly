import discord

from packages.harness.context import ToolContext


def can_manage_channel(context: ToolContext) -> bool:
    if context.message.guild is None:
        return False

    member = context.message.author
    if not isinstance(member, discord.Member):
        return False

    permissions = context.message.channel.permissions_for(member)
    return permissions.manage_channels or permissions.administrator


def can_create_threads(context: ToolContext) -> bool:
    if context.message.guild is None:
        return False

    member = context.message.author
    if not isinstance(member, discord.Member):
        return False

    permissions = context.message.channel.permissions_for(member)
    return (
        permissions.create_public_threads
        or permissions.manage_threads
        or permissions.administrator
    )

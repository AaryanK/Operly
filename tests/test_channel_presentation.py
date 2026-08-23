from packages.business_brain.attachments.formatter import split_discord_text
from packages.channels.presentation import discord_markdown, format_for_channel


def test_web_keeps_canonical_markdown_table():
    source = "| Name | Email | Created |\n| --- | --- | --- |\n| Ada | ada@example.com | Aug 23 |"
    assert format_for_channel(source, "web") == source


def test_discord_rewrites_unsupported_markdown_table():
    source = (
        "You have **2 contacts**:\n\n"
        "| Name | Email | Created |\n"
        "| --- | --- | --- |\n"
        "| Ada Lovelace | ada@example.com | Aug 23 |\n"
        "| Grace Hopper | grace@example.com | Aug 24 |"
    )
    rendered = discord_markdown(source)

    assert "| --- |" not in rendered
    assert "**Ada Lovelace**" in rendered
    assert "• **Email:** ada@example.com" in rendered
    assert "• **Created:** Aug 23" in rendered
    assert "**Grace Hopper**" in rendered


def test_discord_preserves_tables_inside_code_fences():
    source = "```md\n| Name | Email |\n| --- | --- |\n| Ada | ada@example.com |\n```"
    assert discord_markdown(source) == source


def test_discord_chunking_applies_presentation_before_limits():
    source = "| Name | Email |\n| --- | --- |\n| Ada | ada@example.com |"
    chunks = split_discord_text(source, limit=1900)

    assert len(chunks) == 1
    assert "| --- |" not in chunks[0]
    assert "**Ada**" in chunks[0]
    assert "• **Email:** ada@example.com" in chunks[0]

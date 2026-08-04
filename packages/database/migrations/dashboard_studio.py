"""Compatibility wrapper for the revisioned Alembic migration command."""
async def migrate():
    import asyncio
    from alembic import command
    from packages.database.migrate import config,database_url,validate
    url=database_url()
    await asyncio.to_thread(command.upgrade,config(url),"head")
    await asyncio.to_thread(validate,url)

# OPERLY Harness Phase

Add these files into the OPERLY repository.

Run:

```powershell
uv pip install -r requirements.txt
uv run python -m packages.connectors.discord.bot_harness
```

Test:

```text
@OPERLY remind me in 2 minutes to drink water
@OPERLY DM me saying hello
@OPERLY create a task called Prepare quotation
@OPERLY list our open tasks
@OPERLY remember that refunds require manager approval
```

The reminder is stored in the database and restored after a process restart.

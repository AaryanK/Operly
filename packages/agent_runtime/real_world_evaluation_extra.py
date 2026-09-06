from __future__ import annotations

from packages.agent_runtime.real_world_evaluation import RawPromptCase, _c


# Scope-collision and lower-frequency operations that deserve explicit natural-language
# coverage in addition to the main corpus. Kept separate so the primary benchmark stays
# readable while the static coverage test can require every current built-in capability.
EXTRA_CURATED_CASES: tuple[RawPromptCase, ...] = (
    _c("comp.files.mkdir", "agent_computer", "workspace", "make a folder /workspace/reports in cs-1", kind="act", external=True, mutation=True, caps=("computer.files.mkdir",), styles=("technical", "casual")),
    _c("comp.files.remove", "agent_computer", "workspace", "delete /workspace/old.tmp from cs-1", kind="act", external=True, mutation=True, caps=("computer.files.remove",), styles=("technical", "imperative")),
    _c("comp.files.move", "agent_computer", "workspace", "rename /workspace/a.txt to final.txt on cs-1", kind="act", external=True, mutation=True, caps=("computer.files.move",), styles=("technical", "colloquial")),

    # Workspace Google lives in a much broader registry than Personal Google, so test
    # operations that could otherwise be confused with Workspace OS records/workflows.
    _c("wg.status", "workspace_google", "workspace", "do we actually have google hooked up in this workspace", kind="retrieve", external=True, caps=("google.connection.status",), styles=("casual", "workspace_scope")),
    _c("wg.read", "workspace_google", "workspace", "open company gmail message msg-44", kind="retrieve", external=True, caps=("google.gmail.read_message",), styles=("identifier", "workspace_scope")),
    _c("wg.draft", "workspace_google", "workspace", "draft an email from the company account to acme saying well follow up monday dont send", kind="act", external=True, mutation=True, caps=("google.gmail.create_draft",), styles=("negation", "workspace_scope")),
    _c("wg.labels", "workspace_google", "workspace", "archive company email msg-44", kind="act", external=True, mutation=True, caps=("google.gmail.modify_labels",), styles=("identifier", "workspace_scope")),
    _c("wg.calendars", "workspace_google", "workspace", "which google calendars can this workspace see", kind="retrieve", external=True, caps=("google.calendar.list_calendars",), styles=("question", "workspace_scope")),
    _c("wg.freebusy", "workspace_google", "workspace", "is the team free around 2 friday on google calendar", kind="retrieve", external=True, caps=("google.calendar.freebusy",), styles=("fuzzy_time", "workspace_scope")),
    _c("wg.update", "workspace_google", "workspace", "move google calendar event evt-9 to 4pm", kind="act", external=True, mutation=True, caps=("google.calendar.update_event",), styles=("identifier", "workspace_scope")),
    _c("wg.delete", "workspace_google", "workspace", "cancel google calendar event evt-9", kind="act", external=True, mutation=True, caps=("google.calendar.delete_event",), styles=("identifier", "workspace_scope")),

    # Run the Workflow semantics against Workspace authority as well; these are common
    # operator phrasings and catch ranking collisions with Workspace business run/list.
    _c("wwf.list", "workspace_workflow", "workspace", "what automations does this workspace have", kind="retrieve", external=True, caps=("workflow.list",), styles=("workspace_scope", "colloquial")),
    _c("wwf.create", "workspace_workflow", "workspace", "make a workspace workflow called overdue invoice followup", kind="act", external=True, mutation=True, caps=("workflow.create",), styles=("workspace_scope", "business")),
    _c("wwf.run", "workspace_workflow", "workspace", "run overdue invoice followup now", kind="act", external=True, mutation=True, caps=("workflow.run.start",), styles=("workspace_scope", "business")),
    _c("wwf.trace", "workspace_workflow", "workspace", "show trace for workspace run run-901", kind="retrieve", external=True, caps=("workflow.trace",), styles=("workspace_scope", "identifier")),

    # Human messiness / pragmatic intent traps.
    _c("messy.caps.gmail", "human_messiness", "personal", "FIND THE EMAIL FROM DAD ABOUT DELHI pls", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("mixed_case", "polite")),
    _c("messy.emoji.task", "human_messiness", "personal", "add todo: pay tuition 😭 tomorrow", kind="act", external=True, mutation=True, caps=("tasks.create",), styles=("emoji", "casual")),
    _c("messy.polite.cal", "human_messiness", "personal", "could you pls check whether i have anything around 11 tomorrow morning?", kind="retrieve", external=True, caps=("google.calendar.list_events", "google.calendar.freebusy"), styles=("polite", "implicit")),
    _c("messy.voice.invoice", "human_messiness", "workspace", "okay so acme paid that five hundred dollar invoice today by ach can you just mark that payment down", kind="act", external=True, mutation=True, caps=("workspace.finance.payment.record",), styles=("voice_dictation", "implicit")),
    _c("messy.negation.sale", "human_messiness", "workspace", "dont make an invoice just record the sale as paid card", kind="act", external=True, mutation=True, caps=("workspace.sales.complete",), styles=("negation", "business")),
    _c("messy.context.discord", "human_messiness", "workspace", "yea send that there", kind="act", external=True, mutation=True, caps=("discord.message.send",), context=("assistant: The selected Discord channel is 123456789 and the drafted text is Deployment is live.",), styles=("contextual", "pronoun", "fragment")),
)


__all__ = ["EXTRA_CURATED_CASES"]

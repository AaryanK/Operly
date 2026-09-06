from __future__ import annotations

import asyncio
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence

from packages.agent_runtime.context import ContextItem, ContextKind
from packages.agent_runtime.inference import OpenAICompatibleAgentModel
from packages.agent_runtime.objective import ObjectiveInterpretationError, ObjectiveInterpreter
from packages.agent_runtime.runtime import AgentRuntimeSettings
from packages.personal_modules.runtime import build_personal_runtime
from packages.security.execution_context import (
    ExecutionContext,
    PERSONAL_EXECUTION_PERMISSIONS,
    ScopeKind,
)
from packages.security.surfaces import SurfaceKind
from packages.workspace_modules.tools.runtime import build_workspace_runtime


@dataclass(frozen=True, slots=True)
class RawPromptCase:
    case_id: str
    family: str
    scope: str
    prompt: str
    kind: str
    external: bool
    mutation: bool = False
    wait: bool = False
    dispatch: str = "direct_capability"
    expected_any: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    styles: tuple[str, ...] = ("conversational",)


def _c(
    case_id: str,
    family: str,
    scope: str,
    prompt: str,
    *,
    kind: str,
    external: bool,
    mutation: bool = False,
    wait: bool = False,
    dispatch: str = "direct_capability",
    caps: Sequence[str] = (),
    context: Sequence[str] = (),
    styles: Sequence[str] = ("conversational",),
) -> RawPromptCase:
    return RawPromptCase(
        case_id=case_id,
        family=family,
        scope=scope,
        prompt=prompt,
        kind=kind,
        external=external,
        mutation=mutation,
        wait=wait,
        dispatch=dispatch,
        expected_any=tuple(caps),
        context=tuple(context),
        styles=tuple(styles),
    )


# These prompts are deliberately not polished benchmark prose. They contain fragments,
# shorthand, typos, negation, vague references, voice-dictation style, business jargon,
# mixed casing and the kind of ellipsis people use in chat. Ground truth is authored by
# hand; the model never generates its own test questions.
CURATED_CASES: tuple[RawPromptCase, ...] = (
    # No-tool false-positive traps: nouns that resemble tools must not force external state.
    _c("nt.email.header", "no_tool", "personal", "wait what even is an email header lol", kind="respond", external=False, dispatch="respond", styles=("casual", "slang")),
    _c("nt.calendar.leap", "no_tool", "personal", "why tf do calendars need leap years", kind="respond", external=False, dispatch="respond", styles=("casual", "slang")),
    _c("nt.workflow.explain", "no_tool", "personal", "explain a workflow like im 10", kind="respond", external=False, dispatch="respond", styles=("casual",)),
    _c("nt.invoice.diff", "no_tool", "workspace", "invoice vs receipt whats actually the diff", kind="respond", external=False, dispatch="respond", styles=("fragment", "casual")),
    _c("nt.customer.copy", "no_tool", "workspace", "write a nice apology msg for a pissed customer. generic one", kind="respond", external=False, dispatch="respond", styles=("slang", "writing")),
    _c("nt.file.concept", "no_tool", "workspace", "whats the point of a json file vs yaml", kind="respond", external=False, dispatch="respond", styles=("technical",)),
    _c("nt.task.plan", "no_tool", "personal", "gimme a study task plan for the weekend dont add anything anywhere", kind="respond", external=False, dispatch="respond", styles=("negation", "casual")),
    _c("nt.email.template", "no_tool", "personal", "can u write me an email asking prof for 2 more days just the text", kind="respond", external=False, dispatch="respond", styles=("shorthand", "writing")),
    _c("nt.business.margin", "no_tool", "workspace", "gross margin 42% good or bad for a small shop?", kind="respond", external=False, dispatch="respond", styles=("business_shorthand",)),
    _c("nt.pricing", "no_tool", "workspace", "coffee shop pricing ideas? keep it simple", kind="respond", external=False, dispatch="respond", styles=("fragment",)),
    _c("nt.code", "no_tool", "workspace", "python fn to dedupe a list pls", kind="respond", external=False, dispatch="respond", styles=("technical", "shorthand")),
    _c("nt.summary.context", "no_tool", "personal", "yea ok sum that in 1 line", kind="respond", external=False, dispatch="respond", context=("assistant: Capability discovery is separate from authorization and execution.",), styles=("contextual", "fragment")),

    # Personal system/tasks.
    _c("personal.runtime.status", "personal_system", "personal", "is operlys runtime actually up rn", kind="retrieve", external=True, caps=("system.runtime.status",), styles=("casual", "shorthand")),
    _c("tasks.list.raw", "personal_tasks", "personal", "what do i still gotta do", kind="retrieve", external=True, caps=("tasks.list",), styles=("casual", "implicit")),
    _c("tasks.list.fragment", "personal_tasks", "personal", "my todos", kind="retrieve", external=True, caps=("tasks.list",), styles=("fragment",)),
    _c("tasks.create.typo", "personal_tasks", "personal", "add a todo submit the report tmrw", kind="act", external=True, mutation=True, caps=("tasks.create",), styles=("shorthand", "typo")),
    _c("tasks.update.context", "personal_tasks", "personal", "mark that one done", kind="act", external=True, mutation=True, caps=("tasks.update_status",), context=("assistant: The selected task has task_id task-42 and is titled Submit report.",), styles=("contextual", "pronoun")),

    # Personal Gmail.
    _c("pgmail.status", "personal_gmail", "personal", "is my google even connected properly", kind="retrieve", external=True, caps=("google.connection.status",), styles=("casual",)),
    _c("pgmail.search.dad", "personal_gmail", "personal", "yo find dads emails abt my flight", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("slang", "shorthand")),
    _c("pgmail.search.receipt", "personal_gmail", "personal", "look thru inbox for that tuition receipt thing", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("casual", "implicit")),
    _c("pgmail.search.fragment", "personal_gmail", "personal", "prof muether workshop email", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("fragment",)),
    _c("pgmail.read.id", "personal_gmail", "personal", "open gmail msg 18d3abc", kind="retrieve", external=True, caps=("google.gmail.read_message",), styles=("shorthand", "identifier")),
    _c("pgmail.draft.negation", "personal_gmail", "personal", "draft dad a msg saying ill call tonight BUT dont send it", kind="act", external=True, mutation=True, caps=("google.gmail.create_draft",), styles=("negation", "emphasis")),
    _c("pgmail.send.typo", "personal_gmail", "personal", "send dad an emial saying im back in wichita", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), styles=("typo", "casual")),
    _c("pgmail.labels", "personal_gmail", "personal", "archive that email for me", kind="act", external=True, mutation=True, caps=("google.gmail.modify_labels",), context=("assistant: The selected Gmail message id is msg-88.",), styles=("contextual", "pronoun")),
    _c("pgmail.search_read", "compound", "personal", "find the latest visa email and tell me exactly what it says", kind="retrieve", external=True, dispatch="agent_loop", caps=("google.gmail.search",), styles=("compound", "casual")),
    _c("pgmail.reply.latest", "compound", "personal", "reply yep sounds good to dads latest email", kind="composite", external=True, mutation=True, dispatch="agent_loop", caps=("google.gmail.search",), styles=("compound", "implicit")),

    # Personal Calendar.
    _c("pcal.list.typo", "personal_calendar", "personal", "wht meetings i got tmrw", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("typo", "shorthand")),
    _c("pcal.list.fragment", "personal_calendar", "personal", "tomorrow morning calendar", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("fragment",)),
    _c("pcal.calendars", "personal_calendar", "personal", "what calendars do i even have connected", kind="retrieve", external=True, caps=("google.calendar.list_calendars",), styles=("casual",)),
    _c("pcal.freebusy", "personal_calendar", "personal", "am i free friday like 3ish", kind="retrieve", external=True, caps=("google.calendar.freebusy",), styles=("casual", "fuzzy_time")),
    _c("pcal.create", "personal_calendar", "personal", "put dentist friday 2-3 on my calendar", kind="act", external=True, mutation=True, caps=("google.calendar.create_event",), styles=("shorthand",)),
    _c("pcal.update.id", "personal_calendar", "personal", "change event evt-22 title to qualifier prep", kind="act", external=True, mutation=True, caps=("google.calendar.update_event",), styles=("identifier", "imperative")),
    _c("pcal.delete.id", "personal_calendar", "personal", "delete calendar event evt-22", kind="act", external=True, mutation=True, caps=("google.calendar.delete_event",), styles=("identifier", "imperative")),
    _c("pcal.move.ambiguous", "compound", "personal", "move whatever meeting i have at 3 tomorrow to friday", kind="composite", external=True, mutation=True, dispatch="agent_loop", caps=("google.calendar.list_events",), styles=("compound", "ambiguous_target")),
    _c("pcal.email.times", "compound", "personal", "check when im free friday and email dad the open times", kind="composite", external=True, mutation=True, dispatch="agent_loop", styles=("compound", "cross_tool")),

    # Personal durable workflow family.
    _c("wf.list", "workflow", "personal", "show me my automations", kind="retrieve", external=True, caps=("workflow.list",), styles=("colloquial",)),
    _c("wf.get", "workflow", "personal", "open workflow wf-12 i wanna see what it does", kind="retrieve", external=True, caps=("workflow.get",), styles=("identifier", "casual")),
    _c("wf.versions", "workflow", "personal", "show me older versions of wf-12", kind="retrieve", external=True, caps=("workflow.version.list",), styles=("identifier",)),
    _c("wf.version.get", "workflow", "personal", "pull version 3 of wf-12", kind="retrieve", external=True, caps=("workflow.version.get",), styles=("fragment", "identifier")),
    _c("wf.create", "workflow", "personal", "make me a workflow called morning brief", kind="act", external=True, mutation=True, caps=("workflow.create",), styles=("casual",)),
    _c("wf.update", "workflow", "personal", "change wf-12 to run weekdays at 8", kind="act", external=True, mutation=True, caps=("workflow.update",), styles=("identifier", "schedule")),
    _c("wf.enable", "workflow", "personal", "turn wf-12 back on", kind="act", external=True, mutation=True, caps=("workflow.enable",), styles=("colloquial", "identifier")),
    _c("wf.disable", "workflow", "personal", "shut that workflow off for now", kind="act", external=True, mutation=True, caps=("workflow.disable",), context=("assistant: The selected workflow id is wf-12.",), styles=("contextual", "colloquial")),
    _c("wf.archive", "workflow", "personal", "archive wf-12 i dont use it anymore", kind="act", external=True, mutation=True, caps=("workflow.archive",), styles=("identifier", "casual")),
    _c("wf.trigger.list", "workflow", "personal", "what triggers wf-12", kind="retrieve", external=True, caps=("workflow.trigger.list",), styles=("fragment", "identifier")),
    _c("wf.trigger.create", "workflow", "personal", "make wf-12 kick off when a gmail msg gets sent", kind="act", external=True, mutation=True, caps=("workflow.trigger.create",), styles=("colloquial", "identifier")),
    _c("wf.trigger.delete", "workflow", "personal", "remove trigger trig-7", kind="act", external=True, mutation=True, caps=("workflow.trigger.delete",), styles=("fragment", "identifier")),
    _c("wf.run.start", "workflow", "personal", "run morning brief rn", kind="act", external=True, mutation=True, caps=("workflow.run.start",), styles=("shorthand",)),
    _c("wf.run.list", "workflow", "personal", "any recent runs for morning brief?", kind="retrieve", external=True, caps=("workflow.run.list",), styles=("question", "casual")),
    _c("wf.run.get", "workflow", "personal", "why did run run-77 fail show me it", kind="retrieve", external=True, caps=("workflow.run.get",), styles=("identifier", "casual")),
    _c("wf.run.cancel", "workflow", "personal", "stop run-77", kind="act", external=True, mutation=True, caps=("workflow.run.cancel",), styles=("fragment", "identifier")),
    _c("wf.run.retry", "workflow", "personal", "retry that failed run", kind="act", external=True, mutation=True, caps=("workflow.run.retry",), context=("assistant: The failed workflow run id is run-77.",), styles=("contextual", "pronoun")),
    _c("wf.trace", "workflow", "personal", "gimme the trace for run-77", kind="retrieve", external=True, caps=("workflow.trace",), styles=("shorthand", "identifier")),
    _c("wf.schedule.preview", "workflow", "personal", "if this ran every mon wed fri at 8 what are next 5 times", kind="retrieve", external=True, caps=("workflow.schedule.preview",), styles=("schedule", "question")),
    _c("wf.runtime.status", "workflow", "personal", "is the workflow scheduler healthy rn", kind="retrieve", external=True, caps=("workflow.runtime.status",), styles=("technical", "shorthand")),

    # Wait/future objectives: classifier should identify future dependency instead of pretending completion now.
    _c("wait.email", "wait", "personal", "lmk when dad replies", kind="wait", external=True, wait=True, dispatch="wait", styles=("shorthand", "future")),
    _c("wait.workflow", "wait", "personal", "ping me once that run finishes", kind="wait", external=True, wait=True, dispatch="wait", styles=("contextual", "future")),
    _c("wait.workspace", "wait", "workspace", "tell me when invoice INV-1042 gets paid", kind="wait", external=True, wait=True, dispatch="wait", styles=("business_shorthand", "future")),

    # Workspace system/control.
    _c("ws.describe", "workspace_system", "workspace", "what workspace am i in rn and whats the timezone", kind="retrieve", external=True, caps=("workspace.describe",), styles=("casual",)),
    _c("ws.modules.list", "workspace_system", "workspace", "what modules are turned on here", kind="retrieve", external=True, caps=("workspace.modules.list",), styles=("colloquial",)),
    _c("ws.summary", "workspace_controls", "workspace", "give me quick numbers for this business", kind="retrieve", external=True, caps=("workspace.summary.read",), styles=("implicit", "business")),
    _c("ws.activity", "workspace_controls", "workspace", "what changed in here lately", kind="retrieve", external=True, caps=("workspace.activity.list",), styles=("implicit", "casual")),
    _c("ws.settings", "workspace_controls", "workspace", "change this workspace timezone to America/Chicago", kind="act", external=True, mutation=True, caps=("workspace.settings.update",), styles=("imperative",)),
    _c("ws.module.set", "workspace_controls", "workspace", "turn on support module", kind="act", external=True, mutation=True, caps=("workspace.modules.set",), styles=("colloquial",)),
    _c("ws.presets.list", "workspace_controls", "workspace", "what workspace packs do we have", kind="retrieve", external=True, caps=("workspace.presets.list",), styles=("colloquial",)),
    _c("ws.preset.apply", "workspace_controls", "workspace", "set this up like an ecommerce business", kind="act", external=True, mutation=True, caps=("workspace.presets.apply",), styles=("implicit", "business")),
    _c("ws.members.list", "workspace_access", "workspace", "whos in this workspace", kind="retrieve", external=True, caps=("workspace.members.list",), styles=("casual",)),
    _c("ws.members.add", "workspace_access", "workspace", "add sam@example.com as an employee", kind="act", external=True, mutation=True, caps=("workspace.members.add",), styles=("imperative",)),
    _c("ws.members.role", "workspace_access", "workspace", "make user u-44 a manager", kind="act", external=True, mutation=True, caps=("workspace.members.role.update",), styles=("identifier", "colloquial")),
    _c("ws.members.remove", "workspace_access", "workspace", "remove u-44 from the workspace", kind="act", external=True, mutation=True, caps=("workspace.members.remove",), styles=("identifier",)),
    _c("ws.roles.list", "workspace_access", "workspace", "what roles can people have here", kind="retrieve", external=True, caps=("workspace.roles.list",), styles=("question",)),
    _c("ws.roles.permissions", "workspace_access", "workspace", "give the analyst role finance read and crm read only", kind="act", external=True, mutation=True, caps=("workspace.roles.permissions.set",), styles=("access_control", "business_shorthand")),
    _c("ws.invites.list", "workspace_access", "workspace", "any pending invites?", kind="retrieve", external=True, caps=("workspace.invitations.list",), styles=("fragment",)),
    _c("ws.invites.create", "workspace_access", "workspace", "invite jane@example.com as employee", kind="act", external=True, mutation=True, caps=("workspace.invitations.create",), styles=("imperative",)),
    _c("ws.invites.revoke", "workspace_access", "workspace", "kill invite inv-9", kind="act", external=True, mutation=True, caps=("workspace.invitations.revoke",), styles=("slang", "identifier")),
    _c("ws.inventory.moves", "workspace_inventory", "workspace", "show stock moves for item item-22", kind="retrieve", external=True, caps=("workspace.inventory.movements.list",), styles=("business_shorthand", "identifier")),
    _c("ws.inventory.adjust", "workspace_inventory", "workspace", "we counted 4 extra of item-22 add em to stock reason physical count", kind="act", external=True, mutation=True, caps=("workspace.inventory.adjust",), styles=("voice_dictation", "business")),

    # Workspace business shortcuts.
    _c("biz.search.customer", "workspace_business", "workspace", "find acme for me", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.crm.contacts.list", "workspace_os.crm.organizations.list"), styles=("fragment", "implicit")),
    _c("biz.search.invoice", "workspace_business", "workspace", "where is inv 1042", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.finance.invoices.list"), styles=("business_shorthand", "fragment")),
    _c("biz.attention", "workspace_business", "workspace", "ok whats on fire today", kind="retrieve", external=True, dispatch="agent_loop", caps=("workspace.attention.list",), styles=("idiom", "casual")),
    _c("biz.customer.snapshot", "workspace_business", "workspace", "give me everything we know about this customer", kind="retrieve", external=True, caps=("workspace.customer.snapshot",), context=("assistant: The selected customer contact_id is contact-123.",), styles=("contextual", "implicit")),
    _c("biz.sale", "workspace_business", "workspace", "ring up 2 of item-22 at normal price for customer c-9", kind="act", external=True, mutation=True, caps=("workspace.sales.complete",), styles=("retail_slang", "identifier")),
    _c("biz.invoice", "workspace_business", "workspace", "invoice acme 500 bucks for design work due in 14 days", kind="act", external=True, mutation=True, caps=("workspace.finance.invoice.create_simple",), styles=("business_shorthand", "casual")),
    _c("biz.payment", "workspace_business", "workspace", "inv-1042 got paid 500 by ach record it", kind="act", external=True, mutation=True, caps=("workspace.finance.payment.record",), styles=("business_shorthand", "voice_dictation")),

    # Studio.
    _c("studio.projects", "studio", "workspace", "show me the apps/sites we got in studio", kind="retrieve", external=True, caps=("studio.projects.list",), styles=("colloquial",)),
    _c("studio.inspect", "studio", "workspace", "whats going on with project proj-7 is it deployable", kind="retrieve", external=True, caps=("studio.project.inspect",), styles=("identifier", "casual")),
    _c("studio.status", "studio", "workspace", "is solution sol-4 live rn", kind="retrieve", external=True, caps=("studio.solution.status",), styles=("identifier", "shorthand")),
    _c("studio.deploy", "studio", "workspace", "ship project proj-7", kind="act", external=True, mutation=True, caps=("studio.solution.deploy",), styles=("developer_slang", "identifier")),
    _c("studio.rollback", "studio", "workspace", "prod broke roll sol-4 back to the last healthy deploy", kind="act", external=True, mutation=True, caps=("studio.solution.rollback",), styles=("developer_slang", "incident")),
    _c("studio.domain", "studio", "workspace", "hook app.example.com up to sol-4", kind="act", external=True, mutation=True, caps=("studio.solution.domain.request",), styles=("developer_slang", "identifier")),

    # Agent Computer: representative lifecycle, shell/code/files/process/git/web/browser operations.
    _c("comp.start", "agent_computer", "workspace", "spin up the coding computer for session cs-1", kind="act", external=True, mutation=True, caps=("computer.runtime.start",), styles=("developer_slang", "identifier")),
    _c("comp.status", "agent_computer", "workspace", "is cs-1 computer alive", kind="retrieve", external=True, caps=("computer.runtime.status",), styles=("developer_slang", "identifier")),
    _c("comp.stop", "agent_computer", "workspace", "kill the sandbox for cs-1", kind="act", external=True, mutation=True, caps=("computer.runtime.stop",), styles=("developer_slang", "identifier")),
    _c("comp.shell", "agent_computer", "workspace", "in cs-1 run pytest -q", kind="act", external=True, mutation=True, caps=("computer.terminal.exec",), styles=("technical", "identifier")),
    _c("comp.python", "agent_computer", "workspace", "use python in cs-1 to print the csv row count", kind="act", external=True, mutation=True, caps=("computer.python.exec",), styles=("technical",)),
    _c("comp.files.list", "agent_computer", "workspace", "whats in /workspace on cs-1", kind="retrieve", external=True, caps=("computer.files.list",), styles=("technical", "fragment")),
    _c("comp.files.read", "agent_computer", "workspace", "open /workspace/README.md from cs-1", kind="retrieve", external=True, caps=("computer.files.read",), styles=("technical",)),
    _c("comp.files.write", "agent_computer", "workspace", "write hello to /workspace/note.txt in cs-1", kind="act", external=True, mutation=True, caps=("computer.files.write",), styles=("technical",)),
    _c("comp.files.search", "agent_computer", "workspace", "find TODO anywhere in cs-1 files", kind="retrieve", external=True, caps=("computer.files.search",), styles=("technical",)),
    _c("comp.process.list", "agent_computer", "workspace", "what background stuff is running in cs-1", kind="retrieve", external=True, caps=("computer.process.list",), styles=("casual", "technical")),
    _c("comp.process.kill", "agent_computer", "workspace", "stop process proc-9 in cs-1", kind="act", external=True, mutation=True, caps=("computer.process.kill",), styles=("identifier", "technical")),
    _c("comp.git.status", "agent_computer", "workspace", "git status in cs-1", kind="retrieve", external=True, caps=("computer.git.status",), styles=("fragment", "technical")),
    _c("comp.git.diff", "agent_computer", "workspace", "show me the diff in cs-1", kind="retrieve", external=True, caps=("computer.git.diff",), styles=("technical", "casual")),
    _c("comp.git.exec", "agent_computer", "workspace", "git add README.md in cs-1", kind="act", external=True, mutation=True, caps=("computer.git.exec",), styles=("technical",)),
    _c("comp.web.fetch", "agent_computer", "workspace", "fetch https://example.com from cs-1 just read it", kind="retrieve", external=True, caps=("computer.web.fetch",), styles=("technical", "negation")),
    _c("comp.web.download", "agent_computer", "workspace", "download https://example.com/a.csv into cs-1 /workspace/a.csv", kind="act", external=True, mutation=True, caps=("computer.web.download",), styles=("technical",)),
    _c("comp.browser.open", "agent_computer", "workspace", "open a browser in cs-1", kind="act", external=True, mutation=True, caps=("computer.browser.open",), styles=("technical",)),
    _c("comp.browser.nav", "agent_computer", "workspace", "go to https://example.com in cs-1 browser", kind="act", external=True, mutation=True, caps=("computer.browser.navigate",), styles=("technical",)),
    _c("comp.browser.snapshot", "agent_computer", "workspace", "what does the current cs-1 page say", kind="retrieve", external=True, caps=("computer.browser.snapshot",), styles=("implicit", "technical")),
    _c("comp.browser.click", "agent_computer", "workspace", "click the Sign in button in cs-1", kind="act", external=True, mutation=True, caps=("computer.browser.click",), styles=("technical",)),
    _c("comp.browser.type", "agent_computer", "workspace", "type aaryan in the username box on cs-1", kind="act", external=True, mutation=True, caps=("computer.browser.type",), styles=("technical",)),
    _c("comp.browser.press", "agent_computer", "workspace", "hit enter in cs-1 browser", kind="act", external=True, mutation=True, caps=("computer.browser.press",), styles=("colloquial", "technical")),
    _c("comp.browser.eval", "agent_computer", "workspace", "run document.title in cs-1 page js", kind="act", external=True, mutation=True, caps=("computer.browser.evaluate",), styles=("technical", "shorthand")),
    _c("comp.browser.shot", "agent_computer", "workspace", "grab a screenshot of cs-1 page", kind="retrieve", external=True, caps=("computer.browser.screenshot",), styles=("colloquial", "technical")),
    _c("comp.browser.close", "agent_computer", "workspace", "close the browser in cs-1 but keep computer running", kind="act", external=True, mutation=True, caps=("computer.browser.close",), styles=("negation", "technical")),

    # Workspace Google: same semantic IDs, different authority scope.
    _c("wgmail.search", "workspace_google", "workspace", "check the company inbox for acmes last email", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("business", "casual")),
    _c("wgmail.send", "workspace_google", "workspace", "email acme from the workspace saying invoice attached", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), styles=("business",)),
    _c("wcal.list", "workspace_google", "workspace", "whats on the team google calendar tomorrow", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("business", "casual")),
    _c("wcal.create", "workspace_google", "workspace", "add client kickoff friday 10-11 to the workspace calendar", kind="act", external=True, mutation=True, caps=("google.calendar.create_event",), styles=("business",)),

    # Canva core + authoring.
    _c("canva.status", "canva", "workspace", "is canva connected here or nah", kind="retrieve", external=True, caps=("canva.connection.status",), styles=("slang",)),
    _c("canva.profile", "canva", "workspace", "which canva account is this using", kind="retrieve", external=True, caps=("canva.profile.read",), styles=("question",)),
    _c("canva.designs", "canva", "workspace", "find our summer sale designs in canva", kind="retrieve", external=True, caps=("canva.designs.list",), styles=("business",)),
    _c("canva.get", "canva", "workspace", "open canva design D-77 details", kind="retrieve", external=True, caps=("canva.design.get",), styles=("identifier",)),
    _c("canva.create", "canva", "workspace", "make a blank presentation in canva called q4 review", kind="act", external=True, mutation=True, caps=("canva.design.create",), styles=("business",)),
    _c("canva.formats", "canva", "workspace", "what can D-77 export as", kind="retrieve", external=True, caps=("canva.design.export_formats",), styles=("identifier", "fragment")),
    _c("canva.export", "canva", "workspace", "export D-77 as pdf", kind="act", external=True, mutation=True, caps=("canva.design.export.create",), styles=("identifier", "imperative")),
    _c("canva.export.get", "canva", "workspace", "is canva export exp-9 done yet", kind="retrieve", external=True, caps=("canva.design.export.get",), styles=("identifier", "casual")),
    _c("canva.folder", "canva", "workspace", "show stuff inside canva folder fld-3", kind="retrieve", external=True, caps=("canva.folder.items.list",), styles=("identifier", "casual")),
    _c("canva.dataset", "canva_authoring", "workspace", "what autofill fields does design D-77 have", kind="retrieve", external=True, caps=("canva.design.dataset",), styles=("technical", "identifier")),
    _c("canva.templates", "canva_authoring", "workspace", "find me an autofill brand template for promos", kind="retrieve", external=True, caps=("canva.brand_templates.list",), styles=("business",)),
    _c("canva.template.get", "canva_authoring", "workspace", "open brand template BT-4", kind="retrieve", external=True, caps=("canva.brand_template.get",), styles=("identifier", "fragment")),
    _c("canva.template.dataset", "canva_authoring", "workspace", "what fields can i fill in BT-4", kind="retrieve", external=True, caps=("canva.brand_template.dataset",), styles=("identifier", "casual")),
    _c("canva.autofill", "canva_authoring", "workspace", "use BT-4 and fill headline with FALL SALE make a new design", kind="act", external=True, mutation=True, caps=("canva.autofill.create",), styles=("voice_dictation", "business")),
    _c("canva.autofill.get", "canva_authoring", "workspace", "did autofill job job-8 finish", kind="retrieve", external=True, caps=("canva.autofill.get",), styles=("identifier", "casual")),

    # Discord.
    _c("discord.status", "discord", "workspace", "is the discord bot online", kind="retrieve", external=True, caps=("discord.bot.status",), styles=("casual",)),
    _c("discord.installs", "discord", "workspace", "which discord servers are hooked to this workspace", kind="retrieve", external=True, caps=("discord.installations.list",), styles=("colloquial",)),
    _c("discord.channels", "discord", "workspace", "show me channels operly can see in discord", kind="retrieve", external=True, caps=("discord.channels.list",), styles=("casual",)),
    _c("discord.messages", "discord", "workspace", "read last 20 msgs in channel 123456789", kind="retrieve", external=True, caps=("discord.messages.list",), styles=("shorthand", "identifier")),
    _c("discord.send", "discord", "workspace", "drop 'deploy is live' in discord channel 123456789", kind="act", external=True, mutation=True, caps=("discord.message.send",), styles=("slang", "identifier")),
    _c("discord.react", "discord", "workspace", "put a 👍 on msg 555 in channel 123", kind="act", external=True, mutation=True, caps=("discord.reaction.add",), styles=("emoji", "identifier")),
    _c("discord.thread", "discord", "workspace", "make a thread off msg 555 called bug triage", kind="act", external=True, mutation=True, caps=("discord.thread.create",), styles=("colloquial", "identifier")),

    # Multilingual / code-switching / voice-like stress cases.
    _c("multi.ne.gmail", "multilingual", "personal", "बुवाले flight को बारेमा पठाएको email खोज न", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("nepali", "code_switch")),
    _c("multi.ne.cal", "multilingual", "personal", "भोलि calendar मा के छ मेरो", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("nepali", "code_switch")),
    _c("multi.hi.send", "multilingual", "personal", "papa ko mail bhej do ki ghar pahunch gaya", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), styles=("hinglish", "code_switch")),
    _c("multi.es.search", "multilingual", "workspace", "busca Acme en este workspace porfa", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.crm.contacts.list", "workspace_os.crm.organizations.list"), styles=("spanish", "code_switch")),
    _c("multi.fr.free", "multilingual", "personal", "check si je suis free vendredi vers 3pm", kind="retrieve", external=True, caps=("google.calendar.freebusy",), styles=("french", "code_switch")),
    _c("multi.de.workflow", "multilingual", "personal", "mach den workflow wf-12 aus bitte", kind="act", external=True, mutation=True, caps=("workflow.disable",), styles=("german", "code_switch")),
    _c("multi.ja.gmail", "multilingual", "personal", "父がflightについて送ったメール探して", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("japanese", "code_switch")),
    _c("multi.zh.cal", "multilingual", "personal", "看看我周五3点左右有没有空", kind="retrieve", external=True, caps=("google.calendar.freebusy",), styles=("chinese",)),
    _c("multi.ar.gmail", "multilingual", "personal", "دورلي على ايميل ابوي عن الرحلة", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("arabic", "colloquial")),
    _c("voice.no.punct", "multilingual", "workspace", "hey can you find northstar supplier and then show me what we owe them i think there was something last week", kind="retrieve", external=True, dispatch="agent_loop", caps=("workspace.search", "workspace_os.suppliers.suppliers.list"), styles=("voice_dictation", "compound")),
)


def _workspace_os_cases() -> tuple[RawPromptCase, ...]:
    """Generate one human-shaped prompt for every current Workspace OS record contract.

    The catalog is large and changes frequently. Deriving these cases from the canonical
    registry prevents the benchmark from silently missing a newly-added record family.
    These are intentionally simple raw prompts; high-noise language is exercised by the
    curated cases above.
    """

    cases: list[RawPromptCase] = []
    for spec in build_workspace_runtime().registry.all():
        if not spec.id.startswith("workspace_os."):
            continue
        action = spec.id.rsplit(".", 1)[-1]
        label = spec.display_name.strip()
        resource = label
        for prefix in ("List ", "Create ", "Update ", "Delete "):
            if resource.lower().startswith(prefix.lower()):
                resource = resource[len(prefix) :]
                break
        slug = spec.id.replace(".", "_")
        if action == "list":
            prompt = f"pull up our {resource.lower()} i wanna see whats in there"
            kind, mutation = "retrieve", False
            style = ("generated_raw", "casual", "record_read")
        elif action == "create":
            prompt = f"add a new {resource.lower()} record for me"
            kind, mutation = "act", True
            style = ("generated_raw", "record_create")
        elif action == "update":
            prompt = f"i need to change one of the {resource.lower()} records"
            kind, mutation = "act", True
            style = ("generated_raw", "record_update")
        elif action == "delete":
            prompt = f"get rid of one of the {resource.lower()} records"
            kind, mutation = "act", True
            style = ("generated_raw", "record_delete")
        else:
            continue
        cases.append(
            _c(
                f"workspace_os.{slug}",
                "workspace_os_records",
                "workspace",
                prompt,
                kind=kind,
                external=True,
                mutation=mutation,
                caps=(spec.id,),
                styles=style,
            )
        )
    return tuple(cases)


def all_cases(*, include_workspace_os: bool = True) -> tuple[RawPromptCase, ...]:
    return CURATED_CASES + (_workspace_os_cases() if include_workspace_os else ())


def _context(scope: str) -> ExecutionContext:
    if scope == "workspace":
        return ExecutionContext(
            workspace_id="real-world-eval-workspace",
            user_id="real-world-eval-user",
            membership_id="real-world-eval-membership",
            role="owner",
            permissions=frozenset(),
            channel="real_world_eval",
            surface=SurfaceKind.WORKSPACE_PRIVATE,
            conversation_id="real-world-eval-workspace-conversation",
            scope_kind=ScopeKind.WORKSPACE,
            focus_workspace_id="real-world-eval-workspace",
            principal_id="user:real-world-eval-user",
            workspace_mode="full",
        )
    return ExecutionContext(
        workspace_id=None,
        user_id="real-world-eval-user",
        membership_id=None,
        role="personal_owner",
        permissions=PERSONAL_EXECUTION_PERMISSIONS,
        channel="real_world_eval",
        surface=SurfaceKind.PERSONAL_PRIVATE,
        conversation_id="real-world-eval-personal-conversation",
        scope_kind=ScopeKind.PERSONAL,
        principal_id="user:real-world-eval-user",
        workspace_mode="personal",
    )


def _registry(scope: str):
    return build_workspace_runtime().registry if scope == "workspace" else build_personal_runtime().registry


def _context_items(case: RawPromptCase) -> tuple[ContextItem, ...]:
    return tuple(
        ContextItem(
            key=f"real-world:{case.case_id}:{index}",
            kind=ContextKind.CONVERSATION,
            text=text,
            relevance=1.0,
            priority=50,
        )
        for index, text in enumerate(case.context, 1)
    )


def corpus_inventory() -> dict[str, Any]:
    cases = all_cases()
    family_counts: dict[str, int] = defaultdict(int)
    style_counts: dict[str, int] = defaultdict(int)
    scope_counts: dict[str, int] = defaultdict(int)
    for case in cases:
        family_counts[case.family] += 1
        scope_counts[case.scope] += 1
        for style in case.styles:
            style_counts[style] += 1
    return {
        "total": len(cases),
        "curated": len(CURATED_CASES),
        "workspace_os_generated": len(cases) - len(CURATED_CASES),
        "families": dict(sorted(family_counts.items())),
        "styles": dict(sorted(style_counts.items())),
        "scopes": dict(sorted(scope_counts.items())),
    }


async def _interpret_with_retry(
    interpreter: ObjectiveInterpreter,
    case: RawPromptCase,
    *,
    max_attempts: int,
    retry_delay_seconds: float,
):
    context = _context(case.scope)
    last_error: Exception | None = None
    for attempt in range(1, max_attempts + 1):
        try:
            return await interpreter.interpret(
                message=case.prompt,
                context=context,
                context_items=_context_items(case),
            )
        except ObjectiveInterpretationError as error:
            last_error = error
            if error.code != "objective_model_failed" or attempt >= max_attempts:
                raise
            await asyncio.sleep(retry_delay_seconds * attempt)
    raise last_error or RuntimeError("objective evaluation failed")


async def run_real_world_eval(
    *,
    cases: Sequence[RawPromptCase] | None = None,
    include_workspace_os: bool = False,
    interval_seconds: float = 0.75,
    max_attempts: int = 3,
    retry_delay_seconds: float = 3.0,
) -> dict[str, Any]:
    """Run raw prompts through the configured production inference route without tools.

    This exercises raw prompt -> model ObjectiveIR -> scoped capability retrieval. It
    never executes a capability and therefore never reads or mutates connected user or
    workspace provider data.
    """

    selected = tuple(cases or all_cases(include_workspace_os=include_workspace_os))
    interpreter = ObjectiveInterpreter(
        model=OpenAICompatibleAgentModel(),
        settings=AgentRuntimeSettings(enabled=True),
    )
    results: list[dict[str, Any]] = []
    by_family: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    by_style: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    by_scope: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "passed": 0})
    ranks: list[int] = []
    model_errors = 0

    for ordinal, case in enumerate(selected, 1):
        context = _context(case.scope)
        row: dict[str, Any] = {
            "case_id": case.case_id,
            "family": case.family,
            "scope": case.scope,
            "prompt": case.prompt,
            "styles": list(case.styles),
            "expected_kind": case.kind,
            "expected_dispatch": case.dispatch,
            "expected_any": list(case.expected_any),
        }
        try:
            objective = await _interpret_with_retry(
                interpreter,
                case,
                max_attempts=max_attempts,
                retry_delay_seconds=retry_delay_seconds,
            )
            tool_ids: list[str] = []
            if objective.requires_external_state and objective.dispatch_path().value != "wait":
                tool_ids = [
                    spec.id
                    for spec in _registry(case.scope).search(
                        objective.capability_query(),
                        context=context,
                        effective_only=True,
                        limit=12,
                    )
                ]

            mismatches: list[str] = []
            if objective.kind.value != case.kind:
                mismatches.append(f"kind:{objective.kind.value}!={case.kind}")
            if objective.requires_external_state != case.external:
                mismatches.append(f"external:{objective.requires_external_state}!={case.external}")
            if objective.requires_mutation != case.mutation:
                mismatches.append(f"mutation:{objective.requires_mutation}!={case.mutation}")
            if objective.requires_future_wait != case.wait:
                mismatches.append(f"wait:{objective.requires_future_wait}!={case.wait}")
            if objective.dispatch_path().value != case.dispatch:
                mismatches.append(f"dispatch:{objective.dispatch_path().value}!={case.dispatch}")

            rank: int | None = None
            if case.expected_any:
                found = [tool_ids.index(cap) + 1 for cap in case.expected_any if cap in tool_ids]
                if found:
                    rank = min(found)
                    ranks.append(rank)
                else:
                    mismatches.append("tool_missing:" + ",".join(case.expected_any))

            passed = not mismatches
            row.update(
                {
                    "passed": passed,
                    "mismatches": mismatches,
                    "actual_objective": objective.objective,
                    "actual_kind": objective.kind.value,
                    "actual_operations": [op.value for op in objective.operations],
                    "actual_resources": list(objective.resource_hints),
                    "actual_external": objective.requires_external_state,
                    "actual_mutation": objective.requires_mutation,
                    "actual_wait": objective.requires_future_wait,
                    "actual_complexity": objective.complexity.value,
                    "actual_dispatch": objective.dispatch_path().value,
                    "tool_ids": tool_ids,
                    "best_expected_rank": rank,
                }
            )
        except Exception as error:
            model_errors += 1
            row.update(
                {
                    "passed": False,
                    "mismatches": [f"exception:{type(error).__name__}"],
                    "error": str(error)[:500],
                }
            )

        passed = bool(row.get("passed"))
        by_family[case.family]["total"] += 1
        by_scope[case.scope]["total"] += 1
        if passed:
            by_family[case.family]["passed"] += 1
            by_scope[case.scope]["passed"] += 1
        for style in case.styles:
            by_style[style]["total"] += 1
            if passed:
                by_style[style]["passed"] += 1
        results.append(row)

        print("REAL_WORLD_EVAL " + json.dumps(row, ensure_ascii=False, sort_keys=True), flush=True)
        if ordinal < len(selected) and interval_seconds:
            await asyncio.sleep(max(0.0, interval_seconds))

    passed_total = sum(1 for row in results if row.get("passed"))
    classifier_passed = sum(
        1
        for row in results
        if not any(
            str(item).startswith(("kind:", "external:", "mutation:", "wait:", "dispatch:"))
            for item in row.get("mismatches", [])
        )
        and not any(str(item).startswith("exception:") for item in row.get("mismatches", []))
    )
    retrieval_rows = [row for row in results if row.get("expected_any")]
    retrieval_passed = sum(
        1
        for row in retrieval_rows
        if not any(str(item).startswith("tool_missing:") for item in row.get("mismatches", []))
        and not any(str(item).startswith("exception:") for item in row.get("mismatches", []))
    )
    no_tool_rows = [row for row in results if not next(case for case in selected if case.case_id == row["case_id"]).external]
    no_tool_passed = sum(1 for row in no_tool_rows if row.get("actual_external") is False)

    def finalize(stats: dict[str, dict[str, int]]) -> dict[str, dict[str, Any]]:
        return {
            key: {
                **value,
                "accuracy": round(value["passed"] / value["total"], 4) if value["total"] else None,
            }
            for key, value in sorted(stats.items())
        }

    summary = {
        "total": len(results),
        "passed": passed_total,
        "accuracy": round(passed_total / len(results), 4) if results else None,
        "model_errors": model_errors,
        "classifier_passed": classifier_passed,
        "classifier_accuracy": round(classifier_passed / len(results), 4) if results else None,
        "retrieval_total": len(retrieval_rows),
        "retrieval_passed": retrieval_passed,
        "retrieval_hit_rate_at_12": round(retrieval_passed / len(retrieval_rows), 4) if retrieval_rows else None,
        "retrieval_rank_1": sum(1 for rank in ranks if rank == 1),
        "retrieval_rank_3": sum(1 for rank in ranks if rank <= 3),
        "retrieval_rank_5": sum(1 for rank in ranks if rank <= 5),
        "no_tool_total": len(no_tool_rows),
        "no_tool_passed": no_tool_passed,
        "no_tool_accuracy": round(no_tool_passed / len(no_tool_rows), 4) if no_tool_rows else None,
        "by_family": finalize(by_family),
        "by_style": finalize(by_style),
        "by_scope": finalize(by_scope),
        "failed_case_ids": [row["case_id"] for row in results if not row.get("passed")],
    }
    print("REAL_WORLD_EVAL_SUMMARY " + json.dumps(summary, ensure_ascii=False, sort_keys=True), flush=True)
    return {**summary, "results": results}


def markdown_report(report: dict[str, Any]) -> str:
    lines = [
        "# Operly real-world semantic routing report",
        "",
        f"- Prompts tested: **{report['total']}**",
        f"- Passed end-to-end semantic+retrieval checks: **{report['passed']} ({report['accuracy']:.1%})**",
        f"- Classifier accuracy: **{report['classifier_accuracy']:.1%}**",
        f"- Capability hit rate @12: **{report['retrieval_hit_rate_at_12']:.1%}**",
        f"- No-tool accuracy: **{report['no_tool_accuracy']:.1%}**",
        f"- Model/transport errors: **{report['model_errors']}**",
        "",
        "## Capability ranking",
        "",
        f"- expected capability rank #1: {report['retrieval_rank_1']}/{report['retrieval_total']}",
        f"- within top 3: {report['retrieval_rank_3']}/{report['retrieval_total']}",
        f"- within top 5: {report['retrieval_rank_5']}/{report['retrieval_total']}",
        "",
        "## By family",
        "",
        "| Family | Passed | Total | Accuracy |",
        "|---|---:|---:|---:|",
    ]
    for family, stats in report["by_family"].items():
        lines.append(f"| {family} | {stats['passed']} | {stats['total']} | {stats['accuracy']:.1%} |")
    lines.extend(["", "## Failures", ""])
    failures = [row for row in report["results"] if not row.get("passed")]
    if not failures:
        lines.append("No semantic/retrieval failures in the tested corpus.")
    else:
        for row in failures:
            lines.extend(
                [
                    f"### {row['case_id']}",
                    "",
                    f"Prompt: `{row['prompt']}`",
                    "",
                    f"Mismatch: `{', '.join(row.get('mismatches', []))}`",
                    "",
                    f"Actual: `{row.get('actual_kind', 'n/a')} / {row.get('actual_dispatch', 'n/a')}`",
                    "",
                    f"Candidates: `{', '.join(row.get('tool_ids', [])[:12])}`",
                    "",
                ]
            )
    return "\n".join(lines) + "\n"


def write_report_files(report: dict[str, Any], *, directory: str | Path = ".") -> tuple[Path, Path]:
    root = Path(directory)
    root.mkdir(parents=True, exist_ok=True)
    json_path = root / "real-world-objective-report.json"
    md_path = root / "real-world-objective-report.md"
    json_path.write_text(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    md_path.write_text(markdown_report(report), encoding="utf-8")
    return json_path, md_path


__all__ = [
    "CURATED_CASES",
    "RawPromptCase",
    "all_cases",
    "corpus_inventory",
    "markdown_report",
    "run_real_world_eval",
    "write_report_files",
]

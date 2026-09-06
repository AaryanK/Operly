from __future__ import annotations

from packages.agent_runtime.real_world_evaluation import RawPromptCase, _c


# Exactly 100 deliberately implicit prompts. These avoid obvious capability nouns where
# practical and force the model to infer the user's meaning before capability retrieval.
# They are synthetic and never execute tools or read connected provider data. Failures
# should improve semantic compilation/retrieval, not create phrase-specific hard routes.
IMPLICIT_SEMANTIC_CASES: tuple[RawPromptCase, ...] = (
    # Personal communications: infer mailbox/message retrieval without saying email/Gmail/inbox.
    _c("implicit.p.mail.01", "implicit_personal_mail", "personal", "did dad ever say what gate hes landing at", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "casual")),
    _c("implicit.p.mail.02", "implicit_personal_mail", "personal", "what did professor say about the workshop dates again", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "contextual")),
    _c("implicit.p.mail.03", "implicit_personal_mail", "personal", "has the bursar sent that tuition receipt yet", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "casual")),
    _c("implicit.p.mail.04", "implicit_personal_mail", "personal", "what was the last thing jeffrey told me about the mapper data", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "casual")),
    _c("implicit.p.mail.05", "implicit_personal_mail", "personal", "did chase ever confirm that checking offer thing", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "fragment")),
    _c("implicit.p.mail.06", "implicit_personal_mail", "personal", "where did dad say i should meet him at the airport", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "question")),
    _c("implicit.p.mail.07", "implicit_personal_mail", "personal", "anything from school about my visa stuff lately", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "casual")),
    _c("implicit.p.mail.08", "implicit_personal_mail", "personal", "what did the airline say about my baggage allowance", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "question")),
    _c("implicit.p.mail.09", "implicit_personal_mail", "personal", "did prof ever say whether joining remote was okay", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "casual")),
    _c("implicit.p.mail.10", "implicit_personal_mail", "personal", "when did dad say hed call me", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "question")),

    # Personal communications mutations with context carrying the channel/target.
    _c("implicit.p.mail.11", "implicit_personal_mail", "personal", "tell him i made it home safe", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), context=("assistant: Dad is the selected correspondent and his address is dad@example.test.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.p.mail.12", "implicit_personal_mail", "personal", "send this back to prof: friday works for me", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), context=("assistant: Professor's selected message is from prof@example.test.",), styles=("implicit", "contextual")),
    _c("implicit.p.mail.13", "implicit_personal_mail", "personal", "write something to dad saying ill call tonight but leave it unsent", kind="act", external=True, mutation=True, caps=("google.gmail.create_draft",), context=("assistant: Dad's address is dad@example.test.",), styles=("implicit", "negation")),
    _c("implicit.p.mail.14", "implicit_personal_mail", "personal", "put that one away so its not cluttering things", kind="act", external=True, mutation=True, caps=("google.gmail.modify_labels",), context=("assistant: The selected Gmail message id is msg-88.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.p.mail.15", "implicit_personal_mail", "personal", "say yes that works back to him", kind="act", external=True, mutation=True, caps=("google.gmail.send_email",), context=("assistant: Dad's selected message is from dad@example.test and asks whether 7 PM works.",), styles=("implicit", "pronoun", "contextual")),

    # Personal time semantics: infer calendar availability/events without the calendar noun.
    _c("implicit.p.time.01", "implicit_personal_time", "personal", "what am i doing tomorrow after lunch", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("implicit", "casual")),
    _c("implicit.p.time.02", "implicit_personal_time", "personal", "how packed am i friday", kind="retrieve", external=True, caps=("google.calendar.list_events", "google.calendar.freebusy"), styles=("implicit", "idiom")),
    _c("implicit.p.time.03", "implicit_personal_time", "personal", "do i have a hole around 3 friday", kind="retrieve", external=True, caps=("google.calendar.freebusy",), styles=("implicit", "idiom", "fuzzy_time")),
    _c("implicit.p.time.04", "implicit_personal_time", "personal", "keep friday 2 to 3 for dentist", kind="act", external=True, mutation=True, caps=("google.calendar.create_event",), styles=("implicit", "imperative")),
    _c("implicit.p.time.05", "implicit_personal_time", "personal", "block tomorrow 6 to 8 for studying", kind="act", external=True, mutation=True, caps=("google.calendar.create_event",), styles=("implicit", "casual")),
    _c("implicit.p.time.06", "implicit_personal_time", "personal", "push that 3pm thing to friday", kind="act", external=True, mutation=True, caps=("google.calendar.update_event",), context=("assistant: The selected calendar event id is evt-22.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.p.time.07", "implicit_personal_time", "personal", "scratch the dentist thing on friday", kind="act", external=True, mutation=True, caps=("google.calendar.delete_event",), context=("assistant: The selected calendar event id is evt-22 and it is Dentist.",), styles=("implicit", "colloquial", "contextual")),
    _c("implicit.p.time.08", "implicit_personal_time", "personal", "move whatever is at 3 tomorrow over to friday", kind="composite", external=True, mutation=True, dispatch="agent_loop", caps=("google.calendar.list_events",), styles=("implicit", "ambiguous_target", "compound")),
    _c("implicit.p.time.09", "implicit_personal_time", "personal", "whats right before qualifier prep tomorrow", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("implicit", "question")),
    _c("implicit.p.time.10", "implicit_personal_time", "personal", "which day next week looks the least packed", kind="retrieve", external=True, caps=("google.calendar.list_events", "google.calendar.freebusy"), styles=("implicit", "comparative")),

    # Personal tasks/workflows: infer durable state from colloquial phrasing.
    _c("implicit.p.work.01", "implicit_personal_work", "personal", "what do i still owe myself this week", kind="retrieve", external=True, caps=("tasks.list",), styles=("implicit", "idiom")),
    _c("implicit.p.work.02", "implicit_personal_work", "personal", "dont let me forget the report tomorrow", kind="act", external=True, mutation=True, caps=("tasks.create",), styles=("implicit", "casual")),
    _c("implicit.p.work.03", "implicit_personal_work", "personal", "thats done", kind="act", external=True, mutation=True, caps=("tasks.update_status",), context=("assistant: The selected task id is task-42 and it is Submit report.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.p.work.04", "implicit_personal_work", "personal", "what ran overnight", kind="retrieve", external=True, caps=("workflow.run.list",), context=("assistant: We were discussing your scheduled automations.",), styles=("implicit", "fragment", "contextual")),
    _c("implicit.p.work.05", "implicit_personal_work", "personal", "why did the 2am thing bomb", kind="retrieve", external=True, caps=("workflow.run.list", "workflow.run.get", "workflow.trace"), context=("assistant: The 2 AM scheduled automation is Morning brief.",), styles=("implicit", "slang", "contextual")),
    _c("implicit.p.work.06", "implicit_personal_work", "personal", "kick the morning thing again", kind="act", external=True, mutation=True, caps=("workflow.run.start",), context=("assistant: Morning brief is workflow wf-12.",), styles=("implicit", "colloquial", "contextual")),
    _c("implicit.p.work.07", "implicit_personal_work", "personal", "pause the nightly thing for now", kind="act", external=True, mutation=True, caps=("workflow.disable",), context=("assistant: The nightly automation is workflow wf-12.",), styles=("implicit", "colloquial", "contextual")),
    _c("implicit.p.work.08", "implicit_personal_work", "personal", "bring it back", kind="act", external=True, mutation=True, caps=("workflow.enable",), context=("assistant: Workflow wf-12 is currently disabled.",), styles=("implicit", "pronoun", "contextual")),

    # Personal compound/wait semantics.
    _c("implicit.p.compound.01", "implicit_compound", "personal", "when dad gets back to me nudge me", kind="wait", external=True, wait=True, dispatch="wait", styles=("implicit", "future")),
    _c("implicit.p.compound.02", "implicit_compound", "personal", "once the nightly thing is done tell me", kind="wait", external=True, wait=True, dispatch="wait", context=("assistant: The nightly thing is workflow run run-77.",), styles=("implicit", "future", "contextual")),
    _c("implicit.p.compound.03", "implicit_compound", "personal", "figure out when im open friday then tell dad those options", kind="composite", external=True, mutation=True, dispatch="agent_loop", styles=("implicit", "compound", "cross_tool")),
    _c("implicit.p.compound.04", "implicit_compound", "personal", "see what deadline prof gave me and make sure i dont forget it", kind="composite", external=True, mutation=True, dispatch="agent_loop", styles=("implicit", "compound", "cross_tool")),
    _c("implicit.p.compound.05", "implicit_compound", "personal", "find the last thing dad said and give me the exact wording", kind="retrieve", external=True, dispatch="agent_loop", caps=("google.gmail.search",), styles=("implicit", "compound")),

    # Personal no-tool traps: semantic resemblance must not force external state.
    _c("implicit.p.notool.01", "implicit_no_tool", "personal", "what does being booked solid mean", kind="respond", external=False, dispatch="respond", styles=("implicit", "no_tool")),
    _c("implicit.p.notool.02", "implicit_no_tool", "personal", "how do i politely tell dad im running late", kind="respond", external=False, dispatch="respond", styles=("implicit", "writing", "no_tool")),
    _c("implicit.p.notool.03", "implicit_no_tool", "personal", "what does pending mean on a charge", kind="respond", external=False, dispatch="respond", styles=("implicit", "no_tool")),
    _c("implicit.p.notool.04", "implicit_no_tool", "personal", "if i study 2 hours a day for 5 days how many hours is that", kind="respond", external=False, dispatch="respond", styles=("implicit", "math", "no_tool")),
    _c("implicit.p.notool.05", "implicit_no_tool", "personal", "give me three ways to say yes that works professionally", kind="respond", external=False, dispatch="respond", styles=("implicit", "writing", "no_tool")),
    _c("implicit.p.notool.06", "implicit_no_tool", "personal", "what usually makes a week feel overloaded", kind="respond", external=False, dispatch="respond", styles=("implicit", "no_tool")),
    _c("implicit.p.notool.07", "implicit_no_tool", "personal", "explain what an automation trigger is without touching my stuff", kind="respond", external=False, dispatch="respond", styles=("implicit", "negation", "no_tool")),

    # Workspace search/current-state semantics without saying search/workspace where possible.
    _c("implicit.w.biz.01", "implicit_workspace_business", "workspace", "whos acme again", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.crm.contacts.list", "workspace_os.crm.organizations.list"), styles=("implicit", "fragment")),
    _c("implicit.w.biz.02", "implicit_workspace_business", "workspace", "where are we with northstar", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.suppliers.suppliers.list"), styles=("implicit", "business_shorthand")),
    _c("implicit.w.biz.03", "implicit_workspace_business", "workspace", "pull up 1042", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.finance.invoices.list"), context=("assistant: We were discussing invoice INV-1042.",), styles=("implicit", "identifier", "contextual")),
    _c("implicit.w.biz.04", "implicit_workspace_business", "workspace", "whats the whole story on this customer", kind="retrieve", external=True, caps=("workspace.customer.snapshot",), context=("assistant: The selected customer contact_id is contact-123.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.w.biz.05", "implicit_workspace_business", "workspace", "anything ugly today", kind="retrieve", external=True, dispatch="agent_loop", caps=("workspace.attention.list",), styles=("implicit", "idiom")),
    _c("implicit.w.biz.06", "implicit_workspace_business", "workspace", "who still owes us money", kind="retrieve", external=True, caps=("workspace_os.finance.invoices.list", "workspace.search"), styles=("implicit", "business")),
    _c("implicit.w.biz.07", "implicit_workspace_business", "workspace", "when did we last hear from acme", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.crm.interactions.list"), styles=("implicit", "business")),
    _c("implicit.w.biz.08", "implicit_workspace_business", "workspace", "show me everything tied to launch", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.projects.projects.list"), styles=("implicit", "fragment")),
    _c("implicit.w.biz.09", "implicit_workspace_business", "workspace", "what changed around here since yesterday", kind="retrieve", external=True, caps=("workspace.activity.list",), styles=("implicit", "casual")),
    _c("implicit.w.biz.10", "implicit_workspace_business", "workspace", "quick pulse on the business", kind="retrieve", external=True, caps=("workspace.summary.read",), styles=("implicit", "business_shorthand")),

    # Workspace finance/inventory/CRM/scheduling/support/docs/team/marketing.
    _c("implicit.w.ops.01", "implicit_workspace_ops", "workspace", "acme gave us 500 by ach mark it down", kind="act", external=True, mutation=True, caps=("workspace.finance.payment.record",), styles=("implicit", "business_shorthand")),
    _c("implicit.w.ops.02", "implicit_workspace_ops", "workspace", "charge acme 750 for logo work due next friday", kind="act", external=True, mutation=True, caps=("workspace.finance.invoice.create_simple",), styles=("implicit", "business_shorthand")),
    _c("implicit.w.ops.03", "implicit_workspace_ops", "workspace", "we counted four more blue mugs than the system thinks", kind="act", external=True, mutation=True, caps=("workspace.inventory.adjust",), styles=("implicit", "voice_dictation")),
    _c("implicit.w.ops.04", "implicit_workspace_ops", "workspace", "sarah wants tuesday at 2 put her in", kind="act", external=True, mutation=True, caps=("workspace_os.scheduling.appointments.create",), styles=("implicit", "voice_dictation")),
    _c("implicit.w.ops.05", "implicit_workspace_ops", "workspace", "what support stuff is still open", kind="retrieve", external=True, caps=("workspace_os.support.tickets.list",), styles=("implicit", "casual")),
    _c("implicit.w.ops.06", "implicit_workspace_ops", "workspace", "pull the onboarding procedure", kind="retrieve", external=True, caps=("workspace_os.documents.documents.list", "workspace.search"), styles=("implicit", "business")),
    _c("implicit.w.ops.07", "implicit_workspace_ops", "workspace", "whos taking time off next week", kind="retrieve", external=True, caps=("workspace.search",), styles=("implicit", "business")),
    _c("implicit.w.ops.08", "implicit_workspace_ops", "workspace", "which promos are running right now", kind="retrieve", external=True, caps=("workspace.search",), styles=("implicit", "business")),
    _c("implicit.w.ops.09", "implicit_workspace_ops", "workspace", "what jobs are slipping", kind="retrieve", external=True, caps=("workspace.search", "workspace_os.projects.projects.list"), styles=("implicit", "business")),
    _c("implicit.w.ops.10", "implicit_workspace_ops", "workspace", "what are we almost out of", kind="retrieve", external=True, caps=("workspace.search", "workspace.inventory.movements.list"), styles=("implicit", "business")),

    # Workspace authority/configuration semantics.
    _c("implicit.w.access.01", "implicit_workspace_access", "workspace", "who can get in here", kind="retrieve", external=True, caps=("workspace.members.list",), styles=("implicit", "access_control")),
    _c("implicit.w.access.02", "implicit_workspace_access", "workspace", "let jane@example.com in as an employee", kind="act", external=True, mutation=True, caps=("workspace.invitations.create", "workspace.members.add"), styles=("implicit", "access_control")),
    _c("implicit.w.access.03", "implicit_workspace_access", "workspace", "sam should be manager now", kind="act", external=True, mutation=True, caps=("workspace.members.role.update",), context=("assistant: Sam's workspace user id is u-44.",), styles=("implicit", "access_control", "contextual")),
    _c("implicit.w.access.04", "implicit_workspace_access", "workspace", "take u-44 out of here", kind="act", external=True, mutation=True, caps=("workspace.members.remove",), styles=("implicit", "access_control", "identifier")),
    _c("implicit.w.access.05", "implicit_workspace_access", "workspace", "what can an analyst actually do here", kind="retrieve", external=True, caps=("workspace.roles.list",), styles=("implicit", "access_control")),
    _c("implicit.w.access.06", "implicit_workspace_access", "workspace", "make this run on chicago time", kind="act", external=True, mutation=True, caps=("workspace.settings.update",), styles=("implicit", "configuration")),
    _c("implicit.w.access.07", "implicit_workspace_access", "workspace", "give me the ecommerce setup", kind="act", external=True, mutation=True, caps=("workspace.presets.apply",), styles=("implicit", "configuration")),
    _c("implicit.w.access.08", "implicit_workspace_access", "workspace", "what packs could this business use", kind="retrieve", external=True, caps=("workspace.presets.list",), styles=("implicit", "configuration")),

    # Workspace connected apps: infer service family from contextual objects rather than explicit verbs.
    _c("implicit.w.apps.01", "implicit_workspace_apps", "workspace", "make me a blank deck called q4 review", kind="act", external=True, mutation=True, caps=("canva.design.create",), context=("assistant: Use the connected design service for this request.",), styles=("implicit", "contextual")),
    _c("implicit.w.apps.02", "implicit_workspace_apps", "workspace", "turn D-77 into a pdf", kind="act", external=True, mutation=True, caps=("canva.design.export.create",), context=("assistant: D-77 is the selected Canva design.",), styles=("implicit", "identifier", "contextual")),
    _c("implicit.w.apps.03", "implicit_workspace_apps", "workspace", "is that render done yet", kind="retrieve", external=True, caps=("canva.design.export.get",), context=("assistant: The active Canva export id is exp-9.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.w.apps.04", "implicit_workspace_apps", "workspace", "what summer sale designs do we already have", kind="retrieve", external=True, caps=("canva.designs.list",), context=("assistant: We are reviewing connected Canva assets.",), styles=("implicit", "business", "contextual")),
    _c("implicit.w.apps.05", "implicit_workspace_apps", "workspace", "what have people been saying in 123456789", kind="retrieve", external=True, caps=("discord.messages.list",), context=("assistant: 123456789 is the selected Discord channel id.",), styles=("implicit", "identifier", "contextual")),
    _c("implicit.w.apps.06", "implicit_workspace_apps", "workspace", "tell 123456789 deploy is live", kind="act", external=True, mutation=True, caps=("discord.message.send",), context=("assistant: 123456789 is the selected Discord channel id.",), styles=("implicit", "identifier", "contextual")),
    _c("implicit.w.apps.07", "implicit_workspace_apps", "workspace", "thumbs up that one", kind="act", external=True, mutation=True, caps=("discord.reaction.add",), context=("assistant: Selected Discord channel id is 123 and message id is 555.",), styles=("implicit", "pronoun", "contextual")),
    _c("implicit.w.apps.08", "implicit_workspace_apps", "workspace", "spin bug triage off that", kind="act", external=True, mutation=True, caps=("discord.thread.create",), context=("assistant: Selected Discord channel id is 123 and message id is 555.",), styles=("implicit", "pronoun", "contextual")),

    # Studio / Agent Computer semantics with developer shorthand and omitted capability nouns.
    _c("implicit.w.dev.01", "implicit_workspace_dev", "workspace", "ship proj-7", kind="act", external=True, mutation=True, caps=("studio.solution.deploy",), styles=("implicit", "developer_slang", "identifier")),
    _c("implicit.w.dev.02", "implicit_workspace_dev", "workspace", "is sol-4 actually live", kind="retrieve", external=True, caps=("studio.solution.status",), styles=("implicit", "developer_slang", "identifier")),
    _c("implicit.w.dev.03", "implicit_workspace_dev", "workspace", "prods broken put sol-4 back to the previous healthy one", kind="act", external=True, mutation=True, caps=("studio.solution.rollback",), styles=("implicit", "developer_slang", "incident")),
    _c("implicit.w.dev.04", "implicit_workspace_dev", "workspace", "point app.example.com at sol-4", kind="act", external=True, mutation=True, caps=("studio.solution.domain.request",), styles=("implicit", "developer_slang")),
    _c("implicit.w.dev.05", "implicit_workspace_dev", "workspace", "whats sitting in /workspace on cs-1", kind="retrieve", external=True, caps=("computer.files.list",), styles=("implicit", "technical", "identifier")),
    _c("implicit.w.dev.06", "implicit_workspace_dev", "workspace", "show me README from cs-1", kind="retrieve", external=True, caps=("computer.files.read",), styles=("implicit", "technical", "identifier")),
    _c("implicit.w.dev.07", "implicit_workspace_dev", "workspace", "what changed in cs-1", kind="retrieve", external=True, caps=("computer.git.diff",), context=("assistant: We are reviewing the repository state in coding session cs-1.",), styles=("implicit", "developer_slang", "contextual")),
    _c("implicit.w.dev.08", "implicit_workspace_dev", "workspace", "is cs-1 alive", kind="retrieve", external=True, caps=("computer.runtime.status",), styles=("implicit", "developer_slang", "identifier")),
    _c("implicit.w.dev.09", "implicit_workspace_dev", "workspace", "run the tests in cs-1", kind="act", external=True, mutation=True, caps=("computer.terminal.exec",), styles=("implicit", "developer_slang", "identifier")),
    _c("implicit.w.dev.10", "implicit_workspace_dev", "workspace", "whats the page showing right now", kind="retrieve", external=True, caps=("computer.browser.snapshot",), context=("assistant: Browser session cs-1 is open on the current page.",), styles=("implicit", "pronoun", "contextual")),

    # Cross-lingual implicit semantics: still no language-specific hard routing.
    _c("implicit.multi.01", "implicit_multilingual", "personal", "est-ce que papa a dit à quelle heure il arrive", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "french")),
    _c("implicit.multi.02", "implicit_multilingual", "personal", "heeft papa ooit gezegd wanneer hij landt", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "dutch")),
    _c("implicit.multi.03", "implicit_multilingual", "personal", "बुबाले कति बजे आइपुग्छु भनेका थिए", kind="retrieve", external=True, caps=("google.gmail.search",), styles=("implicit", "nepali")),
    _c("implicit.multi.04", "implicit_multilingual", "personal", "tengo algo el viernes sobre las 3", kind="retrieve", external=True, caps=("google.calendar.freebusy", "google.calendar.list_events"), styles=("implicit", "spanish")),
    _c("implicit.multi.05", "implicit_multilingual", "personal", "halte morgen 6 bis 8 fürs lernen frei", kind="act", external=True, mutation=True, caps=("google.calendar.create_event",), styles=("implicit", "german")),
    _c("implicit.multi.06", "implicit_multilingual", "personal", "kal 3 baje kuch hai kya mera", kind="retrieve", external=True, caps=("google.calendar.freebusy", "google.calendar.list_events"), styles=("implicit", "hinglish")),
    _c("implicit.multi.07", "implicit_multilingual", "personal", "هل عندي شيء الجمعة حوالي الثالثة", kind="retrieve", external=True, caps=("google.calendar.freebusy", "google.calendar.list_events"), styles=("implicit", "arabic")),
    _c("implicit.multi.08", "implicit_multilingual", "personal", "明日の午後って何か入ってる", kind="retrieve", external=True, caps=("google.calendar.list_events",), styles=("implicit", "japanese")),
    _c("implicit.multi.09", "implicit_multilingual", "workspace", "explique moi simplement pourquoi une petite entreprise peut avoir du profit mais manquer de cash", kind="respond", external=False, dispatch="respond", styles=("implicit", "french", "no_tool")),
)


assert len(IMPLICIT_SEMANTIC_CASES) == 100


__all__ = ["IMPLICIT_SEMANTIC_CASES"]

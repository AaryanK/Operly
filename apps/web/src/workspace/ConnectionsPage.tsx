import { FormEvent, useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
type Connection = {
  id: string;
  provider: string;
  connector_type: string;
  display_name: string;
  account?: string | null;
  status: string;
  enabled: boolean;
  health_status?: string | null;
  last_health_check?: string | null;
  last_error?: string | null;
  scopes?: string[];
  capabilities?: string[];
  guild_id?: string;
};
type DiscordStatus = { configured: boolean; ready: boolean; bot_user: string | null; invite_url: string | null; ai_enabled: boolean };
type Tool = { id: string; display_name: string; description: string; provider_id: string; approval_required: boolean; endpoint: string; method: "POST"; permissions: string[]; risk: string };
type ToolIndex = { tools: Tool[] };
type ToolRun = { run_id: string; status: string; capability_id: string; result: unknown; done: boolean };
type Tab = "overview" | "gmail" | "calendar" | "canva" | "discord" | "connections";
type PendingApproval = { approvalId: string; requestId: string; tool: Tool; args: Row; summary: string; onSuccess?: (value: unknown) => void };

const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const list = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === "object" && !Array.isArray(item)) : [];
const splitList = (value: FormDataEntryValue | null) => text(value).split(/[;,\n]/).map((item) => item.trim()).filter(Boolean);
const providerName = (provider: string) => provider === "google" ? "Google Workspace" : provider === "canva" ? "Canva" : provider === "discord" ? "Discord" : provider;
const stableRequestId = (capabilityId: string) => {
  try { return `${capabilityId}:${crypto.randomUUID()}`; }
  catch { return `${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`; }
};
const localInputValue = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return "";
  const local = new Date(date.getTime() - date.getTimezoneOffset() * 60000);
  return local.toISOString().slice(0, 16);
};
const isoFromLocal = (value: FormDataEntryValue | null) => {
  const raw = text(value);
  if (!raw) return "";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : date.toISOString();
};
const eventDate = (value: unknown) => {
  const row = object(value);
  return text(row.dateTime || row.date);
};
const formatWhen = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  const date = new Date(raw);
  return Number.isNaN(date.getTime()) ? raw : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
};

function PageHeader({ children }: { children?: React.ReactNode }) {
  return <header className="surface-header page-header"><div><span className="eyebrow">Workspace modules · deterministic integrations</span><h1>Integrations</h1><p>Use Gmail, Calendar, Canva, and Discord directly from the workspace. Every action resolves current Operly authority plus the provider's own scopes or resource permissions before execution.</p></div>{children}</header>;
}

function Empty({ children }: { children: React.ReactNode }) { return <div className="empty-panel">{children}</div>; }

function Badge({ children, active = false }: { children: React.ReactNode; active?: boolean }) {
  return <span className={`status-chip ${active ? "status-active" : ""}`}>{children}</span>;
}

export function ConnectionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [tab, setTab] = useState<Tab>(() => {
    const requested = new URLSearchParams(window.location.search).get("provider") as Tab | null;
    return requested && ["gmail", "calendar", "canva", "discord", "connections"].includes(requested) ? requested : "overview";
  });
  const [connections, setConnections] = useState<Connection[]>([]);
  const [discordStatus, setDiscordStatus] = useState<DiscordStatus | null>(null);
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);

  const [gmailQuery, setGmailQuery] = useState("in:inbox newer_than:14d");
  const [gmailMessages, setGmailMessages] = useState<Row[]>([]);
  const [gmailSelected, setGmailSelected] = useState<Row | null>(null);

  const [calendarEvents, setCalendarEvents] = useState<Row[]>([]);
  const [calendarSelected, setCalendarSelected] = useState<Row | null>(null);

  const [canvaDesigns, setCanvaDesigns] = useState<Row[]>([]);
  const [canvaSelected, setCanvaSelected] = useState<Row | null>(null);
  const [canvaDataset, setCanvaDataset] = useState<Row>({});
  const [canvaTemplates, setCanvaTemplates] = useState<Row[]>([]);
  const [canvaTemplate, setCanvaTemplate] = useState<Row | null>(null);
  const [canvaTemplateDataset, setCanvaTemplateDataset] = useState<Row>({});
  const [canvaJob, setCanvaJob] = useState<Row | null>(null);

  const [discordInstallations, setDiscordInstallations] = useState<Row[]>([]);
  const [discordChannels, setDiscordChannels] = useState<Row[]>([]);
  const [discordChannel, setDiscordChannel] = useState<Row | null>(null);
  const [discordMessages, setDiscordMessages] = useState<Row[]>([]);

  const toolMap = useMemo(() => new Map(tools.map((item) => [item.id, item])), [tools]);
  const available = (id: string) => toolMap.has(id);
  const canManage = workspace.role === "owner";

  async function reloadFoundation() {
    setLoading(true); setError(null);
    try {
      const [connectorRows, discord, index] = await Promise.all([
        api<Connection[]>("/connectors"),
        api<DiscordStatus>("/connectors/discord/status"),
        api<ToolIndex>("/workspace-tools"),
      ]);
      setConnections(connectorRows); setDiscordStatus(discord); setTools(index.tools);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not resolve workspace integrations");
    } finally { setLoading(false); }
  }

  useEffect(() => { reloadFoundation(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function invoke(id: string, args: Row, summary: string, onSuccess?: (value: unknown) => void) {
    const tool = toolMap.get(id);
    if (!tool) { setError(`${id} is not currently available for your workspace permissions or provider grants.`); return; }
    const requestId = stableRequestId(id);
    setBusy(id); setError(null); setNotice(null);
    try {
      const run = await api<ToolRun>(tool.endpoint, { method: tool.method, body: JSON.stringify({ arguments: args, request_id: requestId }) });
      onSuccess?.(run.result);
      setNotice(`${tool.display_name} completed.`);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "approval_required") {
        const details = object(caught.details);
        const approvalId = text(details.approval_id);
        if (approvalId) setPending({ approvalId, requestId, tool, args, summary, onSuccess });
        else setError(caught.message);
      } else setError(caught instanceof Error ? caught.message : `${tool.display_name} failed`);
    } finally { setBusy(null); }
  }

  async function approvePending() {
    if (!pending) return;
    setBusy(pending.tool.id); setError(null);
    try {
      await api(`/workspace-tools/approvals/${encodeURIComponent(pending.approvalId)}/decision`, { method: "POST", body: JSON.stringify({ approved: true }) });
      const run = await api<ToolRun>(pending.tool.endpoint, { method: pending.tool.method, body: JSON.stringify({ arguments: pending.args, request_id: pending.requestId, approval_id: pending.approvalId }) });
      pending.onSuccess?.(run.result);
      setNotice(`${pending.tool.display_name} completed after approval.`); setPending(null);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Approved action could not execute"); }
    finally { setBusy(null); }
  }

  async function startOAuth(provider: "google" | "canva") {
    setBusy(provider); setError(null);
    try {
      const path = provider === "google" ? "/connectors/google/connect?tier=assistant" : "/connectors/canva/connect";
      const result = await api<{ authorization_url: string }>(path, { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (caught) { setError(caught instanceof Error ? caught.message : `Could not connect ${providerName(provider)}`); setBusy(null); }
  }

  function addDiscord() {
    if (!discordStatus?.invite_url) { setError("Discord bot installation is not configured on the server yet."); return; }
    window.open(discordStatus.invite_url, "_blank", "noopener,noreferrer");
    setNotice(`After adding the bot, run !operly bind ${workspace.name} in that Discord server.`);
  }

  async function testConnection(connection: Connection) {
    setBusy(`test:${connection.id}`); setError(null);
    try {
      const result = await api<{ ok: boolean; health_status: string; error?: string | null }>(`/connectors/${encodeURIComponent(connection.id)}/test`, { method: "POST" });
      setNotice(result.ok ? `${providerName(connection.provider)} is healthy.` : result.error || "Connection test failed.");
      await reloadFoundation();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Connection test failed"); }
    finally { setBusy(null); }
  }

  async function disconnect(connection: Connection) {
    if (!window.confirm(`Disconnect ${providerName(connection.provider)} from this workspace?`)) return;
    setBusy(`disconnect:${connection.id}`); setError(null);
    try { await api(`/connectors/${encodeURIComponent(connection.id)}`, { method: "DELETE" }); await reloadFoundation(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not disconnect integration"); }
    finally { setBusy(null); }
  }

  async function searchGmail(event?: FormEvent) {
    event?.preventDefault();
    await invoke("google.gmail.search", { query: gmailQuery, limit: 20 }, `Search Gmail for “${gmailQuery}”`, (value) => setGmailMessages(list(object(value).messages)));
  }

  async function readGmail(message: Row) {
    await invoke("google.gmail.read_message", { message_id: text(message.id) }, `Read Gmail message ${text(message.subject, "message")}`, (value) => setGmailSelected(object(value)));
  }

  async function composeGmail(event: FormEvent<HTMLFormElement>, mode: "draft" | "send") {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const args = { to: splitList(form.get("to")), cc: splitList(form.get("cc")), bcc: splitList(form.get("bcc")), subject: text(form.get("subject")), text_body: text(form.get("body")) };
    const id = mode === "draft" ? "google.gmail.create_draft" : "google.gmail.send_email";
    await invoke(id, args, mode === "draft" ? `Create Gmail draft “${args.subject}”` : `Send “${args.subject}” to ${args.to.join(", ")}`);
  }

  async function loadCalendar() {
    const start = new Date(); const end = new Date(start.getTime() + 30 * 24 * 60 * 60 * 1000);
    await invoke("google.calendar.list_events", { time_min: start.toISOString(), time_max: end.toISOString(), limit: 50 }, "Load the next 30 days of calendar events", (value) => setCalendarEvents(list(object(value).events)));
  }

  async function saveCalendar(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const currentId = text(calendarSelected?.id);
    const args: Row = {
      summary: text(form.get("summary")), start: isoFromLocal(form.get("start")), end: isoFromLocal(form.get("end")), attendees: splitList(form.get("attendees")), description: text(form.get("description")), location: text(form.get("location")), add_video_conference: form.get("meet") === "on",
    };
    if (currentId) args.event_id = currentId;
    const id = currentId ? "google.calendar.update_event" : "google.calendar.create_event";
    await invoke(id, args, `${currentId ? "Update" : "Create"} calendar event “${text(args.summary)}”`, async () => { setCalendarSelected(null); await loadCalendar(); });
  }

  async function deleteCalendar(event: Row) {
    const eventId = text(event.id);
    if (!eventId) return;
    await invoke("google.calendar.delete_event", { event_id: eventId }, `Delete calendar event “${text(event.summary, "Untitled event")}”`, async () => { setCalendarSelected(null); await loadCalendar(); });
  }

  async function loadCanvaDesigns() {
    await invoke("canva.designs.list", { ownership: "any", sort_by: "modified_descending" }, "Load recent Canva designs", (value) => setCanvaDesigns(list(object(value).items || object(value).designs)));
  }

  async function selectCanvaDesign(design: Row) {
    const id = text(design.id); if (!id) return;
    await invoke("canva.design.get", { design_id: id }, `Open Canva design ${text(design.title, id)}`, (value) => setCanvaSelected(object(value).design ? object(object(value).design) : object(value)));
    if (available("canva.design.dataset")) await invoke("canva.design.dataset", { design_id: id }, "Read Canva autofill fields", (value) => setCanvaDataset(object(object(value).dataset || value)));
  }

  async function createCanvaDesign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    await invoke("canva.design.create", { title: text(form.get("title")), preset: text(form.get("preset"), "presentation") }, `Create Canva design “${text(form.get("title"))}”`, async (value) => { const result = object(value); const design = object(result.design || result); setCanvaSelected(design); await loadCanvaDesigns(); });
  }

  async function loadCanvaTemplates() {
    await invoke("canva.brand_templates.list", { dataset: "non_empty", ownership: "any", sort_by: "modified_descending", limit: 50 }, "Load autofill-enabled Canva brand templates", (value) => setCanvaTemplates(list(object(value).items)));
  }

  async function selectCanvaTemplate(template: Row) {
    setCanvaTemplate(template); const id = text(template.id); if (!id) return;
    await invoke("canva.brand_template.dataset", { brand_template_id: id }, `Read data fields for ${text(template.title, "brand template")}`, (value) => setCanvaTemplateDataset(object(object(value).dataset || value)));
  }

  async function autofillTemplate(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!canvaTemplate) return;
    const form = new FormData(event.currentTarget); const data: Row = {};
    for (const [fieldName, definition] of Object.entries(canvaTemplateDataset)) {
      const kind = text(object(definition).type, "text"); const raw = text(form.get(`field:${fieldName}`));
      if (!raw) continue;
      data[fieldName] = kind === "image" || kind === "video" ? { type: kind, asset_id: raw } : { type: "text", text: raw };
    }
    await invoke("canva.autofill.create", { type: "create_from_brand_template", brand_template_id: text(canvaTemplate.id), title: text(form.get("title")), data }, `Create Canva design from “${text(canvaTemplate.title, "template")}”`, (value) => setCanvaJob(object(value).job ? object(object(value).job) : object(value)));
  }

  async function updateCanvaDesign(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!canvaSelected) return;
    const form = new FormData(event.currentTarget); const data: Row = {};
    for (const [fieldName, definition] of Object.entries(canvaDataset)) {
      const kind = text(object(definition).type, "text"); const raw = text(form.get(`field:${fieldName}`));
      if (!raw) continue;
      data[fieldName] = kind === "image" || kind === "video" ? { type: kind, asset_id: raw } : { type: "text", text: raw };
    }
    await invoke("canva.autofill.create", { type: "update_design", design_id: text(canvaSelected.id), data }, `Update autofill fields in Canva design “${text(canvaSelected.title, canvaSelected.id)}”`, (value) => setCanvaJob(object(value).job ? object(object(value).job) : object(value)));
  }

  async function exportCanva(format: string) {
    const designId = text(canvaSelected?.id); if (!designId) return;
    await invoke("canva.design.export.create", { design_id: designId, format }, `Export Canva design as ${format.toUpperCase()}`, (value) => setCanvaJob(object(value).job ? object(object(value).job) : object(value)));
  }

  async function loadDiscord() {
    await invoke("discord.installations.list", {}, "Load Discord installations", (value) => setDiscordInstallations(list(object(value).installations)));
    await invoke("discord.channels.list", {}, "Load Discord channels", (value) => setDiscordChannels(list(object(value).channels)));
  }

  async function selectDiscordChannel(channel: Row) {
    setDiscordChannel(channel);
    await invoke("discord.messages.list", { channel_id: text(channel.channel_id), limit: 50 }, `Load #${text(channel.name)} message history`, (value) => setDiscordMessages(list(object(value).messages)));
  }

  async function sendDiscord(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); if (!discordChannel) return;
    const form = new FormData(event.currentTarget); const content = text(form.get("content"));
    await invoke("discord.message.send", { channel_id: text(discordChannel.channel_id), content }, `Send message to #${text(discordChannel.name)}`, async () => { (event.currentTarget.elements.namedItem("content") as HTMLInputElement | null)?.setAttribute("value", ""); await selectDiscordChannel(discordChannel); });
  }

  async function reactDiscord(message: Row, emoji: string) {
    if (!discordChannel) return;
    await invoke("discord.reaction.add", { channel_id: text(discordChannel.channel_id), message_id: text(message.message_id), emoji }, `React ${emoji} to a Discord message`, () => setNotice(`Reaction ${emoji} added.`));
  }

  async function threadDiscord(message: Row) {
    if (!discordChannel) return; const name = window.prompt("Thread name"); if (!name) return;
    await invoke("discord.thread.create", { channel_id: text(discordChannel.channel_id), message_id: text(message.message_id), name }, `Create Discord thread “${name}”`);
  }

  useEffect(() => {
    if (loading) return;
    if (tab === "gmail" && !gmailMessages.length && available("google.gmail.search")) searchGmail();
    if (tab === "calendar" && !calendarEvents.length && available("google.calendar.list_events")) loadCalendar();
    if (tab === "canva" && !canvaDesigns.length && available("canva.designs.list")) { loadCanvaDesigns(); if (available("canva.brand_templates.list")) loadCanvaTemplates(); }
    if (tab === "discord" && !discordChannels.length && available("discord.channels.list")) loadDiscord();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, loading, tools.length]);

  const providerCounts = useMemo(() => ({
    google: connections.filter((item) => item.provider === "google").length,
    canva: connections.filter((item) => item.provider === "canva").length,
    discord: connections.filter((item) => item.provider === "discord").length,
  }), [connections]);

  const nav: Array<{ id: Tab; label: string; mark: string }> = [
    { id: "overview", label: "Overview", mark: "◫" }, { id: "gmail", label: "Gmail", mark: "✉" }, { id: "calendar", label: "Calendar", mark: "□" }, { id: "canva", label: "Canva", mark: "◇" }, { id: "discord", label: "Discord", mark: "#" }, { id: "connections", label: "Connections", mark: "↗" },
  ];

  return <main className="workspace-page integration-workbench">
    <PageHeader><div className="page-actions"><button type="button" onClick={reloadFoundation}>Refresh authority</button></div></PageHeader>
    <nav className="integration-tabs" aria-label="Integration tools">{nav.map((item) => <button type="button" key={item.id} className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)}><span>{item.mark}</span>{item.label}</button>)}</nav>
    {notice && <div className="inline-notice">{notice}</div>}{error && <div className="inline-error page-error">{error}</div>}{loading && <div className="loading-panel">Resolving current workspace and provider authority…</div>}

    {!loading && tab === "overview" && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Available integration tools</span><strong>{tools.filter((item) => item.provider_id.includes("google") || item.provider_id.includes("canva") || item.provider_id.includes("discord")).length}</strong><small>Permission + provider availability resolved now</small></article>
        <article className="metric-card"><span>Google</span><strong>{providerCounts.google ? "Connected" : "Not connected"}</strong><small>Gmail and Calendar</small></article>
        <article className="metric-card"><span>Canva</span><strong>{providerCounts.canva ? "Connected" : "Not connected"}</strong><small>Designs, templates, autofill and export</small></article>
        <article className="metric-card"><span>Discord</span><strong>{discordStatus?.ready ? "Online" : discordStatus?.configured ? "Offline" : "Not configured"}</strong><small>AI off · deterministic bot only</small></article>
      </section>
      <section className="integration-provider-grid">
        <button className="integration-provider-card" onClick={() => setTab("gmail")}><span className="integration-provider-icon">G</span><div><strong>Gmail</strong><p>Search and read mail, compose drafts, send approved messages, and manage labels.</p></div><Badge active={available("google.gmail.search")}>{available("google.gmail.search") ? "Ready" : "Unavailable"}</Badge></button>
        <button className="integration-provider-card" onClick={() => setTab("calendar")}><span className="integration-provider-icon">31</span><div><strong>Calendar</strong><p>See schedules and free/busy, create meetings, update events, delete with approval, and add Meet links.</p></div><Badge active={available("google.calendar.list_events")}>{available("google.calendar.list_events") ? "Ready" : "Unavailable"}</Badge></button>
        <button className="integration-provider-card" onClick={() => setTab("canva")}><span className="integration-provider-icon">C</span><div><strong>Canva</strong><p>Browse designs, create, export, inspect autofill fields, use brand templates, and update data-enabled designs.</p></div><Badge active={available("canva.designs.list")}>{available("canva.designs.list") ? "Ready" : "Unavailable"}</Badge></button>
        <button className="integration-provider-card" onClick={() => setTab("discord")}><span className="integration-provider-icon">#</span><div><strong>Discord</strong><p>Browse installed servers and channels, read history, send approved messages, react, and create threads.</p></div><Badge active={available("discord.channels.list")}>{available("discord.channels.list") ? "Ready" : "Unavailable"}</Badge></button>
      </section>
    </>}

    {!loading && tab === "gmail" && <section className="integration-split">
      <article className="data-card integration-list-pane"><div className="card-heading"><div><span className="eyebrow">Google Workspace</span><h2>Inbox</h2></div><Badge active={available("google.gmail.search")}>{available("google.gmail.search") ? "Connected" : "Needs permission"}</Badge></div>
        <form className="integration-search" onSubmit={searchGmail}><input value={gmailQuery} onChange={(event) => setGmailQuery(event.target.value)} placeholder="Gmail search syntax" /><button disabled={busy === "google.gmail.search"}>Search</button></form>
        <div className="integration-scroll-list">{gmailMessages.map((message) => <button key={text(message.id)} className={gmailSelected?.id === message.id ? "active" : ""} onClick={() => readGmail(message)}><strong>{text(message.subject, "(no subject)")}</strong><span>{text(message.from, "Unknown sender")}</span><p>{text(message.snippet)}</p><small>{text(message.date)}</small></button>)}{!gmailMessages.length && <Empty>No messages loaded.</Empty>}</div>
      </article>
      <div className="integration-detail-stack">
        <article className="data-card">{gmailSelected ? <><div className="card-heading"><div><span className="eyebrow">Message</span><h2>{text(gmailSelected.subject, "(no subject)")}</h2></div></div><p className="integration-meta">From {text(gmailSelected.from)} · To {text(gmailSelected.to)} · {text(gmailSelected.date)}</p><pre className="integration-message-body">{text(gmailSelected.text_body, text(gmailSelected.snippet, "This message has no plain-text body."))}</pre></> : <Empty>Select a message to read it.</Empty>}</article>
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Compose</span><h2>New email</h2></div><small>Send requires approval</small></div><form className="integration-form" onSubmit={(event) => composeGmail(event, "send")}><label>To<input name="to" required placeholder="name@example.com, another@example.com" /></label><label>CC<input name="cc" placeholder="Optional" /></label><label>BCC<input name="bcc" placeholder="Optional" /></label><label>Subject<input name="subject" required maxLength={998} /></label><label>Message<textarea name="body" rows={8} /></label><div className="row-actions"><button type="button" disabled={!available("google.gmail.create_draft") || busy === "google.gmail.create_draft"} onClick={(event) => { const form = event.currentTarget.closest("form"); if (form) composeGmail({ preventDefault() {}, currentTarget: form } as unknown as FormEvent<HTMLFormElement>, "draft"); }}>Save draft</button><button className="primary-button" disabled={!available("google.gmail.send_email") || busy === "google.gmail.send_email"}>Review & send</button></div></form></article>
      </div>
    </section>}

    {!loading && tab === "calendar" && <section className="integration-split">
      <article className="data-card integration-list-pane"><div className="card-heading"><div><span className="eyebrow">Google Workspace</span><h2>Next 30 days</h2></div><button onClick={loadCalendar}>Refresh</button></div><div className="integration-scroll-list">{calendarEvents.map((item) => <button key={text(item.id)} className={calendarSelected?.id === item.id ? "active" : ""} onClick={() => setCalendarSelected(item)}><strong>{text(item.summary, "Untitled event")}</strong><span>{formatWhen(eventDate(item.start))}</span><p>{text(item.location) || list(item.attendees).map((person) => text(person.email)).filter(Boolean).join(", ")}</p></button>)}{!calendarEvents.length && <Empty>No upcoming events loaded.</Empty>}</div></article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">{calendarSelected ? "Edit event" : "Create event"}</span><h2>{calendarSelected ? text(calendarSelected.summary, "Event") : "New meeting"}</h2></div>{calendarSelected && <button onClick={() => setCalendarSelected(null)}>New event</button>}</div><form key={text(calendarSelected?.id, "new")} className="integration-form" onSubmit={saveCalendar}><label>Title<input name="summary" required defaultValue={text(calendarSelected?.summary)} /></label><div className="integration-form-row"><label>Start<input type="datetime-local" name="start" required defaultValue={localInputValue(eventDate(calendarSelected?.start))} /></label><label>End<input type="datetime-local" name="end" required defaultValue={localInputValue(eventDate(calendarSelected?.end))} /></label></div><label>Attendees<input name="attendees" defaultValue={list(calendarSelected?.attendees).map((person) => text(person.email)).filter(Boolean).join(", ")} placeholder="email@example.com, ..." /></label><label>Location<input name="location" defaultValue={text(calendarSelected?.location)} /></label><label>Description<textarea name="description" rows={5} defaultValue={text(calendarSelected?.description)} /></label>{!calendarSelected && <label className="integration-check"><input type="checkbox" name="meet" /> Add Google Meet</label>}<div className="row-actions">{calendarSelected && <button type="button" className="danger-button" disabled={!available("google.calendar.delete_event")} onClick={() => deleteCalendar(calendarSelected)}>Delete</button>}<button className="primary-button" disabled={!available(calendarSelected ? "google.calendar.update_event" : "google.calendar.create_event")}>{calendarSelected ? "Review update" : "Review & create"}</button></div>{calendarSelected?.html_link && <a href={text(calendarSelected.html_link)} target="_blank" rel="noreferrer">Open in Google Calendar ↗</a>}</form></article>
    </section>}

    {!loading && tab === "canva" && <section className="integration-canva-layout">
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Canva library</span><h2>Designs</h2></div><button onClick={loadCanvaDesigns}>Refresh</button></div><form className="integration-form compact" onSubmit={createCanvaDesign}><div className="integration-form-row"><label>New design title<input name="title" required placeholder="Campaign concept" /></label><label>Type<select name="preset" defaultValue="presentation"><option value="presentation">Presentation</option><option value="doc">Doc</option><option value="email">Email</option><option value="whiteboard">Whiteboard</option></select></label></div><button className="primary-button" disabled={!available("canva.design.create")}>Review & create design</button></form><div className="canva-card-grid">{canvaDesigns.map((design) => <button key={text(design.id)} className={canvaSelected?.id === design.id ? "active" : ""} onClick={() => selectCanvaDesign(design)}>{object(design.thumbnail).url && <img src={text(object(design.thumbnail).url)} alt="" />}<strong>{text(design.title, "Untitled design")}</strong><small>{text(design.id)}</small></button>)}{!canvaDesigns.length && <Empty>No Canva designs loaded.</Empty>}</div></article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Data-aware authoring</span><h2>{canvaSelected ? text(canvaSelected.title, "Selected design") : "Design editor"}</h2></div></div>{canvaSelected ? <><div className="row-actions">{text(object(canvaSelected.urls).edit_url || canvaSelected.edit_url) && <a className="button-link" href={text(object(canvaSelected.urls).edit_url || canvaSelected.edit_url)} target="_blank" rel="noreferrer">Open full Canva editor ↗</a>}<select aria-label="Export format" defaultValue="png" onChange={(event) => exportCanva(event.target.value)}><option value="png">Export…</option><option value="pdf">PDF</option><option value="jpg">JPG</option><option value="png">PNG</option><option value="pptx">PPTX</option><option value="mp4">MP4</option></select></div>{Object.keys(canvaDataset).length ? <form className="integration-form" onSubmit={updateCanvaDesign}><p>Canva only exposes deterministic in-place editing for fields explicitly configured for Data Autofill. Those real fields are shown below.</p>{Object.entries(canvaDataset).map(([name, definition]) => <label key={name}>{name}<small>{text(object(definition).type, "field")}</small><input name={`field:${name}`} placeholder={["image", "video"].includes(text(object(definition).type)) ? "Canva asset ID" : `New ${name} value`} /></label>)}<button className="primary-button" disabled={!available("canva.autofill.create")}>Review & update fields</button></form> : <Empty>This design has no exposed Data Autofill fields. Use “Open full Canva editor” for arbitrary visual editing.</Empty>}</> : <Empty>Select a design to inspect it.</Empty>}</article>
      <article className="data-card full-span"><div className="card-heading"><div><span className="eyebrow">Brand templates</span><h2>Autofill production</h2></div><button onClick={loadCanvaTemplates} disabled={!available("canva.brand_templates.list")}>Refresh templates</button></div><div className="integration-template-layout"><div className="integration-scroll-list">{canvaTemplates.map((template) => <button key={text(template.id)} className={canvaTemplate?.id === template.id ? "active" : ""} onClick={() => selectCanvaTemplate(template)}><strong>{text(template.title, "Brand template")}</strong><small>{text(template.id)}</small></button>)}{!canvaTemplates.length && <Empty>Reconnect Canva with brand-template scopes to use template autofill.</Empty>}</div><div>{canvaTemplate ? <form className="integration-form" onSubmit={autofillTemplate}><h3>{text(canvaTemplate.title, "Brand template")}</h3><label>New design title<input name="title" placeholder={text(canvaTemplate.title)} /></label>{Object.entries(canvaTemplateDataset).map(([name, definition]) => <label key={name}>{name}<small>{text(object(definition).type, "field")}</small><input name={`field:${name}`} placeholder={["image", "video"].includes(text(object(definition).type)) ? "Canva asset ID" : `Value for ${name}`} /></label>)}<button className="primary-button" disabled={!available("canva.autofill.create")}>Review & create from template</button></form> : <Empty>Select an autofill-enabled template.</Empty>}</div></div>{canvaJob && <details className="integration-job"><summary>Latest Canva job</summary><pre>{JSON.stringify(canvaJob, null, 2)}</pre></details>}</article>
    </section>}

    {!loading && tab === "discord" && <section className="integration-discord-layout">
      <article className="data-card integration-list-pane"><div className="card-heading"><div><span className="eyebrow">Discord</span><h2>Channels</h2></div><Badge active={!!discordStatus?.ready}>{discordStatus?.ready ? "Bot online" : "Bot offline"}</Badge></div>{!discordStatus?.configured && canManage && <button className="primary-button" onClick={addDiscord}>Add Discord bot</button>}<div className="integration-scroll-list">{discordChannels.map((channel) => <button key={text(channel.channel_id)} className={discordChannel?.channel_id === channel.channel_id ? "active" : ""} onClick={() => selectDiscordChannel(channel)}><strong>#{text(channel.name)}</strong><span>{text(channel.guild_name)}</span><small>{channel.can_send ? "Can send" : "Read only"}</small></button>)}{!discordChannels.length && <Empty>{discordInstallations.length ? "No visible Discord channels." : "No Discord servers are bound to this workspace."}</Empty>}</div></article>
      <article className="data-card integration-chat-pane"><div className="card-heading"><div><span className="eyebrow">Deterministic messaging</span><h2>{discordChannel ? `#${text(discordChannel.name)}` : "Select a channel"}</h2></div><Badge>AI off</Badge></div><div className="integration-chat-log">{discordMessages.map((message) => <div className="integration-chat-message" key={text(message.message_id)}><span className="mini-avatar">{text(message.author, "?").slice(0, 1).toUpperCase()}</span><div><strong>{text(message.author, "Unknown")}</strong><small>{formatWhen(message.created_at)}</small><p>{text(message.content)}</p><div className="integration-message-actions"><button onClick={() => reactDiscord(message, "👍")}>👍</button><button onClick={() => reactDiscord(message, "✅")}>✅</button><button onClick={() => threadDiscord(message)}>Thread</button></div></div></div>)}{discordChannel && !discordMessages.length && <Empty>No recent messages.</Empty>}</div>{discordChannel && <form className="integration-chat-compose" onSubmit={sendDiscord}><textarea name="content" rows={3} required maxLength={1900} placeholder={`Message #${text(discordChannel.name)}`} /><button className="primary-button" disabled={!available("discord.message.send")}>Review & send</button></form>}</article>
    </section>}

    {!loading && tab === "connections" && <>
      <section className="integration-connect-actions"><article className="data-card"><span className="integration-provider-icon">G</span><h2>Google Workspace</h2><p>Gmail and Calendar are permission-scoped independently.</p>{canManage && <button className="primary-button" onClick={() => startOAuth("google")} disabled={busy === "google"}>{providerCounts.google ? "Reconnect / expand scopes" : "Connect Google"}</button>}</article><article className="data-card"><span className="integration-provider-icon">C</span><h2>Canva</h2><p>Design, export, brand-template and autofill scopes are explicit.</p>{canManage && <button className="primary-button" onClick={() => startOAuth("canva")} disabled={busy === "canva"}>{providerCounts.canva ? "Reconnect / expand scopes" : "Connect Canva"}</button>}</article><article className="data-card"><span className="integration-provider-icon">#</span><h2>Discord</h2><p>Install the deterministic bot, then bind a server to this workspace.</p>{canManage && <button className="primary-button" onClick={addDiscord}>Add Discord bot</button>}</article></section>
      <section className="data-card"><div className="card-heading"><div><span className="eyebrow">Workspace-owned accounts</span><h2>Connected services</h2></div><span>{connections.length}</span></div><div className="row-list">{connections.map((connection) => <article className="data-row stacked" key={connection.id}><div><strong>{providerName(connection.provider)}</strong><small>{connection.display_name}{connection.account ? ` · ${connection.account}` : ""}</small><small>Status: {connection.status} · Health: {connection.health_status || "unknown"}</small>{!!connection.scopes?.length && <details><summary>{connection.scopes.length} provider scopes</summary><small>{connection.scopes.join(", ")}</small></details>}</div>{canManage && <div className="page-actions"><button onClick={() => testConnection(connection)} disabled={busy === `test:${connection.id}`}>Test</button><button onClick={() => disconnect(connection)} disabled={busy === `disconnect:${connection.id}`}>Disconnect</button></div>}</article>)}{!connections.length && <Empty>No external services are connected yet.</Empty>}</div></section>
    </>}

    {pending && <div className="integration-approval-backdrop" role="dialog" aria-modal="true" aria-label="Approve integration action"><article className="integration-approval-card"><span className="eyebrow">Human approval required</span><h2>{pending.tool.display_name}</h2><p>{pending.summary}</p><div className="approval-substance-grid"><span><small>Permission</small><strong>{pending.tool.permissions.join(", ")}</strong></span><span><small>Risk</small><strong>{pending.tool.risk}</strong></span><span><small>Provider</small><strong>{pending.tool.provider_id}</strong></span></div><details><summary>Exact arguments</summary><pre>{JSON.stringify(pending.args, null, 2)}</pre></details><div className="row-actions"><button onClick={() => setPending(null)}>Cancel</button><button className="primary-button" disabled={busy === pending.tool.id} onClick={approvePending}>{busy === pending.tool.id ? "Executing…" : "Approve exact action"}</button></div></article></div>}
  </main>;
}

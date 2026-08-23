import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
const rows = (value: unknown): Row[] => Array.isArray(value) ? value.filter((item): item is Row => !!item && typeof item === "object") : [];
const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const num = (value: unknown) => Number.isFinite(Number(value)) ? Number(value) : 0;
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const titleCase = (value: unknown) => text(value, "unknown").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
const money = (value: unknown, currency = "USD") => {
  try { return new Intl.NumberFormat(undefined, { style: "currency", currency, maximumFractionDigits: 0 }).format(num(value)); }
  catch { return `${currency} ${num(value).toLocaleString()}`; }
};
const when = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(raw)); }
  catch { return raw; }
};

function PageHeader({ eyebrow, title, description, actions }: { eyebrow: string; title: string; description: string; actions?: React.ReactNode }) {
  return <header className="surface-header page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>;
}

function Empty({ children }: { children: React.ReactNode }) { return <div className="empty-panel">{children}</div>; }
function Status({ value }: { value: unknown }) { return <span className={`status-chip status-${text(value).toLowerCase().replaceAll("_", "-")}`}>{titleCase(value)}</span>; }

function useWorkspaceData<T>(workspaceId: string, loader: () => Promise<T>) {
  const [data, setData] = useState<T | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const reload = async () => {
    setLoading(true); setError(null);
    try { setData(await loader()); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load this workspace data"); }
    finally { setLoading(false); }
  };
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspaceId]);
  return { data, loading, error, reload };
}

function metric(label: string, value: React.ReactNode, note: string) {
  return <article className="metric-card" key={label}><span>{label}</span><strong>{value}</strong><small>{note}</small></article>;
}

export function CRMPage({ workspace }: { workspace: WorkspaceSummary }) {
  const { data, loading, error, reload } = useWorkspaceData(workspace.id, async () => {
    const [contacts, leads, quotes, orders] = await Promise.all([
      api<Row[]>("/business/contacts"), api<Row[]>("/business/leads"), api<Row[]>("/business/quotes"), api<Row[]>("/business/orders"),
    ]);
    return { contacts, leads, quotes, orders };
  });
  const [adding, setAdding] = useState(false);
  const [formError, setFormError] = useState<string | null>(null);
  const contacts = data?.contacts || [];
  const uniqueContacts = useMemo(() => {
    const seen = new Set<string>();
    return contacts.filter((item) => {
      const email = text(item.email).trim().toLowerCase();
      const phone = text(item.phone).replace(/\D/g, "");
      const name = text(item.name).trim().toLowerCase();
      const key = email ? `email:${email}` : phone ? `phone:${phone}` : `${name}:${text(item.id)}`;
      if (seen.has(key)) return false;
      seen.add(key); return true;
    });
  }, [contacts]);
  const activeLeads = (data?.leads || []).filter((item) => !["won", "lost"].includes(text(item.stage).toLowerCase()));
  const quoteValue = (data?.quotes || []).reduce((sum, item) => sum + num(item.total ?? item.value), 0);
  const orderValue = (data?.orders || []).reduce((sum, item) => sum + num(item.total ?? item.value), 0);

  async function addContact(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); setFormError(null);
    const form = new FormData(event.currentTarget);
    try {
      await api("/business/contacts", { method: "POST", body: JSON.stringify({ name: form.get("name"), email: form.get("email") || null, phone: form.get("phone") || null }) });
      setAdding(false); await reload();
    } catch (caught) { setFormError(caught instanceof Error ? caught.message : "Contact could not be created"); }
  }

  return <main className="workspace-page">
    <PageHeader eyebrow="Business" title="CRM" description="Customers, opportunities, quotes, and orders in one workspace-owned surface." actions={<button className="primary-button" onClick={() => setAdding((value) => !value)}>+ Contact</button>} />
    {loading && <div className="loading-panel">Loading CRM…</div>}{error && <div className="inline-error page-error">{error}</div>}
    {adding && <form className="inline-form" onSubmit={addContact}><label>Name<input name="name" required maxLength={200} /></label><label>Email<input name="email" type="email" maxLength={320} /></label><label>Phone<input name="phone" maxLength={50} /></label><button className="primary-button">Add contact</button>{formError && <span className="form-error">{formError}</span>}</form>}
    {data && <>
      <section className="metric-grid">{metric("Contacts", uniqueContacts.length, contacts.length > uniqueContacts.length ? `${contacts.length - uniqueContacts.length} duplicate display row(s) collapsed` : "Known customers")}{metric("Active leads", activeLeads.length, "Open opportunities")}{metric("Quotes", money(quoteValue), `${data.quotes.length} quote${data.quotes.length === 1 ? "" : "s"}`)}{metric("Orders", money(orderValue), `${data.orders.length} order${data.orders.length === 1 ? "" : "s"}`)}</section>
      <section className="content-grid two-column">
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">People</span><h2>Contacts</h2></div><span>{uniqueContacts.length}</span></div>{uniqueContacts.length ? <div className="row-list">{uniqueContacts.slice(0, 20).map((item) => <div className="data-row" key={text(item.id) || `${text(item.name)}-${text(item.email)}`}><div><strong>{text(item.name, "Unnamed contact")}</strong><small>{[text(item.email), text(item.phone)].filter(Boolean).join(" · ") || "No contact details"}</small></div><Status value={item.status || "active"} /></div>)}</div> : <Empty>No contacts yet.</Empty>}</article>
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Pipeline</span><h2>Leads</h2></div><span>{data.leads.length}</span></div>{data.leads.length ? <div className="row-list">{data.leads.slice(0, 20).map((item) => <div className="data-row" key={text(item.id)}><div><strong>{text(item.title, text(item.contact_name, "Opportunity"))}</strong><small>{text(item.contact_name) || text(item.contact_email) || money(item.value)}</small></div><Status value={item.stage || "new"} /></div>)}</div> : <Empty>No leads yet.</Empty>}</article>
      </section>
    </>}
  </main>;
}

export function OperationsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const { data, loading, error } = useWorkspaceData(workspace.id, async () => {
    const [snapshotResult, alertsResult] = await Promise.allSettled([api<Row>("/operations/snapshot"), api<Row[]>("/operations/alerts")]);
    if (snapshotResult.status === "rejected") throw snapshotResult.reason;
    return { snapshot: snapshotResult.value, alerts: alertsResult.status === "fulfilled" ? alertsResult.value : [] };
  });
  const snapshot = data?.snapshot || {};
  const counts = object(snapshot.counts);
  const currency = text(object(snapshot.profile).currency, "USD");
  const attention = num(counts.overdue_tasks) + num(counts.stale_leads) + num(counts.low_stock) + num(counts.pending_approvals);
  return <main className="workspace-page"><PageHeader eyebrow="Business" title="Operations" description="A semantic operating snapshot. Metrics are rendered as components, not concatenated legacy markup." />
    {loading && <div className="loading-panel">Reading operational state…</div>}{error && <div className="inline-error page-error">{error}</div>}
    {data && <><section className="metric-grid">{metric("Needs attention", attention, "Exceptions across the workspace")}{metric("Pipeline", money(snapshot.pipeline_value, currency), `${num(counts.stale_leads)} stalled lead(s)`)}{metric("Open tasks", num(counts.open_tasks), `${num(counts.overdue_tasks)} overdue`)}{metric("Approvals", num(counts.pending_approvals), "Waiting for a human decision")}</section>
      <section className="content-grid"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Exceptions</span><h2>Operational alerts</h2></div><span>{data.alerts.length}</span></div>{data.alerts.length ? <div className="row-list">{data.alerts.map((item, index) => <div className="data-row" key={text(item.id) || String(index)}><div><strong>{text(item.title, titleCase(item.kind || item.type || "Alert"))}</strong><small>{text(item.message, text(item.detail, "Review this operational exception"))}</small></div><Status value={item.severity || item.status || "attention"} /></div>)}</div> : <Empty>No operational alerts.</Empty>}</article></section></>}
  </main>;
}

export function ActivityPage({ workspace }: { workspace: WorkspaceSummary }) {
  const { data, loading, error, reload } = useWorkspaceData(workspace.id, async () => {
    const [messagesResult, tasksResult, approvalsResult] = await Promise.allSettled([api<Row[]>("/messages"), api<Row[]>("/tasks"), api<Row[]>("/approvals")]);
    return { messages: messagesResult.status === "fulfilled" ? messagesResult.value : [], tasks: tasksResult.status === "fulfilled" ? tasksResult.value : [], approvals: approvalsResult.status === "fulfilled" ? approvalsResult.value : [] };
  });
  async function approval(id: string, status: "approved" | "rejected") { await api(`/approvals/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ status }) }); await reload(); }
  async function complete(id: string) { await api(`/tasks/${encodeURIComponent(id)}/complete`, { method: "PATCH" }); await reload(); }
  const pending = (data?.approvals || []).filter((item) => text(item.status) === "pending");
  const openTasks = (data?.tasks || []).filter((item) => text(item.status) !== "completed");
  return <main className="workspace-page"><PageHeader eyebrow="Workspace" title="Activity" description="Decisions, work, and recent conversations in one auditable surface." />
    {loading && <div className="loading-panel">Loading activity…</div>}{error && <div className="inline-error page-error">{error}</div>}
    {data && <section className="activity-columns">
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Human control</span><h2>Approvals</h2></div><span>{pending.length} pending</span></div>{data.approvals.length ? <div className="row-list">{data.approvals.slice(0, 12).map((item) => <div className="data-row stacked" key={text(item.id)}><div><Status value={item.status} /><strong>{text(item.action, "Action")}</strong><small>{text(object(item.details).rationale, "Review this action before execution")}</small></div>{text(item.status) === "pending" && <div className="row-actions"><button onClick={() => approval(text(item.id), "rejected")}>Reject</button><button className="primary-button" onClick={() => approval(text(item.id), "approved")}>Approve</button></div>}</div>)}</div> : <Empty>No approvals yet.</Empty>}</article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Execution</span><h2>Tasks</h2></div><span>{openTasks.length} open</span></div>{data.tasks.length ? <div className="row-list">{data.tasks.slice(0, 14).map((item) => <div className="data-row" key={text(item.id)}><div><strong>{text(item.title, "Task")}</strong><small>{item.due_at ? `Due ${when(item.due_at)}` : titleCase(item.status)}</small></div>{text(item.status) !== "completed" ? <button className="icon-action" onClick={() => complete(text(item.id))}>✓</button> : <Status value="completed" />}</div>)}</div> : <Empty>No tasks yet.</Empty>}</article>
      <article className="data-card full-span"><div className="card-heading"><div><span className="eyebrow">Channels</span><h2>Recent messages</h2></div><span>{data.messages.length}</span></div>{data.messages.length ? <div className="row-list">{data.messages.slice(0, 16).map((item) => <div className="message-row" key={text(item.id)}><span className="mini-avatar">{text(item.author_name, "?").slice(0, 1).toUpperCase()}</span><div><strong>{text(item.author_name, "Unknown")}</strong><p>{text(item.content)}</p></div><time>{when(item.created_at)}</time></div>)}</div> : <Empty>No messages yet.</Empty>}</article>
    </section>}
  </main>;
}

type Connector = { id: string; provider: string; connector_type: string; display_name: string; status: string; enabled: boolean; account?: string | null; scopes?: string[]; capabilities?: string[]; permission_tier?: string | null; health_status?: string | null; last_error?: string | null };

export function ConnectionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const { data, loading, error, reload } = useWorkspaceData(workspace.id, () => api<Connector[]>("/connectors"));
  const [actionError, setActionError] = useState<string | null>(null);
  async function act(action: () => Promise<unknown>) { setActionError(null); try { await action(); await reload(); } catch (caught) { setActionError(caught instanceof Error ? caught.message : "Connection action failed"); } }
  async function connectGoogle(tier: "basic" | "assistant") {
    try { const result = await api<{ authorization_url: string }>(`/connectors/google/connect?tier=${tier}`, { method: "POST", body: "{}" }); window.location.assign(result.authorization_url); }
    catch (caught) { setActionError(caught instanceof Error ? caught.message : "Google connection could not start"); }
  }
  return <main className="workspace-page"><PageHeader eyebrow="Extend" title="Connections" description="Workspace-owned credentials and connector health. Personal connectors remain outside this boundary." actions={<button className="primary-button" onClick={() => connectGoogle("assistant")}>Connect Google</button>} />
    {loading && <div className="loading-panel">Loading connections…</div>}{(error || actionError) && <div className="inline-error page-error">{error || actionError}</div>}
    {data && <section className="content-grid"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Installed</span><h2>Workspace connections</h2></div><span>{data.length}</span></div>{data.length ? <div className="connector-grid">{data.map((item) => <article className="connector-card" key={item.id}><div className="connector-logo">{item.provider.slice(0, 1).toUpperCase()}</div><div className="connector-copy"><div><strong>{item.display_name}</strong><Status value={item.health_status || item.status} /></div><p>{item.account || titleCase(item.connector_type)}</p><small>{(item.capabilities || []).slice(0, 4).map(titleCase).join(" · ") || "No exposed capabilities"}</small></div><div className="row-actions"><button onClick={() => act(() => api(`/connectors/${item.id}/test`, { method: "POST", body: "{}" }))}>Test</button>{item.enabled && <button onClick={() => act(() => api(`/connectors/${item.id}/disable`, { method: "POST", body: "{}" }))}>Disable</button>}<button className="danger-button" onClick={() => act(() => api(`/connectors/${item.id}`, { method: "DELETE" }))}>Disconnect</button></div>{item.last_error && <p className="connector-error">{item.last_error}</p>}<details><summary>Advanced permissions</summary><code>{(item.scopes || []).join("\n") || "No raw OAuth scopes"}</code></details></article>)}</div> : <Empty>No workspace connectors yet. Connect Google when this workspace needs it.</Empty>}</article></section>}
  </main>;
}

export function PresencePage({ workspace }: { workspace: WorkspaceSummary }) {
  const { data, loading, error, reload } = useWorkspaceData(workspace.id, async () => {
    const [solutionsResult, profileResult, connectorsResult] = await Promise.allSettled([api<Row[]>("/solutions"), api<Row>("/company/profile"), api<Connector[]>("/connectors")]);
    return { solutions: solutionsResult.status === "fulfilled" ? solutionsResult.value : [], profile: profileResult.status === "fulfilled" ? object(profileResult.value.profile) : {}, connectors: connectorsResult.status === "fulfilled" ? connectorsResult.value : [] };
  });
  const [actionError, setActionError] = useState<string | null>(null);
  const presence = data?.solutions.find((item) => text(item.solution_type) === "digital_presence");
  const production = object(presence?.production);
  const preview = object(presence?.preview);
  async function createPresence() { setActionError(null); try { await api("/solutions", { method: "POST", body: JSON.stringify({ solution_type: "digital_presence" }) }); await reload(); } catch (caught) { setActionError(caught instanceof Error ? caught.message : "Presence could not be created"); } }
  async function publish() { if (!presence) return; setActionError(null); try { const result = await api<Row>(`/solutions/${text(presence.id)}/approve`, { method: "POST", body: "{}" }); const job = object(result.job); if (text(job.status) !== "succeeded") throw new Error("Publishing was not verified. No unverified version was made public."); await reload(); } catch (caught) { setActionError(caught instanceof Error ? caught.message : "Publishing failed"); } }
  async function rollback() { if (!presence) return; setActionError(null); try { const result = await api<Row>(`/solutions/${text(presence.id)}/rollback`, { method: "POST", body: "{}" }); const job = object(result.job); if (text(job.status) !== "succeeded") throw new Error("Rollback could not be verified; the current live version remains serving."); await reload(); } catch (caught) { setActionError(caught instanceof Error ? caught.message : "Rollback failed"); } }
  return <main className="workspace-page"><PageHeader eyebrow="Digital presence" title={text(data?.profile.display_name || data?.profile.business_name || data?.profile.legal_name, "Your business online")} description="A truthful view of verified preview and production state." />
    {loading && <div className="loading-panel">Checking your business presence…</div>}{(error || actionError) && <div className="inline-error page-error">{error || actionError}</div>}
    {data && <section className="content-grid"><article className="presence-card">{presence ? <><div><span className="eyebrow">Website</span><h2>{titleCase(presence.status)}</h2><p>{text(production.state) === "live" ? "Your verified website is live." : text(presence.status) === "failed" ? "Publishing failed. No unverified version was made public." : "Your verified preview stays private until publishing succeeds."}</p></div><div className="page-actions">{text(preview.url) && <a className="secondary-button" href={text(preview.url)} target="_blank" rel="noreferrer">Preview</a>}{text(production.state) === "live" && text(production.url) ? <><a className="primary-button" href={text(production.url)} target="_blank" rel="noreferrer">View live</a><button className="secondary-button" onClick={rollback}>Rollback</button></> : <button className="primary-button" onClick={publish}>Publish</button>}</div></> : <><div><span className="eyebrow">Website</span><h2>No digital presence Solution yet</h2><p>Create a preview from the business information Operly already knows.</p></div><button className="primary-button" disabled={Object.keys(data.profile).length === 0} onClick={createPresence}>Get my business online</button></>}</article><section className="metric-grid">{metric("Business information", Object.keys(data.profile).length ? "Ready" : "Learning", text(data.profile.description, "Add business details so generated presence starts accurate."))}{metric("Connected channels", data.connectors.filter((item) => item.status === "connected").length, data.connectors.filter((item) => item.status === "connected").map((item) => item.display_name).join(" · ") || "No workspace channels connected")}</section></section>}
  </main>;
}

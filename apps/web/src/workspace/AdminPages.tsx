import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const title = (value: unknown) => text(value).replaceAll("_", " ").replaceAll(":", " · ").replace(/\b\w/g, (char) => char.toUpperCase());
const initials = (value: unknown) => text(value, "?").split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase();

const capabilityLabels: Record<string, string> = {
  "workspace:settings:manage": "Manage workspace settings",
  "workspace:members:manage": "Manage members",
  "workspace:roles:manage": "Manage roles and permissions",
  "crm:read": "View customers and sales",
  "crm:write": "Manage customers and sales",
  "operations:read": "View operations",
  "operations:write": "Manage operations",
  "tasks:read": "View tasks",
  "tasks:write": "Manage tasks",
  "approvals:read": "View approvals",
  "approvals:decide": "Approve or reject actions",
  "connectors:read": "View integrations",
  "connectors:manage": "Manage integrations",
  "solutions:read": "View Solutions",
  "solutions:write": "Create and change Solutions",
  "agent:use": "Use workspace Operly",
};
function capabilityLabel(id: string) { return capabilityLabels[id] || title(id); }
function status(value: unknown) { const raw = text(value, "active"); return <span className={`status-chip status-${raw.replaceAll("_", "-")}`}>{title(raw)}</span>; }
function header(eyebrow: string, heading: string, description: string, actions?: React.ReactNode) { return <header className="surface-header page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{heading}</h1><p>{description}</p></div>{actions && <div className="page-actions">{actions}</div>}</header>; }

export function MembersPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [members, setMembers] = useState<Row[]>([]);
  const [roles, setRoles] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addMember, setAddMember] = useState(false);
  const [addRole, setAddRole] = useState(false);

  async function reload() {
    setLoading(true); setError(null);
    try {
      const [memberRows, roleRows] = await Promise.all([api<Row[]>("/workspace/members"), api<Row[]>("/workspace/roles")]);
      setMembers(memberRows); setRoles(roleRows);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace administration is unavailable"); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function createMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api("/workspace/members", { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) }); setAddMember(false); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Member could not be added"); }
  }
  async function createRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api("/workspace/roles", { method: "POST", body: JSON.stringify({ name: form.get("name"), key: form.get("key") || null, permissions: [] }) }); setAddRole(false); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Role could not be created"); }
  }
  async function changeRole(memberId: string, role: string) {
    try { await api(`/workspace/members/${encodeURIComponent(memberId)}/role`, { method: "PATCH", body: JSON.stringify({ role }) }); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Role could not be changed"); }
  }

  return <main className="workspace-page">{header("Administration", "Members & roles", "People see human capability names first. Raw permission identifiers stay available under Advanced.", <><button className="secondary-button" onClick={() => setAddRole((value) => !value)}>+ Role</button><button className="primary-button" onClick={() => setAddMember((value) => !value)}>+ Member</button></>)}
    {error && <div className="inline-error page-error">{error}</div>}{loading && <div className="loading-panel">Loading members and roles…</div>}
    {addMember && <form className="inline-form" onSubmit={createMember}><label>Email<input name="email" type="email" required /></label><label>Role<select name="role">{roles.map((role) => <option key={text(role.key)} value={text(role.key)}>{text(role.name, title(role.key))}</option>)}</select></label><button className="primary-button">Add member</button></form>}
    {addRole && <form className="inline-form" onSubmit={createRole}><label>Role name<input name="name" required maxLength={120} /></label><label>Key (optional)<input name="key" maxLength={120} placeholder="support_lead" /></label><button className="primary-button">Create role</button><small>New roles start with no privileges. Add permissions through the governed role-permission API.</small></form>}
    {!loading && <section className="content-grid two-column"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">People</span><h2>Members</h2></div><span>{members.length}</span></div><div className="row-list">{members.map((member) => <div className="data-row member-row" key={text(member.user_id)}><span className="mini-avatar">{initials(member.display_name || member.email)}</span><div><strong>{text(member.display_name, text(member.email, "Member"))}</strong><small>{text(member.email)}</small></div><select value={text(member.role)} onChange={(event) => changeRole(text(member.user_id), event.target.value)}>{roles.map((role) => <option key={text(role.key)} value={text(role.key)}>{text(role.name, title(role.key))}</option>)}</select></div>)}{members.length === 0 && <div className="empty-panel">No members yet.</div>}</div></article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Authority</span><h2>Roles</h2></div><span>{roles.length}</span></div><div className="role-grid">{roles.map((role) => { const permissions = Array.isArray(role.permissions) ? role.permissions.map(String) : []; return <article className="role-card" key={text(role.key)}><div><strong>{text(role.name, title(role.key))}</strong><span className="role-key">{text(role.key)}</span></div><div className="capability-list">{permissions.slice(0, 7).map((permission) => <span key={permission}>{capabilityLabel(permission)}</span>)}{permissions.length === 0 && <small>No capabilities granted.</small>}</div>{permissions.length > 0 && <details><summary>Advanced · {permissions.length} raw permission{permissions.length === 1 ? "" : "s"}</summary><code>{permissions.join("\n")}</code></details>}</article>; })}{roles.length === 0 && <div className="empty-panel">No roles available.</div>}</div></article></section>}
  </main>;
}

type Connector = { id: string; provider: string; display_name: string; connector_type: string; status: string; enabled: boolean; capabilities?: string[]; health_status?: string | null };
const builtins = [
  { name: "CRM", description: "Customers, leads, quotes and orders", capability: "Business data" },
  { name: "Tasks & approvals", description: "Human-controlled execution and follow-up", capability: "Operations" },
  { name: "Operly Intelligence", description: "Workspace reasoning through governed tools", capability: "AI" },
  { name: "Solutions", description: "Business software and digital presence", capability: "Build" },
];

export function PluginsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => { api<Connector[]>("/connectors").then(setConnectors).catch((caught) => setError(caught instanceof Error ? caught.message : "Extensions are unavailable")); }, [workspace.id]);
  return <main className="workspace-page">{header("Extend", "Plugins", "Operly capabilities are product modules; external connectors extend them without becoming a second frontend or bypassing workspace permissions.")}{error && <div className="inline-error page-error">{error}</div>}
    <section className="plugin-grid">{builtins.map((plugin) => <article className="plugin-card" key={plugin.name}><span className="plugin-icon">✦</span><div><strong>{plugin.name}</strong><p>{plugin.description}</p></div><div className="plugin-footer"><span>{plugin.capability}</span><span className="status-chip status-active">Built in</span></div></article>)}{connectors.map((connector) => <article className="plugin-card" key={connector.id}><span className="plugin-icon external">{connector.provider.slice(0, 1).toUpperCase()}</span><div><strong>{connector.display_name}</strong><p>{title(connector.connector_type)} · {(connector.capabilities || []).length} exposed capabilities</p></div><div className="plugin-footer"><span>Workspace connector</span>{status(connector.health_status || connector.status)}</div></article>)}</section>
    <section className="info-banner"><strong>Management lives where ownership lives.</strong><p>Connector credentials and health are managed under Connections. This page shows the plugin/capability composition without duplicating connector controls.</p></section>
  </main>;
}

export function AccessPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [grants, setGrants] = useState<Row[]>([]);
  const [exposures, setExposures] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [showGrant, setShowGrant] = useState(false);
  const [showTool, setShowTool] = useState(false);
  async function reload() {
    setError(null);
    const [grantResult, exposureResult] = await Promise.allSettled([api<Row[]>("/access/client-grants"), api<Row[]>("/access/tool-exposure")]);
    if (grantResult.status === "fulfilled") setGrants(grantResult.value); else setError(grantResult.reason instanceof Error ? grantResult.reason.message : "Client grants are unavailable");
    if (exposureResult.status === "fulfilled") setExposures(exposureResult.value);
  }
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);
  async function createGrant(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); const scopes = text(form.get("scopes")).split(",").map((item) => item.trim()).filter(Boolean); try { await api("/access/client-grants", { method: "POST", body: JSON.stringify({ client_id: form.get("client_id"), scopes, workspace_only: true }) }); setShowGrant(false); await reload(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Grant could not be created"); } }
  async function saveTool(event: FormEvent<HTMLFormElement>) { event.preventDefault(); const form = new FormData(event.currentTarget); try { await api("/access/tool-exposure", { method: "PUT", body: JSON.stringify({ tool_id: form.get("tool_id"), surface: "mcp", access_mode: form.get("access_mode"), exposed: form.get("exposed") === "true" }) }); setShowTool(false); await reload(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Tool policy could not be saved"); } }
  async function revoke(id: string) { try { await api(`/access/client-grants/${encodeURIComponent(id)}`, { method: "DELETE" }); await reload(); } catch (caught) { setError(caught instanceof Error ? caught.message : "Grant could not be revoked"); } }
  return <main className="workspace-page">{header("Security", "AI & MCP access", "External clients receive explicit workspace grants. Raw scope and tool identifiers are secondary details, not the primary product language.", <><button className="secondary-button" onClick={() => setShowTool((value) => !value)}>+ Tool policy</button><button className="primary-button" onClick={() => setShowGrant((value) => !value)}>+ Client grant</button></>)}{error && <div className="inline-error page-error">{error}</div>}
    {showGrant && <form className="inline-form" onSubmit={createGrant}><label>Client name<input name="client_id" required placeholder="ChatGPT team connector" /></label><label>Capabilities / scopes<input name="scopes" placeholder="crm.read, tasks.read" /></label><button className="primary-button">Grant access</button></form>}
    {showTool && <form className="inline-form" onSubmit={saveTool}><label>Tool ID<input name="tool_id" required /></label><label>Access<select name="access_mode"><option value="authenticated">Authenticated</option><option value="owner">Owner only</option></select></label><label>Exposure<select name="exposed"><option value="true">Exposed</option><option value="false">Hidden</option></select></label><button className="primary-button">Save policy</button></form>}
    <section className="content-grid two-column"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Clients</span><h2>Client grants</h2></div><span>{grants.length}</span></div>{grants.length ? <div className="row-list">{grants.map((grant) => { const scopes = Array.isArray(grant.scopes) ? grant.scopes.map(String) : []; return <div className="data-row stacked" key={text(grant.id)}><div><strong>{text(grant.client_id, "External client")}</strong>{status(grant.status || "active")}<p>{scopes.slice(0, 4).map(capabilityLabel).join(" · ") || "No capabilities"}</p><details><summary>Advanced identifiers</summary><code>{scopes.join("\n") || "No raw scopes"}</code></details></div>{text(grant.status, "active") === "active" && <button className="danger-button" onClick={() => revoke(text(grant.id))}>Revoke</button>}</div>; })}</div> : <div className="empty-panel">No external client grants. Nothing outside Operly has explicit access.</div>}</article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">MCP</span><h2>Tool exposure</h2></div><span>{exposures.length}</span></div>{exposures.length ? <div className="row-list">{exposures.map((item) => <div className="data-row stacked" key={text(item.tool_id)}><div><strong>{capabilityLabel(text(item.tool_id, "Tool"))}</strong>{status(item.exposed ? "exposed" : "hidden")}<p>{title(item.access_mode || "authenticated")} access</p><details><summary>Advanced identifier</summary><code>{text(item.tool_id)}\n{text(item.surface, "mcp")}</code></details></div></div>)}</div> : <div className="empty-panel">No explicit tool policies. Tools remain governed by default workspace policy.</div>}</article></section>
  </main>;
}

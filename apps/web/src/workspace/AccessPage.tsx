import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Client = { id: string; name: string; surface: string; owner_managed: boolean };
type Grant = { id: string; client_id: string; scopes: string[]; status: string; expires_at?: string | null };
type Exposure = { tool_id: string; surface: string; exposed: boolean; access_mode: "public" | "authenticated" };

const labels: Record<string, string> = {
  "crm:read": "View customers and sales",
  "crm:write": "Manage customers and sales",
  "tasks:read": "View tasks",
  "tasks:write": "Manage tasks",
  "actions:read": "View actions and approvals",
  "actions:approve": "Approve consequential actions",
  "operations:read": "View operations",
  "operations:write": "Manage operations",
  "solution:read": "View Solutions",
  "solution:generate": "Generate Solutions",
  "solution:write": "Change Solutions",
  "messages:read": "Read workspace messages",
  "model:invoke": "Use workspace AI",
};
const human = (value: string) => labels[value] || value.replaceAll(":", " · ").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const statusClass = (value: string) => `status-chip status-${value.replaceAll("_", "-")}`;

export function AccessPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [grantForm, setGrantForm] = useState(false);
  const [toolForm, setToolForm] = useState(false);

  async function reload() {
    setError(null);
    const [clientResult, grantResult, exposureResult] = await Promise.allSettled([
      api<Client[]>("/access/external-clients"),
      api<Grant[]>("/access/client-grants"),
      api<Exposure[]>("/access/tool-exposure"),
    ]);
    if (clientResult.status === "fulfilled") setClients(clientResult.value);
    if (grantResult.status === "fulfilled") setGrants(grantResult.value);
    else setError(grantResult.reason instanceof Error ? grantResult.reason.message : "Client grants are unavailable");
    if (exposureResult.status === "fulfilled") setExposures(exposureResult.value);
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function createGrant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const scopes = String(form.get("scopes") || "").split(",").map((item) => item.trim()).filter(Boolean);
    try {
      await api("/access/client-grants", { method: "POST", body: JSON.stringify({ client_id: form.get("client_id"), scopes, workspace_only: true }) });
      setGrantForm(false); await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Client grant could not be created"); }
  }

  async function saveExposure(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    try {
      await api("/access/tool-exposure", { method: "PUT", body: JSON.stringify({
        tool_id: form.get("tool_id"),
        surface: "mcp",
        access_mode: form.get("access_mode"),
        exposed: form.get("exposed") === "true",
      }) });
      setToolForm(false); await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Tool policy could not be saved"); }
  }

  async function revoke(id: string) {
    try { await api(`/access/client-grants/${encodeURIComponent(id)}`, { method: "DELETE" }); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Grant could not be revoked"); }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header"><div><span className="eyebrow">Security</span><h1>AI & MCP access</h1><p>External AI clients receive explicit workspace grants. Raw scope and tool IDs are available as advanced details instead of being the primary UX.</p></div><div className="page-actions"><button className="secondary-button" onClick={() => setToolForm((v) => !v)}>+ Tool policy</button><button className="primary-button" onClick={() => setGrantForm((v) => !v)}>+ Client grant</button></div></header>
    {error && <div className="inline-error page-error">{error}</div>}
    {grantForm && <form className="inline-form" onSubmit={createGrant}><label>External client<select name="client_id" required>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select></label><label>Capabilities / scopes<input name="scopes" placeholder="crm:read, tasks:read" /></label><button className="primary-button" disabled={!clients.length}>Grant workspace access</button><small>Only supported clients can receive grants.</small></form>}
    {toolForm && <form className="inline-form" onSubmit={saveExposure}><label>Tool ID<input name="tool_id" required maxLength={160} /></label><label>Who may call it<select name="access_mode"><option value="authenticated">Authenticated clients</option><option value="public">Public</option></select></label><label>Exposure<select name="exposed"><option value="true">Exposed</option><option value="false">Hidden</option></select></label><button className="primary-button">Save policy</button></form>}
    <section className="content-grid two-column"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Clients</span><h2>Client grants</h2></div><span>{grants.length}</span></div>{grants.length ? <div className="row-list">{grants.map((grant) => <div className="data-row stacked" key={grant.id}><div><strong>{clients.find((client) => client.id === grant.client_id)?.name || human(grant.client_id)}</strong><span className={statusClass(grant.status || "active")}>{human(grant.status || "active")}</span><p>{grant.scopes.slice(0, 4).map(human).join(" · ") || "No explicit capability scopes"}</p><details><summary>Advanced identifiers</summary><code>{grant.scopes.join("\n") || "No raw scopes"}</code></details></div>{grant.status === "active" && <button className="danger-button" onClick={() => revoke(grant.id)}>Revoke</button>}</div>)}</div> : <div className="empty-panel">No external client grants. Nothing outside Operly has explicit workspace access.</div>}</article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">MCP</span><h2>Tool exposure</h2></div><span>{exposures.length}</span></div>{exposures.length ? <div className="row-list">{exposures.map((item) => <div className="data-row stacked" key={`${item.surface}:${item.tool_id}`}><div><strong>{human(item.tool_id)}</strong><span className={statusClass(item.exposed ? "exposed" : "hidden")}>{item.exposed ? "Exposed" : "Hidden"}</span><p>{item.access_mode === "public" ? "Public access" : "Authenticated access"}</p><details><summary>Advanced identifier</summary><code>{item.tool_id}\n{item.surface}</code></details></div></div>)}</div> : <div className="empty-panel">No explicit tool policies. Tools remain governed by the default workspace policy.</div>}</article></section>
  </main>;
}

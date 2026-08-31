import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Client = { id: string; name: string; surface: string; owner_managed: boolean };
type Grant = { id: string; client_id: string; scopes: string[]; status: string; expires_at?: string | null };
type Exposure = { tool_id: string; surface: string; exposed: boolean; access_mode: "authenticated" };
type McpTool = {
  name: string;
  title?: string;
  description: string;
  annotations?: { readOnlyHint?: boolean; destructiveHint?: boolean; openWorldHint?: boolean };
  _meta?: {
    "operly/risk"?: string;
    "operly/approvalRequired"?: boolean;
    "operly/providerId"?: string;
    "operly/permissions"?: string[];
  };
};
type McpCatalog = {
  protocol_version: string;
  endpoint: string;
  default_policy: string;
  tool_count: number;
  tools: McpTool[];
};

const labels: Record<string, string> = {
  "workspace:*": "All currently authorized Workspace capabilities",
  "crm:read": "View customers and sales",
  "crm:write": "Manage customers and sales",
  "actions:read": "View actions and traces",
  "actions:approve": "Approve consequential actions",
  "operations:read": "View operations",
  "operations:write": "Manage operations",
  "solution:read": "View Solutions",
  "solution:write": "Change Solutions",
  "computer.*": "Agent Computer",
  "workflow.*": "Workflows",
};
const human = (value: string) => labels[value] || value.replaceAll(":", " · ").replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());
const statusClass = (value: string) => `status-chip status-${value.replaceAll("_", "-")}`;

export function AccessPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [clients, setClients] = useState<Client[]>([]);
  const [grants, setGrants] = useState<Grant[]>([]);
  const [exposures, setExposures] = useState<Exposure[]>([]);
  const [catalog, setCatalog] = useState<McpCatalog | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [grantForm, setGrantForm] = useState(false);
  const [toolForm, setToolForm] = useState(false);
  const [toolQuery, setToolQuery] = useState("");

  const exposureMap = useMemo(() => new Map(exposures.map((item) => [item.tool_id, item])), [exposures]);
  const visibleTools = useMemo(() => {
    const query = toolQuery.trim().toLowerCase();
    const rows = catalog?.tools || [];
    if (!query) return rows.slice(0, 80);
    return rows.filter((tool) => `${tool.name} ${tool.title || ""} ${tool.description}`.toLowerCase().includes(query)).slice(0, 80);
  }, [catalog, toolQuery]);

  async function reload() {
    setError(null);
    const [clientResult, grantResult, exposureResult, catalogResult] = await Promise.allSettled([
      api<Client[]>("/access/external-clients"),
      api<Grant[]>("/access/client-grants"),
      api<Exposure[]>("/access/tool-exposure"),
      api<McpCatalog>("/access/mcp-catalog"),
    ]);
    if (clientResult.status === "fulfilled") setClients(clientResult.value);
    if (grantResult.status === "fulfilled") setGrants(grantResult.value);
    else setError(grantResult.reason instanceof Error ? grantResult.reason.message : "Client grants are unavailable");
    if (exposureResult.status === "fulfilled") setExposures(exposureResult.value);
    if (catalogResult.status === "fulfilled") setCatalog(catalogResult.value);
    else setError((current) => current || (catalogResult.reason instanceof Error ? catalogResult.reason.message : "MCP catalog is unavailable"));
  }

  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function createGrant(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const scopes = String(form.get("scopes") || "workspace:*").split(",").map((item) => item.trim()).filter(Boolean);
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
        access_mode: "authenticated",
        exposed: form.get("exposed") === "true",
      }) });
      setToolForm(false); await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Tool policy could not be saved"); }
  }

  async function setExposure(toolId: string, exposed: boolean) {
    try {
      await api("/access/tool-exposure", { method: "PUT", body: JSON.stringify({ tool_id: toolId, surface: "mcp", access_mode: "authenticated", exposed }) });
      await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Tool policy could not be saved"); }
  }

  async function revoke(id: string) {
    try { await api(`/access/client-grants/${encodeURIComponent(id)}`, { method: "DELETE" }); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Grant could not be revoked"); }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header"><div><span className="eyebrow">Agent security boundary</span><h1>AI & MCP access</h1><p>Operly exposes the same governed Workspace capabilities to agents. MCP never creates authority: live membership, permissions, connector scopes, provider availability and human approvals are checked again on every call.</p></div><div className="page-actions"><button className="secondary-button" onClick={() => void reload()}>Refresh</button><button className="secondary-button" onClick={() => setToolForm((v) => !v)}>Tool policy</button><button className="primary-button" onClick={() => setGrantForm((v) => !v)}>Client grant</button></div></header>
    {error && <div className="inline-error page-error">{error}</div>}

    <section className="metric-grid">
      <article className="metric-card"><span>Agent tools</span><strong>{catalog?.tool_count ?? "—"}</strong><small>Current authorized Workspace catalog</small></article>
      <article className="metric-card"><span>MCP protocol</span><strong>{catalog?.protocol_version || "2026-07-28"}</strong><small>Stateless MCP core</small></article>
      <article className="metric-card"><span>Endpoint</span><strong>{catalog?.endpoint || "/mcp"}</strong><small>Bearer-authenticated remote MCP</small></article>
      <article className="metric-card"><span>Explicit overrides</span><strong>{exposures.length}</strong><small>Default is expose authorized tools</small></article>
    </section>

    {grantForm && <form className="inline-form" onSubmit={createGrant}>
      <label>External client<select name="client_id" required>{clients.map((client) => <option key={client.id} value={client.id}>{client.name}</option>)}</select></label>
      <label>Capability scope<input name="scopes" defaultValue="workspace:*" placeholder="workspace:*" /><small>Use <code>workspace:*</code> for every capability the current user is already allowed to use, or narrow it with values such as <code>computer.*</code>, <code>workflow.*</code>, an exact capability ID, or a permission such as <code>crm:read</code>.</small></label>
      <button className="primary-button" disabled={!clients.length}>Grant workspace access</button>
      <small>This grant cannot add a Workspace permission. Revoking it invalidates the client's future MCP requests.</small>
    </form>}

    {toolForm && <form className="inline-form" onSubmit={saveExposure}>
      <label>Capability<select name="tool_id" required>{(catalog?.tools || []).map((tool) => <option key={tool.name} value={tool.name}>{tool.title || tool.name} · {tool.name}</option>)}</select></label>
      <label>MCP exposure<select name="exposed"><option value="false">Hide from MCP agents</option><option value="true">Expose when authorized</option></select></label>
      <button className="primary-button">Save policy</button><small>MCP is authenticated only. An exposure override never bypasses the user's live Workspace authority.</small>
    </form>}

    <section className="content-grid two-column">
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">External identities</span><h2>Client grants</h2></div><span>{grants.length}</span></div>{grants.length ? <div className="row-list">{grants.map((grant) => <div className="data-row stacked" key={grant.id}><div><strong>{clients.find((client) => client.id === grant.client_id)?.name || human(grant.client_id)}</strong><span className={statusClass(grant.status || "active")}>{human(grant.status || "active")}</span><p>{grant.scopes.map(human).join(" · ") || "No capability scope"}</p><details><summary>Raw narrowing rules</summary><code>{grant.scopes.join("\n") || "No raw scopes"}</code></details></div>{grant.status === "active" && <button className="danger-button" onClick={() => void revoke(grant.id)}>Revoke</button>}</div>)}</div> : <div className="empty-panel">No external client grants. Nothing outside Operly can authenticate to this Workspace through MCP.</div>}</article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Policy</span><h2>How authority resolves</h2></div></div><div className="row-list"><div className="data-row stacked"><div><strong>1. Authenticate the client</strong><p>OAuth identifies the Operly principal and active Workspace grant.</p></div></div><div className="data-row stacked"><div><strong>2. Resolve current Workspace authority</strong><p>Operly reloads membership, role permissions and surface privacy on every request.</p></div></div><div className="data-row stacked"><div><strong>3. Narrow the catalog</strong><p>Client scope and explicit MCP hide rules can remove tools but never add one.</p></div></div><div className="data-row stacked"><div><strong>4. Execute normally</strong><p>The same provider checks, JSON schemas, approval boundary, Kernel trace and events used by the human UI remain in force.</p></div></div></div></article>
    </section>

    <section className="data-card"><div className="card-heading"><div><span className="eyebrow">Live discovery</span><h2>Agent capability catalog</h2><p>These descriptions and JSON contracts are what an MCP agent receives from <code>tools/list</code>.</p></div><span>{catalog?.tool_count || 0}</span></div><label>Find a capability<input value={toolQuery} onChange={(event) => setToolQuery(event.target.value)} placeholder="Python, Gmail, schedule, customer, deploy…" /></label><div className="row-list">{visibleTools.map((tool) => {
      const override = exposureMap.get(tool.name);
      const hidden = override?.exposed === false;
      const risk = tool._meta?.["operly/risk"] || (tool.annotations?.readOnlyHint ? "read_only" : "change");
      const approval = !!tool._meta?.["operly/approvalRequired"];
      return <div className="data-row stacked" key={tool.name}><div><strong>{tool.title || tool.name}</strong><span className={statusClass(hidden ? "hidden" : "exposed")}>{hidden ? "Hidden from MCP" : "Available when authorized"}</span><p>{tool.description}</p><small>{tool.name} · {risk.replaceAll("_", " ")}{approval ? " · human approval may be required" : ""}</small><details><summary>Agent contract metadata</summary><code>{JSON.stringify(tool._meta || {}, null, 2)}</code></details></div><button className={hidden ? "primary-button" : "secondary-button"} onClick={() => void setExposure(tool.name, hidden)}>{hidden ? "Expose" : "Hide"}</button></div>;
    })}{!visibleTools.length && <div className="empty-panel">No matching MCP capabilities.</div>}</div>{catalog && catalog.tools.length > visibleTools.length && <small>Showing the first {visibleTools.length} matching tools. Search to narrow the live catalog.</small>}</section>
  </main>;
}

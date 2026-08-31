import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Schema = Record<string, unknown>;
type Capability = {
  id: string;
  version: string;
  display_name: string;
  description: string;
  provider_id: string;
  scopes: string[];
  input_schema: Schema;
  output_schema: Schema;
  permissions: string[];
  risk: "read_only" | "low" | "medium" | "high" | string;
  approval_required: boolean;
  resource_scope: string;
  reversible: boolean;
  aliases: string[];
  emits: string[];
  tags: string[];
};

type CapabilityResponse = {
  scope_kind: string;
  workspace_id: string;
  workspace_mode: string;
  capabilities: Capability[];
};

type RunResult = {
  run_id: string;
  status: string;
  capability_id: string;
  decision: string;
  result: unknown;
  done: boolean;
  trace: Array<Record<string, unknown>>;
};

const titleCase = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());

function stableRequestId(capabilityId: string) {
  try { return `${capabilityId}:${crypto.randomUUID()}`; }
  catch { return `${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`; }
}

function defaultArguments(capability: Capability): string {
  const required = Array.isArray(capability.input_schema?.required) ? capability.input_schema.required as string[] : [];
  const properties = capability.input_schema?.properties && typeof capability.input_schema.properties === "object"
    ? capability.input_schema.properties as Record<string, Record<string, unknown>>
    : {};
  const result: Record<string, unknown> = {};
  for (const key of required) {
    const schema = properties[key] || {};
    const type = Array.isArray(schema.type) ? schema.type.find((item) => item !== "null") : schema.type;
    if (Array.isArray(schema.enum) && schema.enum.length) result[key] = schema.enum.find((item) => item !== null) ?? null;
    else if (type === "boolean") result[key] = false;
    else if (type === "integer" || type === "number") result[key] = schema.minimum ?? 0;
    else if (type === "object") result[key] = {};
    else if (type === "array") result[key] = [];
    else result[key] = "";
  }
  return JSON.stringify(result, null, 2);
}

export function CapabilitiesPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [data, setData] = useState<CapabilityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [risk, setRisk] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [argumentsText, setArgumentsText] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [run, setRun] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const next = await api<CapabilityResponse>("/kernel/capabilities");
      setData(next);
      setSelectedId((current) => current && next.capabilities.some((item) => item.id === current) ? current : next.capabilities[0]?.id || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load deterministic capabilities");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  const capabilities = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.capabilities || []).filter((item) => {
      if (risk !== "all" && item.risk !== risk) return false;
      if (!needle) return true;
      return `${item.id} ${item.display_name} ${item.description} ${item.provider_id} ${item.tags.join(" ")} ${item.permissions.join(" ")}`.toLowerCase().includes(needle);
    });
  }, [data, query, risk]);

  const selected = data?.capabilities.find((item) => item.id === selectedId) || null;

  function select(capability: Capability) {
    setSelectedId(capability.id);
    setArgumentsText(defaultArguments(capability));
    setRun(null);
    setRunError(null);
    setApprovalId(null);
    setRequestId(null);
  }

  async function execute(existingApprovalId?: string) {
    if (!selected) return;
    setBusy(true);
    setRun(null);
    setRunError(null);
    try {
      const parsed = JSON.parse(argumentsText || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Arguments must be a JSON object");
      const nextRequestId = requestId || stableRequestId(selected.id);
      setRequestId(nextRequestId);
      const result = await api<RunResult>("/kernel/execute", {
        method: "POST",
        body: JSON.stringify({
          capability_id: selected.id,
          arguments: parsed,
          request_id: nextRequestId,
          approval_id: existingApprovalId || undefined,
        }),
      });
      setRun(result);
      setApprovalId(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "approval_required") {
        const details = caught.details && typeof caught.details === "object" ? caught.details as Record<string, unknown> : {};
        const id = typeof details.approval_id === "string" ? details.approval_id : null;
        setApprovalId(id);
        setRunError(id ? "This exact invocation requires human approval before it can execute." : caught.message);
      } else {
        setRunError(caught instanceof Error ? caught.message : "Capability execution failed");
      }
    } finally {
      setBusy(false);
    }
  }

  async function approveAndExecute() {
    if (!approvalId) return;
    setBusy(true);
    setRunError(null);
    try {
      await api(`/kernel/approvals/${encodeURIComponent(approvalId)}/decision`, {
        method: "POST",
        body: JSON.stringify({ approved: true }),
      });
      await execute(approvalId);
    } catch (caught) {
      setRunError(caught instanceof Error ? caught.message : "Approval could not be completed");
      setBusy(false);
    }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header">
      <div>
        <span className="eyebrow">Deterministic infrastructure</span>
        <h1>Capabilities</h1>
        <p>Everything shown here is a real, permission-scoped Operly operation. The Workspace UI, workflows, APIs, and future AI agents can share this same execution boundary.</p>
      </div>
      <div className="page-actions"><button type="button" onClick={reload}>Refresh</button></div>
    </header>

    {loading && <div className="loading-panel">Resolving effective workspace capabilities…</div>}
    {error && <div className="inline-error page-error">{error}</div>}

    {data && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Effective tools</span><strong>{data.capabilities.length}</strong><small>Allowed for your current role</small></article>
        <article className="metric-card"><span>Approval gated</span><strong>{data.capabilities.filter((item) => item.approval_required).length}</strong><small>Require an explicit human decision</small></article>
        <article className="metric-card"><span>Providers</span><strong>{new Set(data.capabilities.map((item) => item.provider_id)).size}</strong><small>Behind one Kernel contract</small></article>
        <article className="metric-card"><span>Workspace mode</span><strong>{titleCase(data.workspace_mode || "full")}</strong><small>Authority is resolved server-side</small></article>
      </section>

      <section className="content-grid two-column">
        <article className="data-card">
          <div className="card-heading"><div><span className="eyebrow">Registry</span><h2>Available capabilities</h2></div><span>{capabilities.length}</span></div>
          <div className="inline-form">
            <label>Search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="CRM, inventory, permissions…" /></label>
            <label>Risk<select value={risk} onChange={(event) => setRisk(event.target.value)}><option value="all">All</option><option value="read_only">Read only</option><option value="low">Low</option><option value="medium">Medium</option><option value="high">High</option></select></label>
          </div>
          <div className="row-list">
            {capabilities.map((item) => <button type="button" className={`data-row stacked ${selectedId === item.id ? "active" : ""}`} key={item.id} onClick={() => select(item)}>
              <div><strong>{item.display_name}</strong><small>{item.id}</small><small>{item.provider_id} · {titleCase(item.risk)}{item.approval_required ? " · approval" : ""}</small></div>
            </button>)}
            {!capabilities.length && <div className="empty-panel">No capabilities match this filter.</div>}
          </div>
        </article>

        <article className="data-card">
          {selected ? <>
            <div className="card-heading"><div><span className="eyebrow">Executable contract</span><h2>{selected.display_name}</h2></div><span className={`status-chip status-${selected.risk.replaceAll("_", "-")}`}>{titleCase(selected.risk)}</span></div>
            <p>{selected.description}</p>
            <div className="approval-substance-grid">
              <span><small>Provider</small><strong>{selected.provider_id}</strong></span>
              <span><small>Permission</small><strong>{selected.permissions.join(", ") || "None"}</strong></span>
              <span><small>Resource scope</small><strong>{selected.resource_scope}</strong></span>
              <span><small>Reversible</small><strong>{selected.reversible ? "Yes" : "No"}</strong></span>
            </div>
            <details><summary>Input contract</summary><code>{JSON.stringify(selected.input_schema, null, 2)}</code></details>
            <details><summary>Output contract</summary><code>{JSON.stringify(selected.output_schema, null, 2)}</code></details>
            <label className="capability-arguments">Arguments JSON<textarea rows={10} value={argumentsText} onChange={(event) => { setArgumentsText(event.target.value); setRun(null); setRunError(null); setApprovalId(null); setRequestId(null); }} /></label>
            <div className="row-actions">
              <button type="button" className="primary-button" disabled={busy} onClick={() => execute()}>{busy ? "Executing…" : selected.approval_required ? "Request execution" : "Execute deterministically"}</button>
              {approvalId && <button type="button" disabled={busy} onClick={approveAndExecute}>Approve exact invocation & execute</button>}
            </div>
            {runError && <div className="inline-error page-error">{runError}{approvalId && <small> Approval: {approvalId}</small>}</div>}
            {run && <div className="approval-substance"><strong>Completed</strong><small>Run {run.run_id}</small><details open><summary>Validated result</summary><code>{JSON.stringify(run.result, null, 2)}</code></details><details><summary>Kernel trace</summary><code>{JSON.stringify(run.trace, null, 2)}</code></details></div>}
          </> : <div className="empty-panel">Select a capability to inspect its deterministic contract.</div>}
        </article>
      </section>
    </>}
  </main>;
}

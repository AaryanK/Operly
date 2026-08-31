import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { WorkspaceSummary } from "../app/types";

type JsonSchema = {
  type?: string | string[];
  title?: string;
  description?: string;
  enum?: unknown[];
  default?: unknown;
  examples?: unknown[];
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  additionalProperties?: boolean | JsonSchema;
};

type Capability = {
  id: string;
  version: string;
  display_name: string;
  description: string;
  provider_id: string;
  scopes: string[];
  input_schema: JsonSchema;
  output_schema: JsonSchema;
  permissions: string[];
  risk: "read_only" | "low" | "medium" | "high" | string;
  approval_required: boolean;
  resource_scope: string;
  reversible: boolean;
  aliases: string[];
  emits: string[];
  tags: string[];
  method: "POST";
  endpoint: string;
  contract_endpoint: string;
};

type CapabilityResponse = {
  scope_kind: string;
  workspace_id: string;
  workspace_mode: string;
  tools: Capability[];
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

type AreaKey = "everyday" | "customers" | "money" | "work" | "email" | "calendar" | "canva" | "discord" | "studio" | "computer" | "workspace" | "system" | "other";
type EditorMode = "guided" | "advanced";
type FieldValue = string | boolean;

const areaOrder: AreaKey[] = ["everyday", "customers", "money", "work", "email", "calendar", "canva", "discord", "studio", "computer", "workspace", "system", "other"];
const areaMeta: Record<AreaKey, { title: string; icon: string; description: string }> = {
  everyday: { title: "Everyday", icon: "✦", description: "Search, summaries, and things that need attention." },
  customers: { title: "Customers & sales", icon: "◎", description: "Customer context, opportunities, sales, and relationships." },
  money: { title: "Money & stock", icon: "$", description: "Invoices, payments, orders, products, and inventory." },
  work: { title: "Work & operations", icon: "✓", description: "Tasks, projects, appointments, support, suppliers, and research." },
  email: { title: "Email", icon: "✉", description: "Read, draft, send, and organize Gmail." },
  calendar: { title: "Calendar", icon: "◫", description: "Calendars, availability, and meetings." },
  canva: { title: "Canva", icon: "◇", description: "Designs, exports, templates, and autofill." },
  discord: { title: "Discord", icon: "◉", description: "Servers, channels, messages, reactions, and threads." },
  studio: { title: "Studio & publishing", icon: "▣", description: "Inspect, deploy, roll back, and publish Studio solutions." },
  computer: { title: "Agent Computer", icon: "⌘", description: "Python, terminal, files, Git, browser, and runtime controls." },
  workspace: { title: "Workspace setup", icon: "⚙", description: "Members, roles, invitations, modules, and settings." },
  system: { title: "System & health", icon: "◆", description: "Runtime status and technical workspace checks." },
  other: { title: "Other tools", icon: "＋", description: "Everything else Operly currently exposes." },
};

const titleCase = (value: string) => value
  .replaceAll("_", " ")
  .replace(/\b\w/g, (char) => char.toUpperCase())
  .replace(/\bId\b/g, "ID")
  .replace(/\bUrl\b/g, "URL")
  .replace(/\bApi\b/g, "API");

const schemaType = (schema: JsonSchema) => {
  if (Array.isArray(schema.type)) return schema.type.find((item) => item !== "null") || "string";
  return schema.type || "string";
};

function stableRequestId(capabilityId: string) {
  try { return `${capabilityId}:${crypto.randomUUID()}`; }
  catch { return `${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`; }
}

function areaFor(capability: Capability): AreaKey {
  const id = capability.id.toLowerCase();
  const tags = new Set(capability.tags.map((item) => item.toLowerCase()));
  if (id.startsWith("google.gmail.")) return "email";
  if (id.startsWith("google.calendar.")) return "calendar";
  if (id.startsWith("canva.")) return "canva";
  if (id.startsWith("discord.")) return "discord";
  if (id.startsWith("studio.")) return "studio";
  if (id.startsWith("computer.")) return "computer";
  if (["workspace.search", "workspace.attention.list", "workspace.summary.read", "workspace.activity.list"].includes(id)) return "everyday";
  if ([...tags].some((tag) => ["crm", "customer", "customers", "sales", "lead", "leads", "contact", "contacts"].includes(tag)) || id.includes("customer") || id.includes("sales")) return "customers";
  if ([...tags].some((tag) => ["finance", "invoice", "payment", "payments", "inventory", "catalog", "orders", "order", "stock"].includes(tag)) || /finance|invoice|payment|inventory|catalog|order/.test(id)) return "money";
  if ([...tags].some((tag) => ["tasks", "task", "projects", "project", "appointments", "scheduling", "support", "suppliers", "research", "operations"].includes(tag)) || /task|project|appointment|support|supplier|research/.test(id)) return "work";
  if (/members|roles|permissions|invitation|settings|modules|presets|workspace\.describe/.test(id)) return "workspace";
  if (capability.provider_id.includes("system") || tags.has("system") || tags.has("runtime")) return "system";
  return "other";
}

function riskCopy(capability: Capability) {
  if (capability.risk === "read_only") return { label: "Just looking", detail: "This reads information and does not change anything." };
  if (capability.risk === "low") return { label: "Small change", detail: "This can change workspace data, but the effect is limited." };
  if (capability.risk === "medium") return { label: "Real change", detail: "This changes something meaningful. Review what you entered before running it." };
  if (capability.risk === "high") return { label: "Important change", detail: "This can have a significant effect. Operly will make the safety boundary obvious." };
  return { label: titleCase(capability.risk), detail: "Review this action before running it." };
}

function defaultFieldValue(schema: JsonSchema, required: boolean): FieldValue {
  const type = schemaType(schema);
  const chosenDefault = schema.default ?? schema.examples?.[0];
  if (type === "boolean") return Boolean(chosenDefault ?? false);
  if (type === "array") {
    if (Array.isArray(chosenDefault)) {
      if (schemaType(schema.items || {}) === "object") return JSON.stringify(chosenDefault, null, 2);
      return chosenDefault.map((item) => String(item)).join("\n");
    }
    return "";
  }
  if (type === "object") return chosenDefault && typeof chosenDefault === "object" ? JSON.stringify(chosenDefault, null, 2) : "{}";
  if (chosenDefault != null) return String(chosenDefault);
  if (required && Array.isArray(schema.enum)) {
    const first = schema.enum.find((item) => item != null);
    return first == null ? "" : String(first);
  }
  if (required && (type === "integer" || type === "number") && schema.minimum != null) return String(schema.minimum);
  return "";
}

function initialFieldValues(capability: Capability): Record<string, FieldValue> {
  const properties = capability.input_schema.properties || {};
  const required = new Set(capability.input_schema.required || []);
  return Object.fromEntries(Object.entries(properties).map(([key, schema]) => [key, defaultFieldValue(schema, required.has(key))]));
}

function defaultArguments(capability: Capability): string {
  const values = initialFieldValues(capability);
  try { return JSON.stringify(buildArguments(capability, values), null, 2); }
  catch { return "{}"; }
}

function parsePrimitive(value: string, type: string) {
  if (type === "integer") {
    const parsed = Number.parseInt(value, 10);
    if (!Number.isFinite(parsed)) throw new Error("Please enter a whole number.");
    return parsed;
  }
  if (type === "number") {
    const parsed = Number(value);
    if (!Number.isFinite(parsed)) throw new Error("Please enter a number.");
    return parsed;
  }
  if (type === "boolean") return value.toLowerCase() === "true";
  return value;
}

function buildArguments(capability: Capability, values: Record<string, FieldValue>): Record<string, unknown> {
  const properties = capability.input_schema.properties || {};
  const required = new Set(capability.input_schema.required || []);
  const result: Record<string, unknown> = {};

  for (const [key, schema] of Object.entries(properties)) {
    const type = schemaType(schema);
    const value = values[key];
    if (type === "boolean") {
      result[key] = Boolean(value);
      continue;
    }
    const raw = String(value ?? "").trim();
    if (!raw && !required.has(key)) continue;
    if (!raw && required.has(key) && type === "string") throw new Error(`${fieldLabel(key, schema)} is required.`);

    if (type === "array") {
      const itemType = schemaType(schema.items || {});
      if (!raw) {
        result[key] = [];
      } else if (itemType === "object" || itemType === "array") {
        const parsed = JSON.parse(raw);
        if (!Array.isArray(parsed)) throw new Error(`${fieldLabel(key, schema)} must be a JSON list.`);
        result[key] = parsed;
      } else {
        result[key] = raw.split(/\r?\n|,/).map((item) => item.trim()).filter(Boolean).map((item) => parsePrimitive(item, itemType));
      }
      continue;
    }

    if (type === "object") {
      const parsed = raw ? JSON.parse(raw) : {};
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${fieldLabel(key, schema)} must be a JSON object.`);
      result[key] = parsed;
      continue;
    }

    result[key] = parsePrimitive(raw, type);
  }
  return result;
}

function fieldLabel(key: string, schema: JsonSchema) {
  return schema.title || titleCase(key);
}

function fieldHint(key: string, schema: JsonSchema) {
  if (schema.description) return schema.description;
  const type = schemaType(schema);
  if (key.endsWith("_id") || key === "id") return "Use the ID shown on the related page or in a previous tool result.";
  if (["to", "cc", "bcc", "attendees", "calendar_ids"].includes(key)) return "Add one item per line. Commas also work.";
  if (key === "query") return "Type what you want Operly to find.";
  if (key.includes("time_min") || key.includes("start")) return "Enter the starting date/time in the format requested by the provider.";
  if (key.includes("time_max") || key.includes("end")) return "Enter the ending date/time in the format requested by the provider.";
  if (key === "limit") return "How many results should Operly return?";
  if (type === "array") return schemaType(schema.items || {}) === "object" ? "Advanced list: enter a JSON array." : "Enter one item per line.";
  if (type === "object") return "Advanced field: enter a small JSON object.";
  return undefined;
}

function inputKind(key: string, schema: JsonSchema) {
  if (schema.format === "email" || key === "email" || key.endsWith("_email")) return "email";
  if (schema.format === "uri" || key.includes("url")) return "url";
  if (schema.format === "date") return "date";
  return "text";
}

function friendlyResult(value: unknown) {
  if (value == null) return "Done. The tool completed successfully.";
  if (Array.isArray(value)) return `Done. Operly returned ${value.length} item${value.length === 1 ? "" : "s"}.`;
  if (typeof value === "object") {
    const object = value as Record<string, unknown>;
    const list = Object.values(object).find((item) => Array.isArray(item)) as unknown[] | undefined;
    if (list) return `Done. Operly returned ${list.length} item${list.length === 1 ? "" : "s"}.`;
    return "Done. Operly completed the action and returned a verified result.";
  }
  return `Done. Result: ${String(value)}`;
}

export function CapabilitiesPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [data, setData] = useState<CapabilityResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [area, setArea] = useState<AreaKey | "all">("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [editorMode, setEditorMode] = useState<EditorMode>("guided");
  const [fieldValues, setFieldValues] = useState<Record<string, FieldValue>>({});
  const [argumentsText, setArgumentsText] = useState("{}");
  const [busy, setBusy] = useState(false);
  const [contractBusy, setContractBusy] = useState(false);
  const [run, setRun] = useState<RunResult | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  const [approvalId, setApprovalId] = useState<string | null>(null);
  const [requestId, setRequestId] = useState<string | null>(null);

  function prepare(capability: Capability) {
    setFieldValues(initialFieldValues(capability));
    setArgumentsText(defaultArguments(capability));
    setRun(null);
    setRunError(null);
    setApprovalId(null);
    setRequestId(null);
  }

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const next = await api<CapabilityResponse>("/workspace-tools");
      setData(next);
      const preferred = next.tools.find((item) => item.id === "workspace.search") || next.tools[0] || null;
      const nextId = selectedId && next.tools.some((item) => item.id === selectedId) ? selectedId : preferred?.id || null;
      setSelectedId(nextId);
      const selected = next.tools.find((item) => item.id === nextId);
      if (selected) prepare(selected);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load workspace tools");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  const areaCounts = useMemo(() => {
    const counts = new Map<AreaKey, number>();
    for (const item of data?.tools || []) counts.set(areaFor(item), (counts.get(areaFor(item)) || 0) + 1);
    return counts;
  }, [data]);

  const capabilities = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return (data?.tools || []).filter((item) => {
      if (area !== "all" && areaFor(item) !== area) return false;
      if (!needle) return true;
      return `${item.id} ${item.display_name} ${item.description} ${item.provider_id} ${item.tags.join(" ")} ${areaMeta[areaFor(item)].title}`.toLowerCase().includes(needle);
    });
  }, [data, query, area]);

  const selected = data?.tools.find((item) => item.id === selectedId) || null;
  const selectedRisk = selected ? riskCopy(selected) : null;
  const selectedProperties = selected?.input_schema.properties || {};
  const selectedRequired = new Set(selected?.input_schema.required || []);

  function select(capability: Capability) {
    setSelectedId(capability.id);
    setArea((current) => current === "all" ? current : areaFor(capability));
    setEditorMode("guided");
    prepare(capability);
  }

  function setField(key: string, value: FieldValue) {
    setFieldValues((current) => ({ ...current, [key]: value }));
    setRun(null);
    setRunError(null);
    setApprovalId(null);
    setRequestId(null);
  }

  async function refreshContract() {
    if (!selected) return;
    setContractBusy(true);
    setRunError(null);
    try {
      const fresh = await api<Capability>(selected.contract_endpoint);
      setData((current) => current ? { ...current, tools: current.tools.map((item) => item.id === fresh.id ? fresh : item) } : current);
      prepare(fresh);
    } catch (caught) {
      setRunError(caught instanceof Error ? caught.message : "Could not re-check this tool");
    } finally {
      setContractBusy(false);
    }
  }

  async function execute(existingApprovalId?: string) {
    if (!selected) return;
    setBusy(true);
    setRun(null);
    setRunError(null);
    try {
      let parsed: Record<string, unknown>;
      if (editorMode === "guided") {
        parsed = buildArguments(selected, fieldValues);
        setArgumentsText(JSON.stringify(parsed, null, 2));
      } else {
        const advanced = JSON.parse(argumentsText || "{}");
        if (!advanced || typeof advanced !== "object" || Array.isArray(advanced)) throw new Error("Advanced arguments must be a JSON object.");
        parsed = advanced as Record<string, unknown>;
      }
      const nextRequestId = requestId || stableRequestId(selected.id);
      setRequestId(nextRequestId);
      const result = await api<RunResult>(selected.endpoint, {
        method: selected.method,
        body: JSON.stringify({ arguments: parsed, request_id: nextRequestId, approval_id: existingApprovalId || undefined }),
      });
      setRun(result);
      setApprovalId(null);
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "approval_required") {
        const details = caught.details && typeof caught.details === "object" ? caught.details as Record<string, unknown> : {};
        const id = typeof details.approval_id === "string" ? details.approval_id : null;
        setApprovalId(id);
        setRunError(id ? "Operly is waiting for you to approve this exact action." : caught.message);
      } else {
        setRunError(caught instanceof Error ? caught.message : "This tool could not run");
      }
    } finally {
      setBusy(false);
    }
  }

  async function decideApproval(approved: boolean) {
    if (!approvalId) return;
    setBusy(true);
    setRunError(null);
    try {
      await api(`/workspace-tools/approvals/${encodeURIComponent(approvalId)}/decision`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      if (approved) await execute(approvalId);
      else {
        setApprovalId(null);
        setRequestId(null);
        setRunError("Nothing was changed. You chose not to approve this action.");
      }
    } catch (caught) {
      setRunError(caught instanceof Error ? caught.message : "Your approval decision could not be saved");
    } finally {
      setBusy(false);
    }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header">
      <div>
        <span className="eyebrow">Everything Operly can do in this workspace</span>
        <h1>All tools</h1>
        <p>Pick what you want to do, fill in a simple form, and run it. Every currently authorized tool advertised by the Workspace API appears here automatically, so there is no hidden API-only action.</p>
      </div>
      <div className="page-actions"><button type="button" onClick={() => void reload()} disabled={loading}>Refresh tools</button></div>
    </header>

    {loading && <div className="loading-panel">Finding the tools you are allowed to use…</div>}
    {error && <div className="inline-error page-error">{error}</div>}

    {data && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Tools you can use</span><strong>{data.tools.length}</strong><small>Every one is reachable from this page</small></article>
        <article className="metric-card"><span>Simple read actions</span><strong>{data.tools.filter((item) => item.risk === "read_only").length}</strong><small>They only look at information</small></article>
        <article className="metric-card"><span>Ask before acting</span><strong>{data.tools.filter((item) => item.approval_required).length}</strong><small>You get the final say</small></article>
        <article className="metric-card"><span>Workspace mode</span><strong>{titleCase(data.workspace_mode || "full")}</strong><small>Permissions are checked on the server</small></article>
      </section>

      <section className="data-card" style={{ marginBottom: 18 }}>
        <div className="card-heading"><div><span className="eyebrow">Start with what makes sense to you</span><h2>Choose an area</h2></div><span>{capabilities.length} shown</span></div>
        <div className="row-actions" style={{ flexWrap: "wrap" }}>
          <button type="button" className={area === "all" ? "primary-button" : ""} onClick={() => setArea("all")}>Everything · {data.tools.length}</button>
          {areaOrder.filter((key) => areaCounts.has(key)).map((key) => <button type="button" key={key} className={area === key ? "primary-button" : ""} onClick={() => setArea(key)}>{areaMeta[key].icon} {areaMeta[key].title} · {areaCounts.get(key)}</button>)}
        </div>
        <div className="inline-form" style={{ marginTop: 14 }}><label>Search tools<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Try: send email, invoice, calendar, customer, deploy…" /></label></div>
      </section>

      <section className="content-grid two-column">
        <article className="data-card">
          <div className="card-heading"><div><span className="eyebrow">Pick an action</span><h2>{area === "all" ? "All available actions" : areaMeta[area].title}</h2>{area !== "all" && <p>{areaMeta[area].description}</p>}</div><span>{capabilities.length}</span></div>
          <div className="row-list">
            {capabilities.map((item) => {
              const meta = areaMeta[areaFor(item)];
              const safety = riskCopy(item);
              return <button type="button" className={`data-row stacked ${selectedId === item.id ? "active" : ""}`} key={item.id} onClick={() => select(item)}>
                <div><strong>{meta.icon} {item.display_name}</strong><small>{item.description}</small><small>{safety.label}{item.approval_required ? " · asks for approval" : ""}</small></div>
              </button>;
            })}
            {!capabilities.length && <div className="empty-panel">No tools match that search. Try a simpler word.</div>}
          </div>
        </article>

        <article className="data-card">
          {selected ? <>
            <div className="card-heading">
              <div><span className="eyebrow">{areaMeta[areaFor(selected)].title}</span><h2>{selected.display_name}</h2></div>
              <span className={`status-chip status-${selected.risk.replaceAll("_", "-")}`}>{selectedRisk?.label}</span>
            </div>
            <p>{selected.description}</p>
            <div className="approval-substance" style={{ marginBottom: 14 }}><strong>{selectedRisk?.detail}</strong>{selected.approval_required && <small> This action will pause and ask you before the real change is made.</small>}</div>

            <div className="row-actions" style={{ marginBottom: 16 }}>
              <button type="button" className={editorMode === "guided" ? "primary-button" : ""} onClick={() => setEditorMode("guided")}>Guided form</button>
              <button type="button" className={editorMode === "advanced" ? "primary-button" : ""} onClick={() => { setArgumentsText(JSON.stringify(buildArguments(selected, fieldValues), null, 2)); setEditorMode("advanced"); }}>Advanced JSON</button>
            </div>

            {editorMode === "guided" ? <>
              {Object.keys(selectedProperties).length ? <div className="inline-form" style={{ display: "grid", gap: 14 }}>
                {Object.entries(selectedProperties).map(([key, schema]) => {
                  const type = schemaType(schema);
                  const label = fieldLabel(key, schema);
                  const hint = fieldHint(key, schema);
                  const required = selectedRequired.has(key);
                  const value = fieldValues[key] ?? (type === "boolean" ? false : "");
                  const enumValues = (schema.enum || []).filter((item) => item != null);
                  const longText = type === "object" || type === "array" || (schema.maxLength || 0) > 500 || /body|description|notes|content|code|expression/.test(key);

                  if (type === "boolean") return <label key={key}>{label}{required ? " *" : ""}<select value={String(Boolean(value))} onChange={(event) => setField(key, event.target.value === "true")}><option value="false">No</option><option value="true">Yes</option></select>{hint && <small>{hint}</small>}</label>;
                  if (enumValues.length) return <label key={key}>{label}{required ? " *" : ""}<select value={String(value)} onChange={(event) => setField(key, event.target.value)}>{!required && <option value="">Leave unchanged / not needed</option>}{enumValues.map((item) => <option key={String(item)} value={String(item)}>{titleCase(String(item))}</option>)}</select>{hint && <small>{hint}</small>}</label>;
                  if (type === "integer" || type === "number") return <label key={key}>{label}{required ? " *" : ""}<input type="number" value={String(value)} min={schema.minimum} max={schema.maximum} step={type === "integer" ? 1 : "any"} onChange={(event) => setField(key, event.target.value)} placeholder={required ? "Required" : "Optional"} />{hint && <small>{hint}</small>}</label>;
                  if (longText) return <label key={key}>{label}{required ? " *" : ""}<textarea rows={type === "object" || (type === "array" && schemaType(schema.items || {}) === "object") ? 7 : 4} value={String(value)} onChange={(event) => setField(key, event.target.value)} placeholder={type === "array" ? "One item per line" : required ? "Required" : "Optional"} />{hint && <small>{hint}</small>}</label>;
                  return <label key={key}>{label}{required ? " *" : ""}<input type={inputKind(key, schema)} value={String(value)} onChange={(event) => setField(key, event.target.value)} placeholder={required ? "Required" : "Optional"} />{hint && <small>{hint}</small>}</label>;
                })}
              </div> : <div className="empty-panel">This action does not need any extra information. You can run it now.</div>}
            </> : <label className="capability-arguments">Advanced arguments JSON<textarea rows={12} value={argumentsText} onChange={(event) => { setArgumentsText(event.target.value); setRun(null); setRunError(null); setApprovalId(null); setRequestId(null); }} /><small>Use this only when you need an exact API value such as null, a nested object, or a complex list.</small></label>}

            <div className="row-actions" style={{ marginTop: 16 }}>
              <button type="button" className="primary-button" disabled={busy} onClick={() => void execute()}>{busy ? "Working…" : selected.approval_required ? "Review & run" : selected.risk === "read_only" ? "Show me" : "Run action"}</button>
              <button type="button" disabled={contractBusy || busy} onClick={() => void refreshContract()}>{contractBusy ? "Checking…" : "Re-check access"}</button>
            </div>

            {approvalId && <div className="approval-substance" style={{ marginTop: 16 }}>
              <strong>This action is waiting for your OK.</strong>
              <p>Operly will run exactly what you entered. You can approve it or choose “Don’t do it” and nothing will change.</p>
              <div className="row-actions"><button type="button" disabled={busy} onClick={() => void decideApproval(false)}>Don’t do it</button><button type="button" className="primary-button" disabled={busy} onClick={() => void decideApproval(true)}>Yes, do it</button></div>
            </div>}

            {runError && <div className="inline-error page-error">{runError}</div>}
            {run && <div className="approval-substance" style={{ marginTop: 16 }}><strong>{friendlyResult(run.result)}</strong><small>Run {run.run_id}</small><details><summary>See the returned data</summary><code>{JSON.stringify(run.result, null, 2)}</code></details><details><summary>See how Operly executed it</summary><code>{JSON.stringify(run.trace, null, 2)}</code></details></div>}

            <details style={{ marginTop: 18 }}>
              <summary>Technical details</summary>
              <div className="approval-substance-grid" style={{ marginTop: 12 }}>
                <span><small>Tool ID</small><strong>{selected.id}</strong></span>
                <span><small>Endpoint</small><strong>{selected.method} /api{selected.endpoint}</strong></span>
                <span><small>Provider</small><strong>{selected.provider_id}</strong></span>
                <span><small>Permission</small><strong>{selected.permissions.join(", ") || "None"}</strong></span>
                <span><small>Reversible</small><strong>{selected.reversible ? "Yes" : "No"}</strong></span>
              </div>
              <details><summary>Input contract</summary><code>{JSON.stringify(selected.input_schema, null, 2)}</code></details>
              <details><summary>Output contract</summary><code>{JSON.stringify(selected.output_schema, null, 2)}</code></details>
            </details>
          </> : <div className="empty-panel">Choose an action on the left.</div>}
        </article>
      </section>
    </>}
  </main>;
}

import { FormEvent, ReactNode, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";


type FieldType = "text" | "email" | "number" | "textarea" | "datetime-local" | "select" | "checkbox" | "reference";
type FieldDef = {
  key: string;
  label: string;
  type?: FieldType;
  required?: boolean;
  options?: string[];
  referenceEntity?: string;
  referenceLabel?: string;
  placeholder?: string;
};
type EntityDef = {
  entity: string;
  label: string;
  singular: string;
  columns: string[];
  fields: FieldDef[];
  readOnly?: boolean;
};
type ModuleInfo = {
  key: string;
  name: string;
  category: string;
  description: string;
  enabled: boolean;
  locked?: boolean;
  dependencies: string[];
  can_read: boolean;
  can_write: boolean;
  can_manage: boolean;
  entities: EntityDef[];
};
type WorkspaceContext = {
  workspace: { id: string; name: string; slug?: string | null; timezone: string; logo_url?: string | null };
  user: { id: string; email: string; display_name: string };
  role: string;
  permissions: string[];
  modules: ModuleInfo[];
};
type Summary = Record<string, number>;
type RecordPage = { items: Record<string, unknown>[]; total: number; limit: number; offset: number };
type Member = { user_id: string; display_name: string; email: string; role: string };
type RoleInfo = { key: string; name: string; system: boolean; permissions: string[] };
type RolesResponse = { roles: RoleInfo[]; known_permissions: string[] };
type Preset = { key: string; name: string; description: string; modules: string[] };
type Activity = { id: string; event_type: string; entity_type: string; entity_id?: string | null; summary: string; actor: string; created_at: string };
type Invite = { id: string; target_email?: string | null; role: string; status: string; expires_at: string; accepted_at?: string | null; created_at: string };
type CreatedInvite = Invite & { invite_url: string; token: string };

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function pathSection(pathname: string, workspaceId: string): string {
  const prefix = `/channels/${encodeURIComponent(workspaceId)}`;
  if (!pathname.startsWith(prefix)) return "dashboard";
  return pathname.slice(prefix.length).replace(/^\/+/, "").split("/", 1)[0] || "dashboard";
}

function workspacePath(workspaceId: string, section: string): string {
  return `/channels/${encodeURIComponent(workspaceId)}/${section}`;
}

function humanize(key: string): string {
  return key.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

const MONEY_KEYS = new Set([
  "price", "cost", "value", "total", "subtotal", "tax", "amount", "budget", "spent", "attributed_revenue",
  "minimum_order_value", "refund_amount", "fulfillment_cost", "unit_price", "unit_cost", "opening_balance", "debit", "credit",
  "hourly_rate", "acquisition_cost", "estimated_cost", "actual_cost", "grant_pipeline", "pipeline_value", "sales_total", "invoice_total",
  "expenses_total", "incoming_payments", "net_operating",
]);

function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") {
    if (key === "tax_rate") return `${value}%`;
    if (MONEY_KEYS.has(key)) return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value);
    return new Intl.NumberFormat(undefined, { maximumFractionDigits: 2 }).format(value);
  }
  const text = String(value);
  if (key.endsWith("_at") || key.endsWith("_date") || key === "created_at" || key === "updated_at") {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  if (key.endsWith("_id") && text.length > 14) return `${text.slice(0, 8)}…${text.slice(-4)}`;
  return text;
}

function Modal({ children, onClose }: { children: ReactNode; onClose: () => void }) {
  return <div className="workspace-os-modal-backdrop" onMouseDown={onClose}><div className="workspace-os-modal" onMouseDown={(event) => event.stopPropagation()}>{children}</div></div>;
}

function ReferenceField({ field, defaultValue }: { field: FieldDef; defaultValue: unknown }) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!field.referenceEntity) return;
    api<RecordPage>(`/workspace-os/records/${field.referenceEntity}?limit=200&direction=asc`)
      .then((result) => setItems(result.items)).catch(() => setItems([]));
  }, [field.referenceEntity]);
  return <select name={field.key} defaultValue={String(defaultValue ?? "")} required={field.required}>
    <option value="">None</option>
    {items.map((item) => {
      const id = String(item.id ?? "");
      const label = String(item[field.referenceLabel || "name"] ?? item.id ?? "Record");
      return <option key={id} value={id}>{label}</option>;
    })}
  </select>;
}

function RecordEditor({ def, record, onClose, onSaved }: { def: EntityDef; record?: Record<string, unknown>; onClose: () => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    const payload: Record<string, unknown> = {};
    for (const field of def.fields) {
      if (field.type === "checkbox") payload[field.key] = form.get(field.key) === "on";
      else {
        const value = form.get(field.key);
        if (value !== null && String(value) !== "") payload[field.key] = value;
        else if (record && field.key in record) payload[field.key] = null;
      }
    }
    try {
      await api(`/workspace-os/records/${def.entity}${record?.id ? `/${String(record.id)}` : ""}`, {
        method: record?.id ? "PATCH" : "POST", body: JSON.stringify(payload),
      });
      onSaved(); onClose();
    } catch (caught) { setError(errorText(caught, `Could not save ${def.singular.toLowerCase()}`)); }
    finally { setBusy(false); }
  };
  return <Modal onClose={onClose}><form className="workspace-os-form" onSubmit={submit}>
    <div className="workspace-os-modal-heading"><div><span>{record ? "EDIT" : "NEW"}</span><h2>{record ? `Edit ${def.singular}` : `Create ${def.singular}`}</h2></div><button type="button" onClick={onClose}>×</button></div>
    <div className="workspace-os-form-grid">{def.fields.map((field) => {
      const value = record?.[field.key];
      return <label key={field.key} className={field.type === "textarea" ? "wide" : ""}><span>{field.label}</span>
        {field.type === "textarea" ? <textarea name={field.key} defaultValue={String(value ?? "")} rows={7} required={field.required} placeholder={field.placeholder} />
          : field.type === "select" ? <select name={field.key} defaultValue={String(value ?? field.options?.[0] ?? "")} required={field.required}>{field.options?.map((option) => <option key={option} value={option}>{humanize(option)}</option>)}</select>
          : field.type === "reference" ? <ReferenceField field={field} defaultValue={value} />
          : field.type === "checkbox" ? <input name={field.key} type="checkbox" defaultChecked={value === undefined ? true : Boolean(value)} />
          : <input name={field.key} type={field.type || "text"} defaultValue={String(value ?? "")} required={field.required} placeholder={field.placeholder} step={field.type === "number" ? "any" : undefined} />}
      </label>;
    })}</div>
    {error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button></div>
  </form></Modal>;
}

function RecordTable({ def, writable }: { def: EntityDef; writable: boolean }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 50, offset: 0 });
  const [loading, setLoading] = useState(true);
  const [q, setQ] = useState("");
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<Record<string, unknown> | null | undefined>(undefined);
  const [error, setError] = useState("");
  const load = useCallback(async (offset = 0) => {
    setLoading(true); setError("");
    try { setData(await api<RecordPage>(`/workspace-os/records/${def.entity}?limit=50&offset=${offset}&q=${encodeURIComponent(query)}`)); }
    catch (caught) { setError(errorText(caught, `Could not load ${def.label.toLowerCase()}`)); }
    finally { setLoading(false); }
  }, [def.entity, def.label, query]);
  useEffect(() => { void load(0); }, [load]);
  const remove = async (record: Record<string, unknown>) => {
    if (!record.id || !window.confirm(`Delete this ${def.singular.toLowerCase()}?`)) return;
    try { await api(`/workspace-os/records/${def.entity}/${String(record.id)}`, { method: "DELETE" }); await load(data.offset); }
    catch (caught) { setError(errorText(caught, "Could not delete record")); }
  };
  const colspan = def.columns.length + (writable && !def.readOnly ? 1 : 0);
  return <section className="workspace-os-records"><div className="workspace-os-toolbar">
    <div><h2>{def.label}</h2><span>{data.total} record{data.total === 1 ? "" : "s"}</span></div>
    <div className="workspace-os-toolbar-actions"><form onSubmit={(event) => { event.preventDefault(); setQuery(q.trim()); }}><input value={q} onChange={(event) => setQ(event.target.value)} placeholder={`Search ${def.label.toLowerCase()}…`} /><button>Search</button></form>
      {writable && !def.readOnly && <button className="primary" onClick={() => setEditing(null)}>+ New {def.singular}</button>}
    </div>
  </div>{error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-table-wrap"><table><thead><tr>{def.columns.map((column) => <th key={column}>{humanize(column)}</th>)}{writable && !def.readOnly && <th />}</tr></thead>
      <tbody>{loading ? <tr><td colSpan={colspan}>Loading…</td></tr> : data.items.length === 0 ? <tr><td className="workspace-os-empty" colSpan={colspan}>No records yet.</td></tr> : data.items.map((record) => <tr key={String(record.id)}>
        {def.columns.map((column) => <td key={column} title={String(record[column] ?? "")}>{formatValue(column, record[column])}</td>)}
        {writable && !def.readOnly && <td className="workspace-os-row-actions"><button onClick={() => setEditing(record)}>Edit</button><button onClick={() => void remove(record)}>Delete</button></td>}
      </tr>)}</tbody></table></div>
    {data.total > data.limit && <div className="workspace-os-pagination"><button disabled={data.offset === 0} onClick={() => void load(Math.max(0, data.offset - data.limit))}>Previous</button><span>{data.offset + 1}–{Math.min(data.total, data.offset + data.limit)} of {data.total}</span><button disabled={data.offset + data.limit >= data.total} onClick={() => void load(data.offset + data.limit)}>Next</button></div>}
    {editing !== undefined && <RecordEditor def={def} record={editing || undefined} onClose={() => setEditing(undefined)} onSaved={() => void load(data.offset)} />}
  </section>;
}

function InventoryStock({ writable }: { writable: boolean }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 200, offset: 0 });
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api<RecordPage>("/workspace-os/records/catalog?limit=200&direction=asc&sort=name")); }
    catch (caught) { setError(errorText(caught, "Could not load inventory")); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const adjust = async (itemId: string, amount: number, reason: string) => {
    try { await api(`/workspace-os/inventory/${itemId}/adjust`, { method: "POST", body: JSON.stringify({ quantity_change: amount, reason }) }); await load(); }
    catch (caught) { setError(errorText(caught, "Could not adjust inventory")); }
  };
  return <section className="workspace-os-records"><div className="workspace-os-toolbar"><div><h2>Stock</h2><span>{data.total} catalog items</span></div></div>{error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-inventory-grid">{data.items.map((item) => <article key={String(item.id)} className={Number(item.stock_qty) <= Number(item.reorder_level) ? "low" : ""}>
      <div><strong>{String(item.name ?? "Item")}</strong><span>{String(item.sku ?? "No SKU")}</span></div><div className="workspace-os-stock"><b>{Number(item.stock_qty || 0)}</b><small>in stock · reorder at {Number(item.reorder_level || 0)}</small></div>
      {writable && <InventoryAdjust onAdjust={(amount, reason) => adjust(String(item.id), amount, reason)} />}
    </article>)}</div>
  </section>;
}

function InventoryAdjust({ onAdjust }: { onAdjust: (amount: number, reason: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); const amount = Number(form.get("amount")); if (!amount) return;
    setBusy(true); await onAdjust(amount, String(form.get("reason") || "adjustment")); setBusy(false); event.currentTarget.reset();
  };
  return <form className="workspace-os-adjust" onSubmit={submit}><input name="amount" type="number" placeholder="± qty" required /><input name="reason" placeholder="reason" /><button disabled={busy}>{busy ? "…" : "Adjust"}</button></form>;
}

function Dashboard({ summary, modules }: { summary: Summary | null; modules: ModuleInfo[] }) {
  const [activity, setActivity] = useState<Activity[]>([]);
  useEffect(() => { api<Activity[]>("/workspace-os/activity?limit=12").then(setActivity).catch(() => setActivity([])); }, []);
  const cards: [string, string, boolean][] = [
    ["Open opportunities", "open_leads", false], ["Pipeline", "pipeline_value", true], ["Incoming payments", "incoming_payments", true],
    ["Net operating", "net_operating", true], ["Active projects", "active_projects", false], ["Open work orders", "open_work_orders", false],
    ["Open risks", "open_risks", false], ["Active research", "active_research_projects", false], ["Grant pipeline", "grant_pipeline", true],
    ["Low stock", "low_stock", false], ["Open tickets", "open_tickets", false], ["Upcoming appointments", "upcoming_appointments", false],
  ];
  return <div className="workspace-os-dashboard"><section className="workspace-os-hero"><span>WORKSPACE OVERVIEW</span><h1>One operating system for the whole organization.</h1><p>Run customer, commercial, financial, project, operational, people, compliance and research work from the same workspace authority boundary.</p></section>
    <div className="workspace-os-metrics">{cards.map(([label, key, money]) => <article key={key}><span>{label}</span><strong>{summary ? (money ? formatValue(key, summary[key] || 0) : formatValue(key, summary[key] || 0)) : "—"}</strong></article>)}</div>
    <div className="workspace-os-dashboard-columns"><section className="workspace-os-module-cards"><div className="workspace-os-section-title"><h2>Active tools</h2><span>Enable more in Settings</span></div><div>{modules.filter((module) => module.enabled && module.can_read && module.key !== "dashboard").map((module) => <a key={module.key} href={module.key}><strong>{module.name}</strong><span>{module.description}</span></a>)}</div></section>
      <section className="workspace-os-activity"><div className="workspace-os-section-title"><h2>Recent activity</h2><span>Workspace history</span></div>{activity.length === 0 ? <p>No activity yet.</p> : activity.map((item) => <article key={item.id}><div><strong>{item.summary}</strong><span>{item.actor}</span></div><time>{new Date(item.created_at).toLocaleString()}</time></article>)}</section>
    </div>
  </div>;
}

function ModulePage({ module }: { module: ModuleInfo }) {
  const inventoryTabs = module.key === "inventory" ? [{ entity: "stock", label: "Stock", singular: "Stock", columns: [], fields: [] } as EntityDef, ...module.entities] : module.entities;
  const [activeEntity, setActiveEntity] = useState(inventoryTabs[0]?.entity || "");
  useEffect(() => setActiveEntity((module.key === "inventory" ? "stock" : module.entities[0]?.entity) || ""), [module.key]);
  if (inventoryTabs.length === 0) return <section className="workspace-os-blank"><h1>{module.name}</h1><p>{module.description}</p></section>;
  const def = inventoryTabs.find((candidate) => candidate.entity === activeEntity) || inventoryTabs[0];
  return <div className="workspace-os-module-page"><header className="workspace-os-module-header"><div><span>{module.category.toUpperCase()}</span><h1>{module.name}</h1><p>{module.description}</p></div></header>
    <nav className="workspace-os-entity-tabs">{inventoryTabs.map((candidate) => <button key={candidate.entity} className={candidate.entity === def.entity ? "active" : ""} onClick={() => setActiveEntity(candidate.entity)}>{candidate.label}</button>)}</nav>
    {module.key === "inventory" && def.entity === "stock" ? <InventoryStock writable={module.can_write} /> : <RecordTable def={def} writable={module.can_write} />}
  </div>;
}

function GeneralSettings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const canManage = context.permissions.includes("workspace:settings:manage") || context.role === "owner";
  const [busy, setBusy] = useState(false); const [error, setError] = useState(""); const [saved, setSaved] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(""); setSaved(false);
    try { await api("/workspace-os/settings", { method: "PATCH", body: JSON.stringify({ name: form.get("name"), timezone: form.get("timezone"), logo_url: form.get("logo_url") }) }); await onReload(); setSaved(true); }
    catch (caught) { setError(errorText(caught, "Could not update workspace")); } finally { setBusy(false); }
  };
  return <form className="workspace-os-settings-card" onSubmit={submit}><h2>Workspace identity</h2><p>Name, timezone and visual identity are shared by everyone in this workspace.</p>
    <label><span>Name</span><input name="name" defaultValue={context.workspace.name} disabled={!canManage} required /></label>
    <label><span>Timezone</span><input name="timezone" defaultValue={context.workspace.timezone} disabled={!canManage} required /></label>
    <label><span>Logo URL</span><input name="logo_url" defaultValue={context.workspace.logo_url || ""} disabled={!canManage} placeholder="https://…" /></label>
    {error && <div className="workspace-os-error">{error}</div>}{saved && <div className="workspace-os-success">Workspace updated.</div>}
    {canManage && <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save workspace"}</button>}
  </form>;
}

function ModuleSettings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const [presets, setPresets] = useState<Preset[]>([]); const [busy, setBusy] = useState(""); const [error, setError] = useState("");
  useEffect(() => { api<Preset[]>("/workspace-os/presets").then(setPresets).catch(() => setPresets([])); }, []);
  const toggle = async (module: ModuleInfo) => {
    setBusy(module.key); setError(""); try { await api(`/workspace-os/modules/${module.key}`, { method: "PUT", body: JSON.stringify({ enabled: !module.enabled, configuration: {} }) }); await onReload(); }
    catch (caught) { setError(errorText(caught, "Could not change module")); } finally { setBusy(""); }
  };
  const apply = async (preset: Preset) => {
    if (!window.confirm(`Apply the ${preset.name} pack? Existing modules and data will be preserved.`)) return;
    setBusy(`preset:${preset.key}`); setError(""); try { await api(`/workspace-os/presets/${preset.key}/apply`, { method: "POST" }); await onReload(); }
    catch (caught) { setError(errorText(caught, "Could not apply workspace pack")); } finally { setBusy(""); }
  };
  return <div className="workspace-os-settings-stack"><section className="workspace-os-settings-card"><h2>Starter packs</h2><p>Starter packs only enable modules. They never delete data or create a separate kind of workspace.</p>
    <div className="workspace-os-presets">{presets.map((preset) => <article key={preset.key}><strong>{preset.name}</strong><p>{preset.description}</p><small>{preset.modules.length} modules</small><button disabled={!context.modules.some((m) => m.can_manage) || busy === `preset:${preset.key}`} onClick={() => void apply(preset)}>{busy === `preset:${preset.key}` ? "Applying…" : "Apply pack"}</button></article>)}</div>
  </section><section className="workspace-os-settings-card"><h2>Business modules</h2><p>Everything is installed once in Operly and activated per workspace. Dependencies are enabled automatically.</p>{error && <div className="workspace-os-error">{error}</div>}
    <div className="workspace-os-settings-modules">{context.modules.filter((module) => module.key !== "dashboard").map((module) => <article key={module.key}><div><strong>{module.name}</strong><span>{module.description}</span>{module.dependencies.length > 0 && <small>Requires {module.dependencies.join(", ")}</small>}</div><button className={module.enabled ? "on" : ""} disabled={Boolean(module.locked) || !module.can_manage || busy === module.key} onClick={() => void toggle(module)}>{busy === module.key ? "…" : module.enabled ? "Enabled" : "Enable"}</button></article>)}</div>
  </section></div>;
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return; }
  const area = document.createElement("textarea"); area.value = value; document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove();
}

function MembersSettings({ context }: { context: WorkspaceContext }) {
  const [members, setMembers] = useState<Member[]>([]); const [roles, setRoles] = useState<RoleInfo[]>([]); const [invites, setInvites] = useState<Invite[]>([]);
  const [createdInvite, setCreatedInvite] = useState<CreatedInvite | null>(null); const [copied, setCopied] = useState(false); const [error, setError] = useState("");
  const canManage = context.permissions.includes("workspace:members:manage") || context.role === "owner";
  const load = useCallback(async () => {
    try {
      const [memberRows, roleRows, inviteRows] = await Promise.all([api<Member[]>("/workspace-os/members"), api<RolesResponse>("/workspace-os/roles"), canManage ? api<Invite[]>("/workspace-os/invitations") : Promise.resolve([])]);
      setMembers(memberRows); setRoles(roleRows.roles); setInvites(inviteRows);
    } catch (caught) { setError(errorText(caught, "Could not load members")); }
  }, [canManage]);
  useEffect(() => { void load(); }, [load]);
  const add = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setError("");
    try { await api("/workspace-os/members", { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) }); event.currentTarget.reset(); await load(); }
    catch (caught) { setError(errorText(caught, "Could not add member")); }
  };
  const createInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setError(""); setCopied(false);
    try {
      const result = await api<CreatedInvite>("/workspace-os/invitations", { method: "POST", body: JSON.stringify({ email: form.get("email") || null, role: form.get("role"), ttl_days: Number(form.get("ttl_days") || 7) }) });
      setCreatedInvite(result); await load();
    } catch (caught) { setError(errorText(caught, "Could not create invite link")); }
  };
  const setRole = async (userId: string, role: string) => { try { await api(`/workspace-os/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not change role")); } };
  const remove = async (member: Member) => { if (!window.confirm(`Remove ${member.display_name || member.email} from this workspace?`)) return; try { await api(`/workspace-os/members/${member.user_id}`, { method: "DELETE" }); await load(); } catch (caught) { setError(errorText(caught, "Could not remove member")); } };
  const revoke = async (invite: Invite) => { if (!window.confirm("Revoke this invitation?")) return; try { await api(`/workspace-os/invitations/${invite.id}`, { method: "DELETE" }); await load(); } catch (caught) { setError(errorText(caught, "Could not revoke invitation")); } };
  return <div className="workspace-os-settings-stack"><section className="workspace-os-settings-card"><h2>Members & access</h2><p>Workspace membership is the authority boundary. The selected workspace plus the member's role determines what that session can read or change.</p>{error && <div className="workspace-os-error">{error}</div>}
    {canManage && <form className="workspace-os-member-add" onSubmit={add}><input name="email" type="email" placeholder="Existing Operly account email" required /><select name="role" defaultValue="employee">{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><button className="primary">Add member</button></form>}
    <div className="workspace-os-members">{members.map((member) => <article key={member.user_id}><div><strong>{member.display_name || member.email}</strong><span>{member.email}</span></div>{canManage ? <><select value={member.role} onChange={(event) => void setRole(member.user_id, event.target.value)}>{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><button onClick={() => void remove(member)}>Remove</button></> : <b>{humanize(member.role)}</b>}</article>)}</div>
  </section>{canManage && <section className="workspace-os-settings-card"><h2>Invite by link</h2><p>Create a copyable link for someone who does not have an Operly account yet. After they sign up or sign in, the invitation is accepted and their session enters this workspace directly. Leave email blank for a reusable-to-one-person generic link; specify an email to restrict who can accept it.</p>
    <form className="workspace-os-invite-create" onSubmit={createInvite}><input name="email" type="email" placeholder="Restrict to email (optional)" /><select name="role" defaultValue="employee">{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><select name="ttl_days" defaultValue="7"><option value="1">1 day</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></select><button className="primary">Create invite link</button></form>
    {createdInvite && <div className="workspace-os-invite-link"><input readOnly value={createdInvite.invite_url} onFocus={(event) => event.currentTarget.select()} /><button onClick={() => void copyText(createdInvite.invite_url).then(() => setCopied(true))}>{copied ? "Copied" : "Copy link"}</button></div>}
    <div className="workspace-os-invites">{invites.length === 0 ? <p>No invitations yet.</p> : invites.map((invite) => <article key={invite.id}><div><strong>{invite.target_email || "Anyone with this link"}</strong><span>{humanize(invite.role)} · {humanize(invite.status)} · expires {new Date(invite.expires_at).toLocaleString()}</span></div>{invite.status === "pending" && <button onClick={() => void revoke(invite)}>Revoke</button>}</article>)}</div>
  </section>}</div>;
}

function RolesSettings({ context }: { context: WorkspaceContext }) {
  const [data, setData] = useState<RolesResponse>({ roles: [], known_permissions: [] }); const [selected, setSelected] = useState(""); const [selectedPermissions, setSelectedPermissions] = useState<Set<string>>(new Set()); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const canManage = context.permissions.includes("workspace:roles:manage") || context.role === "owner";
  const load = useCallback(async () => { try { const result = await api<RolesResponse>("/workspace-os/roles"); setData(result); setSelected((value) => value || result.roles[0]?.key || ""); } catch (caught) { setError(errorText(caught, "Could not load roles")); } }, []);
  useEffect(() => { void load(); }, [load]);
  const role = data.roles.find((candidate) => candidate.key === selected);
  const permissionKey = role?.permissions.join("|") || "";
  useEffect(() => { setSelectedPermissions(new Set(role?.permissions || [])); }, [role?.key, permissionKey]);
  const save = async () => { if (!role) return; setBusy(true); setError(""); try { await api(`/workspace-os/roles/${role.key}`, { method: "PUT", body: JSON.stringify({ name: role.name, permissions: [...selectedPermissions] }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not save role")); } finally { setBusy(false); } };
  const create = async (event: FormEvent<HTMLFormElement>) => { event.preventDefault(); const form = new FormData(event.currentTarget); const name = String(form.get("name") || "").trim(); const key = name.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, ""); if (!key) return; try { await api(`/workspace-os/roles/${key}`, { method: "PUT", body: JSON.stringify({ name, permissions: ["workspace:read"] }) }); await load(); setSelected(key); event.currentTarget.reset(); } catch (caught) { setError(errorText(caught, "Could not create role")); } };
  const groups = useMemo(() => { const result: Record<string, string[]> = {}; for (const permission of data.known_permissions) { const group = permission.split(":", 1)[0]; (result[group] ||= []).push(permission); } return result; }, [data.known_permissions]);
  return <div className="workspace-os-settings-card"><h2>Roles & permissions</h2><p>Built-in roles evolve with safe Operly defaults. Custom roles remain explicit so new modules never silently grant them authority.</p>{error && <div className="workspace-os-error">{error}</div>}
    {canManage && <form className="workspace-os-role-add" onSubmit={create}><input name="name" placeholder="New custom role" required /><button>Create role</button></form>}
    <div className="workspace-os-role-layout"><nav>{data.roles.map((item) => <button key={item.key} className={selected === item.key ? "active" : ""} onClick={() => setSelected(item.key)}><strong>{item.name}</strong><small>{item.system ? "Built-in" : "Custom"}</small></button>)}</nav>
      {role && <div className="workspace-os-permissions"><h3>{role.name}</h3>{Object.entries(groups).map(([group, permissions]) => <fieldset key={group}><legend>{humanize(group)}</legend>{permissions.map((permission) => <label key={permission}><input type="checkbox" checked={selectedPermissions.has(permission)} disabled={!canManage} onChange={(event) => setSelectedPermissions((current) => { const next = new Set(current); if (event.target.checked) next.add(permission); else next.delete(permission); return next; })} /><span>{permission}</span></label>)}</fieldset>)}{canManage && <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save permissions"}</button>}</div>}
    </div>
  </div>;
}

function Settings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const [tab, setTab] = useState("general");
  return <div className="workspace-os-settings"><header><span>WORKSPACE SETTINGS</span><h1>{context.workspace.name}</h1><p>Workspace identity, installed modules and member authority all use the same session-scoped boundary.</p></header>
    <nav className="workspace-os-settings-tabs">{[["general", "General"], ["modules", "Modules"], ["members", "Members"], ["roles", "Roles & permissions"]].map(([key, label]) => <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}</nav>
    {tab === "general" && <GeneralSettings context={context} onReload={onReload} />}{tab === "modules" && <ModuleSettings context={context} onReload={onReload} />}{tab === "members" && <MembersSettings context={context} />}{tab === "roles" && <RolesSettings context={context} />}
  </div>;
}

export function WorkspaceOSPanel({ workspaceId, pathname }: { workspaceId: string; pathname: string }) {
  const [context, setContext] = useState<WorkspaceContext | null>(null); const [summary, setSummary] = useState<Summary | null>(null); const [error, setError] = useState("");
  const section = pathSection(pathname, workspaceId);
  const reload = useCallback(async () => {
    setError("");
    try { const current = await api<WorkspaceContext>("/workspace-os/context"); if (current.workspace.id !== workspaceId) throw new Error("Workspace session is still switching"); setContext(current); setSummary(await api<Summary>("/workspace-os/summary")); }
    catch (caught) { setError(errorText(caught, "Could not open workspace")); }
  }, [workspaceId]);
  useEffect(() => { void reload(); }, [reload]);
  if (error && !context) return <div className="workspace-os-fatal"><h1>Workspace unavailable</h1><p>{error}</p><button onClick={() => void reload()}>Retry</button></div>;
  if (!context) return <div className="workspace-os-loading">Loading workspace…</div>;
  const activeModule = context.modules.find((module) => module.key === section);
  const readableModules = context.modules.filter((module) => module.enabled && module.can_read);
  const categories: [string, ModuleInfo[]][] = Object.entries(readableModules.reduce<Record<string, ModuleInfo[]>>((result, module) => { (result[module.category] ||= []).push(module); return result; }, {}));
  return <div className="workspace-os-layout"><aside className="workspace-os-sidebar"><div className="workspace-os-sidebar-head"><strong>{context.workspace.name}</strong><span>{humanize(context.role)}</span></div>
    <nav>{categories.map(([category, modules]) => <div className="workspace-os-nav-group" key={category}><small>{humanize(category)}</small>{modules.map((module) => <a key={module.key} className={section === module.key ? "active" : ""} href={workspacePath(workspaceId, module.key)}><span className="workspace-os-nav-dot" />{module.name}</a>)}</div>)}</nav>
    <a className={`workspace-os-settings-link ${section === "settings" ? "active" : ""}`} href={workspacePath(workspaceId, "settings")}>⚙ Workspace settings</a>
  </aside><div className="workspace-os-pane">{error && <div className="workspace-os-error workspace-os-top-error">{error}</div>}
    {section === "settings" ? <Settings context={context} onReload={reload} /> : section === "dashboard" ? <Dashboard summary={summary} modules={context.modules} />
      : activeModule && activeModule.enabled && activeModule.can_read ? <ModulePage module={activeModule} />
      : activeModule && !activeModule.enabled ? <section className="workspace-os-blank"><h1>{activeModule.name}</h1><p>This module is installed but not enabled for this workspace.</p>{activeModule.can_manage && <a className="primary-link" href={workspacePath(workspaceId, "settings")}>Enable in workspace settings</a>}</section>
      : <section className="workspace-os-blank"><h1>Module unavailable</h1><p>This role does not have access to that workspace module.</p></section>}
  </div></div>;
}

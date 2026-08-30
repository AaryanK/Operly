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
  configuration?: Record<string, unknown>;
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
type Activity = { id: string; event_type: string; entity_type: string; entity_id?: string | null; summary: string; actor: string; created_at: string };
type SearchResult = { id: string; entity: string; destination: string; type: string; title: string; subtitle: string };
type AttentionItem = { key: string; tone: "urgent" | "warning" | "normal"; destination: string; entity: string; title: string; detail: string; count: number };
type TodayItem = { kind: string; entity: string; id: string; title: string; when?: string | null; destination: string };
type AttentionResponse = { items: AttentionItem[]; today: TodayItem[] };
type Member = { user_id: string; display_name: string; email: string; role: string };
type RoleInfo = { key: string; name: string; system: boolean; permissions: string[] };
type RolesResponse = { roles: RoleInfo[]; known_permissions: string[] };
type Preset = { key: string; name: string; description: string; modules: string[] };
type Invite = { id: string; target_email?: string | null; role: string; status: string; expires_at: string; accepted_at?: string | null; created_at: string };
type CreatedInvite = Invite & { invite_url: string; token: string };
type CustomerSnapshot = {
  contact: Record<string, unknown>;
  summary: { lifetime_sales: number; outstanding: number; open_opportunities: number };
  leads: Array<{ id: string; title: string; stage: string; value: number; next_action_at?: string | null }>;
  orders: Array<{ id: string; status: string; total: number; created_at: string }>;
  invoices: Array<{ id: string; number: string; status: string; total: number; due_at?: string | null }>;
  interactions: Array<{ id: string; subject: string; type: string; channel: string; occurred_at: string }>;
};

type QuickAction = "sale" | "customer" | "invoice" | "payment" | "expense" | "task" | "appointment" | "product" | "supplier" | "purchase" | "note";

type BusinessProfile = {
  key: string;
  name: string;
  description: string;
  preset: string;
};

const BUSINESS_PROFILES: BusinessProfile[] = [
  { key: "retail", name: "Store / retail", description: "Sell products, manage stock, suppliers, customers and money.", preset: "ecommerce" },
  { key: "services", name: "Services / solo business", description: "Clients, appointments, projects, invoices, expenses and follow-ups.", preset: "professional-services" },
  { key: "ecommerce", name: "E-commerce / dropshipping", description: "Products, suppliers, orders, fulfillment, returns and marketing.", preset: "ecommerce" },
  { key: "hospitality", name: "Hotel / lodging / hospitality", description: "Customers, scheduling, operations, finance and service workflows.", preset: "small-business" },
  { key: "operations", name: "Operations / field work", description: "Projects, assets, work orders, purchasing, scheduling and compliance.", preset: "operations" },
  { key: "research", name: "Research group / lab", description: "Projects, experiments, samples, datasets, funding, assets and SOPs.", preset: "research-group" },
  { key: "agency", name: "Agency / studio", description: "Clients, campaigns, projects, time, sales, finance and team work.", preset: "agency" },
  { key: "general", name: "General business / organization", description: "Start with the universal operating toolkit and customize from there.", preset: "small-business" },
];

const CORE_FIELDS: Record<string, string[]> = {
  contacts: ["name", "phone", "email"],
  organizations: ["name", "account_type", "email", "phone"],
  leads: ["contact_id", "title", "stage", "value", "next_action_at"],
  interactions: ["contact_id", "interaction_type", "subject", "occurred_at"],
  catalog: ["name", "item_type", "price", "sku", "stock_qty"],
  expenses: ["vendor", "amount", "category", "incurred_at"],
  tasks: ["title", "due_at"],
  appointments: ["title", "starts_at", "contact_id"],
  suppliers: ["name", "email", "phone"],
  "purchase-orders": ["supplier_id", "reference", "expected_at"],
  documents: ["title", "content"],
  projects: ["name", "status", "owner", "due_at"],
  tickets: ["contact_id", "subject", "priority", "status"],
};

const DESTINATION_ENTITIES: Record<string, string[]> = {
  customers: ["contacts", "leads", "organizations", "interactions", "tickets"],
  work: ["tasks", "appointments", "projects", "milestones", "work-orders", "time-entries"],
  money: ["invoices", "payments", "expenses", "orders", "quotes", "budgets", "financial-accounts", "ledger"],
  products: ["catalog", "stock", "warehouses", "suppliers", "purchase-orders", "fulfillments", "returns"],
};

function errorText(error: unknown, fallback: string): string {
  return error instanceof Error ? error.message : fallback;
}

function humanize(key: string): string {
  return key.replaceAll("_", " ").replaceAll("-", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function workspacePath(workspaceId: string, section: string): string {
  return `/channels/${encodeURIComponent(workspaceId)}/${section}`;
}

function pathSection(pathname: string, workspaceId: string): string {
  const prefix = `/channels/${encodeURIComponent(workspaceId)}`;
  if (!pathname.startsWith(prefix)) return "home";
  const section = pathname.slice(prefix.length).replace(/^\/+/, "").split("/", 1)[0] || "home";
  return section === "dashboard" ? "home" : section;
}

function money(value: number): string {
  return new Intl.NumberFormat(undefined, { style: "currency", currency: "USD", maximumFractionDigits: 2 }).format(value || 0);
}

const MONEY_KEYS = new Set(["price", "cost", "value", "total", "subtotal", "tax", "amount", "budget", "spent", "unit_price", "unit_cost", "opening_balance", "debit", "credit", "hourly_rate", "acquisition_cost", "estimated_cost", "actual_cost"]);

function formatValue(key: string, value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "boolean") return value ? "Yes" : "No";
  if (typeof value === "number") return MONEY_KEYS.has(key) ? money(value) : new Intl.NumberFormat().format(value);
  const text = String(value);
  if (key.endsWith("_at") || key.endsWith("_date") || key === "created_at" || key === "updated_at") {
    const date = new Date(text);
    if (!Number.isNaN(date.getTime())) return date.toLocaleString();
  }
  if (key.endsWith("_id") && text.length > 14) return `${text.slice(0, 8)}…${text.slice(-4)}`;
  return text;
}

function moduleByKey(context: WorkspaceContext, key: string): ModuleInfo | undefined {
  return context.modules.find((module) => module.key === key);
}

function entityByKey(context: WorkspaceContext, entityKey: string): { module: ModuleInfo; def: EntityDef } | null {
  for (const module of context.modules) {
    const def = module.entities?.find((candidate) => candidate.entity === entityKey);
    if (def) return { module, def };
  }
  return null;
}

function availableEntity(context: WorkspaceContext, entityKey: string): boolean {
  if (entityKey === "stock") {
    const inventory = moduleByKey(context, "inventory");
    return Boolean(inventory?.enabled && inventory.can_read);
  }
  const found = entityByKey(context, entityKey);
  return Boolean(found?.module.enabled && found.module.can_read);
}

function Modal({ children, onClose, wide = false }: { children: ReactNode; onClose: () => void; wide?: boolean }) {
  return <div className="human-modal-backdrop" onMouseDown={onClose}><div className={`human-modal ${wide ? "wide" : ""}`} onMouseDown={(event) => event.stopPropagation()}>{children}</div></div>;
}

function ReferenceField({ field, defaultValue }: { field: FieldDef; defaultValue: unknown }) {
  const [items, setItems] = useState<Record<string, unknown>[]>([]);
  useEffect(() => {
    if (!field.referenceEntity) return;
    api<RecordPage>(`/workspace-os/records/${field.referenceEntity}?limit=200&direction=asc`)
      .then((result) => setItems(result.items))
      .catch(() => setItems([]));
  }, [field.referenceEntity]);
  return <select name={field.key} defaultValue={String(defaultValue ?? "")} required={field.required}>
    <option value="">None</option>
    {items.map((item) => {
      const id = String(item.id ?? "");
      const label = String(item[field.referenceLabel || "name"] ?? item.title ?? item.number ?? item.id ?? "Record");
      return <option key={id} value={id}>{label}</option>;
    })}
  </select>;
}

function FieldControl({ field, value }: { field: FieldDef; value: unknown }) {
  if (field.type === "textarea") return <textarea name={field.key} defaultValue={String(value ?? "")} rows={5} required={field.required} placeholder={field.placeholder} />;
  if (field.type === "select") return <select name={field.key} defaultValue={String(value ?? field.options?.[0] ?? "")} required={field.required}>{field.options?.map((option) => <option key={option} value={option}>{humanize(option)}</option>)}</select>;
  if (field.type === "reference") return <ReferenceField field={field} defaultValue={value} />;
  if (field.type === "checkbox") return <input name={field.key} type="checkbox" defaultChecked={value === undefined ? true : Boolean(value)} />;
  return <input name={field.key} type={field.type || "text"} defaultValue={String(value ?? "")} required={field.required} placeholder={field.placeholder} step={field.type === "number" ? "any" : undefined} />;
}

function RecordEditor({ def, record, coreKeys, onClose, onSaved }: { def: EntityDef; record?: Record<string, unknown>; coreKeys?: string[]; onClose: () => void; onSaved: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const preferred = coreKeys || CORE_FIELDS[def.entity] || def.fields.filter((field) => field.required).map((field) => field.key);
  const core = def.fields.filter((field, index) => preferred.includes(field.key) || (preferred.length === 0 && index < 4));
  const extra = def.fields.filter((field) => !core.includes(field));

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
        method: record?.id ? "PATCH" : "POST",
        body: JSON.stringify(payload),
      });
      onSaved(); onClose();
    } catch (caught) {
      setError(errorText(caught, `Could not save ${def.singular.toLowerCase()}`));
    } finally {
      setBusy(false);
    }
  };

  const renderFields = (fields: FieldDef[]) => <div className="human-form-grid">{fields.map((field) => <label key={field.key} className={field.type === "textarea" ? "wide" : ""}><span>{field.label}</span><FieldControl field={field} value={record?.[field.key]} /></label>)}</div>;

  return <Modal onClose={onClose}><form className="human-form" onSubmit={submit}>
    <header className="human-modal-heading"><div><small>{record ? "EDIT" : "ADD"}</small><h2>{record ? `Edit ${def.singular}` : `Add ${def.singular}`}</h2></div><button type="button" onClick={onClose}>×</button></header>
    {renderFields(core)}
    {extra.length > 0 && <details className="human-more-fields"><summary>More details</summary>{renderFields(extra)}</details>}
    {error && <div className="human-error">{error}</div>}
    <footer className="human-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button></footer>
  </form></Modal>;
}

function HumanRecordList({ def, writable, initialQuery = "", onOpen }: { def: EntityDef; writable: boolean; initialQuery?: string; onOpen?: (record: Record<string, unknown>) => void }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 50, offset: 0 });
  const [loading, setLoading] = useState(true);
  const [query, setQuery] = useState(initialQuery);
  const [input, setInput] = useState(initialQuery);
  const [editing, setEditing] = useState<Record<string, unknown> | null | undefined>(undefined);
  const [error, setError] = useState("");

  const load = useCallback(async (offset = 0) => {
    setLoading(true); setError("");
    try {
      setData(await api<RecordPage>(`/workspace-os/records/${def.entity}?limit=50&offset=${offset}&q=${encodeURIComponent(query)}`));
    } catch (caught) {
      setError(errorText(caught, `Could not load ${def.label.toLowerCase()}`));
    } finally {
      setLoading(false);
    }
  }, [def.entity, def.label, query]);
  useEffect(() => { void load(0); }, [load]);

  const remove = async (record: Record<string, unknown>) => {
    if (!record.id || !window.confirm(`Delete this ${def.singular.toLowerCase()}?`)) return;
    try {
      await api(`/workspace-os/records/${def.entity}/${String(record.id)}`, { method: "DELETE" });
      await load(data.offset);
    } catch (caught) {
      setError(errorText(caught, "Could not delete record"));
    }
  };

  return <section className="human-record-list">
    <div className="human-list-toolbar">
      <div><h2>{def.label}</h2><span>{data.total} total</span></div>
      <div className="human-list-actions">
        <form onSubmit={(event) => { event.preventDefault(); setQuery(input.trim()); }}><input value={input} onChange={(event) => setInput(event.target.value)} placeholder={`Search ${def.label.toLowerCase()}…`} /></form>
        {writable && !def.readOnly && <button className="primary" onClick={() => setEditing(null)}>+ Add</button>}
      </div>
    </div>
    {error && <div className="human-error">{error}</div>}
    <div className="human-record-cards">
      {loading ? <div className="human-empty">Loading…</div> : data.items.length === 0 ? <div className="human-empty">Nothing here yet. {writable && !def.readOnly ? `Add your first ${def.singular.toLowerCase()}.` : ""}</div> : data.items.map((record) => {
        const titleKey = def.columns[0] || "id";
        return <article key={String(record.id)} className={onOpen ? "clickable" : ""} onClick={() => onOpen?.(record)}>
          <div className="human-record-main"><strong>{formatValue(titleKey, record[titleKey])}</strong><span>{def.singular}</span></div>
          <div className="human-record-meta">{def.columns.slice(1, 5).map((column) => <span key={column}><small>{humanize(column)}</small>{formatValue(column, record[column])}</span>)}</div>
          {writable && !def.readOnly && <div className="human-record-actions" onClick={(event) => event.stopPropagation()}><button onClick={() => setEditing(record)}>Edit</button><button onClick={() => void remove(record)}>Delete</button></div>}
        </article>;
      })}
    </div>
    {data.total > data.limit && <div className="human-pagination"><button disabled={data.offset === 0} onClick={() => void load(Math.max(0, data.offset - data.limit))}>Previous</button><span>{data.offset + 1}–{Math.min(data.total, data.offset + data.limit)} of {data.total}</span><button disabled={data.offset + data.limit >= data.total} onClick={() => void load(data.offset + data.limit)}>Next</button></div>}
    {editing !== undefined && <RecordEditor def={def} record={editing || undefined} onClose={() => setEditing(undefined)} onSaved={() => void load(data.offset)} />}
  </section>;
}

function InventoryStock({ writable }: { writable: boolean }) {
  const [data, setData] = useState<RecordPage>({ items: [], total: 0, limit: 200, offset: 0 });
  const [error, setError] = useState("");
  const load = useCallback(async () => {
    try { setData(await api<RecordPage>("/workspace-os/records/catalog?limit=200&direction=asc&sort=name")); }
    catch (caught) { setError(errorText(caught, "Could not load stock")); }
  }, []);
  useEffect(() => { void load(); }, [load]);
  const adjust = async (itemId: string, amount: number, reason: string) => {
    try {
      await api(`/workspace-os/inventory/${itemId}/adjust`, { method: "POST", body: JSON.stringify({ quantity_change: amount, reason }) });
      await load();
    } catch (caught) { setError(errorText(caught, "Could not adjust stock")); }
  };
  return <section className="human-record-list"><div className="human-list-toolbar"><div><h2>Stock</h2><span>{data.total} items</span></div></div>{error && <div className="human-error">{error}</div>}
    <div className="human-stock-grid">{data.items.map((item) => {
      const low = Number(item.stock_qty || 0) <= Number(item.reorder_level || 0) && String(item.item_type) === "product";
      return <article key={String(item.id)} className={low ? "low" : ""}><div><strong>{String(item.name || "Item")}</strong><span>{String(item.sku || "No SKU")}</span></div><b>{Number(item.stock_qty || 0)}</b><small>{low ? "Low stock" : `Reorder at ${Number(item.reorder_level || 0)}`}</small>{writable && <StockAdjust onAdjust={(amount, reason) => adjust(String(item.id), amount, reason)} />}</article>;
    })}</div>
  </section>;
}

function StockAdjust({ onAdjust }: { onAdjust: (amount: number, reason: string) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const amount = Number(form.get("amount"));
    if (!amount) return;
    setBusy(true);
    await onAdjust(amount, String(form.get("reason") || "adjustment"));
    setBusy(false);
    event.currentTarget.reset();
  };
  return <form className="human-stock-adjust" onSubmit={submit}><input name="amount" type="number" placeholder="± qty" required /><input name="reason" placeholder="Reason" /><button disabled={busy}>{busy ? "…" : "Adjust"}</button></form>;
}

function GlobalSearch({ workspaceId }: { workspaceId: string }) {
  const [query, setQuery] = useState("");
  const [results, setResults] = useState<SearchResult[]>([]);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    const value = query.trim();
    if (value.length < 2) { setResults([]); setLoading(false); return; }
    setLoading(true);
    const timer = window.setTimeout(() => {
      api<{ items: SearchResult[] }>(`/workspace-simple/search?q=${encodeURIComponent(value)}&limit=18`)
        .then((result) => setResults(result.items))
        .catch(() => setResults([]))
        .finally(() => setLoading(false));
    }, 220);
    return () => window.clearTimeout(timer);
  }, [query]);

  const open = (result: SearchResult) => {
    window.location.assign(`${workspacePath(workspaceId, result.destination)}?entity=${encodeURIComponent(result.entity)}&q=${encodeURIComponent(result.title)}`);
  };

  return <div className="human-search"><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search customers, invoices, products, projects…" aria-label="Search workspace" />
    {(query.trim().length >= 2) && <div className="human-search-results">{loading ? <div>Searching…</div> : results.length === 0 ? <div>No matches</div> : results.map((result) => <button key={`${result.entity}:${result.id}`} onClick={() => open(result)}><span><strong>{result.title}</strong><small>{result.subtitle || result.type}</small></span><b>{result.type}</b></button>)}</div>}
  </div>;
}

function SaleModal({ initialContactId, onClose, onSaved }: { initialContactId?: string; onClose: () => void; onSaved: (message: string) => void }) {
  const [contacts, setContacts] = useState<Record<string, unknown>[]>([]);
  const [catalog, setCatalog] = useState<Record<string, unknown>[]>([]);
  const [lines, setLines] = useState<Array<{ catalog_item_id: string; description: string; quantity: number; unit_price: number }>>([{ catalog_item_id: "", description: "", quantity: 1, unit_price: 0 }]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  useEffect(() => {
    Promise.all([
      api<RecordPage>("/workspace-os/records/contacts?limit=200&direction=asc").catch(() => ({ items: [], total: 0, limit: 200, offset: 0 })),
      api<RecordPage>("/workspace-os/records/catalog?limit=200&direction=asc&sort=name").catch(() => ({ items: [], total: 0, limit: 200, offset: 0 })),
    ]).then(([contactPage, catalogPage]) => { setContacts(contactPage.items); setCatalog(catalogPage.items); });
  }, []);

  const chooseItem = (index: number, id: string) => {
    const item = catalog.find((candidate) => String(candidate.id) === id);
    setLines((current) => current.map((line, lineIndex) => lineIndex === index ? {
      ...line,
      catalog_item_id: id,
      description: item ? String(item.name || "") : line.description,
      unit_price: item ? Number(item.price || 0) : line.unit_price,
    } : line));
  };

  const total = lines.reduce((sum, line) => sum + Number(line.quantity || 0) * Number(line.unit_price || 0), 0);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError("");
    const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ total: number; invoice_number?: string | null }>("/workspace-simple/sales", {
        method: "POST",
        body: JSON.stringify({
          contact_id: form.get("contact_id") || null,
          payment_method: form.get("payment_method"),
          due_days: Number(form.get("due_days") || 30),
          notes: form.get("notes") || "",
          items: lines.map((line) => ({ ...line, catalog_item_id: line.catalog_item_id || null })),
        }),
      });
      onSaved(result.invoice_number ? `Sale saved and ${result.invoice_number} created.` : `Sale completed for ${money(result.total)}.`);
      onClose();
    } catch (caught) { setError(errorText(caught, "Could not complete sale")); }
    finally { setBusy(false); }
  };

  return <Modal onClose={onClose} wide><form className="human-form human-sale" onSubmit={submit}><header className="human-modal-heading"><div><small>SELL</small><h2>New sale</h2><p>One screen. Operly creates the order, line items, payment or invoice, and stock movement behind the scenes.</p></div><button type="button" onClick={onClose}>×</button></header>
    <label className="human-single-field"><span>Customer <small>optional</small></span><select name="contact_id" defaultValue={initialContactId || ""}><option value="">Walk-in / no customer</option>{contacts.map((contact) => <option key={String(contact.id)} value={String(contact.id)}>{String(contact.name || contact.email || "Customer")}</option>)}</select></label>
    <div className="human-sale-lines"><div className="human-sale-line-head"><strong>Items</strong><button type="button" onClick={() => setLines((current) => [...current, { catalog_item_id: "", description: "", quantity: 1, unit_price: 0 }])}>+ Add item</button></div>
      {lines.map((line, index) => <div className="human-sale-line" key={index}><select value={line.catalog_item_id} onChange={(event) => chooseItem(index, event.target.value)}><option value="">Custom item</option>{catalog.map((item) => <option key={String(item.id)} value={String(item.id)}>{String(item.name)} · {money(Number(item.price || 0))}</option>)}</select><input value={line.description} onChange={(event) => setLines((current) => current.map((candidate, i) => i === index ? { ...candidate, description: event.target.value } : candidate))} placeholder="Description" required /><input type="number" min="1" step="1" value={line.quantity} onChange={(event) => setLines((current) => current.map((candidate, i) => i === index ? { ...candidate, quantity: Number(event.target.value) } : candidate))} /><input type="number" min="0" step="any" value={line.unit_price} onChange={(event) => setLines((current) => current.map((candidate, i) => i === index ? { ...candidate, unit_price: Number(event.target.value) } : candidate))} /><b>{money(line.quantity * line.unit_price)}</b>{lines.length > 1 && <button type="button" className="remove" onClick={() => setLines((current) => current.filter((_, i) => i !== index))}>×</button>}</div>)}
    </div>
    <div className="human-sale-bottom"><label><span>Payment</span><select name="payment_method" defaultValue="cash"><option value="cash">Cash</option><option value="card">Card</option><option value="bank">Bank / transfer</option><option value="other">Other</option><option value="pay_later">Pay later / invoice</option></select></label><label><span>Invoice due in</span><select name="due_days" defaultValue="30"><option value="0">Today</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option></select></label><label className="wide"><span>Note <small>optional</small></span><input name="notes" /></label><div className="human-sale-total"><span>Total</span><strong>{money(total)}</strong></div></div>
    {error && <div className="human-error">{error}</div>}<footer className="human-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Completing…" : "Complete sale"}</button></footer>
  </form></Modal>;
}

function InvoiceModal({ initialContactId, onClose, onSaved }: { initialContactId?: string; onClose: () => void; onSaved: (message: string) => void }) {
  const [contacts, setContacts] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { api<RecordPage>("/workspace-os/records/contacts?limit=200&direction=asc").then((page) => setContacts(page.items)).catch(() => setContacts([])); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ number: string; total: number }>("/workspace-simple/invoices", { method: "POST", body: JSON.stringify({ contact_id: form.get("contact_id") || null, description: form.get("description"), amount: Number(form.get("amount")), currency: "USD", due_days: Number(form.get("due_days") || 30), notes: form.get("notes") || "" }) });
      onSaved(`${result.number} created for ${money(result.total)}.`); onClose();
    } catch (caught) { setError(errorText(caught, "Could not create invoice")); } finally { setBusy(false); }
  };
  return <Modal onClose={onClose}><form className="human-form" onSubmit={submit}><header className="human-modal-heading"><div><small>GET PAID</small><h2>Create invoice</h2></div><button type="button" onClick={onClose}>×</button></header><div className="human-form-grid"><label><span>Customer</span><select name="contact_id" defaultValue={initialContactId || ""}><option value="">No customer</option>{contacts.map((contact) => <option key={String(contact.id)} value={String(contact.id)}>{String(contact.name || "Customer")}</option>)}</select></label><label><span>Amount</span><input name="amount" type="number" min="0" step="any" required /></label><label className="wide"><span>What is this for?</span><input name="description" required /></label><label><span>Due</span><select name="due_days" defaultValue="30"><option value="0">Today</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option><option value="60">60 days</option></select></label><label className="wide"><span>Note <small>optional</small></span><textarea name="notes" rows={4} /></label></div>{error && <div className="human-error">{error}</div>}<footer className="human-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Creating…" : "Create invoice"}</button></footer></form></Modal>;
}

function PaymentModal({ onClose, onSaved }: { onClose: () => void; onSaved: (message: string) => void }) {
  const [invoices, setInvoices] = useState<Record<string, unknown>[]>([]);
  const [busy, setBusy] = useState(false); const [error, setError] = useState("");
  useEffect(() => { api<RecordPage>("/workspace-os/records/invoices?limit=200&direction=desc").then((page) => setInvoices(page.items.filter((invoice) => !["paid", "void", "draft"].includes(String(invoice.status)))).catch(() => setInvoices([])); }, []);
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); setBusy(true); setError(""); const form = new FormData(event.currentTarget);
    try {
      const result = await api<{ invoice_status: string }>("/workspace-simple/payments", { method: "POST", body: JSON.stringify({ invoice_id: form.get("invoice_id"), amount: Number(form.get("amount")), method: form.get("method"), reference: form.get("reference") || null, notes: form.get("notes") || "" }) });
      onSaved(`Payment recorded. Invoice is now ${humanize(result.invoice_status)}.`); onClose();
    } catch (caught) { setError(errorText(caught, "Could not record payment")); } finally { setBusy(false); }
  };
  return <Modal onClose={onClose}><form className="human-form" onSubmit={submit}><header className="human-modal-heading"><div><small>MONEY IN</small><h2>Record payment</h2></div><button type="button" onClick={onClose}>×</button></header><div className="human-form-grid"><label className="wide"><span>Invoice</span><select name="invoice_id" required><option value="">Choose invoice</option>{invoices.map((invoice) => <option key={String(invoice.id)} value={String(invoice.id)}>{String(invoice.number)} · {money(Number(invoice.total || 0))} · {humanize(String(invoice.status))}</option>)}</select></label><label><span>Amount</span><input name="amount" type="number" min="0" step="any" required /></label><label><span>Method</span><select name="method" defaultValue="cash"><option value="cash">Cash</option><option value="card">Card</option><option value="bank">Bank / transfer</option><option value="check">Check</option><option value="other">Other</option></select></label><label><span>Reference <small>optional</small></span><input name="reference" /></label><label className="wide"><span>Note <small>optional</small></span><textarea name="notes" rows={3} /></label></div>{error && <div className="human-error">{error}</div>}<footer className="human-form-actions"><button type="button" onClick={onClose}>Cancel</button><button className="primary" disabled={busy}>{busy ? "Saving…" : "Record payment"}</button></footer></form></Modal>;
}

function QuickAdd({ context, onSaved, initialAction, initialContactId, onClosed }: { context: WorkspaceContext; onSaved: (message: string) => void; initialAction?: QuickAction | null; initialContactId?: string; onClosed?: () => void }) {
  const [open, setOpen] = useState(Boolean(initialAction));
  const [action, setAction] = useState<QuickAction | null>(initialAction || null);
  useEffect(() => { if (initialAction) { setAction(initialAction); setOpen(true); } }, [initialAction, initialContactId]);
  const close = () => { setOpen(false); setAction(null); onClosed?.(); };

  const actionInfo: Array<{ key: QuickAction; label: string; hint: string; entity?: string; module?: string }> = [
    { key: "sale", label: "Sale", hint: "Sell now or invoice later", module: "sales" },
    { key: "customer", label: "Customer", hint: "Add a person or customer", entity: "contacts" },
    { key: "invoice", label: "Invoice", hint: "Ask a customer to pay", module: "finance" },
    { key: "payment", label: "Payment", hint: "Record money received", module: "finance" },
    { key: "expense", label: "Expense", hint: "Record money spent", entity: "expenses" },
    { key: "task", label: "Task", hint: "Something that needs doing", entity: "tasks" },
    { key: "appointment", label: "Appointment", hint: "Book time with someone", entity: "appointments" },
    { key: "product", label: "Product / service", hint: "Something you sell", entity: "catalog" },
    { key: "supplier", label: "Supplier", hint: "Someone you buy from", entity: "suppliers" },
    { key: "purchase", label: "Purchase order", hint: "Order from a supplier", entity: "purchase-orders" },
    { key: "note", label: "Note / SOP", hint: "Save business knowledge", entity: "documents" },
  ];

  const available = (item: typeof actionInfo[number]) => {
    if (item.entity) {
      const found = entityByKey(context, item.entity);
      return Boolean(found?.module.enabled && found.module.can_write && !found.def.readOnly);
    }
    const module = moduleByKey(context, item.module || "");
    return Boolean(module?.enabled && module.can_write);
  };

  const generic = action ? actionInfo.find((item) => item.key === action)?.entity : undefined;
  const genericFound = generic ? entityByKey(context, generic) : null;

  return <>{!initialAction && <button className="human-add-button" onClick={() => setOpen(true)}>+ Add</button>}
    {open && !action && <Modal onClose={close}><div className="human-quick-menu"><header className="human-modal-heading"><div><small>QUICK ADD</small><h2>What do you want to do?</h2></div><button onClick={close}>×</button></header><div>{actionInfo.filter(available).map((item) => <button key={item.key} onClick={() => setAction(item.key)}><span><strong>{item.label}</strong><small>{item.hint}</small></span><b>→</b></button>)}</div></div></Modal>}
    {action === "sale" && <SaleModal initialContactId={initialContactId} onClose={close} onSaved={onSaved} />}
    {action === "invoice" && <InvoiceModal initialContactId={initialContactId} onClose={close} onSaved={onSaved} />}
    {action === "payment" && <PaymentModal onClose={close} onSaved={onSaved} />}
    {genericFound && <RecordEditor def={genericFound.def} coreKeys={CORE_FIELDS[genericFound.def.entity]} onClose={close} onSaved={() => onSaved(`${genericFound.def.singular} added.`)} />}
  </>;
}

function CustomerDetail({ contactId, context, onClose, onQuick }: { contactId: string; context: WorkspaceContext; onClose: () => void; onQuick: (action: QuickAction, contactId: string) => void }) {
  const [data, setData] = useState<CustomerSnapshot | null>(null);
  const [error, setError] = useState("");
  useEffect(() => { api<CustomerSnapshot>(`/workspace-simple/customers/${contactId}`).then(setData).catch((caught) => setError(errorText(caught, "Could not open customer"))); }, [contactId]);
  const sales = moduleByKey(context, "sales"); const finance = moduleByKey(context, "finance");
  return <Modal onClose={onClose} wide><div className="human-customer-detail"><header className="human-modal-heading"><div><small>CUSTOMER</small><h2>{String(data?.contact.name || "Customer")}</h2><p>{[data?.contact.company, data?.contact.email, data?.contact.phone].filter(Boolean).join(" · ")}</p></div><button onClick={onClose}>×</button></header>{error && <div className="human-error">{error}</div>}{!data ? <div className="human-empty">Loading customer…</div> : <><div className="human-customer-actions">{sales?.enabled && sales.can_write && <button className="primary" onClick={() => onQuick("sale", contactId)}>New sale</button>}{finance?.enabled && finance.can_write && <button onClick={() => onQuick("invoice", contactId)}>Create invoice</button>}<a href={data.contact.email ? `mailto:${String(data.contact.email)}` : undefined}>Email</a><a href={data.contact.phone ? `tel:${String(data.contact.phone)}` : undefined}>Call</a></div><div className="human-customer-metrics"><article><span>Sales</span><strong>{money(data.summary.lifetime_sales)}</strong></article><article><span>Outstanding</span><strong>{money(data.summary.outstanding)}</strong></article><article><span>Open opportunities</span><strong>{data.summary.open_opportunities}</strong></article></div><div className="human-customer-columns"><section><h3>Recent activity</h3>{data.interactions.length === 0 ? <p>No interactions yet.</p> : data.interactions.map((item) => <article key={item.id}><strong>{item.subject}</strong><span>{humanize(item.type)} · {new Date(item.occurred_at).toLocaleString()}</span></article>)}</section><section><h3>Open business</h3>{data.invoices.filter((invoice) => !["paid", "void"].includes(invoice.status)).map((invoice) => <article key={invoice.id}><strong>{invoice.number}</strong><span>{money(invoice.total)} · {humanize(invoice.status)}</span></article>)}{data.leads.filter((lead) => !["won", "lost"].includes(lead.stage)).map((lead) => <article key={lead.id}><strong>{lead.title}</strong><span>{money(lead.value)} · {humanize(lead.stage)}</span></article>)}</section></div></>}</div></Modal>;
}

function Home({ context, summary, workspaceId, onQuick, onSetup, refreshKey }: { context: WorkspaceContext; summary: Summary | null; workspaceId: string; onQuick: (action: QuickAction) => void; onSetup: () => void; refreshKey: number }) {
  const [attention, setAttention] = useState<AttentionResponse>({ items: [], today: [] });
  const [activity, setActivity] = useState<Activity[]>([]);
  useEffect(() => {
    Promise.all([
      api<AttentionResponse>("/workspace-simple/attention").catch(() => ({ items: [], today: [] })),
      api<Activity[]>("/workspace-os/activity?limit=10").catch(() => []),
    ]).then(([attentionResult, activityResult]) => { setAttention(attentionResult); setActivity(activityResult); });
  }, [refreshKey]);

  const dashboard = moduleByKey(context, "dashboard");
  const businessType = String(dashboard?.configuration?.business_type || "");
  const canSetup = dashboard?.can_manage || context.role === "owner";
  const greetingName = context.user.display_name?.split(" ")[0] || "there";
  const quick: QuickAction[] = ["sale", "customer", "expense", "task", "appointment", "product"];
  const quickLabels: Record<QuickAction, string> = { sale: "Sale", customer: "Customer", invoice: "Invoice", payment: "Payment", expense: "Expense", task: "Task", appointment: "Appointment", product: "Product", supplier: "Supplier", purchase: "Purchase", note: "Note" };
  const usableQuick = quick.filter((action) => {
    if (action === "sale") return Boolean(moduleByKey(context, "sales")?.enabled && moduleByKey(context, "sales")?.can_write);
    const entity = { customer: "contacts", expense: "expenses", task: "tasks", appointment: "appointments", product: "catalog" }[action];
    if (!entity) return false;
    const found = entityByKey(context, entity);
    return Boolean(found?.module.enabled && found.module.can_write);
  });

  return <main className="human-home"><section className="human-home-hero"><div><span>TODAY</span><h1>Hi {greetingName}. What needs your attention?</h1><p>{attention.items.length === 0 ? "Nothing urgent is waiting. You can keep moving the business forward." : `${attention.items.length} area${attention.items.length === 1 ? "" : "s"} need attention right now.`}</p></div><div className="human-quick-strip">{usableQuick.map((action) => <button key={action} onClick={() => onQuick(action)}>+ {quickLabels[action]}</button>)}</div></section>
    {!businessType && canSetup && <button className="human-setup-banner" onClick={onSetup}><span><strong>Make Operly fit your work</strong><small>Tell us what kind of organization this is. We’ll simplify the workspace around it.</small></span><b>Set up workspace →</b></button>}
    <section className="human-attention"><div className="human-section-heading"><h2>Needs attention</h2><span>Operly brings exceptions to you instead of making you hunt for them.</span></div>{attention.items.length === 0 ? <div className="human-clear-card"><strong>All clear</strong><span>No overdue or urgent operating items right now.</span></div> : <div className="human-attention-grid">{attention.items.map((item) => <a key={item.key} className={item.tone} href={`${workspacePath(workspaceId, item.destination)}?entity=${encodeURIComponent(item.entity)}`}><div><strong>{item.title}</strong><span>{item.detail}</span></div><b>→</b></a>)}</div>}</section>
    <div className="human-home-columns"><section><div className="human-section-heading"><h2>Today</h2><span>Tasks and appointments in the next 24 hours.</span></div><div className="human-agenda">{attention.today.length === 0 ? <div className="human-empty">Your near-term agenda is clear.</div> : attention.today.map((item) => <a key={`${item.kind}:${item.id}`} href={`${workspacePath(workspaceId, item.destination)}?entity=${encodeURIComponent(item.entity)}`}><time>{item.when ? new Date(item.when).toLocaleTimeString([], { hour: "numeric", minute: "2-digit" }) : "Any time"}</time><span><strong>{item.title}</strong><small>{humanize(item.kind)}</small></span></a>)}</div></section><section><div className="human-section-heading"><h2>At a glance</h2><span>A few useful signals, not a wall of charts.</span></div><div className="human-glance"><article><span>Money received</span><strong>{money(summary?.incoming_payments || 0)}</strong></article><article><span>Open opportunities</span><strong>{summary?.open_leads || 0}</strong></article><article><span>Low stock</span><strong>{summary?.low_stock || 0}</strong></article><article><span>Active projects</span><strong>{summary?.active_projects || 0}</strong></article></div></section></div>
    <section className="human-activity"><div className="human-section-heading"><h2>Recent activity</h2><span>The workspace story.</span></div>{activity.length === 0 ? <div className="human-empty">Activity will appear as the team works.</div> : activity.map((item) => <article key={item.id}><span className="dot" /><div><strong>{item.summary}</strong><small>{item.actor} · {new Date(item.created_at).toLocaleString()}</small></div></article>)}</section>
  </main>;
}

function DestinationPage({ context, destination, initialEntity, initialQuery, onCustomer }: { context: WorkspaceContext; destination: string; initialEntity?: string; initialQuery?: string; onCustomer: (id: string) => void }) {
  const titles: Record<string, [string, string]> = {
    customers: ["Customers", "People, organizations, follow-ups and customer history in one place."],
    work: ["Work", "What the organization needs to deliver: tasks, appointments, projects and jobs."],
    money: ["Money", "Get paid, understand spending and keep operating finances under control."],
    products: ["Products", "What you sell, what you have, what you buy and how it gets delivered."],
  };
  const entityKeys = (DESTINATION_ENTITIES[destination] || []).filter((entity) => availableEntity(context, entity));
  const [active, setActive] = useState(() => (initialEntity && entityKeys.includes(initialEntity) ? initialEntity : entityKeys[0] || ""));
  useEffect(() => { if (initialEntity && entityKeys.includes(initialEntity)) setActive(initialEntity); }, [initialEntity, destination]);
  const [title, subtitle] = titles[destination] || [humanize(destination), ""];
  if (!active) return <main className="human-destination"><header><h1>{title}</h1><p>No tools in this area are enabled for your role.</p></header></main>;
  const found = active === "stock" ? null : entityByKey(context, active);
  const inventory = moduleByKey(context, "inventory");
  return <main className="human-destination"><header className="human-destination-heading"><div><span>{title.toUpperCase()}</span><h1>{title}</h1><p>{subtitle}</p></div></header><nav className="human-tabs">{entityKeys.map((entity) => <button key={entity} className={active === entity ? "active" : ""} onClick={() => setActive(entity)}>{entity === "stock" ? "Stock" : entityByKey(context, entity)?.def.label || humanize(entity)}</button>)}</nav>{active === "stock" ? <InventoryStock writable={Boolean(inventory?.can_write)} /> : found ? <HumanRecordList def={found.def} writable={found.module.can_write} initialQuery={initialQuery || ""} onOpen={active === "contacts" ? (record) => onCustomer(String(record.id)) : undefined} /> : null}</main>;
}

function BusinessSetup({ context, onClose, onReload }: { context: WorkspaceContext; onClose: () => void; onReload: () => Promise<void> }) {
  const [busy, setBusy] = useState(""); const [error, setError] = useState("");
  const choose = async (profile: BusinessProfile) => {
    setBusy(profile.key); setError("");
    try {
      await api(`/workspace-os/presets/${profile.preset}/apply`, { method: "POST" });
      await api("/workspace-os/modules/dashboard", { method: "PUT", body: JSON.stringify({ enabled: true, configuration: { business_type: profile.key } }) });
      await onReload(); onClose();
    } catch (caught) { setError(errorText(caught, "Could not configure workspace")); }
    finally { setBusy(""); }
  };
  return <Modal onClose={onClose} wide><div className="human-business-setup"><header className="human-modal-heading"><div><small>WORKSPACE SETUP</small><h2>What kind of work happens here?</h2><p>You do not need to understand modules. Pick the closest fit; everything remains editable later.</p></div><button onClick={onClose}>×</button></header>{error && <div className="human-error">{error}</div>}<div className="human-profile-grid">{BUSINESS_PROFILES.map((profile) => <button key={profile.key} disabled={Boolean(busy)} onClick={() => void choose(profile)}><strong>{profile.name}</strong><span>{profile.description}</span><b>{busy === profile.key ? "Setting up…" : "Choose →"}</b></button>)}</div></div></Modal>;
}

function MorePage({ context, onSetup }: { context: WorkspaceContext; onSetup: () => void }) {
  const [selectedModule, setSelectedModule] = useState("");
  const modules = context.modules.filter((module) => module.key !== "dashboard" && module.enabled && module.can_read);
  const selected = modules.find((module) => module.key === selectedModule);
  const [activeEntity, setActiveEntity] = useState("");
  useEffect(() => { setActiveEntity(selected?.entities?.[0]?.entity || ""); }, [selectedModule]);
  const profile = String(moduleByKey(context, "dashboard")?.configuration?.business_type || "general");
  if (selected) {
    const def = selected.entities.find((entity) => entity.entity === activeEntity) || selected.entities[0];
    return <main className="human-destination"><button className="human-back" onClick={() => setSelectedModule("")}>← All tools</button><header className="human-destination-heading"><div><span>ADVANCED TOOL</span><h1>{selected.name}</h1><p>{selected.description}</p></div></header>{selected.entities.length === 0 ? <div className="human-empty">This tool has no record view.</div> : <><nav className="human-tabs">{selected.entities.map((entity) => <button key={entity.entity} className={def?.entity === entity.entity ? "active" : ""} onClick={() => setActiveEntity(entity.entity)}>{entity.label}</button>)}</nav>{def && <HumanRecordList def={def} writable={selected.can_write} />}</>}</main>;
  }
  return <main className="human-more"><header><span>MORE</span><h1>Everything else, when you need it.</h1><p>The everyday workspace stays simple. Specialized and advanced tools live here instead of crowding normal work.</p></header><section className="human-more-card human-experience-card"><div><strong>Workspace setup</strong><span>Current fit: {humanize(profile)}. Change how Operly is organized around this business.</span></div>{context.modules.some((module) => module.can_manage) && <button onClick={onSetup}>Change setup</button>}</section><div className="human-tool-grid">{modules.map((module) => <button key={module.key} onClick={() => setSelectedModule(module.key)}><span><strong>{module.name}</strong><small>{module.description}</small></span><b>Open →</b></button>)}</div><a className="human-settings-card" href="settings"><span><strong>Workspace settings</strong><small>Members, permissions, tools, identity and invitation links.</small></span><b>Open settings →</b></a></main>;
}

async function copyText(value: string): Promise<void> {
  if (navigator.clipboard?.writeText) { await navigator.clipboard.writeText(value); return; }
  const area = document.createElement("textarea"); area.value = value; document.body.appendChild(area); area.select(); document.execCommand("copy"); area.remove();
}

function GeneralSettings({ context, onReload }: { context: WorkspaceContext; onReload: () => Promise<void> }) {
  const canManage = context.role === "owner" || context.permissions.includes("workspace:settings:manage");
  const [busy, setBusy] = useState(false); const [message, setMessage] = useState(""); const [error, setError] = useState("");
  const submit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(""); setMessage("");
    try { await api("/workspace-os/settings", { method: "PATCH", body: JSON.stringify({ name: form.get("name"), timezone: form.get("timezone"), logo_url: form.get("logo_url") }) }); await onReload(); setMessage("Workspace updated."); }
    catch (caught) { setError(errorText(caught, "Could not update workspace")); }
    finally { setBusy(false); }
  };
  return <form className="human-settings-card" onSubmit={submit}><h2>Workspace</h2><p>Basic identity shared by everyone in this workspace.</p><label><span>Name</span><input name="name" defaultValue={context.workspace.name} disabled={!canManage} required /></label><label><span>Timezone</span><input name="timezone" defaultValue={context.workspace.timezone} disabled={!canManage} required /></label><label><span>Logo URL <small>optional</small></span><input name="logo_url" defaultValue={context.workspace.logo_url || ""} disabled={!canManage} /></label>{message && <div className="human-success">{message}</div>}{error && <div className="human-error">{error}</div>}{canManage && <button className="primary" disabled={busy}>{busy ? "Saving…" : "Save"}</button>}</form>;
}

function ToolsSettings({ context, onReload, onSetup }: { context: WorkspaceContext; onReload: () => Promise<void>; onSetup: () => void }) {
  const [busy, setBusy] = useState(""); const [error, setError] = useState("");
  const toggle = async (module: ModuleInfo) => {
    setBusy(module.key); setError("");
    try { await api(`/workspace-os/modules/${module.key}`, { method: "PUT", body: JSON.stringify({ enabled: !module.enabled, configuration: module.configuration || {} }) }); await onReload(); }
    catch (caught) { setError(errorText(caught, "Could not change tool")); }
    finally { setBusy(""); }
  };
  return <div className="human-settings-card"><div className="human-settings-card-heading"><div><h2>Tools</h2><p>Only turn on what this organization actually uses.</p></div>{context.modules.some((module) => module.can_manage) && <button onClick={onSetup}>Choose by business type</button>}</div>{error && <div className="human-error">{error}</div>}<div className="human-tool-settings">{context.modules.filter((module) => module.key !== "dashboard").map((module) => <article key={module.key}><div><strong>{module.name}</strong><span>{module.description}</span></div><button className={module.enabled ? "on" : ""} disabled={!module.can_manage || Boolean(module.locked) || busy === module.key} onClick={() => void toggle(module)}>{busy === module.key ? "…" : module.enabled ? "On" : "Off"}</button></article>)}</div></div>;
}

function MembersSettings({ context }: { context: WorkspaceContext }) {
  const [members, setMembers] = useState<Member[]>([]); const [roles, setRoles] = useState<RoleInfo[]>([]); const [invites, setInvites] = useState<Invite[]>([]); const [createdInvite, setCreatedInvite] = useState<CreatedInvite | null>(null); const [copied, setCopied] = useState(false); const [error, setError] = useState("");
  const canManage = context.role === "owner" || context.permissions.includes("workspace:members:manage");
  const load = useCallback(async () => {
    try {
      const [memberRows, roleRows, inviteRows] = await Promise.all([api<Member[]>("/workspace-os/members"), api<RolesResponse>("/workspace-os/roles"), canManage ? api<Invite[]>("/workspace-os/invitations") : Promise.resolve([])]);
      setMembers(memberRows); setRoles(roleRows.roles); setInvites(inviteRows);
    } catch (caught) { setError(errorText(caught, "Could not load members")); }
  }, [canManage]);
  useEffect(() => { void load(); }, [load]);
  const createInvite = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault(); const form = new FormData(event.currentTarget); setError(""); setCopied(false);
    try { const result = await api<CreatedInvite>("/workspace-os/invitations", { method: "POST", body: JSON.stringify({ email: form.get("email") || null, role: form.get("role"), ttl_days: Number(form.get("ttl_days") || 7) }) }); setCreatedInvite(result); await load(); }
    catch (caught) { setError(errorText(caught, "Could not create invite")); }
  };
  const setRole = async (userId: string, role: string) => { try { await api(`/workspace-os/members/${userId}`, { method: "PATCH", body: JSON.stringify({ role }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not change role")); } };
  const remove = async (member: Member) => { if (!window.confirm(`Remove ${member.display_name || member.email}?`)) return; try { await api(`/workspace-os/members/${member.user_id}`, { method: "DELETE" }); await load(); } catch (caught) { setError(errorText(caught, "Could not remove member")); } };
  const revoke = async (invite: Invite) => { if (!window.confirm("Revoke this invitation?")) return; try { await api(`/workspace-os/invitations/${invite.id}`, { method: "DELETE" }); await load(); } catch (caught) { setError(errorText(caught, "Could not revoke invite")); } };
  return <div className="human-settings-card"><h2>People & access</h2><p>Invite someone with a link. After sign in or signup, Operly adds them directly to this workspace with the role you choose.</p>{error && <div className="human-error">{error}</div>}{canManage && <form className="human-invite-form" onSubmit={createInvite}><input name="email" type="email" placeholder="Restrict to email (optional)" /><select name="role" defaultValue="employee">{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><select name="ttl_days" defaultValue="7"><option value="1">1 day</option><option value="7">7 days</option><option value="14">14 days</option><option value="30">30 days</option></select><button className="primary">Create invite link</button></form>}{createdInvite && <div className="human-invite-copy"><input readOnly value={createdInvite.invite_url} onFocus={(event) => event.currentTarget.select()} /><button onClick={() => void copyText(createdInvite.invite_url).then(() => setCopied(true))}>{copied ? "Copied" : "Copy"}</button></div>}<div className="human-members">{members.map((member) => <article key={member.user_id}><div><strong>{member.display_name || member.email}</strong><span>{member.email}</span></div>{canManage ? <><select value={member.role} onChange={(event) => void setRole(member.user_id, event.target.value)}>{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select><button onClick={() => void remove(member)}>Remove</button></> : <b>{humanize(member.role)}</b>}</article>)}</div>{canManage && invites.some((invite) => invite.status === "pending") && <details className="human-pending-invites"><summary>Pending invitations</summary>{invites.filter((invite) => invite.status === "pending").map((invite) => <article key={invite.id}><span>{invite.target_email || "Anyone with link"} · {humanize(invite.role)} · expires {new Date(invite.expires_at).toLocaleDateString()}</span><button onClick={() => void revoke(invite)}>Revoke</button></article>)}</details>}</div>;
}

function RolesSettings({ context }: { context: WorkspaceContext }) {
  const [data, setData] = useState<RolesResponse>({ roles: [], known_permissions: [] }); const [selected, setSelected] = useState(""); const [permissions, setPermissions] = useState<Set<string>>(new Set()); const [error, setError] = useState(""); const [busy, setBusy] = useState(false);
  const canManage = context.role === "owner" || context.permissions.includes("workspace:roles:manage");
  const load = useCallback(async () => { try { const result = await api<RolesResponse>("/workspace-os/roles"); setData(result); setSelected((value) => value || result.roles[0]?.key || ""); } catch (caught) { setError(errorText(caught, "Could not load roles")); } }, []);
  useEffect(() => { void load(); }, [load]);
  const role = data.roles.find((candidate) => candidate.key === selected);
  const rolePermissionKey = role?.permissions.join("|") || "";
  useEffect(() => { setPermissions(new Set(role?.permissions || [])); }, [role?.key, rolePermissionKey]);
  const save = async () => { if (!role) return; setBusy(true); setError(""); try { await api(`/workspace-os/roles/${role.key}`, { method: "PUT", body: JSON.stringify({ name: role.name, permissions: [...permissions] }) }); await load(); } catch (caught) { setError(errorText(caught, "Could not save permissions")); } finally { setBusy(false); } };
  const groups = useMemo(() => { const result: Record<string, string[]> = {}; for (const permission of data.known_permissions) { const group = permission.split(":", 1)[0]; (result[group] ||= []).push(permission); } return result; }, [data.known_permissions]);
  return <div className="human-settings-card"><h2>Roles & permissions</h2><p>Most people never need this. Use it when a role needs more or less authority than the defaults.</p>{error && <div className="human-error">{error}</div>}<div className="human-role-layout"><nav>{data.roles.map((item) => <button key={item.key} className={selected === item.key ? "active" : ""} onClick={() => setSelected(item.key)}><strong>{item.name}</strong><small>{item.system ? "Built-in" : "Custom"}</small></button>)}</nav>{role && <div className="human-permissions"><h3>{role.name}</h3>{Object.entries(groups).map(([group, values]) => <details key={group}><summary>{humanize(group)} <small>{values.filter((permission) => permissions.has(permission)).length}/{values.length}</small></summary>{values.map((permission) => <label key={permission}><input type="checkbox" checked={permissions.has(permission)} disabled={!canManage} onChange={(event) => setPermissions((current) => { const next = new Set(current); if (event.target.checked) next.add(permission); else next.delete(permission); return next; })} /><span>{permission}</span></label>)}</details>)}{canManage && <button className="primary" disabled={busy} onClick={() => void save()}>{busy ? "Saving…" : "Save permissions"}</button>}</div>}</div></div>;
}

function Settings({ context, onReload, onSetup }: { context: WorkspaceContext; onReload: () => Promise<void>; onSetup: () => void }) {
  const [tab, setTab] = useState("general");
  const tabs = [["general", "General"], ["tools", "Tools"], ["people", "People"], ["access", "Advanced access"]];
  return <main className="human-settings"><header><span>SETTINGS</span><h1>{context.workspace.name}</h1><p>Keep the normal workspace simple. Configuration lives here.</p></header><nav className="human-settings-tabs">{tabs.map(([key, label]) => <button key={key} className={tab === key ? "active" : ""} onClick={() => setTab(key)}>{label}</button>)}</nav>{tab === "general" && <GeneralSettings context={context} onReload={onReload} />}{tab === "tools" && <ToolsSettings context={context} onReload={onReload} onSetup={onSetup} />}{tab === "people" && <MembersSettings context={context} />}{tab === "access" && <RolesSettings context={context} />}</main>;
}

export function WorkspaceHumanPanel({ workspaceId, pathname }: { workspaceId: string; pathname: string }) {
  const [context, setContext] = useState<WorkspaceContext | null>(null);
  const [summary, setSummary] = useState<Summary | null>(null);
  const [error, setError] = useState("");
  const [toast, setToast] = useState("");
  const [quickAction, setQuickAction] = useState<QuickAction | null>(null);
  const [quickContact, setQuickContact] = useState<string | undefined>(undefined);
  const [setupOpen, setSetupOpen] = useState(false);
  const [customerId, setCustomerId] = useState("");
  const [refreshKey, setRefreshKey] = useState(0);
  const section = pathSection(pathname, workspaceId);

  const reload = useCallback(async () => {
    setError("");
    try {
      const current = await api<WorkspaceContext>("/workspace-os/context");
      if (current.workspace.id !== workspaceId) throw new Error("Workspace session is still switching");
      setContext(current);
      setSummary(await api<Summary>("/workspace-os/summary"));
      setRefreshKey((value) => value + 1);
    } catch (caught) { setError(errorText(caught, "Could not open workspace")); }
  }, [workspaceId]);
  useEffect(() => { void reload(); }, [reload]);
  useEffect(() => { if (!toast) return; const timer = window.setTimeout(() => setToast(""), 4200); return () => window.clearTimeout(timer); }, [toast]);

  if (error && !context) return <div className="human-fatal"><h1>Workspace unavailable</h1><p>{error}</p><button onClick={() => void reload()}>Retry</button></div>;
  if (!context) return <div className="human-loading">Opening workspace…</div>;

  const query = new URLSearchParams(window.location.search);
  const requestedEntity = query.get("entity") || undefined;
  const requestedQuery = query.get("q") || undefined;
  const oldModule = context.modules.find((module) => module.key === section && section !== "dashboard");
  const openQuick = (action: QuickAction, contactId?: string) => { setQuickContact(contactId); setQuickAction(action); };
  const onSaved = (message: string) => { setToast(message); void reload(); };

  const nav = [
    ["home", "Home"], ["customers", "Customers"], ["work", "Work"], ["money", "Money"], ["products", "Products"], ["more", "More"],
  ];

  return <div className="human-workspace"><aside className="human-sidebar"><div className="human-sidebar-head"><strong>{context.workspace.name}</strong><span>{humanize(context.role)}</span></div><nav>{nav.map(([key, label]) => <a key={key} className={section === key ? "active" : ""} href={workspacePath(workspaceId, key)}><span className="human-nav-icon">{key === "home" ? "⌂" : key === "customers" ? "◎" : key === "work" ? "✓" : key === "money" ? "$" : key === "products" ? "□" : "•••"}</span>{label}</a>)}</nav><a className={`human-settings-link ${section === "settings" ? "active" : ""}`} href={workspacePath(workspaceId, "settings")}>⚙ Settings</a></aside>
    <div className="human-pane"><header className="human-command-bar"><GlobalSearch workspaceId={workspaceId} /><QuickAdd context={context} onSaved={onSaved} /></header>{error && <div className="human-error human-top-error">{error}</div>}{toast && <div className="human-toast">{toast}</div>}
      {section === "home" && <Home context={context} summary={summary} workspaceId={workspaceId} onQuick={(action) => openQuick(action)} onSetup={() => setSetupOpen(true)} refreshKey={refreshKey} />}
      {["customers", "work", "money", "products"].includes(section) && <DestinationPage context={context} destination={section} initialEntity={requestedEntity} initialQuery={requestedQuery} onCustomer={(id) => setCustomerId(id)} />}
      {section === "more" && <MorePage context={context} onSetup={() => setSetupOpen(true)} />}
      {section === "settings" && <Settings context={context} onReload={reload} onSetup={() => setSetupOpen(true)} />}
      {oldModule && section !== "home" && !["customers", "work", "money", "products", "more", "settings"].includes(section) && <main className="human-destination"><header className="human-destination-heading"><div><span>ADVANCED TOOL</span><h1>{oldModule.name}</h1><p>{oldModule.description}</p></div></header>{oldModule.entities?.[0] ? <HumanRecordList def={oldModule.entities[0]} writable={oldModule.can_write} /> : <div className="human-empty">This tool has no record view.</div>}</main>}
    </div>
    {quickAction && <QuickAdd context={context} initialAction={quickAction} initialContactId={quickContact} onSaved={onSaved} onClosed={() => { setQuickAction(null); setQuickContact(undefined); }} />}
    {setupOpen && <BusinessSetup context={context} onClose={() => setSetupOpen(false)} onReload={reload} />}
    {customerId && <CustomerDetail contactId={customerId} context={context} onClose={() => setCustomerId("")} onQuick={(action, contactId) => { setCustomerId(""); openQuick(action, contactId); }} />}
  </div>;
}

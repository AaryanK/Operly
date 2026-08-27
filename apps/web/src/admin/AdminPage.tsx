import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

type AdminSession = { user?: { display_name?: string; email?: string } };
type Overview = { generated_at?: string; metrics?: Record<string, number>; recent_users?: any[]; geography?: { countries?: any[]; top_paths?: any[] }; activity?: any[] };

type Tab = "overview" | "users" | "workspaces";

function fmt(value: any) { return new Intl.NumberFormat(undefined, { notation: Number(value) >= 10000 ? "compact" : "standard", maximumFractionDigits: 1 }).format(Number(value || 0)); }
function date(value: any) { if (!value) return "Never"; const d = new Date(value); return Number.isNaN(d.getTime()) ? "—" : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(d); }
function countryFlag(code?: string) { const value = String(code || "").toUpperCase(); return /^[A-Z]{2}$/.test(value) ? String.fromCodePoint(...[...value].map((letter) => 127397 + letter.charCodeAt(0))) : "◌"; }

function AdminLogin({ onReady, initialError }: { onReady: (session: AdminSession) => void; initialError?: string }) {
  const [busy, setBusy] = useState(false); const [error, setError] = useState(initialError || "");
  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError("");
    try {
      await api("/auth/bootstrap");
      await api("/auth/login", { method: "POST", body: JSON.stringify({ email: form.get("email"), password: form.get("password") }) });
      const session = await api<AdminSession>("/admin/session");
      onReady(session);
    } catch (caught) {
      setError(caught instanceof ApiError && caught.status === 403 ? "Those credentials belong to an Operly account, but it is not the configured platform administrator." : caught instanceof Error ? caught.message : "Admin sign-in failed");
    } finally { setBusy(false); }
  }
  return <div className="admin-react-login"><OperlyMark className="admin-react-mark" /><form onSubmit={submit}><span className="eyebrow">Platform administration</span><h1>Open Operly Admin</h1><p>Use the configured platform administrator account.</p><label>Email<input name="email" type="email" autoComplete="username" required /></label><label>Password<input name="password" type="password" autoComplete="current-password" required /></label>{error && <div className="inline-error">{error}</div>}<button className="primary-button full-button" disabled={busy}>{busy ? "Signing in…" : "Open admin dashboard"}</button><a href="/">← Back to Operly</a></form></div>;
}

function Metric({ label, value }: { label: string; value: any }) { return <article className="admin-metric"><span>{label}</span><strong>{fmt(value)}</strong></article>; }

function OverviewTab({ overview }: { overview: Overview | null }) {
  const metrics = overview?.metrics || {};
  const countries = overview?.geography?.countries || [];
  return <div className="admin-tab-content"><div className="admin-metric-grid"><Metric label="Users" value={metrics.users} /><Metric label="Verified users" value={metrics.verified_users} /><Metric label="Workspaces" value={metrics.workspaces} /><Metric label="Active sessions" value={metrics.active_sessions} /></div><div className="admin-panel-grid"><section className="admin-panel"><h3>Recent users</h3>{(overview?.recent_users || []).length ? (overview?.recent_users || []).slice(0, 12).map((user: any) => <div className="admin-list-row" key={user.id || user.email}><div><strong>{user.display_name || "Unnamed user"}</strong><small>{user.email}</small></div><span>{user.verified ? "Verified" : "Unverified"}</span></div>) : <p className="muted">No customer signups yet.</p>}</section><section className="admin-panel"><h3>Top countries</h3>{countries.length ? countries.slice(0, 10).map((item: any) => <div className="admin-list-row" key={item.country_code}><div><strong>{countryFlag(item.country_code)} {item.country_code || "Unknown"}</strong><small>{fmt(item.unique_users)} users</small></div><span>{fmt(item.visits)} views</span></div>) : <p className="muted">No country data yet.</p>}</section></div></div>;
}

function UsersTab({ users, query, setQuery }: { users: any[]; query: string; setQuery: (value: string) => void }) {
  const rows = useMemo(() => users.filter((user) => `${user.display_name} ${user.email} ${user.country_code || ""} ${(user.workspaces || []).map((w: any) => w.name).join(" ")}`.toLowerCase().includes(query.toLowerCase())), [users, query]);
  return <div className="admin-tab-content"><div className="admin-toolbar"><input placeholder="Search users" value={query} onChange={(event) => setQuery(event.target.value)} /></div><div className="admin-table"><div className="admin-table-head"><span>User</span><span>Status</span><span>Country</span><span>Workspaces</span><span>Joined</span></div>{rows.map((user) => <div className="admin-table-row" key={user.id}><div><strong>{user.display_name || "Unnamed user"}</strong><small>{user.email}</small>{user.is_admin && <em>Platform admin</em>}</div><span>{user.active ? "Active" : "Disabled"}{user.verified ? " · Verified" : " · Unverified"}</span><span>{countryFlag(user.country_code)} {user.country_code || "Unknown"}</span><span>{(user.workspaces || []).length ? (user.workspaces || []).map((w: any) => w.name).slice(0,3).join(", ") : "Personal only"}</span><span>{date(user.created_at)}</span></div>)}</div></div>;
}

function WorkspacesTab({ workspaces }: { workspaces: any[] }) {
  return <div className="admin-tab-content"><div className="admin-workspace-grid">{workspaces.map((workspace) => <article className="admin-workspace-card" key={workspace.id}><header><div><h3>{workspace.name}</h3><small>/{workspace.slug || workspace.id}</small></div><span>{fmt(workspace.member_count)} members</span></header><p>{workspace.timezone || "UTC"} · Created {date(workspace.created_at)}</p><div>{(workspace.members || []).slice(0,8).map((member: any) => <div className="admin-list-row" key={member.id || member.email}><div><strong>{member.display_name || "Unknown user"}</strong><small>{member.email}</small></div><span>{member.role}</span></div>)}</div></article>)}</div></div>;
}

export function AdminPage() {
  const [session, setSession] = useState<AdminSession | null>(null); const [initialError, setInitialError] = useState(""); const [loading, setLoading] = useState(true); const [tab, setTab] = useState<Tab>("overview"); const [overview, setOverview] = useState<Overview | null>(null); const [users, setUsers] = useState<any[]>([]); const [workspaces, setWorkspaces] = useState<any[]>([]); const [query, setQuery] = useState(""); const [busy, setBusy] = useState(false);
  useEffect(() => { api<AdminSession>("/admin/session").then(setSession).catch((error) => { if (error instanceof ApiError && error.status === 403) setInitialError("The signed-in Operly account is not the configured platform administrator."); }).finally(() => setLoading(false)); }, []);
  useEffect(() => { if (!session) return; if (tab === "overview") api<Overview>("/admin/overview").then(setOverview); if (tab === "users" && !users.length) api<any[]>("/admin/users?limit=500").then(setUsers); if (tab === "workspaces" && !workspaces.length) api<any[]>("/admin/workspaces?limit=500").then(setWorkspaces); }, [session, tab]);
  async function refresh() { if (!session) return; setBusy(true); try { if (tab === "overview") setOverview(await api<Overview>("/admin/overview")); if (tab === "users") setUsers(await api<any[]>("/admin/users?limit=500")); if (tab === "workspaces") setWorkspaces(await api<any[]>("/admin/workspaces?limit=500")); } finally { setBusy(false); } }
  async function logout() { try { await api("/auth/logout", { method: "POST", body: "{}" }); } finally { window.location.reload(); } }
  if (loading) return <div className="admin-react-loading"><OperlyMark /><span>Opening admin…</span></div>;
  if (!session) return <AdminLogin onReady={setSession} initialError={initialError} />;
  return <div className="admin-react-shell"><aside><div className="admin-brand"><OperlyMark /><div><strong>OPERLY</strong><small>Platform admin</small></div></div><nav><button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>Overview</button><button className={tab === "users" ? "active" : ""} onClick={() => setTab("users")}>Users</button><button className={tab === "workspaces" ? "active" : ""} onClick={() => setTab("workspaces")}>Workspaces</button></nav><div className="admin-user"><strong>{session.user?.display_name || "Admin"}</strong><small>{session.user?.email}</small><button onClick={logout}>Sign out</button></div></aside><main><header><div><span className="eyebrow">Platform administration</span><h1>{tab[0].toUpperCase() + tab.slice(1)}</h1></div><button className="secondary-button" disabled={busy} onClick={refresh}>{busy ? "Refreshing…" : "Refresh"}</button></header>{tab === "overview" && <OverviewTab overview={overview} />}{tab === "users" && <UsersTab users={users} query={query} setQuery={setQuery} />}{tab === "workspaces" && <WorkspacesTab workspaces={workspaces} />}</main></div>;
}

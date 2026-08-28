import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

type AdminSession = { user?: { display_name?: string; email?: string } };
type Overview = {
  generated_at?: string;
  metrics?: Record<string, number>;
  recent_users?: any[];
  geography?: { countries?: any[]; top_paths?: any[] };
  activity?: any[];
};
type Tab = "overview" | "users" | "workspaces";

const tabs: Array<{ id: Tab; label: string; icon: string; copy: string }> = [
  { id: "overview", label: "Overview", icon: "⌁", copy: "Platform pulse, growth and account health" },
  { id: "users", label: "Users", icon: "◎", copy: "Accounts, activity and workspace membership" },
  { id: "workspaces", label: "Workspaces", icon: "◇", copy: "Tenancy, membership and workspace state" },
];

function fmt(value: any) {
  return new Intl.NumberFormat(undefined, {
    notation: Number(value) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(Number(value || 0));
}

function date(value: any) {
  if (!value) return "Never";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "—"
    : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", year: "numeric" }).format(d);
}

function time(value: any) {
  if (!value) return "Live data";
  const d = new Date(value);
  return Number.isNaN(d.getTime())
    ? "Live data"
    : `Updated ${new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(d)}`;
}

function countryFlag(code?: string) {
  const value = String(code || "").toUpperCase();
  return /^[A-Z]{2}$/.test(value)
    ? String.fromCodePoint(...[...value].map((letter) => 127397 + letter.charCodeAt(0)))
    : "◌";
}

function initials(name?: string, email?: string) {
  const source = (name || email || "O").trim();
  return source
    .split(/[\s@._-]+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0]?.toUpperCase())
    .join("") || "O";
}

function AdminLogin({ onReady, initialError }: { onReady: (session: AdminSession) => void; initialError?: string }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState(initialError || "");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/auth/bootstrap");
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      const session = await api<AdminSession>("/admin/session");
      onReady(session);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 403
          ? "Those credentials belong to an Operly account, but it is not the configured platform administrator."
          : caught instanceof Error
            ? caught.message
            : "Admin sign-in failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="admin-react-login">
      <div className="admin-login-orbit" aria-hidden="true"><i /><i /><i /></div>
      <div className="admin-login-brand"><OperlyMark className="admin-react-mark" /><div><strong>OPERLY</strong><span>Platform control plane</span></div></div>
      <form onSubmit={submit}>
        <span className="eyebrow">Platform administration</span>
        <h1>Owner console</h1>
        <p>Private operational visibility for the Operly platform administrator.</p>
        <div className="admin-login-security"><span>◆</span><div><strong>Protected surface</strong><small>Only the configured platform administrator can enter.</small></div></div>
        <label>Email<input name="email" type="email" autoComplete="username" required /></label>
        <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
        {error && <div className="inline-error">{error}</div>}
        <button className="primary-button full-button" disabled={busy}>{busy ? "Signing in…" : "Open admin dashboard"}</button>
        <a href="/">← Back to Operly</a>
      </form>
    </div>
  );
}

function Metric({ label, value, note, glyph }: { label: string; value: any; note: string; glyph: string }) {
  return (
    <article className="admin-metric">
      <header><span>{label}</span><i>{glyph}</i></header>
      <strong>{fmt(value)}</strong>
      <small>{note}</small>
    </article>
  );
}

function OverviewTab({ overview }: { overview: Overview | null }) {
  const metrics = overview?.metrics || {};
  const countries = overview?.geography?.countries || [];
  const paths = overview?.geography?.top_paths || [];
  const users = Number(metrics.users || 0);
  const verified = Number(metrics.verified_users || 0);
  const verifiedPercent = users ? Math.round((verified / users) * 100) : 0;

  return (
    <div className="admin-tab-content admin-overview">
      <section className="admin-overview-intro">
        <div>
          <span className="eyebrow">Platform pulse</span>
          <h2>Everything important, at a glance.</h2>
          <p>Live platform-level visibility without leaving the canonical Operly control plane.</p>
        </div>
        <div className="admin-live-pill"><i /> {time(overview?.generated_at)}</div>
      </section>

      <div className="admin-metric-grid">
        <Metric label="Users" value={metrics.users} note="All registered accounts" glyph="◎" />
        <Metric label="Active now" value={metrics.active_now ?? metrics.active_sessions} note="Recent live sessions" glyph="◉" />
        <Metric label="DAU" value={metrics.dau} note="Active in the last 24h" glyph="↗" />
        <Metric label="WAU" value={metrics.wau} note="Active in the last 7d" glyph="⌁" />
        <Metric label="Verified" value={metrics.verified_users} note={`${verifiedPercent}% of accounts`} glyph="✓" />
        <Metric label="Workspaces" value={metrics.workspaces} note={`${fmt(metrics.memberships)} memberships`} glyph="◇" />
      </div>

      <div className="admin-overview-grid">
        <section className="admin-panel admin-health-panel">
          <header className="admin-panel-heading"><div><span className="eyebrow">Account health</span><h3>Trust & activation</h3></div><span className="admin-panel-chip">{verifiedPercent}% verified</span></header>
          <div className="admin-health-ring" style={{ "--health": `${verifiedPercent}%` } as any}><div><strong>{verifiedPercent}%</strong><span>verified</span></div></div>
          <div className="admin-health-copy">
            <div><span>Verified users</span><strong>{fmt(metrics.verified_users)}</strong></div>
            <div><span>Active accounts</span><strong>{fmt(metrics.active_accounts ?? metrics.dau)}</strong></div>
            <div><span>Memberships</span><strong>{fmt(metrics.memberships)}</strong></div>
          </div>
        </section>

        <section className="admin-panel admin-country-panel">
          <header className="admin-panel-heading"><div><span className="eyebrow">Geography</span><h3>Where Operly is active</h3></div><span className="admin-panel-chip">Top regions</span></header>
          <div className="admin-country-cloud">
            {countries.length ? countries.slice(0, 6).map((item: any) => (
              <span key={item.country_code}>{countryFlag(item.country_code)} <b>{item.country_code || "Unknown"}</b><small>{fmt(item.unique_users)}</small></span>
            )) : <p className="muted">Location data will appear as users become active.</p>}
          </div>
          <div className="admin-country-list">
            {countries.slice(0, 5).map((item: any, index: number) => (
              <div className="admin-country-row" key={`${item.country_code}-${index}`}>
                <div><strong>{countryFlag(item.country_code)} {item.country_code || "Unknown"}</strong><small>{fmt(item.unique_users)} users</small></div>
                <span>{fmt(item.visits)} views</span>
              </div>
            ))}
          </div>
        </section>

        <section className="admin-panel admin-recent-panel">
          <header className="admin-panel-heading"><div><span className="eyebrow">Newest accounts</span><h3>Recent users</h3></div><span className="admin-panel-chip">Latest 8</span></header>
          <div className="admin-recent-grid">
            {(overview?.recent_users || []).length ? (overview?.recent_users || []).slice(0, 8).map((user: any) => (
              <div className="admin-recent-user" key={user.id || user.email}>
                <span className="admin-avatar">{initials(user.display_name, user.email)}</span>
                <div><strong>{user.display_name || "Unnamed user"}</strong><small>{user.email}</small></div>
                <em>{user.verified ? "Verified" : "Unverified"}</em>
              </div>
            )) : <p className="muted">No customer signups yet.</p>}
          </div>
        </section>

        <section className="admin-panel admin-path-panel">
          <header className="admin-panel-heading"><div><span className="eyebrow">Product</span><h3>Top app paths</h3></div><span className="admin-panel-chip">30 day signal</span></header>
          <div className="admin-path-list">
            {paths.length ? paths.slice(0, 7).map((item: any, index: number) => (
              <div className="admin-path-row" key={`${item.path || item.pathname}-${index}`}>
                <span>{String(item.path || item.pathname || "/")}</span><strong>{fmt(item.visits ?? item.count)}</strong>
              </div>
            )) : <p className="muted">Usage paths will appear as traffic is recorded.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

function UsersTab({ users, query, setQuery }: { users: any[]; query: string; setQuery: (value: string) => void }) {
  const rows = useMemo(
    () => users.filter((user) => `${user.display_name} ${user.email} ${user.country_code || ""} ${(user.workspaces || []).map((w: any) => w.name).join(" ")}`.toLowerCase().includes(query.toLowerCase())),
    [users, query],
  );

  return (
    <div className="admin-tab-content">
      <section className="admin-section-heading"><div><span className="eyebrow">Accounts</span><h2>Users</h2><p>Platform-wide identity, activation and workspace membership.</p></div><div className="admin-result-count"><strong>{fmt(rows.length)}</strong><span>matching users</span></div></section>
      <div className="admin-toolbar"><label><span>Search accounts</span><input placeholder="Name, email, country or workspace…" value={query} onChange={(event) => setQuery(event.target.value)} /></label></div>
      <div className="admin-table">
        <div className="admin-table-head"><span>User</span><span>Status</span><span>Country</span><span>Workspaces</span><span>Joined</span></div>
        {rows.map((user) => (
          <div className="admin-table-row" key={user.id}>
            <div className="admin-user-cell"><span className="admin-avatar">{initials(user.display_name, user.email)}</span><div><strong>{user.display_name || "Unnamed user"}</strong><small>{user.email}</small>{user.is_admin && <em>Platform admin</em>}</div></div>
            <span className={`admin-status ${user.active ? "active" : ""}`}><i />{user.active ? "Active" : "Disabled"}{user.verified ? " · Verified" : " · Unverified"}</span>
            <span>{countryFlag(user.country_code)} {user.country_code || "Unknown"}</span>
            <span>{(user.workspaces || []).length ? (user.workspaces || []).map((w: any) => w.name).slice(0, 3).join(", ") : "Personal only"}</span>
            <span>{date(user.created_at)}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function WorkspacesTab({ workspaces }: { workspaces: any[] }) {
  return (
    <div className="admin-tab-content">
      <section className="admin-section-heading"><div><span className="eyebrow">Tenancy</span><h2>Workspaces</h2><p>Every shared Operly environment and the people currently attached to it.</p></div><div className="admin-result-count"><strong>{fmt(workspaces.length)}</strong><span>workspaces</span></div></section>
      <div className="admin-workspace-grid">
        {workspaces.map((workspace) => (
          <article className="admin-workspace-card" key={workspace.id}>
            <header><div className="admin-workspace-identity"><span>◇</span><div><h3>{workspace.name}</h3><small>/{workspace.slug || workspace.id}</small></div></div><span className="admin-panel-chip">{fmt(workspace.member_count)} members</span></header>
            <p>{workspace.timezone || "UTC"} · Created {date(workspace.created_at)}</p>
            <div className="admin-workspace-members">
              {(workspace.members || []).slice(0, 8).map((member: any) => (
                <div className="admin-list-row" key={member.id || member.email}>
                  <div className="admin-user-cell"><span className="admin-avatar small">{initials(member.display_name, member.email)}</span><div><strong>{member.display_name || "Unknown user"}</strong><small>{member.email}</small></div></div>
                  <span>{member.role}</span>
                </div>
              ))}
            </div>
          </article>
        ))}
      </div>
    </div>
  );
}

export function AdminPage() {
  const [session, setSession] = useState<AdminSession | null>(null);
  const [initialError, setInitialError] = useState("");
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<Tab>("overview");
  const [overview, setOverview] = useState<Overview | null>(null);
  const [users, setUsers] = useState<any[]>([]);
  const [workspaces, setWorkspaces] = useState<any[]>([]);
  const [query, setQuery] = useState("");
  const [busy, setBusy] = useState(false);

  const activeTab = tabs.find((item) => item.id === tab) || tabs[0];

  useEffect(() => {
    api<AdminSession>("/admin/session")
      .then(setSession)
      .catch((error) => {
        if (error instanceof ApiError && error.status === 403) setInitialError("The signed-in Operly account is not the configured platform administrator.");
      })
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    if (!session) return;
    if (tab === "overview") api<Overview>("/admin/overview").then(setOverview);
    if (tab === "users" && !users.length) api<any[]>("/admin/users?limit=500").then(setUsers);
    if (tab === "workspaces" && !workspaces.length) api<any[]>("/admin/workspaces?limit=500").then(setWorkspaces);
  }, [session, tab]);

  async function refresh() {
    if (!session) return;
    setBusy(true);
    try {
      if (tab === "overview") setOverview(await api<Overview>("/admin/overview"));
      if (tab === "users") setUsers(await api<any[]>("/admin/users?limit=500"));
      if (tab === "workspaces") setWorkspaces(await api<any[]>("/admin/workspaces?limit=500"));
    } finally {
      setBusy(false);
    }
  }

  async function logout() {
    try {
      await api("/auth/logout", { method: "POST", body: "{}" });
    } finally {
      window.location.reload();
    }
  }

  if (loading) return <div className="admin-react-loading"><div className="admin-loading-orbit"><OperlyMark /><i /><i /></div><strong>OPERLY ADMIN</strong><span>Opening platform state…</span></div>;
  if (!session) return <AdminLogin onReady={setSession} initialError={initialError} />;

  return (
    <div className="admin-react-shell">
      <div className="admin-shell-orb" aria-hidden="true" />
      <aside>
        <div className="admin-brand"><OperlyMark /><div><strong>OPERLY</strong><small>Platform admin</small></div></div>
        <div className="admin-nav-caption">Control plane</div>
        <nav>
          {tabs.map((item) => (
            <button className={tab === item.id ? "active" : ""} onClick={() => setTab(item.id)} key={item.id}>
              <span>{item.icon}</span><div><strong>{item.label}</strong><small>{item.copy}</small></div>
            </button>
          ))}
        </nav>
        <div className="admin-user"><div className="admin-user-cell"><span className="admin-avatar">{initials(session.user?.display_name, session.user?.email)}</span><div><strong>{session.user?.display_name || "Admin"}</strong><small>{session.user?.email}</small></div></div><button onClick={logout}>Sign out</button></div>
      </aside>
      <main>
        <header>
          <div><span className="eyebrow">Operly platform</span><h1>{activeTab.label}</h1><p>{activeTab.copy}</p></div>
          <div className="admin-top-actions"><span className="admin-live-pill"><i /> Secure admin session</span><button className="secondary-button" disabled={busy} onClick={refresh}>{busy ? "Refreshing…" : "↻ Refresh"}</button></div>
        </header>
        {tab === "overview" && <OverviewTab overview={overview} />}
        {tab === "users" && <UsersTab users={users} query={query} setQuery={setQuery} />}
        {tab === "workspaces" && <WorkspacesTab workspaces={workspaces} />}
      </main>
    </div>
  );
}
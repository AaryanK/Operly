import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

type AdminSession = { user?: { display_name?: string; email?: string } };
type ActivityPoint = { date?: string; signups?: number; signins?: number };
type Overview = {
  generated_at?: string;
  metrics?: Record<string, number>;
  recent_users?: any[];
  geography?: { countries?: any[]; top_paths?: any[] };
  activity?: ActivityPoint[];
};
type UsageRange = "1h" | "24h" | "7d" | "30d" | "all";
type UsagePoint = { bucket?: string; total_tokens?: number };
type ModelUsage = {
  provider?: string;
  model?: string;
  input_tokens?: number;
  output_tokens?: number;
  total_tokens?: number;
  calls?: number;
  tracked_calls?: number;
  share_percent?: number;
};
type AIUsage = {
  range?: UsageRange;
  generated_at?: string;
  totals?: {
    input_tokens?: number;
    output_tokens?: number;
    total_tokens?: number;
    calls?: number;
    tracked_calls?: number;
  };
  coverage?: { tracked_calls?: number; calls?: number; percent?: number };
  models?: number;
  series?: UsagePoint[];
  by_model?: ModelUsage[];
};
type Tab = "overview" | "ai-usage" | "users" | "workspaces";

const tabs: Array<{ id: Tab; label: string; icon: string; copy: string }> = [
  { id: "overview", label: "Overview", icon: "⌁", copy: "Platform pulse, growth and account health" },
  { id: "ai-usage", label: "AI Usage", icon: "◒", copy: "Model calls, token volume and route distribution" },
  { id: "users", label: "Users", icon: "◎", copy: "Accounts, activity and workspace membership" },
  { id: "workspaces", label: "Workspaces", icon: "◇", copy: "Tenancy, membership and workspace state" },
];

const usageRanges: Array<{ id: UsageRange; label: string }> = [
  { id: "1h", label: "1H" },
  { id: "24h", label: "24H" },
  { id: "7d", label: "7D" },
  { id: "30d", label: "30D" },
  { id: "all", label: "All" },
];

function numeric(value: any) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed : 0;
}

function fmt(value: any) {
  return new Intl.NumberFormat(undefined, {
    notation: Math.abs(numeric(value)) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(numeric(value));
}

function exact(value: any) {
  return new Intl.NumberFormat().format(numeric(value));
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

function bucketLabel(value?: string, range: UsageRange = "24h") {
  if (!value) return "";
  const normalized = value.length === 10 ? `${value}T00:00:00Z` : value.endsWith("Z") ? value : `${value}Z`;
  const d = new Date(normalized);
  if (Number.isNaN(d.getTime())) return value;
  if (range === "1h") return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(d);
  if (range === "24h") return new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(d);
  return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(d);
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
      <strong title={exact(value)}>{fmt(value)}</strong>
      <small>{note}</small>
    </article>
  );
}

function GrowthChart({ rows }: { rows: ActivityPoint[] }) {
  const data = rows || [];
  const width = 920;
  const height = 250;
  const left = 44;
  const right = 14;
  const top = 16;
  const bottom = 34;
  const innerWidth = width - left - right;
  const innerHeight = height - top - bottom;
  const maxValue = Math.max(1, ...data.flatMap((row) => [numeric(row.signups), numeric(row.signins)]));
  const x = (index: number) => left + (index / Math.max(1, data.length - 1)) * innerWidth;
  const y = (value: number) => top + innerHeight - (value / maxValue) * innerHeight;
  const signups = data.map((row, index) => `${x(index).toFixed(2)},${y(numeric(row.signups)).toFixed(2)}`).join(" ");
  const signins = data.map((row, index) => `${x(index).toFixed(2)},${y(numeric(row.signins)).toFixed(2)}`).join(" ");
  const labels = data.filter((_, index) => index % 6 === 0 || index === data.length - 1);

  if (!data.length) return <div className="admin-chart-empty">Growth data will appear after platform activity is recorded.</div>;

  return (
    <div className="admin-growth-chart" aria-label="30 day signup and sign-in activity">
      <svg viewBox={`0 0 ${width} ${height}`} role="img">
        {[0, 0.5, 1].map((fraction) => {
          const lineY = top + innerHeight - fraction * innerHeight;
          return <g key={fraction}><line x1={left} x2={width - right} y1={lineY} y2={lineY} /><text x={left - 9} y={lineY + 4} textAnchor="end">{fmt(maxValue * fraction)}</text></g>;
        })}
        <polyline className="admin-growth-signins" points={signins} />
        <polyline className="admin-growth-signups" points={signups} />
        {labels.map((row) => {
          const index = data.indexOf(row);
          return <text className="admin-growth-x" key={`${row.date}-${index}`} x={x(index)} y={height - 9} textAnchor="middle">{row.date ? new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(`${row.date}T00:00:00`)) : ""}</text>;
        })}
      </svg>
      <div className="admin-growth-legend"><span><i className="signups" /> Signups</span><span><i className="signins" /> Sign-ins</span></div>
    </div>
  );
}

function OverviewTab({ overview }: { overview: Overview | null }) {
  const metrics = overview?.metrics || {};
  const countries = overview?.geography?.countries || [];
  const paths = overview?.geography?.top_paths || [];
  const users = numeric(metrics.users);
  const verified = numeric(metrics.verified_users);
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

      <div className="admin-metric-grid admin-metric-grid-live">
        <Metric label="Users" value={metrics.users} note="All registered accounts" glyph="◎" />
        <Metric label="New today" value={metrics.signups_today} note="Accounts created today" glyph="＋" />
        <Metric label="Active now" value={metrics.active_now ?? metrics.active_sessions} note="Last 15 minutes" glyph="◉" />
        <Metric label="DAU" value={metrics.dau} note="Active in the last 24h" glyph="↗" />
        <Metric label="WAU" value={metrics.wau} note="Active in the last 7d" glyph="⌁" />
        <Metric label="MAU" value={metrics.mau} note="Active in the last 30d" glyph="◌" />
        <Metric label="Verified" value={metrics.verified_users} note={`${verifiedPercent}% of accounts`} glyph="✓" />
        <Metric label="Workspaces" value={metrics.workspaces} note={`${fmt(metrics.memberships)} memberships`} glyph="◇" />
      </div>

      <div className="admin-overview-grid admin-overview-grid-live">
        <section className="admin-panel admin-growth-panel">
          <header className="admin-panel-heading"><div><span className="eyebrow">Growth</span><h3>Signups & sign-ins</h3></div><span className="admin-panel-chip">30 days</span></header>
          <GrowthChart rows={overview?.activity || []} />
        </section>

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
                <span>{String(item.path || item.pathname || "/")}</span><strong>{fmt(item.views ?? item.visits ?? item.count)}</strong>
              </div>
            )) : <p className="muted">Usage paths will appear as traffic is recorded.</p>}
          </div>
        </section>
      </div>
    </div>
  );
}

function TokenUsageChart({ rows, range }: { rows: UsagePoint[]; range: UsageRange }) {
  const data = rows || [];
  const max = Math.max(1, ...data.map((row) => numeric(row.total_tokens)));
  if (!data.length || !data.some((row) => numeric(row.total_tokens) > 0)) {
    return <div className="admin-chart-empty">No token-bearing model calls in this range yet.</div>;
  }

  const labelEvery = Math.max(1, Math.ceil(data.length / 6));
  return (
    <div className="admin-token-chart" aria-label="Token usage over time">
      <div className="admin-token-bars">
        {data.map((row, index) => {
          const tokens = numeric(row.total_tokens);
          const height = Math.max(2, (tokens / max) * 100);
          const showLabel = index % labelEvery === 0 || index === data.length - 1;
          return (
            <div className="admin-token-slot" key={`${row.bucket}-${index}`} title={`${bucketLabel(row.bucket, range)}: ${exact(tokens)} tokens`}>
              <div className="admin-token-bar-wrap"><i style={{ height: `${height}%` }} /></div>
              <span>{showLabel ? bucketLabel(row.bucket, range) : ""}</span>
            </div>
          );
        })}
      </div>
      <div className="admin-token-scale"><span>{fmt(max)}</span><span>{fmt(max / 2)}</span><span>0</span></div>
    </div>
  );
}

function AIUsageTab({ usage, range, setRange, loading, error }: { usage: AIUsage | null; range: UsageRange; setRange: (range: UsageRange) => void; loading: boolean; error: string }) {
  const totals = usage?.totals || {};
  const tracked = numeric(usage?.coverage?.tracked_calls ?? totals.tracked_calls);
  const calls = numeric(usage?.coverage?.calls ?? totals.calls);
  const coverage = calls ? numeric(usage?.coverage?.percent ?? (tracked / calls) * 100) : 0;
  const models = usage?.by_model || [];

  return (
    <div className="admin-tab-content admin-ai-usage">
      <section className="admin-overview-intro admin-usage-intro">
        <div><span className="eyebrow">Model activity</span><h2>AI usage is back in the owner console.</h2><p>Persisted model traces, token coverage, route mix and usage over time.</p></div>
        <div className="admin-usage-range" aria-label="AI usage time range">
          {usageRanges.map((item) => <button key={item.id} className={range === item.id ? "active" : ""} onClick={() => setRange(item.id)} disabled={loading}>{item.label}</button>)}
        </div>
      </section>

      {error && <div className="admin-usage-error">{error}</div>}

      <div className="admin-usage-coverage">
        <span><i /> {loading ? "Loading model traces…" : calls ? `${coverage.toFixed(0)}% token coverage` : "Waiting for model calls"}</span>
        <small>{calls ? `${exact(tracked)} of ${exact(calls)} successful calls carry token metadata` : time(usage?.generated_at)}</small>
      </div>

      <div className="admin-metric-grid admin-metric-grid-live admin-usage-metrics">
        <Metric label="Total tokens" value={totals.total_tokens} note="Input + output" glyph="Σ" />
        <Metric label="Input tokens" value={totals.input_tokens} note="Prompt & context" glyph="↘" />
        <Metric label="Output tokens" value={totals.output_tokens} note="Model completion" glyph="↗" />
        <Metric label="Tracked calls" value={totals.tracked_calls} note="Calls with usage metadata" glyph="✓" />
        <Metric label="All calls" value={totals.calls} note="Successful model calls" glyph="◉" />
        <Metric label="Models" value={usage?.models ?? models.length} note="Observed in this range" glyph="◇" />
      </div>

      <section className="admin-panel admin-token-panel">
        <header className="admin-panel-heading"><div><span className="eyebrow">Usage</span><h3>Tokens over time</h3></div><span className="admin-panel-chip">{range.toUpperCase()} · all models</span></header>
        <TokenUsageChart rows={usage?.series || []} range={range} />
      </section>

      <section className="admin-panel admin-model-panel">
        <header className="admin-panel-heading"><div><span className="eyebrow">Routes</span><h3>Tokens by model</h3></div><span className="admin-panel-chip">Highest usage first</span></header>
        <div className="admin-model-head"><span>Provider / model</span><span>Input</span><span>Output</span><span>Total</span><span>Share</span></div>
        <div className="admin-model-rows">
          {models.length ? models.map((row, index) => {
            const share = Math.max(0, Math.min(100, numeric(row.share_percent)));
            return (
              <div className="admin-model-row" key={`${row.provider}-${row.model}-${index}`}>
                <div><strong>{row.model || "Unknown model"}</strong><small>{row.provider || "unknown"} · {exact(row.calls)} calls{numeric(row.tracked_calls) !== numeric(row.calls) ? ` · ${exact(row.tracked_calls)} token-tracked` : ""}</small></div>
                <span title={`${exact(row.input_tokens)} input tokens`}>{fmt(row.input_tokens)}</span>
                <span title={`${exact(row.output_tokens)} output tokens`}>{fmt(row.output_tokens)}</span>
                <strong title={`${exact(row.total_tokens)} total tokens`}>{fmt(row.total_tokens)}</strong>
                <div className="admin-model-share"><span>{share.toFixed(1)}%</span><i><b style={{ width: `${share}%` }} /></i></div>
              </div>
            );
          }) : <div className="admin-chart-empty">No model usage has been recorded in this range yet.</div>}
        </div>
        <p className="admin-model-note">Counts come from Operly&apos;s persisted model traces. Older calls may exist without historical token counts when a provider did not return usage metadata.</p>
      </section>
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
  const [aiUsage, setAiUsage] = useState<AIUsage | null>(null);
  const [usageRange, setUsageRange] = useState<UsageRange>("24h");
  const [usageLoading, setUsageLoading] = useState(false);
  const [usageError, setUsageError] = useState("");
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
    if (tab === "overview") api<Overview>("/admin/overview").then(setOverview).catch(() => undefined);
    if (tab === "users" && !users.length) api<any[]>("/admin/users?limit=500").then(setUsers).catch(() => undefined);
    if (tab === "workspaces" && !workspaces.length) api<any[]>("/admin/workspaces?limit=500").then(setWorkspaces).catch(() => undefined);
  }, [session, tab]);

  useEffect(() => {
    if (!session || tab !== "ai-usage") return;
    let cancelled = false;
    setUsageLoading(true);
    setUsageError("");
    api<AIUsage>(`/admin/ai-usage?range=${encodeURIComponent(usageRange)}`)
      .then((result) => { if (!cancelled) setAiUsage(result); })
      .catch((error) => { if (!cancelled) setUsageError(error instanceof Error ? error.message : "AI usage could not be loaded."); })
      .finally(() => { if (!cancelled) setUsageLoading(false); });
    return () => { cancelled = true; };
  }, [session, tab, usageRange]);

  async function refresh() {
    if (!session) return;
    setBusy(true);
    try {
      if (tab === "overview") setOverview(await api<Overview>("/admin/overview"));
      if (tab === "ai-usage") {
        setUsageLoading(true);
        setUsageError("");
        try { setAiUsage(await api<AIUsage>(`/admin/ai-usage?range=${encodeURIComponent(usageRange)}`)); }
        catch (error) { setUsageError(error instanceof Error ? error.message : "AI usage could not be loaded."); }
        finally { setUsageLoading(false); }
      }
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
        {tab === "ai-usage" && <AIUsageTab usage={aiUsage} range={usageRange} setRange={setUsageRange} loading={usageLoading} error={usageError} />}
        {tab === "users" && <UsersTab users={users} query={query} setQuery={setQuery} />}
        {tab === "workspaces" && <WorkspacesTab workspaces={workspaces} />}
      </main>
    </div>
  );
}

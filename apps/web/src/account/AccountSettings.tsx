import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import { PersonalProfile, WorkspaceSummary } from "../app/types";
import { ResolvedTheme, ThemePreference } from "../ui/theme";

type Connector = {
  id: string;
  provider: string;
  displayName: string;
  status: string;
  enabled: boolean;
  account?: string | null;
  avatarUrl?: string | null;
  capabilities?: string[];
  healthStatus?: string | null;
  lastError?: string | null;
};

type ExternalIdentity = {
  id: string;
  provider: string;
  display_name?: string | null;
  avatar_url?: string | null;
  verified_at?: string | null;
};

type SettingsTab = "account" | "appearance" | "connections" | "security" | "workspaces";
type Props = {
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
  initialTab?: SettingsTab;
  themePreference: ThemePreference;
  resolvedTheme: ResolvedTheme;
  onThemePreference: (preference: ThemePreference) => void;
  onClose: () => void;
  onRefresh: () => Promise<unknown>;
  onWorkspace: (workspaceId: string) => Promise<unknown>;
};

const APPEARANCE_OPTIONS: Array<{ value: ThemePreference; title: string; description: string }> = [
  { value: "light", title: "Light", description: "Bright premium workspace surfaces." },
  { value: "dark", title: "Dark", description: "Deep graphite surfaces with Operly glow." },
  { value: "system", title: "System", description: "Follow this device automatically." },
];

function ConnectionAvatar({ url, fallback, label }: { url?: string | null; fallback: string; label: string }) {
  return <span className="connector-logo" style={{ position: "relative", overflow: "hidden" }} aria-label={label}>
    <span aria-hidden="true">{fallback}</span>
    {url && <img src={url} alt="" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = "none"; }} style={{ position: "absolute", inset: 0, width: "100%", height: "100%", objectFit: "cover" }} />}
  </span>;
}

export function AccountSettings({ profile, workspaces, initialTab = "account", themePreference, resolvedTheme, onThemePreference, onClose, onRefresh, onWorkspace }: Props) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [discordIdentity, setDiscordIdentity] = useState<ExternalIdentity | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function loadConnections() {
    try {
      const [nextConnectors, identities] = await Promise.all([
        api<Connector[]>("/personal-connectors"),
        api<ExternalIdentity[]>("/identities"),
      ]);
      setConnectors(nextConnectors);
      setDiscordIdentity(identities.find((identity) => identity.provider === "discord") || null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Personal connections are unavailable");
    }
  }
  useEffect(() => { if (tab === "connections") loadConnections(); }, [tab]);

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(null); setMessage(null);
    try { await api("/personal-agent/me", { method: "PATCH", body: JSON.stringify({ display_name: form.get("display_name") }) }); await onRefresh(); setMessage("Account profile updated."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Account profile could not be updated"); }
    finally { setBusy(false); }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); const next = String(form.get("new_password") || ""); const confirm = String(form.get("confirm_password") || "");
    if (next !== confirm) { setError("New passwords do not match."); return; }
    if (next.length < 12) { setError("Use at least 12 characters for the new password."); return; }
    setBusy(true); setError(null); setMessage(null);
    try { await api("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password") || null, new_password: next }) }); event.currentTarget.reset(); setMessage("Password changed."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Password could not be changed"); }
    finally { setBusy(false); }
  }

  async function signOut() {
    setBusy(true); setError(null);
    try { await api("/auth/logout", { method: "POST", body: "{}" }); window.location.assign("/login"); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Sign out failed"); setBusy(false); }
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget); setBusy(true); setError(null);
    try { const created = await api<WorkspaceSummary>("/workspaces", { method: "POST", body: JSON.stringify({ name: form.get("name"), timezone: form.get("timezone") || "UTC" }) }); await onRefresh(); onClose(); await onWorkspace(created.id); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace could not be created"); }
    finally { setBusy(false); }
  }

  async function connectGoogle() {
    setBusy(true); setError(null);
    try { const result = await api<{ authorization_url: string }>("/personal-connectors/google/connect?tier=assistant", { method: "POST", body: "{}" }); window.location.assign(result.authorization_url); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Google authorization could not start"); setBusy(false); }
  }

  function connectDiscord() {
    window.location.assign("/api/identities/discord/sign-in");
  }

  async function connectorAction(action: () => Promise<unknown>) {
    setError(null); setMessage(null);
    try { await action(); await loadConnections(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Connector action failed"); }
  }

  async function disconnectDiscord() {
    if (!discordIdentity) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      await api(`/identities/${discordIdentity.id}`, { method: "DELETE" });
      await loadConnections();
      setMessage("Discord disconnected.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Discord could not be disconnected");
    } finally {
      setBusy(false);
    }
  }

  const heading = tab === "account" ? "My account" : tab === "appearance" ? "Appearance" : tab === "connections" ? "Personal connections" : tab === "security" ? "Password & security" : "Your workspaces";
  return <div className="editor-overlay account-settings-overlay"><button className="editor-backdrop" onClick={onClose} aria-label="Close account settings"></button><section className="account-settings-card" role="dialog" aria-modal="true" aria-label="Account settings">
    <aside className="account-settings-nav"><div><span className="eyebrow">Personal</span><strong>User settings</strong></div><button className={tab === "account" ? "active" : ""} onClick={() => setTab("account")}>My account</button><button className={tab === "appearance" ? "active" : ""} onClick={() => setTab("appearance")}>Appearance</button><button className={tab === "connections" ? "active" : ""} onClick={() => setTab("connections")}>Connections</button><button className={tab === "security" ? "active" : ""} onClick={() => setTab("security")}>Security</button><button className={tab === "workspaces" ? "active" : ""} onClick={() => setTab("workspaces")}>Workspaces</button></aside>
    <main className="account-settings-main"><header><div><span className="eyebrow">Private account</span><h2>{heading}</h2></div><button onClick={onClose} aria-label="Close">×</button></header>{error && <div className="inline-error">{error}</div>}{message && <div className="success-banner">{message}</div>}
      {tab === "account" && <form className="form-stack account-form" onSubmit={saveProfile}><div className="account-identity"><span>{(profile?.display_name || profile?.email || "Me").slice(0,1).toUpperCase()}</span><div><strong>{profile?.display_name || "Operly user"}</strong><small>{profile?.email}</small></div></div><label>Display name<input name="display_name" defaultValue={profile?.display_name || ""} required maxLength={200} /></label><label>Email<input value={profile?.email || ""} disabled readOnly /><small>Email changes require a verified identity flow.</small></label><button className="primary-button" disabled={busy}>{busy ? "Saving…" : "Save profile"}</button></form>}
      {tab === "appearance" && <section className="settings-section-stack"><div className="settings-callout"><div><strong>Choose how Operly feels</strong><p>Appearance is a private device preference. It changes presentation only; workspace behavior and permissions stay unchanged.</p></div></div><div className="appearance-grid" role="radiogroup" aria-label="Appearance">{APPEARANCE_OPTIONS.map((option) => <button key={option.value} type="button" role="radio" aria-checked={themePreference === option.value} data-choice={option.value} className={`appearance-choice ${themePreference === option.value ? "active" : ""}`} onClick={() => onThemePreference(option.value)}><span className="appearance-preview" aria-hidden="true"><span></span><div></div></span><strong>{option.title}</strong><small>{option.description}</small></button>)}</div><p className="appearance-current">Currently rendered in <strong>{resolvedTheme}</strong> mode{themePreference === "system" ? " from your system preference" : ""}.</p></section>}
      {tab === "connections" && <div className="settings-section-stack"><div className="settings-callout"><div><strong>Your tools, wherever you go</strong><p>Personal connections belong to you, not a workspace. Workspace members cannot see these credentials or this private transcript.</p></div><div className="row-actions"><button className="primary-button" disabled={busy} onClick={connectGoogle}>Connect Google</button>{!discordIdentity && <button className="secondary-button" disabled={busy} onClick={connectDiscord}>Connect Discord</button>}</div></div>{connectors.length ? connectors.map((connector) => <article className="personal-connector-card" key={connector.id}><ConnectionAvatar url={connector.avatarUrl} fallback={connector.provider.slice(0,1).toUpperCase()} label={`${connector.displayName} profile picture`} /><div><strong>{connector.displayName}</strong><p>{connector.account || connector.provider}</p><small>{(connector.capabilities || []).slice(0,5).join(" · ") || "No exposed capabilities"}</small>{connector.lastError && <span className="form-error">{connector.lastError}</span>}</div><span className={`status-chip status-${connector.healthStatus || connector.status}`}>{connector.healthStatus || connector.status}</span><div className="row-actions"><button className="secondary-button" onClick={() => connectorAction(() => api(`/personal-connectors/${connector.id}/test`, { method: "POST", body: "{}" }))}>Test</button><button className="danger-button" onClick={() => connectorAction(() => api(`/personal-connectors/${connector.id}`, { method: "DELETE" }))}>Disconnect</button></div></article>) : <div className="empty-panel">No OAuth tool connectors yet.</div>}<article className="personal-connector-card" data-provider="discord"><ConnectionAvatar url={discordIdentity?.avatar_url} fallback="D" label="Personal Discord profile picture" /><div><strong>Personal Discord</strong><p>{discordIdentity?.display_name || (discordIdentity ? "Discord account connected" : "Not connected")}</p><small>{discordIdentity ? "Discord DMs resolve to this Personal Operly user. Server and workspace access stays separately authorized." : "Connect your Discord identity so DMs resolve to this Personal Operly user. This does not grant server or workspace access."}</small></div><span className={`status-chip ${discordIdentity ? "status-healthy" : ""}`}>{discordIdentity ? "Connected" : "Not connected"}</span><div className="row-actions">{discordIdentity ? <button className="danger-button" disabled={busy} onClick={disconnectDiscord}>Disconnect</button> : <button className="primary-button" disabled={busy} onClick={connectDiscord}>Connect Discord</button>}</div></article></div>}
      {tab === "security" && <div className="settings-section-stack"><form className="form-stack account-form" onSubmit={changePassword}><div><strong>Change password</strong><p className="settings-copy">Password fields go directly to authentication; they are never sent through a model conversation.</p></div><label>Current password<input name="current_password" type="password" autoComplete="current-password" /></label><label>New password<input name="new_password" type="password" minLength={12} required autoComplete="new-password" /></label><label>Confirm new password<input name="confirm_password" type="password" minLength={12} required autoComplete="new-password" /></label><button className="primary-button" disabled={busy}>{busy ? "Updating…" : "Change password"}</button></form><div className="danger-settings"><div><strong>Sign out</strong><p>End this Operly session on this browser.</p></div><button className="danger-button" disabled={busy} onClick={signOut}>Sign out</button></div></div>}
      {tab === "workspaces" && <div className="settings-section-stack"><form className="form-stack create-workspace-card" onSubmit={createWorkspace}><div><strong>Create a workspace</strong><p>A workspace is a shared boundary for a business, team, project, or community. Personal Operly remains above it.</p></div><label>Name<input name="name" required maxLength={200} placeholder="ORB Eats" /></label><label>Timezone<input name="timezone" required maxLength={100} defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"} /></label><button className="primary-button" disabled={busy}>Create workspace</button></form><div className="workspace-settings-list">{workspaces.map((workspace) => <button key={workspace.id} onClick={async () => { onClose(); await onWorkspace(workspace.id); }}><span>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : workspace.name.slice(0,2).toUpperCase()}</span><div><strong>{workspace.name}</strong><small>{workspace.role} · {workspace.timezone || "UTC"}</small></div><b>›</b></button>)}</div></div>}
    </main>
  </section></div>;
}

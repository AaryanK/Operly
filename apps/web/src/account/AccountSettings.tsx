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

type WorkspaceCreateResult = { ok: boolean; workspace: WorkspaceSummary };
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
  { value: "dark", title: "Dark", description: "The native Operly workspace theme." },
  { value: "light", title: "Light", description: "Bright surfaces for daylight work." },
  { value: "system", title: "System", description: "Follow this device automatically." },
];

function initials(value: string) {
  return value.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "ME";
}

function ConnectionAvatar({ url, fallback, label }: { url?: string | null; fallback: string; label: string }) {
  return <span className="connector-logo discord-settings-connector-avatar" aria-label={label}>
    <span aria-hidden="true">{fallback}</span>
    {url && <img src={url} alt="" referrerPolicy="no-referrer" onError={(event) => { event.currentTarget.style.display = "none"; }} />}
  </span>;
}

export function AccountSettings({ profile, workspaces, initialTab = "account", themePreference, resolvedTheme, onThemePreference, onClose, onRefresh, onWorkspace }: Props) {
  const [tab, setTab] = useState<SettingsTab>(initialTab);
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [connectionsLoaded, setConnectionsLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  useEffect(() => { setTab(initialTab); setError(null); setMessage(null); }, [initialTab]);

  async function loadConnections() {
    setError(null);
    try {
      const nextConnectors = await api<Connector[]>("/personal-connectors");
      setConnectors(nextConnectors);
      setConnectionsLoaded(true);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Personal connections are unavailable");
    }
  }

  useEffect(() => {
    if (tab === "connections" && !connectionsLoaded) void loadConnections();
  }, [connectionsLoaded, tab]);

  async function saveProfile(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError(null); setMessage(null);
    try {
      await api("/auth/me", { method: "PATCH", body: JSON.stringify({ display_name: form.get("display_name") }) });
      await onRefresh();
      setMessage("Profile updated.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Profile could not be updated");
    } finally { setBusy(false); }
  }

  async function changePassword(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const next = String(form.get("new_password") || "");
    const confirm = String(form.get("confirm_password") || "");
    if (next !== confirm) { setError("New passwords do not match."); return; }
    if (next.length < 12) { setError("Use at least 12 characters for the new password."); return; }
    setBusy(true); setError(null); setMessage(null);
    try {
      await api("/auth/change-password", { method: "POST", body: JSON.stringify({ current_password: form.get("current_password") || null, new_password: next }) });
      event.currentTarget.reset();
      setMessage("Password changed.");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Password could not be changed");
    } finally { setBusy(false); }
  }

  async function signOut() {
    setBusy(true); setError(null);
    try {
      await api("/auth/logout", { method: "POST", body: "{}" });
      window.location.assign("/login");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Sign out failed");
      setBusy(false);
    }
  }

  async function createWorkspace(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true); setError(null); setMessage(null);
    try {
      const created = await api<WorkspaceCreateResult>("/auth/workspaces", {
        method: "POST",
        body: JSON.stringify({ name: form.get("name"), timezone: form.get("timezone") || "UTC" }),
      });
      await onRefresh();
      onClose();
      await onWorkspace(created.workspace.id);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Workspace could not be created");
    } finally { setBusy(false); }
  }

  async function connectGoogle() {
    setBusy(true); setError(null);
    try {
      const result = await api<{ authorization_url: string }>("/personal-connectors/google/connect?tier=assistant", { method: "POST", body: "{}" });
      window.location.assign(result.authorization_url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Google authorization could not start");
      setBusy(false);
    }
  }

  async function connectCanva() {
    setBusy(true); setError(null);
    try {
      const result = await api<{ authorization_url: string }>("/personal-connectors/canva/connect", { method: "POST", body: "{}" });
      window.location.assign(result.authorization_url);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Canva authorization could not start");
      setBusy(false);
    }
  }

  async function connectorAction(action: () => Promise<unknown>, success: string) {
    setBusy(true); setError(null); setMessage(null);
    try {
      await action();
      await loadConnections();
      setMessage(success);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Connector action failed");
    } finally { setBusy(false); }
  }

  const title = tab === "account" ? "My Account" : tab === "appearance" ? "Appearance" : tab === "connections" ? "Connections" : tab === "workspaces" ? "Workspaces" : "Password & Security";
  const accountName = profile?.display_name || "Operly user";
  const accountEmail = profile?.email || "Private account";

  return <div className="account-settings-overlay discord-settings-overlay" role="presentation">
    <button className="discord-settings-backdrop" type="button" onClick={onClose} aria-label="Close user settings" />
    <section className="discord-settings" role="dialog" aria-modal="true" aria-label="User settings">
      <aside className="discord-settings-sidebar">
        <div className="discord-settings-profile-mini">
          <span className="discord-settings-avatar">{initials(accountName || accountEmail)}</span>
          <div><strong>{accountName}</strong><small>{accountEmail}</small></div>
        </div>
        <span className="discord-settings-group-label">USER SETTINGS</span>
        <button className={tab === "account" ? "active" : ""} onClick={() => setTab("account")}>My Account</button>
        <button className={tab === "appearance" ? "active" : ""} onClick={() => setTab("appearance")}>Appearance</button>
        <button className={tab === "connections" ? "active" : ""} onClick={() => setTab("connections")}>Connections</button>
        <span className="discord-settings-separator" />
        <button className={tab === "workspaces" ? "active" : ""} onClick={() => setTab("workspaces")}>Workspaces</button>
        <button className={tab === "security" ? "active" : ""} onClick={() => setTab("security")}>Password & Security</button>
        <span className="discord-settings-separator" />
        <button className="discord-settings-signout" disabled={busy} onClick={() => void signOut()}>Sign Out</button>
      </aside>

      <main className="discord-settings-main">
        <header className="discord-settings-header"><div><small>USER SETTINGS</small><h2>{title}</h2></div><button className="discord-settings-close" type="button" onClick={onClose} aria-label="Close settings">×</button></header>
        {error && <div className="discord-settings-alert error">{error}</div>}
        {message && <div className="discord-settings-alert success">{message}</div>}

        {tab === "account" && <section className="discord-settings-section">
          <div className="discord-profile-card">
            <div className="discord-profile-banner" />
            <div className="discord-profile-body">
              <span className="discord-profile-avatar">{initials(accountName || accountEmail)}</span>
              <div className="discord-profile-copy"><strong>{accountName}</strong><small>{accountEmail}</small></div>
            </div>
          </div>
          <form className="discord-settings-form" onSubmit={saveProfile}>
            <label>DISPLAY NAME<input name="display_name" defaultValue={profile?.display_name || ""} required maxLength={200} /></label>
            <label>EMAIL<input value={accountEmail} disabled readOnly /><small>Your verified sign-in email is managed by authentication.</small></label>
            <button className="discord-settings-primary" disabled={busy}>{busy ? "Saving…" : "Save Changes"}</button>
          </form>
        </section>}

        {tab === "appearance" && <section className="discord-settings-section">
          <p className="discord-settings-copy">Choose how Operly is rendered on this device. This does not change workspace permissions or agent behavior.</p>
          <div className="discord-appearance-grid" role="radiogroup" aria-label="Appearance">
            {APPEARANCE_OPTIONS.map((option) => <button key={option.value} type="button" role="radio" aria-checked={themePreference === option.value} className={themePreference === option.value ? "active" : ""} onClick={() => onThemePreference(option.value)}>
              <span className={`discord-appearance-preview ${option.value}`}><i /><b /></span><strong>{option.title}</strong><small>{option.description}</small>
            </button>)}
          </div>
          <p className="discord-settings-copy">Currently rendered in <strong>{resolvedTheme}</strong> mode.</p>
        </section>}

        {tab === "connections" && <section className="discord-settings-section">
          <div className="discord-settings-section-head"><div><h3>Connected Apps</h3><p>Only current Personal Operly connectors are shown here. Legacy identity routes are intentionally not used by this screen.</p></div><div><button className="discord-settings-secondary" disabled={busy} onClick={() => void connectGoogle()}>Connect Google</button><button className="discord-settings-secondary" disabled={busy} onClick={() => void connectCanva()}>Connect Canva</button></div></div>
          <div className="discord-connector-list">
            {connectionsLoaded && connectors.length === 0 && <div className="discord-settings-empty">No personal connectors yet.</div>}
            {connectors.map((connector) => <article className="discord-connector-row" key={connector.id}>
              <ConnectionAvatar url={connector.avatarUrl} fallback={connector.provider.slice(0, 1).toUpperCase()} label={`${connector.displayName} profile`} />
              <div><strong>{connector.displayName}</strong><small>{connector.account || connector.provider}</small>{connector.lastError && <span>{connector.lastError}</span>}</div>
              <em>{connector.healthStatus || connector.status}</em>
              <div className="discord-connector-actions"><button disabled={busy} onClick={() => void connectorAction(() => api(`/personal-connectors/${connector.id}/test`, { method: "POST", body: "{}" }), `${connector.displayName} connection tested.`)}>Test</button><button disabled={busy} onClick={() => void connectorAction(() => api(`/personal-connectors/${connector.id}`, { method: "DELETE" }), `${connector.displayName} disconnected.`)}>Disconnect</button></div>
            </article>)}
          </div>
        </section>}

        {tab === "workspaces" && <section className="discord-settings-section">
          <div className="discord-settings-section-head"><div><h3>Your Workspaces</h3><p>Workspaces are shared authorization boundaries. Personal Operly stays private above them.</p></div></div>
          <form className="discord-settings-form discord-create-workspace" onSubmit={createWorkspace}>
            <label>WORKSPACE NAME<input name="name" required maxLength={200} placeholder="New workspace" /></label>
            <label>TIMEZONE<input name="timezone" required maxLength={100} defaultValue={Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"} /></label>
            <button className="discord-settings-primary" disabled={busy}>{busy ? "Creating…" : "Create Workspace"}</button>
          </form>
          <div className="discord-workspace-list">{workspaces.map((workspace) => <button key={workspace.id} disabled={busy} onClick={async () => { onClose(); await onWorkspace(workspace.id); }}><span>{workspace.name.slice(0, 2).toUpperCase()}</span><div><strong>{workspace.name}</strong><small>{workspace.role}{workspace.current ? " · Current" : ""}</small></div><b>›</b></button>)}</div>
        </section>}

        {tab === "security" && <section className="discord-settings-section">
          <form className="discord-settings-form" onSubmit={changePassword}>
            <h3>Change Password</h3>
            <p className="discord-settings-copy">Password fields go directly to authentication and never through a model conversation.</p>
            <label>CURRENT PASSWORD<input name="current_password" type="password" autoComplete="current-password" /></label>
            <label>NEW PASSWORD<input name="new_password" type="password" minLength={12} required autoComplete="new-password" /></label>
            <label>CONFIRM NEW PASSWORD<input name="confirm_password" type="password" minLength={12} required autoComplete="new-password" /></label>
            <button className="discord-settings-primary" disabled={busy}>{busy ? "Updating…" : "Change Password"}</button>
          </form>
          <div className="discord-danger-zone"><div><strong>Sign out of this browser</strong><small>Your other active sessions are not changed.</small></div><button disabled={busy} onClick={() => void signOut()}>Sign Out</button></div>
        </section>}
      </main>
    </section>
  </div>;
}

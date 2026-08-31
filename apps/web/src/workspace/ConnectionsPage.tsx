import { useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Connection = {
  id: string;
  provider: string;
  connector_type: string;
  display_name: string;
  account?: string | null;
  status: string;
  enabled: boolean;
  health_status?: string | null;
  last_health_check?: string | null;
  last_error?: string | null;
  scopes?: string[];
  capabilities?: string[];
  guild_id?: string;
};

type DiscordStatus = {
  configured: boolean;
  ready: boolean;
  bot_user: string | null;
  invite_url: string | null;
  ai_enabled: boolean;
};

function providerName(provider: string) {
  if (provider === "google") return "Google Workspace";
  if (provider === "canva") return "Canva";
  if (provider === "discord") return "Discord";
  return provider;
}

export function ConnectionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [discord, setDiscord] = useState<DiscordStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const canManage = workspace.role === "owner";

  async function reload() {
    setLoading(true);
    setMessage(null);
    try {
      const [rows, discordStatus] = await Promise.all([
        api<Connection[]>("/connectors"),
        api<DiscordStatus>("/connectors/discord/status"),
      ]);
      setConnections(rows);
      setDiscord(discordStatus);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not load workspace connections");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function startOAuth(provider: "google" | "canva") {
    setBusy(provider);
    setMessage(null);
    try {
      const path = provider === "google"
        ? "/connectors/google/connect?tier=assistant"
        : "/connectors/canva/connect";
      const result = await api<{ authorization_url: string }>(path, { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : `Could not connect ${providerName(provider)}`);
      setBusy(null);
    }
  }

  function addDiscord() {
    if (!discord?.invite_url) {
      setMessage("Discord bot installation is not configured on the server yet.");
      return;
    }
    window.open(discord.invite_url, "_blank", "noopener,noreferrer");
    setMessage(`After adding the bot to your server, run !operly bind ${workspace.name} in that Discord server.`);
  }

  async function testConnection(connection: Connection) {
    setBusy(`test:${connection.id}`);
    setMessage(null);
    try {
      const result = await api<{ ok: boolean; health_status: string; error?: string | null }>(
        `/connectors/${encodeURIComponent(connection.id)}/test`,
        { method: "POST" },
      );
      setMessage(result.ok ? `${providerName(connection.provider)} connection is healthy.` : result.error || "Connection test failed.");
      await reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Connection test failed");
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(connection: Connection) {
    if (!window.confirm(`Disconnect ${providerName(connection.provider)} from this workspace?`)) return;
    setBusy(`disconnect:${connection.id}`);
    setMessage(null);
    try {
      await api(`/connectors/${encodeURIComponent(connection.id)}`, { method: "DELETE" });
      await reload();
    } catch (error) {
      setMessage(error instanceof Error ? error.message : "Could not disconnect integration");
    } finally {
      setBusy(null);
    }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header">
      <div>
        <span className="eyebrow">Workspace modules · integrations</span>
        <h1>Connections</h1>
        <p>Connect external services to this workspace. Operly resolves both workspace permissions and provider-side scopes or resource permissions before a tool is exposed or executed.</p>
      </div>
      {canManage && <div className="page-actions">
        <button type="button" onClick={() => startOAuth("google")} disabled={busy === "google"}>{busy === "google" ? "Connecting…" : "Connect Google"}</button>
        <button type="button" onClick={() => startOAuth("canva")} disabled={busy === "canva"}>{busy === "canva" ? "Connecting…" : "Connect Canva"}</button>
        <button type="button" onClick={addDiscord}>Add Discord bot</button>
      </div>}
    </header>

    {message && <div className="inline-notice">{message}</div>}
    {loading && <div className="loading-panel">Loading workspace connections…</div>}

    {!loading && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Connections</span><strong>{connections.length}</strong><small>Owned by this workspace</small></article>
        <article className="metric-card"><span>Healthy</span><strong>{connections.filter((item) => item.health_status === "healthy").length}</strong><small>Latest deterministic health state</small></article>
        <article className="metric-card"><span>Discord bot</span><strong>{discord?.ready ? "Online" : discord?.configured ? "Starting / offline" : "Not configured"}</strong><small>{discord?.bot_user || "No connected bot user"}</small></article>
        <article className="metric-card"><span>Discord AI</span><strong>Off</strong><small>Deterministic commands/tools only</small></article>
      </section>

      {discord?.configured && <section className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Discord runtime</span><h2>Deterministic bot</h2></div><span className={`status-chip ${discord.ready ? "status-active" : ""}`}>{discord.ready ? "Online" : "Offline"}</span></div>
        <p>The bot starts with the Operly API process. It supports deterministic commands such as <code>!operly status</code>, <code>!operly bind WORKSPACE</code>, and <code>!operly help</code>. Mentions do not invoke a model.</p>
      </section>}

      <section className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Workspace-owned accounts</span><h2>Connected services</h2></div><span>{connections.length}</span></div>
        <div className="row-list">
          {connections.map((connection) => <article className="data-row stacked" key={connection.id}>
            <div>
              <strong>{providerName(connection.provider)}</strong>
              <small>{connection.display_name}{connection.account ? ` · ${connection.account}` : ""}</small>
              <small>Status: {connection.status} · Health: {connection.health_status || "unknown"}</small>
              {!!connection.scopes?.length && <small>Provider scopes: {connection.scopes.join(", ")}</small>}
              {!!connection.capabilities?.length && <small>Capabilities: {connection.capabilities.join(", ")}</small>}
            </div>
            {canManage && <div className="page-actions">
              <button type="button" onClick={() => testConnection(connection)} disabled={busy === `test:${connection.id}`}>Test</button>
              <button type="button" onClick={() => disconnect(connection)} disabled={busy === `disconnect:${connection.id}`}>Disconnect</button>
            </div>}
          </article>)}
          {!connections.length && <div className="empty-panel">No external services are connected to this workspace yet.</div>}
        </div>
      </section>
    </>}
  </main>;
}

import { providerName, useIntegrationRuntime } from "./runtime";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-panel">{children}</div>;
}

export function ConnectionsManager() {
  const runtime = useIntegrationRuntime();
  const googleConnected = runtime.connections.some((item) => item.provider === "google");
  const canvaConnected = runtime.connections.some((item) => item.provider === "canva");

  return (
    <>
      <section className="integration-connect-actions">
        <article className="data-card">
          <span className="integration-provider-icon">G</span>
          <h2>Google Workspace</h2>
          <p>
            Gmail and Calendar scopes are resolved independently. Reconnect to add newly required
            scopes without giving a Workspace member more Operly authority.
          </p>
          {runtime.canManage && (
            <button
              type="button"
              className="primary-button"
              onClick={() => void runtime.startOAuth("google")}
              disabled={runtime.busy === "google"}
            >
              {googleConnected ? "Reconnect / expand scopes" : "Connect Google"}
            </button>
          )}
        </article>

        <article className="data-card">
          <span className="integration-provider-icon">C</span>
          <h2>Canva</h2>
          <p>
            Design, export, Uploads, brand-template, and Autofill scopes are explicit. Existing
            connections must reconnect once to grant newly added authoring scopes.
          </p>
          {runtime.canManage && (
            <button
              type="button"
              className="primary-button"
              onClick={() => void runtime.startOAuth("canva")}
              disabled={runtime.busy === "canva"}
            >
              {canvaConnected ? "Reconnect / expand scopes" : "Connect Canva"}
            </button>
          )}
        </article>

        <article className="data-card">
          <span className="integration-provider-icon">#</span>
          <h2>Discord</h2>
          <p>
            Install the deterministic bot, then bind a server to this Workspace. The bot has no AI
            message-dispatch path yet.
          </p>
          <span
            className={`status-chip ${runtime.discordStatus?.ready ? "status-active" : ""}`}
          >
            {runtime.discordStatus?.ready ? "Bot online" : "AI off"}
          </span>
          {runtime.canManage && (
            <button type="button" className="primary-button" onClick={runtime.addDiscord}>
              Add Discord bot
            </button>
          )}
        </article>
      </section>

      <section className="data-card">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Workspace-owned accounts</span>
            <h2>Connected services</h2>
          </div>
          <span>{runtime.connections.length}</span>
        </div>
        <div className="row-list">
          {runtime.connections.map((connection) => (
            <article className="data-row stacked" key={connection.id}>
              <div>
                <strong>{providerName(connection.provider)}</strong>
                <small>
                  {connection.display_name}
                  {connection.account ? ` · ${connection.account}` : ""}
                </small>
                <small>
                  Status: {connection.status} · Health: {connection.health_status || "unknown"}
                </small>
                {connection.last_error && <small>Last error: {connection.last_error}</small>}
                {!!connection.scopes?.length && (
                  <details>
                    <summary>{connection.scopes.length} provider scopes</summary>
                    <small>{connection.scopes.join(", ")}</small>
                  </details>
                )}
              </div>
              {runtime.canManage && (
                <div className="page-actions">
                  <button
                    type="button"
                    onClick={() => void runtime.testConnection(connection)}
                    disabled={runtime.busy === `test:${connection.id}`}
                  >
                    Test
                  </button>
                  <button
                    type="button"
                    onClick={() => void runtime.disconnect(connection)}
                    disabled={runtime.busy === `disconnect:${connection.id}`}
                  >
                    Disconnect
                  </button>
                </div>
              )}
            </article>
          ))}
          {!runtime.connections.length && <Empty>No external services are connected yet.</Empty>}
        </div>
      </section>
    </>
  );
}

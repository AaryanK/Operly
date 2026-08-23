import { useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Connector = {
  id: string;
  provider: string;
  display_name: string;
  connector_type: string;
  status: string;
  enabled: boolean;
  capabilities?: string[];
  health_status?: string | null;
};

const builtins = [
  { name: "CRM", description: "Customers, leads, quotes and orders", category: "Business data" },
  { name: "Tasks & approvals", description: "Human-controlled execution and follow-up", category: "Operations" },
  { name: "Operly Intelligence", description: "Workspace reasoning through governed tools", category: "AI" },
  { name: "Solutions", description: "Business software and digital presence", category: "Build" },
];
const human = (value: string) => value.replaceAll("_", " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function PluginsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [connectors, setConnectors] = useState<Connector[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    api<Connector[]>("/connectors").then(setConnectors).catch((caught) => setError(caught instanceof Error ? caught.message : "Plugins are unavailable"));
  }, [workspace.id]);

  return <main className="workspace-page">
    <header className="surface-header page-header"><div><span className="eyebrow">Extend</span><h1>Plugins</h1><p>Operly product modules and connector extensions share one governed capability model. A plugin never gets a parallel security or frontend path.</p></div></header>
    {error && <div className="inline-error page-error">{error}</div>}
    <section className="plugin-grid">
      {builtins.map((plugin) => <article className="plugin-card" key={plugin.name}><span className="plugin-icon">✦</span><div><strong>{plugin.name}</strong><p>{plugin.description}</p></div><div className="plugin-footer"><span>{plugin.category}</span><span className="status-chip status-active">Built in</span></div></article>)}
      {connectors.map((connector) => <article className="plugin-card" key={connector.id}><span className="plugin-icon external">{connector.provider.slice(0, 1).toUpperCase()}</span><div><strong>{connector.display_name}</strong><p>{human(connector.connector_type)} · {(connector.capabilities || []).length} exposed capabilities</p></div><div className="plugin-footer"><span>Workspace connector</span><span className={`status-chip status-${(connector.health_status || connector.status).replaceAll("_", "-")}`}>{human(connector.health_status || connector.status)}</span></div><details><summary>Capabilities</summary><code>{(connector.capabilities || []).join("\n") || "No connector capabilities exposed"}</code></details></article>)}
    </section>
    <section className="info-banner"><strong>Controls stay with the owning surface.</strong><p>Credential health, OAuth, disable, and disconnect actions live under Connections. Plugins shows what the workspace can use without duplicating those controls.</p></section>
  </main>;
}

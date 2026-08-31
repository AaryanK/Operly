import { useState } from "react";

import { WorkspaceSummary } from "../../app/types";
import { CalendarPanel } from "./CalendarPanel";
import { CanvaPanel } from "./CanvaPanel";
import { ConnectionsManager } from "./ConnectionsManager";
import { DiscordPanel } from "./DiscordPanel";
import { GmailPanel } from "./GmailPanel";
import { IntegrationTab, OverviewPanel } from "./OverviewPanel";
import {
  IntegrationApprovalDialog,
  IntegrationRuntimeProvider,
  useIntegrationRuntime,
} from "./runtime";

const TABS: Array<{ id: IntegrationTab; label: string; mark: string }> = [
  { id: "overview", label: "Overview", mark: "◫" },
  { id: "gmail", label: "Gmail", mark: "✉" },
  { id: "calendar", label: "Calendar", mark: "□" },
  { id: "canva", label: "Canva", mark: "◇" },
  { id: "discord", label: "Discord", mark: "#" },
  { id: "connections", label: "Connections", mark: "↗" },
];

function initialTab(): IntegrationTab {
  const query = new URLSearchParams(window.location.search);
  const requested = query.get("provider") as IntegrationTab | null;
  if (requested && TABS.some((item) => item.id === requested)) return requested;
  if (query.get("connector")) return "connections";
  return "overview";
}

function WorkbenchBody() {
  const runtime = useIntegrationRuntime();
  const [tab, setTab] = useState<IntegrationTab>(initialTab);

  function open(next: IntegrationTab) {
    runtime.clearFeedback();
    setTab(next);
  }

  return (
    <main className="workspace-page integration-workbench">
      <header className="surface-header page-header">
        <div>
          <span className="eyebrow">Workspace modules · deterministic integrations</span>
          <h1>Integrations</h1>
          <p>
            Use Gmail, Calendar, Canva, and Discord directly from the Workspace. Every operation
            resolves current Operly authority plus the provider's own scopes or live resource
            permissions before execution.
          </p>
        </div>
        <div className="page-actions">
          <button type="button" onClick={() => void runtime.reload()}>
            Refresh authority
          </button>
        </div>
      </header>

      <nav className="integration-tabs" aria-label="Integration tools">
        {TABS.map((item) => (
          <button
            type="button"
            key={item.id}
            className={tab === item.id ? "active" : ""}
            onClick={() => open(item.id)}
          >
            <span>{item.mark}</span>
            {item.label}
          </button>
        ))}
      </nav>

      {runtime.notice && <div className="inline-notice">{runtime.notice}</div>}
      {runtime.error && <div className="inline-error page-error">{runtime.error}</div>}
      {runtime.loading && (
        <div className="loading-panel">
          Resolving current Workspace permissions and provider authority…
        </div>
      )}

      {!runtime.loading && tab === "overview" && <OverviewPanel onOpen={open} />}
      {!runtime.loading && tab === "gmail" && <GmailPanel />}
      {!runtime.loading && tab === "calendar" && <CalendarPanel />}
      {!runtime.loading && tab === "canva" && <CanvaPanel />}
      {!runtime.loading && tab === "discord" && <DiscordPanel />}
      {!runtime.loading && tab === "connections" && <ConnectionsManager />}

      <IntegrationApprovalDialog />
    </main>
  );
}

export function IntegrationWorkbench({ workspace }: { workspace: WorkspaceSummary }) {
  return (
    <IntegrationRuntimeProvider workspace={workspace}>
      <WorkbenchBody />
    </IntegrationRuntimeProvider>
  );
}

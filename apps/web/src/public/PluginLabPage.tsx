import { useMemo, useState } from "react";

import { OperlyMark } from "../ui/OperlyMark";

type DemoPlugin = {
  id: string;
  name: string;
  category: string;
  detail: string;
};

const DEMO_PLUGINS: DemoPlugin[] = [
  { id: "e2e.lead-pulse", name: "Lead Pulse", category: "Sales", detail: "Lead priority and follow-up" },
  { id: "e2e.invoice-watch", name: "Invoice Watch", category: "Finance", detail: "Receivables and invoice exceptions" },
  { id: "e2e.inventory-forecast", name: "Inventory Forecast", category: "Operations", detail: "Inventory demand planning" },
  { id: "e2e.support-triage", name: "Support Triage", category: "Support", detail: "Urgency and ownership queue" },
  { id: "e2e.campaign-board", name: "Campaign Board", category: "Marketing", detail: "Campaign launch lanes" },
  { id: "e2e.contract-review", name: "Contract Review", category: "Documents", detail: "Contract risk review" },
  { id: "e2e.sales-radar", name: "Sales Radar", category: "Sales", detail: "Pipeline velocity and opportunities" },
  { id: "e2e.data-reconcile", name: "Data Reconcile", category: "Data", detail: "Cross-record reconciliation" },
];

function workspaceIdFromPath(pathname: string): string {
  const prefix = "/plugin-lab/";
  const raw = pathname.startsWith(prefix) ? pathname.slice(prefix.length).split("/", 1)[0] : "";
  try { return decodeURIComponent(raw); } catch { return raw; }
}

export function PluginLabPage({ pathname }: { pathname: string }) {
  const workspaceId = useMemo(() => workspaceIdFromPath(pathname), [pathname]);
  const [selectedId, setSelectedId] = useState(DEMO_PLUGINS[0].id);
  const selected = DEMO_PLUGINS.find((plugin) => plugin.id === selectedId) || DEMO_PLUGINS[0];
  const src = workspaceId ? `/api/public/plugins/${encodeURIComponent(workspaceId)}/${encodeURIComponent(selected.id)}` : "";

  return <div style={{ minHeight: "100vh", background: "#090b10", color: "#f6f7fb", fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" }}>
    <header style={{ height: 58, borderBottom: "1px solid #20242d", display: "flex", alignItems: "center", justifyContent: "space-between", padding: "0 18px", background: "#0d1016" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}><OperlyMark /><strong style={{ letterSpacing: ".1em", fontSize: 13 }}>OPERLY</strong><span style={{ color: "#636a78" }}>/</span><span style={{ color: "#aeb5c2", fontSize: 13 }}>Plugin Lab</span></div>
      <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 12, color: "#8f97a6" }}><span style={{ width: 7, height: 7, borderRadius: 999, background: "#62d394", boxShadow: "0 0 12px #62d39466" }} />Test Workspace · {workspaceId || "missing workspace"}</div>
    </header>

    <div style={{ display: "grid", gridTemplateColumns: "260px minmax(0, 1fr)", minHeight: "calc(100vh - 59px)" }}>
      <aside style={{ borderRight: "1px solid #20242d", background: "#0d1016", padding: "18px 12px" }}>
        <div style={{ padding: "4px 10px 14px" }}><div style={{ color: "#6f7787", fontSize: 11, letterSpacing: ".13em", fontWeight: 700 }}>INSTALLED APPS</div><p style={{ margin: "7px 0 0", color: "#8d95a4", fontSize: 12, lineHeight: 1.45 }}>These frames are served from the exact validated plugin artifacts installed in this test Workspace.</p></div>
        <nav style={{ display: "grid", gap: 4 }}>
          {DEMO_PLUGINS.map((plugin) => {
            const active = plugin.id === selected.id;
            return <button key={plugin.id} onClick={() => setSelectedId(plugin.id)} style={{ textAlign: "left", border: active ? "1px solid #414957" : "1px solid transparent", background: active ? "#171b23" : "transparent", color: active ? "#fff" : "#bdc3cf", padding: "10px 11px", borderRadius: 10, cursor: "pointer" }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8, alignItems: "center" }}><strong style={{ fontSize: 13 }}>{plugin.name}</strong><span style={{ color: "#70798a", fontSize: 10 }}>{plugin.category}</span></div>
              <div style={{ color: active ? "#9fa7b6" : "#737c8c", fontSize: 11, marginTop: 4 }}>{plugin.detail}</div>
            </button>;
          })}
        </nav>
      </aside>

      <main style={{ minWidth: 0, display: "flex", flexDirection: "column", background: "#0a0d12" }}>
        <div style={{ minHeight: 64, borderBottom: "1px solid #20242d", padding: "12px 18px", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16 }}>
          <div><div style={{ fontWeight: 700, fontSize: 15 }}>{selected.name}</div><div style={{ color: "#7f8796", fontSize: 12, marginTop: 3 }}>{selected.id} · Workspace plugin UI</div></div>
          {src && <a href={src} target="_blank" rel="noreferrer" style={{ color: "#d8dce5", border: "1px solid #303642", borderRadius: 8, padding: "7px 10px", fontSize: 12, textDecoration: "none", background: "#141820" }}>Open standalone ↗</a>}
        </div>
        {workspaceId ? <iframe key={src} title={`${selected.name} plugin interface`} src={src} style={{ width: "100%", flex: 1, minHeight: "calc(100vh - 123px)", border: 0, background: "#0d0a14" }} sandbox="allow-scripts allow-forms" />
          : <div style={{ padding: 40, color: "#98a0ae" }}>A Workspace ID is required in the Plugin Lab URL.</div>}
      </main>
    </div>
  </div>;
}

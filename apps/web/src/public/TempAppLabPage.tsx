import { useEffect, useMemo, useRef, useState } from "react";

import { OperlyMark } from "../ui/OperlyMark";

type TempApp = {
  plugin_id: string;
  name: string;
  category: string;
  description: string;
  hosted_path: string;
  capability_id?: string | null;
};

type BridgeMessage = {
  source?: string;
  requestId?: string;
  action?: string;
  payload?: Record<string, unknown>;
};

function workspaceIdFromPath(pathname: string): string {
  const prefix = "/temp-app-lab/";
  const raw = pathname.startsWith(prefix) ? pathname.slice(prefix.length).split("/", 1)[0] : "";
  try { return decodeURIComponent(raw); } catch { return raw; }
}

export function TempAppLabPage({ pathname }: { pathname: string }) {
  const workspaceId = useMemo(() => workspaceIdFromPath(pathname), [pathname]);
  const token = useMemo(() => new URLSearchParams(window.location.search).get("token") || "", []);
  const [apps, setApps] = useState<TempApp[]>([]);
  const [selectedId, setSelectedId] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const frameRef = useRef<HTMLIFrameElement | null>(null);
  const selected = apps.find((item) => item.plugin_id === selectedId) || apps[0];

  useEffect(() => {
    let cancelled = false;
    async function load() {
      if (!workspaceId || !token) {
        setError("This temporary app lab link is missing its Workspace access token.");
        setLoading(false);
        return;
      }
      try {
        const response = await fetch(`/api/public/plugin-demos/${encodeURIComponent(workspaceId)}/apps`, {
          headers: { "X-Operly-Demo-Token": token },
          credentials: "omit",
        });
        if (!response.ok) throw new Error(`Temporary app lab is unavailable (${response.status}).`);
        const payload = await response.json();
        const next = Array.isArray(payload.apps) ? payload.apps : [];
        if (!cancelled) {
          setApps(next);
          setSelectedId(next[0]?.plugin_id || "");
          setLoading(false);
        }
      } catch (err) {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "Could not open the temporary app lab.");
          setLoading(false);
        }
      }
    }
    load();
    return () => { cancelled = true; };
  }, [workspaceId, token]);

  useEffect(() => {
    async function onMessage(event: MessageEvent<BridgeMessage>) {
      const frame = frameRef.current;
      const message = event.data || {};
      if (!frame?.contentWindow || event.source !== frame.contentWindow) return;
      if (message.source !== "operly.temp-app" || !message.requestId || !selected) return;

      const reply = (ok: boolean, data?: unknown, bridgeError?: string) => {
        frame.contentWindow?.postMessage({
          source: "operly.temp-app.response",
          requestId: message.requestId,
          ok,
          data,
          error: bridgeError,
        }, "*");
      };

      const base = `/api/public/plugin-demos/${encodeURIComponent(workspaceId)}/${encodeURIComponent(selected.plugin_id)}`;
      const common = {
        "X-Operly-Demo-Token": token,
        "Content-Type": "application/json",
      };
      try {
        let response: Response;
        if (message.action === "state.get") {
          response = await fetch(`${base}/state`, { headers: { "X-Operly-Demo-Token": token }, credentials: "omit" });
        } else if (message.action === "state.put") {
          response = await fetch(`${base}/state`, {
            method: "PUT",
            headers: common,
            credentials: "omit",
            body: JSON.stringify({ state: message.payload?.state || {} }),
          });
        } else if (message.action === "state.reset") {
          response = await fetch(`${base}/reset`, {
            method: "POST",
            headers: common,
            credentials: "omit",
            body: "{}",
          });
        } else if (message.action === "capability.execute") {
          response = await fetch(`${base}/execute`, {
            method: "POST",
            headers: common,
            credentials: "omit",
            body: JSON.stringify({ action: message.payload?.action || "analyze" }),
          });
        } else {
          reply(false, undefined, "This plugin action is not exposed by the temporary Operly bridge.");
          return;
        }
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
          const detail = typeof payload.detail === "string" ? payload.detail : payload.detail?.message;
          throw new Error(detail || `Operly action failed (${response.status}).`);
        }
        reply(true, payload);
      } catch (err) {
        reply(false, undefined, err instanceof Error ? err.message : "Operly action failed.");
      }
    }
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [selected, workspaceId, token]);

  const src = selected?.hosted_path || "";

  if (loading) {
    return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#090b10", color: "#aab2c0", fontFamily: "Inter, system-ui, sans-serif" }}>
      Loading temporary Workspace apps…
    </div>;
  }

  if (error) {
    return <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", background: "#090b10", color: "#f3f4f6", fontFamily: "Inter, system-ui, sans-serif", padding: 24 }}>
      <div style={{ maxWidth: 520, border: "1px solid #303642", borderRadius: 16, padding: 24, background: "#11151c" }}>
        <strong>Temporary App Lab unavailable</strong>
        <p style={{ color: "#929aa8", lineHeight: 1.5 }}>{error}</p>
      </div>
    </div>;
  }

  return <div style={{ minHeight: "100vh", background: "#080a0f", color: "#f6f7fb", fontFamily: "Inter, ui-sans-serif, system-ui, sans-serif" }}>
    <header style={{ minHeight: 58, borderBottom: "1px solid #20242d", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 16, padding: "10px 16px", background: "#0d1016", flexWrap: "wrap" }}>
      <div style={{ display: "flex", alignItems: "center", gap: 9 }}>
        <OperlyMark />
        <strong style={{ letterSpacing: ".1em", fontSize: 12 }}>OPERLY</strong>
        <span style={{ color: "#565f6f" }}>/</span>
        <span style={{ color: "#aab1bf", fontSize: 12 }}>Temporary Functional App Lab</span>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", color: "#8992a1", fontSize: 11 }}>
        <span style={{ width: 7, height: 7, borderRadius: 999, background: "#f59e0b" }} />
        {apps.length} disposable apps · Workspace {workspaceId.slice(0, 8)}
      </div>
    </header>

    <div className="temp-app-lab-grid">
      <aside className="temp-app-lab-sidebar">
        <div style={{ padding: "5px 9px 12px" }}>
          <div style={{ color: "#70798a", fontSize: 10, letterSpacing: ".13em", fontWeight: 750 }}>TEMPORARY APPS</div>
          <p style={{ color: "#858e9d", fontSize: 11, lineHeight: 1.45, margin: "7px 0 0" }}>
            Real plugin artifacts with persisted demo state and isolated Sandbox analysis. This Workspace is designed to be deleted later.
          </p>
        </div>
        <nav style={{ display: "grid", gap: 4 }}>
          {apps.map((app) => {
            const active = app.plugin_id === selected?.plugin_id;
            return <button key={app.plugin_id} onClick={() => setSelectedId(app.plugin_id)} style={{
              border: active ? "1px solid #404756" : "1px solid transparent",
              background: active ? "#181d25" : "transparent",
              color: active ? "#fff" : "#b4bbc7",
              borderRadius: 9,
              padding: "9px 10px",
              textAlign: "left",
              cursor: "pointer",
            }}>
              <div style={{ display: "flex", justifyContent: "space-between", gap: 8 }}>
                <strong style={{ fontSize: 12 }}>{app.name}</strong>
                <span style={{ color: "#727b8b", fontSize: 9 }}>{app.category}</span>
              </div>
              <div style={{ color: "#737c8c", fontSize: 10, marginTop: 3, whiteSpace: "nowrap", overflow: "hidden", textOverflow: "ellipsis" }}>{app.description}</div>
            </button>;
          })}
        </nav>
      </aside>

      <main style={{ minWidth: 0, minHeight: "calc(100vh - 59px)", display: "flex", flexDirection: "column" }}>
        <div style={{ minHeight: 54, padding: "9px 14px", borderBottom: "1px solid #20242d", background: "#0c0f14", display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12 }}>
          <div>
            <strong style={{ fontSize: 13 }}>{selected?.name}</strong>
            <div style={{ color: "#737c8b", fontSize: 10, marginTop: 2 }}>{selected?.plugin_id} · persisted plugin state · governed Sandbox capability</div>
          </div>
          <span style={{ color: "#9aa3b2", border: "1px solid #2c323d", background: "#141820", borderRadius: 999, padding: "5px 8px", fontSize: 9 }}>TEMP</span>
        </div>
        {src ? <iframe
          ref={frameRef}
          key={src}
          title={`${selected?.name || "Temporary app"} plugin interface`}
          src={src}
          style={{ width: "100%", flex: 1, minHeight: "calc(100vh - 113px)", border: 0, background: "#090b10" }}
          sandbox="allow-scripts allow-forms allow-modals"
        /> : <div style={{ padding: 30, color: "#8d95a3" }}>No temporary apps are installed.</div>}
      </main>
    </div>

    <style>{`
      .temp-app-lab-grid { display:grid; grid-template-columns:240px minmax(0,1fr); min-height:calc(100vh - 59px); }
      .temp-app-lab-sidebar { border-right:1px solid #20242d; background:#0d1016; padding:14px 10px; }
      @media (max-width: 760px) {
        .temp-app-lab-grid { display:block; }
        .temp-app-lab-sidebar { border-right:0; border-bottom:1px solid #20242d; overflow-x:auto; padding:8px; }
        .temp-app-lab-sidebar > div { display:none; }
        .temp-app-lab-sidebar nav { display:flex !important; gap:6px !important; width:max-content; }
        .temp-app-lab-sidebar nav button { min-width:145px; max-width:170px; }
      }
    `}</style>
  </div>;
}

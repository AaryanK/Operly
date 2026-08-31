import { useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { OperlyMark } from "../ui/OperlyMark";
import { WorkspaceOSPanel } from "./WorkspaceOSPanel";

type Workspace = { id: string; name: string; role: string; current: boolean };

function go(path: string) {
  window.location.assign(path);
}

function routeWorkspaceId(pathname: string): string | null {
  if (!pathname.startsWith("/channels/")) return null;
  const raw = pathname.slice("/channels/".length).split("/", 1)[0];
  if (!raw || raw === "@me") return null;
  try { return decodeURIComponent(raw); } catch { return raw; }
}

function workspaceControlPath(workspaceId: string, section: string): string {
  return `/channels/${encodeURIComponent(workspaceId)}/${section}`;
}

export function WorkspaceSafeApp({ pathname }: { pathname: string }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedId = useMemo(() => routeWorkspaceId(pathname), [pathname]);
  const selected = useMemo(() => workspaces.find((workspace) => workspace.id === selectedId) || null, [selectedId, workspaces]);

  const load = useCallback(async () => {
    try {
      setWorkspaces(await api<Workspace[]>("/auth/workspaces"));
    } catch {
      go("/login");
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { void load(); }, [load]);

  const switchWorkspace = useCallback(async (workspaceId: string, destination?: string) => {
    setBusy(true); setError("");
    try {
      await api("/auth/switch-workspace", { method: "POST", body: JSON.stringify({ tenant_id: workspaceId }) });
      go(destination || `/channels/${encodeURIComponent(workspaceId)}/dashboard`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch workspace");
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!selected || selected.current || busy) return;
    void switchWorkspace(selected.id, pathname);
  }, [busy, pathname, selected, switchWorkspace]);

  const personal = async () => {
    setBusy(true); setError("");
    try {
      await api("/auth/personal-scope", { method: "POST" });
      go("/personal");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch to personal scope");
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    try { await api("/auth/logout", { method: "POST" }); go("/"); }
    catch { setBusy(false); }
  };

  if (loading) return <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening your spaces…</span></div>;

  const accountView = pathname === "/account" || pathname === "/channels" || pathname === "/app";
  const personalView = pathname === "/personal" || pathname === "/channels/@me";

  return <div className="workspace-lite-shell">
    <nav className="workspace-lite-rail" aria-label="Operly spaces">
      <button className={`workspace-lite-mark workspace-lite-personal ${personalView ? "active" : ""}`} onClick={() => void personal()} disabled={busy}><OperlyMark label="Personal" /></button>
      <span className="workspace-lite-divider" />
      <div className="workspace-lite-rail-list">{workspaces.map((workspace) => <button key={workspace.id} className={`workspace-lite-mark ${selectedId === workspace.id ? "active" : ""}`} title={workspace.name} disabled={busy} onClick={() => void switchWorkspace(workspace.id)}>{workspace.name.trim().slice(0, 2).toUpperCase()}</button>)}</div>
      <button className="workspace-lite-mark workspace-lite-account" onClick={() => go("/account")}>A</button>
    </nav>
    <div className="workspace-lite-content">
      <header className="workspace-lite-topbar">
        <a className="workspace-lite-brand" href="/account"><OperlyMark /><span>OPERLY</span></a>
        <div className="workspace-lite-topbar-actions">
          {selected && selected.current && <>
            <a href={workspaceControlPath(selected.id, "workflows")}>Workflows</a>
            <a href={workspaceControlPath(selected.id, "activity")}>Activity</a>
            <a href={workspaceControlPath(selected.id, "agent-computer")}>Computer</a>
            <a href={workspaceControlPath(selected.id, "connections")}>Integrations</a>
            <a href={workspaceControlPath(selected.id, "capabilities")}>All tools</a>
          </>}
          <a href="/account">Workspaces</a>
          <button className="workspace-lite-button" onClick={() => void load()}>Refresh</button>
          <button className="workspace-lite-button" onClick={() => void logout()}>Sign out</button>
        </div>
      </header>
      {error && <div className="workspace-lite-error">{error}</div>}
      {accountView && <main className="workspace-lite-main"><section className="workspace-lite-heading"><span className="workspace-lite-kicker">YOUR OPERLY</span><h1>Workspaces</h1><p>Select a workspace to continue.</p></section><div className="workspace-lite-grid">{workspaces.map((workspace) => <button className="workspace-lite-card" key={workspace.id} disabled={busy} onClick={() => void switchWorkspace(workspace.id)}><span className="workspace-lite-card-icon workspace-lite-initials">{workspace.name.trim().slice(0, 2).toUpperCase()}</span><span><strong>{workspace.name}</strong><small>{workspace.role}{workspace.current ? " · current session" : ""}</small></span></button>)}</div></main>}
      {personalView && <main className="workspace-lite-main workspace-lite-space-view"><section className="workspace-lite-space-hero"><span className="workspace-lite-kicker">PERSONAL</span><h1>Your private Operly</h1><p>Personal account scope. Workspace business data remains isolated.</p></section></main>}
      {!accountView && !personalView && selected && selected.current && <WorkspaceOSPanel workspaceId={selected.id} pathname={pathname} />}
      {!accountView && !personalView && selected && !selected.current && <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Entering {selected.name}…</span></div>}
      {!accountView && !personalView && selectedId && !selected && <main className="workspace-lite-main"><h1>Workspace unavailable</h1><p>This account is not a member of the requested workspace.</p></main>}
    </div>
  </div>;
}

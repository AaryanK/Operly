import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { navigate } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { OperlyMark } from "../ui/OperlyMark";
import { WorkspaceOSPanel } from "./WorkspaceOSPanel";

const PersonalHome = lazy(() => import("../account/PersonalHome").then((module) => ({ default: module.PersonalHome })));
const WorkspaceOperly = lazy(() => import("../workspace/WorkspaceOperly").then((module) => ({ default: module.WorkspaceOperly })));
const WorkflowPage = lazy(() => import("../workspace/WorkflowPage").then((module) => ({ default: module.WorkflowPage })));
const ActivityPage = lazy(() => import("../workspace/ActivityPage").then((module) => ({ default: module.ActivityPage })));
const AgentComputerPage = lazy(() => import("../workspace/AgentComputerPage").then((module) => ({ default: module.AgentComputerPage })));
const ConnectionsPage = lazy(() => import("../workspace/ConnectionsPage").then((module) => ({ default: module.ConnectionsPage })));
const CapabilitiesPage = lazy(() => import("../workspace/CapabilitiesPage").then((module) => ({ default: module.CapabilitiesPage })));
const AccessPage = lazy(() => import("../workspace/AccessPage").then((module) => ({ default: module.AccessPage })));

type Workspace = WorkspaceSummary & { current: boolean };
type AdvancedSection = "operly" | "workflows" | "activity" | "agent-computer" | "connections" | "capabilities" | "access";

const ADVANCED_WORKSPACE_SECTIONS = new Set<AdvancedSection>([
  "operly",
  "workflows",
  "activity",
  "agent-computer",
  "connections",
  "capabilities",
  "access",
]);

function go(path: string) {
  window.location.assign(path);
}

function routeWorkspaceId(pathname: string): string | null {
  if (!pathname.startsWith("/channels/")) return null;
  const raw = pathname.slice("/channels/".length).split("/", 1)[0];
  if (!raw || raw === "@me") return null;
  try { return decodeURIComponent(raw); } catch { return raw; }
}

function workspaceControlPath(workspaceId: string, section: AdvancedSection): string {
  return `/channels/${encodeURIComponent(workspaceId)}/${section}`;
}

function advancedWorkspaceSection(pathname: string, workspaceId: string): AdvancedSection | null {
  const prefix = `/channels/${encodeURIComponent(workspaceId)}/`;
  if (!pathname.startsWith(prefix)) return null;
  const requested = pathname.slice(prefix.length).split("/", 1)[0] as AdvancedSection;
  return ADVANCED_WORKSPACE_SECTIONS.has(requested) ? requested : null;
}

function AdvancedWorkspacePage({ workspace, section }: { workspace: Workspace; section: AdvancedSection }) {
  switch (section) {
    case "operly": return <WorkspaceOperly workspace={workspace} />;
    case "workflows": return <WorkflowPage workspace={workspace} />;
    case "activity": return <ActivityPage workspace={workspace} />;
    case "agent-computer": return <AgentComputerPage workspace={workspace} />;
    case "connections": return <ConnectionsPage workspace={workspace} />;
    case "capabilities": return <CapabilitiesPage workspace={workspace} />;
    case "access": return <AccessPage workspace={workspace} />;
  }
}

function WorkspaceControlLink({ workspaceId, section, active, children }: { workspaceId: string; section: AdvancedSection; active: boolean; children: string }) {
  const path = workspaceControlPath(workspaceId, section);
  return <a className={active ? "active" : undefined} href={path} onClick={(event) => { event.preventDefault(); navigate(path); }}>{children}</a>;
}

export function WorkspaceSafeApp({ pathname }: { pathname: string }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const selectedId = useMemo(() => routeWorkspaceId(pathname), [pathname]);
  const selected = useMemo(() => workspaces.find((workspace) => workspace.id === selectedId) || null, [selectedId, workspaces]);
  const advancedSection = useMemo(
    () => selected ? advancedWorkspaceSection(pathname, selected.id) : null,
    [pathname, selected],
  );

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
      await api("/auth/personal-scope", { method: "POST", body: "{}" });
      go("/personal");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch to personal scope");
      setBusy(false);
    }
  };

  const logout = async () => {
    if (busy) return;
    setBusy(true);
    setError("");
    try {
      await api("/auth/logout", { method: "POST", body: "{}" });
      go("/login");
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) {
        go("/login");
        return;
      }
      setError(caught instanceof Error ? `Could not sign out: ${caught.message}` : "Could not sign out. Please try again.");
      setBusy(false);
    }
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
            <WorkspaceControlLink workspaceId={selected.id} section="operly" active={advancedSection === "operly"}>Operly</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="workflows" active={advancedSection === "workflows"}>Workflows</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="activity" active={advancedSection === "activity"}>Activity</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="agent-computer" active={advancedSection === "agent-computer"}>Computer</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="connections" active={advancedSection === "connections"}>Integrations</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="capabilities" active={advancedSection === "capabilities"}>All tools</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="access" active={advancedSection === "access"}>AI & MCP</WorkspaceControlLink>
          </>}
          <a href="/account">Workspaces</a>
          <button className="workspace-lite-button" onClick={() => void load()}>Refresh</button>
          <button className="workspace-lite-button" onClick={() => void logout()} disabled={busy}>{busy ? "Working…" : "Sign out"}</button>
        </div>
      </header>
      {error && <div className="workspace-lite-error">{error}</div>}
      {accountView && <main className="workspace-lite-main"><section className="workspace-lite-heading"><span className="workspace-lite-kicker">YOUR OPERLY</span><h1>Workspaces</h1><p>Select a workspace to continue.</p></section><div className="workspace-lite-grid">{workspaces.map((workspace) => <button className="workspace-lite-card" key={workspace.id} disabled={busy} onClick={() => void switchWorkspace(workspace.id)}><span className="workspace-lite-card-icon workspace-lite-initials">{workspace.name.trim().slice(0, 2).toUpperCase()}</span><span><strong>{workspace.name}</strong><small>{workspace.role}{workspace.current ? " · current session" : ""}</small></span></button>)}</div></main>}
      {personalView && <Suspense fallback={<div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening Personal Operly…</span></div>}><PersonalHome profile={null} /></Suspense>}
      {!accountView && !personalView && selected && selected.current && (advancedSection
        ? <div className="workspace-lite-advanced"><Suspense fallback={<div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening workspace tool…</span></div>}><AdvancedWorkspacePage workspace={selected} section={advancedSection} /></Suspense></div>
        : <WorkspaceOSPanel workspaceId={selected.id} pathname={pathname} />)}
      {!accountView && !personalView && selected && !selected.current && <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Entering {selected.name}…</span></div>}
      {!accountView && !personalView && selectedId && !selected && <main className="workspace-lite-main"><h1>Workspace unavailable</h1><p>This account is not a member of the requested workspace.</p></main>}
    </div>
  </div>;
}

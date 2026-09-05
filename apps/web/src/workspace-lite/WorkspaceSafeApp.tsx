import { lazy, Suspense, useCallback, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { navigate } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { OperlyMark } from "../ui/OperlyMark";
import { WorkspaceOSPanel } from "./WorkspaceOSPanel";

const PersonalHome = lazy(() => import("../account/PersonalHome").then((module) => ({ default: module.PersonalHome })));
const WorkspaceOperly = lazy(() => import("../workspace/WorkspaceOperly").then((module) => ({ default: module.WorkspaceOperly })));
const WorkspaceAssistantPanel = lazy(() => import("../workspace/WorkspaceAssistantPanel").then((module) => ({ default: module.WorkspaceAssistantPanel })));
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

function WorkspaceControlLink({ workspaceId, section, active, prominent = false, children }: { workspaceId: string; section: AdvancedSection; active: boolean; prominent?: boolean; children: string }) {
  const path = workspaceControlPath(workspaceId, section);
  const className = [active ? "active" : "", prominent ? "workspace-lite-button workspace-lite-button-primary" : ""].filter(Boolean).join(" ") || undefined;
  return <a className={className} href={path} onClick={(event) => { event.preventDefault(); navigate(path); }}>{children}</a>;
}

export function WorkspaceSafeApp({ pathname }: { pathname: string }) {
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [logoutBusy, setLogoutBusy] = useState(false);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [error, setError] = useState("");
  const selectedId = useMemo(() => routeWorkspaceId(pathname), [pathname]);
  const selected = useMemo(() => workspaces.find((workspace) => workspace.id === selectedId) || null, [selectedId, workspaces]);
  const currentWorkspace = useMemo(() => workspaces.find((workspace) => workspace.current) || null, [workspaces]);
  const advancedSection = useMemo(
    () => selected ? advancedWorkspaceSection(pathname, selected.id) : null,
    [pathname, selected],
  );
  const accountView = pathname === "/account" || pathname === "/channels" || pathname === "/app";
  const personalView = pathname === "/personal" || pathname === "/channels/@me";
  const operlyWorkspace = selected || currentWorkspace;
  const operlyPath = operlyWorkspace ? workspaceControlPath(operlyWorkspace.id, "operly") : "/personal";

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

  useEffect(() => {
    if (!selected?.current || accountView || personalView || advancedSection === "operly") setAssistantOpen(false);
  }, [accountView, advancedSection, personalView, selected]);

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

  const openOperly = () => {
    if (selected?.current && !accountView && !personalView && advancedSection !== "operly") {
      setAssistantOpen((current) => !current);
      return;
    }
    navigate(operlyPath);
  };

  const logout = async () => {
    if (logoutBusy) return;
    setLogoutBusy(true);
    setError("");
    try {
      await api("/auth/logout", { method: "POST" });
      go("/login");
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 409)) {
        go("/login");
        return;
      }
      setError(caught instanceof Error ? `Could not sign out: ${caught.message}` : "Could not sign out. Please try again.");
      setLogoutBusy(false);
    }
  };

  if (loading) return <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening your spaces…</span></div>;

  const operlyView = advancedSection === "operly" || personalView || assistantOpen;
  const showAssistant = Boolean(assistantOpen && selected?.current && !accountView && !personalView && advancedSection !== "operly");

  return <div className="workspace-lite-shell">
    <nav className="workspace-lite-rail" aria-label="Operly spaces">
      <button className={`workspace-lite-mark ${operlyView ? "active" : ""}`} onClick={openOperly} disabled={busy} title={operlyWorkspace ? `Ask Operly in ${operlyWorkspace.name}` : "Open Personal Operly"} aria-label={operlyWorkspace ? `Ask Operly in ${operlyWorkspace.name}` : "Open Personal Operly"}><OperlyMark label="Operly AI" /></button>
      <button className={`workspace-lite-mark workspace-lite-personal ${personalView ? "active" : ""}`} onClick={() => void personal()} disabled={busy} title="Personal Operly" aria-label="Switch to Personal Operly">ME</button>
      <span className="workspace-lite-divider" />
      <div className="workspace-lite-rail-list">{workspaces.map((workspace) => <button key={workspace.id} className={`workspace-lite-mark ${selectedId === workspace.id ? "active" : ""}`} title={workspace.name} disabled={busy} onClick={() => void switchWorkspace(workspace.id)}>{workspace.name.trim().slice(0, 2).toUpperCase()}</button>)}</div>
      <button className="workspace-lite-mark workspace-lite-account" onClick={() => go("/account")} title="Account & workspaces" aria-label="Account and workspaces">A</button>
    </nav>
    <div className="workspace-lite-content">
      <header className="workspace-lite-topbar">
        <a className="workspace-lite-brand" href={operlyPath} onClick={(event) => { event.preventDefault(); openOperly(); }}><OperlyMark /><span>OPERLY</span></a>
        <div className="workspace-lite-topbar-actions">
          {selected && selected.current && <>
            <button className={`workspace-lite-button workspace-lite-button-primary workspace-lite-ask ${assistantOpen ? "active" : ""}`} type="button" onClick={openOperly} aria-pressed={assistantOpen}>{assistantOpen ? "Hide Operly" : "Ask Operly"}</button>
            <WorkspaceControlLink workspaceId={selected.id} section="workflows" active={advancedSection === "workflows"}>Workflows</WorkspaceControlLink>
            <WorkspaceControlLink workspaceId={selected.id} section="activity" active={advancedSection === "activity"}>Activity</WorkspaceControlLink>
            <details className="workspace-lite-menu">
              <summary>Tools</summary>
              <div className="workspace-lite-menu-panel">
                <WorkspaceControlLink workspaceId={selected.id} section="agent-computer" active={advancedSection === "agent-computer"}>Computer</WorkspaceControlLink>
                <WorkspaceControlLink workspaceId={selected.id} section="connections" active={advancedSection === "connections"}>Integrations</WorkspaceControlLink>
                <WorkspaceControlLink workspaceId={selected.id} section="capabilities" active={advancedSection === "capabilities"}>All tools</WorkspaceControlLink>
                <WorkspaceControlLink workspaceId={selected.id} section="access" active={advancedSection === "access"}>AI & MCP</WorkspaceControlLink>
                <a href={workspaceControlPath(selected.id, "operly")} onClick={(event) => { event.preventDefault(); navigate(workspaceControlPath(selected.id, "operly")); }}>Open Operly full page</a>
              </div>
            </details>
          </>}
          <details className="workspace-lite-menu workspace-lite-account-menu">
            <summary>Account</summary>
            <div className="workspace-lite-menu-panel">
              <a href="/account" onClick={(event) => { event.preventDefault(); navigate("/account"); }}>Workspaces</a>
              <button type="button" onClick={() => void load()}>Refresh workspace list</button>
              <button type="button" onClick={() => void logout()} disabled={logoutBusy}>{logoutBusy ? "Signing out…" : "Sign out"}</button>
            </div>
          </details>
        </div>
      </header>
      {error && <div className="workspace-lite-error">{error}</div>}
      <div className={`workspace-lite-stage ${showAssistant ? "assistant-open" : ""}`}>
        <div className="workspace-lite-primary">
          {accountView && <main className="workspace-lite-main"><section className="workspace-lite-heading"><span className="workspace-lite-kicker">YOUR OPERLY</span><h1>Workspaces</h1><p>Select a workspace to continue.</p></section><div className="workspace-lite-grid">{workspaces.map((workspace) => <button className="workspace-lite-card" key={workspace.id} disabled={busy} onClick={() => void switchWorkspace(workspace.id)}><span className="workspace-lite-card-icon workspace-lite-initials">{workspace.name.trim().slice(0, 2).toUpperCase()}</span><span><strong>{workspace.name}</strong><small>{workspace.role}{workspace.current ? " · current session" : ""}</small></span></button>)}</div></main>}
          {personalView && <Suspense fallback={<div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening Personal Operly…</span></div>}><PersonalHome profile={null} /></Suspense>}
          {!accountView && !personalView && selected && selected.current && (advancedSection
            ? <div className="workspace-lite-advanced"><Suspense fallback={<div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Opening workspace tool…</span></div>}><AdvancedWorkspacePage workspace={selected} section={advancedSection} /></Suspense></div>
            : <WorkspaceOSPanel workspaceId={selected.id} pathname={pathname} />)}
          {!accountView && !personalView && selected && !selected.current && <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Entering {selected.name}…</span></div>}
          {!accountView && !personalView && selectedId && !selected && <main className="workspace-lite-main"><h1>Workspace unavailable</h1><p>This account is not a member of the requested workspace.</p></main>}
        </div>
        {showAssistant && selected && <aside className="workspace-assistant-slot"><Suspense fallback={<div className="workspace-assistant-loading"><OperlyMark /><span>Opening Operly…</span></div>}><WorkspaceAssistantPanel workspace={selected} onClose={() => setAssistantOpen(false)} /></Suspense></aside>}
      </div>
    </div>
  </div>;
}

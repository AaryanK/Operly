import { lazy, ReactNode, Suspense, useEffect, useMemo, useRef, useState } from "react";

import { navigate, WorkspaceSection, workspacePath, workspaceSections } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { WorkspaceHome } from "./WorkspaceHome";

const AccessPage = lazy(() => import("./AccessPage").then((module) => ({ default: module.AccessPage })));
const AIDebugPage = lazy(() => import("./AIDebugPage").then((module) => ({ default: module.AIDebugPage })));
const CRMPage = lazy(() => import("./DataPages").then((module) => ({ default: module.CRMPage })));
const OperationsPage = lazy(() => import("./DataPages").then((module) => ({ default: module.OperationsPage })));
const ActivityPage = lazy(() => import("./ActivityPage").then((module) => ({ default: module.ActivityPage })));
const PresencePage = lazy(() => import("./DataPages").then((module) => ({ default: module.PresencePage })));
const ConnectionsPage = lazy(() => import("./DataPages").then((module) => ({ default: module.ConnectionsPage })));
const MembersPage = lazy(() => import("./MembersPage").then((module) => ({ default: module.MembersPage })));
const PluginsPage = lazy(() => import("./PluginsPage").then((module) => ({ default: module.PluginsPage })));
const SolutionsPage = lazy(() => import("./SolutionsPage").then((module) => ({ default: module.SolutionsPage })));
const WorkspaceOperly = lazy(() => import("./WorkspaceOperly").then((module) => ({ default: module.WorkspaceOperly })));
const WorkspaceSettings = lazy(() => import("./WorkspaceSettings").then((module) => ({ default: module.WorkspaceSettings })));

type Props = {
  workspace: WorkspaceSummary;
  section: WorkspaceSection;
  onScopeRefresh: () => Promise<unknown>;
};

type GroupKey = (typeof workspaceSections)[number]["group"];

const groupLabels: Record<GroupKey, string> = {
  workspace: "Workspace",
  business: "Business",
  digital: "Digital presence",
  extend: "Extend",
  debug: "Debug",
  admin: "Administration",
};

const navGlyphs: Record<WorkspaceSection, string> = {
  home: "⌂",
  operly: "✦",
  crm: "◎",
  operations: "▦",
  activity: "◫",
  presence: "◉",
  solutions: "◇",
  connections: "↗",
  plugins: "⬡",
  members: "♙",
  access: "⌁",
  "ai-debug": "⌘",
  settings: "⚙",
};

function DeferredPage({ children }: { children: ReactNode }) {
  return <Suspense fallback={<main className="workspace-page"><div className="loading-panel">Loading workspace section…</div></main>}>{children}</Suspense>;
}

function WorkspaceContent({ workspace, section, onScopeRefresh }: Props) {
  switch (section) {
    case "home": return <WorkspaceHome workspace={workspace} />;
    case "operly": return <DeferredPage><WorkspaceOperly workspace={workspace} /></DeferredPage>;
    case "crm": return <DeferredPage><CRMPage workspace={workspace} /></DeferredPage>;
    case "operations": return <DeferredPage><OperationsPage workspace={workspace} /></DeferredPage>;
    case "activity": return <DeferredPage><ActivityPage workspace={workspace} /></DeferredPage>;
    case "presence": return <DeferredPage><PresencePage workspace={workspace} /></DeferredPage>;
    case "solutions": return <DeferredPage><SolutionsPage workspace={workspace} /></DeferredPage>;
    case "connections": return <DeferredPage><ConnectionsPage workspace={workspace} /></DeferredPage>;
    case "plugins": return <DeferredPage><PluginsPage workspace={workspace} /></DeferredPage>;
    case "members": return <DeferredPage><MembersPage workspace={workspace} /></DeferredPage>;
    case "access": return <DeferredPage><AccessPage workspace={workspace} /></DeferredPage>;
    case "ai-debug": return <DeferredPage><AIDebugPage workspace={workspace} /></DeferredPage>;
    case "settings": return <DeferredPage><WorkspaceSettings workspace={workspace} onRefresh={onScopeRefresh} /></DeferredPage>;
  }
}

function readCollapsedGroups(workspaceId: string): Set<GroupKey> {
  try {
    const value = JSON.parse(window.localStorage.getItem(`operly.workspace-nav-groups:${workspaceId}`) || "[]");
    return new Set(Array.isArray(value) ? value.filter((item): item is GroupKey => item in groupLabels) : []);
  } catch {
    return new Set();
  }
}

export function WorkspaceShell({ workspace, section, onScopeRefresh }: Props) {
  const [query, setQuery] = useState("");
  const [collapsed, setCollapsed] = useState(() => {
    try { return window.localStorage.getItem("operly.workspace-nav-collapsed") === "true"; }
    catch { return false; }
  });
  const [collapsedGroups, setCollapsedGroups] = useState<Set<GroupKey>>(() => readCollapsedGroups(workspace.id));
  const [mobileNavigationOpen, setMobileNavigationOpen] = useState(() => {
    try { return window.matchMedia("(max-width: 680px)").matches && section === "home"; }
    catch { return false; }
  });
  const previousWorkspaceId = useRef(workspace.id);

  const visibleSections = useMemo(
    () => workspaceSections.filter((item) => item.id !== "ai-debug" || workspace.role === "owner"),
    [workspace.role],
  );
  const activeItem = visibleSections.find((item) => item.id === section) || visibleSections[0];
  const normalizedQuery = query.trim().toLowerCase();
  const grouped = visibleSections.reduce<Record<string, typeof workspaceSections>>((result, item) => {
    if (normalizedQuery && !`${item.label} ${groupLabels[item.group]}`.toLowerCase().includes(normalizedQuery)) return result;
    (result[item.group] ||= []).push(item);
    return result;
  }, {});

  useEffect(() => {
    if (previousWorkspaceId.current === workspace.id) return;
    previousWorkspaceId.current = workspace.id;
    setQuery("");
    setCollapsedGroups(readCollapsedGroups(workspace.id));
    setMobileNavigationOpen(true);
  }, [workspace.id]);

  useEffect(() => {
    if (!mobileNavigationOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape" && section !== "home") setMobileNavigationOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileNavigationOpen, section]);

  function toggleCollapsed() {
    setCollapsed((current) => {
      const next = !current;
      try { window.localStorage.setItem("operly.workspace-nav-collapsed", String(next)); } catch { /* device storage is optional */ }
      return next;
    });
  }

  function toggleGroup(group: GroupKey) {
    setCollapsedGroups((current) => {
      const next = new Set(current);
      if (next.has(group)) next.delete(group);
      else next.add(group);
      try { window.localStorage.setItem(`operly.workspace-nav-groups:${workspace.id}`, JSON.stringify([...next])); } catch { /* optional */ }
      return next;
    });
  }

  function goTo(nextSection: WorkspaceSection) {
    setMobileNavigationOpen(false);
    navigate(workspacePath(workspace.id, nextSection));
  }

  return (
    <div className={`workspace-shell ${collapsed ? "workspace-nav-collapsed" : ""} ${mobileNavigationOpen ? "mobile-nav-open" : "mobile-content-open"}`}>
      <aside id="workspace-navigation-panel" className="workspace-nav" aria-label={`${workspace.name} navigation`}>
        <div className="workspace-nav-head">
          <button className="workspace-identity workspace-identity-button" type="button" onClick={() => goTo("settings")} title="Open workspace settings">
            <span>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : workspace.name.slice(0, 2).toUpperCase()}</span>
            <div><strong>{workspace.name}</strong><small>{workspace.role}</small></div>
            <span className="workspace-identity-chevron" aria-hidden="true">›</span>
          </button>
          <button className="workspace-nav-toggle" type="button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand workspace navigation" : "Collapse workspace navigation"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>{collapsed ? "›" : "‹"}</button>
        </div>

        <label className="workspace-nav-search">
          <span aria-hidden="true">⌕</span>
          <input value={query} onChange={(event) => setQuery(event.target.value)} type="search" placeholder="Search workspace" aria-label={`Search ${workspace.name} sections`} />
        </label>

        <nav aria-label={`${workspace.name} sections`}>
          {Object.entries(grouped).map(([group, items]) => {
            const key = group as GroupKey;
            const groupCollapsed = !normalizedQuery && collapsedGroups.has(key);
            return (
              <section className={`nav-group ${groupCollapsed ? "nav-group-collapsed" : ""}`} key={group}>
                <button className="nav-group-heading" type="button" aria-expanded={!groupCollapsed} onClick={() => toggleGroup(key)}>
                  <span>{groupLabels[key]}</span><span aria-hidden="true">⌄</span>
                </button>
                <div className="nav-group-items">
                  {items.map((item) => (
                    <button key={item.id} className={section === item.id ? "active" : ""} title={collapsed ? item.label : undefined} onClick={() => goTo(item.id)}>
                      <span className="nav-item-glyph" aria-hidden="true">{navGlyphs[item.id]}</span>
                      <span className="nav-item-label">{item.label}</span>
                    </button>
                  ))}
                </div>
              </section>
            );
          })}
          {Object.keys(grouped).length === 0 && <p className="workspace-nav-empty">No workspace sections match “{query}”.</p>}
        </nav>
      </aside>

      <div className="workspace-content-frame">
        <header className="mobile-content-header workspace-mobile-content-header">
          <button type="button" onClick={() => setMobileNavigationOpen(true)} aria-label={`Back to ${workspace.name} navigation`}>←</button>
          <div><small>{workspace.name}</small><strong>{activeItem?.label || "Workspace"}</strong></div>
        </header>
        <WorkspaceContent workspace={workspace} section={section} onScopeRefresh={onScopeRefresh} />
      </div>
    </div>
  );
}

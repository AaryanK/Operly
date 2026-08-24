import { useEffect, useState } from "react";

import { navigate, WorkspaceSection, workspacePath, workspaceSections } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { AccessPage } from "./AccessPage";
import { AIDebugPage } from "./AIDebugPage";
import { ActivityPage, ConnectionsPage, CRMPage, OperationsPage, PresencePage } from "./DataPages";
import { MembersPage } from "./MembersPage";
import { PluginsPage } from "./PluginsPage";
import { SolutionsPage } from "./SolutionsPage";
import { WorkspaceHome } from "./WorkspaceHome";
import { WorkspaceOperly } from "./WorkspaceOperly";
import { WorkspaceSettings } from "./WorkspaceSettings";

type Props = {
  workspace: WorkspaceSummary;
  section: WorkspaceSection;
  onScopeRefresh: () => Promise<unknown>;
};

const groupLabels: Record<(typeof workspaceSections)[number]["group"], string> = {
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

const mobilePrimarySections: WorkspaceSection[] = ["home", "operly", "activity", "solutions"];

function WorkspaceContent({ workspace, section, onScopeRefresh }: Props) {
  switch (section) {
    case "home": return <WorkspaceHome workspace={workspace} />;
    case "operly": return <WorkspaceOperly workspace={workspace} />;
    case "crm": return <CRMPage workspace={workspace} />;
    case "operations": return <OperationsPage workspace={workspace} />;
    case "activity": return <ActivityPage workspace={workspace} />;
    case "presence": return <PresencePage workspace={workspace} />;
    case "solutions": return <SolutionsPage workspace={workspace} />;
    case "connections": return <ConnectionsPage workspace={workspace} />;
    case "plugins": return <PluginsPage workspace={workspace} />;
    case "members": return <MembersPage workspace={workspace} />;
    case "access": return <AccessPage workspace={workspace} />;
    case "ai-debug": return <AIDebugPage workspace={workspace} />;
    case "settings": return <WorkspaceSettings workspace={workspace} onRefresh={onScopeRefresh} />;
  }
}

export function WorkspaceShell({ workspace, section, onScopeRefresh }: Props) {
  const [mobileMoreOpen, setMobileMoreOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(() => {
    try { return window.localStorage.getItem("operly.workspace-nav-collapsed") === "true"; }
    catch { return false; }
  });
  const visibleSections = workspaceSections.filter((item) => item.id !== "ai-debug" || workspace.role === "owner");
  const grouped = visibleSections.reduce<Record<string, typeof workspaceSections>>((result, item) => {
    (result[item.group] ||= []).push(item);
    return result;
  }, {});
  const mobileSecondarySections = visibleSections.filter((item) => !mobilePrimarySections.includes(item.id));
  const secondaryActive = mobileSecondarySections.some((item) => item.id === section);

  useEffect(() => {
    setMobileMoreOpen(false);
  }, [workspace.id, section]);

  useEffect(() => {
    if (!mobileMoreOpen) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileMoreOpen(false);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [mobileMoreOpen]);

  function toggleCollapsed() {
    setCollapsed((current) => {
      const next = !current;
      try { window.localStorage.setItem("operly.workspace-nav-collapsed", String(next)); } catch { /* device storage is optional */ }
      return next;
    });
  }

  function goTo(nextSection: WorkspaceSection) {
    setMobileMoreOpen(false);
    navigate(workspacePath(workspace.id, nextSection));
  }

  return (
    <div className={`workspace-shell ${collapsed ? "workspace-nav-collapsed" : ""} ${mobileMoreOpen ? "workspace-more-open" : ""}`}>
      <aside className="workspace-nav">
        <div className="workspace-nav-head">
          <header className="workspace-identity" onClick={() => navigate(workspacePath(workspace.id, "settings"))} title="Open workspace settings">
            <span>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : workspace.name.slice(0, 2).toUpperCase()}</span>
            <div><strong>{workspace.name}</strong><small>{workspace.role}</small></div>
          </header>
          <button className="workspace-nav-toggle" type="button" onClick={toggleCollapsed} aria-label={collapsed ? "Expand workspace navigation" : "Collapse workspace navigation"} title={collapsed ? "Expand sidebar" : "Collapse sidebar"}>{collapsed ? "›" : "‹"}</button>
        </div>
        <nav aria-label={`${workspace.name} sections`}>
          {Object.entries(grouped).map(([group, items]) => (
            <section className="nav-group" key={group}>
              <small>{groupLabels[group as keyof typeof groupLabels]}</small>
              {items.map((item) => (
                <button key={item.id} className={section === item.id ? "active" : ""} title={collapsed ? item.label : undefined} onClick={() => navigate(workspacePath(workspace.id, item.id))}><span className="nav-item-glyph" aria-hidden="true">{navGlyphs[item.id]}</span><span className="nav-item-label">{item.label}</span></button>
              ))}
            </section>
          ))}
        </nav>
      </aside>

      <WorkspaceContent workspace={workspace} section={section} onScopeRefresh={onScopeRefresh} />

      <button className="workspace-more-backdrop" type="button" aria-label="Close workspace navigation" onClick={() => setMobileMoreOpen(false)} />
      <aside className="workspace-more-sheet" aria-label="More workspace sections">
        <header><div><small>{workspace.name}</small><strong>More</strong></div><button type="button" aria-label="Close more navigation" onClick={() => setMobileMoreOpen(false)}>×</button></header>
        <div className="workspace-more-list">
          {mobileSecondarySections.map((item) => <button key={item.id} className={section === item.id ? "active" : ""} onClick={() => goTo(item.id)}><span aria-hidden="true">{navGlyphs[item.id]}</span><span><strong>{item.label}</strong><small>{groupLabels[item.group]}</small></span></button>)}
        </div>
      </aside>

      <nav className="workspace-mobile-nav" aria-label={`${workspace.name} primary sections`}>
        {mobilePrimarySections.map((id) => {
          const item = visibleSections.find((candidate) => candidate.id === id);
          if (!item) return null;
          return <button key={id} className={section === id ? "active" : ""} onClick={() => goTo(id)}><span aria-hidden="true">{navGlyphs[id]}</span><small>{item.label}</small></button>;
        })}
        <button className={secondaryActive || mobileMoreOpen ? "active" : ""} aria-expanded={mobileMoreOpen} onClick={() => setMobileMoreOpen((current) => !current)}><span aria-hidden="true">•••</span><small>More</small></button>
      </nav>
    </div>
  );
}

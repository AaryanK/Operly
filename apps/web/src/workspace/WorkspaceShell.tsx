import { useState } from "react";

import { navigate, WorkspaceSection, workspacePath, workspaceSections } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { AccessPage } from "./AccessPage";
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
  settings: "⚙",
};

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
    case "settings": return <WorkspaceSettings workspace={workspace} onRefresh={onScopeRefresh} />;
  }
}

export function WorkspaceShell({ workspace, section, onScopeRefresh }: Props) {
  const [collapsed, setCollapsed] = useState(() => {
    try { return window.localStorage.getItem("operly.workspace-nav-collapsed") === "true"; }
    catch { return false; }
  });
  const grouped = workspaceSections.reduce<Record<string, typeof workspaceSections>>((result, item) => {
    (result[item.group] ||= []).push(item);
    return result;
  }, {});

  function toggleCollapsed() {
    setCollapsed((current) => {
      const next = !current;
      try { window.localStorage.setItem("operly.workspace-nav-collapsed", String(next)); } catch { /* device storage is optional */ }
      return next;
    });
  }

  return (
    <div className={`workspace-shell ${collapsed ? "workspace-nav-collapsed" : ""}`}>
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
    </div>
  );
}

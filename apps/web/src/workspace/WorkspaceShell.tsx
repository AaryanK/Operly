import { navigate, WorkspaceSection, workspacePath, workspaceSections } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { AccessPage } from "./AccessPage";
import { ActivityPage, ConnectionsPage, CRMPage, OperationsPage, PresencePage } from "./DataPages";
import { MembersPage } from "./MembersPage";
import { PluginsPage } from "./PluginsPage";
import { SolutionsPage } from "./SolutionsPage";
import { WorkspaceHome } from "./WorkspaceHome";
import { WorkspaceOperly } from "./WorkspaceOperly";

type Props = {
  workspace: WorkspaceSummary;
  section: WorkspaceSection;
};

const groupLabels: Record<(typeof workspaceSections)[number]["group"], string> = {
  workspace: "Workspace",
  business: "Business",
  digital: "Digital presence",
  extend: "Extend",
  admin: "Administration",
};

function WorkspaceContent({ workspace, section }: Props) {
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
  }
}

export function WorkspaceShell({ workspace, section }: Props) {
  const grouped = workspaceSections.reduce<Record<string, typeof workspaceSections>>((result, item) => {
    (result[item.group] ||= []).push(item);
    return result;
  }, {});

  return (
    <div className="workspace-shell">
      <aside className="workspace-nav">
        <header className="workspace-identity">
          <span>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : workspace.name.slice(0, 2).toUpperCase()}</span>
          <div><strong>{workspace.name}</strong><small>{workspace.role}</small></div>
        </header>
        <nav aria-label={`${workspace.name} sections`}>
          {Object.entries(grouped).map(([group, items]) => (
            <section className="nav-group" key={group}>
              <small>{groupLabels[group as keyof typeof groupLabels]}</small>
              {items.map((item) => (
                <button
                  key={item.id}
                  className={section === item.id ? "active" : ""}
                  onClick={() => navigate(workspacePath(workspace.id, item.id))}
                >
                  {item.label}
                </button>
              ))}
            </section>
          ))}
        </nav>
      </aside>
      <WorkspaceContent workspace={workspace} section={section} />
    </div>
  );
}

import { navigate, WorkspaceSection, workspacePath, workspaceSections } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { WorkspaceHome } from "./WorkspaceHome";

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

function PendingMigration({ section }: { section: WorkspaceSection }) {
  const item = workspaceSections.find((candidate) => candidate.id === section);
  return (
    <main className="workspace-page">
      <header className="surface-header">
        <div>
          <span className="eyebrow">Canonical frontend</span>
          <h1>{item?.label || section}</h1>
          <p>This route now has one React owner. Its legacy data surface will be ported here before the old static renderer is deleted.</p>
        </div>
      </header>
      <section className="migration-card">
        <strong>Migration boundary</strong>
        <p>No hidden click, bridge script, DOM repair observer, or alternate renderer is invoked from this route.</p>
      </section>
    </main>
  );
}

function WorkspaceContent({ workspace, section }: Props) {
  if (section === "home") return <WorkspaceHome workspace={workspace} />;
  return <PendingMigration section={section} />;
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

import { navigate, WorkspaceSection, workspacePath } from "../app/routes";
import { WorkspaceSummary } from "../app/types";

type Props = {
  workspace: WorkspaceSummary;
};

const destinations: Array<{ section: WorkspaceSection; title: string; description: string }> = [
  { section: "operly", title: "Ask Operly", description: "Work with the workspace AI using this workspace's permissions and connectors." },
  { section: "activity", title: "Review activity", description: "See actions, approvals, failures, and other work that needs attention." },
  { section: "solutions", title: "Open Solutions", description: "Build and operate software for this workspace without a second command composer on Home." },
];

export function WorkspaceHome({ workspace }: Props) {
  return (
    <main className="workspace-page">
      <header className="surface-header workspace-hero">
        <div>
          <span className="eyebrow">Workspace</span>
          <h1>{workspace.name}</h1>
          <p>Home is the workspace overview. AI conversations belong in Operly; software creation belongs in Solutions.</p>
        </div>
      </header>

      <section className="home-grid" aria-label="Workspace destinations">
        {destinations.map((item) => (
          <button key={item.section} className="destination-card" onClick={() => navigate(workspacePath(workspace.id, item.section))}>
            <span>→</span>
            <strong>{item.title}</strong>
            <p>{item.description}</p>
          </button>
        ))}
      </section>
    </main>
  );
}

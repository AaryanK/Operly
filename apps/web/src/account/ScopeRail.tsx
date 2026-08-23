import { navigate, personalPath, workspacePath } from "../app/routes";
import { PersonalProfile, WorkspaceSummary } from "../app/types";

type Props = {
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
  activeWorkspaceId?: string | null;
  personal: boolean;
};

function initials(value: string): string {
  return value
    .trim()
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part[0])
    .join("")
    .toUpperCase() || "O";
}

export function ScopeRail({ profile, workspaces, activeWorkspaceId, personal }: Props) {
  return (
    <nav className="scope-rail" aria-label="Operly spaces">
      <button
        className={`scope-mark ${personal ? "active" : ""}`}
        aria-label="Personal Operly"
        title="Personal Operly"
        onClick={() => navigate(personalPath())}
      >
        ✦
      </button>
      <span className="scope-divider" aria-hidden="true" />
      <div className="scope-list">
        {workspaces.map((workspace) => (
          <button
            key={workspace.id}
            className={`scope-mark ${activeWorkspaceId === workspace.id ? "active" : ""}`}
            aria-label={workspace.name}
            title={workspace.name}
            onClick={() => navigate(workspacePath(workspace.id))}
          >
            {workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : initials(workspace.name)}
          </button>
        ))}
      </div>
      <div className="scope-account" title={profile?.email || "Account"}>
        {initials(profile?.display_name || profile?.email || "Me")}
      </div>
    </nav>
  );
}

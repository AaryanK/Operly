import { PersonalProfile, WorkspaceSummary } from "../app/types";

type Props = {
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
  activeWorkspaceId?: string | null;
  personal: boolean;
  transitioning?: boolean;
  onPersonal: () => void;
  onWorkspace: (workspaceId: string) => void;
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

export function ScopeRail({
  profile,
  workspaces,
  activeWorkspaceId,
  personal,
  transitioning = false,
  onPersonal,
  onWorkspace,
}: Props) {
  return (
    <nav className="scope-rail" aria-label="Operly spaces" aria-busy={transitioning}>
      <button
        className={`scope-mark ${personal ? "active" : ""}`}
        aria-label="Personal Operly"
        title="Personal Operly"
        disabled={transitioning}
        onClick={onPersonal}
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
            disabled={transitioning}
            onClick={() => onWorkspace(workspace.id)}
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

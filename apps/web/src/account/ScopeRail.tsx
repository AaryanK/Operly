import { PersonalProfile, WorkspaceSummary } from "../app/types";
import { OperlyMark } from "../ui/OperlyMark";

type Props = {
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
  activeWorkspaceId?: string | null;
  personal: boolean;
  transitioning?: boolean;
  onPersonal: () => void;
  onWorkspace: (workspaceId: string) => void;
  onAccount: () => void;
  onCreateWorkspace: () => void;
  onSignOut: () => void;
};

function initials(value: string): string {
  return value.trim().split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "O";
}

export function ScopeRail({ profile, workspaces, activeWorkspaceId, personal, transitioning = false, onPersonal, onWorkspace, onAccount, onCreateWorkspace, onSignOut }: Props) {
  return <nav className="scope-rail" aria-label="Operly spaces" aria-busy={transitioning}>
    <button className={`scope-mark scope-brand ${personal ? "active" : ""}`} aria-label="Personal Operly" title="Personal Operly" disabled={transitioning} onClick={onPersonal}><OperlyMark label="Personal Operly" /></button>
    <span className="scope-divider" aria-hidden="true" />
    <div className="scope-list">
      {workspaces.map((workspace) => <button key={workspace.id} className={`scope-mark ${activeWorkspaceId === workspace.id ? "active" : ""}`} aria-label={workspace.name} title={workspace.name} disabled={transitioning} onClick={() => onWorkspace(workspace.id)}>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : initials(workspace.name)}</button>)}
      <button className="scope-mark scope-add" aria-label="Create workspace" title="Create workspace" disabled={transitioning} onClick={onCreateWorkspace}>+</button>
    </div>
    <button className="scope-account" title={profile?.email || "Account settings"} aria-label="Account settings" onClick={onAccount}>{initials(profile?.display_name || profile?.email || "Me")}</button>
    <button className="scope-mark scope-signout" type="button" title="Sign out" aria-label="Sign out" disabled={transitioning} onClick={onSignOut}>↪</button>
  </nav>;
}

import { useEffect, useState } from "react";

import { AccountSettings } from "../account/AccountSettings";
import { PersonalHome } from "../account/PersonalHome";
import { ScopeRail } from "../account/ScopeRail";
import { WorkspaceShell } from "../workspace/WorkspaceShell";
import { navigate, personalPath, workspacePath } from "./routes";
import { useRoute } from "./useRoute";
import { useScope } from "./useScope";

export function App() {
  const route = useRoute();
  const [accountSettingsOpen, setAccountSettingsOpen] = useState(false);
  const { loading, transitioning, error, profile, workspaces, refresh, activatePersonal, activateWorkspace } = useScope();
  const workspace = route.kind === "workspace" ? workspaces.find((item) => item.id === route.workspaceId || item.slug === route.workspaceId) : undefined;

  useEffect(() => { if (route.kind === "unknown" && !loading) navigate(personalPath(), { replace: true }); }, [route, loading]);
  useEffect(() => {
    if (!profile || loading || transitioning) return;
    if (route.kind === "personal" && profile.current_workspace_id) { activatePersonal(personalPath()).catch(() => undefined); return; }
    if (route.kind === "workspace" && workspace && profile.current_workspace_id !== workspace.id) activateWorkspace(workspace.id, workspacePath(workspace.id, route.section)).catch(() => undefined);
  }, [activatePersonal, activateWorkspace, loading, profile, route, transitioning, workspace]);

  if (loading && !profile) return <div className="boot-screen"><span>✦</span><p>Opening Operly…</p></div>;
  if (error && !profile) return <div className="boot-screen error-state"><span>!</span><h1>Operly could not open this account.</h1><p>{error}</p><a href="/login">Sign in</a></div>;
  if (route.kind === "unknown") return null;

  const rail = (personal: boolean, activeWorkspaceId?: string | null) => <ScopeRail profile={profile} workspaces={workspaces} personal={personal} activeWorkspaceId={activeWorkspaceId} transitioning={transitioning} onPersonal={() => activatePersonal().catch(() => undefined)} onWorkspace={(workspaceId) => activateWorkspace(workspaceId).catch(() => undefined)} onAccount={() => setAccountSettingsOpen(true)} onCreateWorkspace={() => setAccountSettingsOpen(true)} />;
  const settings = accountSettingsOpen ? <AccountSettings profile={profile} workspaces={workspaces} onClose={() => setAccountSettingsOpen(false)} onRefresh={refresh} onWorkspace={(workspaceId) => activateWorkspace(workspaceId)} /> : null;

  if (route.kind === "personal") {
    if (profile?.current_workspace_id || transitioning) return <div className="boot-screen"><span>✦</span><p>Switching to your private Operly…</p></div>;
    return <div className="authenticated-shell">{rail(true)}<PersonalHome profile={profile} />{settings}</div>;
  }

  if (!workspace) return <div className="authenticated-shell">{rail(false)}<main className="workspace-page missing-workspace"><h1>Workspace unavailable</h1><p>This account is not authorized for the requested workspace.</p><button onClick={() => activatePersonal().catch(() => undefined)}>Return to Personal Operly</button></main>{settings}</div>;
  if (profile?.current_workspace_id !== workspace.id || transitioning) return <div className="boot-screen"><span>✦</span><p>Entering {workspace.name}…</p></div>;

  return <div className="authenticated-shell">{rail(false, workspace.id)}<WorkspaceShell workspace={workspace} section={route.section} onScopeRefresh={refresh} />{settings}</div>;
}

import { useEffect } from "react";

import { ScopeRail } from "../account/ScopeRail";
import { PersonalHome } from "../account/PersonalHome";
import { WorkspaceShell } from "../workspace/WorkspaceShell";
import { navigate, personalPath } from "./routes";
import { useRoute } from "./useRoute";
import { useScope } from "./useScope";

export function App() {
  const route = useRoute();
  const { loading, error, profile, workspaces } = useScope();

  useEffect(() => {
    if (route.kind === "unknown" && !loading) navigate(personalPath(), { replace: true });
  }, [route, loading]);

  if (loading) {
    return <div className="boot-screen"><span>✦</span><p>Opening Operly…</p></div>;
  }

  if (error) {
    return (
      <div className="boot-screen error-state">
        <span>!</span>
        <h1>Operly could not open this account.</h1>
        <p>{error}</p>
        <a href="/login">Sign in</a>
      </div>
    );
  }

  if (route.kind === "unknown") return null;

  if (route.kind === "personal") {
    return (
      <div className="authenticated-shell">
        <ScopeRail profile={profile} workspaces={workspaces} personal />
        <PersonalHome />
      </div>
    );
  }

  const workspace = workspaces.find((item) => item.id === route.workspaceId || item.slug === route.workspaceId);
  if (!workspace) {
    return (
      <div className="authenticated-shell">
        <ScopeRail profile={profile} workspaces={workspaces} personal={false} />
        <main className="workspace-page missing-workspace">
          <h1>Workspace unavailable</h1>
          <p>This account is not authorized for the requested workspace.</p>
          <button onClick={() => navigate(personalPath())}>Return to Personal Operly</button>
        </main>
      </div>
    );
  }

  return (
    <div className="authenticated-shell">
      <ScopeRail profile={profile} workspaces={workspaces} personal={false} activeWorkspaceId={workspace.id} />
      <WorkspaceShell workspace={workspace} section={route.section} />
    </div>
  );
}

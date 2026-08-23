import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { navigate, personalPath, workspacePath } from "./routes";
import { PersonalProfile, WorkspaceSummary } from "./types";

type ScopeState = {
  loading: boolean;
  transitioning: boolean;
  error: string | null;
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
};

const initialState: ScopeState = {
  loading: true,
  transitioning: false,
  error: null,
  profile: null,
  workspaces: [],
};

export function useScope() {
  const [state, setState] = useState<ScopeState>(initialState);

  const refresh = useCallback(async () => {
    setState((current) => ({ ...current, loading: true, error: null }));
    try {
      const [profile, workspaces] = await Promise.all([
        api<PersonalProfile>("/personal-agent/me"),
        api<WorkspaceSummary[]>("/personal-agent/workspaces"),
      ]);
      setState((current) => ({ ...current, loading: false, error: null, profile, workspaces }));
      return { profile, workspaces };
    } catch (error) {
      const message = error instanceof Error ? error.message : "Could not load account scope";
      setState((current) => ({ ...current, loading: false, error: message }));
      throw error;
    }
  }, []);

  const activatePersonal = useCallback(async (destination = personalPath()) => {
    setState((current) => ({ ...current, transitioning: true, error: null }));
    try {
      if (state.profile?.current_workspace_id) {
        await api("/auth/personal-scope", { method: "POST", body: "{}" });
      }
      await refresh();
      navigate(destination, { replace: window.location.pathname === destination });
    } finally {
      setState((current) => ({ ...current, transitioning: false }));
    }
  }, [refresh, state.profile?.current_workspace_id]);

  const activateWorkspace = useCallback(async (workspaceId: string, destination = workspacePath(workspaceId)) => {
    setState((current) => ({ ...current, transitioning: true, error: null }));
    try {
      if (state.profile?.current_workspace_id !== workspaceId) {
        await api("/auth/switch-workspace", {
          method: "POST",
          body: JSON.stringify({ tenant_id: workspaceId }),
        });
      }
      await refresh();
      navigate(destination, { replace: window.location.pathname === destination });
    } finally {
      setState((current) => ({ ...current, transitioning: false }));
    }
  }, [refresh, state.profile?.current_workspace_id]);

  useEffect(() => {
    refresh().catch(() => undefined);
  }, [refresh]);

  return { ...state, refresh, activatePersonal, activateWorkspace };
}

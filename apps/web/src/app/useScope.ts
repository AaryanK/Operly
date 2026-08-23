import { useCallback, useEffect, useState } from "react";

import { api } from "../api";
import { PersonalProfile, WorkspaceSummary } from "./types";

type ScopeState = {
  loading: boolean;
  error: string | null;
  profile: PersonalProfile | null;
  workspaces: WorkspaceSummary[];
};

const initialState: ScopeState = {
  loading: true,
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
      setState({ loading: false, error: null, profile, workspaces });
    } catch (error) {
      setState((current) => ({
        ...current,
        loading: false,
        error: error instanceof Error ? error.message : "Could not load account scope",
      }));
    }
  }, []);

  useEffect(() => {
    refresh();
  }, [refresh]);

  return { ...state, refresh };
}

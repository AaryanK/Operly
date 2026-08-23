export type WorkspaceSummary = {
  id: string;
  name: string;
  role: string;
  current?: boolean;
  slug?: string | null;
  logo_url?: string | null;
  timezone?: string | null;
};

export type PersonalProfile = {
  id?: string;
  email: string;
  display_name: string;
  current_workspace_id?: string | null;
};

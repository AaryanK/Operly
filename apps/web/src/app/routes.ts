export type WorkspaceSection =
  | "home"
  | "operly"
  | "crm"
  | "operations"
  | "activity"
  | "presence"
  | "solutions"
  | "connections"
  | "plugins"
  | "members"
  | "access";

export type OperlyRoute =
  | { kind: "personal" }
  | { kind: "workspace"; workspaceId: string; section: WorkspaceSection }
  | { kind: "unknown"; pathname: string };

export const workspaceSections: Array<{
  id: WorkspaceSection;
  label: string;
  group: "workspace" | "business" | "digital" | "extend" | "admin";
}> = [
  { id: "home", label: "Home", group: "workspace" },
  { id: "operly", label: "Operly", group: "workspace" },
  { id: "crm", label: "CRM", group: "business" },
  { id: "operations", label: "Operations", group: "business" },
  { id: "activity", label: "Activity", group: "business" },
  { id: "presence", label: "Presence", group: "digital" },
  { id: "solutions", label: "Solutions", group: "digital" },
  { id: "connections", label: "Connections", group: "extend" },
  { id: "plugins", label: "Plugins", group: "extend" },
  { id: "members", label: "Members & roles", group: "admin" },
  { id: "access", label: "AI & MCP access", group: "admin" },
];

const workspaceSectionIds = new Set<WorkspaceSection>(workspaceSections.map((item) => item.id));

export function parseRoute(pathname: string): OperlyRoute {
  const normalized = pathname.replace(/\/+$/, "") || "/";
  if (normalized === "/channels/@me" || normalized.startsWith("/channels/@me/")) {
    return { kind: "personal" };
  }

  const match = normalized.match(/^\/channels\/([^/]+)(?:\/([^/]+))?$/);
  if (match && match[1] !== "@me") {
    const requested = (match[2] || "home") as WorkspaceSection;
    return {
      kind: "workspace",
      workspaceId: decodeURIComponent(match[1]),
      section: workspaceSectionIds.has(requested) ? requested : "home",
    };
  }

  return { kind: "unknown", pathname: normalized };
}

export function personalPath(): string {
  return "/channels/@me";
}

export function workspacePath(workspaceId: string, section: WorkspaceSection = "home"): string {
  const base = `/channels/${encodeURIComponent(workspaceId)}`;
  return section === "home" ? base : `${base}/${section}`;
}

export function navigate(path: string, options: { replace?: boolean } = {}): void {
  if (window.location.pathname === path) return;
  if (options.replace) window.history.replaceState({}, "", path);
  else window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

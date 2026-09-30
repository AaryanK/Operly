export function workspacePath(workspaceId: string, section = "home"): string {
  const base = `/channels/${encodeURIComponent(workspaceId)}`;
  return section === "home" ? base : `${base}/${encodeURIComponent(section)}`;
}

export function navigate(path: string, options: { replace?: boolean } = {}): void {
  if (window.location.pathname === path) return;
  if (options.replace) window.history.replaceState({}, "", path);
  else window.history.pushState({}, "", path);
  window.dispatchEvent(new PopStateEvent("popstate"));
}

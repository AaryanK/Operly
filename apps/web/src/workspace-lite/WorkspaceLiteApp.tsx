import { FormEvent, useCallback, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { OperlyMark } from "../ui/OperlyMark";
import { WorkspaceHumanPanel } from "./WorkspaceHumanPanel";

type Workspace = {
  id: string;
  name: string;
  role: string;
  current: boolean;
};

type CreateWorkspaceResult = {
  ok: boolean;
  workspace: {
    id: string;
    name: string;
    slug?: string;
    role: string;
  };
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

function go(path: string) {
  window.location.assign(path);
}

function routeWorkspaceId(pathname: string): string | null {
  if (!pathname.startsWith("/channels/")) return null;
  const raw = pathname.slice("/channels/".length).split("/", 1)[0];
  if (!raw || raw === "@me") return null;
  try {
    return decodeURIComponent(raw);
  } catch {
    return raw;
  }
}

function WorkspaceRail({
  pathname,
  workspaces,
  busy,
  onPersonal,
  onWorkspace,
  onCreate,
}: {
  pathname: string;
  workspaces: Workspace[];
  busy: boolean;
  onPersonal: () => void;
  onWorkspace: (workspaceId: string) => void;
  onCreate: () => void;
}) {
  const activeWorkspaceId = routeWorkspaceId(pathname);
  const personal = pathname === "/personal" || pathname === "/channels/@me";

  return (
    <nav className="workspace-lite-rail" aria-label="Operly spaces" aria-busy={busy}>
      <button
        className={`workspace-lite-mark workspace-lite-personal ${personal ? "active" : ""}`}
        type="button"
        title="Personal"
        aria-label="Personal"
        disabled={busy}
        onClick={onPersonal}
      >
        <OperlyMark label="Personal" />
      </button>
      <span className="workspace-lite-divider" aria-hidden="true" />
      <div className="workspace-lite-rail-list">
        {workspaces.map((workspace) => (
          <button
            key={workspace.id}
            className={`workspace-lite-mark ${activeWorkspaceId === workspace.id ? "active" : ""}`}
            type="button"
            title={workspace.name}
            aria-label={workspace.name}
            disabled={busy}
            onClick={() => onWorkspace(workspace.id)}
          >
            {initials(workspace.name)}
          </button>
        ))}
        <button
          className="workspace-lite-mark workspace-lite-add"
          type="button"
          title="Create workspace"
          aria-label="Create workspace"
          disabled={busy}
          onClick={onCreate}
        >
          +
        </button>
      </div>
      <button
        className={`workspace-lite-mark workspace-lite-account ${pathname === "/account" ? "active" : ""}`}
        type="button"
        title="Account and workspaces"
        aria-label="Account and workspaces"
        onClick={() => go("/account")}
      >
        A
      </button>
    </nav>
  );
}

function CreateWorkspace({
  busy,
  onCancel,
  onCreate,
}: {
  busy: boolean;
  onCancel: () => void;
  onCreate: (name: string) => Promise<void>;
}) {
  const submit = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    const name = String(new FormData(event.currentTarget).get("name") || "").trim();
    if (name) void onCreate(name);
  };

  return (
    <div className="workspace-lite-modal-backdrop" role="presentation" onMouseDown={onCancel}>
      <form className="workspace-lite-modal" onSubmit={submit} onMouseDown={(event) => event.stopPropagation()}>
        <span className="workspace-lite-kicker">NEW WORKSPACE</span>
        <h2>Create a workspace</h2>
        <p>Give it a name. Once inside, Operly can shape the workspace around what this organization actually does.</p>
        <label>
          <span>Workspace name</span>
          <input name="name" autoFocus maxLength={200} required />
        </label>
        <div className="workspace-lite-modal-actions">
          <button className="workspace-lite-button" type="button" disabled={busy} onClick={onCancel}>Cancel</button>
          <button className="workspace-lite-button workspace-lite-button-primary" disabled={busy}>{busy ? "Creating…" : "Create workspace"}</button>
        </div>
      </form>
    </div>
  );
}

export function WorkspaceLiteApp({ pathname }: { pathname: string }) {
  const [loading, setLoading] = useState(true);
  const [workspaces, setWorkspaces] = useState<Workspace[]>([]);
  const [error, setError] = useState("");
  const [busy, setBusy] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);

  const selectedWorkspaceId = useMemo(() => routeWorkspaceId(pathname), [pathname]);
  const selectedWorkspace = useMemo(
    () => workspaces.find((workspace) => workspace.id === selectedWorkspaceId) || null,
    [selectedWorkspaceId, workspaces],
  );

  const refreshWorkspaces = useCallback(async () => {
    const result = await api<Workspace[]>("/auth/workspaces");
    setWorkspaces(result);
    return result;
  }, []);

  useEffect(() => {
    let cancelled = false;
    api<Workspace[]>("/auth/workspaces")
      .then((result) => {
        if (!cancelled) setWorkspaces(result);
      })
      .catch(() => {
        if (!cancelled) go("/login");
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  useEffect(() => {
    if (pathname === "/account" || pathname === "/channels") document.title = "Workspaces · OPERLY";
    else if (pathname === "/personal" || pathname === "/channels/@me") document.title = "Personal · OPERLY";
    else if (selectedWorkspace) document.title = `${selectedWorkspace.name} · OPERLY`;
  }, [pathname, selectedWorkspace]);

  const switchWorkspace = useCallback(async (workspaceId: string, destination?: string) => {
    setBusy(true);
    setError("");
    try {
      await api("/auth/switch-workspace", {
        method: "POST",
        body: JSON.stringify({ tenant_id: workspaceId }),
      });
      go(destination || `/channels/${encodeURIComponent(workspaceId)}/home`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch workspace");
      setBusy(false);
    }
  }, []);

  useEffect(() => {
    if (!selectedWorkspace || selectedWorkspace.current || busy) return;
    void switchWorkspace(selectedWorkspace.id, pathname);
  }, [busy, pathname, selectedWorkspace, switchWorkspace]);

  const switchPersonal = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/auth/personal-scope", { method: "POST" });
      go("/personal");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not switch to personal scope");
      setBusy(false);
    }
  };

  const createWorkspace = async (name: string) => {
    setBusy(true);
    setError("");
    try {
      const result = await api<CreateWorkspaceResult>("/auth/workspaces", {
        method: "POST",
        body: JSON.stringify({ name }),
      });
      go(`/channels/${encodeURIComponent(result.workspace.id)}/home`);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not create workspace");
      setBusy(false);
    }
  };

  const logout = async () => {
    setBusy(true);
    setError("");
    try {
      await api("/auth/logout", { method: "POST" });
      go("/");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not sign out");
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="workspace-lite-boot">
        <OperlyMark />
        <strong>OPERLY</strong>
        <span>Opening your spaces…</span>
      </div>
    );
  }

  const accountView = pathname === "/account" || pathname === "/channels" || pathname === "/app";
  const personalView = pathname === "/personal" || pathname === "/channels/@me";

  return (
    <div className="workspace-lite-shell">
      <WorkspaceRail
        pathname={pathname}
        workspaces={workspaces}
        busy={busy}
        onPersonal={switchPersonal}
        onWorkspace={(workspaceId) => void switchWorkspace(workspaceId)}
        onCreate={() => setCreateOpen(true)}
      />

      <div className="workspace-lite-content">
        <header className="workspace-lite-topbar">
          <a className="workspace-lite-brand" href="/account"><OperlyMark /><span>OPERLY</span></a>
          <div className="workspace-lite-topbar-actions">
            <a href="/account">Workspaces</a>
            <button className="workspace-lite-button" type="button" disabled={busy} onClick={() => void refreshWorkspaces()}>Refresh</button>
            <button className="workspace-lite-button" type="button" disabled={busy} onClick={logout}>Sign out</button>
          </div>
        </header>

        {error && <div className="workspace-lite-error">{error}</div>}

        {accountView && (
          <main className="workspace-lite-main">
            <section className="workspace-lite-heading">
              <span className="workspace-lite-kicker">YOUR OPERLY</span>
              <h1>Workspaces</h1>
              <p>Each workspace is its own permission boundary and operating environment.</p>
            </section>

            <div className="workspace-lite-grid">
              <button className="workspace-lite-card workspace-lite-card-personal" type="button" disabled={busy} onClick={switchPersonal}>
                <span className="workspace-lite-card-icon"><OperlyMark /></span>
                <span><strong>Personal</strong><small>Private account scope</small></span>
              </button>
              {workspaces.map((workspace) => (
                <button className="workspace-lite-card" key={workspace.id} type="button" disabled={busy} onClick={() => void switchWorkspace(workspace.id)}>
                  <span className="workspace-lite-card-icon workspace-lite-initials">{initials(workspace.name)}</span>
                  <span className="workspace-lite-card-copy">
                    <strong>{workspace.name}</strong>
                    <small>{workspace.role}{workspace.current ? " · current session" : ""}</small>
                  </span>
                </button>
              ))}
              <button className="workspace-lite-card workspace-lite-create-card" type="button" disabled={busy} onClick={() => setCreateOpen(true)}>
                <span className="workspace-lite-card-icon">+</span>
                <span><strong>Create workspace</strong><small>Start another operating environment</small></span>
              </button>
            </div>
          </main>
        )}

        {personalView && (
          <main className="workspace-lite-main workspace-lite-space-view">
            <section className="workspace-lite-space-hero">
              <div className="workspace-lite-space-logo"><OperlyMark /></div>
              <span className="workspace-lite-kicker">PERSONAL</span>
              <h1>Your private Operly</h1>
              <p>This is your personal account scope. Workspace business data is not visible here unless you explicitly enter that workspace.</p>
            </section>
            <section className="workspace-lite-detail-grid">
              <article><span>Scope</span><strong>Personal</strong></article>
              <article><span>Visibility</span><strong>Private to your account</strong></article>
              <article><span>AI runtime</span><strong>Not enabled</strong></article>
            </section>
          </main>
        )}

        {!accountView && !personalView && selectedWorkspace && selectedWorkspace.current && (
          <WorkspaceHumanPanel workspaceId={selectedWorkspace.id} pathname={pathname} />
        )}

        {!accountView && !personalView && selectedWorkspace && !selectedWorkspace.current && (
          <div className="workspace-lite-boot"><OperlyMark /><strong>OPERLY</strong><span>Entering {selectedWorkspace.name}…</span></div>
        )}

        {!accountView && !personalView && selectedWorkspaceId && !selectedWorkspace && (
          <main className="workspace-lite-main workspace-lite-space-view">
            <section className="workspace-lite-space-hero">
              <span className="workspace-lite-kicker">WORKSPACE</span>
              <h1>Workspace unavailable</h1>
              <p>This account is not a member of the requested workspace.</p>
              <a className="workspace-lite-button workspace-lite-button-primary" href="/account">Return to workspaces</a>
            </section>
          </main>
        )}

        {(accountView || personalView || !selectedWorkspace) && <footer className="workspace-lite-footer"><span>© 2026 OPERLY</span><nav><a href="/privacy">Privacy</a><a href="/terms">Terms</a></nav></footer>}
      </div>

      {createOpen && <CreateWorkspace busy={busy} onCancel={() => setCreateOpen(false)} onCreate={createWorkspace} />}
    </div>
  );
}
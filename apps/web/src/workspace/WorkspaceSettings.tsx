import { ChangeEvent, FormEvent, useRef, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Props = { workspace: WorkspaceSummary; onRefresh: () => Promise<unknown> };

export function WorkspaceSettings({ workspace, onRefresh }: Props) {
  const [name, setName] = useState(workspace.name);
  const [timezone, setTimezone] = useState(workspace.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);

  async function save(event: FormEvent) {
    event.preventDefault(); setBusy(true); setError(null); setMessage(null);
    try {
      await api(`/personal-agent/workspaces/${encodeURIComponent(workspace.id)}`, { method: "PATCH", body: JSON.stringify({ name: name.trim(), timezone: timezone.trim() }) });
      await onRefresh(); setMessage("Workspace settings saved.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace settings could not be saved"); }
    finally { setBusy(false); }
  }

  async function uploadIcon(event: ChangeEvent<HTMLInputElement>) {
    const file = event.target.files?.[0]; event.target.value = "";
    if (!file) return;
    setBusy(true); setError(null); setMessage(null);
    try {
      await api(`/personal-agent/workspaces/${encodeURIComponent(workspace.id)}/icon`, { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
      await onRefresh(); setMessage("Workspace icon updated.");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace icon could not be updated"); }
    finally { setBusy(false); }
  }

  async function removeIcon() {
    setBusy(true); setError(null); setMessage(null);
    try { await api(`/personal-agent/workspaces/${encodeURIComponent(workspace.id)}/icon`, { method: "DELETE" }); await onRefresh(); setMessage("Workspace icon removed."); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace icon could not be removed"); }
    finally { setBusy(false); }
  }

  return <main className="workspace-page">
    <header className="surface-header page-header"><div><span className="eyebrow">Administration</span><h1>Workspace settings</h1><p>Workspace identity belongs to this shared boundary. Personal account settings and connectors remain outside it.</p></div></header>
    <section className="settings-grid">
      <article className="data-card settings-card"><div className="card-heading"><div><span className="eyebrow">Identity</span><h2>Workspace profile</h2></div></div><div className="workspace-settings-identity"><span>{workspace.logo_url ? <img src={workspace.logo_url} alt="" /> : workspace.name.slice(0,2).toUpperCase()}</span><div><strong>{workspace.name}</strong><small>{workspace.role}</small></div></div><div className="page-actions"><input ref={picker} type="file" hidden accept="image/png,image/jpeg,image/webp" onChange={uploadIcon} /><button className="secondary-button" disabled={busy} onClick={() => picker.current?.click()}>Upload icon</button>{workspace.logo_url && <button className="danger-button" disabled={busy} onClick={removeIcon}>Remove icon</button>}</div></article>
      <form className="data-card settings-card form-stack" onSubmit={save}><div className="card-heading"><div><span className="eyebrow">General</span><h2>Name & timezone</h2></div></div><label>Workspace name<input value={name} onChange={(event) => setName(event.target.value)} maxLength={200} required /></label><label>Timezone<input value={timezone} onChange={(event) => setTimezone(event.target.value)} maxLength={100} required /><small>Used when Operly interprets dates, schedules, and operational times in this workspace.</small></label><button className="primary-button" disabled={busy || !name.trim() || !timezone.trim()}>{busy ? "Saving…" : "Save settings"}</button>{message && <div className="success-banner">{message}</div>}{error && <div className="inline-error">{error}</div>}</form>
      <article className="data-card settings-card full-settings"><div className="card-heading"><div><span className="eyebrow">Boundary</span><h2>What belongs here</h2></div></div><div className="settings-boundary-grid"><div><strong>Workspace-owned</strong><p>Members, roles, workspace connectors, business data, activity, Solutions, AI/MCP grants.</p></div><div><strong>Personal-owned</strong><p>Your private Operly transcript, personal connectors, account profile, and personal cross-workspace context.</p></div></div></article>
    </section>
  </main>;
}

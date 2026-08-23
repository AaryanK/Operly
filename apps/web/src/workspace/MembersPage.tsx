import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Member = { user_id: string; display_name: string; email: string; role: string };
type Role = { key: string; name: string; customized: boolean; permissions: string[] };

const builtinRoles = new Set(["owner", "manager", "agent", "employee"]);
const permissionLabels: Record<string, string> = {
  "company:read": "View company profile",
  "research:read": "View company research",
  "analytics:read": "View analytics",
  "crm:read": "View customers and pipeline",
  "crm:write": "Manage customers and pipeline",
  "website:read": "View website and presence",
  "website:write": "Change website and presence",
  "messaging:read": "Read workspace messages",
  "messaging:draft": "Draft outbound messages",
  "messaging:curate": "Curate outbound messages",
  "messaging:write": "Manage workspace messaging",
  "messaging:send": "Send outbound messages",
  "gmail:read": "Read connected Gmail",
  "gmail:write": "Manage connected Gmail",
  "gmail:draft": "Create Gmail drafts",
  "calendar:read": "View connected calendars",
  "calendar:write": "Manage connected calendars",
  "solution:read": "View Solutions",
  "solution:generate": "Generate Solutions",
  "solution:write": "Change Solutions",
  "tasks:read": "View tasks",
  "tasks:write": "Manage tasks",
  "memory:read": "Read workspace memory",
  "memory:write": "Manage workspace memory",
  "messages:read": "View channel history",
  "actions:read": "View actions and approvals",
  "actions:approve": "Approve consequential actions",
  "model:invoke": "Use workspace AI models",
  "catalog:write": "Manage catalog",
  "inventory:write": "Manage inventory",
  "orders:write": "Manage orders",
  "quotes:write": "Manage quotes",
  "operations:read": "View operations",
  "operations:write": "Manage operations",
  "reminders:write": "Manage reminders",
  "discord:read": "Read connected Discord",
  "discord:write": "Act through connected Discord",
  "context:human:read": "Read authorized human context",
  "context:human:write": "Write authorized human context",
  "context:tenant:read": "Read workspace context",
  "context:tenant:write": "Write workspace context",
  "context:conversation:read": "Read conversation context",
  "context:conversation:write": "Write conversation context",
  "workspace:read": "Enter and view workspace",
  "workspace:settings:manage": "Manage workspace settings",
  "workspace:members:manage": "Manage members",
  "workspace:roles:manage": "Manage roles and permissions",
  "workspace:channels:manage": "Manage channels",
  "workspace:clients:manage": "Manage external clients",
  "workspace:tools:expose": "Expose tools through MCP",
};
const permissionLabel = (permission: string) => permissionLabels[permission] || permission.replaceAll(":", " · ").replaceAll("_", " ");
const initials = (value: string) => value.split(/\s+/).filter(Boolean).slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "?";

function PermissionEditor({ role, available, onSave, onClose }: { role: Role; available: string[]; onSave: (permissions: string[]) => Promise<void>; onClose: () => void }) {
  const [selected, setSelected] = useState(new Set(role.permissions));
  const [busy, setBusy] = useState(false);
  const grouped = useMemo(() => available.reduce<Record<string, string[]>>((result, permission) => {
    const group = permission.split(":", 1)[0] || "other";
    (result[group] ||= []).push(permission);
    return result;
  }, {}), [available]);
  return <div className="permission-editor"><div className="permission-editor-head"><div><span className="eyebrow">Custom role</span><h3>{role.name}</h3><p>Choose the capabilities this role receives. Nothing outside this list is granted.</p></div><button onClick={onClose} aria-label="Close">×</button></div><div className="permission-groups">{Object.entries(grouped).map(([group, permissions]) => <section key={group}><strong>{group.replaceAll("_", " ")}</strong>{permissions.map((permission) => <label key={permission}><input type="checkbox" checked={selected.has(permission)} onChange={(event) => { const next = new Set(selected); if (event.target.checked) next.add(permission); else next.delete(permission); setSelected(next); }} /><span><b>{permissionLabel(permission)}</b><small>{permission}</small></span></label>)}</section>)}</div><div className="permission-editor-actions"><button className="secondary-button" onClick={onClose}>Cancel</button><button className="primary-button" disabled={busy} onClick={async () => { setBusy(true); try { await onSave([...selected]); onClose(); } finally { setBusy(false); } }}>{busy ? "Saving…" : "Save capabilities"}</button></div></div>;
}

export function MembersPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [members, setMembers] = useState<Member[]>([]);
  const [roles, setRoles] = useState<Role[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [addMember, setAddMember] = useState(false);
  const [addRole, setAddRole] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);

  async function reload() {
    setLoading(true); setError(null);
    try {
      const [memberRows, roleRows] = await Promise.all([api<Member[]>("/workspace/members"), api<Role[]>("/workspace/roles")]);
      setMembers(memberRows); setRoles(roleRows);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace administration is unavailable"); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  const availablePermissions = useMemo(() => [...new Set(roles.flatMap((role) => role.permissions))].sort(), [roles]);

  async function createMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api("/workspace/members", { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) }); setAddMember(false); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Member could not be added"); }
  }
  async function createRole(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    try { await api("/workspace/roles", { method: "POST", body: JSON.stringify({ name: form.get("name"), key: form.get("key") || null, permissions: [] }) }); setAddRole(false); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Role could not be created"); }
  }
  async function changeRole(memberId: string, role: string) {
    try { await api(`/workspace/members/${encodeURIComponent(memberId)}/role`, { method: "PATCH", body: JSON.stringify({ role }) }); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Role could not be changed"); }
  }
  async function savePermissions(roleKey: string, permissions: string[]) {
    try { await api(`/workspace/roles/${encodeURIComponent(roleKey)}/permissions`, { method: "PUT", body: JSON.stringify({ permissions }) }); await reload(); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Role permissions could not be saved"); throw caught; }
  }

  return <main className="workspace-page"><header className="surface-header page-header"><div><span className="eyebrow">Administration</span><h1>Members & roles</h1><p>Assign people to understandable workspace roles. Technical permission IDs are available only as advanced details.</p></div><div className="page-actions"><button className="secondary-button" onClick={() => setAddRole((value) => !value)}>+ Role</button><button className="primary-button" onClick={() => setAddMember((value) => !value)}>+ Member</button></div></header>
    {error && <div className="inline-error page-error">{error}</div>}{loading && <div className="loading-panel">Loading members and roles…</div>}
    {addMember && <form className="inline-form" onSubmit={createMember}><label>Email<input name="email" type="email" required /></label><label>Role<select name="role">{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></label><button className="primary-button">Add member</button><small>The person must already have an Operly account.</small></form>}
    {addRole && <form className="inline-form" onSubmit={createRole}><label>Role name<input name="name" required maxLength={120} /></label><label>Key (optional)<input name="key" maxLength={30} placeholder="support_lead" /></label><button className="primary-button">Create role</button><small>Custom roles start with zero capabilities so authority is never granted accidentally.</small></form>}
    {!loading && <section className="content-grid two-column"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">People</span><h2>Members</h2></div><span>{members.length}</span></div><div className="row-list">{members.map((member) => <div className="data-row member-row" key={member.user_id}><span className="mini-avatar">{initials(member.display_name || member.email)}</span><div><strong>{member.display_name || member.email}</strong><small>{member.email}</small></div><select value={member.role} onChange={(event) => changeRole(member.user_id, event.target.value)}>{roles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></div>)}{members.length === 0 && <div className="empty-panel">No members yet.</div>}</div></article><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Authority</span><h2>Roles</h2></div><span>{roles.length}</span></div><div className="role-grid">{roles.map((role) => <article className="role-card" key={role.key}><div><strong>{role.name}</strong><span className="role-key">{role.key}</span></div><div className="capability-list">{role.permissions.slice(0, 8).map((permission) => <span key={permission}>{permissionLabel(permission)}</span>)}{role.permissions.length === 0 && <small>No capabilities granted.</small>}</div><div className="role-actions">{builtinRoles.has(role.key) ? <small>Built-in baseline role</small> : <button className="secondary-button" onClick={() => setEditingRole(role)}>Edit capabilities</button>}</div>{role.permissions.length > 0 && <details><summary>Advanced · {role.permissions.length} raw permission{role.permissions.length === 1 ? "" : "s"}</summary><code>{role.permissions.join("\n")}</code></details>}</article>)}{roles.length === 0 && <div className="empty-panel">No roles available.</div>}</div></article></section>}
    {editingRole && <div className="editor-overlay"><button className="editor-backdrop" aria-label="Close permission editor" onClick={() => setEditingRole(null)}></button><PermissionEditor role={editingRole} available={availablePermissions} onSave={(permissions) => savePermissions(editingRole.key, permissions)} onClose={() => setEditingRole(null)} /></div>}
  </main>;
}

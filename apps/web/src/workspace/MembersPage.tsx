import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Member = { user_id: string; display_name: string; email: string; role: string };
type Role = { key: string; name: string; customized: boolean; permissions: string[] };
type WorkspaceInvitation = {
  id: string;
  target_email: string | null;
  role: string;
  status: string;
  source: string;
  expires_at: string;
  accepted_at: string | null;
  accepted_by_user_id: string | null;
  created_at: string;
};
type MemberCreateResult =
  | { membership_created: true; user_id: string; display_name: string; email: string; role: string }
  | {
      membership_created: false;
      invitation: {
        id: string;
        email: string;
        role: string;
        expires_at: string;
        invite_url: string;
        token: string;
      };
    };
type InviteResult = {
  id: string;
  workspace_id: string;
  target_email: string | null;
  role: string;
  expires_at: string;
  invite_url: string;
  token: string;
};

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
  const [invitations, setInvitations] = useState<WorkspaceInvitation[]>([]);
  const [canManageInvitations, setCanManageInvitations] = useState(workspace.role === "owner");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [inviteUrl, setInviteUrl] = useState<string | null>(null);
  const [addMember, setAddMember] = useState(false);
  const [addRole, setAddRole] = useState(false);
  const [editingRole, setEditingRole] = useState<Role | null>(null);

  async function reload() {
    setLoading(true); setError(null);
    try {
      const [memberRows, roleRows] = await Promise.all([api<Member[]>("/workspace/members"), api<Role[]>("/workspace/roles")]);
      setMembers(memberRows); setRoles(roleRows);
      try {
        const invitationRows = await api<WorkspaceInvitation[]>("/workspace/invitations");
        setInvitations(invitationRows);
        setCanManageInvitations(true);
      } catch {
        setInvitations([]);
        setCanManageInvitations(false);
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workspace administration is unavailable"); }
    finally { setLoading(false); }
  }
  useEffect(() => { setInviteUrl(null); setNotice(null); setCanManageInvitations(workspace.role === "owner"); reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  const availablePermissions = useMemo(() => [...new Set(roles.flatMap((role) => role.permissions))].sort(), [roles]);
  const pendingInvitations = useMemo(() => invitations.filter((row) => row.status === "pending"), [invitations]);
  const currentRolePermissions = useMemo(() => new Set(roles.find((role) => role.key === workspace.role)?.permissions || []), [roles, workspace.role]);
  const canManageRoles = workspace.role === "owner" || currentRolePermissions.has("workspace:roles:manage");
  const assignableRoles = useMemo(() => workspace.role === "owner" ? roles : roles.filter((role) => role.key !== "owner"), [roles, workspace.role]);

  async function copyInvite(url: string) {
    try {
      await navigator.clipboard.writeText(url);
      setNotice("Invitation link copied.");
    } catch {
      setNotice("Copy the invitation link shown below.");
    }
  }

  async function refreshAndCopyInvitation(invitation: WorkspaceInvitation) {
    setError(null); setNotice(null); setInviteUrl(null);
    try {
      // Invitation secrets are hash-only. Recovering a pending link rotates it rather
      // than storing a plaintext bearer token: the previous link is invalidated first,
      // then an equivalent fresh one is created and copied.
      await api(`/workspace/invitations/${encodeURIComponent(invitation.id)}`, { method: "DELETE" });
      const replacement = await api<InviteResult>("/workspace/invitations", {
        method: "POST",
        body: JSON.stringify({ role: invitation.role, target_email: invitation.target_email, ttl_days: 7 }),
      });
      setInviteUrl(replacement.invite_url);
      await copyInvite(replacement.invite_url);
      setNotice(`A fresh link for ${invitation.target_email || "this invitation"} was copied. The previous link no longer works.`);
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Invitation link could not be refreshed");
      await reload();
    }
  }

  async function createMember(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setError(null); setNotice(null); setInviteUrl(null);
    try {
      const result = await api<MemberCreateResult>("/workspace/members", { method: "POST", body: JSON.stringify({ email: form.get("email"), role: form.get("role") }) });
      if (result.membership_created) {
        setNotice(`${result.display_name || result.email} was added to ${workspace.name}.`);
      } else {
        setInviteUrl(result.invitation.invite_url);
        setNotice(`Invitation created for ${result.invitation.email}. They will join ${workspace.name} as ${result.invitation.role} after signing in or creating an Operly account.`);
      }
      setAddMember(false); await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Member could not be added"); }
  }
  async function createOpenInvite(event: FormEvent<HTMLFormElement>) {
    event.preventDefault(); const form = new FormData(event.currentTarget);
    setError(null); setNotice(null); setInviteUrl(null);
    try {
      const result = await api<InviteResult>("/workspace/invitations", { method: "POST", body: JSON.stringify({ role: form.get("role"), target_email: null, ttl_days: 7 }) });
      setInviteUrl(result.invite_url);
      setNotice(`One-time ${result.role} invitation created. Whoever claims this link first will join ${workspace.name}.`);
      await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Invitation could not be created"); }
  }
  async function revokeInvitation(invitationId: string) {
    setError(null); setNotice(null);
    try {
      await api(`/workspace/invitations/${encodeURIComponent(invitationId)}`, { method: "DELETE" });
      setNotice("Invitation revoked."); await reload();
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Invitation could not be revoked"); }
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

  return <main className="workspace-page"><header className="surface-header page-header"><div><span className="eyebrow">Administration</span><h1>Members & roles</h1><p>Invite anyone into this workspace, even if they do not have an Operly account yet. Roles become effective only after the person joins.</p></div><div className="page-actions">{canManageRoles && <button className="secondary-button" onClick={() => setAddRole((value) => !value)}>+ Role</button>}{canManageInvitations && <button className="primary-button" onClick={() => setAddMember((value) => !value)}>+ Member</button>}</div></header>
    {error && <div className="inline-error page-error">{error}</div>}{notice && <div className="inline-success page-error">{notice}</div>}{inviteUrl && <div className="data-card"><div className="card-heading"><div><span className="eyebrow">Invitation ready</span><h2>Share this one-time link</h2></div><button className="primary-button" onClick={() => copyInvite(inviteUrl)}>Copy link</button></div><code>{inviteUrl}</code><p>The link opens Operly sign-in/signup, preserves the invitation through authentication, then adds the person directly to this workspace.</p></div>}{loading && <div className="loading-panel">Loading members and roles…</div>}
    {canManageInvitations && addMember && <form className="inline-form" onSubmit={createMember}><label>Email<input name="email" type="email" required /></label><label>Role<select name="role">{assignableRoles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></label><button className="primary-button">Add or invite</button><small>If the email already belongs to an Operly user they are added now. Otherwise Operly creates a secure one-time invitation link.</small></form>}
    {!loading && canManageInvitations && pendingInvitations.length > 0 && <section className="data-card"><div className="card-heading"><div><span className="eyebrow">Onboarding</span><h2>Pending invitations</h2></div><span>{pendingInvitations.length}</span></div><div className="row-list">{pendingInvitations.map((invitation) => <div className="data-row" key={invitation.id}><div><strong>{invitation.target_email || "One-time share link"}</strong><small>{roles.find((role) => role.key === invitation.role)?.name || invitation.role} · expires {new Date(invitation.expires_at).toLocaleString()}</small></div><div className="role-actions"><button className="secondary-button" onClick={() => refreshAndCopyInvitation(invitation)}>Copy link</button><button className="secondary-button" onClick={() => revokeInvitation(invitation.id)}>Revoke</button></div></div>)}</div><form className="inline-form" onSubmit={createOpenInvite}><label>Share-link role<select name="role">{assignableRoles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></label><button className="secondary-button">Create one-time share link</button><small>Use this when you want to invite someone from an external workspace without requiring their email first.</small></form></section>}
    {!loading && canManageInvitations && pendingInvitations.length === 0 && <form className="inline-form" onSubmit={createOpenInvite}><label>Share-link role<select name="role">{assignableRoles.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></label><button className="secondary-button">Create one-time share link</button><small>Useful for Discord, Slack, or another external workspace when you want the participant to claim membership themselves.</small></form>}
    {canManageRoles && addRole && <form className="inline-form" onSubmit={createRole}><label>Role name<input name="name" required maxLength={120} /></label><label>Key (optional)<input name="key" maxLength={30} placeholder="support_lead" /></label><button className="primary-button">Create role</button><small>Custom roles start with zero capabilities so authority is never granted accidentally.</small></form>}
    {!loading && <section className="content-grid two-column"><article className="data-card"><div className="card-heading"><div><span className="eyebrow">People</span><h2>Members</h2></div><span>{members.length}</span></div><div className="row-list">{members.map((member) => { const protectedOwner = member.role === "owner" && workspace.role !== "owner"; const roleOptions = protectedOwner ? roles.filter((role) => role.key === "owner") : assignableRoles; return <div className="data-row member-row" key={member.user_id}><span className="mini-avatar">{initials(member.display_name || member.email)}</span><div><strong>{member.display_name || member.email}</strong><small>{member.email}</small></div><select value={member.role} disabled={!canManageInvitations || protectedOwner} onChange={(event) => changeRole(member.user_id, event.target.value)}>{roleOptions.map((role) => <option key={role.key} value={role.key}>{role.name}</option>)}</select></div>; })}{members.length === 0 && <div className="empty-panel">No members yet.</div>}</div></article><article className="data-card"><div className="card-heading"><div><span className="eyebrow">Authority</span><h2>Roles</h2></div><span>{roles.length}</span></div><div className="role-grid">{roles.map((role) => <article className="role-card" key={role.key}><div><strong>{role.name}</strong><span className="role-key">{role.key}</span></div><div className="capability-list">{role.permissions.slice(0, 8).map((permission) => <span key={permission}>{permissionLabel(permission)}</span>)}{role.permissions.length === 0 && <small>No capabilities granted.</small>}</div><div className="role-actions">{builtinRoles.has(role.key) ? <small>Built-in baseline role</small> : canManageRoles ? <button className="secondary-button" onClick={() => setEditingRole(role)}>Edit capabilities</button> : <small>Custom role</small>}</div>{role.permissions.length > 0 && <details><summary>Advanced · {role.permissions.length} raw permission{role.permissions.length === 1 ? "" : "s"}</summary><code>{role.permissions.join("\n")}</code></details>}</article>)}{roles.length === 0 && <div className="empty-panel">No roles available.</div>}</div></article></section>}
    {canManageRoles && editingRole && <div className="editor-overlay"><button className="editor-backdrop" aria-label="Close permission editor" onClick={() => setEditingRole(null)}></button><PermissionEditor role={editingRole} available={availablePermissions} onSave={(permissions) => savePermissions(editingRole.key, permissions)} onClose={() => setEditingRole(null)} /></div>}
  </main>;
}

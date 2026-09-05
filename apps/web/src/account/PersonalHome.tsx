import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { PersonalProfile } from "../app/types";
import { MessageContent } from "../ui/MessageContent";
import { OperlyMark } from "../ui/OperlyMark";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Artifact = { artifact_id: string; filename: string; content_type?: string | null; size_bytes?: number | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; created_at?: string | null; artifacts?: Artifact[] };
type ChatResult = { message: string; conversation_id?: string | null; artifacts?: Artifact[] };
type KernelApproval = {
  id: string;
  capability_id: string;
  status: string;
  arguments?: Record<string, unknown>;
  created_at?: string | null;
};
type KernelApprovalResponse = { approvals: KernelApproval[] };

type Props = { profile: PersonalProfile | null };

function formatDate(value?: string | null) {
  if (!value) return "";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value)); }
  catch { return ""; }
}

function artifactSize(value?: number | null) {
  const bytes = Number(value || 0);
  if (!bytes) return "File";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

function ArtifactCards({ artifacts }: { artifacts?: Artifact[] }) {
  if (!artifacts?.length) return null;
  return <div className="chat-artifacts" aria-label="Generated files">
    {artifacts.map((artifact) => <a
      className="artifact-chip"
      href={`/api/personal/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
      key={artifact.artifact_id}
    >
      <span className="artifact-icon" aria-hidden="true">↧</span>
      <span className="artifact-copy"><strong>{artifact.filename}</strong><small>{artifactSize(artifact.size_bytes)} · {artifact.content_type || "file"}</small></span>
      <span className="artifact-action">Download</span>
    </a>)}
  </div>;
}

function approvalStatus(value: string) {
  return value.replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function ApprovalCard({ item, busy, onDecision }: { item: KernelApproval; busy: boolean; onDecision: (approved: boolean) => void }) {
  return <article className="personal-approval-card">
    <div className="personal-approval-title">
      <div><span className={`status-chip status-${item.status.toLowerCase().replaceAll("_", "-")}`}>{approvalStatus(item.status)}</span><strong>{item.capability_id}</strong></div>
      <small>{formatDate(item.created_at)}</small>
    </div>
    <div className="approval-substance">
      <div className="approval-substance-grid">
        <span><small>Capability</small><strong>{item.capability_id}</strong></span>
        <span><small>Scope</small><strong>Personal account</strong></span>
      </div>
      <details>
        <summary>Review arguments</summary>
        <code>{JSON.stringify(item.arguments || {}, null, 2)}</code>
      </details>
    </div>
    {item.status === "pending" && <div className="row-actions">
      <button disabled={busy} onClick={() => onDecision(false)}>Reject</button>
      <button className="primary-button" disabled={busy} onClick={() => onDecision(true)}>{busy ? "Working…" : "Approve"}</button>
    </div>}
  </article>;
}

export function PersonalHome({ profile }: Props) {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationSearch, setConversationSearch] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [approvals, setApprovals] = useState<KernelApproval[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [approvalBusy, setApprovalBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [approvalError, setApprovalError] = useState<string | null>(null);
  const [mobileListOpen, setMobileListOpen] = useState(true);
  const [historyCollapsed, setHistoryCollapsed] = useState(() => {
    try { return window.localStorage.getItem("operly.personal-history-collapsed") === "true"; }
    catch { return false; }
  });
  const fileInput = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  async function loadConversations(prefer?: string | null) {
    const rows = await api<Conversation[]>("/personal-agent/conversations");
    setConversations(rows);
    const next = prefer || conversationId || rows[0]?.id || null;
    if (next && next !== conversationId) await openConversation(next, Boolean(prefer));
    else if (prefer && next) setMobileListOpen(false);
    if (!next) setMessages([]);
  }

  async function loadApprovals() {
    try {
      const response = await api<KernelApprovalResponse>("/kernel/personal/approvals?limit=12");
      setApprovals(Array.isArray(response.approvals) ? response.approvals : []);
      setApprovalError(null);
    } catch (caught) {
      setApprovalError(caught instanceof Error ? caught.message : "Personal approvals are unavailable");
    }
  }

  async function decideApproval(id: string, approved: boolean) {
    setApprovalBusy(id);
    setApprovalError(null);
    try {
      await api(`/kernel/personal/approvals/${encodeURIComponent(id)}/decision`, {
        method: "POST",
        body: JSON.stringify({ approved }),
      });
      await loadApprovals();
    } catch (caught) {
      setApprovalError(caught instanceof Error ? caught.message : "Approval decision could not be saved");
    } finally {
      setApprovalBusy(null);
    }
  }

  async function openConversation(id: string, revealOnMobile = true) {
    setConversationId(id);
    setError(null);
    if (revealOnMobile) setMobileListOpen(false);
    try {
      const rows = await api<Message[]>(`/personal-agent/conversations/${encodeURIComponent(id)}/messages`);
      setMessages(rows);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Conversation could not be loaded");
    }
  }

  function newConversation() {
    setConversationId(null);
    setMessages([]);
    setError(null);
    setMobileListOpen(false);
  }

  useEffect(() => {
    loadConversations().catch((caught) => setError(caught instanceof Error ? caught.message : "Conversation history is unavailable"));
    void loadApprovals();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    stage.current?.scrollTo({ top: stage.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  function toggleHistory() {
    setHistoryCollapsed((current) => {
      const next = !current;
      try { window.localStorage.setItem("operly.personal-history-collapsed", String(next)); } catch { /* optional */ }
      return next;
    });
  }

  function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const selected = [...(event.target.files || [])].slice(0, 10);
    setFiles((current) => [...current, ...selected].slice(0, 10));
    event.target.value = "";
  }

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if ((!trimmed && !files.length) || busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    const pendingFiles = files;
    setFiles([]);
    const visibleText = trimmed || `[Attached: ${pendingFiles.map((file) => file.name).join(", ")}]`;
    const optimistic: Message = { role: "user", content: visibleText };
    setMessages((current) => [...current, optimistic]);

    try {
      let result: ChatResult;
      if (pendingFiles.length) {
        const form = new FormData();
        form.append("message", trimmed);
        if (conversationId) form.append("conversation_id", conversationId);
        pendingFiles.forEach((file) => form.append("files", file, file.name));
        result = await apiForm<ChatResult>("/personal-agent/chat-with-attachments", form);
      } else {
        result = await api<ChatResult>("/personal-agent/chat", {
          method: "POST",
          body: JSON.stringify({ message: trimmed, conversation_id: conversationId, selected_workspace_id: null }),
        });
      }
      const nextId = result.conversation_id || conversationId;
      setConversationId(nextId || null);
      setMessages((current) => [...current, {
        role: "assistant",
        content: result.message || (result.artifacts?.length ? "Created the requested file." : "Done."),
        artifacts: result.artifacts || [],
      }]);
      await Promise.all([loadConversations(nextId), loadApprovals()]);
    } catch (caught) {
      setMessages((current) => {
        const index = current.lastIndexOf(optimistic);
        return index >= 0 ? current.filter((_, itemIndex) => itemIndex !== index) : current;
      });
      setError(caught instanceof Error ? caught.message : "Personal Operly could not complete that request");
    } finally {
      setBusy(false);
    }
  }

  const pendingApprovals = approvals.filter((item) => item.status === "pending");
  const normalizedSearch = conversationSearch.trim().toLowerCase();
  const filteredConversations = useMemo(
    () => conversations.filter((item) => !normalizedSearch || (item.title || "Conversation").toLowerCase().includes(normalizedSearch)),
    [conversations, normalizedSearch],
  );
  const activeConversation = conversations.find((item) => item.id === conversationId);

  return (
    <div className={`personal-layout ${historyCollapsed ? "history-collapsed" : ""} ${mobileListOpen ? "mobile-personal-list" : "mobile-personal-thread"}`}>
      <aside id="personal-conversation-history" className="personal-history" aria-label="Conversation history">
        <div className="history-head personal-message-list-head">
          <div><small>YOUR SPACE</small><strong>Messages</strong></div>
          <div className="history-head-actions">
            <button onClick={newConversation} aria-label="New conversation" title="New conversation">＋</button>
            <button className="history-collapse" onClick={toggleHistory} aria-label={historyCollapsed ? "Expand conversation history" : "Collapse conversation history"} title={historyCollapsed ? "Expand conversations" : "Collapse conversations"}>{historyCollapsed ? "›" : "‹"}</button>
          </div>
        </div>
        <label className="personal-conversation-search">
          <span aria-hidden="true">⌕</span>
          <input value={conversationSearch} onChange={(event) => setConversationSearch(event.target.value)} type="search" placeholder="Search conversations" aria-label="Search Personal Operly conversations" />
        </label>
        <div className="history-list">
          {filteredConversations.length === 0 && <p className="empty-copy">{conversations.length ? "No conversations match your search." : "Your private conversations will appear here."}</p>}
          {filteredConversations.map((item) => <button key={item.id} className={conversationId === item.id ? "active" : ""} onClick={() => openConversation(item.id)}><span>✦</span><span><strong>{item.title || "Conversation"}</strong><small>{formatDate(item.updated_at)}</small></span></button>)}
        </div>
        <div className="history-account"><span>{(profile?.display_name || profile?.email || "Me").slice(0, 1).toUpperCase()}</span><div><strong>{profile?.display_name || "Operly user"}</strong><small>{profile?.email || "Private account"}</small></div></div>
      </aside>

      <main className="personal-surface">
        <header className="mobile-content-header personal-mobile-content-header">
          <button type="button" onClick={() => setMobileListOpen(true)} aria-label="Back to conversations">←</button>
          <div><small>Personal Operly</small><strong>{activeConversation?.title || "New conversation"}</strong></div>
          <span className="privacy-pill">Private</span>
        </header>
        <header className="surface-header personal-surface-header compact-header page-header">
          <div><span className="eyebrow">@me · private</span><h1>Personal Operly</h1><p>One private AI for your account. It discovers only the context and capabilities needed for the task.</p></div>
          <div className="personal-header-actions"><span className="privacy-pill">Private</span>{pendingApprovals.length > 0 && <span className="status-chip status-pending">{pendingApprovals.length} approval{pendingApprovals.length === 1 ? "" : "s"}</span>}</div>
        </header>
        <div className="conversation-stage" ref={stage} aria-live="polite">
          {pendingApprovals.length > 0 && <section className="personal-approval-stack" aria-label="Pending Personal approvals">
            <div className="personal-approval-heading"><div><span className="eyebrow">Human control</span><h2>Needs your approval</h2></div><button className="text-button" type="button" onClick={() => { void loadApprovals(); }}>Refresh</button></div>
            {pendingApprovals.map((item) => <ApprovalCard key={item.id} item={item} busy={approvalBusy === item.id} onDecision={(approved) => { void decideApproval(item.id, approved); }} />)}
          </section>}
          {approvalError && <div className="inline-error">{approvalError}</div>}
          {messages.length === 0 && <article className="assistant-message"><span className="assistant-avatar brand-avatar"><OperlyMark /></span><div><strong>Operly</strong><p>What would you like to get done?</p></div></article>}
          {messages.map((item, index) => <article className={`chat-message ${item.role}`} key={item.id || `${item.role}-${index}`}><span className={`assistant-avatar ${item.role === "assistant" ? "brand-avatar" : ""}`}>{item.role === "assistant" ? <OperlyMark /> : "Y"}</span><div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong>{item.role === "assistant" ? <><MessageContent content={item.content} /><ArtifactCards artifacts={item.artifacts} /></> : <p>{item.content}</p>}</div></article>)}
          {busy && <div className="working-state"><span></span>Operly is working…</div>}
          {error && <div className="inline-error">{error}</div>}
        </div>
        <form className="composer" onSubmit={submit}>
          {files.length > 0 && <div className="attachment-strip">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></span>)}</div>}
          <input ref={fileInput} type="file" multiple hidden onChange={addFiles} accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh" />
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Message Operly…" rows={3} aria-label="Message Personal Operly" />
          <div className="composer-actions"><div><button type="button" className="attach-button" onClick={() => fileInput.current?.click()}>＋ Attach</button><span>Private account scope</span></div><button disabled={busy || (!message.trim() && !files.length)}>{busy ? "Working…" : "Send"}</button></div>
        </form>
      </main>
    </div>
  );
}

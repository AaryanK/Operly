import { ChangeEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { PersonalProfile } from "../app/types";
import { MessageContent } from "../ui/MessageContent";
import { OperlyMark } from "../ui/OperlyMark";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Artifact = { artifact_id: string; filename: string; content_type?: string | null; size_bytes?: number | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; created_at?: string | null; artifacts?: Artifact[] };
type ChatResult = { message: string; conversation_id?: string | null; artifacts?: Artifact[] };

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

export function PersonalHome({ profile }: Props) {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [conversationSearch, setConversationSearch] = useState("");
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mobileListOpen, setMobileListOpen] = useState(false);
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
    // The retired /approvals/personal surface is intentionally not queried here.
    // Personal approvals will return through the canonical Agent Runtime checkpoint
    // contract instead of reviving the pre-Kernel approvals router.
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
      await loadConversations(nextId);
    } catch (caught) {
      setMessages((current) => {
        const index = current.lastIndexOf(optimistic);
        return index >= 0 ? current.filter((_, itemIndex) => itemIndex !== index) : current;
      });
      setError(caught instanceof Error ? caught.message : "Personal Operly could not complete that request");
      // Do not silently restore failed files. Restoring them made repeated retries
      // re-upload the same bytes and accumulate duplicate synthetic transcript turns.
    } finally {
      setBusy(false);
    }
  }

  const normalizedSearch = conversationSearch.trim().toLowerCase();
  const filteredConversations = useMemo(
    () => conversations.filter((item) => !normalizedSearch || (item.title || "Conversation").toLowerCase().includes(normalizedSearch)),
    [conversations, normalizedSearch],
  );
  const activeConversation = conversations.find((item) => item.id === conversationId);

  return (
    <div className={`workspace-lite-personal-stage personal-layout ${historyCollapsed ? "history-collapsed" : ""} ${mobileListOpen ? "mobile-personal-list" : "mobile-personal-thread"}`}>
      <aside id="personal-conversation-history" className="personal-history" aria-label="Personal Operly conversation history">
        <div className="history-head personal-message-list-head">
          <div><small>PERSONAL OPERLY</small><strong>Conversations</strong></div>
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
        <div className="history-account"><span>{(profile?.display_name || profile?.email || "Me").slice(0, 1).toUpperCase()}</span><div><strong>{profile?.display_name || "Your account"}</strong><small>{profile?.email || "Private account scope"}</small></div></div>
      </aside>

      <main className="personal-surface">
        <header className="mobile-content-header personal-mobile-content-header">
          <button type="button" className="personal-mobile-chats" onClick={() => setMobileListOpen(true)} aria-label="Open Personal Operly conversations">← Chats</button>
          <div><small>Personal Operly</small><strong>{activeConversation?.title || "New conversation"}</strong></div>
          <span className="privacy-pill">Private</span>
        </header>
        <header className="surface-header personal-surface-header"><div><span className="eyebrow">PERSONAL · PRIVATE</span><h1>Personal Operly</h1><p>Your account-level AI. This conversation stays personal; access to a workspace still crosses that workspace’s permission boundary.</p></div><div className="personal-header-actions"><span className="privacy-pill">Private</span></div></header>
        <div className="conversation-stage" ref={stage} aria-live="polite">
          {messages.length === 0 && <>
            <article className="assistant-message"><span className="assistant-avatar brand-avatar"><OperlyMark /></span><div><strong>Operly</strong><p>I’m your personal Operly. Ask across your account, attach a file, or tell me which workspace you want to work with.</p></div></article>
            <div className="personal-boundary-note"><strong>Personal scope</strong><span>Workspace actions remain permission checked. Sensitive actions will use the Agent Runtime’s canonical human-control checkpoint rather than the retired legacy approvals page.</span></div>
          </>}
          {messages.map((item, index) => <article className={`chat-message ${item.role}`} key={item.id || `${item.role}-${index}`}><span className={`assistant-avatar ${item.role === "assistant" ? "brand-avatar" : ""}`}>{item.role === "assistant" ? <OperlyMark /> : "Y"}</span><div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong>{item.role === "assistant" ? <><MessageContent content={item.content} /><ArtifactCards artifacts={item.artifacts} /></> : <p>{item.content}</p>}</div></article>)}
          {busy && <div className="working-state"><span></span>Operly is working…</div>}
          {error && <div className="inline-error">{error}</div>}
        </div>
        <form className="composer" onSubmit={submit}>
          {files.length > 0 && <div className="attachment-strip">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" onClick={() => setFiles((current) => current.filter((_, itemIndex) => itemIndex !== index))}>×</button></span>)}</div>}
          <input ref={fileInput} type="file" multiple hidden onChange={addFiles} accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh" />
          <textarea value={message} onChange={(event) => setMessage(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Message Personal Operly…" rows={3} aria-label="Message Personal Operly" />
          <div className="composer-actions"><div><button type="button" className="attach-button" onClick={() => fileInput.current?.click()}>＋ Attach</button><span>Private account scope</span></div><button disabled={busy || (!message.trim() && !files.length)}>{busy ? "Working…" : "Send"}</button></div>
        </form>
      </main>
    </div>
  );
}

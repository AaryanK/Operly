import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { WorkspaceSummary } from "../app/types";
import { MessageContent } from "../ui/MessageContent";
import { OperlyMark } from "../ui/OperlyMark";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Artifact = { artifact_id: string; filename: string; content_type?: string | null; size_bytes?: number | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; created_at?: string | null; artifacts?: Artifact[] };
type ChatResult = { message: string; conversation_id: string; artifacts?: Artifact[] };

type Props = { workspace: WorkspaceSummary };

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
      href={`/api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`}
      key={artifact.artifact_id}
    >
      <span className="artifact-icon" aria-hidden="true">↧</span>
      <span className="artifact-copy"><strong>{artifact.filename}</strong><small>{artifactSize(artifact.size_bytes)} · {artifact.content_type || "file"}</small></span>
      <span className="artifact-action">Download</span>
    </a>)}
  </div>;
}

export function WorkspaceOperly({ workspace }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [mobileHistoryOpen, setMobileHistoryOpen] = useState(false);
  const [historyCollapsed, setHistoryCollapsed] = useState(() => {
    try { return window.localStorage.getItem("operly.workspace-history-collapsed") === "true"; }
    catch { return false; }
  });
  const picker = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  async function openConversation(id: string) {
    setConversationId(id);
    const rows = await api<Message[]>(`/agent/conversations/${encodeURIComponent(id)}/messages`);
    setMessages(rows);
    setMobileHistoryOpen(false);
  }

  async function refreshHistory(prefer?: string | null) {
    const rows = await api<Conversation[]>("/agent/conversations");
    setConversations(rows);
    const next = prefer || conversationId || rows[0]?.id;
    if (next && next !== conversationId) await openConversation(next);
  }

  useEffect(() => {
    setMobileHistoryOpen(false);
    refreshHistory().catch((caught) => setError(caught instanceof Error ? caught.message : "Conversation history is unavailable"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  useEffect(() => { stage.current?.scrollTo({ top: stage.current.scrollHeight, behavior: "smooth" }); }, [messages, busy]);

  useEffect(() => {
    if (!mobileHistoryOpen) return;
    const closeOnEscape = (event: KeyboardEvent) => {
      if (event.key === "Escape") setMobileHistoryOpen(false);
    };
    window.addEventListener("keydown", closeOnEscape);
    return () => window.removeEventListener("keydown", closeOnEscape);
  }, [mobileHistoryOpen]);

  function toggleHistory() {
    setHistoryCollapsed((current) => {
      const next = !current;
      try { window.localStorage.setItem("operly.workspace-history-collapsed", String(next)); } catch { /* optional */ }
      return next;
    });
  }

  function startNewConversation() {
    setConversationId(null);
    setMessages([]);
    setMobileHistoryOpen(false);
  }

  function addFiles(event: ChangeEvent<HTMLInputElement>) {
    const selected = [...(event.target.files || [])].slice(0, 10);
    setFiles((current) => [...current, ...selected].slice(0, 10));
    event.target.value = "";
  }

  async function send(event: FormEvent) {
    event.preventDefault();
    const message = text.trim();
    if ((!message && files.length === 0) || busy) return;
    const pendingFiles = files;
    const visible = message || "Analyze the supplied attachment(s).";
    setText(""); setFiles([]); setBusy(true); setError(null);
    setMessages((current) => [...current, { role: "user", content: visible }]);
    try {
      let result: ChatResult;
      if (pendingFiles.length) {
        const form = new FormData();
        form.append("message", message);
        if (conversationId) form.append("conversation_id", conversationId);
        pendingFiles.forEach((file) => form.append("files", file, file.name));
        result = await apiForm<ChatResult>("/agent/chat-with-attachments", form);
      } else {
        result = await api<ChatResult>("/agent/chat", { method: "POST", body: JSON.stringify({ message, conversation_id: conversationId, application_id: null }) });
      }
      setConversationId(result.conversation_id);
      setMessages((current) => [...current, {
        role: "assistant",
        content: result.message || (result.artifacts?.length ? "Created the requested file." : "Done."),
        artifacts: result.artifacts || [],
      }]);
      await refreshHistory(result.conversation_id);
    } catch (caught) {
      setFiles(pendingFiles);
      setError(caught instanceof Error ? caught.message : "Operly could not complete that request");
    } finally { setBusy(false); }
  }

  return <main className="workspace-page ai-page"><div className={`ai-layout-react ${historyCollapsed ? "history-collapsed" : ""}`}>
    <button className={`ai-history-backdrop ${mobileHistoryOpen ? "open" : ""}`} type="button" aria-label="Close conversation history" onClick={() => setMobileHistoryOpen(false)} />
    <aside className={`conversation-list-panel ${mobileHistoryOpen ? "mobile-open" : ""}`} aria-label="Workspace conversations">
      <div className="history-head"><div><small>WORKSPACE AI</small><strong>Conversations</strong></div><div className="history-head-actions"><button onClick={startNewConversation} aria-label="New conversation" title="New conversation">+</button><button className="history-collapse" onClick={toggleHistory} aria-label={historyCollapsed ? "Expand conversation history" : "Collapse conversation history"} title={historyCollapsed ? "Expand conversations" : "Collapse conversations"}>{historyCollapsed ? "›" : "‹"}</button><button className="ai-history-mobile-close" type="button" onClick={() => setMobileHistoryOpen(false)} aria-label="Close conversation history">×</button></div></div>
      <div className="history-list">{conversations.length === 0 && <p className="empty-copy">No workspace conversations yet.</p>}{conversations.map((item) => <button key={item.id} className={item.id === conversationId ? "active" : ""} onClick={() => openConversation(item.id)}><span>✦</span><span><strong>{item.title || "Conversation"}</strong><small>{item.updated_at ? new Date(item.updated_at).toLocaleDateString() : ""}</small></span></button>)}</div>
    </aside>
    <section className="ai-chat-panel">
      <header className="surface-header compact-header"><button className="ai-history-mobile-trigger" type="button" onClick={() => setMobileHistoryOpen(true)} aria-expanded={mobileHistoryOpen} aria-label="Open conversation history"><span aria-hidden="true">☰</span><span>Conversations</span></button><div><span className="eyebrow">Operly · {workspace.name}</span><h1>What should we work on?</h1><p>Workspace context, connectors, tools, approvals, and permissions stay inside this workspace boundary.</p></div><span className="workspace-context-pill">{workspace.name}</span></header>
      <div className="conversation-stage" ref={stage}>{messages.length === 0 && <div className="suggestion-grid"><button onClick={() => setText("What needs my attention right now?")}><strong>Needs attention</strong><span>Review exceptions and pending work</span></button><button onClick={() => setText("Summarize my current sales pipeline")}><strong>Sales pipeline</strong><span>Customers, leads, quotes and orders</span></button><button onClick={() => setText("Show me the actions waiting for my approval")}><strong>Approvals</strong><span>See consequential actions before execution</span></button></div>}{messages.map((item, index) => <article className={`chat-message ${item.role}`} key={item.id || `${item.role}-${index}`}><span className={`assistant-avatar ${item.role === "assistant" ? "brand-avatar" : ""}`}>{item.role === "assistant" ? <OperlyMark /> : "Y"}</span><div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong>{item.role === "assistant" ? <><MessageContent content={item.content} /><ArtifactCards artifacts={item.artifacts} /></> : <p>{item.content}</p>}</div></article>)}{busy && <div className="working-state"><span></span>Operly is working…</div>}{error && <div className="inline-error">{error}</div>}</div>
      <form className="composer" onSubmit={send}>{files.length > 0 && <div className="attachment-strip">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}>×</button></span>)}</div>}<input ref={picker} type="file" multiple hidden onChange={addFiles} accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh" /><textarea value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Ask Operly anything, or tell it what to do…" rows={2} /><div className="composer-actions"><div><button className="attach-button" type="button" onClick={() => picker.current?.click()}>＋ Attach</button><span>Permission- and approval-gated</span></div><button disabled={busy || (!text.trim() && !files.length)}>{busy ? "Working…" : "Send"}</button></div></form>
    </section>
  </div></main>;
}

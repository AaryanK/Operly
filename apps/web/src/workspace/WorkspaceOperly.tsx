import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { WorkspaceSummary } from "../app/types";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; created_at?: string | null };
type ChatResult = { message: string; conversation_id: string };

type Props = { workspace: WorkspaceSummary };

export function WorkspaceOperly({ workspace }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const picker = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  async function openConversation(id: string) {
    setConversationId(id);
    const rows = await api<Message[]>(`/agent/conversations/${encodeURIComponent(id)}/messages`);
    setMessages(rows);
  }

  async function refreshHistory(prefer?: string | null) {
    const rows = await api<Conversation[]>("/agent/conversations");
    setConversations(rows);
    const next = prefer || conversationId || rows[0]?.id;
    if (next && next !== conversationId) await openConversation(next);
  }

  useEffect(() => {
    refreshHistory().catch((caught) => setError(caught instanceof Error ? caught.message : "Conversation history is unavailable"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  useEffect(() => {
    stage.current?.scrollTo({ top: stage.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

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
    setText("");
    setFiles([]);
    setBusy(true);
    setError(null);
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
        result = await api<ChatResult>("/agent/chat", {
          method: "POST",
          body: JSON.stringify({ message, conversation_id: conversationId, application_id: null }),
        });
      }
      setConversationId(result.conversation_id);
      setMessages((current) => [...current, { role: "assistant", content: result.message || "Done." }]);
      await refreshHistory(result.conversation_id);
    } catch (caught) {
      setFiles(pendingFiles);
      setError(caught instanceof Error ? caught.message : "Operly could not complete that request");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="workspace-page ai-page">
      <div className="ai-layout-react">
        <aside className="conversation-list-panel">
          <div className="history-head"><div><small>WORKSPACE AI</small><strong>Conversations</strong></div><button onClick={() => { setConversationId(null); setMessages([]); }}>+</button></div>
          <div className="history-list">
            {conversations.length === 0 && <p className="empty-copy">No workspace conversations yet.</p>}
            {conversations.map((item) => <button key={item.id} className={item.id === conversationId ? "active" : ""} onClick={() => openConversation(item.id)}><span>✦</span><span><strong>{item.title || "Conversation"}</strong><small>{item.updated_at ? new Date(item.updated_at).toLocaleDateString() : ""}</small></span></button>)}
          </div>
        </aside>
        <section className="ai-chat-panel">
          <header className="surface-header compact-header"><div><span className="eyebrow">Operly · {workspace.name}</span><h1>What should we work on?</h1><p>Workspace context, connectors, tools, approvals, and permissions stay inside this workspace boundary.</p></div><span className="workspace-context-pill">{workspace.name}</span></header>
          <div className="conversation-stage" ref={stage}>
            {messages.length === 0 && <div className="suggestion-grid"><button onClick={() => setText("What needs my attention right now?")}><strong>Needs attention</strong><span>Review exceptions and pending work</span></button><button onClick={() => setText("Summarize my current sales pipeline") }><strong>Sales pipeline</strong><span>Customers, leads, quotes and orders</span></button><button onClick={() => setText("Show me the actions waiting for my approval") }><strong>Approvals</strong><span>See consequential actions before execution</span></button></div>}
            {messages.map((item, index) => <article className={`chat-message ${item.role}`} key={item.id || `${item.role}-${index}`}><span className="assistant-avatar">{item.role === "assistant" ? "✦" : "Y"}</span><div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong><p>{item.content}</p></div></article>)}
            {busy && <div className="working-state"><span></span>Operly is working…</div>}
            {error && <div className="inline-error">{error}</div>}
          </div>
          <form className="composer" onSubmit={send}>
            {files.length > 0 && <div className="attachment-strip">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}>×</button></span>)}</div>}
            <input ref={picker} type="file" multiple hidden onChange={addFiles} accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh" />
            <textarea value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder="Ask Operly anything, or tell it what to do…" rows={2} />
            <div className="composer-actions"><div><button className="attach-button" type="button" onClick={() => picker.current?.click()}>＋ Attach</button><span>Permission- and approval-gated</span></div><button disabled={busy || (!text.trim() && !files.length)}>{busy ? "Working…" : "Send"}</button></div>
          </form>
        </section>
      </div>
    </main>
  );
}

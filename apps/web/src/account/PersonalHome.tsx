import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { PersonalProfile } from "../app/types";
import { MessageContent } from "../ui/MessageContent";
import { OperlyMark } from "../ui/OperlyMark";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; created_at?: string | null };
type ChatResult = { message: string; conversation_id?: string | null };

type Props = { profile: PersonalProfile | null };

function formatDate(value?: string | null) {
  if (!value) return "";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(new Date(value)); }
  catch { return ""; }
}

export function PersonalHome({ profile }: Props) {
  const [message, setMessage] = useState("");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);

  async function loadConversations(prefer?: string | null) {
    const rows = await api<Conversation[]>("/personal-agent/conversations");
    setConversations(rows);
    const next = prefer || conversationId || rows[0]?.id || null;
    if (next && next !== conversationId) await openConversation(next);
    if (!next) setMessages([]);
  }

  async function openConversation(id: string) {
    setConversationId(id);
    setError(null);
    try {
      const rows = await api<Message[]>(`/personal-agent/conversations/${encodeURIComponent(id)}/messages`);
      setMessages(rows);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Conversation could not be loaded");
    }
  }

  useEffect(() => {
    loadConversations().catch((caught) => setError(caught instanceof Error ? caught.message : "Conversation history is unavailable"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    stage.current?.scrollTo({ top: stage.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

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
    const visibleText = trimmed || "Analyze the supplied attachment(s).";
    setMessages((current) => [...current, { role: "user", content: visibleText }]);

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
      setMessages((current) => [...current, { role: "assistant", content: result.message }]);
      await loadConversations(nextId);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Personal Operly could not complete that request");
      setFiles(pendingFiles);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="personal-layout">
      <aside className="personal-history">
        <div className="history-head"><div><small>YOUR SPACE</small><strong>Personal Operly</strong></div><button onClick={() => { setConversationId(null); setMessages([]); setError(null); }} aria-label="New conversation">+</button></div>
        <div className="history-list">
          {conversations.length === 0 && <p className="empty-copy">Your private conversations will appear here.</p>}
          {conversations.map((item) => <button key={item.id} className={conversationId === item.id ? "active" : ""} onClick={() => openConversation(item.id)}><span>✦</span><span><strong>{item.title || "Conversation"}</strong><small>{formatDate(item.updated_at)}</small></span></button>)}
        </div>
        <div className="history-account"><span>{(profile?.display_name || profile?.email || "Me").slice(0, 1).toUpperCase()}</span><div><strong>{profile?.display_name || "Operly user"}</strong><small>{profile?.email || "Private account"}</small></div></div>
      </aside>

      <main className="personal-surface">
        <header className="surface-header"><div><span className="eyebrow">@me · private</span><h1>Operly</h1><p>Your account-level AI. This transcript stays personal; workspace tools are reached only through permission-checked account capabilities.</p></div><span className="privacy-pill">Private</span></header>
        <div className="conversation-stage" ref={stage} aria-live="polite">
          {messages.length === 0 && <article className="assistant-message"><span className="assistant-avatar brand-avatar"><OperlyMark /></span><div><strong>Operly</strong><p>I’m your private Operly. Ask across your account, attach a file, or tell me which workspace you want me to work with.</p></div></article>}
          {messages.map((item, index) => <article className={`chat-message ${item.role}`} key={item.id || `${item.role}-${index}`}><span className={`assistant-avatar ${item.role === "assistant" ? "brand-avatar" : ""}`}>{item.role === "assistant" ? <OperlyMark /> : "Y"}</span><div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong>{item.role === "assistant" ? <MessageContent content={item.content} /> : <p>{item.content}</p>}</div></article>)}
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

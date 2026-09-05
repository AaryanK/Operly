import { ChangeEvent, FormEvent, useEffect, useRef, useState } from "react";

import { api, apiForm } from "../api";
import { navigate } from "../app/routes";
import { WorkspaceSummary } from "../app/types";
import { MessageContent } from "../ui/MessageContent";
import { OperlyMark } from "../ui/OperlyMark";

type Conversation = { id: string; title?: string | null; updated_at?: string | null };
type Artifact = { artifact_id: string; filename: string; content_type?: string | null; size_bytes?: number | null };
type Message = { id?: string; role: "user" | "assistant"; content: string; artifacts?: Artifact[] };
type ChatResult = { message: string; conversation_id: string; artifacts?: Artifact[] };

type Props = {
  workspace: WorkspaceSummary;
  onClose: () => void;
};

function artifactSize(value?: number | null) {
  const bytes = Number(value || 0);
  if (!bytes) return "File";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${Math.ceil(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(bytes >= 10 * 1024 * 1024 ? 0 : 1)} MB`;
}

export function WorkspaceAssistantPanel({ workspace, onClose }: Props) {
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [conversations, setConversations] = useState<Conversation[]>([]);
  const [messages, setMessages] = useState<Message[]>([]);
  const [text, setText] = useState("");
  const [files, setFiles] = useState<File[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const picker = useRef<HTMLInputElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const fullPagePath = `/channels/${encodeURIComponent(workspace.id)}/operly`;

  async function openConversation(id: string) {
    setConversationId(id);
    setError("");
    try {
      setMessages(await api<Message[]>(`/agent/conversations/${encodeURIComponent(id)}/messages`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open this conversation");
    }
  }

  async function refreshHistory(prefer?: string | null) {
    const rows = await api<Conversation[]>("/agent/conversations");
    setConversations(rows);
    const next = prefer || conversationId || rows[0]?.id;
    if (next) await openConversation(next);
  }

  useEffect(() => {
    setConversationId(null);
    setMessages([]);
    setFiles([]);
    setText("");
    setError("");
    refreshHistory().catch((caught) => setError(caught instanceof Error ? caught.message : "Conversation history is unavailable"));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  useEffect(() => {
    stage.current?.scrollTo({ top: stage.current.scrollHeight, behavior: "smooth" });
  }, [messages, busy]);

  function startNewConversation() {
    setConversationId(null);
    setMessages([]);
    setText("");
    setFiles([]);
    setError("");
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
    setText("");
    setFiles([]);
    setBusy(true);
    setError("");
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
      setMessages((current) => [...current, {
        role: "assistant",
        content: result.message || (result.artifacts?.length ? "Created the requested file." : "Done."),
        artifacts: result.artifacts || [],
      }]);
      const rows = await api<Conversation[]>("/agent/conversations");
      setConversations(rows);
    } catch (caught) {
      setFiles(pendingFiles);
      setError(caught instanceof Error ? caught.message : "Operly could not complete that request");
    } finally {
      setBusy(false);
    }
  }

  return <section className="workspace-assistant-panel" aria-label={`Operly assistant for ${workspace.name}`}>
    <header className="workspace-assistant-header">
      <div className="workspace-assistant-identity"><OperlyMark /><span><small>OPERLY IN</small><strong>{workspace.name}</strong></span></div>
      <div className="workspace-assistant-header-actions">
        <button type="button" onClick={startNewConversation} title="New conversation" aria-label="New conversation">＋</button>
        <a href={fullPagePath} onClick={(event) => { event.preventDefault(); navigate(fullPagePath); }} title="Open full Operly" aria-label="Open Operly full page">↗</a>
        <button type="button" onClick={onClose} title="Close Operly" aria-label="Close Operly">×</button>
      </div>
    </header>

    <div className="workspace-assistant-history">
      <label><span>Conversation</span><select value={conversationId || ""} onChange={(event) => event.target.value ? void openConversation(event.target.value) : startNewConversation()}>
        <option value="">New conversation</option>
        {conversations.map((item) => <option value={item.id} key={item.id}>{item.title || "Conversation"}</option>)}
      </select></label>
    </div>

    <div className="workspace-assistant-stage" ref={stage}>
      {messages.length === 0 && <div className="workspace-assistant-welcome">
        <OperlyMark />
        <h2>Ask Operly</h2>
        <p>I can work with this workspace's context, tools, connectors, approvals, and permissions without taking you away from what you're looking at.</p>
        <div>
          <button type="button" onClick={() => setText("What needs my attention right now?")}>What needs attention?</button>
          <button type="button" onClick={() => setText("Summarize what changed recently in this workspace")}>What changed?</button>
          <button type="button" onClick={() => setText("What actions are waiting for my approval?")}>Pending approvals</button>
        </div>
      </div>}

      {messages.map((item, index) => <article className={`workspace-assistant-message ${item.role}`} key={item.id || `${item.role}-${index}`}>
        <span className="workspace-assistant-avatar">{item.role === "assistant" ? <OperlyMark /> : "Y"}</span>
        <div><strong>{item.role === "assistant" ? "Operly" : "You"}</strong>{item.role === "assistant" ? <MessageContent content={item.content} /> : <p>{item.content}</p>}
          {!!item.artifacts?.length && <div className="workspace-assistant-artifacts">{item.artifacts.map((artifact) => <a href={`/api/artifacts/${encodeURIComponent(artifact.artifact_id)}/download`} key={artifact.artifact_id}><strong>{artifact.filename}</strong><small>{artifactSize(artifact.size_bytes)} · {artifact.content_type || "file"}</small></a>)}</div>}
        </div>
      </article>)}
      {busy && <div className="workspace-assistant-working"><span />Operly is working…</div>}
      {error && <div className="workspace-assistant-error">{error}</div>}
    </div>

    <form className="workspace-assistant-composer" onSubmit={send}>
      {files.length > 0 && <div className="workspace-assistant-attachments">{files.map((file, index) => <span key={`${file.name}-${index}`}>{file.name}<button type="button" onClick={() => setFiles((current) => current.filter((_, i) => i !== index))}>×</button></span>)}</div>}
      <input ref={picker} type="file" multiple hidden onChange={addFiles} accept="image/*,.pdf,.txt,.md,.csv,.tsv,.json,.xml,.yaml,.yml,.docx,.pptx,.xlsx,.odt,.ods,.html,.log,.css,.sql,.py,.js,.ts,.tsx,.jsx,.java,.c,.cpp,.h,.go,.rs,.rb,.php,.sh" />
      <textarea value={text} onChange={(event) => setText(event.target.value)} onKeyDown={(event) => { if (event.key === "Enter" && !event.shiftKey && !event.nativeEvent.isComposing) { event.preventDefault(); event.currentTarget.form?.requestSubmit(); } }} placeholder={`Ask Operly about ${workspace.name}…`} rows={3} />
      <div className="workspace-assistant-composer-actions"><button type="button" className="workspace-assistant-attach" onClick={() => picker.current?.click()}>＋ Attach</button><span>Workspace-scoped · approval-gated</span><button type="submit" className="workspace-assistant-send" disabled={busy || (!text.trim() && !files.length)}>{busy ? "Working…" : "Send"}</button></div>
    </form>
  </section>;
}

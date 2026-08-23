import { FormEvent, useState } from "react";

import { api } from "../api";

export function PersonalHome() {
  const [message, setMessage] = useState("");
  const [reply, setReply] = useState("I’m your private Operly. I can work across your account and use workspace context only when your permissions allow it.");
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    const trimmed = message.trim();
    if (!trimmed || busy) return;
    setBusy(true);
    setError(null);
    setMessage("");
    try {
      const result = await api<{
        message: string;
        conversation_id?: string | null;
      }>("/personal-agent/chat", {
        method: "POST",
        body: JSON.stringify({
          message: trimmed,
          conversation_id: conversationId,
          selected_workspace_id: null,
        }),
      });
      setConversationId(result.conversation_id || conversationId);
      setReply(result.message);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Personal Operly could not complete that request");
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="personal-surface">
      <header className="surface-header">
        <div>
          <span className="eyebrow">@me · private</span>
          <h1>Operly</h1>
          <p>Your account-level AI. Personal context does not become workspace context unless you choose to share it.</p>
        </div>
      </header>

      <section className="conversation-stage" aria-live="polite">
        <article className="assistant-message">
          <span className="assistant-avatar">✦</span>
          <div><strong>Operly</strong><p>{reply}</p></div>
        </article>
        {error && <div className="inline-error">{error}</div>}
      </section>

      <form className="composer" onSubmit={submit}>
        <textarea
          value={message}
          onChange={(event) => setMessage(event.target.value)}
          placeholder="Message Operly…"
          rows={3}
          aria-label="Message Personal Operly"
        />
        <div className="composer-actions">
          <span>Private account scope</span>
          <button disabled={busy || !message.trim()}>{busy ? "Working…" : "Send"}</button>
        </div>
      </form>
    </main>
  );
}

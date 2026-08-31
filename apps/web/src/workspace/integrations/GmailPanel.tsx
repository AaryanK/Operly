import { FormEvent, useEffect, useState } from "react";

import {
  Row,
  list,
  object,
  splitList,
  text,
  useIntegrationRuntime,
} from "./runtime";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-panel">{children}</div>;
}

export function GmailPanel() {
  const runtime = useIntegrationRuntime();
  const [query, setQuery] = useState("in:inbox newer_than:14d");
  const [messages, setMessages] = useState<Row[]>([]);
  const [selected, setSelected] = useState<Row | null>(null);
  const ready = runtime.available("google.gmail.search");

  async function searchMessages(event?: FormEvent) {
    event?.preventDefault();
    await runtime.invoke(
      "google.gmail.search",
      { query, limit: 20 },
      `Search Gmail for “${query}”`,
      (value) => setMessages(list(object(value).messages)),
    );
  }

  async function readMessage(message: Row) {
    const messageId = text(message.id);
    if (!messageId) return;
    await runtime.invoke(
      "google.gmail.read_message",
      { message_id: messageId },
      `Read Gmail message “${text(message.subject, "message")}”`,
      (value) => setSelected(object(value)),
    );
  }

  async function compose(form: HTMLFormElement, mode: "draft" | "send") {
    const fields = new FormData(form);
    const args = {
      to: splitList(fields.get("to")),
      cc: splitList(fields.get("cc")),
      bcc: splitList(fields.get("bcc")),
      subject: text(fields.get("subject")),
      text_body: text(fields.get("body")),
    };
    const capability =
      mode === "draft" ? "google.gmail.create_draft" : "google.gmail.send_email";
    await runtime.invoke(
      capability,
      args,
      mode === "draft"
        ? `Create Gmail draft “${args.subject}”`
        : `Send “${args.subject}” to ${args.to.join(", ")}`,
      () => {
        if (mode === "send") form.reset();
      },
    );
  }

  async function submitCompose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    await compose(event.currentTarget, "send");
  }

  async function modifyLabels(
    addLabelIds: string[],
    removeLabelIds: string[],
    summary: string,
    clearSelection = false,
  ) {
    const messageId = text(selected?.id);
    if (!messageId) return;
    await runtime.invoke(
      "google.gmail.modify_labels",
      {
        message_id: messageId,
        add_label_ids: addLabelIds,
        remove_label_ids: removeLabelIds,
      },
      summary,
      async () => {
        if (clearSelection) setSelected(null);
        else if (selected) await readMessage(selected);
        await searchMessages();
      },
    );
  }

  useEffect(() => {
    if (ready && messages.length === 0) void searchMessages();
    // Run only when Gmail becomes executable for the current workspace.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, runtime.workspace.id]);

  const labels = new Set(
    Array.isArray(selected?.label_ids)
      ? selected.label_ids.map((item) => text(item)).filter(Boolean)
      : [],
  );

  return (
    <section className="integration-split">
      <article className="data-card integration-list-pane">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Google Workspace</span>
            <h2>Inbox</h2>
          </div>
          <span className={`status-chip ${ready ? "status-active" : ""}`}>
            {ready ? "Connected" : "Needs permission"}
          </span>
        </div>
        <form className="integration-search" onSubmit={searchMessages}>
          <input
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="Gmail search syntax"
            aria-label="Gmail search"
          />
          <button disabled={!ready || runtime.busy === "google.gmail.search"}>Search</button>
        </form>
        <div className="integration-scroll-list">
          {messages.map((message) => (
            <button
              type="button"
              key={text(message.id)}
              className={selected?.id === message.id ? "active" : ""}
              onClick={() => void readMessage(message)}
            >
              <strong>{text(message.subject, "(no subject)")}</strong>
              <span>{text(message.from, "Unknown sender")}</span>
              <p>{text(message.snippet)}</p>
              <small>{text(message.date)}</small>
            </button>
          ))}
          {!messages.length && <Empty>No messages loaded.</Empty>}
        </div>
      </article>

      <div className="integration-detail-stack">
        <article className="data-card">
          {selected ? (
            <>
              <div className="card-heading">
                <div>
                  <span className="eyebrow">Message</span>
                  <h2>{text(selected.subject, "(no subject)")}</h2>
                </div>
                {runtime.available("google.gmail.modify_labels") && (
                  <div className="row-actions">
                    <button
                      type="button"
                      onClick={() =>
                        void modifyLabels([], ["INBOX"], "Archive selected Gmail message", true)
                      }
                    >
                      Archive
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        void modifyLabels(
                          labels.has("UNREAD") ? [] : ["UNREAD"],
                          labels.has("UNREAD") ? ["UNREAD"] : [],
                          labels.has("UNREAD")
                            ? "Mark selected Gmail message as read"
                            : "Mark selected Gmail message as unread",
                        )
                      }
                    >
                      {labels.has("UNREAD") ? "Mark read" : "Mark unread"}
                    </button>
                    <button
                      type="button"
                      onClick={() =>
                        void modifyLabels(
                          labels.has("STARRED") ? [] : ["STARRED"],
                          labels.has("STARRED") ? ["STARRED"] : [],
                          labels.has("STARRED")
                            ? "Remove star from selected Gmail message"
                            : "Star selected Gmail message",
                        )
                      }
                    >
                      {labels.has("STARRED") ? "Unstar" : "Star"}
                    </button>
                  </div>
                )}
              </div>
              <p className="integration-meta">
                From {text(selected.from)} · To {text(selected.to)} · {text(selected.date)}
              </p>
              <pre className="integration-message-body">
                {text(
                  selected.text_body,
                  text(selected.snippet, "This message has no plain-text body."),
                )}
              </pre>
            </>
          ) : (
            <Empty>Select a message to read it.</Empty>
          )}
        </article>

        <article className="data-card">
          <div className="card-heading">
            <div>
              <span className="eyebrow">Compose</span>
              <h2>New email</h2>
            </div>
            <small>Send requires exact-action approval</small>
          </div>
          <form className="integration-form" onSubmit={submitCompose}>
            <label>
              To
              <input name="to" required placeholder="name@example.com, another@example.com" />
            </label>
            <label>
              CC
              <input name="cc" placeholder="Optional" />
            </label>
            <label>
              BCC
              <input name="bcc" placeholder="Optional" />
            </label>
            <label>
              Subject
              <input name="subject" required maxLength={998} />
            </label>
            <label>
              Message
              <textarea name="body" rows={8} />
            </label>
            <div className="row-actions">
              <button
                type="button"
                disabled={
                  !runtime.available("google.gmail.create_draft") ||
                  runtime.busy === "google.gmail.create_draft"
                }
                onClick={(event) => {
                  const form = event.currentTarget.form;
                  if (form) void compose(form, "draft");
                }}
              >
                Save draft
              </button>
              <button
                className="primary-button"
                disabled={
                  !runtime.available("google.gmail.send_email") ||
                  runtime.busy === "google.gmail.send_email"
                }
              >
                Review & send
              </button>
            </div>
          </form>
        </article>
      </div>
    </section>
  );
}

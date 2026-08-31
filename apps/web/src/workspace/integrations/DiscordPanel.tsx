import { FormEvent, useEffect, useState } from "react";

import { Row, formatWhen, list, object, text, useIntegrationRuntime } from "./runtime";

function Empty({ children }: { children: React.ReactNode }) {
  return <div className="empty-panel">{children}</div>;
}

export function DiscordPanel() {
  const runtime = useIntegrationRuntime();
  const [installations, setInstallations] = useState<Row[]>([]);
  const [channels, setChannels] = useState<Row[]>([]);
  const [channel, setChannel] = useState<Row | null>(null);
  const [messages, setMessages] = useState<Row[]>([]);
  const channelsReady = runtime.available("discord.channels.list");

  async function loadDiscord() {
    if (runtime.available("discord.installations.list")) {
      await runtime.invoke(
        "discord.installations.list",
        {},
        "Load Discord servers bound to this workspace",
        (value) => setInstallations(list(object(value).installations)),
      );
    }
    if (channelsReady) {
      await runtime.invoke(
        "discord.channels.list",
        {},
        "Load Discord channels available to this workspace",
        (value) => setChannels(list(object(value).channels)),
      );
    }
  }

  async function selectChannel(next: Row) {
    setChannel(next);
    const channelId = text(next.channel_id);
    if (!channelId) return;
    await runtime.invoke(
      "discord.messages.list",
      { channel_id: channelId, limit: 50 },
      `Load #${text(next.name)} Discord history`,
      (value) => setMessages(list(object(value).messages)),
    );
  }

  async function sendMessage(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!channel) return;
    const form = event.currentTarget;
    const fields = new FormData(form);
    const content = text(fields.get("content")).trim();
    if (!content) return;
    await runtime.invoke(
      "discord.message.send",
      { channel_id: text(channel.channel_id), content },
      `Send message to #${text(channel.name)}`,
      async () => {
        form.reset();
        await selectChannel(channel);
      },
    );
  }

  async function addReaction(message: Row, emoji: string) {
    if (!channel) return;
    await runtime.invoke(
      "discord.reaction.add",
      {
        channel_id: text(channel.channel_id),
        message_id: text(message.message_id),
        emoji,
      },
      `React ${emoji} to a message in #${text(channel.name)}`,
    );
  }

  async function createThread(message: Row) {
    if (!channel) return;
    const name = window.prompt("Thread name")?.trim();
    if (!name) return;
    await runtime.invoke(
      "discord.thread.create",
      {
        channel_id: text(channel.channel_id),
        message_id: text(message.message_id),
        name,
      },
      `Create Discord thread “${name}” in #${text(channel.name)}`,
    );
  }

  useEffect(() => {
    if (channelsReady) void loadDiscord();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [channelsReady, runtime.workspace.id]);

  return (
    <section className="integration-discord-layout">
      <article className="data-card integration-list-pane">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Discord</span>
            <h2>Channels</h2>
          </div>
          <span
            className={`status-chip ${runtime.discordStatus?.ready ? "status-active" : ""}`}
          >
            {runtime.discordStatus?.ready ? "Bot online" : "Bot offline"}
          </span>
        </div>

        <div className="integration-installations">
          {installations.map((installation) => (
            <span className="status-chip" key={text(installation.id)}>
              {text(installation.display_name, text(installation.guild_id, "Discord server"))}
            </span>
          ))}
        </div>

        {!runtime.discordStatus?.configured && runtime.canManage && (
          <button type="button" className="primary-button" onClick={runtime.addDiscord}>
            Add Discord bot
          </button>
        )}

        <div className="integration-scroll-list">
          {channels.map((item) => (
            <button
              type="button"
              key={text(item.channel_id)}
              className={channel?.channel_id === item.channel_id ? "active" : ""}
              onClick={() => void selectChannel(item)}
            >
              <strong>#{text(item.name)}</strong>
              <span>{text(item.guild_name)}</span>
              <small>{item.can_send ? "Can send" : "Read only"}</small>
            </button>
          ))}
          {!channels.length && (
            <Empty>
              {installations.length
                ? "The bot cannot currently view any bound channels."
                : "No Discord servers are bound to this workspace."}
            </Empty>
          )}
        </div>
      </article>

      <article className="data-card integration-chat-pane">
        <div className="card-heading">
          <div>
            <span className="eyebrow">Deterministic messaging</span>
            <h2>{channel ? `#${text(channel.name)}` : "Select a channel"}</h2>
          </div>
          <span className="status-chip">AI off</span>
        </div>
        <p className="integration-meta">
          Operly rechecks the workspace binding and the bot's live Discord channel permissions
          before each read or write.
        </p>

        <div className="integration-chat-log">
          {messages.map((message) => (
            <div className="integration-chat-message" key={text(message.message_id)}>
              <span className="mini-avatar">
                {text(message.author, "?").slice(0, 1).toUpperCase()}
              </span>
              <div>
                <strong>{text(message.author, "Unknown")}</strong>
                <small>{formatWhen(message.created_at)}</small>
                <p>{text(message.content)}</p>
                <div className="integration-message-actions">
                  <button
                    type="button"
                    disabled={!runtime.available("discord.reaction.add")}
                    onClick={() => void addReaction(message, "👍")}
                  >
                    👍
                  </button>
                  <button
                    type="button"
                    disabled={!runtime.available("discord.reaction.add")}
                    onClick={() => void addReaction(message, "✅")}
                  >
                    ✅
                  </button>
                  <button
                    type="button"
                    disabled={!runtime.available("discord.thread.create")}
                    onClick={() => void createThread(message)}
                  >
                    Thread
                  </button>
                </div>
              </div>
            </div>
          ))}
          {channel && !messages.length && <Empty>No recent messages.</Empty>}
        </div>

        {channel && (
          <form className="integration-chat-compose" onSubmit={sendMessage}>
            <textarea
              name="content"
              rows={3}
              required
              maxLength={1900}
              placeholder={`Message #${text(channel.name)}`}
            />
            <button
              className="primary-button"
              disabled={!runtime.available("discord.message.send")}
            >
              Review & send
            </button>
          </form>
        )}
      </article>
    </section>
  );
}

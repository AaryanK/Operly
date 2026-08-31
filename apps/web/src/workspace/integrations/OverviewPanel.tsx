import { useMemo } from "react";

import { useIntegrationRuntime } from "./runtime";

export type IntegrationTab =
  | "overview"
  | "gmail"
  | "calendar"
  | "canva"
  | "discord"
  | "connections";

export function OverviewPanel({ onOpen }: { onOpen: (tab: IntegrationTab) => void }) {
  const runtime = useIntegrationRuntime();
  const providerCounts = useMemo(
    () => ({
      google: runtime.connections.filter((item) => item.provider === "google").length,
      canva: runtime.connections.filter((item) => item.provider === "canva").length,
      discord: runtime.connections.filter((item) => item.provider === "discord").length,
    }),
    [runtime.connections],
  );
  const integrationToolCount = runtime.tools.filter(
    (tool) =>
      tool.provider_id.includes("google") ||
      tool.provider_id.includes("canva") ||
      tool.provider_id.includes("discord"),
  ).length;

  const cards = [
    {
      id: "gmail" as const,
      icon: "G",
      title: "Gmail",
      description:
        "Search and read mail, compose drafts, send approved messages, archive, star, and manage read state.",
      ready: runtime.available("google.gmail.search"),
    },
    {
      id: "calendar" as const,
      icon: "31",
      title: "Calendar",
      description:
        "Browse calendars and events, check free/busy, create meetings, update events, delete, and add Meet links.",
      ready: runtime.available("google.calendar.list_events"),
    },
    {
      id: "canva" as const,
      icon: "C",
      title: "Canva",
      description:
        "Browse designs, create and export, use Uploads, inspect Data Autofill fields, and generate or update designs.",
      ready: runtime.available("canva.designs.list"),
    },
    {
      id: "discord" as const,
      icon: "#",
      title: "Discord",
      description:
        "Browse bound servers and channels, read history, send approved messages, react, and create threads.",
      ready: runtime.available("discord.channels.list"),
    },
  ];

  return (
    <>
      <section className="metric-grid">
        <article className="metric-card">
          <span>Available integration tools</span>
          <strong>{integrationToolCount}</strong>
          <small>Current Workspace + provider authority resolved</small>
        </article>
        <article className="metric-card">
          <span>Google</span>
          <strong>{providerCounts.google ? "Connected" : "Not connected"}</strong>
          <small>Gmail and Calendar</small>
        </article>
        <article className="metric-card">
          <span>Canva</span>
          <strong>{providerCounts.canva ? "Connected" : "Not connected"}</strong>
          <small>Designs, Autofill, assets and export</small>
        </article>
        <article className="metric-card">
          <span>Discord</span>
          <strong>
            {runtime.discordStatus?.ready
              ? "Online"
              : runtime.discordStatus?.configured
                ? "Offline"
                : "Not configured"}
          </strong>
          <small>AI off · deterministic bot only</small>
        </article>
      </section>

      <section className="integration-provider-grid">
        {cards.map((card) => (
          <button
            type="button"
            className="integration-provider-card"
            key={card.id}
            onClick={() => onOpen(card.id)}
          >
            <span className="integration-provider-icon">{card.icon}</span>
            <div>
              <strong>{card.title}</strong>
              <p>{card.description}</p>
            </div>
            <span className={`status-chip ${card.ready ? "status-active" : ""}`}>
              {card.ready ? "Ready" : "Unavailable"}
            </span>
          </button>
        ))}
      </section>

      <section className="data-card integration-foundation-note">
        <span className="eyebrow">One execution substrate</span>
        <h2>These are not special frontend APIs</h2>
        <p>
          Every operational panel discovers the same currently executable Workspace tools and
          calls the endpoint advertised by that tool. Human approval, idempotency, validation,
          trace, events, Workspace permissions, and provider authority stay below every interface.
        </p>
        <button type="button" onClick={() => onOpen("connections")}>
          Manage connected accounts
        </button>
      </section>
    </>
  );
}

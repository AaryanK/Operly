import {
  ReactNode,
  createContext,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";

import { ApiError, api } from "../../api";
import { WorkspaceSummary } from "../../app/types";

export type Row = Record<string, unknown>;

export type Connection = {
  id: string;
  provider: string;
  connector_type: string;
  display_name: string;
  account?: string | null;
  status: string;
  enabled: boolean;
  health_status?: string | null;
  last_health_check?: string | null;
  last_error?: string | null;
  scopes?: string[];
  capabilities?: string[];
  guild_id?: string;
};

export type DiscordStatus = {
  configured: boolean;
  ready: boolean;
  bot_user: string | null;
  invite_url: string | null;
  ai_enabled: boolean;
};

export type Tool = {
  id: string;
  display_name: string;
  description: string;
  provider_id: string;
  approval_required: boolean;
  endpoint: string;
  method: "POST";
  permissions: string[];
  risk: string;
};

type ToolIndex = { tools: Tool[] };
type ToolRun = {
  run_id: string;
  status: string;
  capability_id: string;
  result: unknown;
  done: boolean;
};

type PendingApproval = {
  approvalId: string;
  requestId: string;
  tool: Tool;
  args: Row;
  summary: string;
  onSuccess?: (value: unknown) => void | Promise<void>;
};

type IntegrationRuntime = {
  workspace: WorkspaceSummary;
  connections: Connection[];
  discordStatus: DiscordStatus | null;
  tools: Tool[];
  loading: boolean;
  busy: string | null;
  notice: string | null;
  error: string | null;
  pending: PendingApproval | null;
  canManage: boolean;
  available: (id: string) => boolean;
  reload: () => Promise<void>;
  invoke: (
    id: string,
    args: Row,
    summary: string,
    onSuccess?: (value: unknown) => void | Promise<void>,
  ) => Promise<unknown | undefined>;
  approvePending: () => Promise<void>;
  cancelPending: () => void;
  clearFeedback: () => void;
  startOAuth: (provider: "google" | "canva") => Promise<void>;
  addDiscord: () => void;
  testConnection: (connection: Connection) => Promise<void>;
  disconnect: (connection: Connection) => Promise<void>;
};

const IntegrationRuntimeContext = createContext<IntegrationRuntime | null>(null);

export const text = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : value == null ? fallback : String(value);

export const object = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};

export const list = (value: unknown): Row[] =>
  Array.isArray(value)
    ? value.filter(
        (item): item is Row => !!item && typeof item === "object" && !Array.isArray(item),
      )
    : [];

export const splitList = (value: FormDataEntryValue | null) =>
  text(value)
    .split(/[;,\n]/)
    .map((item) => item.trim())
    .filter(Boolean);

export const providerName = (provider: string) =>
  provider === "google"
    ? "Google Workspace"
    : provider === "canva"
      ? "Canva"
      : provider === "discord"
        ? "Discord"
        : provider;

export const formatWhen = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  const date = new Date(raw);
  return Number.isNaN(date.getTime())
    ? raw
    : new Intl.DateTimeFormat(undefined, {
        month: "short",
        day: "numeric",
        hour: "numeric",
        minute: "2-digit",
      }).format(date);
};

const stableRequestId = (capabilityId: string) => {
  try {
    return `${capabilityId}:${crypto.randomUUID()}`;
  } catch {
    return `${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  }
};

export function IntegrationRuntimeProvider({
  workspace,
  children,
}: {
  workspace: WorkspaceSummary;
  children: ReactNode;
}) {
  const [connections, setConnections] = useState<Connection[]>([]);
  const [discordStatus, setDiscordStatus] = useState<DiscordStatus | null>(null);
  const [tools, setTools] = useState<Tool[]>([]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState<PendingApproval | null>(null);

  const toolMap = useMemo(() => new Map(tools.map((tool) => [tool.id, tool])), [tools]);
  const available = (id: string) => toolMap.has(id);
  const canManage = workspace.role === "owner";

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [connectorRows, discord, index] = await Promise.all([
        api<Connection[]>("/connectors"),
        api<DiscordStatus>("/connectors/discord/status"),
        api<ToolIndex>("/workspace-tools"),
      ]);
      setConnections(connectorRows);
      setDiscordStatus(discord);
      setTools(index.tools);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : "Could not resolve workspace integration authority",
      );
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    setPending(null);
    setNotice(null);
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  async function invoke(
    id: string,
    args: Row,
    summary: string,
    onSuccess?: (value: unknown) => void | Promise<void>,
  ) {
    const tool = toolMap.get(id);
    if (!tool) {
      setError(
        `${id} is not currently available for your workspace permissions or provider grants.`,
      );
      return undefined;
    }

    const requestId = stableRequestId(id);
    setBusy(id);
    setError(null);
    setNotice(null);
    try {
      const run = await api<ToolRun>(tool.endpoint, {
        method: tool.method,
        body: JSON.stringify({ arguments: args, request_id: requestId }),
      });
      await onSuccess?.(run.result);
      setNotice(`${tool.display_name} completed.`);
      return run.result;
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "approval_required") {
        const details = object(caught.details);
        const approvalId = text(details.approval_id);
        if (approvalId) {
          setPending({ approvalId, requestId, tool, args, summary, onSuccess });
          return undefined;
        }
      }
      setError(caught instanceof Error ? caught.message : `${tool.display_name} failed`);
      return undefined;
    } finally {
      setBusy(null);
    }
  }

  async function approvePending() {
    if (!pending) return;
    setBusy(pending.tool.id);
    setError(null);
    try {
      await api(
        `/workspace-tools/approvals/${encodeURIComponent(pending.approvalId)}/decision`,
        {
          method: "POST",
          body: JSON.stringify({ approved: true }),
        },
      );
      const run = await api<ToolRun>(pending.tool.endpoint, {
        method: pending.tool.method,
        body: JSON.stringify({
          arguments: pending.args,
          request_id: pending.requestId,
          approval_id: pending.approvalId,
        }),
      });
      await pending.onSuccess?.(run.result);
      setNotice(`${pending.tool.display_name} completed after approval.`);
      setPending(null);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approved action could not execute");
    } finally {
      setBusy(null);
    }
  }

  async function startOAuth(provider: "google" | "canva") {
    setBusy(provider);
    setError(null);
    try {
      const path =
        provider === "google"
          ? "/connectors/google/connect?tier=assistant"
          : "/connectors/canva/connect";
      const result = await api<{ authorization_url: string }>(path, { method: "POST" });
      window.location.assign(result.authorization_url);
    } catch (caught) {
      setError(
        caught instanceof Error
          ? caught.message
          : `Could not connect ${providerName(provider)}`,
      );
      setBusy(null);
    }
  }

  function addDiscord() {
    if (!discordStatus?.invite_url) {
      setError("Discord bot installation is not configured on the server yet.");
      return;
    }
    window.open(discordStatus.invite_url, "_blank", "noopener,noreferrer");
    setNotice(
      `After adding the bot, run !operly bind ${workspace.name} in that Discord server.`,
    );
  }

  async function testConnection(connection: Connection) {
    setBusy(`test:${connection.id}`);
    setError(null);
    try {
      const result = await api<{
        ok: boolean;
        health_status: string;
        error?: string | null;
      }>(`/connectors/${encodeURIComponent(connection.id)}/test`, { method: "POST" });
      setNotice(
        result.ok
          ? `${providerName(connection.provider)} is healthy.`
          : result.error || "Connection test failed.",
      );
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Connection test failed");
    } finally {
      setBusy(null);
    }
  }

  async function disconnect(connection: Connection) {
    if (!window.confirm(`Disconnect ${providerName(connection.provider)} from this workspace?`)) {
      return;
    }
    setBusy(`disconnect:${connection.id}`);
    setError(null);
    try {
      await api(`/connectors/${encodeURIComponent(connection.id)}`, { method: "DELETE" });
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not disconnect integration");
    } finally {
      setBusy(null);
    }
  }

  const value: IntegrationRuntime = {
    workspace,
    connections,
    discordStatus,
    tools,
    loading,
    busy,
    notice,
    error,
    pending,
    canManage,
    available,
    reload,
    invoke,
    approvePending,
    cancelPending: () => setPending(null),
    clearFeedback: () => {
      setNotice(null);
      setError(null);
    },
    startOAuth,
    addDiscord,
    testConnection,
    disconnect,
  };

  return (
    <IntegrationRuntimeContext.Provider value={value}>
      {children}
    </IntegrationRuntimeContext.Provider>
  );
}

export function useIntegrationRuntime() {
  const context = useContext(IntegrationRuntimeContext);
  if (!context) throw new Error("Integration runtime is not mounted");
  return context;
}

export function IntegrationApprovalDialog() {
  const { pending, busy, approvePending, cancelPending } = useIntegrationRuntime();
  if (!pending) return null;
  return (
    <div
      className="integration-approval-backdrop"
      role="dialog"
      aria-modal="true"
      aria-label="Approve integration action"
    >
      <article className="integration-approval-card">
        <span className="eyebrow">Human approval required</span>
        <h2>{pending.tool.display_name}</h2>
        <p>{pending.summary}</p>
        <div className="approval-substance-grid">
          <span>
            <small>Permission</small>
            <strong>{pending.tool.permissions.join(", ")}</strong>
          </span>
          <span>
            <small>Risk</small>
            <strong>{pending.tool.risk}</strong>
          </span>
          <span>
            <small>Provider</small>
            <strong>{pending.tool.provider_id}</strong>
          </span>
        </div>
        <details>
          <summary>Exact arguments</summary>
          <pre>{JSON.stringify(pending.args, null, 2)}</pre>
        </details>
        <div className="row-actions">
          <button type="button" onClick={cancelPending}>
            Cancel
          </button>
          <button
            type="button"
            className="primary-button"
            disabled={busy === pending.tool.id}
            onClick={() => void approvePending()}
          >
            {busy === pending.tool.id ? "Executing…" : "Approve exact action"}
          </button>
        </div>
      </article>
    </div>
  );
}

import { FormEvent, useEffect, useMemo, useState } from "react";

import { api, ApiError } from "../api";
import { OperlyMark } from "../ui/OperlyMark";

type AdminSession = { user?: { display_name?: string; email?: string } };
type FlowModel = { provider?: string; model?: string };
type FlowRun = {
  runId: string;
  conversationId?: string | null;
  tenantId?: string | null;
  userId?: string | null;
  principalId?: string | null;
  surface?: string | null;
  channel?: string | null;
  components?: string[];
  phases?: string[];
  status?: string;
  startedAt?: string;
  finishedAt?: string;
  durationMs?: number;
  entryCount?: number;
  errorCount?: number;
  modelCandidatesObserved?: FlowModel[];
  tokenUsage?: { inputTokens?: number; outputTokens?: number; totalTokens?: number };
};
type FlowEntry = {
  id?: string;
  runId?: string;
  attemptId?: string;
  step?: number | null;
  component?: string | null;
  phase?: string;
  resourceId?: string;
  provider?: string;
  providerModelId?: string;
  attempt?: number;
  latencyMs?: number | null;
  classification?: string | null;
  retryable?: boolean | null;
  trace?: unknown;
  createdAt?: string;
};
type FlowListResponse = {
  live?: boolean;
  readOnly?: boolean;
  coverage?: string;
  redactionApplied?: boolean;
  hiddenReasoningRedacted?: boolean;
  runs?: FlowRun[];
};
type FlowRunDetail = FlowRun & {
  readOnly?: boolean;
  coverage?: string;
  redactionApplied?: boolean;
  hiddenReasoningRedacted?: boolean;
  entries?: FlowEntry[];
};

type FlowState = "checking" | "ready" | "login" | "error";

function short(value?: string | null, size = 12) {
  const text = String(value || "");
  if (!text) return "—";
  return text.length <= size ? text : `${text.slice(0, size)}…`;
}

function formatTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatDateTime(value?: string | null) {
  if (!value) return "—";
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return "—";
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    second: "2-digit",
  }).format(date);
}

function formatDuration(value?: number | null) {
  const ms = Math.max(0, Number(value || 0));
  if (ms < 1000) return `${Math.round(ms)} ms`;
  if (ms < 60_000) return `${(ms / 1000).toFixed(ms < 10_000 ? 2 : 1)} s`;
  return `${(ms / 60_000).toFixed(1)} min`;
}

function statusLabel(value?: string) {
  const status = String(value || "running").toLowerCase();
  return status.charAt(0).toUpperCase() + status.slice(1);
}

function entryTitle(entry: FlowEntry) {
  const component = String(entry.component || "").trim();
  const phase = String(entry.phase || "event").trim();
  if (component) return `${component} · ${phase}`;
  return phase;
}

function entrySubtitle(entry: FlowEntry) {
  const resource = String(entry.resourceId || "").trim();
  const model = [entry.provider, entry.providerModelId].filter(Boolean).join(" / ");
  return resource || model || "Operly runtime";
}

function pretty(value: unknown) {
  try {
    return JSON.stringify(value ?? {}, null, 2);
  } catch {
    return String(value ?? "");
  }
}

function tracePayload(entry?: FlowEntry | null): unknown {
  if (!entry || typeof entry.trace !== "object" || entry.trace === null) return entry?.trace ?? {};
  const trace = entry.trace as Record<string, unknown>;
  return trace.payload ?? trace;
}

function runMatches(run: FlowRun, query: string, status: string, surface: string) {
  if (status !== "all" && String(run.status || "running") !== status) return false;
  if (surface !== "all" && String(run.surface || "runtime") !== surface) return false;
  if (!query) return true;
  const haystack = [
    run.runId,
    run.conversationId,
    run.tenantId,
    run.userId,
    run.principalId,
    run.surface,
    run.channel,
    ...(run.components || []),
    ...(run.phases || []),
    ...(run.modelCandidatesObserved || []).flatMap((model) => [model.provider, model.model]),
  ]
    .filter(Boolean)
    .join(" ")
    .toLowerCase();
  return haystack.includes(query.toLowerCase());
}

function FlowLogin({ onReady }: { onReady: (session: AdminSession) => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");

  async function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    setBusy(true);
    setError("");
    try {
      await api("/auth/bootstrap");
      await api("/auth/login", {
        method: "POST",
        body: JSON.stringify({ email: form.get("email"), password: form.get("password") }),
      });
      const session = await api<AdminSession>("/admin/session");
      onReady(session);
    } catch (caught) {
      setError(
        caught instanceof ApiError && caught.status === 403
          ? "This account is not the configured Operly platform administrator."
          : caught instanceof Error
            ? caught.message
            : "FLOW sign-in failed",
      );
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="flow-login-page">
      <section className="flow-login-card">
        <div className="flow-brand"><OperlyMark className="flow-mark" /><div><strong>OPERLY / FLOW</strong><span>Live runtime debugger</span></div></div>
        <span className="flow-eyebrow">Protected engineering surface</span>
        <h1>Trace the system while it runs.</h1>
        <p>FLOW reads redacted production runtime evidence. Only the configured platform administrator can open it.</p>
        <form onSubmit={submit}>
          <label>Email<input name="email" type="email" autoComplete="username" required /></label>
          <label>Password<input name="password" type="password" autoComplete="current-password" required /></label>
          {error && <div className="flow-error">{error}</div>}
          <button className="flow-primary" disabled={busy}>{busy ? "Opening FLOW…" : "Open FLOW"}</button>
        </form>
        <a href="/">← Back to Operly</a>
      </section>
    </main>
  );
}

export function FlowPage() {
  const [state, setState] = useState<FlowState>("checking");
  const [session, setSession] = useState<AdminSession | null>(null);
  const [runs, setRuns] = useState<FlowRun[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string>("");
  const [detail, setDetail] = useState<FlowRunDetail | null>(null);
  const [selectedEntryId, setSelectedEntryId] = useState<string>("");
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [surface, setSurface] = useState("all");
  const [live, setLive] = useState(true);
  const [loadingRuns, setLoadingRuns] = useState(false);
  const [loadingDetail, setLoadingDetail] = useState(false);
  const [error, setError] = useState("");
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null);

  const selectedEntry = useMemo(
    () => (detail?.entries || []).find((entry) => entry.id === selectedEntryId) || detail?.entries?.[0] || null,
    [detail, selectedEntryId],
  );

  const surfaces = useMemo(
    () => Array.from(new Set(runs.map((run) => String(run.surface || "runtime")))).sort(),
    [runs],
  );

  const filteredRuns = useMemo(
    () => runs.filter((run) => runMatches(run, query.trim(), status, surface)),
    [runs, query, status, surface],
  );

  async function loadRuns(silent = false) {
    if (!silent) setLoadingRuns(true);
    try {
      const response = await api<FlowListResponse>("/flow/runs?limit=150");
      const nextRuns = response.runs || [];
      setRuns(nextRuns);
      setLastRefresh(new Date());
      setError("");
      setSelectedRunId((current) => current || nextRuns[0]?.runId || "");
    } catch (caught) {
      if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) {
        setState("login");
      } else {
        setError(caught instanceof Error ? caught.message : "FLOW could not load runtime traces");
      }
    } finally {
      if (!silent) setLoadingRuns(false);
    }
  }

  async function loadDetail(runId: string, silent = false) {
    if (!runId) {
      setDetail(null);
      return;
    }
    if (!silent) setLoadingDetail(true);
    try {
      const next = await api<FlowRunDetail>(`/flow/runs/${encodeURIComponent(runId)}`);
      setDetail(next);
      setSelectedEntryId((current) => {
        if (current && (next.entries || []).some((entry) => entry.id === current)) return current;
        return next.entries?.[0]?.id || "";
      });
      setError("");
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "FLOW could not load this run");
    } finally {
      if (!silent) setLoadingDetail(false);
    }
  }

  useEffect(() => {
    let active = true;
    api<AdminSession>("/admin/session")
      .then((next) => {
        if (!active) return;
        setSession(next);
        setState("ready");
      })
      .catch((caught: unknown) => {
        if (!active) return;
        if (caught instanceof ApiError && (caught.status === 401 || caught.status === 403)) setState("login");
        else {
          setState("error");
          setError(caught instanceof Error ? caught.message : "FLOW authorization check failed");
        }
      });
    return () => { active = false; };
  }, []);

  useEffect(() => {
    if (state !== "ready") return;
    void loadRuns();
  }, [state]);

  useEffect(() => {
    if (state !== "ready" || !selectedRunId) return;
    void loadDetail(selectedRunId);
  }, [state, selectedRunId]);

  useEffect(() => {
    if (state !== "ready" || !live) return;
    const timer: ReturnType<typeof setInterval> = setInterval(() => {
      void loadRuns(true);
      if (selectedRunId) void loadDetail(selectedRunId, true);
    }, 2500);
    return () => clearInterval(timer);
  }, [state, live, selectedRunId]);

  if (state === "checking") {
    return <main className="flow-gate"><div className="flow-spinner" /><strong>Opening FLOW</strong><span>Checking platform-admin access…</span></main>;
  }
  if (state === "login") {
    return <FlowLogin onReady={(next) => { setSession(next); setState("ready"); }} />;
  }
  if (state === "error") {
    return <main className="flow-gate"><strong>FLOW unavailable</strong><span>{error || "The debug surface could not start."}</span><a href="/">Back to Operly</a></main>;
  }

  const entries = detail?.entries || [];
  const totalTokens = detail?.tokenUsage?.totalTokens || 0;
  const adminName = session?.user?.display_name || session?.user?.email || "Platform admin";

  return (
    <main className="flow-page">
      <header className="flow-topbar">
        <div className="flow-brand"><OperlyMark className="flow-mark" /><div><strong>OPERLY / FLOW</strong><span>Live runtime debugger</span></div></div>
        <div className="flow-top-actions">
          <span className={`flow-live-pill ${live ? "is-live" : ""}`}><i />{live ? "Following live" : "Paused"}</span>
          <button type="button" onClick={() => setLive((value) => !value)}>{live ? "Pause" : "Follow live"}</button>
          <button type="button" onClick={() => { void loadRuns(); if (selectedRunId) void loadDetail(selectedRunId); }}>Refresh</button>
          <a href="/">Open Operly ↗</a>
        </div>
      </header>

      <section className="flow-summarybar">
        <div><span>Mode</span><strong>Read-only production evidence</strong></div>
        <div><span>Coverage</span><strong>Canonical AgentRuntime</strong></div>
        <div><span>Safety</span><strong>Secrets + hidden reasoning redacted</strong></div>
        <div><span>Viewer</span><strong>{adminName}</strong></div>
      </section>

      {error && <div className="flow-global-error"><span>!</span>{error}<button type="button" onClick={() => setError("")}>Dismiss</button></div>}

      <section className="flow-toolbar">
        <label className="flow-search"><span>⌕</span><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search run, user, workspace, component, model…" /></label>
        <select value={status} onChange={(event) => setStatus(event.target.value)} aria-label="Filter by status">
          <option value="all">All statuses</option>
          <option value="running">Running</option>
          <option value="success">Success</option>
          <option value="recovered">Recovered</option>
          <option value="failed">Failed</option>
          <option value="blocked">Blocked</option>
        </select>
        <select value={surface} onChange={(event) => setSurface(event.target.value)} aria-label="Filter by surface">
          <option value="all">All surfaces</option>
          {surfaces.map((item) => <option value={item} key={item}>{item}</option>)}
        </select>
        <div className="flow-refresh-note">{loadingRuns ? "Reading traces…" : lastRefresh ? `Updated ${formatTime(lastRefresh.toISOString())}` : "Live trace store"}</div>
      </section>

      <section className="flow-workbench">
        <aside className="flow-runs" aria-label="Recent FLOW runs">
          <header><div><span className="flow-eyebrow">Recent runtime executions</span><strong>{filteredRuns.length} runs</strong></div><small>Newest first</small></header>
          <div className="flow-run-list">
            {filteredRuns.map((run) => (
              <button
                type="button"
                className={`flow-run-card ${run.runId === selectedRunId ? "is-selected" : ""}`}
                key={run.runId}
                onClick={() => { setSelectedRunId(run.runId); setSelectedEntryId(""); }}
              >
                <div className="flow-run-head"><span className={`flow-status flow-status-${run.status || "running"}`}>{statusLabel(run.status)}</span><time>{formatTime(run.startedAt)}</time></div>
                <strong>{run.surface || "runtime"}</strong>
                <p>{(run.components || []).slice(0, 3).join(" → ") || "AgentRuntime"}</p>
                <div className="flow-run-meta"><span>{run.entryCount || 0} events</span><span>{formatDuration(run.durationMs)}</span>{Number(run.errorCount || 0) > 0 && <span className="has-error">{run.errorCount} errors</span>}</div>
                <code>{short(run.runId, 18)}</code>
              </button>
            ))}
            {!filteredRuns.length && <div className="flow-empty">No runtime traces match these filters.</div>}
          </div>
        </aside>

        <section className="flow-timeline-panel">
          <header className="flow-panel-head">
            <div><span className="flow-eyebrow">Actual execution path</span><h1>{detail?.surface || "Select a run"}</h1></div>
            {detail && <div className="flow-run-id"><span>RUN</span><code>{detail.runId}</code></div>}
          </header>

          {detail ? (
            <>
              <div className="flow-run-facts">
                <div><span>Status</span><strong className={`flow-text-${detail.status || "running"}`}>{statusLabel(detail.status)}</strong></div>
                <div><span>Duration</span><strong>{formatDuration(detail.durationMs)}</strong></div>
                <div><span>Events</span><strong>{entries.length}</strong></div>
                <div><span>Tokens</span><strong>{new Intl.NumberFormat().format(totalTokens)}</strong></div>
                <div><span>Workspace</span><strong title={detail.tenantId || "Personal"}>{short(detail.tenantId || "Personal", 16)}</strong></div>
              </div>

              <div className="flow-path-strip" aria-label="Observed runtime components">
                {(detail.components || []).length ? (detail.components || []).map((component, index) => (
                  <span key={`${component}-${index}`}><b>{component}</b>{index < (detail.components || []).length - 1 && <i>→</i>}</span>
                )) : <span><b>runtime</b></span>}
              </div>

              <div className="flow-timeline" aria-busy={loadingDetail}>
                {entries.map((entry, index) => (
                  <button
                    type="button"
                    className={`flow-event ${entry.id === selectedEntry?.id ? "is-selected" : ""} ${entry.phase === "error" ? "is-error" : ""}`}
                    key={entry.id || `${entry.createdAt}-${index}`}
                    onClick={() => setSelectedEntryId(entry.id || "")}
                  >
                    <span className="flow-event-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="flow-event-line"><i /></span>
                    <span className="flow-event-copy">
                      <span className="flow-event-top"><strong>{entryTitle(entry)}</strong><time>{formatTime(entry.createdAt)}</time></span>
                      <small>{entrySubtitle(entry)}</small>
                      <span className="flow-event-tags">
                        {entry.step !== null && entry.step !== undefined && <em>step {entry.step}</em>}
                        {entry.latencyMs !== null && entry.latencyMs !== undefined && <em>{formatDuration(entry.latencyMs)}</em>}
                        {entry.classification && <em>{entry.classification}</em>}
                        {entry.attempt && entry.attempt > 1 && <em>attempt {entry.attempt}</em>}
                      </span>
                    </span>
                  </button>
                ))}
                {!entries.length && <div className="flow-empty">This run has no persisted runtime events.</div>}
              </div>
            </>
          ) : <div className="flow-empty flow-empty-large">Choose a run to inspect its execution path.</div>}
        </section>

        <aside className="flow-inspector" aria-label="Selected FLOW event evidence">
          <header><span className="flow-eyebrow">Evidence inspector</span><strong>{selectedEntry ? entryTitle(selectedEntry) : "No event selected"}</strong></header>
          {selectedEntry ? (
            <>
              <dl className="flow-inspector-meta">
                <div><dt>Created</dt><dd>{formatDateTime(selectedEntry.createdAt)}</dd></div>
                <div><dt>Resource</dt><dd>{selectedEntry.resourceId || "—"}</dd></div>
                <div><dt>Provider</dt><dd>{selectedEntry.provider || "—"}</dd></div>
                <div><dt>Model</dt><dd>{selectedEntry.providerModelId || "—"}</dd></div>
                <div><dt>Attempt</dt><dd>{selectedEntry.attempt || 1}</dd></div>
                <div><dt>Retryable</dt><dd>{selectedEntry.retryable === null || selectedEntry.retryable === undefined ? "—" : selectedEntry.retryable ? "Yes" : "No"}</dd></div>
              </dl>
              <div className="flow-evidence-note"><span>◆</span><p><strong>Redacted runtime truth</strong><small>Credential-shaped fields and hidden reasoning are removed before persistence.</small></p></div>
              <div className="flow-json-head"><span>Persisted packet</span><code>{short(selectedEntry.id, 14)}</code></div>
              <pre className="flow-json">{pretty(tracePayload(selectedEntry))}</pre>
            </>
          ) : <div className="flow-empty">Select an event to inspect its persisted evidence.</div>}
        </aside>
      </section>

      <footer className="flow-footer">
        <span><i /> FLOW is observational: it does not replay or alter live runs.</span>
        <span>Current trace coverage is AgentRuntime/model/tool telemetry; ordinary browser render and arbitrary database call spans are not yet recorded.</span>
      </footer>
    </main>
  );
}

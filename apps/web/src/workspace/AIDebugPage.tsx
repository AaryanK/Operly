import { useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type ModelObserved = { provider: string; model: string };
type TokenUsage = { inputTokens: number; outputTokens: number; totalTokens: number };
type AIRun = {
  kind: "runtime" | "studio";
  runId: string;
  conversationId?: string | null;
  surface: string;
  channel?: string | null;
  components: string[];
  status: string;
  startedAt: string;
  finishedAt: string;
  entryCount: number;
  errorCount: number;
  successCount: number;
  modelCandidatesObserved: ModelObserved[];
  tokenUsage: TokenUsage;
  operation?: string;
  projectId?: string;
};
type RunListResponse = { runCount: number; runs: AIRun[]; redactionApplied: boolean; hiddenReasoningRedacted: boolean };
type TraceEntry = {
  id: string;
  phase: string;
  createdAt: string;
  provider?: string;
  providerModelId?: string;
  resourceId?: string;
  component?: string | null;
  step?: number | null;
  latencyMs?: number | null;
  classification?: string | null;
  retryable?: boolean | null;
  callIndex?: number;
  trace: Record<string, unknown>;
};
type RunDetail = AIRun & { entries: TraceEntry[]; instruction?: string; redactionApplied: boolean; hiddenReasoningRedacted: boolean };

function number(value: number | undefined) {
  return new Intl.NumberFormat().format(value || 0);
}

function when(value: string) {
  try { return new Date(value).toLocaleString(); }
  catch { return value; }
}

function statusClass(value: string) {
  return `status-chip status-${value.replaceAll("_", "-")}`;
}

function pretty(value: unknown) {
  return JSON.stringify(value, null, 2);
}

function visiblePayload(entry: TraceEntry): unknown {
  const envelope = entry.trace || {};
  if (typeof envelope === "object" && envelope !== null && "payload" in envelope) {
    return (envelope as { payload?: unknown }).payload;
  }
  return envelope;
}

export function AIDebugPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [runs, setRuns] = useState<AIRun[]>([]);
  const [selected, setSelected] = useState<AIRun | null>(null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(false);
  const [detailLoading, setDetailLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [status, setStatus] = useState("all");
  const [surface, setSurface] = useState("all");

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const response = await api<RunListResponse>(`/runtime-traces/runs?tenant_id=${encodeURIComponent(workspace.id)}&limit=150`);
      setRuns(response.runs);
      if (selected) {
        const refreshed = response.runs.find((run) => run.runId === selected.runId && run.kind === selected.kind);
        if (refreshed) setSelected(refreshed);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "AI traces could not be loaded");
    } finally {
      setLoading(false);
    }
  }

  async function openRun(run: AIRun) {
    setSelected(run);
    setDetail(null);
    setDetailLoading(true);
    setError(null);
    try {
      const response = await api<RunDetail>(`/runtime-traces/runs/${encodeURIComponent(run.runId)}?tenant_id=${encodeURIComponent(workspace.id)}&kind=${run.kind}`);
      setDetail(response);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "AI run could not be loaded");
    } finally {
      setDetailLoading(false);
    }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  const surfaces = useMemo(() => Array.from(new Set(runs.map((run) => run.surface))).sort(), [runs]);
  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    return runs.filter((run) => {
      if (status !== "all" && run.status !== status) return false;
      if (surface !== "all" && run.surface !== surface) return false;
      if (!needle) return true;
      const haystack = [
        run.runId,
        run.conversationId,
        run.surface,
        run.channel,
        run.operation,
        run.projectId,
        ...run.components,
        ...run.modelCandidatesObserved.flatMap((model) => [model.provider, model.model]),
      ].filter(Boolean).join(" ").toLowerCase();
      return haystack.includes(needle);
    });
  }, [query, runs, status, surface]);

  return <main className="workspace-page">
    <header className="surface-header page-header">
      <div>
        <span className="eyebrow">Debug</span>
        <h1>AI runs</h1>
        <p>Inspect the exact model-visible request, supplied context, tools, provider/model selection, responses, retries, token usage, and failures for every persisted AI execution in this workspace.</p>
      </div>
      <div className="page-actions"><button className="secondary-button" onClick={reload} disabled={loading}>{loading ? "Refreshing…" : "Refresh"}</button></div>
    </header>

    {error && <div className="inline-error page-error">{error}</div>}

    <section className="content-grid two-column">
      <article className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Trace index</span><h2>Executions</h2></div><span>{filtered.length}</span></div>
        <div className="inline-form">
          <label>Search<input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Run, model, provider, component…" /></label>
          <label>Status<select value={status} onChange={(event) => setStatus(event.target.value)}><option value="all">All</option><option value="success">Success</option><option value="recovered">Recovered</option><option value="failed">Failed</option><option value="running">Running</option><option value="queued">Queued</option></select></label>
          <label>Surface<select value={surface} onChange={(event) => setSurface(event.target.value)}><option value="all">All</option>{surfaces.map((item) => <option value={item} key={item}>{item}</option>)}</select></label>
        </div>
        {filtered.length ? <div className="row-list">{filtered.map((run) => {
          const primaryModel = run.modelCandidatesObserved[0];
          return <button className={`data-row stacked ${selected?.runId === run.runId && selected?.kind === run.kind ? "active" : ""}`} key={`${run.kind}:${run.runId}`} onClick={() => openRun(run)}>
            <div>
              <strong>{run.surface} · {run.kind === "studio" ? run.operation || "Studio" : run.components[0] || "AI runtime"}</strong>
              <span className={statusClass(run.status)}>{run.status}</span>
              <p>{primaryModel ? `${primaryModel.provider} / ${primaryModel.model}` : "Model not recorded"} · {number(run.tokenUsage.inputTokens)} in / {number(run.tokenUsage.outputTokens)} out · {when(run.startedAt)}</p>
              <p><code>{run.runId}</code>{run.conversationId ? ` · conversation ${run.conversationId}` : ""}{run.projectId ? ` · project ${run.projectId}` : ""}</p>
            </div>
          </button>;
        })}</div> : <div className="empty-panel">{loading ? "Loading AI runs…" : "No AI runs match these filters."}</div>}
      </article>

      <article className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Full trace</span><h2>{selected ? selected.runId : "Select a run"}</h2></div>{selected && <span>{selected.entryCount}</span>}</div>
        {!selected && <div className="empty-panel">Choose an execution to inspect everything Operly supplied to the model and everything returned from the model/provider boundary.</div>}
        {selected && detailLoading && <div className="empty-panel">Loading the complete model trace…</div>}
        {detail && <div className="row-list">
          <div className="data-row stacked"><div><strong>Run summary</strong><p>{detail.surface} · {detail.status} · {number(detail.tokenUsage.totalTokens)} recorded tokens · {detail.modelCandidatesObserved.map((model) => `${model.provider}/${model.model}`).join(" → ") || "No model provenance recorded"}</p>{detail.instruction && <details><summary>Studio instruction</summary><pre><code>{detail.instruction}</code></pre></details>}</div></div>
          {detail.entries.map((entry, index) => <div className="data-row stacked" key={entry.id || `${entry.phase}:${index}`}>
            <div>
              <strong>{entry.phase}{entry.component ? ` · ${entry.component}` : ""}{entry.callIndex ? ` · call ${entry.callIndex}` : ""}</strong>
              <span className={statusClass(entry.phase === "error" ? "failed" : entry.phase === "success" || entry.phase === "response" ? "success" : "running")}>{entry.phase}</span>
              <p>{entry.providerModelId ? `${entry.provider || "provider"} / ${entry.providerModelId}` : entry.resourceId || "model boundary"}{entry.latencyMs != null ? ` · ${entry.latencyMs} ms` : ""}{entry.classification ? ` · ${entry.classification}` : ""} · {when(entry.createdAt)}</p>
              <details open={entry.phase === "start" || entry.phase === "request" || entry.phase === "wire_request"}>
                <summary>Entire persisted model-visible payload</summary>
                <pre><code>{pretty(visiblePayload(entry))}</code></pre>
              </details>
              <details><summary>Trace envelope / integrity metadata</summary><pre><code>{pretty(entry.trace)}</code></pre></details>
            </div>
          </div>)}
        </div>}
      </article>
    </section>
    <p className="surface-note">Provider credentials, cookies, OAuth tokens, and hidden provider reasoning are redacted before persistence. Model-visible prompts, messages, context, tool schemas/results, normalized wire requests, responses, usage, and failures remain inspectable.</p>
  </main>;
}

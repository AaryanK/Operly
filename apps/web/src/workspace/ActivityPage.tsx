import { FormEvent, useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";
import { decideCapabilityApproval } from "../runtime/capabilityRuntime";

type Row = Record<string, unknown>;
type ActivityData = {
  messages: Row[];
  tasks: Row[];
  toolApprovals: Row[];
  toolEvents: Row[];
};

const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const titleCase = (value: unknown) => text(value, "unknown").replaceAll("_", " ").replaceAll(".", " › ").replace(/\b\w/g, (char) => char.toUpperCase());
const when = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(raw)); }
  catch { return raw; }
};

function PageHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="surface-header page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div></header>;
}

function Empty({ children }: { children: React.ReactNode }) { return <div className="empty-panel">{children}</div>; }
function Status({ value }: { value: unknown }) { return <span className={`status-chip status-${text(value).toLowerCase().replaceAll("_", "-")}`}>{titleCase(value)}</span>; }

function ToolApprovalSubstance({ item }: { item: Row }) {
  const args = object(item.arguments);
  return <div className="approval-substance">
    <div className="approval-substance-grid">
      <span><small>Action</small><strong>{titleCase(item.capability_id || item.capability || "Workspace action")}</strong></span>
      <span><small>Requested by</small><strong>{text(item.requested_by_principal_id, text(item.principal_id, "Operly"))}</strong></span>
      <span><small>Status</small><strong>{titleCase(item.status)}</strong></span>
      <span><small>Approval ID</small><strong>{text(item.id)}</strong></span>
    </div>
    <p className="approval-rationale">This is the exact action waiting at the deterministic Workspace approval boundary.</p>
    <details><summary>What will be sent</summary><code>{JSON.stringify(args, null, 2)}</code></details>
  </div>;
}

export function ActivityPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [data, setData] = useState<ActivityData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [decisionBusy, setDecisionBusy] = useState<string | null>(null);
  const [runId, setRunId] = useState("");
  const [runBusy, setRunBusy] = useState(false);
  const [runDetail, setRunDetail] = useState<Row | null>(null);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [messagesResult, tasksResult, toolApprovalsResult, toolEventsResult] = await Promise.allSettled([
        api<Row[]>("/messages"),
        api<Row[]>("/tasks"),
        api<{ approvals: Row[] }>("/workspace-tools/approvals?limit=50"),
        api<{ events: Row[] }>("/workspace-tools/events?limit=80"),
      ]);
      setData({
        messages: messagesResult.status === "fulfilled" ? messagesResult.value : [],
        tasks: tasksResult.status === "fulfilled" ? tasksResult.value : [],
        toolApprovals: toolApprovalsResult.status === "fulfilled" ? toolApprovalsResult.value.approvals || [] : [],
        toolEvents: toolEventsResult.status === "fulfilled" ? toolEventsResult.value.events || [] : [],
      });
      if (toolApprovalsResult.status === "rejected") {
        setError("Workspace tool approvals could not be loaded.");
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load workspace activity");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { void reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function toolApproval(id: string, approved: boolean) {
    setDecisionBusy(id);
    setError(null);
    try {
      await decideCapabilityApproval(id, approved);
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Tool approval decision could not be saved");
    } finally {
      setDecisionBusy(null);
    }
  }

  async function complete(id: string) {
    try {
      await api(`/tasks/${encodeURIComponent(id)}/complete`, { method: "PATCH" });
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Task could not be completed");
    }
  }

  async function inspectRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const clean = runId.trim();
    if (!clean) return;
    setRunBusy(true);
    setRunDetail(null);
    setError(null);
    try {
      setRunDetail(await api<Row>(`/workspace-tools/runs/${encodeURIComponent(clean)}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "That tool run could not be found");
    } finally {
      setRunBusy(false);
    }
  }

  const toolPending = (data?.toolApprovals || []).filter((item) => text(item.status) === "pending");
  const openTasks = (data?.tasks || []).filter((item) => text(item.status) !== "completed");
  const runSteps = Array.isArray(runDetail?.steps) ? runDetail.steps as Row[] : [];

  return <main className="workspace-page">
    <PageHeader eyebrow="Workspace" title="Activity" description="See what Operly is doing, approve important actions, inspect tool history, and open any execution run without leaving the frontend." />
    {loading && <div className="loading-panel">Loading activity…</div>}
    {error && <div className="inline-error page-error">{error}</div>}

    {data && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Needs your OK</span><strong>{toolPending.length}</strong><small>Kernel-governed actions waiting for a human decision</small></article>
        <article className="metric-card"><span>Open tasks</span><strong>{openTasks.length}</strong><small>Work still in progress</small></article>
        <article className="metric-card"><span>Tool events</span><strong>{data.toolEvents.length}</strong><small>Recent deterministic execution history</small></article>
        <article className="metric-card"><span>Messages</span><strong>{data.messages.length}</strong><small>Recent workspace conversations</small></article>
      </section>

      <section className="activity-columns">
        <article className="data-card full-span">
          <div className="card-heading"><div><span className="eyebrow">Human control</span><h2>Workspace tool approvals</h2></div><span>{toolPending.length} pending</span></div>
          {data.toolApprovals.length ? <div className="row-list">{data.toolApprovals.slice(0, 20).map((item) => <div className="data-row stacked approval-row" key={`tool-${text(item.id)}`}>
            <div><Status value={item.status} /><strong>{titleCase(item.capability_id || "Workspace action")}</strong><ToolApprovalSubstance item={item} /></div>
            {text(item.status) === "pending" && <div className="row-actions"><button type="button" disabled={decisionBusy === text(item.id)} onClick={() => void toolApproval(text(item.id), false)}>Don’t allow</button><button type="button" className="primary-button" disabled={decisionBusy === text(item.id)} onClick={() => void toolApproval(text(item.id), true)}>{decisionBusy === text(item.id) ? "Saving…" : "Allow once"}</button></div>}
          </div>)}</div> : <Empty>No Workspace tool approvals yet.</Empty>}
        </article>


        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Execution</span><h2>Tasks</h2></div><span>{openTasks.length} open</span></div>{data.tasks.length ? <div className="row-list">{data.tasks.slice(0, 14).map((item) => <div className="data-row" key={text(item.id)}><div><strong>{text(item.title, "Task")}</strong><small>{item.due_at ? `Due ${when(item.due_at)}` : titleCase(item.status)}</small></div>{text(item.status) !== "completed" ? <button type="button" className="icon-action" onClick={() => void complete(text(item.id))}>✓</button> : <Status value="completed" />}</div>)}</div> : <Empty>No tasks yet.</Empty>}</article>

        <article className="data-card full-span">
          <div className="card-heading"><div><span className="eyebrow">Audit trail</span><h2>Recent tool history</h2></div><span>{data.toolEvents.length}</span></div>
          {data.toolEvents.length ? <div className="row-list">{data.toolEvents.slice(0, 30).map((item) => <div className="data-row stacked" key={text(item.id)}><div><strong>{titleCase(item.event_type || "Tool event")}</strong><small>{item.capability_id ? titleCase(item.capability_id) : "Workspace system"} · {when(item.created_at)}</small><small>{item.actor_id ? `Actor: ${text(item.actor_id)}` : text(item.principal_id)}</small>{item.payload && <details><summary>See event details</summary><code>{JSON.stringify(item.payload, null, 2)}</code></details>}</div></div>)}</div> : <Empty>No tool events are visible to your role yet.</Empty>}
        </article>

        <article className="data-card full-span">
          <div className="card-heading"><div><span className="eyebrow">Execution inspector</span><h2>Open a tool run</h2></div>{runDetail && <Status value={runDetail.status} />}</div>
          <p>If a tool gives you a run ID, paste it here to see exactly what happened step by step.</p>
          <form className="inline-form" onSubmit={inspectRun}><label>Run ID<input value={runId} onChange={(event) => setRunId(event.target.value)} placeholder="Paste a run ID" /></label><button className="primary-button" disabled={runBusy || !runId.trim()}>{runBusy ? "Opening…" : "Open run"}</button></form>
          {runDetail && <div className="approval-substance" style={{ marginTop: 14 }}><div className="approval-substance-grid"><span><small>Action</small><strong>{titleCase(runDetail.capability_id)}</strong></span><span><small>Status</small><strong>{titleCase(runDetail.status)}</strong></span><span><small>Started</small><strong>{when(runDetail.started_at)}</strong></span><span><small>Finished</small><strong>{when(runDetail.finished_at) || "Still running"}</strong></span></div>{runSteps.length > 0 && <div className="row-list" style={{ marginTop: 12 }}>{runSteps.map((step, index) => <div className="data-row" key={`${text(step.step, String(index))}-${index}`}><div><strong>{titleCase(step.name || `Step ${index + 1}`)}</strong><small>{titleCase(step.status)} · {when(step.created_at)}</small></div></div>)}</div>}<details><summary>See complete run data</summary><code>{JSON.stringify(runDetail, null, 2)}</code></details></div>}
        </article>

        <article className="data-card full-span"><div className="card-heading"><div><span className="eyebrow">Channels</span><h2>Recent messages</h2></div><span>{data.messages.length}</span></div>{data.messages.length ? <div className="row-list">{data.messages.slice(0, 16).map((item) => <div className="message-row" key={text(item.id)}><span className="mini-avatar">{text(item.author_name, "?").slice(0, 1).toUpperCase()}</span><div><strong>{text(item.author_name, "Unknown")}</strong><p>{text(item.content)}</p></div><time>{when(item.created_at)}</time></div>)}</div> : <Empty>No messages yet.</Empty>}</article>
      </section>
    </>}
  </main>;
}

import { useEffect, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const titleCase = (value: unknown) => text(value, "unknown").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
const when = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(raw)); }
  catch { return raw; }
};
const failureMessage = (result: PromiseRejectedResult, fallback: string) => result.reason instanceof Error ? result.reason.message : fallback;

function PageHeader({ eyebrow, title, description }: { eyebrow: string; title: string; description: string }) {
  return <header className="surface-header page-header"><div><span className="eyebrow">{eyebrow}</span><h1>{title}</h1><p>{description}</p></div></header>;
}

function Empty({ children }: { children: React.ReactNode }) { return <div className="empty-panel">{children}</div>; }
function Status({ value }: { value: unknown }) { return <span className={`status-chip status-${text(value).toLowerCase().replaceAll("_", "-")}`}>{titleCase(value)}</span>; }

function ApprovalSubstance({ item }: { item: Row }) {
  const details = object(item.details);
  return <div className="approval-substance">
    <div className="approval-substance-grid">
      <span><small>Objective</small><strong>{text(details.objective, text(item.action, "Action"))}</strong></span>
      <span><small>Expected outcome</small><strong>{text(details.expected_outcome, "Complete the requested action")}</strong></span>
      <span><small>Risk</small><strong>{text(details.risk_level, "Review required")}</strong></span>
      <span><small>Capability</small><strong>{text(details.capability, text(item.action, "Action"))}</strong></span>
    </div>
    {details.rationale && <p className="approval-rationale"><strong>Why Operly wants to do this:</strong> {text(details.rationale)}</p>}
    <details open>
      <summary>Full action payload</summary>
      <code>{JSON.stringify(details, null, 2)}</code>
    </details>
  </div>;
}

export function ActivityPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [data, setData] = useState<{ messages: Row[]; tasks: Row[]; approvals: Row[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [decisionBusy, setDecisionBusy] = useState<string | null>(null);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [messagesResult, tasksResult, approvalsResult] = await Promise.allSettled([
        api<Row[]>("/messages"),
        api<Row[]>("/tasks"),
        api<Row[]>("/approvals"),
      ]);
      setData({
        messages: messagesResult.status === "fulfilled" ? messagesResult.value : [],
        tasks: tasksResult.status === "fulfilled" ? tasksResult.value : [],
        approvals: approvalsResult.status === "fulfilled" ? approvalsResult.value : [],
      });
      if (approvalsResult.status === "rejected") {
        setError(`Approvals could not be loaded: ${failureMessage(approvalsResult, "approval service unavailable")}`);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load workspace activity");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function approval(id: string, status: "approved" | "rejected") {
    setDecisionBusy(id);
    setError(null);
    try {
      await api(`/approvals/${encodeURIComponent(id)}`, { method: "PATCH", body: JSON.stringify({ status }) });
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approval decision could not be saved");
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

  const pending = (data?.approvals || []).filter((item) => text(item.status) === "pending");
  const openTasks = (data?.tasks || []).filter((item) => text(item.status) !== "completed");

  return <main className="workspace-page">
    <PageHeader eyebrow="Workspace" title="Activity" description="Decisions, work, and recent conversations in one auditable surface. Approval cards show the exact substance Operly is asking you to authorize." />
    {loading && <div className="loading-panel">Loading activity…</div>}
    {error && <div className="inline-error page-error">{error}</div>}
    {data && <section className="activity-columns">
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Human control</span><h2>Approvals</h2></div><span>{pending.length} pending</span></div>{data.approvals.length ? <div className="row-list">{data.approvals.slice(0, 12).map((item) => <div className="data-row stacked approval-row" key={text(item.id)}><div><Status value={item.status} /><strong>{text(item.action, "Action")}</strong><ApprovalSubstance item={item} /></div>{text(item.status) === "pending" && <div className="row-actions"><button type="button" disabled={decisionBusy === text(item.id)} onClick={() => approval(text(item.id), "rejected")}>Reject</button><button type="button" className="primary-button" disabled={decisionBusy === text(item.id)} onClick={() => approval(text(item.id), "approved")}>{decisionBusy === text(item.id) ? "Working…" : "Approve"}</button></div>}</div>)}</div> : <Empty>No approvals yet.</Empty>}</article>
      <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Execution</span><h2>Tasks</h2></div><span>{openTasks.length} open</span></div>{data.tasks.length ? <div className="row-list">{data.tasks.slice(0, 14).map((item) => <div className="data-row" key={text(item.id)}><div><strong>{text(item.title, "Task")}</strong><small>{item.due_at ? `Due ${when(item.due_at)}` : titleCase(item.status)}</small></div>{text(item.status) !== "completed" ? <button type="button" className="icon-action" onClick={() => complete(text(item.id))}>✓</button> : <Status value="completed" />}</div>)}</div> : <Empty>No tasks yet.</Empty>}</article>
      <article className="data-card full-span"><div className="card-heading"><div><span className="eyebrow">Channels</span><h2>Recent messages</h2></div><span>{data.messages.length}</span></div>{data.messages.length ? <div className="row-list">{data.messages.slice(0, 16).map((item) => <div className="message-row" key={text(item.id)}><span className="mini-avatar">{text(item.author_name, "?").slice(0, 1).toUpperCase()}</span><div><strong>{text(item.author_name, "Unknown")}</strong><p>{text(item.content)}</p></div><time>{when(item.created_at)}</time></div>)}</div> : <Empty>No messages yet.</Empty>}</article>
    </section>}
  </main>;
}

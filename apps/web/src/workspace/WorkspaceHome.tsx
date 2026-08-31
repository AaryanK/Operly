import { useEffect, useState } from "react";

import { api } from "../api";
import { navigate, WorkspaceSection, workspacePath } from "../app/routes";
import { WorkspaceSummary } from "../app/types";

type Props = { workspace: WorkspaceSummary };
type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const titleCase = (value: unknown) => text(value, "Action").replaceAll("_", " ").replaceAll(".", " › ").replace(/\b\w/g, (char) => char.toUpperCase());

const destinations: Array<{ section: WorkspaceSection; title: string; description: string }> = [
  { section: "operly", title: "Ask Operly", description: "Work with the workspace AI using this workspace's permissions and connectors." },
  { section: "workflows", title: "Automate work", description: "Build, schedule, run, approve, and trace repeatable workflows without writing code." },
  { section: "capabilities", title: "Use any tool", description: "Open every action currently available to you with guided forms and clear safety checks." },
  { section: "activity", title: "Review activity", description: "See actions, approvals, failures, workflow events, and other work that needs attention." },
  { section: "solutions", title: "Open Solutions", description: "Build and operate software without a second command composer on Home." },
];

export function WorkspaceHome({ workspace }: Props) {
  const [tasks, setTasks] = useState<Row[]>([]);
  const [approvals, setApprovals] = useState<Row[]>([]);
  const [toolApprovals, setToolApprovals] = useState<Row[]>([]);
  const [solutions, setSolutions] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.allSettled([
      api<Row[]>("/tasks"),
      api<Row[]>("/approvals"),
      api<{ approvals: Row[] }>("/workspace-tools/approvals?status=pending&limit=50"),
      api<Row[]>("/solutions"),
    ]).then(([taskResult, approvalResult, toolApprovalResult, solutionResult]) => {
      setTasks(taskResult.status === "fulfilled" ? taskResult.value : []);
      setApprovals(approvalResult.status === "fulfilled" ? approvalResult.value : []);
      setToolApprovals(toolApprovalResult.status === "fulfilled" ? toolApprovalResult.value.approvals || [] : []);
      setSolutions(solutionResult.status === "fulfilled" ? solutionResult.value : []);
      if ([taskResult, approvalResult, toolApprovalResult, solutionResult].every((item) => item.status === "rejected")) setError("Workspace overview is temporarily unavailable.");
    });
  }, [workspace.id]);

  const openTasks = tasks.filter((item) => text(item.status) !== "completed");
  const pendingApprovals = [
    ...toolApprovals.filter((item) => text(item.status) === "pending"),
    ...approvals.filter((item) => text(item.status) === "pending"),
  ];
  const failedSolutions = solutions.filter((item) => text(item.status) === "failed");
  const attention = pendingApprovals.length + openTasks.filter((item) => item.due_at && new Date(text(item.due_at)).getTime() < Date.now()).length + failedSolutions.length;

  return (
    <main className="workspace-page">
      <header className="surface-header workspace-hero page-header">
        <div><span className="eyebrow">Workspace</span><h1>{workspace.name}</h1><p>Home is the simple starting point. Ask Operly, automate repeatable work, use a tool directly, review activity, or open your Solutions.</p></div>
        <button className="primary-button" onClick={() => navigate(workspacePath(workspace.id, "operly"))}>Ask Operly</button>
      </header>
      {error && <div className="inline-error page-error">{error}</div>}
      <section className="metric-grid home-metrics">
        <article className="metric-card"><span>Needs attention</span><strong>{attention}</strong><small>Overdue work, approvals, or failed Solutions</small></article>
        <article className="metric-card"><span>Pending approvals</span><strong>{pendingApprovals.length}</strong><small>Human decisions before execution</small></article>
        <article className="metric-card"><span>Open tasks</span><strong>{openTasks.length}</strong><small>Work still in progress</small></article>
        <article className="metric-card"><span>Solutions</span><strong>{solutions.length}</strong><small>{failedSolutions.length ? `${failedSolutions.length} need review` : "No known failed Solution"}</small></article>
      </section>
      <section className="home-grid" aria-label="Workspace destinations">
        {destinations.map((item) => <button key={item.section} className="destination-card" onClick={() => navigate(workspacePath(workspace.id, item.section))}><span>→</span><strong>{item.title}</strong><p>{item.description}</p></button>)}
      </section>
      <section className="content-grid two-column home-detail-grid">
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Now</span><h2>Attention queue</h2></div><button className="text-button" onClick={() => navigate(workspacePath(workspace.id, "activity"))}>Open Activity</button></div>{pendingApprovals.length || openTasks.length ? <div className="row-list">{pendingApprovals.slice(0, 4).map((item) => <div className="data-row" key={`a-${text(item.id)}`}><div><strong>{item.capability_id ? titleCase(item.capability_id) : text(item.action, "Approval")}</strong><small>Waiting for your decision</small></div><span className="status-chip status-pending">Pending</span></div>)}{openTasks.slice(0, 5).map((item) => <div className="data-row" key={`t-${text(item.id)}`}><div><strong>{text(item.title, "Task")}</strong><small>{item.due_at ? `Due ${new Date(text(item.due_at)).toLocaleDateString()}` : "In progress"}</small></div></div>)}</div> : <div className="empty-panel">Nothing currently needs attention.</div>}</article>
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Recent</span><h2>Solutions</h2></div><button className="text-button" onClick={() => navigate(workspacePath(workspace.id, "solutions"))}>Open Solutions</button></div>{solutions.length ? <div className="row-list">{solutions.slice(0, 8).map((item) => <div className="data-row" key={text(item.id)}><div><strong>{text(item.name, "Untitled Solution")}</strong><small>{text(item.solution_type, "solution").replaceAll("_", " ")}</small></div><span className={`status-chip status-${text(item.status, "unknown").replaceAll("_", "-")}`}>{text(item.status, "unknown").replaceAll("_", " ")}</span></div>)}</div> : <div className="empty-panel">No Solutions yet.</div>}</article>
      </section>
    </main>
  );
}

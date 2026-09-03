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
  { section: "solutions", title: "Build & publish", description: "Create and operate websites, business apps, and other digital Solutions." },
];

const quickActions: Array<{ section: WorkspaceSection; title: string; description: string }> = [
  { section: "crm", title: "Add or review customers", description: "Open contacts, leads, quotes, and orders." },
  { section: "connections", title: "Connect business tools", description: "Bring Gmail, Calendar, Canva, and Discord into this workspace." },
  { section: "workflows", title: "Automate a routine", description: "Turn a repeatable process into a traceable workflow." },
  { section: "members", title: "Invite your team", description: "Add people and control exactly what each role can do." },
  { section: "presence", title: "Manage online presence", description: "Work on the business website and public presence." },
  { section: "capabilities", title: "Browse all actions", description: "Use the full governed tool catalog directly." },
];

const askPrompts = [
  "What needs my attention right now?",
  "Summarize my sales pipeline and tell me which leads need follow-up.",
  "Show me unpaid or overdue invoices and prioritize the follow-ups.",
  "Show low-stock products and anything that could block upcoming orders.",
];

export function WorkspaceHome({ workspace }: Props) {
  const [tasks, setTasks] = useState<Row[]>([]);
  const [approvals, setApprovals] = useState<Row[]>([]);
  const [toolApprovals, setToolApprovals] = useState<Row[]>([]);
  const [solutions, setSolutions] = useState<Row[]>([]);
  const [contacts, setContacts] = useState<Row[]>([]);
  const [connectors, setConnectors] = useState<Row[]>([]);
  const [members, setMembers] = useState<Row[]>([]);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    Promise.allSettled([
      api<Row[]>("/tasks"),
      api<Row[]>("/approvals"),
      api<{ approvals: Row[] }>("/workspace-tools/approvals?status=pending&limit=50"),
      api<Row[]>("/solutions"),
      api<Row[]>("/business/contacts"),
      api<Row[]>("/connectors"),
      api<Row[]>("/workspace/members"),
    ]).then(([taskResult, approvalResult, toolApprovalResult, solutionResult, contactResult, connectorResult, memberResult]) => {
      setTasks(taskResult.status === "fulfilled" ? taskResult.value : []);
      setApprovals(approvalResult.status === "fulfilled" ? approvalResult.value : []);
      setToolApprovals(toolApprovalResult.status === "fulfilled" ? toolApprovalResult.value.approvals || [] : []);
      setSolutions(solutionResult.status === "fulfilled" ? solutionResult.value : []);
      setContacts(contactResult.status === "fulfilled" ? contactResult.value : []);
      setConnectors(connectorResult.status === "fulfilled" ? connectorResult.value : []);
      setMembers(memberResult.status === "fulfilled" ? memberResult.value : []);
      if ([taskResult, approvalResult, toolApprovalResult, solutionResult].every((item) => item.status === "rejected")) setError("Workspace overview is temporarily unavailable.");
      else setError(null);
    });
  }, [workspace.id]);

  const openTasks = tasks.filter((item) => text(item.status) !== "completed");
  const pendingApprovals = [
    ...toolApprovals.filter((item) => text(item.status) === "pending"),
    ...approvals.filter((item) => text(item.status) === "pending"),
  ];
  const failedSolutions = solutions.filter((item) => text(item.status) === "failed");
  const attention = pendingApprovals.length + openTasks.filter((item) => item.due_at && new Date(text(item.due_at)).getTime() < Date.now()).length + failedSolutions.length;
  const connectedTools = connectors.filter((item) => {
    const status = text(item.status).toLowerCase();
    return item.enabled !== false && !["disconnected", "disabled", "failed", "error"].includes(status);
  }).length;

  const setupSteps: Array<{ done: boolean; section: WorkspaceSection; title: string; description: string; action: string }> = [
    { done: contacts.length > 0, section: "crm", title: "Add your first customer", description: "Give Operly a real customer record to organize sales and follow-up around.", action: contacts.length > 0 ? "Open CRM" : "Add customer" },
    { done: connectedTools > 0, section: "connections", title: "Connect a business account", description: "Connect Google, Canva, or Discord so your tools live inside the workspace.", action: connectedTools > 0 ? "Manage connections" : "Connect tools" },
    { done: members.length > 1, section: "members", title: "Bring in your team", description: "Invite a teammate and give them only the permissions they need.", action: members.length > 1 ? "Manage team" : "Invite teammate" },
  ];
  const setupDone = setupSteps.filter((item) => item.done).length;
  const setupPercent = Math.round((setupDone / setupSteps.length) * 100);
  const firstIncomplete = setupSteps.find((item) => !item.done);
  const recommendation = pendingApprovals.length
    ? { section: "activity" as WorkspaceSection, title: "Review pending approvals", description: `${pendingApprovals.length} action${pendingApprovals.length === 1 ? " is" : "s are"} waiting for a human decision.` }
    : firstIncomplete
      ? { section: firstIncomplete.section, title: firstIncomplete.title, description: firstIncomplete.description }
      : openTasks.length
        ? { section: "activity" as WorkspaceSection, title: "Work through open tasks", description: `${openTasks.length} task${openTasks.length === 1 ? " is" : "s are"} still in progress.` }
        : { section: "workflows" as WorkspaceSection, title: "Automate something repetitive", description: "Your basics are in place. Turn one routine into a workflow and stop doing it manually." };

  function askOperly(prompt: string) {
    navigate(`${workspacePath(workspace.id, "operly")}?prompt=${encodeURIComponent(prompt)}`);
  }

  return (
    <main className="workspace-page">
      <header className="surface-header workspace-hero page-header">
        <div><span className="eyebrow">Workspace</span><h1>{workspace.name}</h1><p>Run the business from one place. Start with what needs attention, connect the tools you already use, then automate the repetitive work.</p></div>
        <button className="primary-button" onClick={() => navigate(workspacePath(workspace.id, "operly"))}>Ask Operly</button>
      </header>
      {error && <div className="inline-error page-error">{error}</div>}

      <section className="metric-grid home-metrics">
        <article className="metric-card"><span>Needs attention</span><strong>{attention}</strong><small>Overdue work, approvals, or failed Solutions</small></article>
        <article className="metric-card"><span>Pending approvals</span><strong>{pendingApprovals.length}</strong><small>Human decisions before execution</small></article>
        <article className="metric-card"><span>Open tasks</span><strong>{openTasks.length}</strong><small>Work still in progress</small></article>
        <article className="metric-card"><span>Business setup</span><strong>{setupPercent}%</strong><small>{setupDone} of {setupSteps.length} essentials complete</small></article>
      </section>

      <section className="content-grid two-column home-detail-grid">
        <article className="data-card">
          <div className="card-heading"><div><span className="eyebrow">Start here</span><h2>Set up the workspace</h2></div><span>{setupDone}/{setupSteps.length}</span></div>
          <p>Three quick steps make this workspace immediately more useful for a small business.</p>
          <div className="row-list">
            {setupSteps.map((item) => <div className="data-row" key={item.title}><div><strong>{item.done ? "✓ " : ""}{item.title}</strong><small>{item.description}</small></div><button className="text-button" onClick={() => navigate(workspacePath(workspace.id, item.section))}>{item.action}</button></div>)}
          </div>
        </article>
        <article className="data-card">
          <div className="card-heading"><div><span className="eyebrow">Recommended next</span><h2>{recommendation.title}</h2></div><span className="status-chip status-pending">Next</span></div>
          <p>{recommendation.description}</p>
          <button className="primary-button" onClick={() => navigate(workspacePath(workspace.id, recommendation.section))}>Continue →</button>
        </article>
      </section>

      <section className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Quick actions</span><h2>Common business jobs</h2></div><small>Go straight to the work</small></div>
        <div className="home-grid" aria-label="Common business actions">
          {quickActions.map((item) => <button key={item.title} className="destination-card" onClick={() => navigate(workspacePath(workspace.id, item.section))}><span>→</span><strong>{item.title}</strong><p>{item.description}</p></button>)}
        </div>
      </section>

      <section className="data-card">
        <div className="card-heading"><div><span className="eyebrow">Ask Operly</span><h2>Start from a business question</h2></div><small>Opens with the prompt ready to edit</small></div>
        <div className="home-grid" aria-label="Suggested Operly questions">
          {askPrompts.map((prompt) => <button key={prompt} className="destination-card" onClick={() => askOperly(prompt)}><span>✦</span><strong>{prompt}</strong><p>Review or edit the request before sending.</p></button>)}
        </div>
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

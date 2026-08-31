import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
type ActionStatus = {
  id: "inspect" | "deploy" | "rollback" | "domain";
  capability_id: string;
  available: boolean;
  risk: string | null;
  approval_required: boolean | null;
};
type ComputerStatus = {
  enabled: boolean;
  planner: string;
  ai_enabled: boolean;
  shell_access: boolean;
  browser_credentials: boolean;
  actions: ActionStatus[];
};
type Project = {
  id: string;
  name: string;
  description: string;
  state: string;
  active_source_version_id?: string | null;
  source_version?: number | null;
  runtime_profile?: string | null;
  deployability?: { deployable?: boolean; reason?: string; file_count?: number; size_bytes?: number };
  solution?: { id: string; name: string; production_state?: string; production_url?: string | null } | null;
  deployment?: { id: string; status: string; health_state: string; public_url?: string | null; previous_deployment_id?: string | null } | null;
};
type Step = {
  id: string;
  sequence: number;
  kind: string;
  status: string;
  capability_id?: string | null;
  run_id?: string | null;
  approval_id?: string | null;
  summary: string;
  payload?: Row;
  created_at?: string | null;
};
type ComputerSession = {
  id: string;
  title: string;
  objective: string;
  action: string;
  state: string;
  project_id?: string | null;
  solution_id?: string | null;
  arguments: Row;
  result: Row;
  current_capability_id?: string | null;
  current_request_id?: string | null;
  current_run_id?: string | null;
  approval_id?: string | null;
  error?: string | null;
  created_at?: string | null;
  updated_at?: string | null;
  completed_at?: string | null;
  steps?: Step[];
};

const actionLabels: Record<string, string> = {
  inspect: "Inspect project",
  deploy: "Deploy to Operly Hosting",
  rollback: "Roll back deployment",
  domain: "Request custom domain",
};

const text = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : value == null ? fallback : String(value);

const object = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value) ? (value as Row) : {};

const formatTime = (value?: string | null) => {
  if (!value) return "";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(date);
};

const statusClass = (state: string) =>
  ["completed", "active", "healthy", "online"].includes(state)
    ? "computer-state-success"
    : ["failed", "unhealthy"].includes(state)
      ? "computer-state-failed"
      : state === "waiting_for_approval"
        ? "computer-state-waiting"
        : "";

export function AgentComputerPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [status, setStatus] = useState<ComputerStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<ComputerSession[]>([]);
  const [active, setActive] = useState<ComputerSession | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [action, setAction] = useState<"inspect" | "deploy" | "rollback" | "domain">("deploy");
  const [objective, setObjective] = useState("");
  const [domain, setDomain] = useState("");
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  );
  const actionStatus = status?.actions.find((item) => item.id === action);

  async function reload() {
    setLoading(true);
    setError(null);
    try {
      const [computer, catalog, recent] = await Promise.all([
        api<ComputerStatus>("/agent-computer/status"),
        api<{ projects: Project[] }>("/agent-computer/catalog"),
        api<{ sessions: ComputerSession[] }>("/agent-computer/sessions"),
      ]);
      setStatus(computer);
      setProjects(catalog.projects || []);
      setSessions(recent.sessions || []);
      setSelectedProjectId((current) => current || catalog.projects?.[0]?.id || "");
      if (active) {
        const refreshed = await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(active.id)}`);
        setActive(refreshed);
      }
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load Agent Computer");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    void reload();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [workspace.id]);

  async function openSession(sessionId: string) {
    setBusy(true);
    setError(null);
    try {
      setActive(await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(sessionId)}`));
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open Agent Computer session");
    } finally {
      setBusy(false);
    }
  }

  async function createAndRun(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedProject) return;
    setBusy(true);
    setError(null);
    try {
      const payload: Row = {
        action,
        project_id: selectedProject.id,
        objective: objective.trim() || `${actionLabels[action]} for ${selectedProject.name}`,
      };
      if (selectedProject.solution?.id) payload.solution_id = selectedProject.solution.id;
      if (action === "domain") payload.domain = domain.trim();
      if (action === "deploy") payload.solution_name = selectedProject.name;
      const session = await api<ComputerSession>("/agent-computer/sessions", {
        method: "POST",
        body: JSON.stringify(payload),
      });
      const running = await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(session.id)}/run`, {
        method: "POST",
      });
      setActive(running);
      setObjective("");
      await refreshLists();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Agent Computer task could not start");
    } finally {
      setBusy(false);
    }
  }

  async function refreshLists() {
    const [catalog, recent] = await Promise.all([
      api<{ projects: Project[] }>("/agent-computer/catalog"),
      api<{ sessions: ComputerSession[] }>("/agent-computer/sessions"),
    ]);
    setProjects(catalog.projects || []);
    setSessions(recent.sessions || []);
  }

  async function approveAndContinue() {
    if (!active?.approval_id) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/workspace-tools/approvals/${encodeURIComponent(active.approval_id)}/decision`, {
        method: "POST",
        body: JSON.stringify({ approved: true }),
      });
      const resumed = await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(active.id)}/resume`, {
        method: "POST",
      });
      setActive(resumed);
      await refreshLists();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Approved Agent Computer task could not continue");
    } finally {
      setBusy(false);
    }
  }

  async function cancelSession() {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      const cancelled = await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(active.id)}/cancel`, {
        method: "POST",
      });
      setActive(cancelled);
      await refreshLists();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not cancel Agent Computer session");
    } finally {
      setBusy(false);
    }
  }

  const resultDeployment = object(active?.result?.deployment);
  const resultSolution = object(active?.result?.solution);
  const productionUrl = text(resultDeployment.public_url || resultSolution.production_url || selectedProject?.deployment?.public_url);
  const selectedDeployable = !!selectedProject?.deployability?.deployable;
  const needsExistingSolution = action === "rollback" || action === "domain";
  const canStart = !!selectedProject && !!actionStatus?.available && (!needsExistingSolution || !!selectedProject.solution?.id) && (action !== "deploy" || selectedDeployable) && (action !== "domain" || !!domain.trim());

  return (
    <main className="workspace-page agent-computer-page">
      <header className="surface-header page-header agent-computer-header">
        <div>
          <span className="eyebrow">Workspace execution interface</span>
          <h1>Agent Computer</h1>
          <p>
            Operate Studio deployments through governed Workspace tools. The Computer records every step,
            pauses at approvals, and never receives a shell, provider credentials, or special execution authority.
          </p>
        </div>
        <div className="page-actions">
          <span className="status-chip">AI planner off</span>
          <button type="button" onClick={() => void reload()} disabled={loading || busy}>Refresh</button>
        </div>
      </header>

      {error && <div className="inline-error page-error">{error}</div>}
      {loading ? <div className="loading-panel">Starting Agent Computer…</div> : (
        <div className="agent-computer-layout">
          <aside className="data-card computer-session-rail">
            <div className="card-heading">
              <div><span className="eyebrow">Task memory</span><h2>Sessions</h2></div>
              <span>{sessions.length}</span>
            </div>
            <button type="button" className="computer-new-task" onClick={() => setActive(null)}>＋ New task</button>
            <div className="computer-session-list">
              {sessions.map((session) => (
                <button type="button" key={session.id} className={active?.id === session.id ? "active" : ""} onClick={() => void openSession(session.id)}>
                  <strong>{session.title}</strong>
                  <span>{actionLabels[session.action] || session.action}</span>
                  <small className={statusClass(session.state)}>{session.state.replaceAll("_", " ")} · {formatTime(session.updated_at)}</small>
                </button>
              ))}
              {!sessions.length && <div className="empty-panel">No Computer sessions yet.</div>}
            </div>
          </aside>

          <section className="computer-screen-shell">
            <div className="computer-screen-bar">
              <div className="computer-window-dots"><span /><span /><span /></div>
              <strong>Operly Computer</strong>
              <small>{active ? active.id.slice(0, 8) : "ready"}</small>
            </div>
            <div className="computer-screen">
              {!active ? (
                <form className="computer-task-composer" onSubmit={createAndRun}>
                  <div className="computer-screen-intro">
                    <span className="computer-glyph">⌘</span>
                    <div><span className="eyebrow">Deterministic operator</span><h2>What should this Computer do?</h2><p>Choose a Studio project and an operation. Future AI can choose these same actions; execution authority stays here.</p></div>
                  </div>

                  <label>Studio project
                    <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)} required>
                      {projects.map((project) => <option key={project.id} value={project.id}>{project.name} · {project.state}</option>)}
                    </select>
                  </label>
                  <label>Operation
                    <select value={action} onChange={(event) => setAction(event.target.value as typeof action)}>
                      {status?.actions.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{actionLabels[item.id]}{item.available ? "" : " · unavailable"}</option>)}
                    </select>
                  </label>
                  {action === "domain" && <label>Custom domain<input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="www.example.com" required /></label>}
                  <label>Task note <span>optional</span>
                    <textarea rows={4} value={objective} onChange={(event) => setObjective(event.target.value)} placeholder={`Example: ${actionLabels[action]} and show me the verified result.`} />
                  </label>

                  {selectedProject && <div className="computer-project-preview">
                    <div><strong>{selectedProject.name}</strong><span>{selectedProject.runtime_profile || "unknown runtime"} · source v{selectedProject.source_version ?? "—"}</span></div>
                    <span className={`status-chip ${selectedDeployable ? "status-active" : ""}`}>{selectedDeployable ? "Deployable" : "Needs build"}</span>
                    <p>{selectedProject.deployability?.reason || "No deployability evidence yet."}</p>
                    {selectedProject.solution?.production_url && <a href={selectedProject.solution.production_url} target="_blank" rel="noreferrer">Current production ↗</a>}
                  </div>}

                  <button className="primary-button computer-run-button" disabled={!canStart || busy}>
                    {busy ? "Submitting…" : actionStatus?.approval_required ? "Run until approval" : "Run task"}
                  </button>
                  {action === "deploy" && selectedProject && !selectedDeployable && <small className="computer-guardrail-note">This Computer will not fake a deployment. Commit a static site or prebuilt dist/build output first.</small>}
                  {needsExistingSolution && selectedProject && !selectedProject.solution?.id && <small className="computer-guardrail-note">This operation requires an existing deployed Solution.</small>}
                </form>
              ) : (
                <div className="computer-session-view">
                  <div className="computer-session-head">
                    <div><span className="eyebrow">{actionLabels[active.action] || active.action}</span><h2>{active.objective}</h2></div>
                    <span className={`computer-state ${statusClass(active.state)}`}>{active.state.replaceAll("_", " ")}</span>
                  </div>

                  {active.state === "waiting_for_approval" && <article className="computer-approval-checkpoint">
                    <div><span className="eyebrow">Execution checkpoint</span><h3>Human approval required</h3><p>The exact capability and arguments below are waiting at the normal Workspace approval boundary.</p></div>
                    <dl><div><dt>Capability</dt><dd>{active.current_capability_id}</dd></div><div><dt>Request</dt><dd>{active.current_request_id}</dd></div><div><dt>Approval</dt><dd>{active.approval_id}</dd></div></dl>
                    <details><summary>Exact arguments</summary><pre>{JSON.stringify(active.arguments, null, 2)}</pre></details>
                    <div className="row-actions"><button type="button" onClick={() => void cancelSession()} disabled={busy}>Deny & cancel</button><button type="button" className="primary-button" onClick={() => void approveAndContinue()} disabled={busy}>{busy ? "Continuing…" : "Approve & continue"}</button></div>
                  </article>}

                  {active.state === "completed" && <article className="computer-result-card">
                    <span className="eyebrow">Verified result</span><h3>Task completed</h3>
                    {productionUrl && <a className="primary-button button-link" href={productionUrl} target="_blank" rel="noreferrer">Open production ↗</a>}
                    <details><summary>Capability result</summary><pre>{JSON.stringify(active.result, null, 2)}</pre></details>
                  </article>}

                  {active.state === "failed" && <article className="computer-result-card computer-result-failed"><span className="eyebrow">Execution stopped</span><h3>{active.error || "The governed operation failed."}</h3><p>Nothing is reported as deployed unless the provider returned a validated successful result.</p></article>}

                  <section className="computer-timeline">
                    <div className="card-heading"><div><span className="eyebrow">Execution trace</span><h3>Computer timeline</h3></div><span>{active.steps?.length || 0} steps</span></div>
                    {(active.steps || []).map((step) => <article key={step.id} className="computer-timeline-step"><span className={`computer-step-dot ${statusClass(step.status)}`} /><div><div><strong>{step.kind.replaceAll("_", " ")}</strong><small>{step.status.replaceAll("_", " ")} · {formatTime(step.created_at)}</small></div><p>{step.summary}</p>{step.capability_id && <code>{step.capability_id}</code>}{step.run_id && <small>Run {step.run_id}</small>}</div></article>)}
                  </section>

                  <div className="row-actions computer-session-actions"><button type="button" onClick={() => setActive(null)}>New task</button>{!(["completed", "failed", "cancelled"].includes(active.state)) && <button type="button" onClick={() => void cancelSession()} disabled={busy}>Cancel session</button>}</div>
                </div>
              )}
            </div>
          </section>

          <aside className="data-card computer-authority-panel">
            <div className="card-heading"><div><span className="eyebrow">Live authority</span><h2>Computer access</h2></div><span className="status-chip status-active">Governed</span></div>
            <div className="computer-security-grid">
              <span><small>Planner</small><strong>{status?.planner || "—"}</strong></span>
              <span><small>AI</small><strong>{status?.ai_enabled ? "On" : "Off"}</strong></span>
              <span><small>Shell</small><strong>{status?.shell_access ? "Allowed" : "None"}</strong></span>
              <span><small>Credentials</small><strong>{status?.browser_credentials ? "Direct" : "None"}</strong></span>
            </div>
            <h3>Studio capabilities</h3>
            <div className="computer-capability-list">
              {status?.actions.map((item) => <div key={item.id}><span className={item.available ? "computer-capability-ready" : "computer-capability-off"} /><div><strong>{actionLabels[item.id]}</strong><small>{item.capability_id}</small><small>{item.available ? `${item.risk || "read"}${item.approval_required ? " · approval" : ""}` : "Unavailable for current authority/runtime"}</small></div></div>)}
            </div>
            <div className="computer-boundary-note"><strong>Execution boundary</strong><p>Computer → Workspace capability → permission policy → approval/idempotency → Studio provider → validated result.</p></div>
          </aside>
        </div>
      )}
    </main>
  );
}

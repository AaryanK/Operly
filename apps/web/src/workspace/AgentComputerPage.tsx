import { FormEvent, useEffect, useMemo, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
type ToolStatus = {
  id: string;
  display_name: string;
  description: string;
  available: boolean;
  risk: string;
  approval_required: boolean;
  tags: string[];
};
type PresetStatus = {
  id: "inspect" | "deploy" | "rollback" | "domain";
  capability_id: string;
  available: boolean;
  risk: string | null;
  approval_required: boolean | null;
};
type ComputerStatus = {
  enabled: boolean;
  role: string;
  planner: string;
  ai_enabled: boolean;
  runner_configured: boolean;
  isolation_required: string;
  direct_operly_secrets: boolean;
  workspace_authority_bypass: boolean;
  native_tool_count: number;
  native_tools_available: number;
  native_tools: ToolStatus[];
  presets: PresetStatus[];
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
type ComputerRuntime = {
  session_id?: string | null;
  state: string;
  profile: string;
  network_policy: string;
  started_at?: string | null;
  updated_at?: string | null;
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
  runtime: ComputerRuntime;
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

type ComputerAction = "general" | "inspect" | "deploy" | "rollback" | "domain";

const actionLabels: Record<string, string> = {
  general: "General agent computer",
  inspect: "Inspect Studio project",
  deploy: "Deploy to Operly Hosting",
  rollback: "Roll back deployment",
  domain: "Request custom domain",
};

const toolExamples: Record<string, Row> = {
  "computer.runtime.status": {},
  "computer.terminal.exec": { command: "pwd && python3 --version && git --version" },
  "computer.python.exec": { code: "import sys\nprint(sys.version)\nprint('hello from Operly Computer')" },
  "computer.files.list": { path: ".", recursive: false, max_entries: 100 },
  "computer.files.read": { path: "README.md", max_bytes: 200000 },
  "computer.files.write": { path: "notes/example.txt", content: "Created inside Agent Computer\n", append: false },
  "computer.files.mkdir": { path: "work" },
  "computer.files.remove": { path: "work", recursive: true },
  "computer.files.move": { source: "notes/example.txt", destination: "notes/moved.txt" },
  "computer.files.search": { query: "TODO", path: ".", glob: "*", max_matches: 100 },
  "computer.process.list": {},
  "computer.process.kill": { process_id: "PROCESS_ID", signal: "TERM" },
  "computer.git.status": { cwd: "." },
  "computer.git.diff": { cwd: ".", staged: false },
  "computer.git.exec": { cwd: ".", args: ["log", "--oneline", "-10"] },
  "computer.web.fetch": { url: "https://example.com", method: "GET", max_bytes: 500000 },
  "computer.web.download": { url: "https://example.com", destination: "downloads/example.html", max_bytes: 5000000 },
  "computer.browser.open": { viewport_width: 1440, viewport_height: 900 },
  "computer.browser.navigate": { url: "https://example.com", wait_until: "domcontentloaded", timeout_seconds: 60 },
  "computer.browser.snapshot": { max_chars: 40000 },
  "computer.browser.click": { selector: "text=More information", timeout_seconds: 30 },
  "computer.browser.type": { selector: "input", text: "hello", press_enter: false, timeout_seconds: 30 },
  "computer.browser.press": { key: "Enter" },
  "computer.browser.evaluate": { expression: "() => ({title: document.title, url: location.href})" },
  "computer.browser.screenshot": { path: "screenshots/page.png", full_page: true },
  "computer.browser.close": {},
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
  ["completed", "active", "healthy", "online", "ready"].includes(state)
    ? "computer-state-success"
    : ["failed", "unhealthy", "unavailable", "teardown_unconfirmed"].includes(state)
      ? "computer-state-failed"
      : state === "waiting_for_approval"
        ? "computer-state-waiting"
        : "";

const toolGroup = (tool: ToolStatus) => {
  if (tool.id.startsWith("computer.python")) return "Python";
  if (tool.id.startsWith("computer.terminal")) return "Terminal";
  if (tool.id.startsWith("computer.files")) return "Files";
  if (tool.id.startsWith("computer.process")) return "Processes";
  if (tool.id.startsWith("computer.git")) return "Git";
  if (tool.id.startsWith("computer.web")) return "Web";
  if (tool.id.startsWith("computer.browser")) return "Browser";
  return "Runtime";
};

export function AgentComputerPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [status, setStatus] = useState<ComputerStatus | null>(null);
  const [projects, setProjects] = useState<Project[]>([]);
  const [sessions, setSessions] = useState<ComputerSession[]>([]);
  const [active, setActive] = useState<ComputerSession | null>(null);
  const [selectedProjectId, setSelectedProjectId] = useState("");
  const [action, setAction] = useState<ComputerAction>("general");
  const [objective, setObjective] = useState("");
  const [domain, setDomain] = useState("");
  const [profile, setProfile] = useState<"general" | "coding" | "data" | "browser">("coding");
  const [networkPolicy, setNetworkPolicy] = useState<"off" | "web" | "full">("web");
  const [toolId, setToolId] = useState("computer.python.exec");
  const [toolArguments, setToolArguments] = useState(JSON.stringify(toolExamples["computer.python.exec"], null, 2));
  const [toolResult, setToolResult] = useState<Row | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const selectedProject = useMemo(
    () => projects.find((project) => project.id === selectedProjectId) || null,
    [projects, selectedProjectId],
  );
  const presetStatus = status?.presets.find((item) => item.id === action);
  const nativeTool = status?.native_tools.find((item) => item.id === toolId);
  const availableNativeTools = status?.native_tools.filter((item) => item.available && !item.id.startsWith("computer.runtime.")) || [];

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
      const firstNative = computer.native_tools?.find((item) => item.available && item.id === "computer.python.exec")
        || computer.native_tools?.find((item) => item.available && !item.id.startsWith("computer.runtime."));
      if (firstNative && !computer.native_tools.some((item) => item.id === toolId && item.available)) {
        setToolId(firstNative.id);
        setToolArguments(JSON.stringify(toolExamples[firstNative.id] || {}, null, 2));
      }
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
    setToolResult(null);
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
    if (action !== "general" && !selectedProject) return;
    setBusy(true);
    setError(null);
    setToolResult(null);
    try {
      const payload: Row = {
        action,
        objective: objective.trim() || (action === "general" ? "General agent computer workspace" : `${actionLabels[action]} for ${selectedProject?.name || "Studio project"}`),
        runtime_profile: profile,
        network_policy: networkPolicy,
      };
      if (selectedProject) {
        payload.project_id = selectedProject.id;
        if (selectedProject.solution?.id) payload.solution_id = selectedProject.solution.id;
        if (action === "deploy") payload.solution_name = selectedProject.name;
      }
      if (action === "domain") payload.domain = domain.trim();
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

  async function refreshActive() {
    if (!active) return;
    const refreshed = await api<ComputerSession>(`/agent-computer/sessions/${encodeURIComponent(active.id)}`);
    setActive(refreshed);
    await refreshLists();
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

  async function ensureRuntime() {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/agent-computer/sessions/${encodeURIComponent(active.id)}/runtime/start`, {
        method: "POST",
        body: JSON.stringify({ profile: active.runtime.profile || profile, network_policy: active.runtime.network_policy || networkPolicy, ttl_seconds: 7200 }),
      });
      await refreshActive();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not start the Computer runtime");
    } finally {
      setBusy(false);
    }
  }

  async function stopRuntime() {
    if (!active) return;
    setBusy(true);
    setError(null);
    try {
      await api(`/agent-computer/sessions/${encodeURIComponent(active.id)}/runtime/stop`, { method: "POST" });
      await refreshActive();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not stop the Computer runtime");
    } finally {
      setBusy(false);
    }
  }

  async function executeNativeTool(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!active || !nativeTool?.available) return;
    setBusy(true);
    setError(null);
    setToolResult(null);
    try {
      let args: Row;
      try {
        const parsed = JSON.parse(toolArguments || "{}");
        if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("Arguments must be a JSON object");
        args = parsed as Row;
      } catch (caught) {
        throw new Error(caught instanceof Error ? caught.message : "Tool arguments must be valid JSON");
      }
      const response = await api<Row>(`/agent-computer/sessions/${encodeURIComponent(active.id)}/tools/${encodeURIComponent(toolId)}/execute`, {
        method: "POST",
        body: JSON.stringify({ arguments: args, goal: `Human-debug execution of ${toolId}` }),
      });
      setToolResult(response);
      await refreshActive();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Computer tool execution failed");
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

  function chooseTool(next: string) {
    setToolId(next);
    setToolArguments(JSON.stringify(toolExamples[next] || {}, null, 2));
    setToolResult(null);
  }

  const resultDeployment = object(active?.result?.deployment);
  const resultSolution = object(active?.result?.solution);
  const productionUrl = text(resultDeployment.public_url || resultSolution.production_url || selectedProject?.deployment?.public_url);
  const selectedDeployable = !!selectedProject?.deployability?.deployable;
  const needsExistingSolution = action === "rollback" || action === "domain";
  const presetReady = action === "general" ? !!status?.runner_configured : !!presetStatus?.available;
  const canStart = presetReady
    && (action === "general" || !!selectedProject)
    && (!needsExistingSolution || !!selectedProject?.solution?.id)
    && (action !== "deploy" || selectedDeployable)
    && (action !== "domain" || !!domain.trim());
  const runtimeActive = active?.runtime?.state === "active";

  return (
    <main className="workspace-page agent-computer-page">
      <header className="surface-header page-header agent-computer-header">
        <div>
          <span className="eyebrow">Agent execution environment</span>
          <h1>Agent Computer</h1>
          <p>
            A general isolated computer for agent runs: Python, terminal, files, processes, Git, web and browser.
            Operly business actions remain separate governed Workspace capabilities, so compute access never becomes hidden business authority.
          </p>
        </div>
        <div className="page-actions">
          <span className={`status-chip ${status?.runner_configured ? "status-active" : ""}`}>{status?.runner_configured ? "Runner connected" : "Runner required"}</span>
          <button type="button" onClick={() => void reload()} disabled={loading || busy}>Refresh</button>
        </div>
      </header>

      {error && <div className="inline-error page-error">{error}</div>}
      {loading ? <div className="loading-panel">Starting Agent Computer…</div> : (
        <div className="agent-computer-layout">
          <aside className="data-card computer-session-rail">
            <div className="card-heading">
              <div><span className="eyebrow">Run memory</span><h2>Computer sessions</h2></div>
              <span>{sessions.length}</span>
            </div>
            <button type="button" className="computer-new-task" onClick={() => { setActive(null); setAction("general"); setToolResult(null); }}>＋ New computer</button>
            <div className="computer-session-list">
              {sessions.map((session) => (
                <button type="button" key={session.id} className={active?.id === session.id ? "active" : ""} onClick={() => void openSession(session.id)}>
                  <strong>{session.title}</strong>
                  <span>{actionLabels[session.action] || session.action}</span>
                  <small className={statusClass(session.runtime?.state || session.state)}>{(session.runtime?.state || session.state).replaceAll("_", " ")} · {formatTime(session.updated_at)}</small>
                </button>
              ))}
              {!sessions.length && <div className="empty-panel">No Computer sessions yet.</div>}
            </div>
          </aside>

          <section className="computer-screen-shell">
            <div className="computer-screen-bar">
              <div className="computer-window-dots"><span /><span /><span /></div>
              <strong>Operly Computer</strong>
              <small>{active ? `${active.runtime?.profile || "general"} · ${active.id.slice(0, 8)}` : "ready"}</small>
            </div>
            <div className="computer-screen">
              {!active ? (
                <form className="computer-task-composer" onSubmit={createAndRun}>
                  <div className="computer-screen-intro">
                    <span className="computer-glyph">⌘</span>
                    <div><span className="eyebrow">Isolated agent workspace</span><h2>Start a Computer</h2><p>Use a general sandbox for coding/research/data work, or choose a Studio preset. The agent will use the same native and Workspace tools.</p></div>
                  </div>

                  <label>Mode
                    <select value={action} onChange={(event) => setAction(event.target.value as ComputerAction)}>
                      <option value="general">General agent computer</option>
                      {status?.presets.map((item) => <option key={item.id} value={item.id} disabled={!item.available}>{actionLabels[item.id]}{item.available ? "" : " · unavailable"}</option>)}
                    </select>
                  </label>

                  {action !== "general" && <label>Studio project
                    <select value={selectedProjectId} onChange={(event) => setSelectedProjectId(event.target.value)} required>
                      {projects.map((project) => <option key={project.id} value={project.id}>{project.name} · {project.state}</option>)}
                    </select>
                  </label>}

                  <div className="computer-runtime-options">
                    <label>Runtime profile
                      <select value={profile} onChange={(event) => setProfile(event.target.value as typeof profile)}>
                        <option value="general">General</option><option value="coding">Coding</option><option value="data">Data / Python</option><option value="browser">Browser / research</option>
                      </select>
                    </label>
                    <label>Network
                      <select value={networkPolicy} onChange={(event) => setNetworkPolicy(event.target.value as typeof networkPolicy)}>
                        <option value="off">Off</option><option value="web">Public web</option><option value="full">Full public egress</option>
                      </select>
                    </label>
                  </div>

                  {action === "domain" && <label>Custom domain<input value={domain} onChange={(event) => setDomain(event.target.value)} placeholder="www.example.com" required /></label>}
                  <label>Objective <span>optional for manual/debug runs</span>
                    <textarea rows={4} value={objective} onChange={(event) => setObjective(event.target.value)} placeholder="Example: Inspect this repository, run the tests, fix the failing code and prepare a deployable build." />
                  </label>

                  {action !== "general" && selectedProject && <div className="computer-project-preview">
                    <div><strong>{selectedProject.name}</strong><span>{selectedProject.runtime_profile || "unknown runtime"} · source v{selectedProject.source_version ?? "—"}</span></div>
                    <span className={`status-chip ${selectedDeployable ? "status-active" : ""}`}>{selectedDeployable ? "Deployable" : "Needs build"}</span>
                    <p>{selectedProject.deployability?.reason || "No deployability evidence yet."}</p>
                    {selectedProject.solution?.production_url && <a href={selectedProject.solution.production_url} target="_blank" rel="noreferrer">Current production ↗</a>}
                  </div>}

                  <button className="primary-button computer-run-button" disabled={!canStart || busy}>
                    {busy ? "Starting…" : action === "general" ? "Start Computer" : presetStatus?.approval_required ? "Run until approval" : "Run preset"}
                  </button>
                  {action === "general" && !status?.runner_configured && <small className="computer-guardrail-note">Configure the isolated Computer runner before starting agent compute. Operly will not fall back to the API server shell.</small>}
                  {action === "deploy" && selectedProject && !selectedDeployable && <small className="computer-guardrail-note">Deployment stays fail-closed until the Studio source has a static or verified built artifact.</small>}
                  {needsExistingSolution && selectedProject && !selectedProject.solution?.id && <small className="computer-guardrail-note">This preset requires an existing Solution.</small>}
                </form>
              ) : (
                <div className="computer-session-view">
                  <div className="computer-session-head">
                    <div><span className="eyebrow">{actionLabels[active.action] || active.action}</span><h2>{active.objective}</h2><small>Runtime: {active.runtime.profile} · network {active.runtime.network_policy}</small></div>
                    <span className={`computer-state ${statusClass(active.runtime?.state || active.state)}`}>{(active.runtime?.state || active.state).replaceAll("_", " ")}</span>
                  </div>

                  {active.state === "waiting_for_approval" && <article className="computer-approval-checkpoint">
                    <div><span className="eyebrow">Workspace authority checkpoint</span><h3>Human approval required</h3><p>The exact business capability and arguments are waiting at the normal Workspace approval boundary. Native sandbox compute is a separate boundary.</p></div>
                    <dl><div><dt>Capability</dt><dd>{active.current_capability_id}</dd></div><div><dt>Request</dt><dd>{active.current_request_id}</dd></div><div><dt>Approval</dt><dd>{active.approval_id}</dd></div></dl>
                    <details><summary>Exact arguments</summary><pre>{JSON.stringify(active.arguments, null, 2)}</pre></details>
                    <div className="row-actions"><button type="button" onClick={() => void cancelSession()} disabled={busy}>Deny & cancel</button><button type="button" className="primary-button" onClick={() => void approveAndContinue()} disabled={busy}>{busy ? "Continuing…" : "Approve & continue"}</button></div>
                  </article>}

                  <article className="computer-result-card">
                    <div className="card-heading"><div><span className="eyebrow">Sandbox runtime</span><h3>{runtimeActive ? "Computer is active" : `Computer is ${active.runtime.state || "stopped"}`}</h3></div><span className={`status-chip ${runtimeActive ? "status-active" : ""}`}>{active.runtime.profile}</span></div>
                    <p>{runtimeActive ? "Python, terminal, files, processes, Git, public web and browser tools are available according to the live tool inventory." : "Start an isolated runtime to use native compute tools. Studio/business preset results remain available independently."}</p>
                    <div className="row-actions">
                      {!runtimeActive && <button className="primary-button" type="button" onClick={() => void ensureRuntime()} disabled={busy || !status?.runner_configured}>Start runtime</button>}
                      {runtimeActive && <button type="button" onClick={() => void stopRuntime()} disabled={busy}>Stop runtime</button>}
                      <button type="button" onClick={() => void refreshActive()} disabled={busy}>Refresh state</button>
                    </div>
                  </article>

                  {runtimeActive && <form className="computer-native-tool-console" onSubmit={executeNativeTool}>
                    <div className="card-heading"><div><span className="eyebrow">Native tool console</span><h3>Run the same tools an agent sees</h3></div><span>{availableNativeTools.length} available</span></div>
                    <label>Tool
                      <select value={toolId} onChange={(event) => chooseTool(event.target.value)}>
                        {availableNativeTools.map((tool) => <option key={tool.id} value={tool.id}>{toolGroup(tool)} · {tool.display_name}</option>)}
                      </select>
                    </label>
                    {nativeTool && <p className="computer-tool-description"><code>{nativeTool.id}</code> — {nativeTool.description}</p>}
                    <label>Arguments JSON <span>computer_session_id is injected server-side</span>
                      <textarea className="computer-tool-json" rows={9} value={toolArguments} onChange={(event) => setToolArguments(event.target.value)} spellCheck={false} />
                    </label>
                    <div className="row-actions"><button type="button" onClick={() => setToolArguments(JSON.stringify(toolExamples[toolId] || {}, null, 2))}>Reset example</button><button className="primary-button" disabled={busy || !nativeTool?.available}>{busy ? "Running…" : "Run tool"}</button></div>
                    {toolResult && <details open className="computer-tool-result"><summary>Tool execution result</summary><pre>{JSON.stringify(toolResult, null, 2)}</pre></details>}
                  </form>}

                  {active.state === "completed" && <article className="computer-result-card">
                    <span className="eyebrow">Verified Workspace result</span><h3>Preset completed</h3>
                    {productionUrl && <a className="primary-button button-link" href={productionUrl} target="_blank" rel="noreferrer">Open production ↗</a>}
                    <details><summary>Capability result</summary><pre>{JSON.stringify(active.result, null, 2)}</pre></details>
                  </article>}

                  {active.state === "failed" && <article className="computer-result-card computer-result-failed"><span className="eyebrow">Execution stopped</span><h3>{active.error || "The governed operation failed."}</h3><p>Operly does not report a compute or business action as successful without a validated provider result.</p></article>}

                  <section className="computer-timeline">
                    <div className="card-heading"><div><span className="eyebrow">Execution trace</span><h3>Computer timeline</h3></div><span>{active.steps?.length || 0} steps</span></div>
                    {(active.steps || []).map((step) => <article key={step.id} className="computer-timeline-step"><span className={`computer-step-dot ${statusClass(step.status)}`} /><div><div><strong>{step.kind.replaceAll("_", " ")}</strong><small>{step.status.replaceAll("_", " ")} · {formatTime(step.created_at)}</small></div><p>{step.summary}</p>{step.capability_id && <code>{step.capability_id}</code>}{step.run_id && <small>Run {step.run_id}</small>}</div></article>)}
                  </section>

                  <div className="row-actions computer-session-actions"><button type="button" onClick={() => { setActive(null); setAction("general"); }}>New computer</button>{active.state !== "cancelled" && <button type="button" onClick={() => void cancelSession()} disabled={busy}>Cancel & tear down</button>}</div>
                </div>
              )}
            </div>
          </section>

          <aside className="data-card computer-authority-panel">
            <div className="card-heading"><div><span className="eyebrow">Runtime inventory</span><h2>Agent tools</h2></div><span className={`status-chip ${status?.runner_configured ? "status-active" : ""}`}>{status?.native_tools_available || 0}/{status?.native_tool_count || 0}</span></div>
            <div className="computer-security-grid">
              <span><small>Role</small><strong>Computer</strong></span>
              <span><small>Python</small><strong>{status?.native_tools.some((tool) => tool.id === "computer.python.exec" && tool.available) ? "Ready" : "Offline"}</strong></span>
              <span><small>Terminal</small><strong>{status?.native_tools.some((tool) => tool.id === "computer.terminal.exec" && tool.available) ? "Ready" : "Offline"}</strong></span>
              <span><small>Operly secrets</small><strong>{status?.direct_operly_secrets ? "Injected" : "None"}</strong></span>
            </div>
            <h3>Native runtime groups</h3>
            <div className="computer-capability-list">
              {["Python", "Terminal", "Files", "Processes", "Git", "Web", "Browser", "Runtime"].map((group) => {
                const tools = status?.native_tools.filter((tool) => toolGroup(tool) === group) || [];
                if (!tools.length) return null;
                const ready = tools.filter((tool) => tool.available).length;
                return <div key={group}><span className={ready ? "computer-capability-ready" : "computer-capability-off"} /><div><strong>{group}</strong><small>{ready}/{tools.length} tools available</small><small>{tools.map((tool) => tool.id.replace("computer.", "")).join(" · ")}</small></div></div>;
              })}
            </div>
            <div className="computer-boundary-note"><strong>Runtime boundary</strong><p>Agent → Computer-native tools → isolated sandbox. Business side effects take a separate path: Agent → Workspace capability → permissions/approval → provider.</p></div>
            <div className="computer-boundary-note"><strong>Production isolation</strong><p>{status?.isolation_required || "per-session container or microVM"}. The API server never becomes the agent's shell.</p></div>
          </aside>
        </div>
      )}
    </main>
  );
}

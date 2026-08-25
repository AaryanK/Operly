import { FormEvent, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
type SourceFile = {
  path: string;
  content: string;
  generatedBy?: string;
  sizeBytes?: number;
};
type SourceBundle = Row & {
  files?: SourceFile[];
  fileCount?: number;
  sourceVersion?: number;
  bundleDigest?: string;
  summary?: string;
  runtimeProfile?: string;
  originatingRunId?: string;
  sourceAuthority?: string;
};
type SourceInspectorState = {
  solutionId: string;
  solutionName: string;
  loading: boolean;
  error: string | null;
  bundle: SourceBundle | null;
  selectedPath: string | null;
};

const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const title = (value: unknown) => text(value, "solution").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());
const formatBytes = (value: unknown) => {
  const bytes = Number(value);
  if (!Number.isFinite(bytes) || bytes < 0) return "";
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
};

export function SolutionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [solutions, setSolutions] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const [sourceInspector, setSourceInspector] = useState<SourceInspectorState | null>(null);
  const composeInFlight = useRef(false);

  async function reload() {
    setLoading(true);
    try { setSolutions(await api<Row[]>("/solutions")); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Solutions are unavailable"); }
    finally { setLoading(false); }
  }

  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);
  useEffect(() => {
    if (!sourceInspector) return;
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "Escape") setSourceInspector(null);
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, [sourceInspector]);

  async function compose(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (composeInFlight.current) return;

    const formElement = event.currentTarget;
    const form = new FormData(formElement);
    const objective = text(form.get("objective")).trim();
    const name = text(form.get("name")).trim() || objective.slice(0, 80) || "New Solution";
    if (!objective) return;

    composeInFlight.current = true;
    setCreating(true); setError(null); setResult(null);
    try {
      const response = await api<Row>("/solutions/compose", {
        method: "POST",
        body: JSON.stringify({ name, objective }),
      });
      const responseStatus = text(response.status || object(response.job).status || object(response.solution).status, "created");
      if (["failed", "error"].includes(responseStatus.toLowerCase())) {
        throw new Error(text(response.message || response.error || object(response.job).failure_message, "Solution creation failed and was recorded."));
      }
      setResult(responseStatus === "created" ? "Solution creation started." : `Solution status: ${title(responseStatus)}.`);
      formElement.reset();
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Solution creation could not be verified");
    } finally {
      composeInFlight.current = false;
      setCreating(false);
    }
  }

  async function retryGeneration(solutionId: string) {
    if (!solutionId || retryingId) return;
    setRetryingId(solutionId); setError(null); setResult(null);
    try {
      const response = await api<Row>(`/solutions/${solutionId}/retry-generation`, { method: "POST" });
      const solution = object(response.solution);
      const status = text(solution.status, "building");
      setResult(`Retry queued. Solution status: ${title(status)}.`);
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Solution retry could not be queued");
    } finally {
      setRetryingId(null);
    }
  }

  async function inspectGeneratedSource(solutionId: string, solutionName: string) {
    if (!solutionId) return;
    setSourceInspector({
      solutionId,
      solutionName,
      loading: true,
      error: null,
      bundle: null,
      selectedPath: null,
    });
    try {
      const bundle = await api<SourceBundle>(`/solutions/${solutionId}/source`);
      const files = Array.isArray(bundle.files) ? bundle.files : [];
      setSourceInspector((current) => current?.solutionId === solutionId ? {
        ...current,
        loading: false,
        bundle: { ...bundle, files },
        selectedPath: files[0]?.path || null,
      } : current);
    } catch (caught) {
      setSourceInspector((current) => current?.solutionId === solutionId ? {
        ...current,
        loading: false,
        error: caught instanceof Error ? caught.message : "Persisted source is unavailable",
      } : current);
    }
  }

  const sourceFiles = sourceInspector?.bundle?.files || [];
  const selectedSourceFile = sourceFiles.find((file) => file.path === sourceInspector?.selectedPath) || sourceFiles[0];

  return <>
    <main className="workspace-page">
      <header className="surface-header page-header"><div><span className="eyebrow">Digital presence · software</span><h1>Solutions</h1><p>Describe the business outcome. Operly creates, runs, and reports the resulting Solution without claiming preview or deployment before verification succeeds.</p></div></header>
      <section className="solution-compose-card"><div><span className="eyebrow">Create</span><h2>What should this Solution accomplish?</h2><p>Start with the objective rather than choosing a fake template or implementation stack. Operly can ask for clarification when the requirement genuinely needs it.</p></div><form onSubmit={compose}><label>Solution name <span>optional</span><input name="name" maxLength={200} placeholder="Inventory assistant" /></label><label>Business objective<textarea name="objective" required rows={5} maxLength={12000} placeholder="Build a system that tracks restaurant inventory, warns when ingredients run low, and lets managers approve restocking…" /></label><div className="compose-guidance"><span>1 · Understand</span><span>2 · Plan capabilities</span><span>3 · Build</span><span>4 · Verify</span></div><button className="primary-button" disabled={creating}>{creating ? "Creating…" : "Create Solution"}</button></form>{result && <div className="success-banner">{result}</div>}{error && <div className="inline-error">{error}</div>}</section>
      <section className="solutions-library"><div className="section-heading"><div><span className="eyebrow">Workspace</span><h2>Your Solutions</h2></div><span>{solutions.length}</span></div>{loading ? <div className="loading-panel">Loading Solutions…</div> : solutions.length ? <div className="solution-grid">{solutions.map((solution) => {
        const preview = object(solution.preview);
        const production = object(solution.production);
        const generation = object(solution.generation);
        const runtime = object(solution.runtime);
        const solutionId = text(solution.id);
        const solutionName = text(solution.name, "Untitled Solution");
        const failed = text(solution.status).toLowerCase() === "failed";
        const runtimeKind = text(runtime.kind).toLowerCase();
        const isSoftware = runtimeKind === "software";
        const canInspectSource = isSoftware;
        const canRetryGeneration = failed && isSoftware;
        return <article className="solution-card" key={solutionId}>
          <div className="solution-card-top"><span>{title(solution.solution_type || solution.type || "Solution")}</span><span className={`status-chip status-${text(solution.status, "unknown").replaceAll("_", "-")}`}>{title(solution.status || "unknown")}</span></div>
          <h3>{solutionName}</h3>
          <p>{text(solution.objective || solution.description, "Workspace Solution")}</p>
          <div className="solution-meta"><span>Runtime: {isSoftware ? "AgentRuntime software" : title(runtimeKind || "unknown")}</span><span>Preview: {text(preview.state || (preview.url ? "available" : "not ready"), "not ready")}</span><span>Production: {text(production.state, "not published")}</span>{generation.stage && <span>Generation stage: {title(generation.stage)}</span>}{generation.attempt && <span>Attempt: {text(generation.attempt)}</span>}{generation.sourceVersion && <span>Source version: {text(generation.sourceVersion)}</span>}{generation.deliveryStatus && <span>Source delivery: {title(generation.deliveryStatus)}</span>}</div>
          {failed && generation.error && <div className="inline-error"><strong>Generation failed at {title(generation.stage || "generation")}</strong><div>{text(generation.error)}</div></div>}
          <div className="page-actions">
            {canInspectSource && <button className="secondary-button" type="button" onClick={() => inspectGeneratedSource(solutionId, solutionName)}>View source</button>}
            {canRetryGeneration && <button className="secondary-button" type="button" disabled={Boolean(retryingId)} onClick={() => retryGeneration(solutionId)}>{retryingId === solutionId ? "Retrying…" : "Retry generation"}</button>}
            {text(preview.url) && <a className="secondary-button" href={text(preview.url)} target="_blank" rel="noreferrer">Preview</a>}
            {text(production.url) && <a className="primary-button" href={text(production.url)} target="_blank" rel="noreferrer">Live</a>}
          </div>
        </article>;
      })}</div> : <div className="empty-panel">No Solutions yet. Create one from the business objective above.</div>}</section>
      <section className="info-banner"><strong>Unified software lifecycle</strong><p>Studio and Operly AI converge on the same Workspace-owned software project and immutable backend source. Preview becomes available only after runner verification; source inspection reads durable source rather than disposable sandbox state.</p></section>
    </main>

    {sourceInspector && <div className="source-inspector-backdrop" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) setSourceInspector(null); }}>
      <section className="source-inspector" role="dialog" aria-modal="true" aria-labelledby="source-inspector-title">
        <header className="source-inspector-header">
          <div><span className="eyebrow">{sourceInspector.bundle?.sourceAuthority === "software_source_versions" ? "Authoritative source" : "Generated source"}</span><h2 id="source-inspector-title">{sourceInspector.solutionName}</h2>{sourceInspector.bundle && <p>Source v{text(sourceInspector.bundle.sourceVersion)} · {text(sourceInspector.bundle.fileCount, sourceFiles.length)} files{sourceInspector.bundle.runtimeProfile ? ` · ${sourceInspector.bundle.runtimeProfile}` : ""}</p>}{sourceInspector.bundle?.originatingRunId && <small>AgentRuntime run {sourceInspector.bundle.originatingRunId}</small>}</div>
          <button className="source-inspector-close" type="button" aria-label="Close source inspector" onClick={() => setSourceInspector(null)}>×</button>
        </header>
        {sourceInspector.loading ? <div className="source-inspector-state">Loading the latest persisted source bundle…</div> : sourceInspector.error ? <div className="source-inspector-state inline-error">{sourceInspector.error}</div> : sourceFiles.length ? <div className="source-inspector-body">
          <nav className="source-file-list" aria-label="Source files">
            {sourceFiles.map((file) => <button key={file.path} type="button" className={file.path === selectedSourceFile?.path ? "active" : ""} aria-current={file.path === selectedSourceFile?.path ? "true" : undefined} onClick={() => setSourceInspector((current) => current ? { ...current, selectedPath: file.path } : current)}><span>{file.path}</span><small>{formatBytes(file.sizeBytes)}</small></button>)}
          </nav>
          <div className="source-file-viewer">
            <div className="source-file-toolbar"><strong>{selectedSourceFile?.path}</strong>{selectedSourceFile?.sizeBytes != null && <span>{formatBytes(selectedSourceFile.sizeBytes)}</span>}</div>
            <pre tabIndex={0}><code>{selectedSourceFile?.content || ""}</code></pre>
          </div>
        </div> : <div className="source-inspector-state">This source bundle does not contain any inspectable text files.</div>}
      </section>
    </div>}
  </>;
}

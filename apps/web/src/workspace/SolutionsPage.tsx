import { FormEvent, useEffect, useRef, useState } from "react";

import { api } from "../api";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const title = (value: unknown) => text(value, "solution").replaceAll("_", " ").replace(/\b\w/g, (char) => char.toUpperCase());

export function SolutionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [solutions, setSolutions] = useState<Row[]>([]);
  const [loading, setLoading] = useState(true);
  const [creating, setCreating] = useState(false);
  const [retryingId, setRetryingId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [result, setResult] = useState<string | null>(null);
  const composeInFlight = useRef(false);

  async function reload() {
    setLoading(true);
    try { setSolutions(await api<Row[]>("/solutions")); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Solutions are unavailable"); }
    finally { setLoading(false); }
  }
  useEffect(() => { reload(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

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

  return <main className="workspace-page">
    <header className="surface-header page-header"><div><span className="eyebrow">Digital presence · software</span><h1>Solutions</h1><p>Describe the business outcome. Operly creates, runs, and reports the resulting Solution without claiming preview or deployment before verification succeeds.</p></div></header>
    <section className="solution-compose-card"><div><span className="eyebrow">Create</span><h2>What should this Solution accomplish?</h2><p>Start with the objective rather than choosing a fake template or implementation stack. Operly can ask for clarification when the requirement genuinely needs it.</p></div><form onSubmit={compose}><label>Solution name <span>optional</span><input name="name" maxLength={200} placeholder="Inventory assistant" /></label><label>Business objective<textarea name="objective" required rows={5} maxLength={12000} placeholder="Build a system that tracks restaurant inventory, warns when ingredients run low, and lets managers approve restocking…" /></label><div className="compose-guidance"><span>1 · Understand</span><span>2 · Plan capabilities</span><span>3 · Build</span><span>4 · Verify</span></div><button className="primary-button" disabled={creating}>{creating ? "Creating…" : "Create Solution"}</button></form>{result && <div className="success-banner">{result}</div>}{error && <div className="inline-error">{error}</div>}</section>
    <section className="solutions-library"><div className="section-heading"><div><span className="eyebrow">Workspace</span><h2>Your Solutions</h2></div><span>{solutions.length}</span></div>{loading ? <div className="loading-panel">Loading Solutions…</div> : solutions.length ? <div className="solution-grid">{solutions.map((solution) => { const preview = object(solution.preview); const production = object(solution.production); const generation = object(solution.generation); const solutionId = text(solution.id); const failed = text(solution.status).toLowerCase() === "failed"; return <article className="solution-card" key={solutionId}><div className="solution-card-top"><span>{title(solution.solution_type || solution.type || "Solution")}</span><span className={`status-chip status-${text(solution.status, "unknown").replaceAll("_", "-")}`}>{title(solution.status || "unknown")}</span></div><h3>{text(solution.name, "Untitled Solution")}</h3><p>{text(solution.objective || solution.description, "Workspace Solution")}</p><div className="solution-meta"><span>Preview: {text(preview.state || (preview.url ? "available" : "not ready"), "not ready")}</span><span>Production: {text(production.state, "not published")}</span>{generation.stage && <span>Generation stage: {title(generation.stage)}</span>}{generation.attempt && <span>Attempt: {text(generation.attempt)}</span>}</div>{failed && generation.error && <div className="inline-error"><strong>Generation failed at {title(generation.stage || "generation")}</strong><div>{text(generation.error)}</div></div>}<div className="page-actions">{failed && <button className="secondary-button" type="button" disabled={Boolean(retryingId)} onClick={() => retryGeneration(solutionId)}>{retryingId === solutionId ? "Retrying…" : "Retry generation"}</button>}{text(preview.url) && <a className="secondary-button" href={text(preview.url)} target="_blank" rel="noreferrer">Preview</a>}{text(production.url) && <a className="primary-button" href={text(production.url)} target="_blank" rel="noreferrer">Live</a>}</div></article>; })}</div> : <div className="empty-panel">No Solutions yet. Create one from the business objective above.</div>}</section>
    <section className="info-banner"><strong>Studio migration boundary</strong><p>The existing deep Studio editor remains protected until PR #94’s final truthful generation contract lands. This React page owns Solution discovery and creation without modifying #94’s in-flight legacy Studio file.</p></section>
  </main>;
}

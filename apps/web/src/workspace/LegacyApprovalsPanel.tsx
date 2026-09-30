import { useEffect, useState } from "react";

import { api } from "../api";

type Row = Record<string, unknown>;

const text = (value: unknown, fallback = "") =>
  typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row =>
  value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const titleCase = (value: unknown) =>
  text(value, "unknown").replaceAll("_", " ").replaceAll(".", " › ").replace(/\b\w/g, (char) => char.toUpperCase());

function Status({ value }: { value: unknown }) {
  return <span className={`status-chip status-${text(value).toLowerCase().replaceAll("_", "-")}`}>{titleCase(value)}</span>;
}

function ApprovalSubstance({ item }: { item: Row }) {
  const details = object(item.details);
  return <div className="approval-substance">
    <div className="approval-substance-grid">
      <span><small>Objective</small><strong>{text(details.objective, text(item.action, "Action"))}</strong></span>
      <span><small>Expected outcome</small><strong>{text(details.expected_outcome, "Complete the requested action")}</strong></span>
      <span><small>Risk</small><strong>{text(details.risk_level, "Review required")}</strong></span>
      <span><small>Capability</small><strong>{text(details.capability, text(item.action, "Action"))}</strong></span>
    </div>
    {details.rationale && <p className="approval-rationale"><strong>Why this legacy flow wants to do this:</strong> {text(details.rationale)}</p>}
    <details><summary>See the full legacy action</summary><code>{JSON.stringify(details, null, 2)}</code></details>
  </div>;
}

/**
 * Compatibility surface for pre-Kernel ActionService/task approvals.
 *
 * Do not add new approval producers here. New governed actions belong to
 * KernelApproval and the shared frontend capability runtime.
 */
export function LegacyApprovalsPanel() {
  const [approvals, setApprovals] = useState<Row[]>([]);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function reload() {
    try {
      setApprovals(await api<Row[]>("/approvals"));
    } catch {
      // Legacy approvals are optional compatibility data. Their absence must
      // never block the modern Activity surface.
      setApprovals([]);
    }
  }

  useEffect(() => { void reload(); }, []);

  async function decide(id: string, status: "approved" | "rejected") {
    setBusy(id);
    setError(null);
    try {
      await api(`/approvals/${encodeURIComponent(id)}`, {
        method: "PATCH",
        body: JSON.stringify({ status }),
      });
      await reload();
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Legacy approval decision could not be saved");
    } finally {
      setBusy(null);
    }
  }

  if (!approvals.length && !error) return null;
  const pending = approvals.filter((item) => text(item.status) === "pending");

  return <article className="data-card">
    <div className="card-heading">
      <div><span className="eyebrow">Compatibility</span><h2>Legacy approvals</h2></div>
      <span>{pending.length} pending</span>
    </div>
    <p className="integration-meta">Only older ActionService/task flows appear here. New Operly actions use Kernel approvals above.</p>
    {error && <div className="inline-error">{error}</div>}
    <div className="row-list">
      {approvals.slice(0, 12).map((item) => <div className="data-row stacked approval-row" key={text(item.id)}>
        <div><Status value={item.status} /><strong>{text(item.action, "Action")}</strong><ApprovalSubstance item={item} /></div>
        {text(item.status) === "pending" && <div className="row-actions">
          <button type="button" disabled={busy === text(item.id)} onClick={() => void decide(text(item.id), "rejected")}>Reject</button>
          <button type="button" className="primary-button" disabled={busy === text(item.id)} onClick={() => void decide(text(item.id), "approved")}>{busy === text(item.id) ? "Working…" : "Approve"}</button>
        </div>}
      </div>)}
    </div>
  </article>;
}

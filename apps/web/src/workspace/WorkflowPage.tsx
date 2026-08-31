import { useEffect, useMemo, useState } from "react";

import { ApiError, api } from "../api";
import { navigate, workspacePath } from "../app/routes";
import { WorkspaceSummary } from "../app/types";

type Row = Record<string, unknown>;
type JsonSchema = {
  type?: string | string[];
  enum?: unknown[];
  properties?: Record<string, JsonSchema>;
  required?: string[];
  items?: JsonSchema;
  description?: string;
};
type Capability = {
  id: string;
  display_name: string;
  description: string;
  endpoint: string;
  method: "POST";
  input_schema: JsonSchema;
  risk: string;
  approval_required: boolean;
  tags: string[];
};
type ToolCatalog = { tools: Capability[] };
type ToolRun = { run_id: string; status: string; result: unknown; done: boolean };
type Workflow = {
  id: string;
  name: string;
  description: string;
  status: string;
  owner_user_id?: string | null;
  current_version: number;
  schedule?: Row | null;
  schedule_enabled?: boolean;
  next_run_at?: string | null;
  created_at?: string;
  updated_at?: string;
};
type WorkflowRun = {
  id: string;
  workflow_id: string;
  workflow_version_id: string;
  authority_user_id?: string | null;
  initiated_by_user_id?: string | null;
  status: string;
  trigger_type: string;
  trigger?: Row;
  current_step_key?: string | null;
  error_code?: string | null;
  error_message?: string | null;
  scheduled_for?: string | null;
  created_at?: string;
  started_at?: string | null;
  finished_at?: string | null;
};
type WorkflowVersion = {
  id: string;
  workflow_id: string;
  version: number;
  name?: string;
  status?: string;
  schedule?: Row | null;
  spec?: { steps?: Row[] };
  snapshot?: { name?: string; description?: string; status?: string; schedule?: Row | null; spec?: { steps?: Row[] } };
  created_by_user_id?: string | null;
  created_at?: string;
};
type WorkflowStepAttempt = {
  id: string;
  attempt: number;
  capability_id?: string | null;
  status: string;
  request_id?: string | null;
  kernel_run_id?: string | null;
  approval_id?: string | null;
  arguments?: Row;
  result?: Row;
  error_code?: string | null;
  error_message?: string | null;
  started_at?: string | null;
  finished_at?: string | null;
};
type WorkflowStep = {
  id: string;
  step_key: string;
  step_order: number;
  kind: string;
  capability_id?: string | null;
  status: string;
  attempt: number;
  request_id?: string | null;
  kernel_run_id?: string | null;
  approval_id?: string | null;
  arguments?: Row;
  result?: Row;
  error_code?: string | null;
  error_message?: string | null;
  wait_until?: string | null;
  attempts?: WorkflowStepAttempt[];
};
type RunDetail = { run: WorkflowRun; version?: WorkflowVersion | null; steps: WorkflowStep[]; result?: Row };
type TraceEvent = {
  id: string;
  event_type: string;
  workflow_id: string;
  workflow_run_id?: string | null;
  step_run_id?: string | null;
  step_attempt_id?: string | null;
  capability_id?: string | null;
  kernel_run_id?: string | null;
  approval_id?: string | null;
  actor_type?: string;
  actor_id?: string | null;
  payload?: Row;
  created_at?: string;
};
type Approval = {
  id: string;
  capability_id?: string;
  conversation_id?: string | null;
  status: string;
  arguments?: Row;
  requested_by_principal_id?: string | null;
  created_at?: string;
};
type PendingAction = {
  capabilityId: string;
  arguments: Row;
  approvalId: string;
  requestId: string;
  label: string;
};
type ScheduleType = "manual" | "once" | "interval" | "daily" | "weekly" | "cron";
type ScheduleDraft = {
  type: ScheduleType;
  timezone: string;
  at: string;
  everySeconds: string;
  startAt: string;
  time: string;
  days: number[];
  expression: string;
};
type StepDraft = {
  id: string;
  kind: "action" | "wait";
  capabilityId: string;
  argumentInputs: Record<string, string | boolean>;
  advancedArguments: string;
  useAdvancedArguments: boolean;
  dependsOn: string[];
  onError: "stop" | "continue";
  conditionEnabled: boolean;
  conditionAdvanced: boolean;
  conditionRef: string;
  conditionOp: string;
  conditionValue: string;
  conditionJson: string;
  waitMode: "seconds" | "until";
  seconds: string;
  until: string;
};

const text = (value: unknown, fallback = "") => typeof value === "string" ? value : value == null ? fallback : String(value);
const object = (value: unknown): Row => value && typeof value === "object" && !Array.isArray(value) ? value as Row : {};
const titleCase = (value: unknown) => text(value, "unknown").replaceAll("_", " ").replaceAll(".", " › ").replace(/\b\w/g, (char) => char.toUpperCase());
const when = (value: unknown) => {
  const raw = text(value);
  if (!raw) return "—";
  try { return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "numeric", minute: "2-digit" }).format(new Date(raw)); }
  catch { return raw; }
};
const timezone = () => {
  try { return Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC"; }
  catch { return "UTC"; }
};
const schemaType = (schema: JsonSchema) => Array.isArray(schema.type) ? schema.type.find((item) => item !== "null") || "string" : schema.type || "string";
const stableRequestId = (capabilityId: string) => {
  try { return `workflow-ui:${capabilityId}:${crypto.randomUUID()}`; }
  catch { return `workflow-ui:${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`; }
};
const terminalRun = (status: string) => ["completed", "completed_with_errors", "failed", "cancelled", "orphaned"].includes(status);

function defaultSchedule(): ScheduleDraft {
  return { type: "manual", timezone: timezone(), at: "", everySeconds: "3600", startAt: "", time: "09:00", days: [0, 1, 2, 3, 4], expression: "0 9 * * 1-5" };
}

function emptyStep(index: number, capabilityId = ""): StepDraft {
  return {
    id: `step_${index + 1}`,
    kind: "action",
    capabilityId,
    argumentInputs: {},
    advancedArguments: "{}",
    useAdvancedArguments: false,
    dependsOn: index > 0 ? [`step_${index}`] : [],
    onError: "stop",
    conditionEnabled: false,
    conditionAdvanced: false,
    conditionRef: "",
    conditionOp: "eq",
    conditionValue: "",
    conditionJson: "{}",
    waitMode: "seconds",
    seconds: "60",
    until: "",
  };
}

function scheduleFrom(value: unknown): ScheduleDraft {
  const row = object(value);
  const type = text(row.type, "manual") as ScheduleType;
  return {
    type: ["manual", "once", "interval", "daily", "weekly", "cron"].includes(type) ? type : "manual",
    timezone: text(row.timezone, timezone()),
    at: text(row.at),
    everySeconds: text(row.every_seconds, "3600"),
    startAt: text(row.start_at),
    time: text(row.time, "09:00"),
    days: Array.isArray(row.days) ? row.days.map((item) => Number(item)).filter((item) => Number.isInteger(item) && item >= 0 && item <= 6) : [0, 1, 2, 3, 4],
    expression: text(row.expression, "0 9 * * 1-5"),
  };
}

function schedulePayload(draft: ScheduleDraft): Row | null {
  if (draft.type === "manual") return null;
  if (draft.type === "once") return { type: "once", at: draft.at, timezone: draft.timezone };
  if (draft.type === "interval") return { type: "interval", every_seconds: Number(draft.everySeconds), ...(draft.startAt ? { start_at: draft.startAt } : {}), timezone: draft.timezone };
  if (draft.type === "daily") return { type: "daily", time: draft.time, timezone: draft.timezone };
  if (draft.type === "weekly") return { type: "weekly", days: draft.days, time: draft.time, timezone: draft.timezone };
  return { type: "cron", expression: draft.expression, timezone: draft.timezone };
}

function inputValue(value: unknown) {
  if (typeof value === "boolean") return value;
  if (value == null) return "";
  if (typeof value === "object") return JSON.stringify(value, null, 2);
  return String(value);
}

function stepFrom(raw: Row, index: number): StepDraft {
  const kind = text(raw.kind, "action") === "wait" ? "wait" : "action";
  const args = object(raw.arguments);
  const whenValue = raw.when;
  const whenRow = object(whenValue);
  const simpleCondition = Boolean(whenValue) && !Array.isArray(whenValue) && !("all" in whenRow) && !("any" in whenRow);
  return {
    ...emptyStep(index, text(raw.capability_id)),
    id: text(raw.id, `step_${index + 1}`),
    kind,
    capabilityId: text(raw.capability_id),
    argumentInputs: Object.fromEntries(Object.entries(args).map(([key, value]) => [key, inputValue(value)])),
    advancedArguments: JSON.stringify(args, null, 2),
    dependsOn: Array.isArray(raw.depends_on) ? raw.depends_on.map(String) : [],
    onError: text(raw.on_error, "stop") === "continue" ? "continue" : "stop",
    conditionEnabled: Boolean(whenValue),
    conditionAdvanced: Boolean(whenValue) && !simpleCondition,
    conditionRef: simpleCondition ? text(whenRow.ref) : "",
    conditionOp: simpleCondition ? text(whenRow.op, "truthy") : "eq",
    conditionValue: simpleCondition && "value" in whenRow ? inputValue(whenRow.value) as string : "",
    conditionJson: whenValue ? JSON.stringify(whenValue, null, 2) : "{}",
    waitMode: "until" in raw ? "until" : "seconds",
    seconds: text(raw.seconds, "60"),
    until: text(raw.until),
  };
}

function parseField(schema: JsonSchema, value: string | boolean): unknown {
  if (typeof value === "boolean") return value;
  const raw = value.trim();
  if (!raw) return undefined;
  if (/^\{\{.+\}\}$/.test(raw)) return raw;
  const type = schemaType(schema);
  if (type === "integer") {
    const parsed = Number.parseInt(raw, 10);
    if (!Number.isFinite(parsed)) throw new Error("Enter a whole number or a {{template}} reference.");
    return parsed;
  }
  if (type === "number") {
    const parsed = Number(raw);
    if (!Number.isFinite(parsed)) throw new Error("Enter a number or a {{template}} reference.");
    return parsed;
  }
  if (type === "boolean") return raw === "true";
  if (type === "array") {
    const parsed = JSON.parse(raw);
    if (!Array.isArray(parsed)) throw new Error("This field needs a JSON list.");
    return parsed;
  }
  if (type === "object") {
    const parsed = JSON.parse(raw);
    if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error("This field needs a JSON object.");
    return parsed;
  }
  return raw;
}

function conditionValue(raw: string): unknown {
  const clean = raw.trim();
  if (!clean) return "";
  if (/^\{\{.+\}\}$/.test(clean)) return clean;
  if (clean === "true") return true;
  if (clean === "false") return false;
  if (clean === "null") return null;
  if (!Number.isNaN(Number(clean)) && clean !== "") return Number(clean);
  try { if (clean.startsWith("[") || clean.startsWith("{")) return JSON.parse(clean); } catch { /* text is valid too */ }
  return clean;
}

function Status({ value }: { value: unknown }) {
  const clean = text(value, "unknown").toLowerCase().replaceAll("_", "-");
  return <span className={`status-chip status-${clean}`}>{titleCase(value)}</span>;
}

export function WorkflowPage({ workspace }: { workspace: WorkspaceSummary }) {
  const [catalog, setCatalog] = useState<ToolCatalog | null>(null);
  const [workflows, setWorkflows] = useState<Workflow[]>([]);
  const [runs, setRuns] = useState<WorkflowRun[]>([]);
  const [runtime, setRuntime] = useState<Row>({});
  const [approvals, setApprovals] = useState<Approval[]>([]);
  const [selectedWorkflowId, setSelectedWorkflowId] = useState<string | null>(null);
  const [selectedWorkflow, setSelectedWorkflow] = useState<Workflow | null>(null);
  const [versions, setVersions] = useState<WorkflowVersion[]>([]);
  const [selectedVersion, setSelectedVersion] = useState<WorkflowVersion | null>(null);
  const [trace, setTrace] = useState<TraceEvent[]>([]);
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);
  const [runDetail, setRunDetail] = useState<RunDetail | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<PendingAction | null>(null);
  const [mode, setMode] = useState<"list" | "new" | "edit">("list");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [createEnabled, setCreateEnabled] = useState(false);
  const [schedule, setSchedule] = useState<ScheduleDraft>(defaultSchedule);
  const [schedulePreview, setSchedulePreview] = useState<string[]>([]);
  const [steps, setSteps] = useState<StepDraft[]>([]);
  const [triggerText, setTriggerText] = useState("{}");

  const toolsById = useMemo(() => new Map((catalog?.tools || []).map((item) => [item.id, item])), [catalog]);
  const workflowTools = useMemo(() => (catalog?.tools || []).filter((item) => item.id.startsWith("workflow.")), [catalog]);
  const actionTools = useMemo(() => (catalog?.tools || []).filter((item) => !item.id.startsWith("workflow.")).sort((a, b) => a.display_name.localeCompare(b.display_name)), [catalog]);
  const canManage = workflowTools.some((item) => item.id === "workflow.create");
  const workflowApprovals = useMemo(() => approvals.filter((item) => item.capability_id?.startsWith("workflow.") || text(item.conversation_id).startsWith("workflow:")), [approvals]);
  const selectedRuns = useMemo(() => selectedWorkflowId ? runs.filter((item) => item.workflow_id === selectedWorkflowId) : runs, [runs, selectedWorkflowId]);

  async function invokeValue<T>(capabilityId: string, argumentsValue: Row, requestId = stableRequestId(capabilityId), approvalId?: string): Promise<T> {
    const capability = toolsById.get(capabilityId);
    if (!capability) throw new Error(`${titleCase(capabilityId)} is not available to your role right now.`);
    const response = await api<ToolRun>(capability.endpoint, {
      method: capability.method,
      body: JSON.stringify({ arguments: argumentsValue, request_id: requestId, approval_id: approvalId }),
    });
    return response.result as T;
  }

  async function refreshApprovals() {
    try {
      const response = await api<{ approvals: Approval[] }>("/workspace-tools/approvals?limit=100");
      setApprovals(response.approvals || []);
    } catch { setApprovals([]); }
  }

  async function refreshOverview(nextCatalog = catalog) {
    if (!nextCatalog) return;
    const map = new Map(nextCatalog.tools.map((item) => [item.id, item]));
    async function read<T>(capabilityId: string, args: Row): Promise<T | null> {
      const capability = map.get(capabilityId);
      if (!capability) return null;
      try {
        const response = await api<ToolRun>(capability.endpoint, { method: capability.method, body: JSON.stringify({ arguments: args, request_id: stableRequestId(capabilityId) }) });
        return response.result as T;
      } catch { return null; }
    }
    const [workflowResult, runResult, runtimeResult] = await Promise.all([
      read<{ workflows: Workflow[] }>("workflow.list", { include_archived: true, limit: 200 }),
      read<{ runs: WorkflowRun[] }>("workflow.run.list", { limit: 200 }),
      read<Row>("workflow.runtime.status", {}),
    ]);
    setWorkflows(workflowResult?.workflows || []);
    setRuns(runResult?.runs || []);
    setRuntime(runtimeResult || {});
    await refreshApprovals();
    if (!selectedWorkflowId && workflowResult?.workflows?.length) setSelectedWorkflowId(workflowResult.workflows[0].id);
  }

  async function boot() {
    setLoading(true);
    setError(null);
    try {
      const nextCatalog = await api<ToolCatalog>("/workspace-tools");
      setCatalog(nextCatalog);
      await refreshOverview(nextCatalog);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not load Workflow tools");
    } finally { setLoading(false); }
  }

  useEffect(() => { void boot(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [workspace.id]);

  async function loadWorkflow(workflowId: string) {
    if (!catalog || !toolsById.get("workflow.get")) return;
    try {
      const [detail, versionList, traceResult] = await Promise.all([
        invokeValue<{ workflow: Workflow; version?: WorkflowVersion | null; recent_runs?: WorkflowRun[] }>("workflow.get", { workflow_id: workflowId }),
        invokeValue<{ versions: WorkflowVersion[] }>("workflow.version.list", { workflow_id: workflowId, limit: 200 }),
        invokeValue<{ events: TraceEvent[] }>("workflow.trace", { workflow_id: workflowId, limit: 300 }),
      ]);
      setSelectedWorkflow(detail.workflow);
      setVersions(versionList.versions || []);
      setTrace(traceResult.events || []);
      if (detail.version) setSelectedVersion(detail.version);
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : "Could not open this workflow");
    }
  }

  useEffect(() => {
    if (selectedWorkflowId && catalog) void loadWorkflow(selectedWorkflowId);
    else { setSelectedWorkflow(null); setVersions([]); setTrace([]); }
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [selectedWorkflowId, catalog]);

  async function loadRun(runId: string) {
    setSelectedRunId(runId);
    try { setRunDetail(await invokeValue<RunDetail>("workflow.run.get", { run_id: runId })); }
    catch (caught) { setError(caught instanceof Error ? caught.message : "Could not inspect this workflow run"); }
  }

  function resetBuilder() {
    setName("");
    setDescription("");
    setCreateEnabled(false);
    setSchedule(defaultSchedule());
    setSchedulePreview([]);
    setSteps([emptyStep(0, actionTools[0]?.id || "")]);
  }

  function startNew() {
    resetBuilder();
    setMode("new");
    setSelectedWorkflowId(null);
    setSelectedWorkflow(null);
    setSelectedRunId(null);
    setRunDetail(null);
    setError(null);
    setNotice(null);
  }

  function startEdit() {
    if (!selectedWorkflow || !selectedVersion) return;
    const snapshot = selectedVersion.snapshot || {};
    const spec = snapshot.spec || selectedVersion.spec || {};
    setName(text(snapshot.name, selectedWorkflow.name));
    setDescription(text(snapshot.description, selectedWorkflow.description));
    setSchedule(scheduleFrom(snapshot.schedule ?? selectedWorkflow.schedule));
    setSteps((spec.steps || []).map((item, index) => stepFrom(item, index)));
    setMode("edit");
    setSchedulePreview([]);
    setError(null);
    setNotice(null);
  }

  function actionSchema(step: StepDraft) {
    return toolsById.get(step.capabilityId)?.input_schema || {};
  }

  function buildArguments(step: StepDraft): Row {
    if (step.useAdvancedArguments) {
      const parsed = JSON.parse(step.advancedArguments || "{}");
      if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) throw new Error(`${step.id}: advanced arguments must be a JSON object.`);
      return parsed as Row;
    }
    const schema = actionSchema(step);
    const properties = schema.properties || {};
    const result: Row = {};
    for (const [key, value] of Object.entries(step.argumentInputs)) {
      const parsed = parseField(properties[key] || {}, value);
      if (parsed !== undefined) result[key] = parsed;
    }
    return result;
  }

  function buildSpec() {
    if (!steps.length) throw new Error("Add at least one step.");
    return {
      steps: steps.map((step, index) => {
        const id = step.id.trim() || `step_${index + 1}`;
        const common: Row = { id, kind: step.kind, depends_on: step.dependsOn, on_error: step.onError };
        if (step.conditionEnabled) {
          if (step.conditionAdvanced) common.when = JSON.parse(step.conditionJson || "{}");
          else {
            if (!step.conditionRef.trim()) throw new Error(`${id}: choose what the optional rule should check.`);
            common.when = { ref: step.conditionRef.trim(), op: step.conditionOp, ...(step.conditionOp === "truthy" || step.conditionOp === "exists" ? {} : { value: conditionValue(step.conditionValue) }) };
          }
        }
        if (step.kind === "wait") {
          if (step.waitMode === "seconds") common.seconds = Number(step.seconds);
          else common.until = step.until;
          return common;
        }
        if (!step.capabilityId) throw new Error(`${id}: choose an action.`);
        common.capability_id = step.capabilityId;
        common.arguments = buildArguments(step);
        return common;
      }),
    };
  }

  async function perform(capabilityId: string, argumentsValue: Row, label: string) {
    const requestId = stableRequestId(capabilityId);
    setBusy(label);
    setError(null);
    setNotice(null);
    try {
      await invokeValue(capabilityId, argumentsValue, requestId);
      setNotice(`${label} completed.`);
      setPendingAction(null);
      await refreshOverview();
      if (selectedWorkflowId) await loadWorkflow(selectedWorkflowId);
      return true;
    } catch (caught) {
      if (caught instanceof ApiError && caught.code === "approval_required") {
        const details = object(caught.details);
        const approvalId = text(details.approval_id);
        if (approvalId) {
          setPendingAction({ capabilityId, arguments: argumentsValue, approvalId, requestId, label });
          setNotice(`${label} is waiting for your approval.`);
          await refreshApprovals();
          return false;
        }
      }
      setError(caught instanceof Error ? caught.message : `${label} failed`);
      return false;
    } finally { setBusy(null); }
  }

  async function decidePending(approved: boolean) {
    if (!pendingAction) return;
    setBusy("approval");
    setError(null);
    try {
      await api(`/workspace-tools/approvals/${encodeURIComponent(pendingAction.approvalId)}/decision`, { method: "POST", body: JSON.stringify({ approved }) });
      if (!approved) {
        setNotice("Nothing changed. You did not approve that action.");
        setPendingAction(null);
        await refreshApprovals();
        return;
      }
      await invokeValue(pendingAction.capabilityId, pendingAction.arguments, pendingAction.requestId, pendingAction.approvalId);
      setNotice(`${pendingAction.label} completed after approval.`);
      setPendingAction(null);
      await refreshOverview();
      if (selectedWorkflowId) await loadWorkflow(selectedWorkflowId);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Approval could not be completed"); }
    finally { setBusy(null); }
  }

  async function decideWorkflowApproval(item: Approval, approved: boolean) {
    setBusy(`approval:${item.id}`);
    setError(null);
    try {
      await api(`/workspace-tools/approvals/${encodeURIComponent(item.id)}/decision`, { method: "POST", body: JSON.stringify({ approved }) });
      setNotice(approved ? "Approved. The workflow scheduler will continue the exact paused action." : "Denied. The workflow will record the rejection in its trace.");
      await refreshApprovals();
      window.setTimeout(() => { void refreshOverview(); if (selectedWorkflowId) void loadWorkflow(selectedWorkflowId); }, 1200);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Approval decision could not be saved"); }
    finally { setBusy(null); }
  }

  async function saveBuilder() {
    try {
      const spec = buildSpec();
      const scheduleValue = schedulePayload(schedule);
      if (mode === "new") {
        const ok = await perform("workflow.create", { name, description, spec, schedule: scheduleValue, enabled: createEnabled }, "Create workflow");
        if (ok) { setMode("list"); await refreshOverview(); }
      } else if (selectedWorkflowId) {
        const ok = await perform("workflow.update", { workflow_id: selectedWorkflowId, name, description, spec, schedule: scheduleValue }, "Save workflow changes");
        if (ok) setMode("list");
      }
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Workflow definition is not valid"); }
  }

  async function previewSchedule() {
    try {
      const value = schedulePayload(schedule);
      if (!value) { setSchedulePreview([]); setNotice("Manual workflows run only when you start them."); return; }
      setBusy("preview");
      const result = await invokeValue<{ occurrences: string[] }>("workflow.schedule.preview", { schedule: value, count: 8 });
      setSchedulePreview(result.occurrences || []);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not preview this schedule"); }
    finally { setBusy(null); }
  }

  async function runNow() {
    if (!selectedWorkflowId) return;
    try {
      const trigger = JSON.parse(triggerText || "{}");
      if (!trigger || typeof trigger !== "object" || Array.isArray(trigger)) throw new Error("Run inputs must be a JSON object.");
      const ok = await perform("workflow.run.start", { workflow_id: selectedWorkflowId, trigger }, "Start workflow run");
      if (ok) setTriggerText("{}");
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Run inputs are not valid JSON"); }
  }

  async function openVersion(version: number) {
    if (!selectedWorkflowId) return;
    setBusy(`version:${version}`);
    try {
      const result = await invokeValue<{ version: WorkflowVersion }>("workflow.version.get", { workflow_id: selectedWorkflowId, version });
      setSelectedVersion(result.version);
    } catch (caught) { setError(caught instanceof Error ? caught.message : "Could not load that workflow version"); }
    finally { setBusy(null); }
  }

  function updateStep(index: number, patch: Partial<StepDraft>) {
    setSteps((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, ...patch } : item));
  }

  function setStepArgument(index: number, key: string, value: string | boolean) {
    setSteps((current) => current.map((item, itemIndex) => itemIndex === index ? { ...item, argumentInputs: { ...item.argumentInputs, [key]: value } } : item));
  }

  function changeStepCapability(index: number, capabilityId: string) {
    const capability = toolsById.get(capabilityId);
    const inputs = Object.fromEntries(Object.keys(capability?.input_schema.properties || {}).map((key) => [key, ""]));
    updateStep(index, { capabilityId, argumentInputs: inputs, advancedArguments: "{}", useAdvancedArguments: false });
  }

  function addStep(kind: "action" | "wait") {
    setSteps((current) => {
      const next = emptyStep(current.length, actionTools[0]?.id || "");
      next.kind = kind;
      if (current.length) next.dependsOn = [current[current.length - 1].id];
      return [...current, next];
    });
  }

  function removeStep(index: number) {
    setSteps((current) => {
      const removed = current[index]?.id;
      return current.filter((_, itemIndex) => itemIndex !== index).map((item) => ({ ...item, dependsOn: item.dependsOn.filter((value) => value !== removed) }));
    });
  }

  const runtimeRunning = Boolean(runtime.running);
  const allAttempts = runDetail?.steps.flatMap((step) => step.attempts || []) || [];

  return <main className="workspace-page">
    <header className="surface-header page-header">
      <div><span className="eyebrow">Automate anything your workspace can already do</span><h1>Workflows</h1><p>Build repeatable work visually, schedule it, watch every run, approve important actions, and trace exactly what happened. Workflow uses the same governed tools as the rest of Operly.</p></div>
      <div className="page-actions"><button type="button" onClick={() => void boot()} disabled={loading}>Refresh</button>{canManage && <button type="button" className="primary-button" onClick={startNew}>＋ New workflow</button>}</div>
    </header>

    {loading && <div className="loading-panel">Loading Workflow…</div>}
    {error && <div className="inline-error page-error">{error}</div>}
    {notice && <div className="approval-substance" style={{ marginBottom: 16 }}><strong>{notice}</strong></div>}

    {!loading && workflowTools.length === 0 && <section className="data-card"><div className="empty-panel"><strong>Workflow is not available to this role.</strong><p>The backend only advertises tools your current Workspace authority allows. A Workspace owner can manage Workflow permissions.</p><button type="button" onClick={() => navigate(workspacePath(workspace.id, "capabilities"))}>Open All tools</button></div></section>}

    {workflowTools.length > 0 && <>
      <section className="metric-grid">
        <article className="metric-card"><span>Workflows</span><strong>{workflows.length}</strong><small>{workflows.filter((item) => item.status === "enabled").length} enabled</small></article>
        <article className="metric-card"><span>Runs</span><strong>{runs.length}</strong><small>{runs.filter((item) => !terminalRun(item.status)).length} active or waiting</small></article>
        <article className="metric-card"><span>Needs your OK</span><strong>{workflowApprovals.filter((item) => item.status === "pending").length}</strong><small>Workflow-related approvals</small></article>
        <article className="metric-card"><span>Scheduler</span><strong>{runtimeRunning ? "Running" : "Stopped"}</strong><small>{runtime.worker_limit ? `${text(runtime.worker_limit)} worker capacity` : "Durable database scheduler"}</small></article>
      </section>

      {pendingAction && <section className="data-card" style={{ marginBottom: 18 }}><div className="card-heading"><div><span className="eyebrow">Your decision</span><h2>{pendingAction.label}</h2></div><Status value="pending" /></div><p>This exact Workflow management action is paused. Nothing will execute until you approve it.</p><details><summary>See exact arguments</summary><code>{JSON.stringify(pendingAction.arguments, null, 2)}</code></details><div className="row-actions" style={{ marginTop: 12 }}><button disabled={busy === "approval"} onClick={() => void decidePending(false)}>Don’t do it</button><button className="primary-button" disabled={busy === "approval"} onClick={() => void decidePending(true)}>Yes, do it</button></div></section>}

      {workflowApprovals.some((item) => item.status === "pending") && <section className="data-card" style={{ marginBottom: 18 }}><div className="card-heading"><div><span className="eyebrow">Human control</span><h2>Workflow actions waiting for approval</h2></div><span>{workflowApprovals.filter((item) => item.status === "pending").length}</span></div><div className="row-list">{workflowApprovals.filter((item) => item.status === "pending").map((item) => <div className="data-row stacked approval-row" key={item.id}><div><strong>{titleCase(item.capability_id || "Workflow action")}</strong><small>{text(item.conversation_id).startsWith("workflow:") ? "A workflow run is paused at this action." : "A Workflow management change is waiting."}</small><details><summary>See exact arguments</summary><code>{JSON.stringify(item.arguments || {}, null, 2)}</code></details></div><div className="row-actions"><button disabled={busy === `approval:${item.id}`} onClick={() => void decideWorkflowApproval(item, false)}>Deny</button><button className="primary-button" disabled={busy === `approval:${item.id}`} onClick={() => void decideWorkflowApproval(item, true)}>Approve once</button></div></div>)}</div></section>}

      {mode === "new" || mode === "edit" ? <section className="data-card">
        <div className="card-heading"><div><span className="eyebrow">{mode === "new" ? "New automation" : "Edit definition"}</span><h2>{mode === "new" ? "Build a workflow" : `Edit ${selectedWorkflow?.name || "workflow"}`}</h2></div><button type="button" onClick={() => setMode("list")}>Close builder</button></div>
        <div className="inline-form" style={{ display: "grid", gap: 14 }}>
          <label>Name<input value={name} onChange={(event) => setName(event.target.value)} placeholder="Example: Follow up with new customers" /></label>
          <label>Description<textarea rows={2} value={description} onChange={(event) => setDescription(event.target.value)} placeholder="Explain what this automation does in plain language" /></label>
        </div>

        <div className="content-grid two-column" style={{ marginTop: 18 }}>
          <article className="data-card"><div className="card-heading"><div><span className="eyebrow">When</span><h3>Schedule</h3></div></div><div className="inline-form" style={{ display: "grid", gap: 12 }}>
            <label>How should it start?<select value={schedule.type} onChange={(event) => setSchedule((current) => ({ ...current, type: event.target.value as ScheduleType }))}><option value="manual">Only when I press Run</option><option value="once">Once at a date/time</option><option value="interval">Every few minutes/hours</option><option value="daily">Every day</option><option value="weekly">Selected weekdays</option><option value="cron">Advanced cron schedule</option></select></label>
            {schedule.type !== "manual" && <label>Timezone<input value={schedule.timezone} onChange={(event) => setSchedule((current) => ({ ...current, timezone: event.target.value }))} /></label>}
            {schedule.type === "once" && <label>Run at<input type="datetime-local" value={schedule.at} onChange={(event) => setSchedule((current) => ({ ...current, at: event.target.value }))} /></label>}
            {schedule.type === "interval" && <><label>Every how many seconds?<input type="number" min={60} value={schedule.everySeconds} onChange={(event) => setSchedule((current) => ({ ...current, everySeconds: event.target.value }))} /><small>60 seconds minimum.</small></label><label>Optional first run<input type="datetime-local" value={schedule.startAt} onChange={(event) => setSchedule((current) => ({ ...current, startAt: event.target.value }))} /></label></>}
            {(schedule.type === "daily" || schedule.type === "weekly") && <label>Time<input type="time" value={schedule.time} onChange={(event) => setSchedule((current) => ({ ...current, time: event.target.value }))} /></label>}
            {schedule.type === "weekly" && <div><strong>Days</strong><div className="row-actions" style={{ marginTop: 8, flexWrap: "wrap" }}>{["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].map((label, day) => <button type="button" key={label} className={schedule.days.includes(day) ? "primary-button" : ""} onClick={() => setSchedule((current) => ({ ...current, days: current.days.includes(day) ? current.days.filter((item) => item !== day) : [...current.days, day].sort() }))}>{label}</button>)}</div></div>}
            {schedule.type === "cron" && <label>Cron expression<input value={schedule.expression} onChange={(event) => setSchedule((current) => ({ ...current, expression: event.target.value }))} placeholder="0 9 * * 1-5" /><small>5 fields: minute, hour, day, month, weekday.</small></label>}
            {mode === "new" && schedule.type !== "manual" && <label>Start scheduling now?<select value={String(createEnabled)} onChange={(event) => setCreateEnabled(event.target.value === "true")}><option value="false">No, save it disabled first</option><option value="true">Yes, enable after creation</option></select></label>}
            <div className="row-actions"><button type="button" disabled={busy === "preview"} onClick={() => void previewSchedule()}>Preview next times</button></div>
            {schedulePreview.length > 0 && <div className="row-list">{schedulePreview.map((item) => <div className="data-row" key={item}><div><strong>{when(item)}</strong><small>{item}</small></div></div>)}</div>}
          </div></article>

          <article className="data-card"><div className="card-heading"><div><span className="eyebrow">How data moves</span><h3>References</h3></div></div><p>Later steps can reuse the trigger or earlier results without copying values by hand.</p><div className="row-list"><div className="data-row stacked"><div><strong>Trigger input</strong><code>{"{{trigger.customer_id}}"}</code></div></div><div className="data-row stacked"><div><strong>Earlier result</strong><code>{"{{steps.step_1.result.id}}"}</code></div></div><div className="data-row stacked"><div><strong>Run information</strong><code>{"{{run.id}}"}</code></div></div></div><p><small>Templates are resolved only when the run reaches that step, then the normal tool schema and permissions are applied.</small></p></article>
        </div>

        <section style={{ marginTop: 18 }}><div className="card-heading"><div><span className="eyebrow">Then</span><h2>Steps</h2></div><div className="row-actions"><button type="button" onClick={() => addStep("wait")}>＋ Wait</button><button type="button" className="primary-button" onClick={() => addStep("action")}>＋ Action</button></div></div>
          <div className="row-list">{steps.map((step, index) => {
            const capability = toolsById.get(step.capabilityId);
            const properties = capability?.input_schema.properties || {};
            const required = new Set(capability?.input_schema.required || []);
            const earlier = steps.slice(0, index);
            return <article className="data-card" key={`${step.id}-${index}`}>
              <div className="card-heading"><div><span className="eyebrow">Step {index + 1}</span><h3>{step.kind === "wait" ? "Wait" : capability?.display_name || "Choose an action"}</h3></div><div className="row-actions"><Status value={step.kind} /><button type="button" onClick={() => removeStep(index)} disabled={steps.length === 1}>Remove</button></div></div>
              <div className="inline-form" style={{ display: "grid", gap: 12 }}>
                <label>Step name / ID<input value={step.id} onChange={(event) => updateStep(index, { id: event.target.value })} /></label>
                <label>Step type<select value={step.kind} onChange={(event) => updateStep(index, { kind: event.target.value as "action" | "wait" })}><option value="action">Do something</option><option value="wait">Wait</option></select></label>
                {step.kind === "action" ? <>
                  <label>Action<select value={step.capabilityId} onChange={(event) => changeStepCapability(index, event.target.value)}><option value="">Choose what Operly should do</option>{actionTools.map((item) => <option key={item.id} value={item.id}>{item.display_name}</option>)}</select>{capability && <small>{capability.description}</small>}</label>
                  {!step.useAdvancedArguments && Object.entries(properties).map(([key, schema]) => {
                    const value = step.argumentInputs[key] ?? (schemaType(schema) === "boolean" ? false : "");
                    const enumValues = (schema.enum || []).filter((item) => item != null);
                    const long = ["object", "array"].includes(schemaType(schema)) || /body|content|description|code|query|notes/.test(key);
                    if (schemaType(schema) === "boolean") return <label key={key}>{titleCase(key)}{required.has(key) ? " *" : ""}<select value={String(Boolean(value))} onChange={(event) => setStepArgument(index, key, event.target.value === "true")}><option value="false">No</option><option value="true">Yes</option></select></label>;
                    if (enumValues.length) return <label key={key}>{titleCase(key)}{required.has(key) ? " *" : ""}<select value={String(value)} onChange={(event) => setStepArgument(index, key, event.target.value)}><option value="">Choose / use a template</option>{enumValues.map((item) => <option key={String(item)} value={String(item)}>{titleCase(item)}</option>)}</select></label>;
                    if (long) return <label key={key}>{titleCase(key)}{required.has(key) ? " *" : ""}<textarea rows={3} value={String(value)} onChange={(event) => setStepArgument(index, key, event.target.value)} placeholder={schemaType(schema) === "object" ? "JSON object or {{template}}" : schemaType(schema) === "array" ? "JSON list or {{template}}" : "Value or {{template}}"} /></label>;
                    return <label key={key}>{titleCase(key)}{required.has(key) ? " *" : ""}<input value={String(value)} onChange={(event) => setStepArgument(index, key, event.target.value)} placeholder="Value or {{template}}" /></label>;
                  })}
                  <details><summary>Advanced exact arguments</summary><label style={{ display: "block", marginTop: 10 }}><input type="checkbox" checked={step.useAdvancedArguments} onChange={(event) => updateStep(index, { useAdvancedArguments: event.target.checked })} /> Use one JSON object instead of the guided fields</label>{step.useAdvancedArguments && <textarea rows={7} value={step.advancedArguments} onChange={(event) => updateStep(index, { advancedArguments: event.target.value })} style={{ width: "100%", marginTop: 8 }} />}</details>
                </> : <>
                  <label>Wait until<select value={step.waitMode} onChange={(event) => updateStep(index, { waitMode: event.target.value as "seconds" | "until" })}><option value="seconds">A number of seconds passes</option><option value="until">A date/time or template is reached</option></select></label>
                  {step.waitMode === "seconds" ? <label>Seconds<input type="number" min={1} value={step.seconds} onChange={(event) => updateStep(index, { seconds: event.target.value })} /></label> : <label>Date/time or template<input value={step.until} onChange={(event) => updateStep(index, { until: event.target.value })} placeholder="2026-09-01T09:00:00Z or {{trigger.follow_up_at}}" /></label>}
                </>}
                {earlier.length > 0 && <div><strong>Wait for these earlier steps</strong><div className="row-actions" style={{ marginTop: 8, flexWrap: "wrap" }}>{earlier.map((item) => <button type="button" key={item.id} className={step.dependsOn.includes(item.id) ? "primary-button" : ""} onClick={() => updateStep(index, { dependsOn: step.dependsOn.includes(item.id) ? step.dependsOn.filter((value) => value !== item.id) : [...step.dependsOn, item.id] })}>{item.id}</button>)}</div></div>}
                <label>If this step fails<select value={step.onError} onChange={(event) => updateStep(index, { onError: event.target.value as "stop" | "continue" })}><option value="stop">Stop the workflow</option><option value="continue">Record the error and continue</option></select></label>
                <label>Optional rule<select value={String(step.conditionEnabled)} onChange={(event) => updateStep(index, { conditionEnabled: event.target.value === "true" })}><option value="false">Always run this step</option><option value="true">Run only when a rule matches</option></select></label>
                {step.conditionEnabled && <>{!step.conditionAdvanced ? <div className="content-grid two-column"><label>What should Operly check?<input value={step.conditionRef} onChange={(event) => updateStep(index, { conditionRef: event.target.value })} placeholder="steps.step_1.result.status" /></label><label>Comparison<select value={step.conditionOp} onChange={(event) => updateStep(index, { conditionOp: event.target.value })}><option value="truthy">Is truthy</option><option value="exists">Exists</option><option value="eq">Equals</option><option value="ne">Does not equal</option><option value="gt">Greater than</option><option value="gte">Greater than or equal</option><option value="lt">Less than</option><option value="lte">Less than or equal</option><option value="in">Is in</option><option value="not_in">Is not in</option></select></label>{!["truthy", "exists"].includes(step.conditionOp) && <label>Expected value<input value={step.conditionValue} onChange={(event) => updateStep(index, { conditionValue: event.target.value })} placeholder="active or 10 or {{trigger.value}}" /></label>}</div> : <label>Advanced condition JSON<textarea rows={6} value={step.conditionJson} onChange={(event) => updateStep(index, { conditionJson: event.target.value })} placeholder='{"all":[{"ref":"steps.a.result.ok","op":"eq","value":true}]}' /></label>}<button type="button" onClick={() => updateStep(index, { conditionAdvanced: !step.conditionAdvanced })}>{step.conditionAdvanced ? "Use simple rule" : "Use all/any advanced rule"}</button></>}
              </div>
            </article>;
          })}</div>
        </section>

        <div className="row-actions" style={{ marginTop: 18 }}><button type="button" onClick={() => setMode("list")}>Cancel</button><button type="button" className="primary-button" disabled={Boolean(busy)} onClick={() => void saveBuilder()}>{busy || (mode === "new" ? "Create workflow" : "Save changes")}</button></div>
      </section> : <section className="content-grid two-column">
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Automations</span><h2>Your workflows</h2></div><span>{workflows.length}</span></div>{workflows.length ? <div className="row-list">{workflows.map((item) => <button type="button" className={`data-row stacked ${selectedWorkflowId === item.id ? "active" : ""}`} key={item.id} onClick={() => { setSelectedWorkflowId(item.id); setSelectedRunId(null); setRunDetail(null); }}><div><strong>{item.name}</strong><small>{item.description || "No description"}</small><small>{item.next_run_at ? `Next: ${when(item.next_run_at)}` : item.schedule ? "No future scheduled occurrence" : "Manual"}</small></div><Status value={item.status} /></button>)}</div> : <div className="empty-panel">No workflows yet. Create one to automate repeatable work.</div>}</article>

        <article className="data-card">{selectedWorkflow ? <><div className="card-heading"><div><span className="eyebrow">Selected workflow</span><h2>{selectedWorkflow.name}</h2></div><Status value={selectedWorkflow.status} /></div><p>{selectedWorkflow.description || "No description yet."}</p><div className="approval-substance-grid"><span><small>Version</small><strong>{selectedWorkflow.current_version}</strong></span><span><small>Next run</small><strong>{when(selectedWorkflow.next_run_at)}</strong></span><span><small>Schedule</small><strong>{titleCase(object(selectedWorkflow.schedule).type || "manual")}</strong></span><span><small>Updated</small><strong>{when(selectedWorkflow.updated_at)}</strong></span></div><div className="row-actions" style={{ marginTop: 14, flexWrap: "wrap" }}>{canManage && <button type="button" onClick={startEdit}>Edit</button>}{selectedWorkflow.status === "enabled" ? <button type="button" onClick={() => void perform("workflow.disable", { workflow_id: selectedWorkflow.id }, "Disable workflow")}>Disable</button> : selectedWorkflow.status !== "archived" && <button type="button" onClick={() => void perform("workflow.enable", { workflow_id: selectedWorkflow.id }, "Enable workflow")}>Enable</button>}<button type="button" className="primary-button" onClick={() => void runNow()}>Run now</button>{selectedWorkflow.status !== "archived" && <button type="button" onClick={() => void perform("workflow.archive", { workflow_id: selectedWorkflow.id }, "Archive workflow")}>Archive</button>}</div><details style={{ marginTop: 14 }}><summary>Run inputs</summary><p><small>Optional JSON passed to the workflow as <code>trigger.*</code>.</small></p><textarea rows={5} value={triggerText} onChange={(event) => setTriggerText(event.target.value)} style={{ width: "100%" }} /></details></> : <div className="empty-panel">Choose a workflow to see its schedule, runs, versions, and trace.</div>}</article>
      </section>}

      {mode === "list" && selectedWorkflow && <>
        <section className="content-grid two-column" style={{ marginTop: 18 }}>
          <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Execution</span><h2>Runs</h2></div><span>{selectedRuns.length}</span></div>{selectedRuns.length ? <div className="row-list">{selectedRuns.map((item) => <button type="button" className={`data-row stacked ${selectedRunId === item.id ? "active" : ""}`} key={item.id} onClick={() => void loadRun(item.id)}><div><strong>{titleCase(item.trigger_type)} run</strong><small>{when(item.created_at)} · {item.current_step_key ? `at ${item.current_step_key}` : "no active step"}</small>{item.error_message && <small>{item.error_message}</small>}</div><Status value={item.status} /></button>)}</div> : <div className="empty-panel">This workflow has not run yet.</div>}</article>

          <article className="data-card"><div className="card-heading"><div><span className="eyebrow">History you can trust</span><h2>Definition versions</h2></div><span>{versions.length}</span></div>{versions.length ? <div className="row-list">{versions.map((item) => <button type="button" className={`data-row ${selectedVersion?.version === item.version ? "active" : ""}`} key={item.id} onClick={() => void openVersion(item.version)}><div><strong>Version {item.version}</strong><small>{when(item.created_at)} · {item.name || selectedWorkflow.name}</small></div><span>Open</span></button>)}</div> : <div className="empty-panel">No version history is visible.</div>}{selectedVersion && <details style={{ marginTop: 12 }} open><summary>Exact version {selectedVersion.version}</summary><code>{JSON.stringify(selectedVersion.snapshot || { spec: selectedVersion.spec }, null, 2)}</code></details>}</article>
        </section>

        {runDetail && <section className="data-card" style={{ marginTop: 18 }}><div className="card-heading"><div><span className="eyebrow">Run inspector</span><h2>{runDetail.run.id}</h2></div><Status value={runDetail.run.status} /></div><div className="approval-substance-grid"><span><small>Authority</small><strong>{text(runDetail.run.authority_user_id, "Unknown")}</strong></span><span><small>Pinned version</small><strong>{runDetail.version ? `v${runDetail.version.version}` : runDetail.run.workflow_version_id}</strong></span><span><small>Started</small><strong>{when(runDetail.run.started_at)}</strong></span><span><small>Finished</small><strong>{when(runDetail.run.finished_at)}</strong></span></div><div className="row-actions" style={{ marginTop: 14 }}>{!terminalRun(runDetail.run.status) && <button type="button" onClick={() => void perform("workflow.run.cancel", { run_id: runDetail.run.id }, "Cancel workflow run")}>Cancel run</button>}{runDetail.run.status === "failed" && <button type="button" className="primary-button" onClick={() => void perform("workflow.run.retry", { run_id: runDetail.run.id }, "Retry workflow run")}>Retry failed step</button>}{runDetail.run.status === "orphaned" && <span className="status-chip status-high">Manual reconciliation required</span>}</div><div className="row-list" style={{ marginTop: 14 }}>{runDetail.steps.map((step) => <article className="data-row stacked" key={step.id}><div><strong>{step.step_order + 1}. {step.step_key} · {step.capability_id ? titleCase(step.capability_id) : titleCase(step.kind)}</strong><small>{titleCase(step.status)} · {step.attempt} attempt{step.attempt === 1 ? "" : "s"}</small>{step.error_message && <small>{step.error_message}</small>}</div>{step.attempts && step.attempts.length > 0 && <details><summary>Immutable attempt history ({step.attempts.length})</summary>{step.attempts.map((attempt) => <div className="approval-substance" key={attempt.id} style={{ marginTop: 8 }}><div className="approval-substance-grid"><span><small>Attempt</small><strong>{attempt.attempt}</strong></span><span><small>Status</small><strong>{titleCase(attempt.status)}</strong></span><span><small>Kernel run</small><strong>{text(attempt.kernel_run_id, "—")}</strong></span><span><small>Approval</small><strong>{text(attempt.approval_id, "Not required")}</strong></span></div><details><summary>Exact arguments and result</summary><code>{JSON.stringify({ request_id: attempt.request_id, arguments: attempt.arguments, result: attempt.result, error: attempt.error_code ? { code: attempt.error_code, message: attempt.error_message } : null }, null, 2)}</code></details></div>)}</details>}</article>)}</div><details style={{ marginTop: 12 }}><summary>Complete run payload</summary><code>{JSON.stringify(runDetail, null, 2)}</code></details></section>}

        <section className="data-card" style={{ marginTop: 18 }}><div className="card-heading"><div><span className="eyebrow">Traceability</span><h2>Workflow trace</h2></div><span>{trace.length}</span></div>{trace.length ? <div className="row-list">{trace.map((item) => <div className="data-row stacked" key={item.id}><div><strong>{titleCase(item.event_type)}</strong><small>{when(item.created_at)} · {item.capability_id ? titleCase(item.capability_id) : "Workflow engine"}</small><small>{item.kernel_run_id ? `Kernel run ${item.kernel_run_id}` : item.workflow_run_id ? `Workflow run ${item.workflow_run_id}` : "Definition event"}</small></div>{item.payload && Object.keys(item.payload).length > 0 && <details><summary>Event details</summary><code>{JSON.stringify(item.payload, null, 2)}</code></details>}</div>)}</div> : <div className="empty-panel">No trace events yet.</div>}</section>
      </>}

      <section className="content-grid two-column" style={{ marginTop: 18 }}>
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Runtime</span><h2>Scheduler health</h2></div><Status value={runtimeRunning ? "running" : "stopped"} /></div><div className="approval-substance-grid"><span><small>Poll</small><strong>{text(runtime.poll_seconds, "—")}s</strong></span><span><small>Lease</small><strong>{text(runtime.lease_seconds, "—")}s</strong></span><span><small>Workers</small><strong>{text(runtime.worker_limit, "—")}</strong></span><span><small>Active</small><strong>{text(runtime.active_workers, "0")}</strong></span></div>{runtime.last_error && <div className="inline-error" style={{ marginTop: 12 }}>{text(runtime.last_error)}</div>}<details style={{ marginTop: 12 }}><summary>Technical runtime status</summary><code>{JSON.stringify(runtime, null, 2)}</code></details></article>
        <article className="data-card"><div className="card-heading"><div><span className="eyebrow">Everything remains reachable</span><h2>All tools</h2></div><span>{catalog?.tools.length || 0}</span></div><p>The visual Workflow screen is a friendly interface over the same deterministic capability registry. If you need an uncommon or newly added action, All tools exposes every authorized capability automatically.</p><div className="row-actions"><button type="button" onClick={() => navigate(workspacePath(workspace.id, "capabilities"))}>Open All tools</button><button type="button" onClick={() => navigate(workspacePath(workspace.id, "activity"))}>Open Activity & approvals</button></div></article>
      </section>
    </>}
  </main>;
}

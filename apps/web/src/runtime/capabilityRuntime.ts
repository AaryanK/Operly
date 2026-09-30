import { ApiError, api } from "../api";

export type CapabilityTool = {
  id: string;
  display_name: string;
  description: string;
  provider_id: string;
  approval_required: boolean;
  endpoint: string;
  method: "POST";
  permissions: string[];
  risk: string;
};

export type CapabilityCatalog = {
  tools: CapabilityTool[];
};

export type CapabilityRun = {
  run_id: string;
  status: string;
  capability_id: string;
  result: unknown;
  done: boolean;
  trace?: Array<Record<string, unknown>>;
};

export type CapabilityApproval = {
  approvalId: string;
  requestId: string;
  tool: CapabilityTool;
  args: Record<string, unknown>;
};

export type CapabilityExecution =
  | { status: "completed"; run: CapabilityRun; requestId: string }
  | { status: "approval_required"; approval: CapabilityApproval };

export function capabilityRequestId(capabilityId: string): string {
  try {
    return `${capabilityId}:${crypto.randomUUID()}`;
  } catch {
    return `${capabilityId}:${Date.now()}:${Math.random().toString(36).slice(2)}`;
  }
}

function record(value: unknown): Record<string, unknown> {
  return value && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

function approvalIdFrom(error: ApiError): string {
  const details = record(error.details);
  return typeof details.approval_id === "string" ? details.approval_id : "";
}

export async function loadWorkspaceCapabilities(): Promise<CapabilityTool[]> {
  const catalog = await api<CapabilityCatalog>("/workspace-tools");
  return catalog.tools || [];
}

export async function executeCapability(
  tool: CapabilityTool,
  args: Record<string, unknown>,
  options: {
    requestId?: string;
    approvalId?: string;
    goal?: string;
    conversationId?: string;
  } = {},
): Promise<CapabilityExecution> {
  const requestId = options.requestId || capabilityRequestId(tool.id);
  try {
    const run = await api<CapabilityRun>(tool.endpoint, {
      method: tool.method,
      body: JSON.stringify({
        arguments: args,
        request_id: requestId,
        approval_id: options.approvalId,
        goal: options.goal || "",
        conversation_id: options.conversationId,
      }),
    });
    return { status: "completed", run, requestId };
  } catch (caught) {
    if (caught instanceof ApiError && caught.code === "approval_required") {
      const approvalId = approvalIdFrom(caught);
      if (approvalId) {
        return {
          status: "approval_required",
          approval: { approvalId, requestId, tool, args },
        };
      }
    }
    throw caught;
  }
}

export async function decideCapabilityApproval(
  approvalId: string,
  approved: boolean,
): Promise<void> {
  await api(
    `/workspace-tools/approvals/${encodeURIComponent(approvalId)}/decision`,
    {
      method: "POST",
      body: JSON.stringify({ approved }),
    },
  );
}

export async function resumeCapability(
  approval: CapabilityApproval,
): Promise<CapabilityRun> {
  const resumed = await executeCapability(approval.tool, approval.args, {
    requestId: approval.requestId,
    approvalId: approval.approvalId,
  });
  if (resumed.status !== "completed") {
    throw new Error("Approved capability did not resume to completion");
  }
  return resumed.run;
}

export async function denyCapability(approval: CapabilityApproval): Promise<void> {
  await decideCapabilityApproval(approval.approvalId, false);
}

export async function approveAndResumeCapability(
  approval: CapabilityApproval,
): Promise<CapabilityRun> {
  await decideCapabilityApproval(approval.approvalId, true);
  return resumeCapability(approval);
}

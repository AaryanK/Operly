import { WorkspaceSummary } from "../app/types";
import { IntegrationWorkbench } from "./integrations/IntegrationWorkbench";

export function ConnectionsPage({ workspace }: { workspace: WorkspaceSummary }) {
  return <IntegrationWorkbench workspace={workspace} />;
}

import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./app/App";
import { installArtifactDownloadHandling } from "./ui/artifactDownload";
import { initializeTheme } from "./ui/theme";
import "./ui/tokens.css";
import "./ui/app.css";
import "./ui/members.css";
import "./ui/messages.css";
import "./ui/settings.css";
import "./ui/connection-avatars.css";
import "./ui/security.css";
import "./ui/premium.css";
import "./ui/theme.css";
import "./ui/brand.css";
import "./ui/surface-polish.css";
import "./ui/legal-links.css";
import "./ui/convergence.css";
import "./ui/approvals.css";
import "./ui/admin-palette.css";
import "./ui/solution-source-inspector.css";
import "./ui/public.css";
import "./ui/mobile.css";
import "./ui/react-public-admin-palette.css";
import "./ui/react-public-live.css";
import "./ui/product-shell.css";
import "./ui/workspace-mobile.css";

initializeTheme();
installArtifactDownloadHandling();

const root = document.getElementById("root");
if (!root) throw new Error("Operly frontend root was not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

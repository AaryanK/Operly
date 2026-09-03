import React from "react";
import ReactDOM from "react-dom/client";

import { App } from "./app/App";
import "./ui/tokens.css";
import "./ui/minimal.css";
import "./ui/public.css";
import "./ui/react-public-admin-palette.css";
import "./ui/app.css";
import "./ui/theme.css";
import "./ui/workspace-lite.css";
import "./ui/workspace-os.css";
import "./ui/workspace-human.css";
import "./ui/integration-workbench.css";
import "./ui/agent-computer.css";
import "./ui/mobile.css";
import "./ui/surface-polish.css";

const INVITE_KEY = "operly:workspace-invite";
const INVITE_MAX_AGE_MS = 31 * 24 * 60 * 60 * 1000;

function bridgeWorkspaceInviteAcrossTabs() {
  const pathname = window.location.pathname;

  // A successful invitation acceptance redirects into the selected workspace.
  // Clear the cross-tab copy there so a one-use invite is never replayed later.
  if (pathname === "/account" || pathname.startsWith("/channels/")) {
    localStorage.removeItem(INVITE_KEY);
    return;
  }

  const tokenFromHash = new URLSearchParams(window.location.hash.slice(1)).get("invite")?.trim() || "";
  if (tokenFromHash) {
    sessionStorage.setItem(INVITE_KEY, tokenFromHash);
    localStorage.setItem(INVITE_KEY, JSON.stringify({ token: tokenFromHash, storedAt: Date.now() }));
    return;
  }

  if (sessionStorage.getItem(INVITE_KEY)) return;

  try {
    const raw = localStorage.getItem(INVITE_KEY);
    if (!raw) return;
    const stored = JSON.parse(raw) as { token?: string; storedAt?: number };
    const token = String(stored.token || "").trim();
    const storedAt = Number(stored.storedAt || 0);
    if (!token || !storedAt || Date.now() - storedAt > INVITE_MAX_AGE_MS) {
      localStorage.removeItem(INVITE_KEY);
      return;
    }
    sessionStorage.setItem(INVITE_KEY, token);
  } catch {
    localStorage.removeItem(INVITE_KEY);
  }
}

bridgeWorkspaceInviteAcrossTabs();

const root = document.getElementById("root");
if (!root) throw new Error("Operly frontend root was not found");

ReactDOM.createRoot(root).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);

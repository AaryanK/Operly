import { useEffect, useState } from "react";

import { LegalPage } from "../legal/LegalPage";
import { MinimalApp } from "../minimal/MinimalApp";
import { WorkspaceSafeApp } from "../workspace-lite/WorkspaceSafeApp";
import { ProductApp } from "./ProductApp";

function currentPath() {
  return window.location.pathname.replace(/\/+$/, "") || "/";
}

const PRODUCT_WORKSPACE_SECTIONS = new Set([
  "operly",
  "workflows",
  "activity",
  "solutions",
  "agent-computer",
  "connections",
  "plugins",
  "capabilities",
  "members",
  "access",
  "ai-debug",
  "settings",
]);

function usesProductWorkspace(pathname: string): boolean {
  const match = pathname.match(/^\/channels\/([^/]+)\/([^/]+)$/);
  return Boolean(match && match[1] !== "@me" && PRODUCT_WORKSPACE_SECTIONS.has(match[2]));
}

export function App() {
  const [pathname, setPathname] = useState(currentPath);

  useEffect(() => {
    const sync = () => setPathname(currentPath());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  if (pathname === "/privacy") return <LegalPage kind="privacy" />;
  if (pathname === "/terms") return <LegalPage kind="terms" />;
  if (usesProductWorkspace(pathname)) return <ProductApp />;
  if (
    pathname === "/account"
    || pathname === "/personal"
    || pathname === "/app"
    || pathname === "/channels"
    || pathname.startsWith("/channels/")
  ) {
    return <WorkspaceSafeApp pathname={pathname} />;
  }
  return <MinimalApp pathname={pathname} />;
}

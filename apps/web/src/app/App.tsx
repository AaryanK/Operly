import { useEffect, useState } from "react";

import { ProviderAuthPage } from "../auth/ProviderAuthPage";
import { LegalPage } from "../legal/LegalPage";
import { MinimalApp } from "../minimal/MinimalApp";
import { PublicApp } from "../public/PublicApp";
import { WorkspaceSafeApp } from "../workspace-lite/WorkspaceSafeApp";
import { ProductApp } from "./ProductApp";

function currentPath() {
  return window.location.pathname.replace(/\/+$/, "") || "/";
}

export function App() {
  const [pathname, setPathname] = useState(currentPath);

  useEffect(() => {
    const sync = () => setPathname(currentPath());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  if (pathname === "/") return <PublicApp pathname={pathname} />;
  if (pathname === "/login" || pathname === "/signup") return <ProviderAuthPage pathname={pathname} />;
  if (pathname === "/privacy") return <LegalPage kind="privacy" />;
  if (pathname === "/terms") return <LegalPage kind="terms" />;

  // Runtime 1.0 must use the canonical authenticated product shell end-to-end.
  // ProductApp owns scope switching, Personal Operly, workspace navigation and
  // Workspace Operly. Keeping these routes together prevents a second nested shell
  // from drifting away from the actual session/capability contracts.
  if (
    pathname === "/personal"
    || pathname === "/channels"
    || pathname === "/channels/@me"
    || pathname.startsWith("/channels/@me/")
    || (pathname.startsWith("/channels/") && pathname !== "/channels")
  ) {
    return <ProductApp />;
  }

  // Keep the transitional workspace picker route available while ProductApp owns
  // every actual Personal/Workspace operating surface.
  if (pathname === "/account" || pathname === "/app") {
    return <WorkspaceSafeApp pathname={pathname} />;
  }
  return <MinimalApp pathname={pathname} />;
}

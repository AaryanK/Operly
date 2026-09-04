import { useEffect, useState } from "react";

import { ProviderAuthPage } from "../auth/ProviderAuthPage";
import { LegalPage } from "../legal/LegalPage";
import { MinimalApp } from "../minimal/MinimalApp";
import { PublicApp } from "../public/PublicApp";
import { WorkspaceSafeApp } from "../workspace-lite/WorkspaceSafeApp";

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

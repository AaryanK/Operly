import { useEffect, useState } from "react";

import { LegalPage } from "../legal/LegalPage";
import { MinimalApp } from "../minimal/MinimalApp";
import { WorkspaceLiteApp } from "../workspace-lite/WorkspaceLiteApp";

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

  if (pathname === "/privacy") return <LegalPage kind="privacy" />;
  if (pathname === "/terms") return <LegalPage kind="terms" />;
  if (
    pathname === "/account"
    || pathname === "/personal"
    || pathname === "/app"
    || pathname === "/channels"
    || pathname.startsWith("/channels/")
  ) {
    return <WorkspaceLiteApp pathname={pathname} />;
  }
  return <MinimalApp pathname={pathname} />;
}

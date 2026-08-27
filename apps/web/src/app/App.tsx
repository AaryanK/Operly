import { useEffect, useState } from "react";

import { AdminPage } from "../admin/AdminPage";
import { LegalPage } from "../legal/LegalPage";
import { PublicApp } from "../public/PublicApp";
import { ProductApp } from "./ProductApp";

function currentPath() { return window.location.pathname.replace(/\/+$/, "") || "/"; }

export function App() {
  const [pathname, setPathname] = useState(currentPath);
  useEffect(() => {
    const sync = () => setPathname(currentPath());
    window.addEventListener("popstate", sync);
    return () => window.removeEventListener("popstate", sync);
  }, []);

  if (pathname === "/admin") return <AdminPage />;
  if (pathname === "/privacy") return <LegalPage kind="privacy" />;
  if (pathname === "/terms") return <LegalPage kind="terms" />;
  if (pathname === "/channels" || pathname.startsWith("/channels/")) return <ProductApp />;
  return <PublicApp pathname={pathname} />;
}

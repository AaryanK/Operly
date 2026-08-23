import { useEffect, useState } from "react";

import { OperlyRoute, parseRoute } from "./routes";

export function useRoute(): OperlyRoute {
  const [route, setRoute] = useState<OperlyRoute>(() => parseRoute(window.location.pathname));

  useEffect(() => {
    const update = () => setRoute(parseRoute(window.location.pathname));
    window.addEventListener("popstate", update);
    return () => window.removeEventListener("popstate", update);
  }, []);

  return route;
}

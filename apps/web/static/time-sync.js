(() => {
  function cookie(name) {
    return document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${name}=`))
      ?.slice(name.length + 1) || "";
  }

  async function syncTimezone() {
    if (!document.body || !document.querySelector("#dashboard")) return;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!timezone) return;
    const cacheKey = "operly:last-timezone";
    if (localStorage.getItem(cacheKey) === timezone) return;
    const csrf = decodeURIComponent(cookie("__Host-operly_csrf") || cookie("operly_csrf"));
    if (!csrf) return;
    try {
      const response = await fetch("/api/identities/preferences/timezone", {
        method: "PUT",
        credentials: "same-origin",
        headers: {
          "Content-Type": "application/json",
          "X-CSRF-Token": csrf,
        },
        body: JSON.stringify({ timezone }),
      });
      if (response.ok) localStorage.setItem(cacheKey, timezone);
    } catch (_) {
      // Timezone sync is opportunistic; normal app behavior must never depend on it.
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncTimezone, { once: true });
  } else {
    syncTimezone();
  }
})();

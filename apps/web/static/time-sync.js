(() => {
  const cacheKey = "operly:last-timezone";
  const retryKey = "operly:timezone-retry";
  let retryTimer = null;
  let inFlight = false;

  function cookie(name) {
    return document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${name}=`))
      ?.slice(name.length + 1) || "";
  }

  function emitFailure(status, retryable) {
    document.dispatchEvent(new CustomEvent("operly:timezone-sync-error", {
      detail: { status, retryable },
    }));
  }

  function scheduleRetry(delayMs) {
    if (retryTimer) return;
    localStorage.setItem(retryKey, String(Date.now() + delayMs));
    retryTimer = window.setTimeout(() => {
      retryTimer = null;
      localStorage.removeItem(retryKey);
      syncTimezone();
    }, delayMs);
  }

  async function syncTimezone() {
    if (inFlight || !document.body || !document.querySelector("#dashboard")) return;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!timezone) return;
    if (localStorage.getItem(cacheKey) === timezone) return;

    const retryAt = Number(localStorage.getItem(retryKey) || "0");
    if (retryAt > Date.now()) {
      scheduleRetry(Math.max(250, retryAt - Date.now()));
      return;
    }

    const csrf = decodeURIComponent(cookie("__Host-operly_csrf") || cookie("operly_csrf"));
    if (!csrf) return;

    inFlight = true;
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

      if (response.ok) {
        localStorage.setItem(cacheKey, timezone);
        localStorage.removeItem(retryKey);
        return;
      }

      localStorage.removeItem(cacheKey);
      const retryable = response.status === 408 || response.status === 429 || response.status >= 500;
      if (response.status !== 401 && response.status !== 403) {
        emitFailure(response.status, retryable);
      }
      if (retryable) scheduleRetry(5000);
    } catch (_) {
      localStorage.removeItem(cacheKey);
      emitFailure(0, true);
      scheduleRetry(5000);
    } finally {
      inFlight = false;
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", syncTimezone, { once: true });
  } else {
    syncTimezone();
  }
})();

(() => {
  const cacheKey = "operly:last-timezone";
  const retryKey = "operly:timezone-retry";
  const rejectedKey = "operly:timezone-rejected:v2";
  const rejectionTtlMs = 6 * 60 * 60 * 1000;
  let retryTimer = null;
  let inFlight = false;

  function cookie(name) {
    return document.cookie
      .split(";")
      .map((part) => part.trim())
      .find((part) => part.startsWith(`${name}=`))
      ?.slice(name.length + 1) || "";
  }

  function rejected(timezone) {
    try {
      const value = JSON.parse(localStorage.getItem(rejectedKey) || "null");
      if (!value || value.timezone !== timezone || Number(value.until || 0) <= Date.now()) {
        if (value) localStorage.removeItem(rejectedKey);
        return false;
      }
      return true;
    } catch {
      localStorage.removeItem(rejectedKey);
      return false;
    }
  }

  function rememberRejection(timezone, status, reason) {
    localStorage.setItem(rejectedKey, JSON.stringify({
      timezone,
      status,
      reason: String(reason || "rejected").slice(0, 400),
      until: Date.now() + rejectionTtlMs,
    }));
  }

  function emitFailure(status, retryable, reason="") {
    document.dispatchEvent(new CustomEvent("operly:timezone-sync-error", {
      detail: { status, retryable, reason: String(reason || "").slice(0, 400) },
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

  async function responseReason(response) {
    try {
      const payload = await response.json();
      const detail = payload?.detail ?? payload;
      if (typeof detail === "string") return detail;
      return detail?.message || detail?.code || "rejected";
    } catch {
      return `HTTP ${response.status}`;
    }
  }

  async function syncTimezone() {
    if (inFlight || !document.body || !document.querySelector("#dashboard")) return;
    const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if (!timezone) return;
    if (localStorage.getItem(cacheKey) === timezone) return;
    if (rejected(timezone)) return;

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
        localStorage.removeItem(rejectedKey);
        return;
      }

      localStorage.removeItem(cacheKey);
      const retryable = response.status === 408 || response.status === 429 || response.status >= 500;
      const reason = await responseReason(response);
      if (response.status !== 401 && response.status !== 403) {
        emitFailure(response.status, retryable, reason);
      }
      if (retryable) {
        scheduleRetry(5000);
      } else if (response.status !== 401 && response.status !== 403) {
        rememberRejection(timezone, response.status, reason);
      }
    } catch (_) {
      localStorage.removeItem(cacheKey);
      emitFailure(0, true, "network_error");
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

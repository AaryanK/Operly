(() => {
  let scheduled = false;
  let running = false;

  const number = value => Number(value || 0);
  const money = value => {
    try {
      return new Intl.NumberFormat(undefined, {style: "currency", currency: "USD", maximumFractionDigits: 0}).format(number(value));
    } catch {
      return `$${number(value).toLocaleString()}`;
    }
  };

  function setIndicator(row, state) {
    const dot = row?.querySelector("i");
    if (!dot) return;
    dot.classList.remove("ok", "warn", "neutral");
    dot.classList.add(state);
  }

  async function repairOperationsSemantics() {
    if (running) return;
    if (document.querySelector("#page-title")?.textContent?.trim() !== "Operations") return;
    const panel = document.querySelector(".op-attention-panel");
    const metricGrid = document.querySelector(".op-metric-grid");
    const pulse = document.querySelector(".op-pulse-list");
    if (!panel || !metricGrid || !pulse || typeof window.api !== "function" && typeof api !== "function") return;

    const request = typeof api === "function" ? api : window.api;
    running = true;
    try {
      const [snapshot, alerts] = await Promise.all([request("/operations/snapshot"), request("/operations/alerts")]);
      const counts = snapshot?.counts || {};
      const activeAlerts = (Array.isArray(alerts) ? alerts : []).filter(item => !item?.resolved && item?.status !== "resolved");

      const attentionMetric = metricGrid.querySelector(".op-metric");
      if (attentionMetric) {
        const strong = attentionMetric.querySelector("strong");
        const small = attentionMetric.querySelector("small");
        if (strong) strong.textContent = String(activeAlerts.length);
        if (small) small.textContent = activeAlerts.length ? `${activeAlerts.length} active operational ${activeAlerts.length === 1 ? "alert" : "alerts"}` : "No active operational alerts";
        attentionMetric.classList.toggle("attention", activeAlerts.length > 0);
        attentionMetric.classList.toggle("healthy", activeAlerts.length === 0);
      }

      const rows = [...pulse.children];
      const sales = rows[0], execution = rows[1], inventory = rows[2], governance = rows[3];
      const salesText = sales?.querySelector("strong");
      const executionText = execution?.querySelector("strong");
      const inventoryText = inventory?.querySelector("strong");
      const governanceText = governance?.querySelector("strong");

      if (salesText) {
        if (number(counts.stale_leads) > 0) {
          salesText.textContent = `${number(counts.stale_leads)} stale ${number(counts.stale_leads) === 1 ? "lead" : "leads"}`;
          setIndicator(sales, "warn");
        } else if (number(snapshot?.pipeline_value) > 0) {
          salesText.textContent = `${money(snapshot.pipeline_value)} open pipeline`;
          setIndicator(sales, "ok");
        } else {
          salesText.textContent = "No active pipeline";
          setIndicator(sales, "neutral");
        }
      }

      if (executionText) {
        if (number(counts.overdue_tasks) > 0) {
          executionText.textContent = `${number(counts.overdue_tasks)} overdue ${number(counts.overdue_tasks) === 1 ? "task" : "tasks"}`;
          setIndicator(execution, "warn");
        } else if (number(counts.open_tasks) > 0) {
          executionText.textContent = `${number(counts.open_tasks)} open ${number(counts.open_tasks) === 1 ? "task" : "tasks"}`;
          setIndicator(execution, "neutral");
        } else {
          executionText.textContent = "No open tasks";
          setIndicator(execution, "ok");
        }
      }

      if (inventoryText) {
        if (number(counts.low_stock) > 0) {
          inventoryText.textContent = `${number(counts.low_stock)} low-stock ${number(counts.low_stock) === 1 ? "item" : "items"}`;
          setIndicator(inventory, "warn");
        } else if (number(counts.catalog_items) > 0) {
          inventoryText.textContent = `${number(counts.catalog_items)} catalog ${number(counts.catalog_items) === 1 ? "item" : "items"}`;
          setIndicator(inventory, "ok");
        } else {
          inventoryText.textContent = "Catalog not configured";
          setIndicator(inventory, "warn");
        }
      }

      if (governanceText) {
        if (number(counts.pending_approvals) > 0) {
          governanceText.textContent = `${number(counts.pending_approvals)} waiting`;
          setIndicator(governance, "warn");
        } else {
          governanceText.textContent = "Clear";
          setIndicator(governance, "ok");
        }
      }
    } catch (error) {
      console.warn("Operly semantic UI repair skipped", error);
    } finally {
      running = false;
    }
  }

  function schedule() {
    if (scheduled) return;
    scheduled = true;
    requestAnimationFrame(() => {
      scheduled = false;
      repairOperationsSemantics();
    });
  }

  new MutationObserver(schedule).observe(document.documentElement, {subtree: true, childList: true});
  document.addEventListener("click", event => {
    if (event.target.closest('[data-shell-page="operations"]')) setTimeout(schedule, 0);
  }, true);
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", schedule, {once: true});
  else schedule();
})();

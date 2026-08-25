(() => {
  const usageState = {
    range: "24h",
    data: null,
    loading: false,
  };

  const number = (value) => Number(value || 0);
  const compact = (value) => new Intl.NumberFormat(undefined, {
    notation: Math.abs(number(value)) >= 10000 ? "compact" : "standard",
    maximumFractionDigits: 1,
  }).format(number(value));
  const exact = (value) => new Intl.NumberFormat().format(number(value));

  function metric(id, value) {
    const node = document.getElementById(id);
    if (!node) return;
    node.textContent = compact(value);
    node.title = exact(value);
  }

  function bucketLabel(value, range) {
    if (!value) return "";
    const date = new Date(value.length === 10 ? `${value}T00:00:00Z` : `${value}Z`);
    if (Number.isNaN(date.getTime())) return value;
    if (range === "1h") {
      return new Intl.DateTimeFormat(undefined, { hour: "numeric", minute: "2-digit" }).format(date);
    }
    if (range === "24h") {
      return new Intl.DateTimeFormat(undefined, { hour: "numeric" }).format(date);
    }
    return new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" }).format(date);
  }

  function renderChart(rows, range) {
    const target = document.getElementById("ai-usage-chart");
    const data = Array.isArray(rows) ? rows : [];
    if (!target) return;
    if (!data.length || !data.some((row) => number(row.total_tokens) > 0)) {
      target.innerHTML = '<div class="usage-empty">No token-bearing model calls in this range yet.</div>';
      return;
    }

    const width = 920;
    const height = 270;
    const left = 58;
    const right = 18;
    const top = 18;
    const bottom = 42;
    const innerWidth = width - left - right;
    const innerHeight = height - top - bottom;
    const maxValue = Math.max(1, ...data.map((row) => number(row.total_tokens)));
    const slot = innerWidth / Math.max(1, data.length);
    const barWidth = Math.max(3, Math.min(34, slot * 0.68));

    const grid = [0, 0.5, 1].map((fraction) => {
      const y = top + innerHeight - (fraction * innerHeight);
      const label = compact(maxValue * fraction);
      return `<line class="usage-grid-line" x1="${left}" y1="${y}" x2="${width - right}" y2="${y}"></line><text class="usage-axis-label" x="${left - 10}" y="${y + 4}" text-anchor="end">${label}</text>`;
    }).join("");

    const bars = data.map((row, index) => {
      const value = number(row.total_tokens);
      const barHeight = (value / maxValue) * innerHeight;
      const x = left + (index * slot) + ((slot - barWidth) / 2);
      const y = top + innerHeight - barHeight;
      const label = bucketLabel(row.bucket, range);
      return `<rect class="usage-bar" x="${x.toFixed(2)}" y="${y.toFixed(2)}" width="${barWidth.toFixed(2)}" height="${Math.max(1, barHeight).toFixed(2)}" rx="${Math.min(5, barWidth / 3).toFixed(2)}"><title>${label}: ${exact(value)} tokens</title></rect>`;
    }).join("");

    const labelCount = Math.min(6, data.length);
    const labelIndexes = new Set();
    for (let index = 0; index < labelCount; index += 1) {
      labelIndexes.add(Math.round((index / Math.max(1, labelCount - 1)) * (data.length - 1)));
    }
    const labels = [...labelIndexes].map((index) => {
      const x = left + (index * slot) + (slot / 2);
      return `<text class="usage-axis-label" x="${x.toFixed(2)}" y="${height - 12}" text-anchor="middle">${bucketLabel(data[index].bucket, range)}</text>`;
    }).join("");

    target.innerHTML = `<svg viewBox="0 0 ${width} ${height}" role="img" aria-label="Token usage over time">${grid}${bars}${labels}</svg>`;
  }

  function renderModels(rows, totalTokens) {
    const target = document.getElementById("ai-usage-model-rows");
    if (!target) return;
    const models = Array.isArray(rows) ? rows : [];
    if (!models.length) {
      target.innerHTML = '<div class="usage-empty">No model usage has been recorded yet.</div>';
      return;
    }

    target.innerHTML = models.map((row) => {
      const share = totalTokens ? Math.max(0, Math.min(100, number(row.share_percent))) : 0;
      const tracked = number(row.tracked_calls);
      const calls = number(row.calls);
      return `<div class="usage-model-row">
        <div class="usage-model-name"><strong title="${escapeHtml(row.model)}">${escapeHtml(row.model)}</strong><small>${escapeHtml(row.provider)} · ${exact(calls)} calls${tracked !== calls ? ` · ${exact(tracked)} token-tracked` : ""}</small></div>
        <span title="${exact(row.input_tokens)} input tokens">${compact(row.input_tokens)}</span>
        <span title="${exact(row.output_tokens)} output tokens">${compact(row.output_tokens)}</span>
        <strong title="${exact(row.total_tokens)} total tokens">${compact(row.total_tokens)}</strong>
        <div class="usage-share"><span>${share.toFixed(1)}%</span><i><b style="width:${share.toFixed(2)}%"></b></i></div>
      </div>`;
    }).join("");
  }

  function render(data) {
    usageState.data = data;
    const totals = data?.totals || {};
    metric("ai-total-tokens", totals.total_tokens);
    metric("ai-input-tokens", totals.input_tokens);
    metric("ai-output-tokens", totals.output_tokens);
    metric("ai-tracked-calls", totals.tracked_calls);
    metric("ai-model-count", data?.models);

    const coverage = document.getElementById("ai-usage-coverage");
    if (coverage) {
      const tracked = number(data?.coverage?.tracked_calls);
      const calls = number(data?.coverage?.calls);
      const percent = number(data?.coverage?.percent);
      coverage.textContent = calls
        ? `${percent.toFixed(0)}% token coverage · ${exact(tracked)} of ${exact(calls)} successful calls`
        : "Waiting for model calls";
    }

    renderChart(data?.series || [], data?.range || usageState.range);
    renderModels(data?.by_model || [], number(totals.total_tokens));

    const updated = document.getElementById("generated-at");
    if (updated && data?.generated_at) {
      updated.textContent = `Updated ${formatDate(data.generated_at, true)}`;
    }
  }

  function showError(message) {
    const target = document.getElementById("ai-usage-error");
    if (!target) return;
    target.textContent = message || "AI usage could not be loaded.";
    target.classList.remove("hidden");
  }

  async function loadUsage(force = false) {
    if (usageState.loading) return;
    if (usageState.data && !force && usageState.data.range === usageState.range) {
      render(usageState.data);
      return;
    }
    usageState.loading = true;
    document.getElementById("ai-usage-error")?.classList.add("hidden");
    try {
      const data = await api(`/api/admin/ai-usage?range=${encodeURIComponent(usageState.range)}`);
      render(data);
    } catch (error) {
      showError(error.message || "AI usage could not be loaded.");
    } finally {
      usageState.loading = false;
    }
  }

  document.querySelector('[data-admin-tab="ai-usage"]')?.addEventListener("click", () => {
    const title = document.getElementById("admin-page-title");
    if (title) title.textContent = "AI Usage";
    loadUsage().catch(() => {});
  });

  document.querySelectorAll("[data-usage-range]").forEach((button) => {
    button.addEventListener("click", () => {
      usageState.range = button.dataset.usageRange || "24h";
      document.querySelectorAll("[data-usage-range]").forEach((item) => {
        item.classList.toggle("active", item === button);
      });
      loadUsage(true).catch(() => {});
    });
  });

  document.getElementById("admin-refresh")?.addEventListener("click", () => {
    const section = document.getElementById("admin-tab-ai-usage");
    if (section && !section.classList.contains("hidden")) {
      loadUsage(true).catch(() => {});
    }
  });
})();

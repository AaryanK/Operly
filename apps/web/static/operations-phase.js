(function () {
  function esc(value = "") {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[character]);
  }

  function date(value) {
    if (!value) return "—";
    return new Intl.DateTimeFormat(undefined, {
      month: "short",
      day: "numeric",
      hour: "numeric",
      minute: "2-digit"
    }).format(new Date(value));
  }

  function lines(value) {
    return String(value || "")
      .split("\n")
      .map((item) => item.trim())
      .filter(Boolean);
  }

  function empty(text) {
    return `<div class="empty">${esc(text)}</div>`;
  }

  async function induction() {
    const [profile, sources] = await Promise.all([
      api("/operations/profile"),
      api("/operations/sources")
    ]);

    const complete = profile.induction_status === "complete";

    document.querySelector("#content").innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Business induction</span><h2>Teach OPERLY how this business works</h2></div>
      </div>

      <div class="induction-status ${complete ? "" : "pending"}">
        <strong>${complete ? "Business model ready" : "Business induction incomplete"}</strong>
        <span>${complete ? "OPERLY can use this profile across every channel." : "Complete the core identity, description and industry."}</span>
      </div>

      <div class="induction-grid">
        <form id="induction-form" class="panel ops-form">
          <div class="form-grid">
            <label>Trading name<input id="ind-trading" value="${esc(profile.trading_name)}" required></label>
            <label>Legal name<input id="ind-legal" value="${esc(profile.legal_name)}"></label>
            <label>Industry<input id="ind-industry" value="${esc(profile.industry)}" required></label>
            <label>Country<input id="ind-country" value="${esc(profile.country)}"></label>
            <label>Currency<input id="ind-currency" value="${esc(profile.currency)}"></label>
            <label>Timezone<input id="ind-timezone" value="${esc(profile.timezone)}"></label>
            <label class="wide">Business description<textarea id="ind-description" required>${esc(profile.description)}</textarea></label>
            <label class="wide">Main goals, one per line<textarea id="ind-goals">${esc((profile.goals || []).join("\n"))}</textarea></label>
            <label class="wide">Current pain points, one per line<textarea id="ind-pains">${esc((profile.pain_points || []).join("\n"))}</textarea></label>
            <label class="wide">Approval rules, one per line<textarea id="ind-approvals">${esc((profile.approval_rules || []).join("\n"))}</textarea></label>
            <label>Communication tone<input id="ind-tone" value="${esc(profile.communication_tone)}"></label>
          </div>
          <button class="button primary">Save business model</button>
        </form>

        <section class="panel">
          <div class="panel-header"><h3>Business source material</h3></div>
          <form id="source-form" class="compact-form">
            <input id="source-title" placeholder="Source title" required>
            <select id="source-type">
              <option value="text">Text</option>
              <option value="policy">Policy</option>
              <option value="catalog">Catalog</option>
              <option value="sop">SOP</option>
              <option value="faq">FAQ</option>
            </select>
            <textarea id="source-content" placeholder="Paste business information here…" required></textarea>
            <button class="button primary">Add source</button>
          </form>
          <div class="source-list">
            ${sources.map((source) => `
              <div class="source-row">
                <div><strong>${esc(source.title)}</strong><p>${esc(source.source_type)} · ${date(source.created_at)}</p></div>
                <span class="pill status connected">${esc(source.status)}</span>
              </div>
            `).join("") || empty("No imported business sources yet.")}
          </div>
        </section>
      </div>
    `;

    document.querySelector("#induction-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/operations/profile", {
        method: "PUT",
        body: JSON.stringify({
          trading_name: document.querySelector("#ind-trading").value,
          legal_name: document.querySelector("#ind-legal").value,
          industry: document.querySelector("#ind-industry").value,
          country: document.querySelector("#ind-country").value,
          currency: document.querySelector("#ind-currency").value,
          timezone: document.querySelector("#ind-timezone").value,
          description: document.querySelector("#ind-description").value,
          goals: lines(document.querySelector("#ind-goals").value),
          pain_points: lines(document.querySelector("#ind-pains").value),
          approval_rules: lines(document.querySelector("#ind-approvals").value),
          communication_tone: document.querySelector("#ind-tone").value,
          operating_hours: {}
        })
      });
      await induction();
    });

    document.querySelector("#source-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/operations/sources", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#source-title").value,
          source_type: document.querySelector("#source-type").value,
          content: document.querySelector("#source-content").value
        })
      });
      await induction();
    });
  }

  async function operationsCenter() {
    const [snapshot, alerts] = await Promise.all([
      api("/operations/snapshot"),
      api("/operations/alerts")
    ]);

    const counts = snapshot.counts;

    document.querySelector("#content").innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">AI operations center</span><h2>What needs attention now</h2></div>
        <div>
          <button id="run-scan" class="button secondary">Run scan</button>
          <button id="generate-brief" class="button primary">Generate owner brief</button>
        </div>
      </div>

      <div class="ops-summary">
        <article class="stat ${counts.overdue_tasks ? "alert" : ""}"><b>${counts.overdue_tasks}</b><span>Overdue tasks</span></article>
        <article class="stat ${counts.stale_leads ? "alert" : ""}"><b>${counts.stale_leads}</b><span>Stalled leads</span></article>
        <article class="stat ${counts.low_stock ? "alert" : ""}"><b>${counts.low_stock}</b><span>Low-stock products</span></article>
        <article class="stat ${counts.pending_approvals ? "alert" : ""}"><b>${counts.pending_approvals}</b><span>Pending approvals</span></article>
      </div>

      <section id="brief-container" class="brief-card hidden">
        <span class="kicker">OPERLY owner brief</span>
        <p id="brief-text"></p>
      </section>

      <div class="alert-list">
        ${alerts.map((alert, index) => `
          <article class="card alert-card ${esc(alert.severity)}">
            <div class="alert-rank">${index + 1}</div>
            <div>
              <span class="pill status ${alert.severity === "high" || alert.severity === "critical" ? "pending" : ""}">${esc(alert.severity)}</span>
              <h3>${esc(alert.title)}</h3>
              <p>${esc(alert.description)}</p>
              <div class="alert-action"><strong>Recommended:</strong> ${esc(alert.recommended_action)}</div>
            </div>
            <button class="button secondary" data-resolve-alert="${alert.id}">Resolve</button>
          </article>
        `).join("") || `<div class="panel">${empty("No active operational exceptions. Run a scan to refresh.")}</div>`}
      </div>
    `;

    document.querySelector("#run-scan").addEventListener("click", async () => {
      await api("/operations/scan", { method: "POST" });
      await operationsCenter();
    });

    document.querySelector("#generate-brief").addEventListener("click", async () => {
      const button = document.querySelector("#generate-brief");
      button.disabled = true;
      button.textContent = "Analyzing…";
      try {
        const result = await api("/operations/brief", { method: "POST" });
        document.querySelector("#brief-text").textContent = result.brief;
        document.querySelector("#brief-container").classList.remove("hidden");
      } finally {
        button.disabled = false;
        button.textContent = "Generate owner brief";
      }
    });

    document.querySelectorAll("[data-resolve-alert]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/operations/alerts/${button.dataset.resolveAlert}/resolve`, {
          method: "PATCH"
        });
        await operationsCenter();
      });
    });
  }

  async function audit() {
    const current = await api("/operations/audit/latest");

    document.querySelector("#content").innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">AI business audit</span><h2>Business health and improvement opportunities</h2></div>
        <button id="run-audit" class="button primary">${current ? "Run new audit" : "Run first audit"}</button>
      </div>

      ${current ? `
        <section class="panel audit-hero">
          <div class="audit-score" style="--score:${current.score}"><b>${current.score}</b></div>
          <div><span class="kicker green">Health score</span><h2>${esc(current.executive_summary)}</h2><p>Generated ${date(current.created_at)}</p></div>
          <span class="pill status connected">${esc(current.status)}</span>
        </section>

        <div class="finding-grid">
          ${current.findings.map((finding) => `
            <article class="card finding-card">
              <span class="pill status ${finding.severity === "high" || finding.severity === "critical" ? "pending" : ""}">${esc(finding.severity)} · ${esc(finding.category)}</span>
              <h3>${esc(finding.title)}</h3>
              <p><b>Evidence:</b> ${esc(finding.evidence)}</p>
              <strong>Recommendation</strong>
              <p>${esc(finding.recommendation)}</p>
              <strong>Expected impact</strong>
              <p>${esc(finding.expected_impact)}</p>
              ${finding.requires_approval ? `<span class="pill status pending">Owner approval required</span>` : ""}
            </article>
          `).join("")}
        </div>
      ` : `<div class="panel">${empty("No audit exists yet. Complete induction and run the first audit.")}</div>`}
    `;

    document.querySelector("#run-audit").addEventListener("click", async () => {
      const button = document.querySelector("#run-audit");
      button.disabled = true;
      button.textContent = "Auditing…";
      try {
        await api("/operations/audit/run", { method: "POST" });
        await audit();
      } finally {
        button.disabled = false;
      }
    });
  }

  function renderPlanCanvas(plan) {
    if (!plan) {
      return `<div class="panel">${empty("No operating plan exists yet.")}</div>`;
    }

    const nodes = Object.fromEntries(plan.nodes.map((node) => [node.key, node]));
    const svgLines = plan.edges.map((edge) => {
      const source = nodes[edge.source];
      const target = nodes[edge.target];
      if (!source || !target) return "";
      const x1 = source.x + 7;
      const x2 = target.x - 7;
      const y1 = source.y;
      const y2 = target.y;
      const tx = (x1 + x2) / 2;
      const ty = (y1 + y2) / 2 - 1;
      return `<line x1="${x1}%" y1="${y1}%" x2="${x2}%" y2="${y2}%"></line>
              ${edge.label ? `<text x="${tx}%" y="${ty}%">${esc(edge.label)}</text>` : ""}`;
    }).join("");

    const cards = plan.nodes.map((node) => `
      <article class="plan-node ${esc(node.type)} ${node.enabled ? "" : "disabled"}"
               style="left:${node.x}%;top:${node.y}%">
        <span class="node-type">${esc(node.type.replace("_", " "))}</span>
        <h3>${esc(node.title)}</h3>
        <p>${esc(node.description)}</p>
        <div class="node-controls">
          <button data-toggle-node="${node.id}" data-enabled="${node.enabled}">${node.enabled ? "Disable" : "Enable"}</button>
          <button data-approval-node="${node.id}" data-approval="${node.approval_required}">${node.approval_required ? "Approval on" : "Require approval"}</button>
        </div>
      </article>
    `).join("");

    return `
      <div class="plan-meta">
        <div><span class="kicker green">Version ${plan.version}</span><h2>${esc(plan.name)}</h2><p>${esc(plan.goal)}</p></div>
        <div>
          <span class="pill status ${plan.status === "approved" ? "connected" : "pending"}">${esc(plan.status)}</span>
          ${plan.status === "draft" ? `<button id="approve-plan" class="button primary">Approve plan</button>` : ""}
        </div>
      </div>
      <div class="plan-canvas">
        <svg class="plan-lines" viewBox="0 0 100 100" preserveAspectRatio="none">${svgLines}</svg>
        ${cards}
      </div>
    `;
  }

  async function operatingPlan() {
    const plan = await api("/operations/plans/latest");

    document.querySelector("#content").innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Visual operating plan</span><h2>Design how the business should run</h2></div>
      </div>

      <form id="plan-form" class="plan-toolbar">
        <input id="plan-goal" placeholder="Example: Turn every travel inquiry into an approved quotation and follow-up." required>
        <button class="button primary">Generate plan</button>
      </form>

      <div id="plan-output">${renderPlanCanvas(plan)}</div>
    `;

    document.querySelector("#plan-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      const button = event.target.querySelector("button");
      button.disabled = true;
      button.textContent = "Designing…";
      try {
        await api("/operations/plans/generate", {
          method: "POST",
          body: JSON.stringify({
            goal: document.querySelector("#plan-goal").value
          })
        });
        await operatingPlan();
      } finally {
        button.disabled = false;
      }
    });

    if (!plan) return;

    const approve = document.querySelector("#approve-plan");
    if (approve) {
      approve.addEventListener("click", async () => {
        await api(`/operations/plans/${plan.id}/approve`, { method: "POST" });
        await operatingPlan();
      });
    }

    document.querySelectorAll("[data-toggle-node]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/operations/plans/nodes/${button.dataset.toggleNode}`, {
          method: "PATCH",
          body: JSON.stringify({
            enabled: button.dataset.enabled !== "true"
          })
        });
        await operatingPlan();
      });
    });

    document.querySelectorAll("[data-approval-node]").forEach((button) => {
      button.addEventListener("click", async () => {
        await api(`/operations/plans/nodes/${button.dataset.approvalNode}`, {
          method: "PATCH",
          body: JSON.stringify({
            approval_required: button.dataset.approval !== "true"
          })
        });
        await operatingPlan();
      });
    });
  }

  window.operlyOperationsPages = {
    induction,
    operationsCenter,
    audit,
    operatingPlan
  };

  window.operlyOperationsTitles = {
    induction: "Business induction",
    operationsCenter: "Operations center",
    audit: "Business audit",
    operatingPlan: "Operating plan"
  };
})();

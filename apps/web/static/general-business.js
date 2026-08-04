(function () {
  const originalRenderPage = window.renderPage;

  const pageTitles = {
    crm: "CRM",
    catalog: "Catalog & inventory",
    sales: "Sales",
    calendar: "Calendar",
    team: "Team & documents",
    reports: "Business report"
  };

  function escapeHtml(value = "") {
    return String(value).replace(/[&<>"']/g, (character) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;"
    })[character]);
  }

  function money(value) {
    return Number(value || 0).toLocaleString(undefined, {
      minimumFractionDigits: 2,
      maximumFractionDigits: 2
    });
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

  function empty(text) {
    return `<div class="empty">${escapeHtml(text)}</div>`;
  }

  async function crm() {
    const [contacts, leads] = await Promise.all([
      api("/business/contacts"),
      api("/business/leads")
    ]);

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Customer operations</span><h2>Contacts and sales pipeline</h2></div>
      </div>

      <div class="business-columns">
        <section class="panel">
          <div class="panel-header"><h3>Contacts</h3></div>
          <form id="contact-form" class="compact-form">
            <input id="contact-name" placeholder="Customer name" required>
            <input id="contact-phone" placeholder="Phone">
            <input id="contact-email" type="email" placeholder="Email">
            <button class="button primary">Add contact</button>
          </form>
          <div class="list">
            ${contacts.map((contact) => `
              <div class="business-row">
                <div><strong>${escapeHtml(contact.name)}</strong>
                <p>${escapeHtml(contact.company || contact.phone || contact.email || "No contact details")}</p></div>
                <span class="pill">${escapeHtml(contact.source)}</span>
              </div>
            `).join("") || empty("No contacts yet.")}
          </div>
        </section>

        <section class="panel">
          <div class="panel-header"><h3>Lead pipeline</h3></div>
          <form id="lead-form" class="compact-form">
            <input id="lead-title" placeholder="Opportunity title" required>
            <input id="lead-value" type="number" min="0" step="0.01" placeholder="Value">
            <select id="lead-stage">
              <option value="new">New</option>
              <option value="qualified">Qualified</option>
              <option value="proposal">Proposal</option>
              <option value="won">Won</option>
            </select>
            <button class="button primary">Add lead</button>
          </form>
          <div class="pipeline">
            ${leads.map((lead) => `
              <article>
                <div><strong>${escapeHtml(lead.title)}</strong><p>${money(lead.value)}</p></div>
                <select data-lead-stage="${lead.id}">
                  ${["new","qualified","proposal","won","lost"].map((stage) =>
                    `<option value="${stage}" ${lead.stage === stage ? "selected" : ""}>${stage}</option>`
                  ).join("")}
                </select>
              </article>
            `).join("") || empty("No active leads.")}
          </div>
        </section>
      </div>
    `;

    document.querySelector("#contact-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/contacts", {
        method: "POST",
        body: JSON.stringify({
          name: document.querySelector("#contact-name").value,
          phone: document.querySelector("#contact-phone").value || null,
          email: document.querySelector("#contact-email").value || null
        })
      });
      await crm();
    });

    document.querySelector("#lead-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/leads", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#lead-title").value,
          value: Number(document.querySelector("#lead-value").value || 0),
          stage: document.querySelector("#lead-stage").value
        })
      });
      await crm();
    });

    document.querySelectorAll("[data-lead-stage]").forEach((select) => {
      select.addEventListener("change", async () => {
        await api(`/business/leads/${select.dataset.leadStage}/stage`, {
          method: "PATCH",
          body: JSON.stringify({ stage: select.value })
        });
        await crm();
      });
    });
  }

  async function catalog() {
    const items = await api("/business/catalog");

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Products and services</span><h2>Catalog and inventory</h2></div>
      </div>

      <form id="catalog-form" class="panel business-form">
        <input id="item-name" placeholder="Item name" required>
        <select id="item-type"><option value="product">Product</option><option value="service">Service</option></select>
        <input id="item-sku" placeholder="SKU">
        <input id="item-price" type="number" step="0.01" min="0" placeholder="Selling price">
        <input id="item-stock" type="number" placeholder="Initial stock">
        <input id="item-reorder" type="number" placeholder="Reorder level">
        <button class="button primary">Add item</button>
      </form>

      <div class="catalog-grid">
        ${items.map((item) => `
          <article class="card catalog-card ${item.item_type}">
            <div class="catalog-head">
              <span class="pill">${escapeHtml(item.item_type)}</span>
              ${item.item_type === "product" && item.stock_qty <= item.reorder_level
                ? `<span class="pill status pending">Low stock</span>` : ""}
            </div>
            <h3>${escapeHtml(item.name)}</h3>
            <p>${escapeHtml(item.sku || "No SKU")}</p>
            <strong>${money(item.price)}</strong>
            ${item.item_type === "product" ? `
              <div class="stock-line"><span>Stock</span><b>${item.stock_qty}</b></div>
              <form class="stock-form" data-item="${item.id}">
                <input name="quantity" type="number" placeholder="+/- quantity" required>
                <button class="button secondary">Adjust</button>
              </form>` : `<div class="stock-line"><span>Service</span><b>Available</b></div>`}
          </article>
        `).join("") || empty("No products or services yet.")}
      </div>
    `;

    document.querySelector("#catalog-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/catalog", {
        method: "POST",
        body: JSON.stringify({
          name: document.querySelector("#item-name").value,
          item_type: document.querySelector("#item-type").value,
          sku: document.querySelector("#item-sku").value || null,
          price: Number(document.querySelector("#item-price").value || 0),
          stock_qty: Number(document.querySelector("#item-stock").value || 0),
          reorder_level: Number(document.querySelector("#item-reorder").value || 0)
        })
      });
      await catalog();
    });

    document.querySelectorAll(".stock-form").forEach((form) => {
      form.addEventListener("submit", async (event) => {
        event.preventDefault();
        const quantity = Number(new FormData(form).get("quantity"));
        await api(`/business/catalog/${form.dataset.item}/inventory`, {
          method: "POST",
          body: JSON.stringify({ quantity_change: quantity, reason: "dashboard adjustment" })
        });
        await catalog();
      });
    });
  }

  async function sales() {
    const [orders, quotes] = await Promise.all([
      api("/business/orders"),
      api("/business/quotes")
    ]);

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Revenue operations</span><h2>Orders and quotations</h2></div>
      </div>

      <div class="business-columns">
        <section class="panel">
          <div class="panel-header"><h3>Orders</h3></div>
          <form id="order-form" class="compact-form">
            <input id="order-total" type="number" step="0.01" min="0" placeholder="Order total" required>
            <input id="order-notes" placeholder="Notes">
            <button class="button primary">Create order</button>
          </form>
          <div class="list">
            ${orders.map((order) => `
              <div class="business-row">
                <div><strong>Order ${order.id.slice(0,8)}</strong><p>${money(order.total)} · ${escapeHtml(order.notes || "No notes")}</p></div>
                <select data-order-status="${order.id}">
                  ${["draft","confirmed","processing","completed","cancelled"].map((status) =>
                    `<option value="${status}" ${order.status === status ? "selected" : ""}>${status}</option>`
                  ).join("")}
                </select>
              </div>
            `).join("") || empty("No orders yet.")}
          </div>
        </section>

        <section class="panel">
          <div class="panel-header"><h3>Quotations</h3></div>
          <form id="quote-form" class="compact-form">
            <input id="quote-title" placeholder="Quotation title" required>
            <input id="quote-total" type="number" step="0.01" min="0" placeholder="Total">
            <button class="button primary">Create quote</button>
          </form>
          <div class="list">
            ${quotes.map((quote) => `
              <div class="business-row">
                <div><strong>${escapeHtml(quote.title)}</strong><p>${money(quote.total)} · ${escapeHtml(quote.status)}</p></div>
                <select data-quote-status="${quote.id}">
                  ${["draft","sent","accepted","rejected","expired"].map((status) =>
                    `<option value="${status}" ${quote.status === status ? "selected" : ""}>${status}</option>`
                  ).join("")}
                </select>
              </div>
            `).join("") || empty("No quotations yet.")}
          </div>
        </section>
      </div>
    `;

    document.querySelector("#order-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/orders", {
        method: "POST",
        body: JSON.stringify({
          total: Number(document.querySelector("#order-total").value || 0),
          notes: document.querySelector("#order-notes").value
        })
      });
      await sales();
    });

    document.querySelector("#quote-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/quotes", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#quote-title").value,
          total: Number(document.querySelector("#quote-total").value || 0)
        })
      });
      await sales();
    });

    document.querySelectorAll("[data-order-status]").forEach((select) => {
      select.addEventListener("change", async () => {
        await api(`/business/orders/${select.dataset.orderStatus}/status`, {
          method: "PATCH",
          body: JSON.stringify({ status: select.value })
        });
      });
    });

    document.querySelectorAll("[data-quote-status]").forEach((select) => {
      select.addEventListener("change", async () => {
        await api(`/business/quotes/${select.dataset.quoteStatus}/status`, {
          method: "PATCH",
          body: JSON.stringify({ status: select.value })
        });
      });
    });
  }

  async function calendar() {
    const appointments = await api("/business/appointments");

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Scheduling</span><h2>Appointments and meetings</h2></div>
      </div>

      <form id="appointment-form" class="panel business-form">
        <input id="appointment-title" placeholder="Appointment title" required>
        <input id="appointment-start" type="datetime-local" required>
        <input id="appointment-end" type="datetime-local">
        <input id="appointment-assignee" placeholder="Assigned employee">
        <button class="button primary">Schedule</button>
      </form>

      <div class="appointment-list">
        ${appointments.map((appointment) => `
          <article class="card appointment-card">
            <div class="calendar-date"><b>${new Date(appointment.starts_at).getDate()}</b><span>${new Date(appointment.starts_at).toLocaleString(undefined,{month:"short"})}</span></div>
            <div><span class="pill">${escapeHtml(appointment.status)}</span><h3>${escapeHtml(appointment.title)}</h3>
            <p>${date(appointment.starts_at)} · ${escapeHtml(appointment.assigned_to || "Unassigned")}</p></div>
            <select data-appointment-status="${appointment.id}">
              ${["scheduled","confirmed","completed","cancelled","no_show"].map((status) =>
                `<option value="${status}" ${appointment.status === status ? "selected" : ""}>${status}</option>`
              ).join("")}
            </select>
          </article>
        `).join("") || empty("No appointments scheduled.")}
      </div>
    `;

    document.querySelector("#appointment-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/appointments", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#appointment-title").value,
          starts_at: new Date(document.querySelector("#appointment-start").value).toISOString(),
          ends_at: document.querySelector("#appointment-end").value
            ? new Date(document.querySelector("#appointment-end").value).toISOString() : null,
          assigned_to: document.querySelector("#appointment-assignee").value || null
        })
      });
      await calendar();
    });

    document.querySelectorAll("[data-appointment-status]").forEach((select) => {
      select.addEventListener("change", async () => {
        await api(`/business/appointments/${select.dataset.appointmentStatus}/status`, {
          method: "PATCH",
          body: JSON.stringify({ status: select.value })
        });
      });
    });
  }

  async function team() {
    const [members, documents] = await Promise.all([
      api("/business/team"),
      api("/business/documents")
    ]);

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">People and knowledge</span><h2>Team and documents</h2></div>
      </div>

      <div class="business-columns">
        <section class="panel">
          <div class="panel-header"><h3>Team members</h3></div>
          <form id="team-form" class="compact-form">
            <input id="team-name" placeholder="Name" required>
            <input id="team-email" type="email" placeholder="Email">
            <input id="team-role" placeholder="Role" value="employee">
            <button class="button primary">Add member</button>
          </form>
          <div class="team-grid">
            ${members.map((member) => `
              <article><span>${escapeHtml(member.name.slice(0,1))}</span><div><strong>${escapeHtml(member.name)}</strong><p>${escapeHtml(member.role)}</p></div></article>
            `).join("") || empty("No team members yet.")}
          </div>
        </section>

        <section class="panel">
          <div class="panel-header"><h3>Business documents</h3></div>
          <form id="document-form" class="compact-form">
            <input id="document-title" placeholder="Document title" required>
            <select id="document-type"><option value="note">Note</option><option value="policy">Policy</option><option value="sop">SOP</option><option value="template">Template</option></select>
            <textarea id="document-content" placeholder="Document content"></textarea>
            <button class="button primary">Create document</button>
          </form>
          <div class="list">
            ${documents.map((document) => `
              <div class="business-row"><div><strong>${escapeHtml(document.title)}</strong><p>${escapeHtml(document.content.slice(0,120) || "Empty document")}</p></div><span class="pill">${escapeHtml(document.document_type)}</span></div>
            `).join("") || empty("No documents yet.")}
          </div>
        </section>
      </div>
    `;

    document.querySelector("#team-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/team", {
        method: "POST",
        body: JSON.stringify({
          name: document.querySelector("#team-name").value,
          email: document.querySelector("#team-email").value || null,
          role: document.querySelector("#team-role").value || "employee"
        })
      });
      await team();
    });

    document.querySelector("#document-form").addEventListener("submit", async (event) => {
      event.preventDefault();
      await api("/business/documents", {
        method: "POST",
        body: JSON.stringify({
          title: document.querySelector("#document-title").value,
          document_type: document.querySelector("#document-type").value,
          content: document.querySelector("#document-content").value
        })
      });
      await team();
    });
  }

  async function reports() {
    const [summary, activity] = await Promise.all([
      api("/business/summary"),
      api("/business/activity")
    ]);

    content.innerHTML = `
      <div class="page-head">
        <div><span class="kicker green">Owner visibility</span><h2>Business report</h2></div>
      </div>

      <div class="stats business-stats">
        <article class="stat"><b>${summary.contacts}</b><span>Contacts</span></article>
        <article class="stat"><b>${summary.open_leads}</b><span>Open leads</span></article>
        <article class="stat"><b>${money(summary.pipeline_value)}</b><span>Pipeline value</span></article>
        <article class="stat"><b>${summary.low_stock}</b><span>Low-stock items</span></article>
        <article class="stat"><b>${summary.open_orders}</b><span>Open orders</span></article>
        <article class="stat"><b>${summary.upcoming_appointments}</b><span>Appointments</span></article>
      </div>

      <section class="panel">
        <div class="panel-header"><h3>Business activity</h3></div>
        <div class="list">
          ${activity.map((event) => `
            <div class="business-row">
              <div><strong>${escapeHtml(event.summary)}</strong><p>${escapeHtml(event.actor)} · ${escapeHtml(event.entity_type)}</p></div>
              <time>${date(event.created_at)}</time>
            </div>
          `).join("") || empty("Business activity will appear here.")}
        </div>
      </section>
    `;
  }

  window.operlyBusinessPages = { crm, catalog, sales, calendar, team, reports };
  window.operlyBusinessTitles = pageTitles;
})();

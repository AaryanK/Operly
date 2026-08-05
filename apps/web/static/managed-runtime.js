(() => {
  const configNode = document.querySelector("#operly-runtime-config");
  if (!configNode) return;

  const config = JSON.parse(configNode.textContent);
  const csrfToken = () => decodeURIComponent(
    (document.cookie.match(/(?:^|; )operly_csrf=([^;]*)/) || [])[1] || "",
  );
  const displayValue = (value) => String(value ?? "");

  async function loadTable(definition) {
    const root = document.querySelector(
      `[data-table-id="${CSS.escape(definition.componentId)}"]`,
    );
    if (!root) return;
    const state = root.querySelector(".table-state");
    const table = root.querySelector("table");
    const body = root.querySelector("tbody");
    state.textContent = "Loading...";
    try {
      const response = await fetch(
        `${config.recordBase}/${encodeURIComponent(definition.entityId)}/records?limit=50`,
        { credentials: "same-origin" },
      );
      if (!response.ok) throw new Error("Records could not be loaded.");
      const payload = await response.json();
      body.replaceChildren();
      payload.records.forEach((record) => {
        const row = document.createElement("tr");
        definition.columns.forEach((column) => {
          const cell = document.createElement("td");
          cell.textContent = displayValue(record.data[column]);
          row.append(cell);
        });
        body.append(row);
      });
      table.hidden = !payload.records.length;
      state.textContent = payload.records.length ? "" : "No records yet.";
    } catch (error) {
      table.hidden = true;
      state.textContent = error.message;
    }
  }

  async function loadTables() {
    await Promise.all(config.tables.map(loadTable));
  }

  document.querySelectorAll(".managed-form").forEach((form) => {
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (config.studio) {
        form.querySelector(".form-general").textContent =
          "Preview forms do not persist records. Apply and open the application to submit.";
        return;
      }
      const button = form.querySelector('[type="submit"]');
      if (button.disabled) return;
      button.disabled = true;
      form.querySelectorAll(".field-error").forEach((node) => { node.textContent = ""; });
      form.querySelector(".form-general").textContent = "";
      form.querySelector(".form-success").textContent = "";
      const data = {};
      new FormData(form).forEach((value, key) => { data[key] = value; });
      try {
        const response = await fetch(
          `${config.recordBase}/${encodeURIComponent(form.dataset.entityId)}/records`,
          {
            method: "POST",
            credentials: "same-origin",
            headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken() },
            body: JSON.stringify({
              data,
              formId: form.dataset.formId,
              versionId: config.versionId,
              idempotencyKey: crypto.randomUUID(),
            }),
          },
        );
        const payload = await response.json();
        if (!response.ok) {
          if (payload.detail?.errors) {
            payload.detail.errors.forEach((error) => {
              const node = form.querySelector(
                `[data-error-for="${CSS.escape(error.field)}"]`,
              );
              if (node) node.textContent = error.message;
            });
          }
          const message = payload.detail?.code === "record_validation_failed"
            ? "Please correct the highlighted fields."
            : String(payload.detail || "Record could not be saved.");
          throw new Error(message);
        }
        form.reset();
        form.querySelector(".form-success").textContent = "Saved successfully.";
        await loadTables();
      } catch (error) {
        form.querySelector(".form-general").textContent = error.message;
      } finally {
        button.disabled = false;
      }
    });
  });

  loadTables();

  if (config.studio) {
    document.addEventListener("click", (event) => {
      const node = event.target.closest("[data-operly-component-id]");
      if (!node) return;
      if (!event.shiftKey) {
        document.querySelectorAll(".selected").forEach((item) => item.classList.remove("selected"));
      }
      node.classList.add("selected");
      parent.postMessage({
        type: "OPERLY_SELECT",
        id: node.dataset.operlyComponentId,
        componentType: node.dataset.operlyComponentType,
        pageId: node.dataset.operlyPageId,
        routeId: node.dataset.operlyRouteId,
        entityId: node.dataset.operlyEntityId,
        fieldId: node.dataset.operlyFieldId,
        multi: event.shiftKey,
      }, location.origin);
    }, true);
  }
})();

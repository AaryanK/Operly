document.addEventListener("click", async (event) => {
  const button = event.target.closest("[data-page]");
  if (!button || !window.operlyOperationsPages) return;

  const page = button.dataset.page;
  const renderer = window.operlyOperationsPages[page];
  if (!renderer) return;

  event.stopImmediatePropagation();

  document.querySelectorAll("#nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === page);
  });

  document.querySelector("#page-title").textContent =
    window.operlyOperationsTitles[page] || page;

  try {
    await renderer();
  } catch (error) {
    document.querySelector("#content").innerHTML =
      `<div class="error">${String(error.message || error)}</div>`;
  }
}, true);

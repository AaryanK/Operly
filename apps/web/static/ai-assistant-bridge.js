document.addEventListener("click", async (event) => {
  const button = event.target.closest('[data-page="assistant"]');
  if (!button || !window.renderOperlyAssistant) return;

  event.stopImmediatePropagation();

  document.querySelectorAll("#nav button").forEach((item) => {
    item.classList.toggle("active", item.dataset.page === "assistant");
  });

  document.querySelector("#page-title").textContent = "OPERLY AI";

  try {
    await window.renderOperlyAssistant();
  } catch (error) {
    document.querySelector("#content").innerHTML =
      `<div class="error">${String(error.message || error)}</div>`;
  }
}, true);

document.addEventListener("click", async (event) => {
  const button = event.target.closest('[data-page="studio"]');
  if (!button || !window.operlyStudio) return;
  event.stopImmediatePropagation();
  document.querySelector("#operly-chat-dock")?.classList.add("page-suppressed");
  document.querySelectorAll("#nav button").forEach((item) => item.classList.toggle("active", item === button));
  document.querySelector("#page-title").textContent = "Build";
  try { await window.operlyStudio(); }
  catch (error) { document.querySelector("#content").textContent = error.message; }
}, true);

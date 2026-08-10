(() => {
  const previous = window.drawSynthesizedSoftwarePlan;
  if (typeof previous !== "function") return;

  function label(value) {
    return String(value || "dynamic capability graph")
      .replaceAll("_", " ")
      .replace(/\b\w/g, ch => ch.toUpperCase());
  }

  window.drawSynthesizedSoftwarePlan = function graphAwarePlanDraw(...args) {
    const result = previous.apply(this, args);
    const plan = typeof customSoftwareState !== "undefined" ? customSoftwareState.plan?.plan : null;
    const architecture = plan?.primaryArchitecture || "dynamic_capability_graph";

    document.querySelectorAll(".plan-fact").forEach(node => {
      if (node.textContent?.startsWith("Planning path:")) {
        node.textContent = `Planning path: ${label(architecture)}`;
      }
    });

    if (architecture === "dynamic_capability_graph") {
      document.querySelectorAll(".plan-section h3, #content h3").forEach(node => {
        if (node.textContent?.trim() === "Recursive plan tree") {
          node.textContent = "Capability graph";
        }
      });
    }
    return result;
  };
})();

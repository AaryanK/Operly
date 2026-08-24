(() => {
  const FEATURES = "camera; microphone; geolocation; payment; usb";

  function apply() {
    const frame = document.querySelector("iframe#ss-frame");
    if (!frame) return;
    if (frame.getAttribute("allow") !== FEATURES) frame.setAttribute("allow", FEATURES);
  }

  const observer = new MutationObserver(apply);
  observer.observe(document.documentElement, { childList: true, subtree: true });
  document.addEventListener("DOMContentLoaded", apply, { once: true });
  apply();
})();

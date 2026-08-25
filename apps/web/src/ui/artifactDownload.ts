const ARTIFACT_PATH = /^\/api\/(?:personal\/)?artifacts\/[^/]+\/download(?:\?.*)?$/;

function filenameFromChip(anchor: HTMLAnchorElement): string {
  const label = anchor.querySelector(".artifact-copy strong")?.textContent?.trim();
  return label || "operly-artifact";
}

async function forceDownload(anchor: HTMLAnchorElement) {
  const url = new URL(anchor.href, window.location.origin);
  if (url.origin !== window.location.origin || !ARTIFACT_PATH.test(url.pathname + url.search)) return;

  const response = await fetch(url.pathname + url.search, {
    method: "GET",
    credentials: "same-origin",
    headers: { Accept: "application/octet-stream,*/*" },
  });
  if (!response.ok) throw new Error(`Artifact download failed (${response.status})`);

  const blob = await response.blob();
  const objectUrl = URL.createObjectURL(blob);
  const download = document.createElement("a");
  download.href = objectUrl;
  download.download = filenameFromChip(anchor);
  download.rel = "noopener";
  download.style.display = "none";
  document.body.appendChild(download);
  download.click();
  download.remove();
  window.setTimeout(() => URL.revokeObjectURL(objectUrl), 30_000);
}

let installed = false;

export function installArtifactDownloadHandling() {
  if (installed || typeof document === "undefined") return;
  installed = true;

  document.addEventListener("click", (event) => {
    const target = event.target;
    if (!(target instanceof Element)) return;
    const anchor = target.closest("a.artifact-chip");
    if (!(anchor instanceof HTMLAnchorElement)) return;

    const url = new URL(anchor.href, window.location.origin);
    if (url.origin !== window.location.origin || !ARTIFACT_PATH.test(url.pathname + url.search)) return;

    event.preventDefault();
    event.stopPropagation();
    anchor.setAttribute("aria-busy", "true");
    forceDownload(anchor)
      .catch((error) => {
        console.error("OPERLY artifact download failed", error);
        window.location.assign(url.pathname + url.search);
      })
      .finally(() => anchor.removeAttribute("aria-busy"));
  });
}

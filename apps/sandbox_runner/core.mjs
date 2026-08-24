import crypto from "node:crypto";

export const MAX_FILES = 100;
export const MAX_BYTES = 2_000_000;
const SAFE_PATH = /^[A-Za-z0-9][A-Za-z0-9._/-]*$/;

export function stableJson(value) {
  if (Array.isArray(value)) return `[${value.map(stableJson).join(",")}]`;
  if (value && typeof value === "object") {
    const keys = Object.keys(value).sort();
    return `{${keys.map((key) => `${JSON.stringify(key)}:${stableJson(value[key])}`).join(",")}}`;
  }
  return JSON.stringify(value);
}

export function sha256Hex(value) {
  return crypto.createHash("sha256").update(value).digest("hex");
}

export function hmacHex(token, value) {
  return crypto.createHmac("sha256", token).update(value).digest("hex");
}

export function safeEqual(left, right) {
  const a = Buffer.from(String(left || ""));
  const b = Buffer.from(String(right || ""));
  if (a.length !== b.length) return false;
  return crypto.timingSafeEqual(a, b);
}

export function normalizedPath(path) {
  if (
    typeof path !== "string" ||
    !path ||
    path.startsWith("/") ||
    path.includes("\\") ||
    path.startsWith(".") ||
    path.includes("/../") ||
    path.startsWith("../") ||
    path.endsWith("/..") ||
    /^[A-Za-z]:/.test(path) ||
    !SAFE_PATH.test(path)
  ) {
    throw new Error("Bundle paths must be safe relative POSIX paths");
  }
  const parts = path.split("/");
  if (parts.some((part) => !part || part === "." || part === "..")) {
    throw new Error("Bundle path traversal is forbidden");
  }
  return path;
}

export function rebuildBundle(submission, rawBundle) {
  if (!rawBundle || typeof rawBundle !== "object") throw new Error("bundle must be an object");
  if (!rawBundle.manifest || typeof rawBundle.manifest !== "object") {
    throw new Error("bundle manifest is required");
  }
  if (!Array.isArray(rawBundle.files)) throw new Error("bundle files are required");
  if (rawBundle.files.length > MAX_FILES) throw new Error("Bundle file-count limit exceeded");

  const seen = new Set();
  const rows = [];
  const clean = [];
  let totalBytes = 0;
  for (const raw of rawBundle.files) {
    if (!raw || typeof raw !== "object") throw new Error("bundle file entries must be objects");
    const path = normalizedPath(raw.path);
    if (seen.has(path)) throw new Error("Duplicate bundle path");
    seen.add(path);
    if (typeof raw.content !== "string") throw new Error("bundle file content must be UTF-8 text");
    const content = Buffer.from(raw.content, "utf8");
    totalBytes += content.length;
    if (totalBytes > MAX_BYTES) throw new Error("Bundle size limit exceeded");
    if (content.includes(Buffer.from("BEGIN PRIVATE KEY")) || content.includes(Buffer.from("OPERLY_SANDBOX_RUNNER_TOKEN"))) {
      throw new Error("Secrets are forbidden in source bundles");
    }
    const generatedBy = String(raw.generatedBy || "coding_harness");
    rows.push({
      path,
      bytes: content.length,
      digest: `sha256:${sha256Hex(content)}`,
      generatedBy,
    });
    clean.push({ path, content, generatedBy });
  }
  rows.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));
  clean.sort((a, b) => (a.path < b.path ? -1 : a.path > b.path ? 1 : 0));

  const source = rawBundle.manifest;
  const required = ["workspaceId", "applicationId", "planId", "planVersion", "sourceVersion", "promptDigest"];
  for (const key of required) {
    if (!(key in source)) throw new Error("bundle manifest is incomplete");
  }
  if (String(source.workspaceId) !== String(submission.workspaceId)) throw new Error("bundle workspace does not match submission");
  if (String(source.applicationId) !== String(submission.applicationId)) throw new Error("bundle application does not match submission");
  if (Number(source.planVersion) !== Number(submission.planVersion)) throw new Error("bundle plan version does not match submission");
  if (Number(source.sourceVersion) !== Number(submission.sourceVersion)) throw new Error("bundle source version does not match submission");

  const manifest = {
    schemaVersion: 1,
    workspaceId: String(submission.workspaceId),
    applicationId: String(submission.applicationId),
    planId: String(source.planId),
    planVersion: Number(submission.planVersion),
    sourceVersion: Number(submission.sourceVersion),
    promptDigest: String(source.promptDigest),
    files: rows,
    totalBytes,
  };
  if (stableJson(manifest) !== stableJson(source)) throw new Error("source bundle manifest mismatch");
  const digest = `sha256:${sha256Hex(Buffer.from(stableJson(manifest)))}`;
  if (digest !== submission.sourceBundleDigest) throw new Error("source bundle digest mismatch");
  return { files: clean, manifest, digest };
}

function fileMap(bundle) {
  return new Map(bundle.files.map((item) => [item.path, item.content.toString("utf8")]));
}

export function validateFullstackSource(submission, bundle) {
  if (!submission || typeof submission !== "object") throw new Error("submission is required");
  if (submission.stackId !== "operly-fullstack-v1" || Number(submission.stackVersion) !== 1) {
    throw new Error("production runner only accepts operly-fullstack-v1 profile version 1");
  }
  if (!Array.isArray(submission.operations)) throw new Error("submission operations are required");
  for (const required of ["stage_source", "static_analysis", "build", "test", "start", "health_check", "acceptance_test"]) {
    if (!submission.operations.includes(required)) throw new Error(`required operation is missing: ${required}`);
  }
  if (!submission.healthCheck || typeof submission.healthCheck.path !== "string") throw new Error("health check is required");
  if (!/^\/[A-Za-z0-9_./-]*$/.test(submission.healthCheck.path)) throw new Error("health path is invalid");

  const files = fileMap(bundle);
  const raw = files.get("operly.solution.json");
  if (!raw) throw new Error("Missing operly.solution.json");
  let manifest;
  try {
    manifest = JSON.parse(raw);
  } catch {
    throw new Error("operly.solution.json must contain JSON");
  }
  if (manifest.schemaVersion !== "operly.solution/v1" || manifest.runtime !== "operly-fullstack-v1" || Number(manifest.runtimeVersion) !== 1) {
    throw new Error("full-stack manifest runtime contract mismatch");
  }
  const layout = manifest.layout || {};
  const expectedLayout = { frontend: "frontend", backend: "backend", workers: "workers", tests: "tests", migrations: "migrations" };
  for (const [key, value] of Object.entries(expectedLayout)) {
    if ((layout[key] ?? value) !== value) throw new Error("full-stack layout must be canonical");
  }
  const execution = manifest.execution || {};
  if (!["static", "npm-build"].includes(execution.frontend || "static")) throw new Error("unsupported frontend execution mode");
  if ((execution.backend || "python-cli") !== "python-cli") throw new Error("unsupported backend execution mode");
  if (!["none", "python-cli"].includes(execution.worker || "none")) throw new Error("unsupported worker execution mode");
  if (!files.has("backend/app.py")) throw new Error("operly-fullstack-v1 requires backend/app.py");
  if ((execution.worker || "none") === "python-cli" && !files.has("workers/worker.py")) {
    throw new Error("python worker mode requires workers/worker.py");
  }
  if (![...files.keys()].some((path) => path.startsWith("frontend/"))) throw new Error("frontend source is required");
  if (![...files.keys()].some((path) => path.startsWith("tests/") && /\.(py|js|mjs|cjs)$/.test(path))) {
    throw new Error("executable tests are required");
  }
  if ((execution.frontend || "static") === "static" && !files.has("frontend/index.html")) {
    throw new Error("static frontend requires frontend/index.html");
  }
  if ((execution.frontend || "static") === "npm-build" && (!files.has("frontend/package.json") || !files.has("frontend/package-lock.json"))) {
    throw new Error("npm-build frontend requires package.json and package-lock.json");
  }

  const deps = Array.isArray(submission.dependencies) ? submission.dependencies : [];
  const pyDeps = deps.filter((item) => item.ecosystem === "python");
  const npmDeps = deps.filter((item) => item.ecosystem === "npm");
  if (pyDeps.length && !files.has("backend/requirements.lock")) throw new Error("Python dependencies require backend/requirements.lock");
  if (npmDeps.length && !files.has("frontend/package-lock.json")) throw new Error("npm dependencies require frontend/package-lock.json");
  if (pyDeps.length) {
    const lock = files.get("backend/requirements.lock");
    for (const line of lock.split(/\r?\n/).map((x) => x.trim()).filter((x) => x && !x.startsWith("#"))) {
      if (!/^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}==[A-Za-z0-9][A-Za-z0-9_.+!-]{0,79}$/.test(line)) {
        throw new Error("Python lockfile contains a non-registry exact pin");
      }
    }
  }
  if (npmDeps.length) {
    let lock;
    try { lock = JSON.parse(files.get("frontend/package-lock.json")); } catch { throw new Error("npm lockfile must contain JSON"); }
    if (![2, 3].includes(lock.lockfileVersion) || !lock.packages || typeof lock.packages !== "object") {
      throw new Error("npm lockfile contract is invalid");
    }
    for (const [path, record] of Object.entries(lock.packages)) {
      if (!path || !record || typeof record !== "object") continue;
      if (record.link) throw new Error("local npm links are forbidden");
      if (record.resolved) {
        const url = new URL(record.resolved);
        if (url.protocol !== "https:" || url.hostname !== "registry.npmjs.org") {
          throw new Error("npm lockfile must resolve through registry.npmjs.org");
        }
      }
    }
  }
  return manifest;
}

export function shellQuote(value) {
  return `'${String(value).replaceAll("'", `'\"'\"'`)}'`;
}

export function publicBaseUrl(value) {
  const parsed = new URL(value);
  if (parsed.protocol !== "https:" || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("runner public base URL must be a clean HTTPS origin");
  }
  return `${parsed.protocol}//${parsed.host}`.replace(/\/$/, "");
}

export function bindingTarget(value, allowedHosts) {
  const parsed = new URL(value);
  if (!["https:", "http:"].includes(parsed.protocol) || parsed.username || parsed.password || parsed.search || parsed.hash) {
    throw new Error("binding target is invalid");
  }
  const host = parsed.hostname.toLowerCase().replace(/\.$/, "");
  if (!allowedHosts.has(host)) throw new Error("binding target host is not allowlisted");
  if (parsed.protocol !== "https:" && process.env.OPERLY_RUNNER_ALLOW_HTTP_BINDINGS !== "1") {
    throw new Error("binding target must use HTTPS");
  }
  return value.replace(/\/$/, "");
}

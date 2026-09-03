import crypto from "node:crypto";
import fs from "node:fs/promises";
import http from "node:http";
import path from "node:path";
import { fileURLToPath } from "node:url";
import { Sandbox } from "railway";

const here = path.dirname(fileURLToPath(import.meta.url));
const TOOL_HELPER = await fs.readFile(path.join(here, "computer_tool.py"));
const PORT = Math.max(1, Math.min(Number(process.env.PORT || 3000), 65535));
const RUNNER_TOKEN = String(process.env.OPERLY_RUNNER_TOKEN || "").trim();
const ENVIRONMENT_ID = String(process.env.RAILWAY_ENVIRONMENT_ID || "").trim();
const MAX_REQUEST_BYTES = 30 * 1024 * 1024;
const MAX_RESPONSE_BYTES = 25 * 1024 * 1024;
const SESSION_RE = /^[A-Za-z0-9_-]{4,200}$/;
const TOOL_RE = /^[a-z][a-z0-9_.-]{1,120}$/;
const PYTHON = "/opt/operly-py/bin/python";
const WORKSPACE = "/workspace/work";
const REQUESTS = "/workspace/.operly/requests";
const EXEC_PROXY_NOT_READY = "tcp-proxy exec WebSocket connection failed.";

const TOOL_IDS = Object.freeze([
  "terminal.exec", "python.exec",
  "files.list", "files.read", "files.write", "files.mkdir", "files.remove", "files.move", "files.search",
  "process.list", "process.kill",
  "git.status", "git.diff", "git.exec",
  "web.fetch", "web.download",
  "browser.open", "browser.navigate", "browser.snapshot", "browser.click", "browser.type", "browser.press", "browser.evaluate", "browser.screenshot", "browser.close",
  "artifact.import", "artifact.export", "environment.info",
]);
const TOOL_SET = new Set(TOOL_IDS);

let cachedTemplate = null;
let templateWarmPromise = null;
let templateWarmState = "idle";
let templateWarmStartedAt = null;
let templateWarmedAt = null;
let templateWarmLastError = null;

function computerTemplate() {
  if (cachedTemplate) return cachedTemplate;
  cachedTemplate = Sandbox.template()
    .withPackages(
      "bash", "build-essential", "ca-certificates", "chromium", "curl", "ffmpeg", "file", "git",
      "iptables", "jq", "nodejs", "npm", "poppler-utils", "procps", "python3", "python3-pip",
      "python3-venv", "ripgrep", "unzip", "wget", "zip",
    )
    .run("python3 -m venv /opt/operly-py")
    .run(
      "/opt/operly-py/bin/pip install --no-cache-dir " +
      "beautifulsoup4 duckdb httpx imageio lxml matplotlib numpy odfpy openpyxl pandas Pillow " +
      "playwright polars pyarrow PyMuPDF pypdf python-docx python-pptx PyYAML reportlab requests scipy soundfile",
    )
    .run("id -u operly >/dev/null 2>&1 || useradd -m -u 10001 -s /bin/bash operly")
    .run("mkdir -p /workspace/work /workspace/.operly/requests /workspace/.operly/processes && chown -R operly:operly /workspace");
  return cachedTemplate;
}

function templateWarmStatus() {
  return {
    state: templateWarmState,
    ready: templateWarmState === "ready",
    started_at: templateWarmStartedAt,
    warmed_at: templateWarmedAt,
    last_error: templateWarmLastError ? "template build failed" : null,
  };
}

function ensureComputerTemplateWarm() {
  if (templateWarmState === "ready") return Promise.resolve();
  if (templateWarmPromise) return templateWarmPromise;

  templateWarmState = "warming";
  templateWarmStartedAt = new Date().toISOString();
  templateWarmLastError = null;
  const started = Date.now();
  console.log("Agent Computer template warm started");

  templateWarmPromise = computerTemplate()
    .build({ environmentId: ENVIRONMENT_ID })
    .then(() => {
      templateWarmState = "ready";
      templateWarmedAt = new Date().toISOString();
      templateWarmLastError = null;
      console.log(`Agent Computer template ready in ${Date.now() - started}ms`);
    })
    .catch((error) => {
      templateWarmState = "failed";
      templateWarmLastError = String(error?.message || error).slice(0, 1000);
      console.error(`Agent Computer template warm failed after ${Date.now() - started}ms: ${templateWarmLastError}`);
      throw error;
    })
    .finally(() => {
      templateWarmPromise = null;
    });

  return templateWarmPromise;
}

function hmac(value) {
  return crypto.createHmac("sha256", RUNNER_TOKEN).update(value).digest("hex");
}

function safeEqual(left, right) {
  const a = Buffer.from(String(left || ""));
  const b = Buffer.from(String(right || ""));
  return a.length === b.length && crypto.timingSafeEqual(a, b);
}

function canonical(method, pathname, raw) {
  return Buffer.concat([Buffer.from(`${method.toUpperCase()}\n${pathname}\n`, "utf8"), raw]);
}

async function readBody(req, limit = MAX_REQUEST_BYTES) {
  const chunks = [];
  let total = 0;
  for await (const chunk of req) {
    total += chunk.length;
    if (total > limit) throw Object.assign(new Error("request body too large"), { statusCode: 413 });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function authenticate(req, pathname, raw) {
  if (!RUNNER_TOKEN) throw Object.assign(new Error("runner token is not configured"), { statusCode: 503 });
  const auth = String(req.headers.authorization || "");
  if (!safeEqual(auth, `Bearer ${RUNNER_TOKEN}`)) throw Object.assign(new Error("invalid runner authorization"), { statusCode: 401 });
  const supplied = String(req.headers["x-operly-signature"] || "");
  const expected = hmac(canonical(req.method || "GET", pathname, raw));
  if (!supplied || !safeEqual(supplied, expected)) throw Object.assign(new Error("invalid runner request signature"), { statusCode: 401 });
}

function sendJson(res, status, value, { signed = true } = {}) {
  const body = Buffer.from(JSON.stringify(value));
  if (body.length > MAX_RESPONSE_BYTES) return sendJson(res, 500, { detail: "runner response exceeded policy" }, { signed });
  res.statusCode = status;
  res.setHeader("Content-Type", "application/json; charset=utf-8");
  res.setHeader("Content-Length", String(body.length));
  res.setHeader("Cache-Control", "no-store");
  if (signed && RUNNER_TOKEN) res.setHeader("X-Operly-Signature", hmac(body));
  res.end(body);
}

function cleanId(value) {
  const clean = String(value || "").trim();
  if (!SESSION_RE.test(clean)) throw Object.assign(new Error("invalid Computer runtime id"), { statusCode: 422 });
  return clean;
}

function cleanTool(value) {
  const clean = String(value || "").trim();
  if (!TOOL_RE.test(clean) || !TOOL_SET.has(clean)) throw Object.assign(new Error("unknown Computer tool"), { statusCode: 404 });
  return clean;
}

function cleanCreate(payload) {
  const clientSessionId = String(payload.client_session_id || payload.clientSessionId || "").trim();
  const workspaceId = String(payload.workspace_id || payload.workspaceId || "").trim();
  const principalId = String(payload.principal_id || payload.principalId || "").trim();
  if (!clientSessionId || !workspaceId || !principalId) throw new Error("Computer session scope is required");
  const profile = String(payload.profile || "general");
  if (!["general", "coding", "data", "browser"].includes(profile)) throw new Error("invalid Computer profile");
  const networkPolicy = String(payload.network_policy || payload.networkPolicy || "web");
  if (!["off", "web"].includes(networkPolicy)) throw new Error("network policy must be off or web");
  const ttlSeconds = Math.max(60, Math.min(Number(payload.ttl_seconds || payload.ttlSeconds || 7200), 21600));
  return { clientSessionId, workspaceId, principalId, profile, networkPolicy, ttlSeconds };
}

async function connect(runtimeId) {
  if (!ENVIRONMENT_ID) throw Object.assign(new Error("RAILWAY_ENVIRONMENT_ID is required"), { statusCode: 503 });
  const box = await Sandbox.connect(cleanId(runtimeId), { environmentId: ENVIRONMENT_ID });
  await box.refresh();
  return box;
}

function isExecProxyNotReady(error) {
  return String(error?.message || error) === EXEC_PROXY_NOT_READY;
}

async function bootstrapOperation(label, operation) {
  for (let attempt = 1; attempt <= 5; attempt += 1) {
    try {
      return await operation();
    } catch (error) {
      if (!isExecProxyNotReady(error) || attempt >= 5) throw error;
      const delayMs = 400 * attempt;
      console.warn(`Agent Computer bootstrap ${label} waiting for exec proxy (attempt ${attempt}/5, ${delayMs}ms)`);
      await new Promise((resolve) => setTimeout(resolve, delayMs));
    }
  }
  throw new Error(`Agent Computer bootstrap ${label} retries exhausted`);
}

async function writeHelper(box) {
  await bootstrapOperation("write-helper", () => box.files.write("/opt/operly-computer-tool.py", TOOL_HELPER, { mode: 0o555 }));
  await bootstrapOperation("prepare-workspace", () => box.exec("mkdir -p /workspace/work /workspace/.operly/requests /workspace/.operly/processes && chown -R operly:operly /workspace", { timeoutSec: 20 }));
}

async function harden(box, networkPolicy) {
  const rules = [
    "iptables -C OUTPUT -m owner --uid-owner 10001 -d 169.254.0.0/16 -j REJECT 2>/dev/null || iptables -A OUTPUT -m owner --uid-owner 10001 -d 169.254.0.0/16 -j REJECT",
    "ip6tables -C OUTPUT -m owner --uid-owner 10001 -d fe80::/10 -j REJECT 2>/dev/null || ip6tables -A OUTPUT -m owner --uid-owner 10001 -d fe80::/10 -j REJECT",
  ];
  if (networkPolicy === "off") {
    rules.push(
      "iptables -C OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT 2>/dev/null || iptables -A OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT",
      "ip6tables -C OUTPUT -m owner --uid-owner 10001 ! -d ::1/128 -j REJECT 2>/dev/null || ip6tables -A OUTPUT -m owner --uid-owner 10001 ! -d ::1/128 -j REJECT",
    );
  }
  for (const rule of rules) {
    try { await bootstrapOperation("network-harden", () => box.exec(rule, { timeoutSec: 10 })); } catch {}
  }
}

async function createSession(payload) {
  if (!ENVIRONMENT_ID) throw Object.assign(new Error("RAILWAY_ENVIRONMENT_ID is required"), { statusCode: 503 });
  const request = cleanCreate(payload);
  const started = Date.now();

  // Build the content-addressed base image once and let all later Computer
  // sessions fork the Railway-cached template instead of building it in the
  // user's request path.
  await ensureComputerTemplateWarm();

  const options = {
    environmentId: ENVIRONMENT_ID,
    idleTimeoutMinutes: Math.max(1, Math.min(Math.ceil(request.ttlSeconds / 60), 360)),
    networkIsolation: "ISOLATED",
  };
  const box = await Sandbox.create(computerTemplate(), options);
  try {
    await writeHelper(box);
    const meta = Buffer.from(JSON.stringify({
      clientSessionId: request.clientSessionId,
      workspaceId: request.workspaceId,
      principalId: request.principalId,
      profile: request.profile,
      networkPolicy: request.networkPolicy,
      createdAt: new Date().toISOString(),
      expiresAt: new Date(Date.now() + request.ttlSeconds * 1000).toISOString(),
    }));
    await bootstrapOperation("write-session-meta", () => box.files.write("/workspace/.operly/session.json", meta, { mode: 0o600 }));
    await bootstrapOperation("chown-session-meta", () => box.exec("chown operly:operly /workspace/.operly/session.json /opt/operly-computer-tool.py", { timeoutSec: 10 }));
    await harden(box, request.networkPolicy);
    console.log(`Agent Computer session ${String(box.id || "").slice(0, 24)} ready in ${Date.now() - started}ms`);
    return {
      id: String(box.id || ""), session_id: String(box.id || ""), state: "active",
      isolation: "railway_sandbox_vm_v2", provider: "railway-sandbox",
      profile: request.profile, network_policy: request.networkPolicy, private_network: false, tools: TOOL_IDS,
    };
  } catch (error) {
    try { await box.destroy(); } catch {}
    throw error;
  }
}

async function statusSession(runtimeId) {
  try {
    const box = await connect(runtimeId);
    let meta = {};
    try {
      const bytes = Buffer.from(await box.files.read("/workspace/.operly/session.json", { format: "bytes" }));
      meta = JSON.parse(bytes.toString("utf8"));
    } catch {}
    const rawStatus = String(box.status || "RUNNING");
    return {
      id: cleanId(runtimeId), session_id: cleanId(runtimeId), state: rawStatus === "RUNNING" ? "active" : rawStatus.toLowerCase(),
      isolation: "railway_sandbox_vm_v2", provider: "railway-sandbox", private_network: false,
      profile: meta.profile || null, network_policy: meta.networkPolicy || null, tools: TOOL_IDS,
    };
  } catch {
    return { id: cleanId(runtimeId), session_id: cleanId(runtimeId), state: "expired", expired: true };
  }
}

async function destroySession(runtimeId) {
  const clean = cleanId(runtimeId);
  try {
    const box = await Sandbox.connect(clean, { environmentId: ENVIRONMENT_ID });
    await box.destroy();
    return { id: clean, session_id: clean, state: "stopped", destroyed: true, expired: false };
  } catch {
    return { id: clean, session_id: clean, state: "stopped", destroyed: false, expired: true };
  }
}

function shellQuote(value) { return `'${String(value).replaceAll("'", `'"'"'`)}'`; }

async function executeTool(runtimeId, toolId, args) {
  const box = await connect(runtimeId);
  if (String(box.status || "") !== "RUNNING") throw Object.assign(new Error("Computer runtime is not running"), { statusCode: 409 });
  const tool = cleanTool(toolId);
  const requestId = crypto.randomUUID();
  const requestPath = `${REQUESTS}/${requestId}.json`;
  const raw = Buffer.from(JSON.stringify(args && typeof args === "object" ? args : {}));
  if (raw.length > MAX_REQUEST_BYTES) throw Object.assign(new Error("tool arguments exceed policy"), { statusCode: 413 });
  await box.files.write(requestPath, raw, { mode: 0o600 });
  await box.exec(`chown operly:operly ${shellQuote(requestPath)}`, { timeoutSec: 10 });
  const requestedTimeout = Number(args?.timeout_seconds || 120);
  const timeoutSec = Math.max(20, Math.min(requestedTimeout + 30, 930));
  let result;
  try {
    result = await box.exec(
      `su -s /bin/bash operly -c ${shellQuote(`${PYTHON} /opt/operly-computer-tool.py ${tool} ${requestPath}`)}`,
      { cwd: WORKSPACE, timeoutSec },
    );
  } finally {
    try { await box.exec(`rm -f ${shellQuote(requestPath)}`, { timeoutSec: 5 }); } catch {}
  }
  const stdout = String(result.stdout || "").trim();
  let packet;
  try { packet = JSON.parse(stdout); }
  catch { throw Object.assign(new Error("Computer sandbox returned invalid tool output"), { statusCode: 502 }); }
  if (!packet || typeof packet !== "object") throw Object.assign(new Error("invalid Computer tool response"), { statusCode: 502 });
  if (!packet.ok) {
    return {
      runtime_state: "active", ok: false,
      error: String(packet.error || "Computer tool failed"), error_type: String(packet.error_type || "ToolError"),
      runner_exit_code: result.exitCode, runner_stderr: String(result.stderr || "").slice(-12000),
    };
  }
  return { runtime_state: "active", ok: true, ...packet.result };
}

function parseJson(raw) {
  if (!raw.length) return {};
  try {
    const value = JSON.parse(raw.toString("utf8"));
    return value && typeof value === "object" && !Array.isArray(value) ? value : {};
  } catch {
    throw Object.assign(new Error("invalid JSON"), { statusCode: 400 });
  }
}

const server = http.createServer(async (req, res) => {
  let pathname = "/";
  try { pathname = new URL(req.url || "/", "http://runner.invalid").pathname; } catch {}
  if (pathname === "/health" && req.method === "GET") {
    return sendJson(res, 200, {
      ok: true,
      service: "operly-sandbox-runner",
      computer_runtime: true,
      isolation: "railway-sandbox",
      private_network_default: false,
      tools: TOOL_IDS.length,
      computer_template: templateWarmStatus(),
    }, { signed: false });
  }
  if (!pathname.startsWith("/v1/computer/")) return sendJson(res, 404, { detail: "Not found" }, { signed: false });
  try {
    const raw = await readBody(req);
    authenticate(req, pathname, raw);
    const payload = parseJson(raw);
    if (pathname === "/v1/computer/sessions" && req.method === "POST") return sendJson(res, 201, await createSession(payload));
    const statusMatch = pathname.match(/^\/v1\/computer\/sessions\/([^/]+)$/);
    if (statusMatch && req.method === "GET") return sendJson(res, 200, await statusSession(statusMatch[1]));
    if (statusMatch && req.method === "DELETE") return sendJson(res, 200, await destroySession(statusMatch[1]));
    const toolMatch = pathname.match(/^\/v1\/computer\/sessions\/([^/]+)\/tools\/([^/]+)$/);
    if (toolMatch && req.method === "POST") return sendJson(res, 200, await executeTool(toolMatch[1], decodeURIComponent(toolMatch[2]), payload.arguments || {}));
    return sendJson(res, 404, { detail: "Computer endpoint not found" });
  } catch (error) {
    const status = Number(error?.statusCode || 400);
    const safeMessage = status >= 500 ? "Sandbox runner operation failed" : String(error?.message || error);
    console.error(`Sandbox runner ${req.method || "GET"} ${pathname} failed: ${String(error?.message || error).slice(0, 1000)}`);
    return sendJson(res, status, { detail: safeMessage });
  }
});

if (!RUNNER_TOKEN) { console.error("OPERLY_RUNNER_TOKEN is required"); process.exit(1); }
if (!ENVIRONMENT_ID) { console.error("RAILWAY_ENVIRONMENT_ID is required"); process.exit(1); }
server.listen(PORT, "0.0.0.0", () => {
  console.log(`Operly Sandbox Runner listening on :${PORT}`);
  void ensureComputerTemplateWarm().catch(() => {});
});

import http from "node:http";
import crypto from "node:crypto";
import path from "node:path";
import { Sandbox } from "railway";
import { hmacHex, safeEqual, shellQuote } from "./core.mjs";

const RUNNER_TOKEN = String(process.env.OPERLY_RUNNER_TOKEN || "");
const ENVIRONMENT_ID = String(process.env.RAILWAY_ENVIRONMENT_ID || "");
const MAX_REQUEST_BYTES = 30 * 1024 * 1024;
const MAX_INPUT_BYTES = 20 * 1024 * 1024;
const MAX_OUTPUT_BYTES = 20 * 1024 * 1024;
const MAX_FILES = 20;
const RUN_SCOPED_IDLE_MINUTES = 15;
const PYTHON = "/opt/operly-py/bin/python";
const SESSION_RE = /^[A-Za-z0-9_-]{4,160}$/;

function signedJson(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Content-Length", String(body.length));
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Operly-Signature", hmacHex(RUNNER_TOKEN, body));
  res.end(body);
}

async function readBody(req, limit = MAX_REQUEST_BYTES) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw Object.assign(new Error("computer request body too large"), { statusCode: 413 });
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

function authenticate(req, raw) {
  const authorization = String(req.headers.authorization || "");
  if (!safeEqual(authorization, `Bearer ${RUNNER_TOKEN}`)) {
    throw Object.assign(new Error("Invalid runner authorization"), { statusCode: 401 });
  }
  const supplied = String(req.headers["x-operly-signature"] || "");
  const expected = hmacHex(RUNNER_TOKEN, raw);
  if (!supplied || !safeEqual(supplied, expected)) {
    throw Object.assign(new Error("Invalid runner request signature"), { statusCode: 401 });
  }
}

function safeRelative(value, rootName) {
  const raw = String(value || "").replaceAll("\\", "/");
  if (!raw || raw.startsWith("/") || raw.includes("\0")) throw new Error("invalid relative path");
  const normalized = path.posix.normalize(raw);
  if (normalized === "." || normalized === ".." || normalized.startsWith("../")) throw new Error("path traversal rejected");
  return `/workspace/${rootName}/${normalized}`;
}

function cleanSessionId(value) {
  const clean = String(value || "").trim();
  if (!clean) return null;
  if (!SESSION_RE.test(clean)) throw new Error("invalid sandbox session id");
  return clean;
}

function validatePayload(payload) {
  const mode = String(payload.mode || "");
  if (!new Set(["python", "command"]).has(mode)) throw new Error("mode must be python or command");
  const inputs = Array.isArray(payload.inputs) ? payload.inputs : [];
  const outputPaths = Array.isArray(payload.outputPaths) ? payload.outputPaths : [];
  if (inputs.length > MAX_FILES || outputPaths.length > MAX_FILES) throw new Error(`maximum ${MAX_FILES} computer files`);
  let total = 0;
  const decoded = inputs.map((item, index) => {
    const bytes = Buffer.from(String(item.contentBase64 || ""), "base64");
    total += bytes.length;
    if (total > MAX_INPUT_BYTES) throw new Error("computer input bytes exceed policy");
    const filename = String(item.filename || `input-${index + 1}.bin`);
    return {
      artifactId: String(item.artifactId || ""),
      filename,
      contentType: String(item.contentType || "application/octet-stream"),
      target: safeRelative(filename, "input"),
      bytes,
    };
  });
  const outputs = outputPaths.map((item) => ({ relative: String(item), target: safeRelative(item, "output") }));
  const timeoutSeconds = Math.max(1, Math.min(Number(payload.timeoutSeconds || 120), 600));
  const sandboxId = cleanSessionId(payload.sandboxId);
  const keepAlive = Boolean(payload.keepAlive);
  if (mode === "python") {
    const code = String(payload.code || "");
    if (!code.trim() || Buffer.byteLength(code) > 250_000) throw new Error("python code is required and bounded");
    return { mode, decoded, outputs, timeoutSeconds, code, sandboxId, keepAlive };
  }
  const argv = Array.isArray(payload.argv) ? payload.argv.map((item) => String(item)) : [];
  if (!argv.length || argv.length > 64 || argv.some((item) => item.length > 2000)) throw new Error("bounded argv is required");
  return { mode, decoded, outputs, timeoutSeconds, argv, sandboxId, keepAlive };
}

let cachedTemplate = null;
function computerTemplate() {
  if (cachedTemplate) return cachedTemplate;
  cachedTemplate = Sandbox.template()
    .withPackages(
      "python3",
      "python3-pip",
      "python3-venv",
      "nodejs",
      "ffmpeg",
      "poppler-utils",
      "ca-certificates",
      "iptables",
      "libsndfile1",
    )
    .run("python3 -m venv /opt/operly-py")
    .run(
      "/opt/operly-py/bin/pip install --no-cache-dir " +
      "numpy pandas scipy matplotlib Pillow PyMuPDF pypdf openpyxl python-docx python-pptx " +
      "reportlab odfpy imageio beautifulsoup4 lxml PyYAML soundfile",
    )
    .run("id -u operly >/dev/null 2>&1 || useradd -m -u 10001 -s /bin/bash operly")
    .run("mkdir -p /workspace/input /workspace/output /workspace/work && chown -R operly:operly /workspace");
  return cachedTemplate;
}

async function createComputer() {
  if (!ENVIRONMENT_ID) throw new Error("RAILWAY_ENVIRONMENT_ID is required");
  return Sandbox.create(computerTemplate(), {
    environmentId: ENVIRONMENT_ID,
    networkIsolation: "ISOLATED",
    idleTimeoutMinutes: RUN_SCOPED_IDLE_MINUTES,
  });
}

async function connectOrCreate(request) {
  if (request.sandboxId) {
    try {
      const box = await Sandbox.connect(request.sandboxId, { environmentId: ENVIRONMENT_ID });
      await box.refresh();
      if (String(box.status || "") === "RUNNING") {
        return { box, reused: true, recovered: false };
      }
    } catch {
      // Railway idle expiry or a destroyed session is not an authority failure. The
      // durable AgentRun remains the source of truth and receives a clean computer.
    }
  }
  return { box: await createComputer(), reused: false, recovered: Boolean(request.sandboxId) };
}

async function prepareInvocation(box) {
  // /workspace/work is run-scoped scratch state. Inputs and declared outputs are
  // invocation-scoped and are cleared so stale files cannot masquerade as evidence.
  await box.exec(
    "mkdir -p /workspace/input /workspace/output /workspace/work && " +
      "rm -rf /workspace/input/* /workspace/output/* && chown -R operly:operly /workspace",
    { timeoutSec: 15 },
  );
  // Defense in depth: the untrusted uid cannot egress even if provider-level
  // isolation semantics change. Loopback remains available for local tools.
  await box.exec(
    "iptables -C OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT 2>/dev/null || " +
      "iptables -A OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT",
    { timeoutSec: 10 },
  );
}

async function executeComputer(payload) {
  const request = validatePayload(payload);
  const connected = await connectOrCreate(request);
  const box = connected.box;
  let destroyAfter = !request.keepAlive;
  try {
    await prepareInvocation(box);
    for (const item of request.decoded) {
      await box.files.write(item.target, item.bytes, { mode: 0o600 });
      await box.exec(`chown operly:operly ${shellQuote(item.target)}`, { timeoutSec: 10 });
    }

    let command;
    if (request.mode === "python") {
      await box.files.write("/workspace/work/agent.py", request.code, { mode: 0o600 });
      await box.exec("chown operly:operly /workspace/work/agent.py", { timeoutSec: 10 });
      command = `${PYTHON} /workspace/work/agent.py`;
    } else {
      command = request.argv.map(shellQuote).join(" ");
    }
    const result = await box.exec(
      `su -s /bin/bash operly -c ${shellQuote(command)}`,
      { cwd: "/workspace/work", timeoutSec: request.timeoutSeconds },
    );

    let outputBytes = 0;
    const outputs = [];
    for (const item of request.outputs) {
      let bytes;
      try {
        bytes = Buffer.from(await box.files.read(item.target, { format: "bytes" }));
      } catch {
        continue;
      }
      outputBytes += bytes.length;
      if (outputBytes > MAX_OUTPUT_BYTES) throw new Error("computer output bytes exceed policy");
      outputs.push({ path: item.relative, sizeBytes: bytes.length, contentBase64: bytes.toString("base64") });
    }
    return {
      ok: result.exitCode === 0 && !result.timedOut,
      exitCode: result.exitCode,
      timedOut: Boolean(result.timedOut),
      stdout: String(result.stdout || "").slice(-12000),
      stderr: String(result.stderr || "").slice(-12000),
      outputs,
      isolation: "railway_sandbox_vm_v1",
      network: "isolated",
      sandboxId: request.keepAlive ? String(box.id || "") : null,
      sessionReused: connected.reused,
      sessionRecovered: connected.recovered,
      runScoped: request.keepAlive,
      environment: {
        python: "operly-rich-python-v1",
        workspace: "/workspace/work",
        modules: [
          "numpy", "pandas", "scipy", "matplotlib", "Pillow", "PyMuPDF", "pypdf",
          "openpyxl", "python-docx", "python-pptx", "reportlab", "odfpy", "imageio",
          "beautifulsoup4", "lxml", "PyYAML", "soundfile",
        ],
        cli: ["python", "node", "ffmpeg", "pdftotext", "pdftoppm"],
      },
    };
  } finally {
    if (destroyAfter) {
      try { await box.destroy(); } catch {}
    }
  }
}

async function destroyComputer(payload) {
  const sandboxId = cleanSessionId(payload.sandboxId);
  if (!sandboxId) return { ok: true, destroyed: false, expired: false };
  try {
    const box = await Sandbox.connect(sandboxId, { environmentId: ENVIRONMENT_ID });
    await box.destroy();
    return { ok: true, destroyed: true, expired: false, sandboxId };
  } catch {
    // Destroy is idempotent: an already-expired Railway sandbox is equivalent to a
    // successful cleanup and must not make an AgentRun falsely fail.
    return { ok: true, destroyed: false, expired: true, sandboxId };
  }
}

async function handleComputer(req, res, pathname) {
  try {
    if (req.method !== "POST") return signedJson(res, 405, { detail: "Method not allowed" });
    const raw = await readBody(req);
    authenticate(req, raw);
    let payload;
    try { payload = JSON.parse(raw.toString("utf8") || "{}"); } catch { return signedJson(res, 400, { detail: "invalid JSON" }); }
    const result = pathname === "/v1/computer/destroy"
      ? await destroyComputer(payload)
      : await executeComputer(payload);
    return signedJson(res, result.ok ? 200 : 422, result);
  } catch (error) {
    const status = Number(error.statusCode || 400);
    return signedJson(res, status, { detail: status >= 500 ? "Computer execution failed" : String(error.message || error) });
  }
}

export function installAgentComputerEndpoint() {
  if (http.__operlyComputerEndpointInstalled) return;
  const original = http.createServer.bind(http);
  http.createServer = function patchedCreateServer(options, listener) {
    let serverOptions = options;
    let downstream = listener;
    if (typeof options === "function") {
      downstream = options;
      serverOptions = undefined;
    }
    const wrapped = (req, res) => {
      let pathname = "";
      try { pathname = new URL(req.url || "/", "http://runner.invalid").pathname; } catch {}
      if (pathname === "/v1/computer/execute" || pathname === "/v1/computer/destroy") {
        void handleComputer(req, res, pathname);
        return;
      }
      return downstream(req, res);
    };
    return serverOptions === undefined ? original(wrapped) : original(serverOptions, wrapped);
  };
  http.__operlyComputerEndpointInstalled = true;
}

export { validatePayload, safeRelative, cleanSessionId };

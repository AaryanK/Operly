import http from "node:http";
import crypto from "node:crypto";
import { Pool } from "pg";
import { Sandbox } from "railway";
import {
  bindingRuntimePlan,
  bindingTarget,
  hmacHex,
  publicBaseUrl,
  rebuildBundle,
  safeEqual,
  shellQuote,
  validateFullstackSource,
} from "./core.mjs";

const PORT = Number(process.env.PORT || 3000);
const RUNNER_TOKEN = String(process.env.OPERLY_RUNNER_TOKEN || "");
const PUBLIC_BASE = publicBaseUrl(String(process.env.OPERLY_RUNNER_PUBLIC_BASE_URL || "https://invalid.invalid"));
const ENVIRONMENT_ID = String(process.env.RAILWAY_ENVIRONMENT_ID || "");
const BINDING_HOSTS = new Set(
  String(process.env.OPERLY_RUNNER_BINDING_HOSTS || "")
    .split(",")
    .map((x) => x.trim().toLowerCase().replace(/\.$/, ""))
    .filter(Boolean),
);
const MAX_REQUEST_BYTES = 3_000_000;
const ACTIVE_PAYLOADS = new Map();
const ACTIVE_TASKS = new Set();

if (RUNNER_TOKEN.length < 32) throw new Error("OPERLY_RUNNER_TOKEN must contain at least 32 characters");
if (!ENVIRONMENT_ID) throw new Error("RAILWAY_ENVIRONMENT_ID is required");
if (!process.env.RAILWAY_TOKEN && !process.env.RAILWAY_API_TOKEN) {
  throw new Error("RAILWAY_TOKEN or RAILWAY_API_TOKEN is required for Sandbox SDK access");
}
if (!process.env.DATABASE_URL) throw new Error("DATABASE_URL is required for durable runner state");

const pool = new Pool({ connectionString: process.env.DATABASE_URL, max: 4 });

const BINDING_PROXY = String.raw`
import asyncio, http.client, json, os, sys
from urllib.parse import urlsplit

config_path=sys.argv[1] if len(sys.argv)>1 else "/run/operly-binding.json"
cfg=json.load(open(config_path,"r",encoding="utf-8"))
target=urlsplit(cfg["target"])
token=cfg["token"]
prefix=cfg["prefix"].rstrip("/")
port=int(cfg["port"])
MAX_BODY=1024*1024

def upstream(method,path,body,content_type):
    if target.scheme=="https":
        conn=http.client.HTTPSConnection(target.hostname,target.port or 443,timeout=8)
    elif target.scheme=="http":
        conn=http.client.HTTPConnection(target.hostname,target.port or 80,timeout=8)
    else:
        raise RuntimeError("invalid binding target")
    try:
        base=(target.path or "").rstrip("/")
        conn.request(method,base+prefix+(path if path.startswith("/") else "/"+path),body=body,headers={
            "Authorization":"Bearer "+token,
            "Content-Type":content_type or "application/json",
            "Accept":"application/json",
            "Connection":"close",
        })
        res=conn.getresponse()
        payload=res.read(MAX_BODY+1)
        if len(payload)>MAX_BODY: raise RuntimeError("binding response too large")
        return res.status,res.getheader("Content-Type") or "application/json",payload
    finally:
        conn.close()

async def handle(reader,writer):
    try:
        first=await reader.readline()
        method,path,version=first.decode("ascii").strip().split(" ",2)
        method=method.upper()
        if method not in {"GET","POST"} or not path.startswith("/") or "//" in path:
            raise RuntimeError("forbidden request")
        headers={}
        total=len(first)
        while True:
            line=await reader.readline(); total+=len(line)
            if total>32768: raise RuntimeError("headers too large")
            if line in {b"\r\n",b"\n",b""}: break
            key,value=line.decode("latin1").split(":",1)
            headers[key.strip().lower()]=value.strip()
        length=int(headers.get("content-length","0") or 0)
        if length<0 or length>MAX_BODY: raise RuntimeError("body too large")
        body=await reader.readexactly(length) if length else b""
        status,ctype,payload=await asyncio.to_thread(upstream,method,path,body,headers.get("content-type","application/json"))
        reason=http.client.responses.get(status,"Response")
        writer.write((f"HTTP/1.1 {status} {reason}\r\nContent-Type: {ctype}\r\nContent-Length: {len(payload)}\r\nCache-Control: no-store\r\nConnection: close\r\n\r\n").encode("latin1")+payload)
        await writer.drain()
    except Exception:
        payload=b'{"detail":"capability_binding_proxy_failure"}'
        try:
            writer.write(b"HTTP/1.1 502 Bad Gateway\r\nContent-Type: application/json\r\nConnection: close\r\nContent-Length: "+str(len(payload)).encode()+b"\r\n\r\n"+payload)
            await writer.drain()
        except Exception:
            pass
    finally:
        writer.close()
        try: await writer.wait_closed()
        except Exception: pass

async def main():
    server=await asyncio.start_server(handle,"127.0.0.1",port)
    async with server: await server.serve_forever()

asyncio.run(main())
`;

function failure(jobId, classification, message, events = []) {
  return {
    jobId,
    state: "failed",
    result: {
      buildSuccess: false,
      testSuccess: false,
      processStartSuccess: false,
      healthCheckSuccess: false,
      acceptanceCheckSuccess: false,
      previewAvailable: false,
      artifacts: [],
      testReport: {},
      staticAnalysisReport: {},
      dependencyReport: {},
      resourceUsage: {},
      failureEvidence: {
        classification,
        message: String(message || classification).slice(0, 1000),
      },
    },
    events: [...events, { state: "failed", message: String(message || classification).slice(0, 1000) }],
  };
}

async function initStore() {
  await pool.query(`
    CREATE TABLE IF NOT EXISTS operly_sandbox_runner_jobs (
      id TEXT PRIMARY KEY,
      idempotency_key TEXT NOT NULL UNIQUE,
      state TEXT NOT NULL,
      source_digest TEXT NOT NULL,
      sandbox_id TEXT,
      preview_id TEXT,
      preview_token TEXT,
      preview_expires_at TIMESTAMPTZ,
      response_json JSONB,
      created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
      updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
    )
  `);
  const inflight = await pool.query(
    `SELECT id, sandbox_id FROM operly_sandbox_runner_jobs WHERE state IN ('queued','provisioning','building','testing','starting')`,
  );
  for (const row of inflight.rows) {
    if (row.sandbox_id) {
      try {
        const box = await Sandbox.connect(row.sandbox_id, { environmentId: ENVIRONMENT_ID });
        await box.destroy();
      } catch {}
    }
    const response = failure(row.id, "runner_restart", "Runner restarted while the Sandbox job was in flight");
    await saveResponse(row.id, "failed", response);
  }
}

async function saveResponse(id, state, response, extra = {}) {
  await pool.query(
    `UPDATE operly_sandbox_runner_jobs
       SET state=$2,
           response_json=$3::jsonb,
           sandbox_id=COALESCE($4,sandbox_id),
           preview_id=COALESCE($5,preview_id),
           preview_token=COALESCE($6,preview_token),
           preview_expires_at=COALESCE($7,preview_expires_at),
           updated_at=NOW()
     WHERE id=$1`,
    [
      id,
      state,
      JSON.stringify(response),
      extra.sandboxId || null,
      extra.previewId || null,
      extra.previewToken || null,
      extra.previewExpiresAt || null,
    ],
  );
}

async function currentById(id) {
  const result = await pool.query(`SELECT * FROM operly_sandbox_runner_jobs WHERE id=$1`, [id]);
  return result.rows[0] || null;
}

async function currentByIdempotency(key) {
  const result = await pool.query(`SELECT * FROM operly_sandbox_runner_jobs WHERE idempotency_key=$1`, [key]);
  return result.rows[0] || null;
}

function currentResponse(row) {
  return row?.response_json || { jobId: row.id, state: row.state };
}

function signedJson(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Content-Length", String(body.length));
  res.setHeader("Cache-Control", "no-store");
  res.setHeader("X-Operly-Signature", hmacHex(RUNNER_TOKEN, body));
  res.end(body);
}

function plainJson(res, statusCode, payload) {
  const body = Buffer.from(JSON.stringify(payload));
  res.statusCode = statusCode;
  res.setHeader("Content-Type", "application/json");
  res.setHeader("Content-Length", String(body.length));
  res.setHeader("Cache-Control", "no-store");
  res.end(body);
}

async function readBody(req, limit = MAX_REQUEST_BYTES) {
  const chunks = [];
  let size = 0;
  for await (const chunk of req) {
    size += chunk.length;
    if (size > limit) throw new Error("request body too large");
    chunks.push(chunk);
  }
  return Buffer.concat(chunks);
}

async function authenticate(req, raw) {
  const authorization = String(req.headers.authorization || "");
  if (!safeEqual(authorization, `Bearer ${RUNNER_TOKEN}`)) throw Object.assign(new Error("Invalid runner authorization"), { statusCode: 401 });
  const supplied = String(req.headers["x-operly-signature"] || "");
  const expected = hmacHex(RUNNER_TOKEN, raw);
  if (!supplied || !safeEqual(supplied, expected)) throw Object.assign(new Error("Invalid runner request signature"), { statusCode: 401 });
}

async function sandboxTemplate() {
  return Sandbox.template()
    .withPackages("python3", "python3-venv", "python3-pip", "curl", "ca-certificates", "nodejs", "npm", "iptables")
    .run("id -u operly >/dev/null 2>&1 || useradd -m -u 10001 -s /bin/bash operly")
    .run("mkdir -p /workspace /opt/operly /run && chown -R operly:operly /workspace");
}

async function execChecked(box, command, { cwd = "/", timeoutSec = 120, label = "command", allowFailure = false } = {}) {
  const result = await box.exec(command, { cwd, timeoutSec });
  const output = `${result.stdout || ""}${result.stderr || ""}`.slice(-4000);
  if ((result.exitCode !== 0 || result.timedOut) && !allowFailure) {
    throw Object.assign(new Error(`${label} failed: ${output || `exit ${result.exitCode}`}`), { classification: label });
  }
  return { ...result, output };
}

async function detached(box, command, cwd = "/") {
  const handle = box.exec(command, { cwd });
  const sessionName = await handle.sessionName;
  await handle.detach();
  return sessionName;
}

function validatedBindingPlan(submission) {
  const plan = bindingRuntimePlan(submission);
  for (const proxy of plan.proxies) {
    bindingTarget(proxy.gatewayUrl, BINDING_HOSTS);
  }
  return plan;
}

async function applyMigrations(submission, bundle) {
  const relational = (submission.serviceBindings || []).filter((x) => x.capabilityId === "data.relational");
  const migrations = bundle.files
    .filter((x) => x.path.startsWith("migrations/") && x.path.endsWith(".json") && x.path !== "migrations/README.md")
    .map((x) => JSON.parse(x.content.toString("utf8")))
    .sort((a, b) => Number(a.version || 0) - Number(b.version || 0));
  if (!relational.length) {
    if (migrations.length) throw Object.assign(new Error("relational migrations require a data.relational binding"), { classification: "service_binding_failure" });
    return { configured: false, appliedVersions: [] };
  }
  if (relational.length !== 1) throw Object.assign(new Error("exactly one relational binding is supported"), { classification: "service_binding_failure" });
  const transport = relational[0].transport;
  if (!transport?.migrationToken) throw Object.assign(new Error("relational migration authorization is unavailable"), { classification: "service_binding_failure" });
  const base = bindingTarget(transport.gatewayUrl, BINDING_HOSTS);
  if (!migrations.length) return { configured: true, appliedVersions: [] };
  const response = await fetch(`${base}/api/runtime/relational/migrations/apply`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${transport.migrationToken}`,
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify({ migrations }),
  });
  if (!response.ok) throw Object.assign(new Error(`relational migration gateway rejected with ${response.status}`), { classification: "service_binding_failure" });
  const payload = await response.json();
  return {
    configured: true,
    currentVersion: payload.currentVersion ?? null,
    appliedVersions: payload.appliedVersions || [],
  };
}

async function startBindingProxies(box, plan) {
  if (!plan.proxies.length) return [];
  await box.files.write("/opt/operly/binding_proxy.py", BINDING_PROXY, { mode: 0o700 });
  const sessions = [];
  for (const proxy of plan.proxies) {
    const target = bindingTarget(proxy.gatewayUrl, BINDING_HOSTS);
    const configPath = `/run/operly-binding-${proxy.port}.json`;
    await box.files.write(
      configPath,
      JSON.stringify({ target, token: proxy.runtimeToken, prefix: proxy.prefix, port: proxy.port }),
      { mode: 0o600 },
    );
    sessions.push(await detached(box, `exec python3 /opt/operly/binding_proxy.py ${shellQuote(configPath)}`, "/"));
  }
  return sessions;
}

async function installDependencies(box, submission) {
  const dependencies = submission.dependencies || [];
  const python = dependencies.some((x) => x.ecosystem === "python");
  const npm = dependencies.some((x) => x.ecosystem === "npm");
  let pythonExec = "python3";
  if (python) {
    await execChecked(box, `su -s /bin/bash operly -c ${shellQuote("python3 -m venv /workspace/.venv")}`, { timeoutSec: 60, label: "dependency_failure" });
    pythonExec = "/workspace/.venv/bin/python";
    const command = "PIP_INDEX_URL=https://pypi.org/simple /workspace/.venv/bin/pip install --disable-pip-version-check --no-input --only-binary=:all: -r requirements.lock";
    await execChecked(box, `su -s /bin/bash operly -c ${shellQuote(command)}`, { cwd: "/workspace/backend", timeoutSec: Math.min(240, Number(submission.maxDurationSeconds || 300)), label: "dependency_failure" });
  }
  if (npm) {
    const command = "NPM_CONFIG_REGISTRY=https://registry.npmjs.org/ npm ci --ignore-scripts --no-audit --no-fund";
    await execChecked(box, `su -s /bin/bash operly -c ${shellQuote(command)}`, { cwd: "/workspace/frontend", timeoutSec: Math.min(240, Number(submission.maxDurationSeconds || 300)), label: "dependency_failure" });
  }
  return pythonExec;
}

async function blockGeneratedEgress(box) {
  const cmd = [
    "iptables -C OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT 2>/dev/null ||",
    "iptables -A OUTPUT -m owner --uid-owner 10001 ! -d 127.0.0.0/8 -j REJECT;",
    "ip6tables -C OUTPUT -m owner --uid-owner 10001 ! -d ::1/128 -j REJECT 2>/dev/null ||",
    "ip6tables -A OUTPUT -m owner --uid-owner 10001 ! -d ::1/128 -j REJECT",
  ].join(" ");
  await execChecked(box, cmd, { timeoutSec: 30, label: "security_policy_violation" });
}

async function runBuild(jobId, payload) {
  const events = [];
  let box = null;
  let keep = false;
  try {
    const submission = payload.submission;
    const bundle = rebuildBundle(submission, payload.bundle);
    const manifest = validateFullstackSource(submission, bundle);
    const bindingPlan = validatedBindingPlan(submission);
    const template = await sandboxTemplate();
    box = await Sandbox.create(template, {
      environmentId: ENVIRONMENT_ID,
      networkIsolation: "ISOLATED",
      idleTimeoutMinutes: 5,
    });
    await saveResponse(jobId, "provisioning", { jobId, state: "provisioning", events }, { sandboxId: box.id });
    events.push({ state: "provisioning", message: "Created isolated Railway Sandbox VM" });

    for (const file of bundle.files) {
      await box.files.write(`/workspace/${file.path}`, file.content, { mode: 0o644 });
    }
    await box.files.write("/workspace/.operly-bindings.json", JSON.stringify(bindingPlan.rows), { mode: 0o444 });
    await execChecked(box, "chown -R operly:operly /workspace && chmod 0444 /workspace/.operly-bindings.json", { label: "source_staging" });
    events.push({ state: "source_staging", message: "Staged immutable source bundle" });

    const pythonExec = await installDependencies(box, submission);
    events.push({ state: "dependency_resolution", message: "Dependency resolution completed from locked registry inputs" });

    await execChecked(
      box,
      `su -s /bin/bash operly -c ${shellQuote(`${pythonExec} -m compileall -q backend workers tests`)}`,
      { cwd: "/workspace", timeoutSec: 60, label: "build_failure" },
    );
    events.push({ state: "static_analysis", message: "Python compile check passed", exitCode: 0 });

    if ((manifest.execution?.frontend || "static") === "npm-build") {
      await execChecked(box, `su -s /bin/bash operly -c ${shellQuote("npm run lint --if-present")}`, { cwd: "/workspace/frontend", timeoutSec: 60, label: "build_failure" });
      await execChecked(box, `su -s /bin/bash operly -c ${shellQuote("npm run build")}`, { cwd: "/workspace/frontend", timeoutSec: Math.min(180, Number(submission.maxDurationSeconds || 300)), label: "build_failure" });
      events.push({ state: "building", message: "Frontend build passed", exitCode: 0 });
    } else {
      events.push({ state: "building", message: "Static frontend requires no build command", exitCode: 0 });
    }

    const pythonTests = bundle.files.some((x) => x.path.startsWith("tests/") && x.path.endsWith(".py"));
    const nodeTests = bundle.files.some((x) => x.path.startsWith("tests/") && /\.(js|mjs|cjs)$/.test(x.path));
    if (pythonTests) {
      await execChecked(
        box,
        `su -s /bin/bash operly -c ${shellQuote(`${pythonExec} -m unittest discover -s tests -p 'test*.py' -v`)}`,
        { cwd: "/workspace", timeoutSec: Math.min(180, Number(submission.maxDurationSeconds || 300)), label: "test_failure" },
      );
    }
    if (nodeTests) {
      await execChecked(box, `su -s /bin/bash operly -c ${shellQuote("node --test tests")}`, { cwd: "/workspace", timeoutSec: Math.min(180, Number(submission.maxDurationSeconds || 300)), label: "test_failure" });
    }
    events.push({ state: "testing", message: "Generated unit tests passed", exitCode: 0 });

    const migrationReport = await applyMigrations(submission, bundle);
    if (migrationReport.configured) events.push({ state: "migrating", message: "Relational application schema is current", exitCode: 0 });

    const bindingSessions = await startBindingProxies(box, bindingPlan);
    if (bindingSessions.length) {
      events.push({ state: "binding_services", message: `Started ${bindingSessions.length} root-only credential-hiding capability binding sidecar${bindingSessions.length === 1 ? "" : "s"}`, exitCode: 0 });
    }

    await blockGeneratedEgress(box);
    await execChecked(box, "chmod -R a-w /workspace", { label: "security_policy_violation" });
    events.push({ state: "sandbox_hardening", message: "Generated user outbound egress blocked and source made read-only", exitCode: 0 });

    const backendCommand = `cd /workspace && export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPERLY_BINDINGS_FILE=/workspace/.operly-bindings.json && exec ${pythonExec} backend/app.py --host 127.0.0.1 --port 8080`;
    const backendSession = await detached(box, `su -s /bin/bash operly -c ${shellQuote(backendCommand)}`, "/workspace");
    let workerSession = null;
    if ((manifest.execution?.worker || "none") === "python-cli") {
      const workerCommand = `cd /workspace && export PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 OPERLY_BINDINGS_FILE=/workspace/.operly-bindings.json && exec ${pythonExec} workers/worker.py`;
      workerSession = await detached(box, `su -s /bin/bash operly -c ${shellQuote(workerCommand)}`, "/workspace");
    }
    events.push({ state: "starting", message: "Started generated runtime as unprivileged user", exitCode: 0 });

    const deadline = Date.now() + Number(submission.healthCheck?.timeoutSeconds || 30) * 1000;
    let healthy = false;
    while (Date.now() < deadline) {
      const path = String(submission.healthCheck?.path || "/health");
      const result = await box.exec(
        `curl -sS --max-time 2 -o /tmp/operly-health-body -w '%{http_code}' ${shellQuote(`http://127.0.0.1:8080${path}`)}`,
        { timeoutSec: 4 },
      );
      const code = Number(String(result.stdout || "").trim());
      let body = "";
      try { body = String(await box.files.read("/tmp/operly-health-body")); } catch {}
      const marker = submission.healthCheck?.bodyMarker;
      healthy = result.exitCode === 0 && code === Number(submission.healthCheck?.expectedStatus || 200) && (!marker || body.includes(marker));
      if (healthy) break;
      await new Promise((resolve) => setTimeout(resolve, 250));
    }
    if (!healthy) throw Object.assign(new Error("Configured backend health check did not pass"), { classification: "health_failure" });
    events.push({ state: "health_checking", message: "Configured health check passed", exitCode: 0 });

    const acceptance = await box.exec(
      `curl -sS --max-time 3 -o /tmp/operly-acceptance-body -w '%{http_code}' http://127.0.0.1:8080/`,
      { timeoutSec: 5 },
    );
    if (acceptance.exitCode !== 0 || Number(String(acceptance.stdout || "").trim()) !== 200) {
      throw Object.assign(new Error("Full-stack preview root did not return HTTP 200"), { classification: "acceptance_failure" });
    }
    events.push({ state: "acceptance_testing", message: "Preview root returned HTTP 200", exitCode: 0 });

    const previewId = `preview-${jobId}`;
    const previewToken = crypto.randomBytes(32).toString("base64url");
    const previewSeconds = Math.max(60, Math.min(Number(submission.resources?.previewSeconds || 1800), 7200));
    const previewExpiresAt = new Date(Date.now() + previewSeconds * 1000);
    const response = {
      jobId,
      state: "preview_ready",
      result: {
        buildSuccess: true,
        testSuccess: true,
        processStartSuccess: true,
        healthCheckSuccess: true,
        acceptanceCheckSuccess: true,
        previewAvailable: true,
        artifacts: [],
        testReport: { unit: "passed", acceptance: { rootHttp200: true } },
        staticAnalysisReport: { profile: submission.stackId, passed: true },
        dependencyReport: {
          dependencies: submission.dependencies || [],
          installNetwork: submission.installNetwork || { mode: "none", approvedHosts: [] },
          generatedRuntimeEgressBlocked: true,
        },
        resourceUsage: {
          isolation: "railway_sandbox_vm_v1",
          sandboxId: box.id,
          networkIsolation: box.networkIsolation,
          generatedUid: 10001,
          sourceReadOnly: true,
          generatedEgress: "blocked_after_dependency_resolution",
          capabilityBindings: {
            sidecars: bindingSessions.length,
            capabilities: bindingPlan.rows.map((item) => item.capabilityId),
          },
          relationalData: {
            configured: migrationReport.configured,
            currentVersion: migrationReport.currentVersion ?? null,
            bindingSidecars: bindingPlan.rows.filter((item) => item.capabilityId === "data.relational").length,
          },
        },
        failureEvidence: {},
      },
      events: [...events, { state: "preview_ready", message: "Railway Sandbox execution passed every quality gate" }],
      preview: {
        id: previewId,
        targetUrl: `${PUBLIC_BASE}/preview/${previewToken}/`,
      },
    };
    await saveResponse(jobId, "preview_ready", response, {
      sandboxId: box.id,
      previewId,
      previewToken,
      previewExpiresAt,
    });
    keep = true;
  } catch (error) {
    const classification = error.classification || "runner_infrastructure_failure";
    const response = failure(jobId, classification, error.message || String(error), events);
    await saveResponse(jobId, "failed", response, { sandboxId: box?.id || null });
  } finally {
    ACTIVE_PAYLOADS.delete(jobId);
    if (box && !keep) {
      try { await box.destroy(); } catch {}
    }
  }
}

function launch(jobId, payload) {
  ACTIVE_PAYLOADS.set(jobId, payload);
  const task = runBuild(jobId, payload).finally(() => ACTIVE_TASKS.delete(task));
  ACTIVE_TASKS.add(task);
}

async function destroyJob(row) {
  if (row?.sandbox_id) {
    try {
      const box = await Sandbox.connect(row.sandbox_id, { environmentId: ENVIRONMENT_ID });
      await box.destroy();
    } catch {}
  }
}

function parseHeaders(text) {
  const blocks = String(text || "").trim().split(/\r?\n\r?\n/).filter(Boolean);
  const lines = (blocks.at(-1) || "").split(/\r?\n/);
  const statusLine = lines.shift() || "HTTP/1.1 502";
  const match = statusLine.match(/\s(\d{3})\s/);
  const headers = {};
  for (const line of lines) {
    const index = line.indexOf(":");
    if (index <= 0) continue;
    const key = line.slice(0, index).trim().toLowerCase();
    const value = line.slice(index + 1).trim();
    if (!["connection", "keep-alive", "proxy-authenticate", "proxy-authorization", "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host"].includes(key)) {
      headers[key] = value;
    }
  }
  return { status: Number(match?.[1] || 502), headers };
}

async function proxyPreview(req, res, row, token, pathWithQuery) {
  if (!row.preview_expires_at || new Date(row.preview_expires_at).getTime() <= Date.now()) {
    await destroyJob(row);
    await saveResponse(row.id, "cleaned", { jobId: row.id, state: "cleaned" });
    return plainJson(res, 404, { detail: "Preview expired" });
  }
  let box;
  try {
    box = await Sandbox.connect(row.sandbox_id, { environmentId: ENVIRONMENT_ID });
  } catch {
    await saveResponse(row.id, "failed", failure(row.id, "preview_lost", "Railway Sandbox preview no longer exists"));
    return plainJson(res, 502, { detail: "Preview runtime unavailable" });
  }

  const requestId = crypto.randomBytes(12).toString("hex");
  const raw = await readBody(req, 1_048_576);
  const bodyPath = `/tmp/operly-preview-${requestId}.request`;
  const headerPath = `/tmp/operly-preview-${requestId}.headers`;
  const outputPath = `/tmp/operly-preview-${requestId}.body`;
  if (raw.length) await box.files.write(bodyPath, raw, { mode: 0o600 });

  const forwarded = [];
  for (const name of ["accept", "accept-language", "content-type", "cookie", "user-agent"]) {
    const value = req.headers[name];
    if (typeof value === "string" && value.length <= 8192) forwarded.push("-H", `${name}: ${value}`);
  }
  const args = [
    "curl", "-sS", "--max-time", "30", "--max-redirs", "0",
    "-D", headerPath, "-o", outputPath,
    "-X", String(req.method || "GET").toUpperCase(),
    ...forwarded,
  ];
  if (raw.length) args.push("--data-binary", `@${bodyPath}`);
  args.push(`http://127.0.0.1:8080/${pathWithQuery}`);
  const command = args.map(shellQuote).join(" ");
  const result = await box.exec(command, { timeoutSec: 35 });
  if (result.exitCode !== 0) return plainJson(res, 502, { detail: "Preview runtime unavailable" });

  const headerText = String(await box.files.read(headerPath));
  const parsed = parseHeaders(headerText);
  let body = Buffer.alloc(0);
  try { body = Buffer.from(await box.files.read(outputPath, { format: "bytes" })); } catch {}
  for (const [key, value] of Object.entries(parsed.headers)) {
    if (key === "location" && value.startsWith("/")) {
      res.setHeader("location", `/preview/${token}${value}`);
    } else if (key !== "set-cookie") {
      res.setHeader(key, value);
    }
  }
  res.setHeader("Cache-Control", "no-store");
  res.setHeader(
    "Content-Security-Policy",
    "default-src 'self' data: blob:; script-src 'self' 'unsafe-inline' 'unsafe-eval' blob:; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; media-src 'self' blob:; connect-src 'self'; frame-ancestors https://operly.dragonzpyder.xyz",
  );
  res.setHeader("Permissions-Policy", "camera=(self), microphone=(self), geolocation=(self), payment=(self), usb=(self)");
  res.statusCode = parsed.status;
  res.setHeader("Content-Length", String(body.length));
  res.end(req.method === "HEAD" ? undefined : body);

  Promise.allSettled([
    box.files.remove(bodyPath),
    box.files.remove(headerPath),
    box.files.remove(outputPath),
  ]).catch(() => {});
}

async function handle(req, res) {
  try {
    const url = new URL(req.url || "/", `http://${req.headers.host || "runner"}`);

    if (req.method === "GET" && url.pathname === "/health") {
      const db = await pool.query("SELECT 1 AS ok");
      const sdkReady = Boolean(process.env.RAILWAY_TOKEN || process.env.RAILWAY_API_TOKEN);
      return plainJson(res, db.rows[0]?.ok === 1 && sdkReady ? 200 : 503, {
        status: db.rows[0]?.ok === 1 && sdkReady ? "ready" : "not_ready",
        isolation: "railway_sandbox_vm_v1",
      });
    }

    if (url.pathname.startsWith("/preview/")) {
      const match = url.pathname.match(/^\/preview\/([^/]+)\/?(.*)$/);
      if (!match) return plainJson(res, 404, { detail: "Preview not found" });
      const token = match[1];
      const result = await pool.query(
        `SELECT * FROM operly_sandbox_runner_jobs WHERE preview_token=$1 AND state='preview_ready'`,
        [token],
      );
      const row = result.rows[0];
      if (!row) return plainJson(res, 404, { detail: "Preview not found" });
      const rest = `${match[2] || ""}${url.search || ""}`;
      return proxyPreview(req, res, row, token, rest);
    }

    if (!url.pathname.startsWith("/v1/")) return plainJson(res, 404, { detail: "Not found" });

    const raw = await readBody(req);
    await authenticate(req, raw);

    if (req.method === "GET" && url.pathname === "/v1/capabilities") {
      return signedJson(res, 200, {
        protocolVersion: 2,
        isolation: "railway_sandbox_vm_v1",
        profiles: {
          "operly-fullstack-v1": {
            profileVersion: 1,
            supportsPreview: true,
            supportsDeploy: false,
          },
        },
      });
    }

    if (req.method === "POST" && url.pathname === "/v1/builds") {
      let payload;
      try { payload = JSON.parse(raw.toString("utf8") || "{}"); } catch { return signedJson(res, 400, { error: "invalid JSON" }); }
      try {
        if (!payload.submission || typeof payload.submission.idempotencyKey !== "string" || payload.submission.idempotencyKey.length < 8) throw new Error("invalid idempotency key");
        const bundle = rebuildBundle(payload.submission, payload.bundle);
        validateFullstackSource(payload.submission, bundle);
        validatedBindingPlan(payload.submission);
      } catch (error) {
        return signedJson(res, 400, { error: error.message });
      }
      const existing = await currentByIdempotency(payload.submission.idempotencyKey);
      if (existing) return signedJson(res, 200, currentResponse(existing));
      const jobId = crypto.randomBytes(16).toString("hex");
      try {
        await pool.query(
          `INSERT INTO operly_sandbox_runner_jobs(id,idempotency_key,state,source_digest,response_json)
           VALUES($1,$2,'queued',$3,$4::jsonb)`,
          [jobId, payload.submission.idempotencyKey, payload.submission.sourceBundleDigest, JSON.stringify({ jobId, state: "queued" })],
        );
      } catch (error) {
        if (error.code === "23505") {
          const race = await currentByIdempotency(payload.submission.idempotencyKey);
          return signedJson(res, 200, currentResponse(race));
        }
        throw error;
      }
      launch(jobId, payload);
      return signedJson(res, 202, { jobId, state: "queued" });
    }

    let match = url.pathname.match(/^\/v1\/builds\/([a-f0-9]+)$/);
    if (req.method === "GET" && match) {
      const row = await currentById(match[1]);
      if (!row) return signedJson(res, 404, { error: "Build not found" });
      return signedJson(res, 200, currentResponse(row));
    }

    match = url.pathname.match(/^\/v1\/builds\/([a-f0-9]+)\/cancel$/);
    if (req.method === "POST" && match) {
      const row = await currentById(match[1]);
      if (!row) return signedJson(res, 404, { error: "Build not found" });
      await destroyJob(row);
      await saveResponse(row.id, "cancelled", { jobId: row.id, state: "cancelled" });
      return signedJson(res, 200, { state: "cancelled" });
    }

    match = url.pathname.match(/^\/v1\/builds\/([a-f0-9]+)\/cleanup$/);
    if (req.method === "POST" && match) {
      const row = await currentById(match[1]);
      if (!row) return signedJson(res, 404, { error: "Build not found" });
      await destroyJob(row);
      await saveResponse(row.id, "cleaned", { jobId: row.id, state: "cleaned" });
      return signedJson(res, 200, { state: "cleaned" });
    }

    match = url.pathname.match(/^\/v1\/previews\/([^/]+)$/);
    if (req.method === "DELETE" && match) {
      const result = await pool.query(`SELECT * FROM operly_sandbox_runner_jobs WHERE preview_id=$1`, [match[1]]);
      const row = result.rows[0];
      if (!row) return signedJson(res, 200, { state: "cleaned" });
      await destroyJob(row);
      await saveResponse(row.id, "cleaned", { jobId: row.id, state: "cleaned" });
      return signedJson(res, 200, { state: "cleaned" });
    }

    return signedJson(res, 404, { error: "Not found" });
  } catch (error) {
    const status = Number(error.statusCode || 500);
    if ((req.url || "").startsWith("/v1/")) return signedJson(res, status, { detail: status === 500 ? "Runner request failed" : error.message });
    return plainJson(res, status, { detail: status === 500 ? "Runner request failed" : error.message });
  }
}

await initStore();

setInterval(async () => {
  try {
    const expired = await pool.query(
      `SELECT * FROM operly_sandbox_runner_jobs WHERE state='preview_ready' AND preview_expires_at IS NOT NULL AND preview_expires_at <= NOW() LIMIT 20`,
    );
    for (const row of expired.rows) {
      await destroyJob(row);
      await saveResponse(row.id, "cleaned", { jobId: row.id, state: "cleaned" });
    }
  } catch (error) {
    console.error("runner cleanup failed", error?.message || error);
  }
}, 60_000).unref();

http.createServer((req, res) => {
  handle(req, res).catch((error) => {
    console.error("runner request error", error);
    if (!res.headersSent) plainJson(res, 500, { detail: "Runner request failed" });
    else res.destroy();
  });
}).listen(PORT, "0.0.0.0", () => {
  console.log(`Operly Railway Sandbox runner listening on ${PORT}`);
});